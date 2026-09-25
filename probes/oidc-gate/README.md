# OIDC gate probe

A throwaway repository that proves a Forgejo Actions run can authenticate
to the Kubernetes API server with the short-lived OIDC token the forge
issues to the run. It proves authentication only: nothing is granted to
the forge identity, and this probe grants nothing.

Read the verdict line of a run before anything else:

- `GATE PASSED (200)` or `GATE PASSED (403)`: authentication works. Both
  are passes; the outcomes section explains the difference.
- `GATE FAILED (401)`: authentication failed. That is the only API
  server answer that means authentication itself did not happen.

The 403 is the one that gets misread. It means the API server accepted the
token, knew exactly who the caller was, and refused the action because no
RoleBinding exists. That refusal is the property this gate measures. A 401
is the opposite: the token was rejected and no identity was attributed.

Inside the homelab repository this directory is inert: the forge scans
`.forgejo/workflows` only at a repository root. Copied into a repository
of its own and pushed, the workflow runs.

## How the probe works

The workflow asks the runner for an OIDC token (`permissions: id-token:
write`), fetches one for the audience `<forge root>/<owner>`, and POSTs a
SelfSubjectReview to the API server with the token as the bearer.

SelfSubjectReview is the cheapest call that reveals identity. It persists
nothing, and its answer is the username the API server attributed to the
caller. On Kubernetes 1.28 and later, every authenticated caller may
create one through the default `system:basic-user` ClusterRole.

Where RBAC refuses even that, the refusal message still names the caller,
so the probe reads the username either way. The expected username is:

    forge:repo:<owner>/<repo>:ref:refs/heads/main

The workflow computes it from runner environment variables and fails the
run unless a 200 answers with exactly that name, or a 403 names it.

## Inputs

The files here name no host or address. Supply these when you create the
throwaway repository:

| Input          | Kind                          | Value                                                |
| -------------- | ----------------------------- | ---------------------------------------------------- |
| KUBE_APISERVER | repository secret             | `https://<control plane address>:6443`, no path      |
| KUBE_CA_B64    | repository secret             | base64 of the cluster CA certificate, see below      |
| OIDC_AUDIENCE  | repository variable, optional | set only if the API server client ID is not `<forge root>/<owner>` |

KUBE_APISERVER must be an address the runner can dial. The runner's
egress policy names the control plane addresses it may reach on 6443;
see `k8s/apps/forgejo-runner/networkpolicy.yaml` in the homelab
repository. If the `server:` in your local kubeconfig is one of them,
reuse it:

    kubectl --kubeconfig talos/clusterconfig/kubeconfig \
      config view -o jsonpath='{.clusters[0].cluster.server}'

KUBE_CA_B64 is the cluster CA certificate. It is not secret: it verifies
the API server and cannot impersonate it. Take it from the local
kubeconfig and paste the value verbatim, since it is already base64:

    kubectl --kubeconfig talos/clusterconfig/kubeconfig \
      config view --raw \
      -o jsonpath='{.clusters[0].cluster.certificate-authority-data}'

The value rides the secret store anyway, so both inputs live in one
place and neither can land in a job log. The workflow decodes it into a
0600 file in a temporary directory that is removed when the step ends.

## Procedure

1. Choose a repository name; it becomes part of the expected identity.
   This runbook uses `oidc-gate-probe`.

2. Copy this directory's contents (`README.md` and `.forgejo/`) into an
   empty directory and make it a repository:

       git init -b main
       git add README.md .forgejo
       git commit -m "probe the forge OIDC trust"

3. Push it to the forge. This forge creates the repository on first
   push:

       git remote add origin <forge root>/<owner>/oidc-gate-probe.git
       git push -u origin main

   `<forge root>` is the URL you browse to reach the forge. No part of
   it belongs in the committed files.

4. The push triggers a run, and that first run fails with `missing
   input: KUBE_APISERVER`. That is expected; the secrets do not exist
   yet. If no run appears at all, enable the Actions unit in the
   repository settings and push an empty commit.

5. In the new repository, open Settings, Actions, Secrets, and add
   KUBE_APISERVER and KUBE_CA_B64 with the values above.

6. Re-run the failed job from the Actions tab, or push an empty commit.

7. Read the verdict line and map it with the outcomes below.

## Outcomes

`GATE PASSED (200)`: the token was accepted and the API server
attributed exactly the expected username. On Kubernetes 1.28 and later
this is the likely answer, because the default bootstrap policy lets
any authenticated caller create a SelfSubjectReview. It grants nothing
beyond that.

`GATE PASSED (403)`: the token was accepted and RBAC refused the review
itself, naming the expected user in the refusal. With nothing granted,
that refusal is the proof.

`GATE FAILED (401)`: the API server rejected the token and no identity
was attributed. Check, in order:

- Issuer match. The API server compares the token's `iss` to its
  configured issuer URL character for character. The configuration is
  in `talos/talconfig.yaml` with the values in `talos/talenv.sops.yaml`.
  The issuer the forge actually serves is the `issuer` field of
  `<forge root>/api/actions/.well-known/openid-configuration`.

- API server reachability to the forge. Each control plane node
  fetches the discovery document and the signing keys from the forge
  over HTTPS, and validation fails if they cannot. The API server log
  on a control plane node says why (`talosctl logs kube-apiserver`).

- Audience. The token's `aud` must equal the configured client ID,
  `<forge root>/<owner>`. The workflow derives that from the runner's
  environment; if the derived forge root is wrong, set OIDC_AUDIENCE
  to the correct value.

- Clock skew between the forge and the control plane nodes.

`GATE FAILED: the forge did not issue an OIDC token`: the failure is
before any cluster contact. The printed curl exit narrows it:

- `missing input: ACTIONS_ID_TOKEN_REQUEST_URL` printed instead: the
  runner ignored `id-token: write`. Only a runner with OIDC support
  injects these variables.

- Exit 22 with a 4xx or 5xx: the forge refused the token request.
  Read the forge's log.

- Exit 60: the job does not trust the forge's TLS certificate. Not
  expected here; the forge serves a publicly trusted wildcard
  certificate.

- Exit 6 or 28: the job could not resolve or reach the forge at all.
  The token URL is the forge's external address, while the runner's
  control channel rides the in-cluster service, so polling can work
  while this cannot. That points at hairpin NAT or DNS for the job
  container.

`GATE FAILED` on 404: the cluster serves no `authentication.k8s.io/v1`
SelfSubjectReview, so it predates Kubernetes 1.28. In the workflow,
change `v1` to `v1beta1` in both the path and the request body and
re-run. Before 1.26 there is no SelfSubjectReview at all; the
equivalent probe is any request the caller may not make, since the 403
message names the caller.

`GATE FAILED: could not reach the API server`: exit 60 means the CA did
not verify, so re-check KUBE_CA_B64. Otherwise the runner could not
dial the address, so re-check KUBE_APISERVER against the egress policy.

A run that stays queued means no runner matches the label. The
announced labels are in `k8s/apps/forgejo-runner/configmap.yaml`;
adjust `runs-on` to one of them.

## Token handling

The OIDC token lives only in a shell variable and reaches curl through
a pipe-backed config, so it appears in no process list, no file, and
no log line. Failure paths print status codes and the API server's
Status message, neither of which contains the token, and redact the
URLs they echo. If a run ever prints the token, treat that as a bug in
this probe.

Writing a kubeconfig at 0600 and deleting it afterward was the
alternative. Not writing the token anywhere is strictly tighter, and
nothing else in the probe needed kubectl.

## Teardown

Delete the repository. The tokens it minted live for minutes, no grant
was created, and the probe wrote nothing to the cluster, so there is
nothing else to undo.

Authorization is out of scope on purpose. Granting the forge identity
any role is a separate decision and belongs to the change that needs
it.
