#!/usr/bin/env bash
# Shared preflight for the two scenario drivers. Sourced, not executed.

gate_kubectl() {
  kubectl ${GATE_KUBE_CONTEXT:+--context "$GATE_KUBE_CONTEXT"} \
    -n "$GATE_NAMESPACE" "$@"
}

gate_require_env() {
  local missing=0 name
  for name in "$@"; do
    if [ -z "${!name:-}" ]; then
      echo "preflight: $name is required" >&2
      missing=1
    fi
  done
  [ "$missing" -eq 0 ] || exit 2
}

# The kill only means something if a live ledger was recording when it
# happened. Anything killed before that is a stimulus with no measurement,
# the exact result shape this gate exists to avoid.
gate_ledger_is_live() {
  python3 - "$GATE_LEDGER" <<'PY'
import json, os, sys, time
path = sys.argv[1]
try:
    before = os.path.getsize(path)
except OSError:
    sys.exit("preflight: ledger file does not exist; start ledger-write.py first")
time.sleep(3)
after = os.path.getsize(path)
last_done = 0.0
with open(path) as fh:
    for line in fh:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("type") == "write":
            last_done = max(last_done, rec.get("done_ts", 0.0))
if after <= before:
    sys.exit("preflight: ledger is not growing; the writer is not running")
if time.time() - last_done > 15:
    sys.exit("preflight: last recorded write is stale; the writer is not answering")
PY
}

gate_pods() {
  gate_kubectl get pods -l "$GATE_POD_SELECTOR" \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}'
}

# Prints "pod node" pairs; scenario 1 refuses to run when two processes
# share a machine, because a kill there cannot prove peers fail
# independently. Override with --force only to collect a knowingly
# inconclusive run.
gate_pod_placement() {
  gate_kubectl get pods -l "$GATE_POD_SELECTOR" -o json | python3 -c "
import json, sys
pods = json.load(sys.stdin)['items']
if not pods:
    sys.exit('preflight: no pods match GATE_POD_SELECTOR')
for pod in pods:
    print(pod['metadata']['name'], pod['spec'].get('nodeName', 'unscheduled'))
"
}

gate_wait_ready() {
  local timeout="$1"
  gate_kubectl wait --for=condition=ready pod -l "$GATE_POD_SELECTOR" \
    --timeout="$timeout"
}
