#!/usr/bin/env python3
"""Local stub for the probe application's HTTP contract.

The acknowledgement ledger and verifier are the measurement the gate runs
on, and a measurement that has never seen a loss cannot be trusted to
report one. This stub speaks the probe's three routes against a JSON file
and adds two fault shapes, so demo-stub.sh can show the ledger and
verifier behaving correctly before they are pointed at a fleet:

  hang   holds each write response for a while after applying it, the
         shape of a takeover window: the write is durable but the answer
         is late, so a short client timeout records unknown for a write
         that is in fact present.
  lose   deletes the most recent acknowledged writes outright, the shape
         the whole gate exists to detect.

The stub is a harness self-check. It mimics two failure shapes; it models
nothing about celld, and a clean demo says nothing about the fleet.
"""

import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs


class Store:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.data = {}
        if os.path.exists(path):
            with open(path) as fh:
                self.data = json.load(fh)
        self.hang_until = 0.0

    def put(self, key, value):
        with self.lock:
            self.data[key] = value
            self._flush()
            delay = self.hang_until - time.time()
        if delay > 0:
            time.sleep(delay)

    def get(self, key):
        with self.lock:
            return self.data.get(key)

    def keys(self):
        with self.lock:
            return sorted(self.data)

    def lose_recent(self, count):
        with self.lock:
            doomed = sorted(self.data)[-count:]
            for key in doomed:
                del self.data[key]
            self._flush()
            return doomed

    def hang(self, seconds):
        with self.lock:
            self.hang_until = time.time() + seconds

    def _flush(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(self.data, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)


def make_handler(store):
    class Handler(BaseHTTPRequestHandler):
        def _json(self, obj, status=200):
            body = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            path = urlparse(self.path).path
            query = parse_qs(urlparse(self.path).query)
            if path == "/write":
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                key = "w/" + str(body["seq"]).zfill(10)
                store.put(key, body["value"])
                self._json({"seq": body["seq"], "ok": True})
            elif path == "/chaos/hang":
                seconds = float(query.get("s", ["5"])[0])
                store.hang(seconds)
                self._json({"hang_s": seconds})
            elif path == "/chaos/lose":
                count = int(query.get("k", ["1"])[0])
                self._json({"removed": store.lose_recent(count)})
            else:
                self._json({"error": "unknown route"}, status=404)

        def do_GET(self):
            path = urlparse(self.path).path
            query = parse_qs(urlparse(self.path).query)
            if path == "/read":
                seq = query.get("seq", [""])[0]
                value = store.get("w/" + str(seq).zfill(10))
                if value is None:
                    self._json({"seq": int(seq), "found": False}, status=404)
                else:
                    self._json({"seq": int(seq), "found": True,
                                "value": value})
            elif path == "/dump":
                self._json({"keys": store.keys()})
            else:
                self._json({"error": "unknown route"}, status=404)

        def log_message(self, fmt, *args):
            pass

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=18777)
    ap.add_argument("--store", required=True, help="JSON file for state")
    args = ap.parse_args()
    store = Store(args.store)
    server = ThreadingHTTPServer(("127.0.0.1", args.port),
                                 make_handler(store))
    print(f"stub fleet on 127.0.0.1:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
