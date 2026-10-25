# Pinned platform images

Every image in the platform namespaces runs by digest, so a retagged upstream
image cannot reach them. Each component takes its pin where its chart or
manifest reads the image. A bump changes the version and the digests of that
one component in one commit.

## Getting a digest

Take the index digest of the tag, the one a multi-arch pull resolves to:

    crane digest quay.io/jetstack/cert-manager-controller:v1.21.3

Without crane, ask the registry for the manifest headers and read
`Docker-Content-Digest`, sending the index media types in `Accept`. A running
pod reports the same value in `status.containerStatuses[].imageID`.

## cert-manager

Pinned in `cert-manager/helmrelease.yaml` as `digest` under `image`,
`webhook.image`, `cainjector.image`, `acmesolver.image` and
`startupapicheck.image`. To bump: set the new chart version, then the digest
of each of the five images at the new tag.
