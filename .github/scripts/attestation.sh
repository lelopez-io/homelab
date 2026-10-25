#!/usr/bin/env bash
# Verifies the GitHub attestation of each listed image digest a pull request
# adds; ALL=1 checks every pinned digest. usage: attestation.sh <base-ref>
# GitHub holds these attestations, not the registry, so admission cannot.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
base=${1:?usage: attestation.sh <base-ref>}
bad=0 checked=0

while IFS='|' read -r image repo signer; do
  case $image in ''|'#'*) continue ;; esac
  image=$(echo "$image" | xargs); repo=$(echo "$repo" | xargs); signer=$(echo "$signer" | xargs)
  pat="$image(:[^@ \"]*)?@sha256:[0-9a-f]{64}"
  if [ "${ALL:-}" = 1 ]; then
    digests=$(grep -rhoE "$pat" k8s | grep -oE 'sha256:[0-9a-f]{64}' | sort -u || true)
  else
    digests=$(git diff -U0 "$base"...HEAD -- k8s | grep -E '^\+' | grep -oE "$pat" | grep -oE 'sha256:[0-9a-f]{64}' | sort -u || true)
  fi
  for d in $digests; do
    checked=$((checked + 1))
    if gh attestation verify "oci://$image@$d" --repo "$repo" --signer-workflow "$signer" >/dev/null 2>&1; then
      echo "ok    $image@$d is attested by $signer"
    else
      echo "FAIL  $image@$d has no valid attestation from $signer"; bad=1
    fi
  done
done < "$here/attested-images.txt"

echo "$checked digest(s) checked"
exit $bad
