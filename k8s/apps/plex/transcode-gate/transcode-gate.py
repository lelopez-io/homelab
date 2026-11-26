#!/usr/bin/env python3
"""Let a client workload read one transcode and nothing else.

A caller proves its identity with a short-lived service account token.
The gate checks every request with a TokenReview, adds the media
server's owner token itself, and forwards only the three calls needed
to run a transcode: start, session reads, stop. The owner token never
leaves the pod, and no path, query, token or session id reaches a log
line.
"""

import hashlib
import json
import os
import re
import signal
import ssl
import sys
import threading
import time
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote_plus, urlsplit

PLEX = os.environ.get("PLEX", "http://127.0.0.1:32400").rstrip("/")
PORT = int(os.environ.get("PORT", "3926"))
AUDIENCE = os.environ.get("AUDIENCE", "plex-transcode")
PREFS = os.environ.get("PREFS", "/plex/Preferences.xml")
KUBE_API = os.environ.get(
    "KUBE_API", "https://kubernetes.default.svc").rstrip("/")
SA_DIR = os.environ.get(
    "SA_DIR", "/var/run/secrets/kubernetes.io/serviceaccount")
IDLE_TIMEOUT_S = float(os.environ.get("IDLE_TIMEOUT", "30"))

UPSTREAM_TIMEOUT_S = 60.0
REVIEW_TIMEOUT_S = 10.0
REVIEW_TTL_S = 60.0     # a caller makes several requests a second
REVIEW_CACHE_MAX = 256
MAX_BODY = 64 * 1024 * 1024

TOKEN_RE = re.compile(r'PlexOnlineToken="([^"]+)"')
BEARER_RE = re.compile(r"^Bearer ([^\s]+)$", re.IGNORECASE)
START_PATH = "/video/:/transcode/universal/start.m3u8"
STOP_PATH = "/video/:/transcode/universal/stop"
SESSION_RE = re.compile(
    r"^/video/:/transcode/universal/session/[A-Za-z0-9-]{1,64}"
    r"/(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+$")
LIBRARY_PATH_RE = re.compile(r"^/library/metadata/[0-9]+$")
REVIEW_PATH = "/apis/authentication.k8s.io/v1/tokenreviews"

REVIEW_CACHE = {}
REVIEW_LOCK = threading.Lock()


class GateError(Exception):
    # Messages are static strings, safe to answer and to log.
    pass


def log(msg):
    sys.stderr.write("transcode-gate: %s\n" % msg)
    sys.stderr.flush()


def owner_token():
    # Read fresh per request and kept in memory only for that request.
    try:
        with open(PREFS, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        raise GateError("owner token unavailable")
    m = TOKEN_RE.search(text)
    if not m:
        raise GateError("owner token unavailable")
    return m.group(1)


def read_gate_token():
    # Fresh each review: the kubelet rotates the projected file.
    try:
        with open(os.path.join(SA_DIR, "token"),
                  "r", encoding="utf-8") as f:
            token = f.read().strip()
    except OSError:
        raise GateError("api unavailable")
    if not token:
        raise GateError("api unavailable")
    return token


def review_cached(key):
    with REVIEW_LOCK:
        hit = REVIEW_CACHE.get(key)
        if hit is None:
            return None
        if hit[0] <= time.monotonic():
            REVIEW_CACHE.pop(key, None)
            return None
        return hit[1]


def review_store(key, username):
    with REVIEW_LOCK:
        if len(REVIEW_CACHE) >= REVIEW_CACHE_MAX:
            oldest = min(REVIEW_CACHE, key=lambda k: REVIEW_CACHE[k][0])
            REVIEW_CACHE.pop(oldest, None)
        REVIEW_CACHE[key] = (time.monotonic() + REVIEW_TTL_S, username)


def review_token(token):
    """The caller's username when the review accepts, None when it
    refuses; refusals are never cached. GateError when the API fails."""
    key = hashlib.sha256(token.encode("utf-8")).hexdigest()
    cached = review_cached(key)
    if cached is not None:
        return cached
    body = json.dumps({
        "apiVersion": "authentication.k8s.io/v1",
        "kind": "TokenReview",
        "spec": {"token": token, "audiences": [AUDIENCE]},
    }).encode("utf-8")
    data = kube_post(REVIEW_PATH, body, read_gate_token())
    try:
        status = json.loads(data.decode("utf-8", "replace")).get(
            "status", {})
    except ValueError:
        raise GateError("api unavailable")
    if not isinstance(status, dict):
        raise GateError("api unavailable")
    if (status.get("authenticated") is True
            and AUDIENCE in (status.get("audiences") or [])):
        username = status.get("user", {}).get("username", "")
        review_store(key, username)
        return username
    return None


def kube_post(path, body, gate_token):
    u = urlsplit(KUBE_API)
    if u.scheme == "https":
        ctx = ssl.create_default_context(
            cafile=os.path.join(SA_DIR, "ca.crt"))
        conn = http.client.HTTPSConnection(
            u.hostname, u.port or 443, timeout=REVIEW_TIMEOUT_S,
            context=ctx)
    else:
        conn = http.client.HTTPConnection(
            u.hostname, u.port or 80, timeout=REVIEW_TIMEOUT_S)
    try:
        conn.request("POST", path, body=body, headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": "Bearer " + gate_token,
        })
        resp = conn.getresponse()
        data = resp.read(MAX_BODY + 1)
        if resp.status not in (200, 201):
            raise GateError("api unavailable")
        return data
    except (OSError, http.client.HTTPException):
        raise GateError("api unavailable")
    finally:
        conn.close()


def forward(path_query, caller_headers):
    owner = owner_token()
    fwd = {}
    for k, v in caller_headers.items():
        lk = k.lower()
        if lk in ("accept", "range"):
            fwd[k] = v
        elif lk.startswith("x-plex-") and lk != "x-plex-token":
            fwd[k] = v
    fwd["X-Plex-Token"] = owner
    u = urlsplit(path_query)
    # Raw pairs, not parse_qsl: the query goes upstream byte for byte.
    # The key is decoded before comparing because the server decodes it.
    kept = [p for p in u.query.split("&")
            if p and unquote_plus(p.split("=", 1)[0]).lower()
               != "x-plex-token"]
    target = u.path + ("?" + "&".join(kept) if kept else "")
    p = urlsplit(PLEX)
    conn = http.client.HTTPConnection(
        p.hostname, p.port or 80, timeout=UPSTREAM_TIMEOUT_S)
    try:
        conn.request("GET", target, headers=fwd)
        resp = conn.getresponse()
        body = resp.read(MAX_BODY + 1)
        status = resp.status
        hdrs = dict((k.lower(), v) for k, v in resp.getheaders())
    except (OSError, http.client.HTTPException):
        raise GateError("media server unavailable")
    finally:
        conn.close()
    if len(body) > MAX_BODY:
        raise GateError("media server body too large")
    return status, hdrs, body


def caller_label(username):
    parts = username.split(":")
    if len(parts) == 4 and parts[1] == "serviceaccount":
        return "%s/%s" % (parts[2], parts[3])
    return username


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "transcode-gate"
    sys_version = ""
    # Per socket operation, so a slow reader is fine but a silent one
    # is dropped.
    timeout = IDLE_TIMEOUT_S

    def version_string(self):
        return "transcode-gate"

    def log_message(self, fmt, *args):
        # The stock message logs the request line, which carries the
        # path and query. _send logs the safe summary instead.
        pass

    def _send(self, code, body=b"", ctype="text/plain", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)
        took = 0.0
        if hasattr(self, "_t0"):
            took = (time.monotonic() - self._t0) * 1000
        if self._caller is not None:
            log("served %s %s %d %dms"
                % (caller_label(self._caller), self._kind, code, took))
        else:
            log("refused %d %dms" % (code, took))

    def _error(self, code, msg, headers=None):
        self._send(code, (msg + "\n").encode("utf-8"), headers=headers)

    def _drain(self):
        # An unread request body would be parsed as the next request on
        # a kept-alive connection.
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n > MAX_BODY:
            self.close_connection = True
            return
        while n > 0:
            chunk = self.rfile.read(min(n, 65536))
            if not chunk:
                break
            n -= len(chunk)

    def _dispatch(self):
        self._t0 = time.monotonic()
        self._caller = None
        self._kind = "session"
        try:
            self._route()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            # A client must never see a traceback.
            log("handler error (%s)" % type(e).__name__)
            try:
                self._error(500, "error")
            except Exception:
                pass

    def do_GET(self):
        self._dispatch()

    def _refuse(self):
        self._t0 = time.monotonic()
        self._caller = None
        self._kind = "session"
        self._drain()
        self._error(404, "not found")

    do_POST = _refuse
    do_PUT = _refuse
    do_DELETE = _refuse
    do_PATCH = _refuse
    do_OPTIONS = _refuse
    do_HEAD = _refuse

    def _route(self):
        # The path decides before anything else, so an unauthenticated
        # scan learns nothing more than a bare 404.
        path = urlsplit(self.path).path
        if path == START_PATH:
            kind = "start"
        elif path == STOP_PATH:
            kind = "stop"
        else:
            m = SESSION_RE.match(path)
            if not m or not self._rest_ok(path):
                return self._error(404, "not found")
            kind = "session"
        self._kind = kind
        auth = self.headers.get("Authorization") or ""
        m = BEARER_RE.match(auth)
        if not m:
            return self._error(401, "unauthorized")
        try:
            caller = review_token(m.group(1))
        except GateError as e:
            return self._error(502, str(e))
        if caller is None:
            return self._error(401, "unauthorized")
        self._caller = caller
        if kind == "start":
            paths = parse_qs(urlsplit(self.path).query).get("path", [])
            if len(paths) != 1 or not LIBRARY_PATH_RE.match(paths[0]):
                return self._error(400, "bad path parameter")
        try:
            status, hdrs, body = forward(self.path, self.headers)
        except GateError as e:
            return self._error(502, str(e))
        out = {}
        if "content-range" in hdrs:
            out["Content-Range"] = hdrs["content-range"]
        self._send(status, body,
                   ctype=hdrs.get("content-type",
                                  "application/octet-stream"),
                   headers=out)

    @staticmethod
    def _rest_ok(path):
        # The regex admits dots inside a segment; these two may not be
        # a whole segment.
        rest = path.split("/session/", 1)[1].split("/", 1)[1]
        return all(seg not in (".", "..") for seg in rest.split("/"))


class Server(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        # The stdlib default prints a traceback with the peer's address;
        # one quiet line with no request data is enough.
        log("connection closed (%s)" % type(sys.exc_info()[1]).__name__)


def cmd_serve():
    srv = Server(("0.0.0.0", PORT), Handler)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log("listening on %d" % PORT)
    stop.wait()
    log("stopping")
    srv.shutdown()
    srv.server_close()
    log("stopped")
    return 0


def main(argv):
    if len(argv) < 2 or argv[1] != "serve":
        sys.stderr.write("usage: transcode-gate.py serve\n")
        return 2
    return cmd_serve()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
