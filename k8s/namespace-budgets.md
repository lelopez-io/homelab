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
| intel-gpu-resource-driver | 3 | 30m, 96Mi | 6 | 100m, 256Mi | none |
| kyverno | 4 | 300m, 448Mi | 10 | 1, 1Gi | 3, 3Gi |
| longhorn-system | 27 | 6072m, 1152Mi | 64 | 13, 4Gi | none |
| metallb-system | 4 | 40m, 128Mi | 10 | 250m, 512Mi | none |
| minecraft | 1 | 2, 4Gi | 3 | 2500m, 5Gi | 5, 8Gi |
| palworld | 1 | 2, 8Gi | 3 | 2500m, 10Gi | 5, 15Gi |
| plane | 11 | 550m, 862Mi | 24 | 1, 1536Mi | none |
| plex | 1 | 60m, 96Mi | 4 | 250m, 512Mi | none |
| tailscale | 6 | 60m, 192Mi | 14 | 250m, 512Mi | none |
| versitygw | 1 | 50m, 128Mi | 3 | 100m, 256Mi | 1500m, 1536Mi |

Kyverno is not running yet; its row is the chart's values.

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

## Without a quota

kube-router is the cluster's network. A pod refused there leaves a node
with no pod network, and restoring it needs that network, so the namespace
carries no quota. Its three pods are a fixed daemonset.
