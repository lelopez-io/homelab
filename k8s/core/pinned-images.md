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

## Envoy Gateway

Pinned in `envoy-gateway/helmrelease.yaml` under `global.images`. The
`envoyGateway` image is read by the controller, the certgen job and the
shutdown manager the controller adds to each proxy pod. The `envoyProxy` and
`ratelimit` images are the ones the controller starts. To bump: set the new
chart version, then take the three images and their tags from the new chart's
`values.yaml`, each with its digest.

## Longhorn

Pinned in `longhorn/helmrelease.yaml` under `image.longhorn` and `image.csi`.
The chart joins `repository` and `tag` into the image string, so each `tag`
holds `<version>@<digest>`. The manager, instance manager, share manager,
engine and CSI sidecar images all come from these values, because the
manager starts the rest from the strings it is given. To bump: set the new
chart version, then copy the tags from the new chart's `values.yaml` with a
digest each.

Longhorn names an engine image from a hash of its string, so a new string is
a new engine image. Volumes keep running on the old one until each is
upgraded to the new one in the Longhorn UI or API. The old engine image and
its instance managers stay up until the last volume has moved.
