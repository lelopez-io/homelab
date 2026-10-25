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

## MetalLB

Pinned in `metallb/helmrelease.yaml` as `controller.image.tag` and
`speaker.image.tag`, each `<version>@<digest>`, because the chart joins
repository and tag. The speaker image also covers its init containers. To
bump: set the new chart version, then the version and digest of both images.

## Tailscale

Pinned in `tailscale/helmrelease.yaml` as `operatorConfig.image.digest` and
`proxyConfig.image.digest`. The operator reads the proxy image from the
`PROXY_IMAGE` variable the chart sets from `proxyConfig`, and starts the
ingress group and the subnet connector pods from it, so a ProxyClass needs no
image of its own. With a digest the chart drops the tag. To bump: set the new
chart version, then the digests of `tailscale/k8s-operator` and
`tailscale/tailscale` at that version.

## Flux

Pinned in `flux/config/flux-system/kustomization.yaml` under `images`, one
entry per controller, each `<tag>@<digest>`. The controllers come from
`gotk-components.yaml`, which `flux bootstrap` or `flux install --export`
generates, and an `images` entry replaces whatever tag that file carries. So a
Flux upgrade is two steps in one commit: regenerate `gotk-components.yaml`,
then set every entry to the new tag and its digest. Skipping the second step
leaves the new manifests running the old digests.

## Kubernetes components

The API server, controller manager, scheduler, kube-proxy and CoreDNS images
are set in `talos/talconfig.yaml` in one `controlPlane` patch, each
`<tag>@<digest>`. They reach the nodes by a manual apply, not a Flux tag:
`talhelper genconfig`, then `talosctl apply-config --dry-run`, then
`--mode=no-reboot` on one node at a time with a health check between nodes.

The three control plane images are static pods the OS renders from the
config, so the apply restarts them with the digest. kube-proxy and CoreDNS
are different: Talos creates their DaemonSet and Deployment once at
bootstrap and never updates them, so the patch sets the image for a new
cluster and for `talosctl upgrade-k8s`, and the running objects stay on their
tag until something else changes them.

To bump: set `kubernetesVersion`, then the digest of each of the four
Kubernetes images at that version, and the CoreDNS tag and digest. A
`talosctl upgrade-k8s` writes plain tags into the machine config, so put the
digests back afterwards.
