#!/usr/bin/env bash
# Runs the gate over each fixture tree: a pass- tree exits 0, a fail- tree
# exits non-zero and says what its expect file names. The shared policies
# are stand-ins; the platform's own rules are tested where they live.
set -uo pipefail

here=$(cd "$(dirname "$0")" && pwd)
bad=0

for dir in "$here"/fixtures/cases/*/; do
  name=$(basename "$dir")
  tree=$(mktemp -d)
  cp -R "$dir/." "$tree/"
  cp -R "$here/fixtures/policies" "$tree/k8s/policies"
  out=$(python3 -I "$here/policy-gate.py" "$tree" 2>&1); code=$?
  case $name in
    pass-*) [ $code -eq 0 ] && ok=1 || ok=0 ;;
    fail-*) [ $code -ne 0 ] && grep -qF "$(cat "$dir/expect")" <<<"$out" && ok=1 || ok=0 ;;
  esac
  if [ "$ok" = 1 ]; then echo "ok    $name"; else echo "BAD   $name (exit $code)"; echo "$out" | sed 's/^/      /'; bad=1; fi
  rm -rf "$tree"
done
exit $bad
