#!/bin/sh
# Offline check of the three pod and Service policies with kyverno-cli. Each
# fixture holds pods the rule must refuse (named bad-*) and ones it admits
# (named ok-*). The bindings' namespace lists are not evaluated here.
set -eu
cd "$(dirname "$0")/.."
check() { # policy fixture expected-fail expected-pass
  out=$(docker run --rm -v "$PWD:/w:ro" ghcr.io/kyverno/kyverno-cli:v1.15.2 \
    apply "/w/$1.yaml" --resource "/w/tests/$2.yaml" 2>&1 || true)
  want="pass: $4, fail: $3,"
  echo "$out" | grep -q "$want" || { echo "$1: want '$want'"; echo "$out"; exit 1; }
  echo "ok $1"
}
check pinned-images pods 4 2
check host-access host 7 1
check lan-services services 3 1
(cd tests/cel && GOTOOLCHAIN=local go test ./...)
