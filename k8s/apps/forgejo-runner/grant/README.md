# Forge run grant

What a forge run may do in the cluster, as three files.

- `rbac.yaml` grants the kinds a run may write.
- `policy.yaml` limits it to namespaces it owns, and bounds its routes and
  services.
- `ceilings.yaml` bounds what it may put in them and which labels its
  namespace may carry. `no-token.yaml` keeps service account tokens out of
  its pods.

All are ValidatingAdmissionPolicies keyed on the caller `forge:repo:<owner>/<repo>`.
They run inside the API server, so there is no webhook to be down.

## Ceilings

- Images: `<registry>/<owner>/<name>@sha256:<64 hex>`, owner being the
  caller's own. The registry host comes from `forgejo-vars`.
- Limits: every container sets cpu and memory, at most 1 cpu and 1Gi. These
  match the namespace LimitRange maximum.
- NetworkPolicy egress: pods of its own namespace, DNS in kube-system on 53,
  the bucket store on 7070, or an IPv4 ipBlock that touches none of
  `privateRanges` or excepts all of each it contains. `egressPorts` and
  `egressApps` are the platform services and can shrink.
- NetworkPolicy ingress: pods of its own namespace and the gateway's proxies.
- A rule with no peers allows everything and is refused.

## Namespace labels

A run's namespace must carry `forge/repository` with its own repository and
enforce `restricted`, and `policy.yaml` keeps it from changing either. Beyond
that a run may set:

- `forge/egress: none`, which withholds the generated internet allow. It
  only narrows, and `none` is the only value accepted.
- `pod-security.kubernetes.io/audit` and `warn`, and any label outside
  `gateway.lan/` and the pod security `-version` labels. A version label
  other than `latest` pins an older, looser `restricted`, so it is refused.

The only selectors in the cluster that read a namespace's labels are the
bucket store's ingress and the gateway's dev listener, on `forge/repository`,
and the gateway's other listeners, on `gateway.lan/`. No label a run may set
widens either.

## Service account tokens

The platform sets `automountServiceAccountToken: false` on pods in
namespaces carrying `forge/repository`. `no-token.yaml` judges the pod after
that, and refuses unset as well as true, so an outage of the mutation does
not lift it. It warns and audits until the mutation is live and the running
pods have rolled; `validationActions: ["Deny"]` enforces it.

## Consistent with the generated baseline

The five ranges in `privateRanges`, the bucket store scoping in
`egressPorts` and `egressApps`, and the token rule match the baseline the
namespace defaults generate for a forge namespace once that baseline excepts
the same five ranges: 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16,
100.64.0.0/10 and 169.254.0.0/16. A baseline that excepts fewer would allow
more than a run may write.

## What the rules leave alone

- Replica counts. The namespace quota caps pods, so no rule here does.
- Objects already in the cluster. A rule is judged on create and update,
  so an object admitted before it stays until its next write.
- Deleting a NetworkPolicy. The generator restores the baseline.

## Tests

`tests/run.sh` judges each fixture with the API server's own CEL libraries,
at the cluster's Kubernetes version. It needs go and the module cache or
network access. Add a case to `tests/cases.yaml` for each rule you change.
