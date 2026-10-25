#!/bin/sh
# Evaluates the grant's admission fixtures with the API server's CEL
# libraries at the cluster's version. The registry host is a stand-in.
set -e
cd "$(dirname "$0")"
go run . ../ceilings.yaml cases.yaml registry.test
go run . ../no-token.yaml cases-no-token.yaml registry.test
