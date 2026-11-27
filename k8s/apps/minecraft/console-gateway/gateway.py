#!/usr/bin/env python3
"""Console gateway for the Minecraft server pod.

Reads chat lines from the server log, holds one rcon connection on
loopback and serves authenticated callers on one TCP port with a small
line protocol: chat events out, pos/dim/tell requests in. The loop
never exits on its own: a crash loop would mark the server pod unready
and drop every player.
"""

import json
import math
import os
import queue
import re
import socket
import ssl
import struct
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timezone

LOG_PATH = "/logs/latest.log"
RCON_ADDR = "127.0.0.1:25575"
RCON_PASSWORD_PATH = "/secrets/rcon/rcon-password"
PORT = 25580
PREFIX = "!"
AUDIENCE = "minecraft-console"
SA_TOKEN_PATH = "/var/run/secrets/gateway/token"
CA_PATH = "/var/run/secrets/gateway/ca.crt"
API_PATH = "/apis/authentication.k8s.io/v1/tokenreviews"

POLL_INTERVAL = 0.5
RCON_TIMEOUT = 3.0
REVIEW_TIMEOUT = 5.0
AUTH_TIMEOUT = 5.0
MAX_CONNECTIONS = 4
MAX_CONN_AGE = 3600.0
MAX_LINE = 2048
CONN_REQUESTS = 20
CONN_WINDOW = 10.0
TELL_LIMIT = 30
TELL_WINDOW = 60.0

MAX_TEXT = 256
MAX_TELL = 400
MAX_COMMAND = 1400

CHAT_RE = re.compile(
    r"\[\d{2}:\d{2}:\d{2}\] \[Async Chat Thread - #\d+/INFO\]: "
    r"(?:\[Not Secure\] )?"
    r"<(\.?[A-Za-z0-9_]{1,16})> "
    r"(.*)"
)
NAME_RE = re.compile(r"\.?[A-Za-z0-9_]{1,16}")
POS_RE = re.compile(
    r"\[\s*(-?\d+(?:\.\d+)?)d?,\s*(-?\d+(?:\.\d+)?)d?,\s*(-?\d+(?:\.\d+)?)d?\s*\]"
)
DIM_RE = re.compile(r'"minecraft:([A-Za-z0-9_.\-/]+)"')
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


class GatewayError(Exception):
    """A local failure the protocol maps to an error kind."""


class TooLong(Exception):
    """A caller sent a line past the byte cap; the connection closes."""


def read_secret(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read().removesuffix("\n")


def outcome_for(exc):
    return type(exc).__name__


def parse_pos(reply):
    match = POS_RE.search(reply)
    if match is None:
        return None
    return tuple(math.floor(float(v)) for v in match.groups())


def parse_dimension(reply):
    match = DIM_RE.search(reply)
    return match.group(1) if match is not None else None


def _recv_exact(sock, count):
    data = b""
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise EOFError("rcon connection closed")
        data += chunk
    return data


class RconError(Exception):
    """The rcon connection or login failed."""


class Rcon:
    """One Source RCON connection, kept open and reopened on error.

    Kept open because each new connection writes two lines to the server
    log this gateway reads.
    """

    def __init__(self, host, port, password_path, timeout=RCON_TIMEOUT):
        self.host = host
        self.port = port
        self.password_path = password_path
        self.timeout = timeout
        self._sock = None
        self._next_id = 1
        self._lock = threading.Lock()

    def close(self):
        self._drop()

    def command(self, cmd, retry=False):
        # retry is for read-only commands: an idle connection can drop
        # unnoticed, and a re-sent tellraw would show up twice in chat.
        with self._lock:
            attempts = 2 if retry else 1
            for attempt in range(attempts):
                try:
                    if self._sock is None:
                        self._connect()
                    return self._roundtrip(cmd)
                except (EOFError, OSError, RconError):
                    self._drop()
                    if attempt + 1 == attempts:
                        raise

    def _connect(self):
        password = read_secret(self.password_path)
        sock = socket.create_connection((self.host, self.port), self.timeout)
        sock.settimeout(self.timeout)
        try:
            self._send(sock, 0, 3, password)
            while True:
                req_id, ptype, _ = self._recv(sock)
                if ptype == 2:
                    if req_id == -1:
                        raise RconError("rcon login rejected")
                    break
        except Exception:
            sock.close()
            raise
        self._sock = sock

    def _roundtrip(self, cmd):
        req_id = self._next_id
        self._next_id += 1
        self._send(self._sock, req_id, 2, cmd)
        while True:
            resp_id, ptype, body = self._recv(self._sock)
            if resp_id == req_id and ptype == 0:
                return body

    def _drop(self):
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    @staticmethod
    def _send(sock, req_id, ptype, body):
        payload = struct.pack("<ii", req_id, ptype) + body.encode("utf-8") + b"\x00\x00"
        sock.sendall(struct.pack("<i", len(payload)) + payload)

    @staticmethod
    def _recv(sock):
        (length,) = struct.unpack("<i", _recv_exact(sock, 4))
        if length < 10:
            raise RconError("short rcon packet")
        payload = _recv_exact(sock, length)
        req_id, ptype = struct.unpack("<ii", payload[:8])
        return req_id, ptype, payload[8:-2].decode("utf-8", "replace")


class LogTailer:
    """Appended lines from a log file, oldest content never read.

    A missing log is not an error: the file appears with the server and
    every poll retries. Rotation (new inode or a shrunk file) reopens
    from the start.
    """

    def __init__(self, path):
        self.path = path
        self._fh = None
        self._ino = None
        self._buf = ""

    def readlines(self):
        if self._fh is None:
            try:
                self._open(seek_end=True)
            except OSError:
                return []
        try:
            st = os.stat(self.path)
        except OSError:
            return []
        if st.st_ino != self._ino or st.st_size < self._fh.tell():
            self._open(seek_end=False)
        data = self._fh.read()
        if not data:
            return []
        lines = (self._buf + data).split("\n")
        self._buf = lines.pop()
        return lines

    def _open(self, seek_end):
        self._close()
        fh = open(self.path, "r", encoding="utf-8", errors="replace")
        self._ino = os.fstat(fh.fileno()).st_ino
        if seek_end:
            fh.seek(0, os.SEEK_END)
        self._fh = fh
        self._buf = ""

    def _close(self):
        if self._fh is not None:
            self._fh.close()
            self._fh = None
            self._ino = None


def token_review(api_url, ca_path, sa_token, caller_token, audiences):
    """The first known audience the caller proves, or None."""
    body = {
        "apiVersion": "authentication.k8s.io/v1",
        "kind": "TokenReview",
        "spec": {"token": caller_token, "audiences": list(audiences)},
    }
    context = None
    if api_url.startswith("https://"):
        context = ssl.create_default_context(cafile=ca_path)
    request = urllib.request.Request(
        api_url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": "Bearer " + sa_token,
                 "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=REVIEW_TIMEOUT,
                                context=context) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    status = data.get("status") or {}
    if not status.get("authenticated"):
        return None
    proved = status.get("audiences") or []
    for audience in audiences:
        if audience in proved:
            return audience
    return None


def _iso_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _emit(record):
    print(json.dumps(record), flush=True)


# Python takes these in seconds. A caller that vanishes without a close
# frees its slot about 30 + 3 * 10 s after its last byte. Each row lists
# the Linux name first, then the macOS one.
_KEEPALIVE = (
    (("TCP_KEEPIDLE", "TCP_KEEPALIVE"), 30),
    (("TCP_KEEPINTVL",), 10),
    (("TCP_KEEPCNT",), 3),
)


def _set_keepalive(sock):
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    for names, seconds in _KEEPALIVE:
        for name in names:
            option = getattr(socket, name, None)
            if option is not None:
                sock.setsockopt(socket.IPPROTO_TCP, option, seconds)
                break


class _Conn:
    def __init__(self, sock, started):
        self.sock = sock
        self.buf = b""
        self.authed = False
        self.audience = None
        self.alive = True
        self.started = started
        self.recent = deque()
        self.send_lock = threading.Lock()

    def send(self, obj):
        data = (json.dumps(obj) + "\n").encode("utf-8")
        with self.send_lock:
            if not self.alive:
                return False
            try:
                self.sock.sendall(data)
                return True
            except OSError:
                return False

    def admit(self, audience):
        """Mark the caller authed and send its ok under one lock, so a
        chat line broadcast meanwhile waits for the ok and is not lost."""
        with self.send_lock:
            if not self.alive:
                return False
            self.audience = audience
            self.authed = True
            try:
                self.sock.sendall(b'{"ok": true}\n')
                return True
            except OSError:
                self.authed = False
                return False

    def close(self):
        self.alive = False
        try:
            self.sock.close()
        except OSError:
            pass


_OP_FIELDS = {
    "pos": {"id", "op", "name"},
    "dim": {"id", "op", "name"},
    "tell": {"id", "op", "name", "text"},
}


def _default_audiences(name):
    """What one audience is offered: its operations and whether chat
    events reach it. The one audience today is named by the AUDIENCE
    setting; a second means a table passed as audiences=."""
    return {name: {"chat": True, "ops": set(_OP_FIELDS)}}


class Gateway:
    def __init__(self, *, rcon, log_path, host="", port=PORT, prefix=PREFIX,
                 audience=AUDIENCE, api_url=None, ca_path=CA_PATH,
                 sa_token_path=SA_TOKEN_PATH, max_connections=MAX_CONNECTIONS,
                 max_age=MAX_CONN_AGE, auth_timeout=AUTH_TIMEOUT,
                 audiences=None, now=time.monotonic):
        self.rcon = rcon
        self.tailer = LogTailer(log_path)
        self.host = host
        self.port = port
        self.prefix = prefix
        self.audience = audience
        self.api_url = api_url
        self.ca_path = ca_path
        self.sa_token_path = sa_token_path
        self.max_connections = max_connections
        self.max_age = max_age
        self.auth_timeout = auth_timeout
        self.audiences = _default_audiences(audience) if audiences is None else audiences
        self.now = now
        self._srv = None
        self._conns = set()
        self._conns_lock = threading.Lock()
        self._tells = {}
        self._tells_lock = threading.Lock()
        self._stopping = threading.Event()

    def start(self):
        self._srv = socket.socket()
        try:
            self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._srv.bind((self.host, self.port))
            self._srv.listen()
        except Exception:
            self._srv.close()
            self._srv = None
            raise
        self.port = self._srv.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()
        _emit({"time": _iso_now(), "event": "listening", "port": self.port})

    def stop(self):
        self._stopping.set()
        if self._srv is not None:
            # Linux keeps a listener serving until the accept blocked on
            # it returns; shutdown wakes that accept, and close alone does
            # not. macOS raises here on a listener, which close covers.
            for end in (lambda: self._srv.shutdown(socket.SHUT_RDWR),
                        self._srv.close):
                try:
                    end()
                except OSError:
                    pass
        with self._conns_lock:
            conns = list(self._conns)
        for conn in conns:
            conn.close()
        self.rcon.close()
        self.tailer._close()

    def run(self):
        while not self._stopping.is_set():
            try:
                self.broadcast_once()
            except Exception as exc:
                _emit({"time": _iso_now(), "event": "poll_failed",
                       "outcome": outcome_for(exc)})
            time.sleep(POLL_INTERVAL)

    def broadcast_once(self):
        for line in self.tailer.readlines():
            match = CHAT_RE.fullmatch(line)
            if match is None:
                continue
            text = match.group(2)[:MAX_TEXT]
            if not text.startswith(self.prefix):
                continue
            self._broadcast({"chat": {"name": match.group(1), "text": text}})

    def _broadcast(self, obj):
        with self._conns_lock:
            conns = [
                c for c in self._conns
                if c.authed
                and self.audiences.get(c.audience, {}).get("chat")
            ]
        for conn in conns:
            if not conn.send(obj):
                conn.close()

    def _accept(self):
        while not self._stopping.is_set():
            try:
                sock, _ = self._accept_conn()
            except OSError as exc:
                if self._stopping.is_set():
                    return
                # A failed accept costs a pause and a log line; the
                # loop keeps listening.
                _emit({"time": _iso_now(), "event": "accept_failed",
                       "outcome": outcome_for(exc)})
                time.sleep(0.5)
                continue
            with self._conns_lock:
                if len(self._conns) >= self.max_connections:
                    sock.close()
                    continue
                conn = _Conn(sock, self.now())
                self._conns.add(conn)
            try:
                threading.Thread(target=self._serve, args=(conn,),
                                 daemon=True).start()
            except Exception:
                with self._conns_lock:
                    self._conns.discard(conn)
                conn.close()

    def _accept_conn(self):
        sock, addr = self._srv.accept()
        try:
            _set_keepalive(sock)
        except OSError:
            sock.close()
            raise
        return sock, addr

    def _serve(self, conn):
        try:
            if self._auth(conn):
                self._requests(conn)
        finally:
            conn.close()
            with self._conns_lock:
                self._conns.discard(conn)

    def _auth(self, conn):
        deadline = time.monotonic() + self.auth_timeout
        try:
            line = self._read_line(conn, deadline)
            token = json.loads(line).get("auth")
        except (TooLong, EOFError, OSError, ValueError,
                AttributeError, socket.timeout):
            return False
        if not isinstance(token, str):
            return False
        try:
            audience = token_review(self.api_url, self.ca_path,
                                    read_secret(self.sa_token_path), token,
                                    list(self.audiences))
        except Exception as exc:
            _emit({"time": _iso_now(), "event": "review_failed",
                   "outcome": outcome_for(exc)})
            return False
        if audience is None:
            _emit({"time": _iso_now(), "event": "auth_refused"})
            return False
        return conn.admit(audience)

    def _requests(self, conn):
        try:
            conn.sock.settimeout(1.0)
        except OSError:
            # Closed since the auth reply: a failed broadcast or a stop.
            return
        while conn.alive:
            if self.now() - conn.started > self.max_age:
                return
            try:
                line = self._read_line(conn)
            except socket.timeout:
                continue
            except (TooLong, EOFError, OSError):
                return
            try:
                req = json.loads(line)
            except ValueError:
                return
            # The op is echoed to the log only when it is a known name;
            # anything else a caller sent stays out of it.
            op = req.get("op") if isinstance(req, dict) else None
            if not isinstance(op, str) or op not in _OP_FIELDS:
                op = None
            started = time.monotonic()
            try:
                resp = self._dispatch(conn, req)
            except Exception as exc:
                # The five-field line with the exception as outcome:
                # a console command may have gone out, and it must not
                # pass unrecorded.
                _emit({"time": _iso_now(), "audience": conn.audience,
                       "op": op, "outcome": outcome_for(exc),
                       "ms": int((time.monotonic() - started) * 1000)})
                return
            if resp is not None:
                _emit({"time": _iso_now(), "audience": conn.audience,
                       "op": op,
                       "outcome": "ok" if resp.get("ok") else resp.get("error"),
                       "ms": int((time.monotonic() - started) * 1000)})
                if not conn.send(resp):
                    return

    def _read_line(self, conn, deadline=None):
        while True:
            idx = conn.buf.find(b"\n")
            if idx != -1:
                line = conn.buf[:idx]
                conn.buf = conn.buf[idx + 1:]
                if len(line) > MAX_LINE:
                    raise TooLong()
                if line.endswith(b"\r"):
                    line = line[:-1]
                return line.decode("utf-8", "replace")
            if len(conn.buf) > MAX_LINE:
                raise TooLong()
            if deadline is not None:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise socket.timeout("line late")
                conn.sock.settimeout(left)
            chunk = conn.sock.recv(4096)
            if not chunk:
                raise EOFError("caller closed")
            conn.buf += chunk

    def _dispatch(self, conn, req):
        req_id = req.get("id") if isinstance(req, dict) else None
        if not isinstance(req_id, int) or isinstance(req_id, bool):
            req_id = None

        def fail(kind):
            return {"id": req_id, "ok": False, "error": kind}

        if not isinstance(req, dict) or req_id is None:
            return fail("bad_request")
        op = req.get("op")
        if not isinstance(op, str) or op not in (
            self.audiences.get(conn.audience, {}).get("ops", ())
        ):
            return fail("bad_request")
        fields = _OP_FIELDS.get(op)
        if fields is None or set(req) != fields:
            return fail("bad_request")
        if not self._allow_request(conn):
            return fail("rate")
        return getattr(self, "_op_" + op)(req_id, req)

    @staticmethod
    def _name(req):
        name = req.get("name")
        if not isinstance(name, str) or NAME_RE.fullmatch(name) is None:
            return None
        return name

    def _allow_request(self, conn):
        now = self.now()
        while conn.recent and now - conn.recent[0] >= CONN_WINDOW:
            conn.recent.popleft()
        if len(conn.recent) >= CONN_REQUESTS:
            return False
        conn.recent.append(now)
        return True

    def _allow_tell(self, name):
        with self._tells_lock:
            now = self.now()
            # A name whose window emptied goes, so the table cannot grow
            # with the number of names ever told.
            for who, calls in list(self._tells.items()):
                while calls and now - calls[0] >= TELL_WINDOW:
                    calls.popleft()
                if not calls:
                    del self._tells[who]
            calls = self._tells.setdefault(name, deque())
            if len(calls) >= TELL_LIMIT:
                return False
            calls.append(now)
            return True

    def _data(self, req_id, name, what):
        try:
            reply = self.rcon.command("data get entity %s %s" % (name, what),
                                      retry=True)
        except Exception:
            return {"id": req_id, "ok": False, "error": "console"}
        return reply

    def _op_pos(self, req_id, req):
        name = self._name(req)
        if name is None:
            return {"id": req_id, "ok": False, "error": "bad_name"}
        reply = self._data(req_id, name, "Pos")
        if isinstance(reply, dict):
            return reply
        pos = parse_pos(reply)
        if pos is None:
            return {"id": req_id, "ok": False, "error": "offline"}
        return {"id": req_id, "ok": True, "pos": list(pos)}

    def _op_dim(self, req_id, req):
        name = self._name(req)
        if name is None:
            return {"id": req_id, "ok": False, "error": "bad_name"}
        reply = self._data(req_id, name, "Dimension")
        if isinstance(reply, dict):
            return reply
        dim = parse_dimension(reply)
        if dim is None:
            return {"id": req_id, "ok": False, "error": "offline"}
        return {"id": req_id, "ok": True, "dim": dim}

    def _op_tell(self, req_id, req):
        name = self._name(req)
        if name is None:
            return {"id": req_id, "ok": False, "error": "bad_name"}
        text = req["text"]
        if not isinstance(text, str):
            return {"id": req_id, "ok": False, "error": "bad_request"}
        if not self._allow_tell(name):
            return {"id": req_id, "ok": False, "error": "rate"}
        text = CONTROL_RE.sub("", text)
        if len(text) > MAX_TELL:
            return {"id": req_id, "ok": False, "error": "too_long"}
        cmd = "tellraw " + name + " " + json.dumps(
            {"text": text, "color": "aqua"}, separators=(",", ":")
        )
        if len(cmd.encode("utf-8")) >= MAX_COMMAND:
            return {"id": req_id, "ok": False, "error": "too_long"}
        try:
            self.rcon.command(cmd)
        except Exception:
            return {"id": req_id, "ok": False, "error": "console"}
        return {"id": req_id, "ok": True}


def _api_url():
    host = os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    return "https://%s:%s%s" % (host, port, API_PATH)


def serve():
    host, _, port = os.environ.get("RCON", RCON_ADDR).rpartition(":")
    gateway = Gateway(
        rcon=Rcon(host, int(port), RCON_PASSWORD_PATH),
        log_path=os.environ.get("LOG", LOG_PATH),
        port=int(os.environ.get("PORT", PORT)),
        prefix=os.environ.get("PREFIX", PREFIX),
        audience=os.environ.get("AUDIENCE", AUDIENCE),
        api_url=_api_url(),
    )
    # A bind that fails (the port taken, say) is retried: the gateway
    # may never exit.
    while True:
        try:
            gateway.start()
            break
        except OSError as exc:
            _emit({"time": _iso_now(), "event": "bind_failed",
                   "outcome": outcome_for(exc)})
            time.sleep(POLL_INTERVAL)
    gateway.run()


if __name__ == "__main__":
    serve()
