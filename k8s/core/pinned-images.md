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
of each image at the new tag.

## Envoy Gateway

Pinned in `envoy-gateway/helmrelease.yaml` under `global.images`. The
`envoyGateway` image is read by the controller, the certgen job and the
shutdown manager the controller adds to each proxy pod. The `envoyProxy` and
`ratelimit` images are the ones the controller starts. To bump: set the new
chart version, then take the images and their tags from the new chart's
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
a new engine image. `defaultSettings.concurrentAutomaticEngineUpgradePerNodeLimit`
is 1, so after the manager restarts it upgrades volumes to the new default
engine image itself, one per node at a time. An attached, healthy volume
upgrades live; a detached one upgrades at once; strict-local, DR, expanding
and migrating volumes are skipped and need a manual upgrade. The chart's
default settings reach an existing setting whenever the manager starts with a
changed ConfigMap.

The old engine image is not deleted for you. When no volume uses it, delete it
in the Longhorn UI so its DaemonSet goes; its instance managers go once no
instance runs in them. Check that every volume runs the pinned image:

    kubectl -n longhorn-system get volumes.longhorn.io \
      -o custom-columns=VOLUME:.metadata.name,SPEC:.spec.image,CURRENT:.status.currentImage

Each SPEC and CURRENT must end in `@sha256:<digest>`.

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
`tailscale/tailscale` at that version, and of `tailscale/k8s-proxy` in
`operatorConfig.extraEnv`. The chart has no value for that last one, so it
rides `K8S_PROXY_IMAGE`.

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

The control plane images are static pods the OS renders from the
config, so the apply restarts them with the digest. For kube-proxy and
CoreDNS the images in the patch serve a new cluster and `talosctl
upgrade-k8s`, while Flux holds the running objects; see the kube-proxy and
CoreDNS sections.

To bump: set `kubernetesVersion`, then the digest of each Kubernetes
image at that version, and the CoreDNS tag and digest. A
`talosctl upgrade-k8s` writes plain tags into the machine config, so put the
digests back afterwards.

## Flannel

The repo owns the objects in `flannel/`, which Flux applies with pruning
off, so deleting the directory never deletes the CNI. The comment in
`flannel/daemonset.yaml` says why. The DaemonSet replaces one node at a time
(`maxUnavailable: 1`, `maxSurge: 0`), so after a bump check each node before
the next moves:

    kubectl -n kube-system rollout status ds/kube-flannel
    kubectl -n kube-system get pods -l k8s-app=flannel -o wide
    kubectl get nodes

Each pod must be Ready on the digest before the next is replaced, and a pod
on one node must still reach a pod on another.

While a node's pod is down its traffic keeps flowing: the vxlan device,
routes and masquerade rules stay in the kernel, and the CNI plugin reads the
subnet file in `/run/flannel`, so new pods on that node still start.

`talosctl upgrade-k8s` syncs every bootstrap manifest by server-side apply
with the OS field manager, from the images in the machine config, and prunes
objects the OS no longer generates. For kube-proxy and CoreDNS those images
are the digests in `talos/talconfig.yaml`, so the write matches the manifests
here as long as both are bumped together. Flannel has no config field, so the
sync writes the OS release's default tag over the DaemonSet. Run it with
`--dry-run` first, which prints the diff, and bump `flannel/daemonset.yaml` to
that release's tag and digest beforehand. Outside an upgrade the OS creates
these objects only when they are missing, so it never contends with Flux.

To bump, set the new tag and its digest on both containers of
`flannel/daemonset.yaml`. The digest rule refuses a tagged write in
`kube-system`, so an upgrade that writes the Talos default tag fails; bump the
manifest first.

## CoreDNS

`coredns/` holds the service account, cluster role and binding, ConfigMap,
Service and Deployment, which Flux applies with pruning off. The Deployment
replaces its replicas one at a time. Check with `kubectl -n kube-system
rollout status deploy/coredns` and resolve a service name from a pod after
each replica.

The same image is in `talos/talconfig.yaml` (`cluster.coreDNS.image`), which a
Kubernetes upgrade reads, so set both digests on every bump or the upgrade
writes one over the other.

## kube-proxy

`kube-proxy/` holds the service account, cluster role binding and DaemonSet,
which Flux applies with pruning off. It replaces one node at a time. Service
rules are in the kernel and outlast the pod, so Service traffic on a node
keeps flowing while its pod restarts; the new pod re-syncs the rules at
start. Check with `kubectl -n kube-system rollout status ds/kube-proxy` and
reach a Service from a pod on that node before the next node moves.

The image is also in `talos/talconfig.yaml` (`cluster.proxy.image`); set both
digests on every bump.

## Checking

`k8s/core/admission/tests/rendered.sh` renders `k8s`, `flux/config/flux-system`
and every HelmRelease with its own values, then runs the digest policy over
the result. It reads no namespace list, so it fails on any unpinned image in
the tree. Run it after every bump. It does not see pods that controllers
create at run time or the static pods; those take their images from the
settings above.
