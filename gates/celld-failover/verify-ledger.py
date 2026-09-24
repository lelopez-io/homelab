#!/usr/bin/env python3
"""Read-back verifier for the failover gate.

For every write the ledger marks ack, reads the value back through the
probe and compares it exactly. Reports counts rather than a verdict: how
many writes were acknowledged, how many came back, and the gap. A gap of
zero is the promise kept in this run; anything else is the finding, and
the missing sequence numbers are listed.

Unacknowledged writes (unknown and reject) are read once at the end and
reported as information only: an absent acknowledgement does not prove a
write is absent, so landing ones are not a failure and neither are lost
ones.

Reads retry until the deadline, because a takeover can hold recovery open
for minutes. A sequence that still reads missing after the deadline counts
as missing. Exit code is 0 when the gap is zero, 3 when it is not, and 2
on harness misuse; the exit code exists for scripting and is not the
result.
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def load_ledger(path):
    acked, rejected, unknown = {}, {}, {}
    runs = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("type") == "begin":
                runs.append(rec.get("run"))
            if rec.get("type") != "write":
                continue
            bucket = {"ack": acked, "reject": rejected,
                      "unknown": unknown}[rec["outcome"]]
            bucket[rec["seq"]] = rec["value"]
    return runs, acked, rejected, unknown


def read_seq(endpoint, seq, timeout):
    url = f"{endpoint.rstrip('/')}/read?seq={seq}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode())
        if payload.get("found") is True:
            return "present", payload.get("value")
        return "missing", None
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return "missing", None
        return "error", f"http-{exc.code}"
    except Exception as exc:
        return "error", type(exc).__name__


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--endpoint", required=True,
                    help="base URL of the deployed probe, no trailing slash")
    ap.add_argument("--ledger", required=True, help="ledger file to verify")
    ap.add_argument("--deadline", type=float, default=600.0,
                    help="seconds to keep retrying reads for recovery")
    ap.add_argument("--read-timeout", type=float, default=30.0)
    ap.add_argument("--json-out", default=None,
                    help="also write the report as JSON to this path")
    args = ap.parse_args()

    runs, acked, rejected, unknown = load_ledger(args.ledger)
    if not acked and not unknown and not rejected:
        print("ledger has no write records; nothing was measured",
              file=sys.stderr)
        sys.exit(2)

    deadline = time.time() + args.deadline
    recovered, mismatched = {}, []
    unresolved = dict(acked)
    while unresolved:
        for seq in sorted(list(unresolved)):
            state, value = read_seq(args.endpoint, seq, args.read_timeout)
            if state == "present":
                if value == unresolved[seq]:
                    recovered[seq] = value
                else:
                    mismatched.append(seq)
                del unresolved[seq]
        if not unresolved:
            break
        if time.time() >= deadline:
            break
        time.sleep(1.0)
    missing = sorted(unresolved)

    def presence(writes):
        present, absent, undetermined = [], [], []
        for seq in sorted(writes):
            state, _ = read_seq(args.endpoint, seq, args.read_timeout)
            if state == "present":
                present.append(seq)
            elif state == "missing":
                absent.append(seq)
            else:
                undetermined.append(seq)
        return present, absent, undetermined

    unk_present, unk_absent, unk_undet = presence(unknown)
    rej_present, rej_absent, rej_undet = presence(rejected)

    gap = len(missing) + len(mismatched)
    report = {
        "ledger": args.ledger,
        "runs": runs,
        "attempted": len(acked) + len(rejected) + len(unknown),
        "ack": len(acked),
        "reject": len(rejected),
        "unknown": len(unknown),
        "ack_recovered": len(recovered),
        "ack_missing": missing,
        "ack_mismatched": sorted(mismatched),
        "gap": gap,
        "unknown_present": len(unk_present),
        "unknown_absent": len(unk_absent),
        "unknown_undetermined": len(unk_undet),
        "reject_present": len(rej_present),
        "reject_absent": len(rej_absent),
        "reject_undetermined": len(rej_undet),
    }

    def seqs(items):
        shown = ", ".join(str(s) for s in items[:50])
        return shown + (f" (+{len(items) - 50} more)" if len(items) > 50 else "")

    print(f"ledger: {args.ledger}")
    print(f"attempted: {report['attempted']} "
          f"(ack {report['ack']}, reject {report['reject']}, "
          f"unknown {report['unknown']})")
    print(f"acknowledged writes recovered: {report['ack_recovered']}"
          f"/{report['ack']}")
    if missing:
        print(f"acknowledged writes missing: {len(missing)} [{seqs(missing)}]")
    if mismatched:
        print(f"acknowledged writes with wrong value: {len(mismatched)} "
              f"[{seqs(sorted(mismatched))}]")
    print(f"gap: {gap}")
    print(f"unacknowledged writes that landed anyway: "
          f"unknown {len(unk_present)}/{len(unknown)}, "
          f"reject {len(rej_present)}/{len(rejected)}")
    if unk_undet or rej_undet:
        print(f"reads left undetermined: unknown {len(unk_undet)}, "
              f"reject {len(rej_undet)} (transport errors, not findings)")

    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump(report, fh, indent=2)
            fh.write("\n")

    sys.exit(3 if gap else 0)


if __name__ == "__main__":
    main()
