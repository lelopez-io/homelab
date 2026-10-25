# Image verification

A digest pin proves an image has not changed since it was chosen. It does
not prove its publisher built it. Kyverno `verifyImages` checks that, at pod
admission, against the publisher's signing identity.

## Engine

Kyverno, because it is installed and the Sigstore policy controller would be
a second webhook on every pod create. Kyverno 1.13 and later verifies cosign
signatures, keyless identities and Sigstore bundles, which is the format of a
GitHub build attestation. Only the pod rules live here. Other validate rules
go in ValidatingAdmissionPolicy, which has no webhook to be down.

## Failure behaviour

- The pod webhook fails closed (`Fail`, 30s). `Ignore` would lift every rule
  silently while Kyverno is down.
- `kube-system`, `kyverno` and `flux-system` are out of the webhook's scope
  by a CEL match condition, so an outage cannot block the pods that restart
  Kyverno or the cluster's DNS and Flux. The chart already exempts
  `kube-system` and `kyverno` through its webhook namespaceSelector, and
  its resource filters skip the same two.
- A pod that is running stays running when Kyverno is down. Only a new pod
  needs a verification.
- Kyverno caches verification results in memory (default on, 60 minutes).
  A registry outage shorter than that does not block a restart of an image
  that was verified inside the window. After a Kyverno restart the cache is
  empty.
- Autogen is off, so the rules read pods only. Rewriting a controller's pod
  template would fight Flux.

## Egress

The admission controller reaches registries, Fulcio, Rekor and the TUF root
on 443 to the internet, private ranges excluded. The background controller
verifies nothing and gets no egress.

The forge registry is reached by its public hostname, which resolves to the
gateway. The policy names the gateway's proxy pods and container port 10443,
and a second policy in the gateway's namespace admits the admission
controller there. The hostname comes from Flux substitution.

Verification needs no extra trust for the forge: its certificate comes from
Let's Encrypt, whose roots the Kyverno image carries.

## Mode per rule

Every rule ships in Audit. The offline run below proves the identities
against the live registries, not the admission path, so no rule is proven
to pass every running image. Audit forbids `mutateDigest`, and the images
run by tag, so `verifyDigest` is off and the tag is resolved when the pod is
verified.

| Rule | Images | Identity | Mode |
|---|---|---|---|
| metallb | `quay.io/metallb/*` | publish workflow, release tag | Audit |
| kube-router | `docker.io/cloudnativelabs/kube-router*` | CI container workflow, release tag | Audit |
| silo | `docker.io/pgsty/silo*` | docker-release workflow, release tag | Audit |
| forge-built | forge registry images | cosign key `forge-build-key` | Audit |

To enforce one of the first three, change its `failureAction` to `Enforce`
and its `mutateDigest` to `true` in the same rule. Enforce needs the
digest pin, or the pod would pull a tag that moved after verification.

## Reading audit results

A failed audit rule lands in a policy report, which the reports controller
writes for admission requests, controller-made pods included.

    kubectl get policyreport -A
    kubectl -n kyverno logs -l app.kubernetes.io/component=admission-controller

The log is the fallback while the reports controller is off.

## Offline test

`tests/pods.yaml` holds a signed pod for each rule, a nonexistent tag and a
pod no rule matches. The CLI reaches the registries, so it checks real
signatures. It crashes on `webhookConfiguration` and does not evaluate
match conditions, so the policy is applied without that block. It cannot
run the forge rule, which needs the key Secret and the hostname.

    sed '/webhookConfiguration:/,/^  rules:/{/^  rules:/!d;}' publishers.yaml > /tmp/p.yaml
    docker run --rm -v /tmp:/t -v "$PWD/tests:/f" \
      reg.kyverno.io/kyverno/kyverno-cli:v1.19.2 \
      apply /t/p.yaml --resource /f/pods.yaml

Expect the signed pods to pass and the missing tag to fail. Replacing a
workflow name in a rule makes the signed pods fail with a subject mismatch.

## Forge-built images

The forge's token issuer is not one public Fulcio accepts, so a keyless
identity is not available. The build must push a cosign signature as the
`<digest>.sig` tag next to each image, made with a key whose public half is
the `forge-build-key` Secret in the `kyverno` namespace. Admission also
needs a read credential for the forge registry, which is the owner's
read-only account and is not created here. Until the build signs and both
exist, every forge image fails its audit.

## Inventory

Looked up read-only on the registries, against the images running now.
"Sig" is a cosign `.sig` tag, "bundle" a Sigstore bundle referrer.

| Publisher | Images | Signature | Trusted by |
|---|---|---|---|
| metallb | controller, speaker | keyless sig | rule above |
| kube-router | kube-router | keyless bundle | rule above |
| pgsty | silo | keyless bundle | rule above |
| fluxcd | the four controllers | keyless sig | flux-system is outside the webhook; checked by lookup |
| registry.k8s.io | coredns, kube-apiserver | keyless sig (Kubernetes release identity) | kube-system is outside the webhook; Talos pins them |
| jetstack | cert-manager | cosign key, sha512, no transparency log | not verified: the key is published by one origin, see below |
| denoland | celld | GitHub attestation in GitHub's store only, nothing in the registry | digest pin; admission cannot fetch it |
| squat | generic-device-plugin | none: the index holds unsigned BuildKit provenance, GitHub reports no attestation | digest pin |
| intel | GPU resource driver | none | digest pin |
| versity | versitygw | none | digest pin |
| forgejo | forgejo, runner | none | digest pin |
| makeplane | the five plane images | none | digest pin |
| envoyproxy | envoy proxy | none | digest pin |
| docker library | postgres, rabbitmq, busybox, docker | none | digest pin |
| valkey, fatedier, factoriotools, itzg, thijsvanloef, plexinc, copyparty | one image each | none (palworld carries unsigned BuildKit provenance) | digest pin |
| envoyproxy gateway, longhornio, tailscale, siderolabs flannel | controllers and drivers | none | chart-version pin, no digest |

Images trusted by chart-version pin alone have no content pin either.

The cert-manager project publishes its image-signing key on its website and
in the website's repository, in a commit GitHub verifies. Both are one
origin, the images are signed without a transparency log entry, and no
second source (a release asset, the main repository) carries the key. A key
with one origin is not pinned here.
