#!/usr/bin/env bash
# Scenario 2: restart every process at once while writes are in flight,
# then verify the ledger.
#
# This exercises the assumption the fleet's disposable scratch stands on:
# a write is durable because a peer holds it, not because a local disk
# does. Restarting every process at once wipes every local copy at the
# same moment, so any acknowledged write whose bucket upload had not
# completed has nowhere left to live. If the verifier finds one, the
# scratch cannot be disposable and each process needs a real volume. This
# is the scenario the storage decision turns on, and scenario 1 passing
# says nothing about it: one dead owner leaves the other disks alive.
#
# The kill is ungraceful for every process at the same time. A rolling or
# graceful restart lets each node seal and hand off before the next goes,
# which is the one path that never tests this assumption.
#
# Two failure shapes are both findings here: acknowledged reads that come
# back missing (loss), and reads that never come back at all (the recovery
# gate holds the cell closed without follower witnesses; the verifier
# reports those as missing after its deadline, and the run log will show
# the cell never served again).
set -euo pipefail
cd "$(dirname "$0")"
source ./lib.sh

gate_require_env GATE_NAMESPACE GATE_POD_SELECTOR GATE_ENDPOINT GATE_LEDGER

echo "scenario 2: restart every process at once with writes in flight."
echo "exercises: the durability assumption behind disposable scratch."
echo "An acknowledged write lost here means each process needs a real"
echo "volume; a clean recovery means ephemeral scratch survives the one"
echo "event that could refute it."
echo "does not exercise: single-owner takeover; that is scenario 1."

echo "preflight: ledger liveness"
gate_ledger_is_live

PODS=()
while IFS= read -r p; do PODS+=("$p"); done < <(gate_pods)
if [ "${#PODS[@]}" -lt 2 ]; then
  echo "fewer than two processes: the scenario needs a full fleet." >&2
  exit 4
fi

echo "kill: all ${#PODS[@]} processes at once at $(date -u +%H:%M:%S)Z"
KILL_TS=$(date +%s)
gate_kubectl delete pod "${PODS[@]}" --grace-period=0 --force

echo "waiting for the fleet to come back ready"
gate_wait_ready 900s
sleep 5
python3 ledger-stats.py --ledger "$GATE_LEDGER" --at "$KILL_TS"

echo "verification: reading back every acknowledged write"
set +e
python3 verify-ledger.py --endpoint "$GATE_ENDPOINT" \
  --ledger "$GATE_LEDGER" --deadline 900
GAP=$?
set -e

echo "result: verifier exit $GAP (0 means zero gap, 3 means a gap above)."
echo "Whatever the gap is, this number is the input to the storage"
echo "decision; report it with the run, do not round it to pass or fail."
exit "$GAP"
