# Platform namespace budgets

Each platform namespace carries a LimitRange named `defaults` and a
ResourceQuota named `budget` beside its Namespace. New namespaces get
theirs from the Kyverno policies in `core/kyverno-policies`; these are the
explicit ones for the namespaces that existed before.

## How a quota is sized

The running total of the namespace's pods, read from the cluster:

- a container's request, or the LimitRange default of 10m and 32Mi when it
  sets none;
- a pod counts the larger of its containers' sum and its largest init
  container;
- plus what a rollout or a resize adds: one surge pod for every
  RollingUpdate deployment at once, since a chart bump rolls them together,
  and a quarter more for a Recreate or stateful workload, which never holds
  two pods.

Limits are capped only where every container and init container sets one,
because a quota on limits refuses a pod that sets none. Elsewhere requests
and the pod count are the cap.

## Budgets

| Namespace | Pods now | Requests now | Quota pods | Quota requests | Quota limits |
|---|---|---|---|---|---|
| celld | 3 | 300m, 768Mi | 5 | 500m, 1Gi | 4, 8Gi |
| cert-manager | 3 | 75m, 160Mi | 8 | 250m, 512Mi | 1500m, 1536Mi |
| envoy-gateway-system | 3 | 1120m, 2368Mi | 8 | 2, 4Gi | none |
| factorio | 1 | 1, 2Gi | 3 | 1500m, 3Gi | none |
| forgejo | 1 | 250m, 512Mi | 3 | 500m, 1Gi | none |
| forgejo-runner | 1 | 350m, 640Mi | 3 | 1, 1536Mi | 6, 10Gi |
| frp-tunnel | 1 | 25m, 32Mi | 3 | 100m, 128Mi | 300m, 384Mi |
| intel-gpu-resource-driver | 3 | 30m, 384Mi | 6 | 100m, 1Gi | none |
| kyverno | 4 | 300m, 448Mi | 10 | 1, 1Gi | 3, 3Gi |
| longhorn-system | 27 | 6145m, 2360Mi | 64 | 13, 4Gi | none |
| metallb-system | 4 | 50m, 256Mi | 10 | 250m, 512Mi | none |
| minecraft | 1 | 2, 4Gi | 3 | 2500m, 5Gi | 5, 8Gi |
| palworld | 1 | 2, 8Gi | 3 | 2500m, 10Gi | 5, 15Gi |
| plane | 11 | 625m, 1182Mi | 24 | 1500m, 2Gi | none |
| plex | 1 | 300m, 576Mi | 4 | 500m, 1Gi | none |
| tailscale | 6 | 60m, 384Mi | 14 | 250m, 1Gi | none |
| versitygw | 1 | 50m, 128Mi | 3 | 100m, 256Mi | 1500m, 1536Mi |

Requests are those after the container sizes below were set. Kyverno is
not running yet; its row is the chart's values.

A namespace that needs another budget changes its `budget` here. A forge
run's namespace gets the generated one, and a larger one is a change to the
`budgets` map in the Kyverno policy: a run holds no grant on quotas.

## Scenarios

- Longhorn engine upgrade. A second set of three instance managers runs
  beside the first until volumes move: 6072m + 5742m = 11814m, plus the new
  engine image (3 pods), the CSI and UI surge (6) and a few share managers
  and job pods (9) at 10m each, about 12 CPU of 13. Pods 27 + 3 + 3 + 6 + 9
  = 48 of 64, memory about 2Gi of 4Gi. The instance manager request is 12%
  of a node's CPU, set by Longhorn's guaranteed-instance-manager-cpu; raise
  the quota with that setting.
- Envoy proxy rollout. Two proxies roll with one surge pod (510m, 1056Mi)
  and the controller one more (100m, 256Mi): 1730m of 2 CPU, 3680Mi of 4Gi,
  5 pods of 8.
- Game server restart. The deployments are Recreate, so the old pod is gone
  before the new one exists and the peak is one pod: Minecraft 2 CPU and
  4Gi of 2500m and 5Gi, Palworld 2 CPU and 8Gi of 2500m and 10Gi, Factorio
  1 CPU and 2Gi of 1500m and 3Gi.

## Container sizes

The cluster has no metrics server, so there is no busy-hour measurement.
The source is the kubelet's summary, read over about 40 idle hours: a
working set and an average CPU for each container. Requests follow the
working set. Memory limits are 2 to 4 times it where the idle size is
stable and a kill is local; every one is an estimate of a busy hour. The
busy components, marked none yet, get theirs in a later change sized from a
week of measurement, since a wrong limit would restart a database or the
volume engine in the busy hour. No container here has a CPU limit, because
throttling a storage engine, database, transcode or edge proxy turns a
burst into latency.

| Component | Working set seen | Request | Memory limit | An OOM kill |
|---|---|---|---|---|
| Longhorn manager | 142 to 719Mi | 50m, 256Mi | none yet | restarts; attach and detach wait, I/O continues |
| Longhorn CSI sidecars | 11 to 59Mi | 10m, 64Mi | 256Mi | provisioning or attach waits |
| Longhorn CSI plugin | 18 to 66Mi | 10m, 96Mi | none yet | node attach waits |
| Longhorn registrar, probe | 22 to 28Mi | 5m, 32Mi | 128Mi | plugin registration waits |
| Longhorn UI, driver deployer | 10 to 13Mi | 10m, 32Mi | none yet | UI or deploy waits |
| MetalLB speaker | 77Mi | 10m, 64Mi | 256Mi | its node stops answering for addresses |
| MetalLB controller | 31Mi | 10m, 32Mi | 128Mi | no new address assigned |
| Tailscale operator | 28Mi | 10m, 64Mi | 256Mi | no new proxies |
| Tailscale proxy | 22 to 37Mi | 10m, 64Mi | 256Mi | sessions drop until it rejoins |
| GPU plugin | 149Mi | 10m, 128Mi | 512Mi | device claims wait |
| Gateway drain sidecar | 90Mi | 10m, 32Mi | 256Mi | a drain is cut short |
| Plex media server | 173Mi idle | 250m, 512Mi | none yet | every stream and transcode ends |
| Plane postgres | 43Mi | 100m, 256Mi | none yet | connections drop, log replays |
| Plane object store | 85Mi | 50m, 256Mi | none yet | uploads fail |
| Plane rabbitmq | 154Mi | 100m, 256Mi | none yet | in-flight work drops |
| Plane valkey | 4Mi | 25m, 64Mi | 256Mi | in-memory cache lost |
| Plane migrator | not measured | 100m, 256Mi | none yet | the job retries |

Not met: Longhorn's instance managers, engine images, share managers and
recurring job pods are made by its manager, which offers a CPU reservation
for the first (12% of a node, already set) and no memory request or limit.
They have neither. Setting one by mutating those pods was decided against:
Longhorn may reconcile the pod spec back, and a memory limit on an
instance manager risks killing the process that holds every volume's
engine. The next step is a measured limit once there is a usage source:
read an instance manager's working set through a rebuild, a snapshot and an
engine upgrade, then set a limit well above its peak.

The init containers of Factorio (2) and Forgejo (3) are sized from their
scripts and are estimates: 10m and 32Mi requested with 128Mi and 256Mi
limits for Factorio's, 100m and 128Mi with 512Mi for Forgejo's.

## Without a quota

kube-router is the cluster's network. A pod refused there leaves a node
with no pod network, and restoring it needs that network, so the namespace
carries no quota. Its three pods are a fixed daemonset. Its container has a
256Mi memory limit and no CPU limit: throttling it stalls pod networking.
