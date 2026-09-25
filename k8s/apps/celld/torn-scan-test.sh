#!/usr/bin/env bash
# End-to-end test for torn-scan.sh against a real versitygw posix backend.
# Fixtures are plain files under the backend root; the gateway serves them
# as objects. Needs docker; runs entirely in containers and a temp dir.
#
#   ./torn-scan-test.sh
#
# The scanner image is the CronJob's, run with the pod's user and a
# read-only root, so what passes here passes in the cluster.
set -u
cd "$(dirname "$0")"

GW_IMAGE="ghcr.io/versity/versitygw:v1.8.0@sha256:30292fc2eeacc67a36993b01f7a7a5e3361a19cced0e80c1d71cfa2a4b0a2499"
SCAN_IMAGE="amazon/aws-cli:2.37.3@sha256:83f8ffe939569070c5b66d22231862ab78718766d9d8e4c44ca84dd0be5569a5"
SCANNER="$(pwd)/torn-scan.sh"
NET="torn-scan-test"
ROOT="$(mktemp -d)/data"
FAILURES=0

mkdir -p "$ROOT/cells/fleet" "$ROOT/cells/nodes/archive" "$ROOT/cells/apps/ledger"
# torn, allowlisted exact path
head -c 4096 /dev/zero >"$ROOT/cells/fleet/capacity-v1.json"
# torn, allowlisted glob
head -c 2048 /dev/zero >"$ROOT/cells/nodes/node-a.json"
# healthy JSON
printf '{"id":"node-b","zone":"z"}' >"$ROOT/cells/nodes/node-b.json"
# legitimately empty
touch "$ROOT/cells/nodes/node-c.json"
# torn but nested under nodes/: the glob must not match
head -c 512 /dev/zero >"$ROOT/cells/nodes/archive/node-d.json"
# torn durable application data: must never be touched
head -c 8192 /dev/zero >"$ROOT/cells/apps/ledger/main.json"
# healthy JSON at an allowlist-adjacent path
printf '{"links":[]}' >"$ROOT/cells/fleet/topology.json"
# NULs followed by a brace: damaged, but not the torn signature
{ head -c 100 /dev/zero; printf '{'; } >"$ROOT/cells/nodes/node-e.json"

for f in nodes/archive/node-d.json apps/ledger/main.json nodes/node-e.json; do
  shasum -a 256 "$ROOT/cells/$f" | awk -v f="$f" '{print $1"  "f}'
done >"$ROOT/before.sums"

docker rm -f gateway >/dev/null 2>&1
docker network rm "$NET" >/dev/null 2>&1
docker network create "$NET" >/dev/null || exit 1
docker run -d --name gateway --network "$NET" \
  -e ROOT_ACCESS_KEY_ID=tornscan -e ROOT_SECRET_ACCESS_KEY=tornscansecret \
  -v "$ROOT:/mnt/data" \
  "$GW_IMAGE" --region us-east-1 posix /mnt/data >/dev/null || exit 1

for i in $(seq 1 30); do
  docker exec gateway true >/dev/null 2>&1 && break
  sleep 0.5
done
sleep 2

scan() {
  docker run --rm --network "$NET" \
    --user 65532:65532 --read-only --tmpfs /tmp:rw,nosuid,size=16m \
    -e HOME=/tmp \
    -e AWS_ENDPOINT_URL="$AWS_ENDPOINT_URL" \
    -e AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" \
    -e AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" \
    -e BUCKET=cells \
    -v "$SCANNER:/scan/torn-scan.sh:ro" \
    --entrypoint /scan/torn-scan.sh \
    "$SCAN_IMAGE" "$@"
}

check() { # check <label> <expected> <actual>
  if [ "$2" = "$3" ]; then
    echo "ok: $1"
  else
    echo "FAIL: $1 (expected [$2], got [$3])"
    FAILURES=$((FAILURES + 1))
  fi
}

export AWS_ENDPOINT_URL="http://gateway:7070"
export AWS_ACCESS_KEY_ID="tornscan"
export AWS_SECRET_ACCESS_KEY="tornscansecret"

echo "== 1. report mode on a store with torn objects"
out="$(scan 2>/tmp/torn-stderr)"; rc=$?
echo "$out"
check "exit code is 1 (torn found)" 1 "$rc"
for k in fleet/capacity-v1.json nodes/node-a.json nodes/archive/node-d.json apps/ledger/main.json; do
  check "reports $k" "present" "$(echo "$out" | grep -qF "$k" && echo present || echo absent)"
done
check "nothing quarantined in report mode" "0" "$(echo "$out" | grep -c QUARANTINED)"
check "no .torn- keys created" "0" "$(find "$ROOT/cells" -name '*.torn-*' | wc -l | tr -d ' ')"
check "all 8 originals still present" "8" "$(find "$ROOT/cells" -type f | wc -l | tr -d ' ')"
check "stderr is empty on a clean run" "0" "$(wc -c </tmp/torn-stderr | tr -d ' ')"

echo "== 2. prefix scope leaves durable data out of the listing"
out="$(scan --prefixes 2>/tmp/torn-stderr)"; rc=$?
echo "$out"
check "exit code is 1 (torn found)" 1 "$rc"
for k in fleet/capacity-v1.json nodes/node-a.json nodes/archive/node-d.json; do
  check "reports $k" "present" "$(echo "$out" | grep -qF "$k" && echo present || echo absent)"
done
check "apps/ledger/main.json is out of scope" "absent" "$(echo "$out" | grep -qF 'apps/ledger' && echo present || echo absent)"
check "summary names the scope" "present" "$(echo "$out" | grep -q 'prefixes fleet/ nodes/' && echo present || echo absent)"

echo "== 3. quarantine mode (the scheduled invocation: --quarantine --prefixes)"
out="$(scan --quarantine --prefixes 2>/tmp/torn-stderr)"; rc=$?
echo "$out"
check "exit code is 1 (torn found)" 1 "$rc"
check "exactly 2 quarantined" "2" "$(echo "$out" | grep -c QUARANTINED)"
check "capacity-v1.json moved" "gone" "$(test -e "$ROOT/cells/fleet/capacity-v1.json" && echo present || echo gone)"
check "node-a.json moved" "gone" "$(test -e "$ROOT/cells/nodes/node-a.json" && echo present || echo gone)"
check "quarantine copy of capacity kept" "4096" "$(stat -f %z "$ROOT"/cells/fleet/capacity-v1.json.torn-* 2>/dev/null || echo missing)"
check "quarantine copy of node-a kept" "2048" "$(stat -f %z "$ROOT"/cells/nodes/node-a.json.torn-* 2>/dev/null || echo missing)"

echo "== 4. out-of-allowlist objects are untouched"
for f in nodes/archive/node-d.json apps/ledger/main.json nodes/node-e.json; do
  before="$(grep "  $f\$" "$ROOT/before.sums" | awk '{print $1}')"
  after="$(shasum -a 256 "$ROOT/cells/$f" | awk '{print $1}')"
  check "$f untouched" "$before" "$after"
done
check "healthy node-b.json untouched" "present" "$(test -e "$ROOT/cells/nodes/node-b.json" && echo present || echo gone)"
check "empty node-c.json untouched" "present" "$(test -e "$ROOT/cells/nodes/node-c.json" && echo present || echo gone)"

echo "== 5. a re-scan skips its own quarantine copies"
out="$(scan 2>/tmp/torn-stderr)"; rc=$?
echo "$out"
check "exit code is 1 (only the out-of-allowlist fixtures remain)" 1 "$rc"
check "no .torn- key is reported" "0" "$(echo "$out" | grep -c '\\.torn-')"

echo "== 6. clean store reports exit 0 (found nothing)"
rm "$ROOT/cells/nodes/archive/node-d.json" "$ROOT/cells/apps/ledger/main.json" "$ROOT/cells/nodes/node-e.json"
out="$(scan 2>/tmp/torn-stderr)"; rc=$?
echo "$out"
check "exit code is 0" 0 "$rc"

echo "== 7. bad credentials report exit 2 (could not run)"
export AWS_SECRET_ACCESS_KEY="wrong"
out="$(scan 2>/tmp/torn-stderr)"; rc=$?
check "exit code is 2" 2 "$rc"
check "stderr names the failing step" "present" "$(grep -q 'head-bucket' /tmp/torn-stderr && echo present || echo absent)"

echo "== 8. unreachable endpoint reports exit 2"
export AWS_SECRET_ACCESS_KEY="tornscansecret"
export AWS_ENDPOINT_URL="http://gateway:9999"
out="$(scan 2>/tmp/torn-stderr)"; rc=$?
check "exit code is 2" 2 "$rc"
check "stderr is non-empty" "present" "$(test -s /tmp/torn-stderr && echo present || echo absent)"

docker rm -f gateway >/dev/null 2>&1
docker network rm "$NET" >/dev/null 2>&1
rm -rf "$(dirname "$ROOT")"

echo
if [ "$FAILURES" -eq 0 ]; then
  echo "ALL CHECKS PASSED"
else
  echo "$FAILURES CHECKS FAILED"
  exit 1
fi
