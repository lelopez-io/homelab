#!/usr/bin/env bash
# torn-scan: find objects the gateway tore in an ungraceful shutdown and,
# with --quarantine, move the ones the runtime can rebuild out of the way.
#
# The gateway links an object into place at full length before its pages
# are durable, so a crash leaves a non-empty object whose bytes are all
# NUL. Every record in this bucket is JSON and opens with a brace, so "no
# byte outside the whitespace set" cannot match a healthy object, and a
# legitimately empty object has zero length and is skipped.
#
# Quarantine renames the object, so it must only ever target state the
# runtime rebuilds on demand: fleet/capacity-v1.json and nodes/*.json.
# Anything else matching the signature is reported and left in place.
#
# --prefixes limits the listing to fleet/ and nodes/, the prefixes whose
# tears stop routing. Scope is a cost knob, not a safety boundary: the
# quarantine allowlist is the same either way.
#
# Exit codes, because "found nothing" and "could not run" must differ:
#   0  scan completed, nothing torn
#   1  scan completed, torn objects found (quarantined if allowlisted)
#   2  scan did not complete; stderr names the step that failed

set -u

fail() { echo "torn-scan: $*" >&2; exit 2; }

QUARANTINE=0
PREFIXES=0
for arg in "$@"; do
  case "$arg" in
    --quarantine) QUARANTINE=1 ;;
    --prefixes) PREFIXES=1 ;;
    *) fail "unknown argument '$arg'; usage: torn-scan.sh [--quarantine] [--prefixes]" ;;
  esac
done

[ -n "${BUCKET:-}" ] || fail "BUCKET is not set; name the bucket to scan"
[ -n "${AWS_ENDPOINT_URL:-}" ] || fail "AWS_ENDPOINT_URL is not set; name the gateway"
[ -n "${AWS_ACCESS_KEY_ID:-}" ] || fail "AWS_ACCESS_KEY_ID is not set"
[ -n "${AWS_SECRET_ACCESS_KEY:-}" ] || fail "AWS_SECRET_ACCESS_KEY is not set"
command -v aws >/dev/null 2>&1 || fail "aws CLI is not on PATH"

export AWS_REGION="${AWS_REGION:-us-east-1}"
export AWS_PAGER=""

WORK="$(mktemp -d)" || fail "cannot create a work directory"
trap 'rm -rf "$WORK"' EXIT

# The gateway answers one host:port, so the bucket has to stay in the URL
# path. The default virtual-host style prepends it to the hostname and the
# name stops resolving.
export AWS_CONFIG_FILE="$WORK/aws-config"
printf '[default]\ns3 =\n  addressing_style = path\n' >"$AWS_CONFIG_FILE"

aws_s3() { aws --endpoint-url "$AWS_ENDPOINT_URL" s3api "$@" </dev/null; }

ERR="$WORK/stderr"

aws_s3 head-bucket --bucket "$BUCKET" >/dev/null 2>"$ERR" ||
  fail "head-bucket $BUCKET failed (endpoint, credentials, network): $(cat "$ERR")"

# The scheduled job runs with --prefixes so its cost stays bounded as the
# durable data in the same bucket grows: those two prefixes are the ones
# whose tears stop routing. The default mode still covers the whole bucket.
if [ "$PREFIXES" -eq 1 ]; then
  : >"$WORK/objects"
  for prefix in fleet/ nodes/; do
    aws_s3 list-objects-v2 --bucket "$BUCKET" --prefix "$prefix" \
      --query 'Contents[].[Key,Size]' --output text >>"$WORK/objects" 2>"$ERR" ||
      fail "list-objects-v2 $BUCKET ($prefix) failed: $(cat "$ERR")"
  done
else
  aws_s3 list-objects-v2 --bucket "$BUCKET" \
    --query 'Contents[].[Key,Size]' --output text >"$WORK/objects" 2>"$ERR" ||
    fail "list-objects-v2 $BUCKET failed: $(cat "$ERR")"
fi

allowlisted() {
  case "$1" in
    fleet/capacity-v1.json) return 0 ;;
    nodes/*.json)
      # The glob covers one path segment; nodes/a/b.json is out of scope.
      case "${1#nodes/}" in
        */*) return 1 ;;
        *) return 0 ;;
      esac
      ;;
    *) return 1 ;;
  esac
}

quarantine() {
  key="$1"
  dest="${key}.torn-$(date -u +%Y%m%dT%H%M%SZ)"
  if aws_s3 head-object --bucket "$BUCKET" --key "$dest" >/dev/null 2>&1; then
    echo "torn-scan: $dest already exists; not overwriting evidence" >&2
    return 1
  fi
  if ! aws_s3 copy-object --bucket "$BUCKET" --key "$dest" \
    --copy-source "$BUCKET/$key" >/dev/null 2>"$ERR"; then
    echo "torn-scan: copy $key -> $dest failed: $(cat "$ERR")" >&2
    return 1
  fi
  if ! aws_s3 delete-object --bucket "$BUCKET" --key "$key" 2>"$ERR"; then
    echo "torn-scan: $key is copied to $dest but the original could not be removed: $(cat "$ERR")" >&2
    echo "torn-scan: remove $key by hand; the runtime then rebuilds it" >&2
    return 1
  fi
  echo "QUARANTINED: $key -> $dest"
  return 0
}

checked=0
torn=0
quarantined=0
incomplete=0

while IFS="$(printf '\t')" read -r key size; do
  case "$key" in
    "" | None) continue ;;
    # Our own quarantine copies are torn by construction. Reporting them on
    # every later run would bury the next real finding, so they are skipped.
    *.torn-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z) continue ;;
  esac
  case "$size" in
    '' | *[!0-9]*)
      echo "torn-scan: cannot parse size for key '$key'; the listing is not trustworthy" >&2
      incomplete=1
      continue
      ;;
  esac
  # Zero length is a legitimately empty object; torn means non-zero.
  [ "$size" -eq 0 ] && continue
  checked=$((checked + 1))

  if ! aws_s3 get-object --bucket "$BUCKET" --key "$key" "$WORK/object" >/dev/null 2>"$ERR"; then
    if aws_s3 head-object --bucket "$BUCKET" --key "$key" >/dev/null 2>&1; then
      echo "torn-scan: could not read $key: $(cat "$ERR")" >&2
      incomplete=1
    else
      echo "torn-scan: $key vanished between listing and read; skipped"
    fi
    continue
  fi

  [ -z "$(tr -d '\0 \t\n\r' <"$WORK/object" | head -c 1)" ] || continue
  torn=$((torn + 1))

  if allowlisted "$key"; then
    if [ "$QUARANTINE" -eq 1 ]; then
      if quarantine "$key"; then
        quarantined=$((quarantined + 1))
      else
        incomplete=1
      fi
    else
      echo "TORN (allowlisted, report-only so left in place): $key ($size bytes)"
    fi
  else
    echo "TORN (outside the allowlist, left in place): $key ($size bytes)"
  fi
done <"$WORK/objects"

if [ "$PREFIXES" -eq 1 ]; then
  scope="prefixes fleet/ nodes/"
else
  scope="full bucket"
fi
echo "torn-scan: $checked non-empty objects in $BUCKET ($scope): $torn torn, $quarantined quarantined"

if [ "$incomplete" -ne 0 ]; then
  echo "torn-scan: INCOMPLETE: at least one step failed; treat the results as partial" >&2
  exit 2
fi
[ "$torn" -eq 0 ] && exit 0
exit 1
