#!/usr/bin/env python3
"""Tests for transcode-gate.py against a fake media server and a fake
Kubernetes API on localhost. No cluster, no real server, no real token.
"""

import ast
import http.client
import importlib.util
import io
import json
import os
import re
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, "transcode-gate.py")

OWNER = "fake-owner-token"
GATE_TOK = "fake-gate-token"
CALLER = "fake-caller-token"
WRONG_AUD = "fake-wrong-aud-token"
DENIED = "fake-denied-token"
ERRTOK = "fake-error-token"
CACHED = "fake-cached-token"
USERNAME = "system:serviceaccount:media:puller"
AUDIENCE = "plex-transcode"

START = "/video/:/transcode/universal/start.m3u8"
STOP = "/video/:/transcode/universal/stop"
SESS = "/video/:/transcode/universal/session/sess123"

spec = importlib.util.spec_from_file_location("transcode_gate", GATE)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def req(port, method, path, headers=None, timeout=30):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    conn.request(method, path, headers=headers or {})
    r = conn.getresponse()   # the method name predates PEP 8
    data = r.read()
    out = (r.status, {k.lower(): v for k, v in r.getheaders()}, data)
    conn.close()
    return out


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def hdr(pairs, name):
    return dict(pairs).get(name)


def hdr_count(pairs, name):
    return sum(1 for k, _ in pairs if k == name)


class QuietServer(ThreadingHTTPServer):
    # A client that went away mid-answer is routine, not a traceback.
    def handle_error(self, request, client_address):
        pass


class FakeMedia(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    lock = threading.Lock()
    seen = []

    def log_message(self, *args):
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

    def do_GET(self):
        with self.lock:
            type(self).seen.append(
                (self.path, [(k.lower(), v)
                             for k, v in self.headers.items()]))
        u = urllib.parse.urlsplit(self.path)
        if u.path == START:
            return self._send(200, b"#EXTM3U\nsession/sess123/index.m3u8\n",
                              "application/vnd.apple.mpegurl")
        if u.path == STOP:
            return self._send(200, b"ok")
        if u.path == SESS + "/index.m3u8":
            return self._send(200, b"#EXTM3U\n#EXT-X-ENDLIST\n",
                              "application/vnd.apple.mpegurl")
        if u.path == SESS + "/redir/index.m3u8":
            return self._send(302, b"", headers={"Location": SESS + "/i"})
        m = re.match(r"^/video/:/transcode/universal/session/[^/]+/.+\.ts$",
                     u.path)
        if m:
            body = (("SEG") * 64).encode()
            rng = self.headers.get("Range")
            if rng:
                rm = re.match(r"bytes=(\d+)-(\d+)$", rng)
                a, b = int(rm.group(1)), int(rm.group(2))
                return self._send(
                    206, body[a:b + 1], "video/mp2t",
                    {"Content-Range": "bytes %d-%d/%d"
                                      % (a, b, len(body))})
            return self._send(200, body, "video/mp2t")
        self._send(404, b"nope")


class FakeKube(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    lock = threading.Lock()
    reviews = []
    behavior = {
        CALLER: ("ok", [AUDIENCE], USERNAME),
        WRONG_AUD: ("ok", ["some-other-audience"], USERNAME),
        DENIED: ("deny", None, None),
        ERRTOK: ("error", None, None),
        CACHED: ("ok", [AUDIENCE], USERNAME),
    }

    def log_message(self, *args):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != gate.REVIEW_PATH:
            return self._send(404, {})
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n).decode() or "{}")
        with self.lock:
            type(self).reviews.append(
                (self.headers.get("Authorization"), body))
        token = body.get("spec", {}).get("token", "")
        mode, aud, user = type(self).behavior.get(token, ("deny", None, None))
        if mode == "error":
            return self._send(500, {})
        if mode == "deny":
            return self._send(200, {"status": {"authenticated": False}})
        self._send(200, {"status": {"authenticated": True,
                                    "audiences": aud,
                                    "user": {"username": user}}})


def make_self_signed(folder):
    # The standard library cannot mint certificates, so this needs the
    # openssl binary; the test skips when it is absent or unusable.
    if shutil.which("openssl") is None:
        return None
    os.makedirs(folder, exist_ok=True)
    key = os.path.join(folder, "key.pem")
    cert = os.path.join(folder, "cert.pem")
    r = subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048",
         "-keyout", key, "-out", cert, "-days", "1", "-nodes",
         "-subj", "/CN=127.0.0.1",
         "-addext", "subjectAltName=IP:127.0.0.1"],
        capture_output=True, timeout=30)
    if r.returncode != 0:
        return None
    return cert, key


def review_count(token):
    with FakeKube.lock:
        return sum(1 for _, b in FakeKube.reviews
                   if b.get("spec", {}).get("token") == token)


class GateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="gate-test-")
        sa = os.path.join(cls.tmp, "sa")
        os.makedirs(sa)
        with open(os.path.join(sa, "token"), "w") as f:
            f.write(GATE_TOK + "\n")
        with open(os.path.join(sa, "ca.crt"), "w") as f:
            f.write("unused over plain http\n")
        cls.prefs = os.path.join(cls.tmp, "Preferences.xml")
        with open(cls.prefs, "w") as f:
            f.write('<Preferences PlexOnlineToken="%s"/>\n' % OWNER)
        cls.media = QuietServer(("127.0.0.1", 0), FakeMedia)
        cls.kube = QuietServer(("127.0.0.1", 0), FakeKube)
        threading.Thread(target=cls.media.serve_forever, daemon=True).start()
        threading.Thread(target=cls.kube.serve_forever, daemon=True).start()
        gate.PLEX = "http://127.0.0.1:%d" % cls.media.server_address[1]
        gate.KUBE_API = "http://127.0.0.1:%d" % cls.kube.server_address[1]
        gate.SA_DIR = sa
        gate.PREFS = cls.prefs
        gate.AUDIENCE = AUDIENCE
        cls.srv = gate.Server(("127.0.0.1", 0), gate.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.real_stderr = sys.stderr
        cls.captured = io.StringIO()
        sys.stderr = cls.captured

    @classmethod
    def tearDownClass(cls):
        sys.stderr = cls.real_stderr
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.media.shutdown()
        cls.kube.shutdown()

    def bearer(self, token=CALLER):
        return {"Authorization": "Bearer " + token}

    def test_00_py39_grammar(self):
        with open(GATE) as f:
            ast.parse(f.read(), feature_version=(3, 9))

    def test_01_allowed_paths_forward(self):
        cases = [
            (START + "?path=/library/metadata/123&protocol=hls", 200,
             "application/vnd.apple.mpegurl"),
            (SESS + "/index.m3u8", 200, "application/vnd.apple.mpegurl"),
            (SESS + "/sub/seg0.ts", 200, "video/mp2t"),
            (STOP + "?session=sess123", 200, "text/plain"),
        ]
        before = len(FakeMedia.seen)
        for path, code, ctype in cases:
            status, hdrs, body = req(self.port, "GET", path,
                                     self.bearer())
            self.assertEqual(status, code, path)
            self.assertEqual(hdrs["content-type"], ctype)
        got = FakeMedia.seen[before:]
        self.assertEqual(len(got), len(cases))
        for path, headers in got:
            self.assertEqual(hdr(headers, "x-plex-token"), OWNER)
            self.assertIsNone(hdr(headers, "authorization"))
        self.assertEqual(FakeMedia.seen[before][0],
                         START + "?path=/library/metadata/123&protocol=hls")

    def test_02_redirect_not_followed(self):
        status, _, _ = req(self.port, "GET", SESS + "/redir/index.m3u8",
                           self.bearer())
        self.assertEqual(status, 302)

    def test_03_not_found_before_auth(self):
        before = len(FakeKube.reviews)
        bad = ["/library/sections", "/security/token", "/",
               SESS + "/../index.m3u8", SESS + "/./index.m3u8",
               SESS + "/a//index.m3u8", SESS + "/",
               "/video/:/transcode/universal/session//index.m3u8",
               "/video/:/transcode/universal/start.m3u8x",
               "/video/:/transcode/other/start.m3u8"]
        for path in bad:
            status, _, body = req(self.port, "GET", path, self.bearer())
            self.assertEqual(status, 404, path)
            self.assertEqual(body, b"not found\n", path)
        for method in ("POST", "PUT", "DELETE", "HEAD", "OPTIONS"):
            status, _, _ = req(self.port, method, START, self.bearer())
            self.assertEqual(status, 404, method)
        self.assertEqual(len(FakeKube.reviews), before)

    def test_04_auth_matrix(self):
        status, _, _ = req(self.port, "GET", STOP)
        self.assertEqual(status, 401)
        for header in ("Token abc", "Bearer", "bearer", "Bearer "):
            status, _, _ = req(self.port, "GET", STOP,
                               {"Authorization": header})
            self.assertEqual(status, 401, header)
        status, _, _ = req(self.port, "GET", STOP, self.bearer(DENIED))
        self.assertEqual(status, 401)
        status, _, _ = req(self.port, "GET", STOP, self.bearer(WRONG_AUD))
        self.assertEqual(status, 401)
        status, _, _ = req(self.port, "GET", STOP, self.bearer(ERRTOK))
        self.assertEqual(status, 502)

    def test_05_review_body_and_gate_token(self):
        with FakeKube.lock:
            FakeKube.reviews[:] = []
        req(self.port, "GET", STOP, self.bearer(CACHED + "-body"))
        FakeKube.behavior[CACHED + "-body"] = ("ok", [AUDIENCE], USERNAME)
        req(self.port, "GET", STOP, self.bearer(CACHED + "-body"))
        with FakeKube.lock:
            reviews = list(FakeKube.reviews)
        self.assertTrue(reviews)
        auth, body = reviews[0]
        self.assertEqual(auth, "Bearer " + GATE_TOK)
        self.assertEqual(body["apiVersion"], "authentication.k8s.io/v1")
        self.assertEqual(body["kind"], "TokenReview")
        self.assertEqual(body["spec"]["audiences"], [AUDIENCE])

    def test_06_cache(self):
        gate.REVIEW_TTL_S = 0.3
        try:
            n0 = review_count(CACHED)
            req(self.port, "GET", STOP, self.bearer(CACHED))
            req(self.port, "GET", STOP, self.bearer(CACHED))
            self.assertEqual(review_count(CACHED), n0 + 1)
            time.sleep(0.4)
            req(self.port, "GET", STOP, self.bearer(CACHED))
            self.assertEqual(review_count(CACHED), n0 + 2)
        finally:
            gate.REVIEW_TTL_S = 60.0
        n0 = review_count(DENIED)
        req(self.port, "GET", STOP, self.bearer(DENIED))
        req(self.port, "GET", STOP, self.bearer(DENIED))
        self.assertEqual(review_count(DENIED), n0 + 2)

    def test_07_start_query_validation(self):
        good = "path=/library/metadata/123"
        for query, code in [
                ("path=/library/sections", 400),
                ("path=" + urllib.parse.quote("http://x/library/metadata/1"),
                 400),
                ("path=/library/metadata/1&path=/library/metadata/2", 400),
                ("", 400),
                ("protocol=hls", 400),
                (good, 200)]:
            status, _, _ = req(self.port, "GET", START + "?" + query,
                               self.bearer())
            self.assertEqual(status, code, query)

    def test_08_caller_token_never_forwarded(self):
        with FakeMedia.lock:
            FakeMedia.seen[:] = []
        headers = {"Authorization": "Bearer " + CALLER,
                   "x-pLeX-tOkEn": "fake-caller-plex-token",
                   "X-Plex-Product": "TestProduct",
                   "X-Plex-Client-Identifier": "abc",
                   "Range": "bytes=0-9",
                   "Accept": "*/*"}
        status, hdrs, body = req(
            self.port, "GET",
            SESS + "/sub/seg0.ts?X-Plex-Token=evil&foo=bar", headers)
        self.assertEqual(status, 206)
        self.assertEqual(len(body), 10)
        self.assertIn("content-range", hdrs)
        path, mhdrs = FakeMedia.seen[-1]
        self.assertEqual(hdr_count(mhdrs, "x-plex-token"), 1)
        self.assertEqual(hdr(mhdrs, "x-plex-token"), OWNER)
        self.assertIsNone(hdr(mhdrs, "authorization"))
        self.assertEqual(hdr(mhdrs, "x-plex-product"), "TestProduct")
        self.assertEqual(hdr(mhdrs, "x-plex-client-identifier"), "abc")
        self.assertEqual(hdr(mhdrs, "range"), "bytes=0-9")
        self.assertEqual(hdr(mhdrs, "accept"), "*/*")
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
        self.assertEqual(q.get("foo"), ["bar"])
        for key in q:
            self.assertNotEqual(key.lower(), "x-plex-token")

    def test_08b_encoded_token_params(self):
        for query in ("X-Plex-Toke%6E=evil", "%58-Plex-Token=evil",
                      "x-plex-token=evil", "X-PLEX-TOKEN=evil"):
            with FakeMedia.lock:
                FakeMedia.seen[:] = []
            status, _, _ = req(self.port, "GET",
                               SESS + "/sub/seg0.ts?" + query,
                               self.bearer())
            self.assertEqual(status, 200)
            path, _ = FakeMedia.seen[-1]
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
            self.assertEqual(q, {}, query)
            self.assertEqual(path, SESS + "/sub/seg0.ts")

    def test_09_media_down(self):
        real = gate.PLEX
        gate.PLEX = "http://127.0.0.1:1"
        try:
            status, _, _ = req(self.port, "GET", STOP, self.bearer())
            self.assertEqual(status, 502)
        finally:
            gate.PLEX = real

    def test_09b_body_cap(self):
        real = gate.MAX_BODY
        gate.MAX_BODY = 100  # the segment body is 192 bytes
        try:
            status, _, _ = req(self.port, "GET", SESS + "/sub/seg0.ts",
                               self.bearer())
            self.assertEqual(status, 502)
        finally:
            gate.MAX_BODY = real

    def test_10_api_down(self):
        real = gate.KUBE_API
        gate.KUBE_API = "http://127.0.0.1:1"
        try:
            status, _, _ = req(self.port, "GET", STOP,
                               self.bearer("fake-unused-token"))
            self.assertEqual(status, 502)
        finally:
            gate.KUBE_API = real

    def test_11_idle_timeout(self):
        gate.Handler.timeout = 1.0
        srv = gate.Server(("127.0.0.1", 0), gate.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            s = socket.create_connection(
                ("127.0.0.1", srv.server_address[1]), timeout=10)
            t0 = time.monotonic()
            closed = s.recv(1)
            took = time.monotonic() - t0
            s.close()
            self.assertEqual(closed, b"")
            self.assertGreater(took, 0.5)
            self.assertLess(took, 8)
            status, _, _ = req(srv.server_address[1], "GET", STOP,
                               self.bearer())
            self.assertEqual(status, 200)
        finally:
            gate.Handler.timeout = gate.IDLE_TIMEOUT_S
            srv.shutdown()
            srv.server_close()

    def test_12_serve_subprocess_sigterm(self):
        port = free_port()
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
               "PLEX": gate.PLEX,
               "PORT": str(port),
               "AUDIENCE": AUDIENCE,
               "PREFS": self.prefs,
               "KUBE_API": gate.KUBE_API,
               "SA_DIR": gate.SA_DIR,
               "IDLE_TIMEOUT": "30"}
        proc = subprocess.Popen([sys.executable, GATE, "serve"], env=env,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 10
            while True:
                if proc.poll() is not None:
                    raise AssertionError(
                        "gate exited early: %s" % proc.stderr.read())
                try:
                    s = socket.create_connection(("127.0.0.1", port),
                                                 timeout=1)
                    s.close()
                    break
                except OSError:
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.1)
            status, _, body = req(
                port, "GET", START + "?path=/library/metadata/7",
                self.bearer())
            self.assertEqual(status, 200)
            self.assertIn(b"#EXTM3U", body)
        finally:
            proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=10)
        self.assertEqual(proc.returncode, 0)
        err = proc.stderr.read()
        proc.stderr.close()
        self.assertIn("listening", err)
        self.assertIn("stopped", err)
        for secret in (OWNER, GATE_TOK, CALLER):
            self.assertNotIn(secret, err)
        for leak in ("/video/:", "sess123", "library/metadata", "Traceback"):
            self.assertNotIn(leak, err)

    def test_13_api_tls(self):
        # The review call over verified TLS: good CA answers, wrong CA
        # is a 502. Needs the openssl binary for a throwaway certificate.
        made = make_self_signed(os.path.join(self.tmp, "tls1"))
        if made is None:
            self.skipTest("openssl binary unavailable or unusable")
        cert, key = made
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        srv = QuietServer(("127.0.0.1", 0), FakeKube)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        sa = os.path.join(self.tmp, "sa-tls")
        os.makedirs(sa)
        shutil.copy(cert, os.path.join(sa, "ca.crt"))
        with open(os.path.join(sa, "token"), "w") as f:
            f.write(GATE_TOK)
        FakeKube.behavior["fake-tls-token"] = ("ok", [AUDIENCE], USERNAME)
        FakeKube.behavior["fake-tls-token-2"] = ("ok", [AUDIENCE], USERNAME)
        real_api, real_sa = gate.KUBE_API, gate.SA_DIR
        gate.KUBE_API = "https://127.0.0.1:%d" % srv.server_address[1]
        gate.SA_DIR = sa
        try:
            status, _, _ = req(self.port, "GET", STOP,
                               self.bearer("fake-tls-token"))
            self.assertEqual(status, 200)
            other = make_self_signed(os.path.join(self.tmp, "tls2"))
            shutil.copy(other[0], os.path.join(sa, "ca.crt"))
            status, _, _ = req(self.port, "GET", STOP,
                               self.bearer("fake-tls-token-2"))
            self.assertEqual(status, 502)
        finally:
            gate.KUBE_API, gate.SA_DIR = real_api, real_sa
            srv.shutdown()
            srv.server_close()

    def test_99_stderr_hygiene(self):
        err = self.captured.getvalue()
        self.assertIn("served media/puller", err)
        self.assertIn("refused", err)
        for secret in (OWNER, GATE_TOK, CALLER, WRONG_AUD, DENIED, ERRTOK,
                       CACHED, "fake-caller-plex-token", "evil"):
            self.assertNotIn(secret, err)
        for leak in ("/video/:", "sess123", "library/metadata", "path=",
                     "Traceback"):
            self.assertNotIn(leak, err)


if __name__ == "__main__":
    unittest.main(verbosity=2)
