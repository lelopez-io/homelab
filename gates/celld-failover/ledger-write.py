#!/usr/bin/env python3
"""Continuous writer for the failover gate's acknowledgement ledger.

Sends one write at a time to the probe application and records the outcome
of every write before sending the next, so the ledger is always complete up
to the last answered write. Three outcomes, by what the client can prove:

  ack      a 200 response whose body names this sequence number. The
           runtime holds each write response until a durability proof
           covers the write, so this answer means the write survives a
           failure. Only acked writes must come back.
  reject   a non-2xx response. The fleet says the work did not start
           (overload and drain answers are defined this way), so the write
           is not acknowledged, though it may still be present.
  unknown  timeout, reset, or an answer that cannot be attributed. The
           write may or may not be durable; an absent acknowledgement does
           not prove the write is absent, so these are reported but never
           counted as lost.

The ledger is newline-delimited JSON, fsynced after every record. Run it
against a healthy fleet and verify before any disruption: a ledger written
afterwards gets written to fit whatever happened.
"""

import argparse
import json
import os
import secrets
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request


def harness_commit():
    try:
        root = os.path.dirname(os.path.abspath(__file__))
        out = subprocess.run(
            ["git", "-C", root, "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--endpoint", required=True,
                    help="base URL of the deployed probe, no trailing slash")
    ap.add_argument("--ledger", required=True, help="ledger file to append")
    ap.add_argument("--run", default=None,
                    help="run id embedded in every value (random if unset)")
    ap.add_argument("--timeout", type=float, default=60.0,
                    help="per-write seconds before a write is unknown; keep "
                         "above the expected takeover window")
    ap.add_argument("--interval", type=float, default=0.0,
                    help="seconds to wait between writes")
    ap.add_argument("--count", type=int, default=0, help="stop after N writes")
    ap.add_argument("--duration", type=float, default=0.0,
                    help="stop after N seconds (0: until SIGTERM/SIGINT)")
    args = ap.parse_args()

    run = args.run or secrets.token_hex(6)
    stopping = False

    def on_signal(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    fd = os.open(args.ledger, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)

    def record(obj):
        os.write(fd, (json.dumps(obj, separators=(",", ":")) + "\n").encode())
        os.fsync(fd)

    counts = {"ack": 0, "reject": 0, "unknown": 0}
    started = time.time()
    record({
        "type": "begin", "run": run, "ts": started, "endpoint": args.endpoint,
        "harness": harness_commit(), "timeout_s": args.timeout,
    })

    seq = 0
    try:
        while not stopping:
            if args.count and seq >= args.count:
                break
            if args.duration and time.time() - started >= args.duration:
                break
            if args.interval and seq > 0:
                time.sleep(args.interval)

            seq += 1
            value = f"{run}:{seq}:{secrets.token_hex(8)}"
            body = json.dumps({"seq": seq, "value": value}).encode()
            req = urllib.request.Request(
                args.endpoint.rstrip("/") + "/write", data=body,
                headers={"Content-Type": "application/json"}, method="POST")

            sent = time.time()
            outcome, status, detail = "unknown", None, ""
            try:
                with urllib.request.urlopen(req, timeout=args.timeout) as resp:
                    status = resp.status
                    payload = json.loads(resp.read().decode())
                if status == 200 and payload.get("seq") == seq \
                        and payload.get("ok") is True:
                    outcome = "ack"
                else:
                    detail = "unattributable-response"
            except urllib.error.HTTPError as exc:
                outcome, status = "reject", exc.code
            except Exception as exc:
                detail = type(exc).__name__

            done = time.time()
            counts[outcome] += 1
            record({
                "type": "write", "run": run, "seq": seq, "value": value,
                "sent_ts": sent, "done_ts": done, "outcome": outcome,
                "status": status, "detail": detail,
                "latency_ms": round((done - sent) * 1000, 1),
            })
            if seq % 20 == 0:
                print(f"seq={seq} ack={counts['ack']} "
                      f"reject={counts['reject']} unknown={counts['unknown']}",
                      file=sys.stderr)
    finally:
        record({"type": "end", "run": run, "ts": time.time(), **counts})
        os.close(fd)

    print(json.dumps({"run": run, **counts}))


if __name__ == "__main__":
    main()
