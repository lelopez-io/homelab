#!/usr/bin/env python3
"""Attribute a disruption to what the ledger saw around it.

Given a kill timestamp, reports how the acknowledgement stream behaved
after it: seconds until the next acknowledged write, the longest silence
between acknowledgements, and how many writes went unknown. Scenario 1
uses these numbers to tell the kill that took the owner apart from kills
that only took a follower; the numbers are evidence for the operator, not
a verdict.
"""

import argparse
import json


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--at", type=float, required=True,
                    help="kill time, seconds since epoch")
    ap.add_argument("--window", type=float, default=300.0,
                    help="seconds after the kill to consider")
    args = ap.parse_args()

    acks, unknowns = [], 0
    with open(args.ledger) as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") != "write":
                continue
            if rec["outcome"] == "ack":
                acks.append((rec["sent_ts"], rec["done_ts"]))
            elif rec["outcome"] == "unknown" \
                    and args.at <= rec["sent_ts"] <= args.at + args.window:
                unknowns += 1

    later = [done for _, done in acks if done >= args.at]
    next_ack = min(later) - args.at if later else None

    silence = 0.0
    for (sent_a, done_a), (sent_b, done_b) in zip(acks, acks[1:]):
        if done_b >= args.at and sent_b <= args.at + args.window:
            silence = max(silence, done_b - done_a)

    print(json.dumps({
        "at": args.at,
        "next_ack_s": round(next_ack, 2) if next_ack is not None else None,
        "longest_ack_silence_s": round(silence, 2),
        "unknown_writes_in_window": unknowns,
    }))


if __name__ == "__main__":
    main()
