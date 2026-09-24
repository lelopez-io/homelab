#!/usr/bin/env bash
# Best-effort answer to "which pod owns the probe cell right now".
#
# There is no documented cell-to-owner lookup. celld's cell list names the
# cells, the node leases name the nodes, and nothing public joins them, so
# this asks each pod's internal operator API whether it holds a cell of the
# probe's script. Under continuous writes the probe cell is resident on
# exactly its owner, so exactly one pod should answer with one.
#
# The /state operator API is documented as alpha and its shape here is a
# tolerant guess, so scenario 1 treats a nonzero exit as normal and falls
# back to the sweep. Confirm this against the live fleet before trusting
# the attribution; the sweep does not depend on it.
set -euo pipefail
cd "$(dirname "$0")"
source ./lib.sh
gate_require_env GATE_NAMESPACE GATE_POD_SELECTOR

PORT="${GATE_INTERNAL_PORT:-8081}"
SCRIPT="${GATE_PROBE_SCRIPT:-celld-gate-probe}"

owner=""
while read -r pod; do
  state=$(gate_kubectl exec "$pod" -- \
    sh -c "curl -sf http://127.0.0.1:$PORT/state" 2>/dev/null) || continue
  held=$(printf '%s' "$state" | python3 -c "
import json, sys
want = '$SCRIPT'
found = 0
def walk(node):
    global found
    if isinstance(node, dict):
        for key, value in node.items():
            if want in str(key) and isinstance(value, dict):
                cells = value.get('cells')
                if isinstance(cells, (int, float)):
                    found = max(found, cells)
            walk(value)
    elif isinstance(node, list):
        for item in node:
            walk(item)
try:
    walk(json.load(sys.stdin))
except Exception:
    pass
print(found)
")
  if [ "$held" -ge 1 ] 2>/dev/null; then
    if [ -n "$owner" ]; then
      echo "resolve-owner: $owner and $pod both report the cell; refusing" >&2
      exit 1
    fi
    owner="$pod"
  fi
done < <(gate_pods)

if [ -z "$owner" ]; then
  echo "resolve-owner: no pod reports the probe cell; use the sweep" >&2
  exit 1
fi
echo "$owner"
