#!/usr/bin/env bash
# Scenario 1: kill the process that owns the probe cell while writes are
# in flight, then verify the ledger.
#
# This exercises the independence assumption: a write is durable because a
# peer held it, so the write survives if and only if that peer fails
# independently of the owner. A run where two fleet processes were
# scheduled on one machine proves nothing, because one machine can take
# both copies at once; the placement check below refuses that run unless
# --force asks for a knowingly inconclusive one.
#
# Scenario 1 says nothing about scenario 2's question. Running only this
# one leaves the disposable-scratch assumption untested.
#
# The default kill is ungraceful (grace-period 0). A graceful delete sends
# SIGTERM, and celld hands its cells off cleanly on SIGTERM; that proves
# the handoff path, not the fence and takeover this scenario exists for.
# --graceful runs that other path as a separate, labelled data point.
set -euo pipefail
cd "$(dirname "$0")"
source ./lib.sh

TAKEOVER_S="${GATE_TAKEOVER_S:-5}"
case "$TAKEOVER_S" in
  *[!0-9.]*) echo "GATE_TAKEOVER_S must be a number" >&2; exit 2 ;;
esac
MODE="auto"   # auto: resolve-owner if it works, sweep otherwise
GRACEFUL=0
FORCE_INCONCLUSIVE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --pod) MODE="pod"; POD_NAME="$2"; shift 2 ;;
    --sweep) MODE="sweep"; shift ;;
    --graceful) GRACEFUL=1; shift ;;
    --force) FORCE_INCONCLUSIVE=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

gate_require_env GATE_NAMESPACE GATE_POD_SELECTOR GATE_ENDPOINT GATE_LEDGER

if [ "$GRACEFUL" -eq 1 ]; then
  echo "scenario 1 (graceful variant): SIGTERM handoff, not the takeover"
  echo "the gate asks about. Collected as a separate data point."
else
  echo "scenario 1: kill the process that owns the probe cell."
fi
echo "exercises: the independence assumption. A write is durable because"
echo "a peer held it, and that only protects it if the peer fails"
echo "independently of the owner."
echo "does not exercise: full-fleet restart; that is scenario 2, and"
echo "running only this one leaves the storage question untested."

echo "preflight: ledger liveness"
gate_ledger_is_live

echo "preflight: one process per machine"
PLACEMENT="$(gate_pod_placement)"
echo "$PLACEMENT"
NODES=$(echo "$PLACEMENT" | awk '{print $2}' | sort -u | wc -l | tr -d ' ')
PODS=$(echo "$PLACEMENT" | wc -l | tr -d ' ')
if [ "$NODES" -ne "$PODS" ]; then
  echo "two processes share a machine: a kill here cannot prove peers"
  echo "fail independently, so this run would prove nothing." >&2
  [ "$FORCE_INCONCLUSIVE" -eq 1 ] || exit 4
  echo "continuing anyway because --force was given; mark the run inconclusive."
fi
if [ "$PODS" -lt 2 ]; then
  echo "fewer than two processes: no peer can hold a write." >&2
  exit 4
fi

KILLS=()
case "$MODE" in
  pod) KILLS=("$POD_NAME") ;;
  sweep)
    while IFS= read -r p; do KILLS+=("$p"); done < <(gate_pods)
    ;;
  auto)
    if OWNER=$(./resolve-owner.sh 2>/dev/null); then
      echo "resolve-owner names $OWNER (unconfirmed alpha API; the sweep"
      echo "is the trustworthy path, see README)"
      KILLS=("$OWNER")
    else
      echo "owner not resolvable; sweeping every process so the owner is"
      echo "killed exactly once, with recovery verified between kills"
      while IFS= read -r p; do KILLS+=("$p"); done < <(gate_pods)
    fi
    ;;
esac

TAKEOVER_SEEN=0
for pod in "${KILLS[@]}"; do
  echo "kill: $pod at $(date -u +%H:%M:%S)Z"
  KILL_TS=$(date +%s)
  if [ "$GRACEFUL" -eq 1 ]; then
    gate_kubectl delete pod "$pod"
  else
    gate_kubectl delete pod "$pod" --grace-period=0 --force
  fi
  gate_wait_ready 300s
  sleep 5   # let the writer's in-flight writes settle past the kill
  S=$(python3 ledger-stats.py --ledger "$GATE_LEDGER" --at "$KILL_TS")
  echo "ledger around the kill: $S"
  if echo "$S" | python3 -c "
import json, sys
r = json.load(sys.stdin)
pause = r['next_ack_s'] if r['next_ack_s'] is not None else float('inf')
consistent = pause >= $TAKEOVER_S or r['unknown_writes_in_window'] >= 1
sys.exit(0 if consistent else 1)
"; then
    TAKEOVER_SEEN=1
    echo "this kill is consistent with a takeover (ack pause >= ${TAKEOVER_S}s"
    echo "or writes went unknown): $pod held the probe cell."
  else
    echo "acks continued through this kill; $pod was not the owner."
  fi
done

echo "verification: reading back every acknowledged write"
set +e
python3 verify-ledger.py --endpoint "$GATE_ENDPOINT" \
  --ledger "$GATE_LEDGER" --deadline 600
GAP=$?
set -e

echo "result: verifier exit $GAP (0 means zero gap, 3 means a gap above)."
if [ "$TAKEOVER_SEEN" -eq 0 ] && [ "$MODE" != "pod" ]; then
  echo "no kill produced a takeover window, so the owner was never"
  echo "demonstrated killed: this run is inconclusive even with a zero"
  echo "gap. A zero gap here is the fleet never having been tested."
  exit 4
fi
exit "$GAP"
