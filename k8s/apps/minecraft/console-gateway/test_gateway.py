"""Tests for gateway.py: fake rcon on loopback, a temp log file, and a
fake TokenReview endpoint over plain http on loopback. No cluster."""

import http.server
import io
import json
import os
import re
import socket
import struct
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import gateway

RCON_PASSWORD = "rcon-secret"
SA_TOKEN = "sa-token"
GOOD_TOKEN = "good-token"
OTHER_TOKEN = "other-token"
POS_REPLY = "has the following entity data: [-502.5d, 76.0d, 314.3d]"
DIM_REPLY = 'has the following entity data: "minecraft:overworld"'
POS = [-503, 76, 314]

OTHER_AUDIENCES = {
    "minecraft-console": {"chat": True, "ops": {"pos", "dim", "tell"}},
    "other-audience": {"chat": False, "ops": {"tell"}},
}


def _recv_exact(conn, count):
    data = b""
    while len(data) < count:
        chunk = conn.recv(count - len(data))
        if not chunk:
            raise EOFError("closed")
        data += chunk
    return data


class FakeRcon(threading.Thread):
    def __init__(self, password, port=0):
        super().__init__(daemon=True)
        self.password = password
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", port))
        self.srv.listen()
        self.port = self.srv.getsockname()[1]
        self.lock = threading.Lock()
        self.commands = []
        self.tellraws = []
        self.offline = set()
        self.sessions = 0
        self.conns = set()
        self._halt = threading.Event()

    def run(self):
        while not self._halt.is_set():
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            with self.lock:
                self.conns.add(conn)
            with conn:
                try:
                    self._session(conn)
                except (EOFError, OSError):
                    pass
            with self.lock:
                self.conns.discard(conn)

    def _session(self, conn):
        with self.lock:
            self.sessions += 1
        req_id, ptype, body = self._read(conn)
        if ptype != 3:
            return
        if body != self.password:
            self._write(conn, -1, 2, "")
            return
        self._write(conn, req_id, 2, "")
        while True:
            req_id, ptype, body = self._read(conn)
            if ptype != 2:
                return
            with self.lock:
                self.commands.append(body)
            self._write(conn, req_id, 0, self._dispatch(body))

    def _dispatch(self, cmd):
        match = re.fullmatch(r"data get entity (\S+) Pos", cmd)
        if match:
            if match.group(1) in self.offline:
                return "No entity was found"
            return match.group(1) + " " + POS_REPLY
        match = re.fullmatch(r"data get entity (\S+) Dimension", cmd)
        if match:
            if match.group(1) in self.offline:
                return "No entity was found"
            return match.group(1) + " " + DIM_REPLY
        match = re.fullmatch(r"tellraw (\S+) (.*)", cmd)
        if match:
            with self.lock:
                self.tellraws.append((match.group(1), match.group(2)))
            return "Sent message"
        return "unknown command"

    def drop(self):
        with self.lock:
            conns = list(self.conns)
        for conn in conns:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()

    def close(self):
        self._halt.set()
        self.drop()
        # Linux keeps the listener serving until the blocked accept
        # returns; shutdown wakes it (macOS raises on a listener).
        try:
            self.srv.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.srv.close()
        if self.is_alive():
            self.join(2)

    @staticmethod
    def _read(conn):
        (length,) = struct.unpack("<i", _recv_exact(conn, 4))
        payload = _recv_exact(conn, length)
        req_id, ptype = struct.unpack("<ii", payload[:8])
        return req_id, ptype, payload[8:-2].decode("utf-8")

    @staticmethod
    def _write(conn, req_id, ptype, body):
        payload = struct.pack("<ii", req_id, ptype) + body.encode("utf-8") + b"\x00\x00"
        conn.sendall(struct.pack("<i", len(payload)) + payload)


class _ReviewHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        body = json.loads(self.rfile.read(length))
        auth = self.headers.get("Authorization", "")
        self.server.reviews.append({"authorization": auth, "body": body})
        audiences = self.server.tokens.get(body.get("spec", {}).get("token"), [])
        reply = {
            "apiVersion": "authentication.k8s.io/v1",
            "kind": "TokenReview",
            "status": {"authenticated": bool(audiences), "audiences": audiences},
        }
        data = json.dumps(reply).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class FakeReview(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _ReviewHandler)
        self.port = self.server_address[1]
        self.tokens = {GOOD_TOKEN: ["minecraft-console"],
                       OTHER_TOKEN: ["other-audience"]}
        self.reviews = []
        threading.Thread(
            target=lambda: self.serve_forever(poll_interval=0.05),
            daemon=True,
        ).start()


class Client:
    def __init__(self, port, timeout=3.0):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout)
        self.sock.settimeout(timeout)
        self.buf = b""

    def send(self, obj):
        self.sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))

    def send_raw(self, data):
        self.sock.sendall(data)

    def obj(self):
        return json.loads(self.line())

    def line(self):
        while True:
            idx = self.buf.find(b"\n")
            if idx != -1:
                line = self.buf[:idx]
                self.buf = self.buf[idx + 1:]
                return line.decode("utf-8")
            chunk = self.sock.recv(4096)
            if not chunk:
                raise EOFError("closed")
            self.buf += chunk

    def auth(self, token=GOOD_TOKEN):
        self.send({"auth": token})
        return self.obj()

    def request(self, req):
        self.send(req)
        return self.obj()

    def closed(self, wait=0.4):
        self.sock.settimeout(wait)
        try:
            self.line()
        except EOFError:
            return True
        except (socket.timeout, TimeoutError):
            return False
        except OSError:
            return True
        return False

    def close(self):
        self.sock.close()


def chat(name, text, not_secure=False, thread="Async Chat Thread - #3"):
    mark = "[Not Secure] " if not_secure else ""
    return f"[05:13:13] [{thread}/INFO]: {mark}<{name}> {text}"


class GatewayCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.log_path = root / "latest.log"
        self.log_path.write_text(chat("Old", "!before start") + "\n")
        self.pw_path = root / "rcon-password"
        self.pw_path.write_text(RCON_PASSWORD + "\n")
        self.sa_path = root / "sa-token"
        self.sa_path.write_text(SA_TOKEN + "\n")
        self.rcon_srv = FakeRcon(RCON_PASSWORD)
        self.rcon_srv.start()
        self.addCleanup(self.rcon_srv.close)
        self.review = FakeReview()
        self.addCleanup(self.review.server_close)
        self.addCleanup(self.review.shutdown)
        self.clock = [1000.0]
        self.gw = self.make_gw()
        self.addCleanup(self.gw.stop)
        self.clients = []
        # Position the log tailer at the end, as the run loop's first
        # poll does, so feeds deliver only their own lines.
        self.gw.broadcast_once()

    def make_gw(self, **overrides):
        args = dict(
            rcon=gateway.Rcon("127.0.0.1", self.rcon_srv.port,
                              str(self.pw_path)),
            log_path=str(self.log_path),
            port=0,
            api_url="http://127.0.0.1:%d/reviews" % self.review.port,
            ca_path=str(self.sa_path),
            sa_token_path=str(self.sa_path),
            now=lambda: self.clock[0],
        )
        args.update(overrides)
        gw = gateway.Gateway(**args)
        gw.start()
        return gw

    def client(self, token=GOOD_TOKEN):
        conn = Client(self.gw.port)
        self.clients.append(conn)
        self.addCleanup(conn.close)
        if token is not None:
            self.assertEqual(conn.auth(token), {"ok": True})
        return conn

    def feed(self, *lines):
        with open(self.log_path, "a", encoding="utf-8") as fh:
            for line in lines:
                fh.write(line + "\n")
        self.gw.broadcast_once()


class AuthTests(GatewayCase):
    def test_good_token_authenticates(self):
        self.client()
        review = self.review.reviews[0]
        self.assertEqual(review["authorization"], "Bearer " + SA_TOKEN)
        self.assertEqual(review["body"]["spec"],
                         {"token": GOOD_TOKEN,
                          "audiences": ["minecraft-console"]})

    def test_bad_token_closes(self):
        conn = Client(self.gw.port)
        self.addCleanup(conn.close)
        conn.send({"auth": "wrong-token"})
        self.assertTrue(conn.closed())

    def test_missing_auth_line_closes(self):
        conn = Client(self.gw.port)
        self.addCleanup(conn.close)
        conn.send({"id": 1, "op": "pos", "name": "Steve"})
        self.assertTrue(conn.closed())

    def test_late_auth_line_closes(self):
        self.gw.auth_timeout = 0.2
        conn = Client(self.gw.port)
        self.addCleanup(conn.close)
        self.assertTrue(conn.closed(wait=3.0))

    def test_slow_auth_line_closed_at_the_deadline(self):
        self.gw.auth_timeout = 0.4
        conn = Client(self.gw.port)
        self.addCleanup(conn.close)
        for byte in b'{"auth": "':  # 9 bytes, 0.2 s apart, over the deadline
            try:
                conn.sock.sendall(bytes([byte]))
            except OSError:
                break
            time.sleep(0.2)
        self.assertTrue(conn.closed(wait=3.0))

    def test_review_endpoint_down_closes(self):
        self.review.shutdown()
        self.review.server_close()
        conn = Client(self.gw.port)
        self.addCleanup(conn.close)
        conn.send({"auth": GOOD_TOKEN})
        self.assertTrue(conn.closed())

    def test_audience_setting_names_the_offered_audience(self):
        self.review.tokens["custom-token"] = ["custom-aud"]
        self.gw.stop()
        self.gw = self.make_gw(audience="custom-aud")
        self.addCleanup(self.gw.stop)
        refused = Client(self.gw.port)
        self.addCleanup(refused.close)
        refused.send({"auth": GOOD_TOKEN})
        self.assertTrue(refused.closed())
        accepted = Client(self.gw.port)
        self.addCleanup(accepted.close)
        self.assertEqual(accepted.auth("custom-token"), {"ok": True})
        review = self.review.reviews[-1]
        self.assertEqual(review["body"]["spec"]["audiences"], ["custom-aud"])


class ChatTests(GatewayCase):
    def test_chat_reaches_chat_audience(self):
        conn = self.client()
        self.feed(chat("Steve", "!ai hello"))
        self.assertEqual(conn.obj(), {"chat": {"name": "Steve",
                                               "text": "!ai hello"}})

    def test_log_content_before_start_is_not_sent(self):
        conn = self.client()
        self.feed()
        conn.sock.settimeout(0.3)
        with self.assertRaises((socket.timeout, TimeoutError)):
            conn.obj()

    def test_line_without_prefix_not_sent(self):
        conn = self.client()
        self.feed(chat("Steve", "hello everyone"))
        conn.sock.settimeout(0.3)
        with self.assertRaises((socket.timeout, TimeoutError)):
            conn.obj()

    def test_non_chat_line_not_sent(self):
        conn = self.client()
        self.feed(chat("Steve", "!ai hi", thread="Server thread"))
        conn.sock.settimeout(0.3)
        with self.assertRaises((socket.timeout, TimeoutError)):
            conn.obj()

    def test_not_secure_line_is_sent(self):
        conn = self.client()
        self.feed(chat(".Alex_1", "!ai hi", not_secure=True))
        self.assertEqual(conn.obj(), {"chat": {"name": ".Alex_1",
                                               "text": "!ai hi"}})

    def test_chat_text_cut_at_256(self):
        conn = self.client()
        self.feed(chat("Steve", "!" + "x" * 300))
        self.assertEqual(conn.obj(), {"chat": {"name": "Steve",
                                               "text": "!" + "x" * 255}})

    def test_chat_goes_only_to_chat_audience(self):
        self.gw.audiences = OTHER_AUDIENCES
        chatty = self.client()
        other = self.client(OTHER_TOKEN)
        self.feed(chat("Steve", "!ai hi"))
        self.assertEqual(chatty.obj()["chat"]["name"], "Steve")
        other.sock.settimeout(0.3)
        with self.assertRaises((socket.timeout, TimeoutError)):
            other.obj()

    def test_two_chat_clients_both_receive(self):
        one = self.client()
        two = self.client()
        self.feed(chat("Steve", "!ai hi"))
        self.assertIn("chat", one.obj())
        self.assertIn("chat", two.obj())

    def test_chat_fed_the_moment_the_ok_arrives_is_delivered(self):
        self.gw.max_connections = 100
        for _ in range(300):
            conn = self.client()
            self.feed(chat("Steve", "!ai hi"))
            self.assertIn("chat", conn.obj())
            conn.close()


class OpTests(GatewayCase):
    def test_pos(self):
        conn = self.client()
        resp = conn.request({"id": 1, "op": "pos", "name": "Steve"})
        self.assertEqual(resp, {"id": 1, "ok": True, "pos": POS})

    def test_dim(self):
        conn = self.client()
        resp = conn.request({"id": 2, "op": "dim", "name": "Steve"})
        self.assertEqual(resp, {"id": 2, "ok": True, "dim": "overworld"})

    def test_tell(self):
        conn = self.client()
        resp = conn.request({"id": 3, "op": "tell", "name": "Steve",
                             "text": "[ai] hi"})
        self.assertEqual(resp, {"id": 3, "ok": True})
        self.assertEqual(
            self.rcon_srv.tellraws,
            [("Steve", '{"text":"[ai] hi","color":"aqua"}')],
        )

    def test_tell_strips_control_characters(self):
        conn = self.client()
        conn.request({"id": 4, "op": "tell", "name": "Steve",
                      "text": "a\nb\x00c"})
        self.assertEqual(self.rcon_srv.tellraws[0][1],
                         '{"text":"abc","color":"aqua"}')

    def test_tell_escapes_non_ascii(self):
        conn = self.client()
        conn.request({"id": 5, "op": "tell", "name": "Steve", "text": "é"})
        self.assertIn("\\u00e9", self.rcon_srv.tellraws[0][1])

    def test_offline_pos_and_dim(self):
        self.rcon_srv.offline.add("Steve")
        conn = self.client()
        for i, op in enumerate(("pos", "dim")):
            resp = conn.request({"id": i, "op": op, "name": "Steve"})
            self.assertEqual(resp, {"id": i, "ok": False,
                                    "error": "offline"})

    def test_bad_name_in_every_op_reaches_no_console(self):
        conn = self.client()
        for i, op in enumerate(("pos", "dim", "tell")):
            req = {"id": i, "op": op, "name": "Bad;Name"}
            if op == "tell":
                req["text"] = "hi"
            resp = conn.request(req)
            self.assertEqual(resp, {"id": i, "ok": False,
                                    "error": "bad_name"})
        self.assertEqual(self.rcon_srv.commands, [])

    def test_too_long_by_characters(self):
        conn = self.client()
        resp = conn.request({"id": 1, "op": "tell", "name": "Steve",
                             "text": "x" * 401})
        self.assertEqual(resp["error"], "too_long")
        self.assertEqual(self.rcon_srv.commands, [])

    def test_too_long_by_bytes(self):
        conn = self.client()
        line = json.dumps({"id": 1, "op": "tell", "name": "Steve",
                           "text": "é" * 400}, ensure_ascii=False)
        conn.send_raw(line.encode("utf-8") + b"\n")
        resp = conn.obj()
        self.assertEqual(resp["error"], "too_long")
        self.assertEqual(self.rcon_srv.commands, [])

    def test_unknown_op_extra_field_and_bad_shapes(self):
        conn = self.client()
        cases = [
            {"id": 1, "op": "ban", "name": "Steve"},
            {"id": 2, "op": "pos", "name": "Steve", "extra": 1},
            {"id": 3, "op": "pos"},
            {"id": "four", "op": "pos", "name": "Steve"},
            {"id": True, "op": "pos", "name": "Steve"},
            {"id": 6, "op": "tell", "name": "Steve", "text": 42},
            {"id": 7, "op": ["pos"], "name": "Steve"},
            {"id": 8, "op": {"name": "pos"}, "name": "Steve"},
        ]
        for req in cases:
            resp = conn.request(req)
            self.assertFalse(resp["ok"])
            self.assertEqual(resp["error"], "bad_request", req)
        conn.send(["not", "a", "dict"])
        self.assertEqual(conn.obj()["error"], "bad_request")
        # The connection survives every bad shape.
        resp = conn.request({"id": 9, "op": "dim", "name": "Steve"})
        self.assertTrue(resp["ok"])
        self.assertEqual(self.rcon_srv.commands,
                         ["data get entity Steve Dimension"])

    def test_op_not_offered_to_audience_is_bad_request(self):
        self.gw.audiences = OTHER_AUDIENCES
        other = self.client(OTHER_TOKEN)
        resp = other.request({"id": 1, "op": "pos", "name": "Steve"})
        self.assertEqual(resp, {"id": 1, "ok": False, "error": "bad_request"})
        resp = other.request({"id": 2, "op": "tell", "name": "Steve",
                              "text": "hi"})
        self.assertEqual(resp, {"id": 2, "ok": True})

    def test_request_log_line(self):
        conn = self.client()
        out = io.StringIO()
        with redirect_stdout(out):
            conn.request({"id": 1, "op": "tell", "name": "TellName",
                          "text": "xyzzy-secret"})
            conn.request({"id": 2, "op": "ban", "name": "TellName"})
            conn.request({"id": 3, "op": "dim", "name": "TellName"})
        raw = out.getvalue()
        reqs = [json.loads(x) for x in raw.splitlines() if "audience" in x]
        self.assertEqual(len(reqs), 3)
        for rec in reqs:
            self.assertEqual(set(rec),
                             {"time", "audience", "op", "outcome", "ms"})
            self.assertEqual(rec["audience"], "minecraft-console")
        self.assertEqual([r["op"] for r in reqs], ["tell", None, "dim"])
        self.assertEqual([r["outcome"] for r in reqs],
                         ["ok", "bad_request", "ok"])
        self.assertNotIn("TellName", raw)
        self.assertNotIn("xyzzy-secret", raw)

    def test_handler_exception_logs_the_five_field_line(self):
        def boom(req_id, req):
            raise ValueError("boom")

        self.gw._op_dim = boom
        conn = self.client()
        out = io.StringIO()
        with redirect_stdout(out):
            conn.send({"id": 1, "op": "dim", "name": "TellName"})
            self.assertTrue(conn.closed())
        raw = out.getvalue()
        reqs = [json.loads(x) for x in raw.splitlines() if "audience" in x]
        self.assertEqual(len(reqs), 1)
        self.assertEqual(set(reqs[0]),
                         {"time", "audience", "op", "outcome", "ms"})
        self.assertEqual(reqs[0]["op"], "dim")
        self.assertEqual(reqs[0]["outcome"], "ValueError")
        self.assertNotIn("TellName", raw)
        self.assertNotIn("request_failed", raw)

    def test_only_the_three_commands_reach_rcon(self):
        conn = self.client()
        conn.request({"id": 1, "op": "pos", "name": "Steve"})
        conn.request({"id": 2, "op": "dim", "name": "Steve"})
        conn.request({"id": 3, "op": "tell", "name": "Steve", "text": "hi"})
        for cmd in self.rcon_srv.commands:
            self.assertRegex(
                cmd, r"^(data get entity \S+ (Pos|Dimension)|tellraw \S+ .*)$"
            )


class LimitTests(GatewayCase):
    def test_twenty_requests_in_ten_seconds(self):
        conn = self.client()
        for i in range(20):
            resp = conn.request({"id": i, "op": "dim", "name": "Steve"})
            self.assertTrue(resp["ok"])
        resp = conn.request({"id": 20, "op": "dim", "name": "Steve"})
        self.assertEqual(resp["error"], "rate")
        self.clock[0] += 10.1
        resp = conn.request({"id": 21, "op": "dim", "name": "Steve"})
        self.assertTrue(resp["ok"])

    def test_thirty_tells_a_minute_per_name(self):
        conn = self.client()
        for i in range(30):
            resp = conn.request({"id": i, "op": "tell", "name": "Steve",
                                 "text": "hi"})
            self.assertTrue(resp["ok"])
            self.clock[0] += 0.6
        resp = conn.request({"id": 30, "op": "tell", "name": "Steve",
                             "text": "hi"})
        self.assertEqual(resp["error"], "rate")
        resp = conn.request({"id": 31, "op": "tell", "name": "Alex",
                             "text": "hi"})
        self.assertTrue(resp["ok"])

    def test_line_cap_closes(self):
        conn = self.client()
        big = json.dumps({"id": 1, "op": "dim", "name": "Steve",
                          "pad": "x" * 2050})
        conn.send_raw(big.encode("utf-8") + b"\n")
        self.assertTrue(conn.closed())

    def test_unparseable_line_closes(self):
        conn = self.client()
        conn.send_raw(b"this is not json\n")
        self.assertTrue(conn.closed())

    def test_connection_cap(self):
        conns = [self.client() for _ in range(4)]
        fifth = Client(self.gw.port)
        self.addCleanup(fifth.close)
        fifth.send({"auth": GOOD_TOKEN})
        self.assertTrue(fifth.closed())
        for conn in conns:
            self.assertFalse(conn.closed())

    def test_connection_closes_after_an_hour(self):
        self.gw.max_age = 5
        conn = self.client()
        self.clock[0] += 10
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not conn.closed(wait=0.3):
            pass
        self.assertTrue(conn.closed(wait=0.3))

    def test_accepted_socket_has_keepalive(self):
        self.client()
        with self.gw._conns_lock:
            (conn,) = self.gw._conns
        self.assertTrue(conn.sock.getsockopt(socket.SOL_SOCKET,
                                             socket.SO_KEEPALIVE))
        for names, seconds in gateway._KEEPALIVE:
            options = [getattr(socket, n) for n in names
                       if hasattr(socket, n)]
            self.assertTrue(options, names)
            self.assertEqual(
                conn.sock.getsockopt(socket.IPPROTO_TCP, options[0]), seconds)

    def test_stopped_gateway_refuses_connections(self):
        self.gw.stop()
        with self.assertRaises(ConnectionRefusedError):
            Client(self.gw.port)

    def test_failed_accept_is_retried(self):
        calls = []
        real = self.gw._accept_conn

        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise OSError("boom")
            return real()

        self.gw._accept_conn = flaky
        with redirect_stdout(io.StringIO()):
            conn = self.client()
        resp = conn.request({"id": 1, "op": "dim", "name": "Steve"})
        self.assertTrue(resp["ok"])
        self.assertGreaterEqual(len(calls), 1)

    def test_tell_table_drops_aged_names(self):
        conn = self.client()
        conn.request({"id": 1, "op": "tell", "name": "Steve", "text": "hi"})
        self.assertEqual(set(self.gw._tells), {"Steve"})
        self.clock[0] += 61
        conn.request({"id": 2, "op": "tell", "name": "Alex", "text": "hi"})
        self.assertEqual(set(self.gw._tells), {"Alex"})

    def test_stdout_has_no_token(self):
        out = io.StringIO()
        with redirect_stdout(out):
            conn = self.client()
            conn.request({"id": 1, "op": "dim", "name": "Steve"})
        for word in (GOOD_TOKEN, SA_TOKEN, RCON_PASSWORD):
            self.assertNotIn(word, out.getvalue())


class FailureTests(GatewayCase):
    def test_rcon_down_answers_console_and_recovers(self):
        self.rcon_srv.close()
        conn = self.client()
        resp = conn.request({"id": 1, "op": "pos", "name": "Steve"})
        self.assertEqual(resp, {"id": 1, "ok": False, "error": "console"})
        self.rcon_srv = FakeRcon(RCON_PASSWORD, port=self.rcon_srv.port)
        self.rcon_srv.start()
        self.addCleanup(self.rcon_srv.close)
        resp = conn.request({"id": 2, "op": "pos", "name": "Steve"})
        self.assertEqual(resp, {"id": 2, "ok": True, "pos": POS})

    def test_read_retried_once_on_dropped_rcon(self):
        conn = self.client()
        resp = conn.request({"id": 1, "op": "dim", "name": "Steve"})
        self.assertTrue(resp["ok"])
        sessions = self.rcon_srv.sessions
        self.rcon_srv.drop()
        resp = conn.request({"id": 2, "op": "dim", "name": "Steve"})
        self.assertEqual(resp, {"id": 2, "ok": True, "dim": "overworld"})
        self.assertGreater(self.rcon_srv.sessions, sessions)

    def test_run_survives_a_failing_poll(self):
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) <= 2:
                raise OSError("gone")
            self.gw._stopping.set()

        self.gw.broadcast_once = flaky
        with redirect_stdout(io.StringIO()) as out:
            self.gw.run()
        self.assertGreaterEqual(len(calls), 3)
        self.assertEqual(
            [json.loads(x)["outcome"] for x in out.getvalue().splitlines()],
            ["OSError", "OSError"],
        )


if __name__ == "__main__":
    unittest.main()
