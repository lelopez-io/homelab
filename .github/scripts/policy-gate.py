#!/usr/bin/env python3
"""Render the tree the way Flux does and judge it with the policies it ships.

usage: python3 -I policy-gate.py [repo-root]

Needs kubectl, helm, yq, kyverno and kubeconform on PATH (install-tools.sh).
No cluster, no secret key: sops files are dropped from the render, and the
values a HelmRelease reads from a Secret or ConfigMap are filled with a
placeholder. Set GATE_OUT=<dir> to keep the rendered objects for inspection.
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
WORK = pathlib.Path(tempfile.mkdtemp(prefix="policy-gate-"))
KUBE_VERSION = "1.34.1"
# Immutable commit of github.com/yannh/kubernetes-json-schema.
SCHEMA_COMMIT = "f7d855e49c34b039c9ef2196444910275e31e09e"
WORKLOADS = {
    "Deployment": ("spec", "template"),
    "StatefulSet": ("spec", "template"),
    "DaemonSet": ("spec", "template"),
    "ReplicaSet": ("spec", "template"),
    "Job": ("spec", "template"),
    "CronJob": ("spec", "jobTemplate", "spec", "template"),
}
NAMESPACED = set(WORKLOADS) | {
    "Pod", "Service", "ServiceAccount", "ConfigMap", "PersistentVolumeClaim",
    "NetworkPolicy", "Role", "RoleBinding",
}
FLUX_KS_UNMODELLED = {"patches", "images", "targetNamespace", "namePrefix",
                      "nameSuffix", "components", "commonMetadata"}

failures = []
notes = []


def run(cmd, stdin=None):
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True)


def docs_of(text):
    out = run(["yq", "-o=json", "-I0", ".", "-"], text)
    if out.returncode:
        sys.exit("yq: " + out.stderr.strip())
    parsed = [json.loads(line) for line in out.stdout.splitlines() if line.strip()]
    return [d for d in parsed if isinstance(d, dict)]


def dump(docs, name):
    path = WORK / name
    path.write_text("\n---\n".join(json.dumps(d) for d in docs) + "\n")
    return str(path)


def group(doc):
    return doc.get("apiVersion", "").rpartition("/")[0]


def is_kind(doc, kind, grp):
    return doc.get("kind") == kind and group(doc) == grp


def meta(doc):
    return doc.get("metadata") or {}


def label(doc):
    return f'{doc.get("kind")}/{meta(doc).get("namespace", "-")}/{meta(doc).get("name")}'


def fail(msg):
    failures.append(msg)
    print("FAIL  " + msg)


def has_kustomization(path):
    return any((path / n).exists() for n in ("kustomization.yaml", "kustomization.yml", "Kustomization"))


def kustomize(path):
    path = pathlib.Path(path)
    if not has_kustomization(path):
        # Flux builds one over every YAML file when the directory has none.
        copy = WORK / f"auto-{abs(hash(str(path)))}"
        shutil.copytree(path, copy)
        files = []

        def walk(d):
            if d != copy and has_kustomization(d):
                files.append(str(d.relative_to(copy)))
                return
            for f in sorted(d.iterdir()):
                if f.is_dir():
                    walk(f)
                elif f.suffix in (".yaml", ".yml"):
                    files.append(str(f.relative_to(copy)))
        walk(copy)
        (copy / "kustomization.yaml").write_text(json.dumps({
            "apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization", "resources": files}))
        path = copy
    out = run(["kubectl", "kustomize", str(path)])
    if out.returncode:
        sys.exit(f"kustomize {path}: {out.stderr.strip()}")
    return out.stdout


def substitute(text):
    """Stand in for the Flux postBuild variables, which come from sops."""
    def fill(m):
        return "192.0.2.0/24" if m.group(1).endswith("CIDR") else "placeholder.invalid"
    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::?[-=?][^}]*)?\}", fill, text)


def encrypted(doc):
    kind = doc.get("kind")
    return "sops" in doc or kind == "Secret" or (isinstance(kind, str) and kind.startswith("ENC["))


def render_tree():
    queue = [p for p in ("k8s", "flux/config/flux-system") if (ROOT / p).is_dir()]
    seen, keys, out = set(), set(), []
    while queue:
        path = queue.pop(0)
        if path in seen:
            continue
        seen.add(path)
        if not (ROOT / path).is_dir():
            fail(f"a Flux Kustomization points at {path}, which is not in the tree")
            continue
        for d in docs_of(substitute(kustomize(ROOT / path))):
            key = (d.get("apiVersion"), d.get("kind"), meta(d).get("namespace"), meta(d).get("name"))
            if encrypted(d) or key in keys:
                continue
            keys.add(key)
            out.append(d)
            if is_kind(d, "Kustomization", "kustomize.toolkit.fluxcd.io"):
                spec = d.get("spec", {})
                bad = FLUX_KS_UNMODELLED & set(spec)
                if bad:
                    fail(f"Flux Kustomization {meta(d).get('name')} sets {sorted(bad)}, which the gate does not render")
                queue.append(spec.get("path", "").lstrip("./"))
    return out


def set_namespace(docs, ns):
    for d in docs:
        if d.get("kind") in NAMESPACED and not meta(d).get("namespace"):
            d.setdefault("metadata", {})["namespace"] = ns
    return docs


def render_release(hr, repos):
    spec, md = hr.get("spec", {}), meta(hr)
    ns = spec.get("targetNamespace") or md.get("namespace")
    name = spec.get("releaseName") or (f"{ns}-{md['name']}" if spec.get("targetNamespace") else md["name"])
    chart = spec.get("chart", {}).get("spec", {})
    ref = chart.get("sourceRef", {})
    if ref.get("kind") != "HelmRepository":
        fail(f"HelmRelease {md.get('name')} uses a {ref.get('kind')} source, which the gate does not render")
        return []
    url = repos.get((ref.get("namespace", md.get("namespace")), ref.get("name")))
    if not url or not chart.get("version"):
        fail(f"HelmRelease {md.get('name')} has no resolvable repository or no chart version")
        return []
    cmd = ["helm", "template", name, chart["chart"], "--namespace", ns,
           "--version", chart["version"], "--kube-version", KUBE_VERSION, "--include-crds", "--skip-tests"]
    if url.startswith("oci://"):
        cmd[3] = url.rstrip("/") + "/" + chart["chart"]
    else:
        cmd += ["--repo", url]
    values = WORK / f"values-{name}.json"
    values.write_text(json.dumps(spec.get("values", {})))
    cmd += ["-f", str(values)]
    for vf in spec.get("valuesFrom", []):
        if vf.get("targetPath"):
            cmd += ["--set-string", vf["targetPath"] + "=placeholder"]
        else:
            notes.append(f"HelmRelease {md.get('name')}: a whole-file valuesFrom is not rendered")
    res = run(cmd)
    if res.returncode:
        fail(f"helm template {md.get('name')}: {res.stderr.strip().splitlines()[-1] if res.stderr.strip() else 'failed'}")
        return []
    rendered = set_namespace(docs_of(res.stdout), ns)
    for i, pr in enumerate(spec.get("postRenderers", [])):
        if "kustomize" not in pr:
            fail(f"HelmRelease {md.get('name')} has a post-renderer the gate does not run")
            continue
        d = WORK / f"post-{name}-{i}"
        d.mkdir()
        dump(rendered, f"post-{name}-{i}/all.yaml")
        k = {"apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization", "resources": ["all.yaml"]}
        k.update(pr["kustomize"])
        (d / "kustomization.yaml").write_text(json.dumps(k))
        rendered = set_namespace(docs_of(kustomize(d)), ns)
    return rendered


def pods_of(docs):
    pods = []
    for d in docs:
        path = WORKLOADS.get(d.get("kind"))
        if not path or group(d) not in ("apps", "batch"):
            continue
        tpl = d
        for key in path:
            tpl = tpl.get(key, {})
        if not tpl.get("spec"):
            continue
        m = tpl.get("metadata", {})
        pods.append({"apiVersion": "v1", "kind": "Pod",
                     "metadata": {"name": meta(d)["name"] + "-pod",
                                  "namespace": meta(d).get("namespace", "default"),
                                  "labels": m.get("labels", {}),
                                  "annotations": m.get("annotations", {})},
                     "spec": tpl["spec"]})
    return pods


def plural(kind):
    k = kind.lower()
    if k.endswith(("s", "x")):
        return k + "es"
    return k[:-1] + "ies" if k.endswith("y") else k + "s"


def rule_match(doc, rules):
    for r in rules or []:
        ops = r.get("operations", ["*"])
        if not ({"*", "CREATE"} & set(ops)):
            continue
        if not ({"*", group(doc)} & set(r.get("apiGroups", ["*"]))):
            continue
        if {"*", plural(doc["kind"])} & set(r.get("resources", ["*"])):
            return True
    return False


def selector_match(sel, labels):
    if not sel:
        return True
    for k, v in (sel.get("matchLabels") or {}).items():
        if labels.get(k) != v:
            return False
    for e in sel.get("matchExpressions") or []:
        key, op, vals = e["key"], e["operator"], e.get("values", [])
        has = key in labels
        if (op == "In" and labels.get(key) not in vals) or (op == "NotIn" and has and labels[key] in vals) \
                or (op == "Exists" and not has) or (op == "DoesNotExist" and has):
            return False
    return True


def ns_labels_of(docs):
    table = {}
    for d in docs:
        if d.get("kind") == "Namespace":
            table[meta(d)["name"]] = {**(meta(d).get("labels") or {}), "kubernetes.io/metadata.name": meta(d)["name"]}
    return table


def in_namespaces(doc, sel, table):
    if not sel:
        return True
    ns = meta(doc)["name"] if doc.get("kind") == "Namespace" else meta(doc).get("namespace")
    if not ns:
        return True
    return selector_match(sel, table.get(ns, {"kubernetes.io/metadata.name": ns}))


def kyverno(policies, resources, tag, *extra):
    # The CLI maps a kind to a resource through the CRDs it is handed.
    crds = [r for r in resources if r.get("kind") == "CustomResourceDefinition"]
    crd_args = ["--crd-paths", dump(crds, f"crd-{tag}.yaml")] if crds else []
    res = run(["kyverno", "apply", dump(policies, f"pol-{tag}.yaml"),
               "--resource", dump(resources, f"res-{tag}.yaml"), "--remove-color", *crd_args, *extra])
    lines = [l for l in res.stdout.splitlines() if l.startswith("policy ") or re.match(r"^\d+ - ", l)]
    return res.returncode, lines or res.stdout.strip().splitlines()[-5:]


def check_vaps(docs, candidates, table):
    vaps = [d for d in docs if is_kind(d, "ValidatingAdmissionPolicy", "admissionregistration.k8s.io")]
    bindings = [d for d in docs if is_kind(d, "ValidatingAdmissionPolicyBinding", "admissionregistration.k8s.io")]
    for vap in vaps:
        name = meta(vap)["name"]
        bound = [b for b in bindings if b["spec"].get("policyName") == name]
        if not bound:
            notes.append(f"VAP {name} has no binding, so the cluster does not enforce it")
        if vap["spec"].get("paramKind"):
            fail(f"VAP {name} takes parameters, which the gate does not supply")
            continue
        cons = vap["spec"].get("matchConstraints", {})
        for b in bound:
            mr = b["spec"].get("matchResources") or {}
            if mr.get("resourceRules") or mr.get("excludeResourceRules") or mr.get("objectSelector") \
                    or cons.get("excludeResourceRules") or cons.get("objectSelector"):
                notes.append(f"VAP {name}: resource and object selectors are not applied, so the gate judges a superset")
            hits = [r for r in candidates
                    if rule_match(r, cons.get("resourceRules"))
                    and in_namespaces(r, cons.get("namespaceSelector"), table)
                    and in_namespaces(r, mr.get("namespaceSelector"), table)]
            if not hits:
                print(f"skip  VAP {name}: matches nothing in the tree")
                continue
            if any("request.userInfo" in c.get("expression", "") for c in vap["spec"].get("matchConditions", [])):
                print(f"skip  VAP {name}: matches on the requesting user, which no object in the tree has")
                continue
            code, lines = kyverno([vap], hits, f"vap-{name}")
            deny = "Deny" in b["spec"].get("validationActions", [])
            if code and deny:
                fail(f"VAP {name} refuses:\n      " + "\n      ".join(lines))
            elif code:
                print(f"warn  VAP {name} reports (Audit/Warn only):\n      " + "\n      ".join(lines))
            else:
                print(f"pass  VAP {name} ({len(hits)} objects)")


def check_kyverno(docs, candidates):
    gen_policies = []
    for p in docs:
        g = group(p)
        if p.get("kind") not in ("ClusterPolicy", "Policy", "ValidatingPolicy", "GeneratingPolicy",
                                 "ImageValidatingPolicy") or g not in ("kyverno.io", "policies.kyverno.io"):
            continue
        name = meta(p)["name"]
        rules = p.get("spec", {}).get("rules", [])
        if p["kind"] == "ImageValidatingPolicy" or any("verifyImages" in r for r in rules):
            notes.append(f"{p['kind']} {name} verifies signatures, which needs the registry; not evaluated")
        if p["kind"] == "GeneratingPolicy" or any("generate" in r for r in rules):
            gen_policies.append(p)
        if p["kind"] in ("ClusterPolicy", "Policy") and any("validate" in r for r in rules):
            code, lines = kyverno([p], candidates, f"kyv-{name}", "--audit-warn")
            if code:
                fail(f"Kyverno policy {name} refuses:\n      " + "\n      ".join(lines))
            else:
                print(f"pass  Kyverno policy {name}")
        elif p["kind"] == "ValidatingPolicy":
            code, lines = kyverno([p], candidates, f"kyv-{name}")
            deny = "Deny" in p.get("spec", {}).get("validationActions", ["Deny"])
            if code and deny:
                fail(f"Kyverno policy {name} refuses:\n      " + "\n      ".join(lines))
            else:
                print(f"{'warn' if code else 'pass'}  Kyverno policy {name}")
    return gen_policies


def generate(gen_policies, docs):
    """Run the generate policies over the rendered namespaces."""
    out = WORK / "generated"
    out.mkdir()
    nss = [d for d in docs if d.get("kind") == "Namespace"]
    for p in gen_policies:
        res = run(["kyverno", "apply", dump([p], f"gen-{meta(p)['name']}.yaml"),
                   "--resource", dump(nss, "namespaces.yaml"), "-o", str(out), "--remove-color"])
        if res.returncode:
            fail(f"generate policy {meta(p)['name']} did not run offline: {res.stdout.strip().splitlines()[-1:]}")
    generated = []
    for f in sorted(out.glob("*.yaml")):
        generated += docs_of(f.read_text())
    return generated


def check_namespace_policy(docs, generated):
    """A namespace has policy when a NetworkPolicy that selects every pod
    exists in it, written in the tree or generated for it by the cluster."""
    covered = {meta(d).get("namespace") for d in docs + generated
               if is_kind(d, "NetworkPolicy", "networking.k8s.io") and not d.get("spec", {}).get("podSelector")}
    for ns in sorted(meta(d)["name"] for d in docs if d.get("kind") == "Namespace"):
        if ns not in covered:
            fail(f"namespace {ns} has no namespace-wide NetworkPolicy, rendered or generated")
    print("done  namespace policy")


def default_enforce():
    out = run(["yq", "-r", '[.. | select(tag == "!!map" and has("defaults")) | .defaults.enforce][0] // "baseline"',
               str(ROOT / "talos/talconfig.yaml")])
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else "baseline"


def check_pod_security(pods, table):
    default = default_enforce()
    by_level = {}
    for p in pods:
        ns = meta(p)["namespace"]
        if ns not in table:
            continue
        level = table[ns].get("pod-security.kubernetes.io/enforce", default)
        if level in ("baseline", "restricted"):
            by_level.setdefault(level, []).append(p)
    for level, group_pods in by_level.items():
        pol = {"apiVersion": "kyverno.io/v1", "kind": "ClusterPolicy",
               "metadata": {"name": f"psa-{level}"},
               "spec": {"validationFailureAction": "Enforce",
                        "rules": [{"name": "psa", "match": {"any": [{"resources": {"kinds": ["Pod"]}}]},
                                   "validate": {"podSecurity": {"level": level, "version": "latest"}}}]}}
        code, lines = kyverno([pol], group_pods, f"psa-{level}")
        if code:
            fail(f"pods in {level} namespaces break the {level} standard:\n      " + "\n      ".join(lines))
        else:
            print(f"pass  pod security {level} ({len(group_pods)} pods)")


def check_schemas(docs):
    loc = (f"https://raw.githubusercontent.com/yannh/kubernetes-json-schema/{SCHEMA_COMMIT}/"
           "{{.NormalizedKubernetesVersion}}-standalone{{.StrictSuffix}}/{{.ResourceKind}}{{.KindSuffix}}.json")
    res = run(["kubeconform", "-strict", "-ignore-missing-schemas", "-summary",
               "-kubernetes-version", KUBE_VERSION, "-schema-location", loc, dump(docs, "schema.yaml")])
    print("schema " + res.stdout.strip().splitlines()[-1] if res.stdout.strip() else "schema (no output)")
    if res.returncode:
        fail("kubeconform:\n      " + "\n      ".join(res.stdout.strip().splitlines()[:-1][:10]))


def main():
    docs = render_tree()
    repos = {(meta(d).get("namespace"), meta(d)["name"]): d["spec"]["url"]
             for d in docs if is_kind(d, "HelmRepository", "source.toolkit.fluxcd.io")}
    for hr in [d for d in docs if is_kind(d, "HelmRelease", "helm.toolkit.fluxcd.io")]:
        docs += render_release(hr, repos)
    table = ns_labels_of(docs)
    made = pods_of(docs)
    pods = [d for d in docs if d.get("kind") == "Pod" and not group(d)] + made
    check_schemas(docs)
    gen_policies = check_kyverno(docs, docs + made)
    generated = generate(gen_policies, docs)
    candidates = docs + made + generated
    check_vaps(docs, candidates, table)
    check_pod_security(pods, table)
    check_namespace_policy(docs, generated)
    if os.environ.get("GATE_OUT"):
        os.makedirs(os.environ["GATE_OUT"], exist_ok=True)
        dump(candidates, "all.yaml")
        pathlib.Path(os.environ["GATE_OUT"], "all.yaml").write_text((WORK / "all.yaml").read_text())
    for n in sorted(set(notes)):
        print("note  " + n)
    print(f"\n{len(docs)} objects, {len(pods)} pods, {len(generated)} generated; {len(failures)} failure(s)")
    sys.exit(1 if failures else 0)


main()
