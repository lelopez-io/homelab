#!/bin/sh
# Renders the tree and runs the pinned-images policy over every pod-bearing
# object. HelmRelease pods go through helm template. Needs kubectl, helm, yq,
# jq and docker.
set -eu
root=$(cd "$(dirname "$0")/../../../.." && pwd)
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT
r="$out/render.yaml"

kubectl kustomize "$root/k8s" > "$r"
kubectl kustomize "$root/flux/config/flux-system" >> "$r"

for f in "$root"/k8s/core/*/helmrelease.yaml; do
  yq -o=json -I0 'select(.kind == "HelmRelease")' "$f" | while read -r hr; do
    chart=$(echo "$hr" | jq -r .spec.chart.spec.chart)
    repo=$(echo "$hr" | jq -r .spec.chart.spec.sourceRef.name)
    url=$(yq -r 'select(.kind == "HelmRepository" and .metadata.name == "'"$repo"'") | .spec.url' \
      "$root"/k8s/core/*/helmrepository.yaml "$f" | grep -v '^$' | head -1)
    echo "$hr" | jq '.spec.values // {}' > "$out/values.json"
    # The operator's credentials come from a Secret through valuesFrom.
    set --
    [ "$chart" = tailscale-operator ] && set -- --set oauth.clientId=x --set oauth.clientSecret=x
    case $url in
      oci://*) src="$url/$chart" ;;
      *) src="$chart --repo $url" ;;
    esac
    # shellcheck disable=SC2086
    helm template "$(echo "$hr" | jq -r .metadata.name)" $src \
      --version "$(echo "$hr" | jq -r .spec.chart.spec.version)" \
      -n "$(echo "$hr" | jq -r .metadata.namespace)" -f "$out/values.json" "$@" >> "$r"
    echo --- >> "$r"
  done
done

# The sops Secrets in the render are not parseable resources and the policy
# matches only these kinds.
yq 'select(.kind == "Pod" or .kind == "Deployment" or .kind == "StatefulSet" or .kind == "DaemonSet" or .kind == "ReplicaSet" or .kind == "Job" or .kind == "CronJob")' "$r" > "$out/pods.yaml"
docker run --rm -v "$root/k8s/core/admission/pinned-images.yaml:/p.yaml:ro" \
  -v "$out/pods.yaml:/r.yaml:ro" ghcr.io/kyverno/kyverno-cli:v1.15.2 \
  apply /p.yaml --resource /r.yaml | tee "$out/result.txt"
grep -q 'fail: 0,' "$out/result.txt"
