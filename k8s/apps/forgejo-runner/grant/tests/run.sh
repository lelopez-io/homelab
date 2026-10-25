#!/bin/sh
# Evaluates the grant's admission fixtures with the API server's CEL libraries
# at the cluster's version; needs go and the module cache or network. Add a case
# to the cases file of each policy rule you change. The registry host is fake.
set -e
cd "$(dirname "$0")"
go run . ../ceilings.yaml cases.yaml registry.test
go run . ../no-token.yaml cases-no-token.yaml registry.test
go run . ../policy.yaml cases-namespaces.yaml registry.test
go run . ../ceilings.yaml cases-namespace-labels.yaml registry.test
