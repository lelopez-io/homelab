#!/usr/bin/env bash
# Demonstrates the measurement against the local stub before it is pointed
# at a fleet: a healthy run shows a zero gap, a takeover-shaped window shows
# unknown writes that landed, and deleted writes are reported by sequence
# number. The last phase is the one that matters: a harness that has never
# reported a loss cannot be trusted to stay silent only when there is none.
set -euo pipefail
cd "$(dirname "$0")"

PORT="${GATE_STUB_PORT:-18777}"
ENDPOINT="http://127.0.0.1:$PORT"
TMP="$(mktemp -d)"
STUB_PID=""
trap 'stop_stub; rm -rf "$TMP"' EXIT

start_stub() { # store file: fresh state, so phases cannot mask each other
  python3 stub-fleet.py --port "$PORT" --store "$1" &
  STUB_PID=$!
  for _ in $(seq 1 50); do
    curl -sf "$ENDPOINT/dump" >/dev/null 2>&1 && return
    sleep 0.1
  done
  echo "stub did not come up" >&2
  exit 2
}

stop_stub() {
  [ -n "$STUB_PID" ] && kill "$STUB_PID" 2>/dev/null || true
  wait "$STUB_PID" 2>/dev/null || true
  STUB_PID=""
}

expect() { # field value file: assert a JSON report field
  python3 -c "
import json
report = json.load(open('$3'))
actual = report['$1']
if isinstance(actual, list): actual = len(actual)
assert actual == $2, f'expected $1 == $2, got {actual}: {report}'
"
}

echo "phase A: healthy run, expect gap 0"
start_stub "$TMP/store-a.json"
python3 ledger-write.py --endpoint "$ENDPOINT" --ledger "$TMP/a.ndjson" \
  --duration 4 --interval 0.02 --timeout 2 >/dev/null
python3 verify-ledger.py --endpoint "$ENDPOINT" --ledger "$TMP/a.ndjson" \
  --deadline 10 --json-out "$TMP/a.json"
expect gap 0 "$TMP/a.json"
expect ack_recovered "$(python3 -c "import json; print(json.load(open('$TMP/a.json'))['ack'])")" "$TMP/a.json"
stop_stub

echo "phase B: hang during writes, expect gap 0 with unknowns present"
start_stub "$TMP/store-b.json"
python3 ledger-write.py --endpoint "$ENDPOINT" --ledger "$TMP/b.ndjson" \
  --duration 6 --interval 0.02 --timeout 1 >/dev/null &
WRITER_PID=$!
sleep 2
curl -sf -X POST "$ENDPOINT/chaos/hang?s=3" >/dev/null
wait "$WRITER_PID"
python3 verify-ledger.py --endpoint "$ENDPOINT" --ledger "$TMP/b.ndjson" \
  --deadline 10 --json-out "$TMP/b.json"
expect gap 0 "$TMP/b.json"
python3 -c "
import json
report = json.load(open('$TMP/b.json'))
assert report['unknown'] > 0, f'expected unknown writes in phase B: {report}'
assert report['unknown_present'] > 0, \
    f'expected unknown writes to have landed: {report}'
"
stop_stub

echo "phase C: five acknowledged writes deleted, expect gap 5 naming them"
start_stub "$TMP/store-c.json"
python3 ledger-write.py --endpoint "$ENDPOINT" --ledger "$TMP/c.ndjson" \
  --duration 3 --interval 0.02 --timeout 2 >/dev/null
REMOVED=$(curl -sf -X POST "$ENDPOINT/chaos/lose?k=5" | python3 -c "import json,sys; print(len(json.load(sys.stdin)['removed']))")
test "$REMOVED" -eq 5
set +e
python3 verify-ledger.py --endpoint "$ENDPOINT" --ledger "$TMP/c.ndjson" \
  --deadline 5 --json-out "$TMP/c.json"
VERDICT=$?
set -e
test "$VERDICT" -eq 3
expect gap 5 "$TMP/c.json"
expect ack_missing 5 "$TMP/c.json"
stop_stub

echo "demo ok: zero gap when healthy, unknowns attributed in the hang"
echo "window, and all five deleted writes reported missing by sequence"
echo "number. This validates the harness; it says nothing about celld."
