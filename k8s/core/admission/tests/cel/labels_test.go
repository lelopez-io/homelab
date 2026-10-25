// Runs pod-security-labels.yaml through the API server's own CEL environment
// (kyverno-cli cannot supply oldObject or the caller). From this directory:
//
//	go test -v ./...
package celtest

import (
	"os"
	"testing"

	"github.com/google/cel-go/cel"
	"k8s.io/apimachinery/pkg/util/version"
	"k8s.io/apiserver/pkg/cel/environment"
	"sigs.k8s.io/yaml"
)

type policy struct {
	Spec struct {
		MatchConditions []struct{ Name, Expression string }
		Variables       []struct{ Name, Expression string }
		Validations     []struct{ Expression string }
	}
}

func eval(t *testing.T, env *cel.Env, expr string, in map[string]any) any {
	ast, iss := env.Compile(expr)
	if iss.Err() != nil {
		t.Fatalf("compile %q: %v", expr, iss.Err())
	}
	p, err := env.Program(ast)
	if err != nil {
		t.Fatal(err)
	}
	v, _, err := p.Eval(in)
	if err != nil {
		t.Fatalf("eval %q: %v", expr, err)
	}
	return v.Value()
}

func TestLabels(t *testing.T) {
	b, err := os.ReadFile("../../pod-security-labels.yaml")
	if err != nil {
		t.Fatal(err)
	}
	var pol policy
	if err := yaml.Unmarshal(b, &pol); err != nil {
		t.Fatal(err)
	}
	es, err := environment.MustBaseEnvSet(version.MajorMinor(1, 34), true).Extend(environment.VersionedOptions{IntroducedVersion: version.MajorMinor(1, 34), EnvOptions: []cel.EnvOption{
		cel.Variable("object", cel.DynType), cel.Variable("oldObject", cel.DynType),
		cel.Variable("request", cel.DynType), cel.Variable("variables", cel.DynType)}})
	if err != nil {
		t.Fatal(err)
	}
	env, err := es.Env(environment.StoredExpressions)
	if err != nil {
		t.Fatal(err)
	}
	const k, f = "pod-security.kubernetes.io/", "system:serviceaccount:flux-system:kustomize-controller"
	L := func(kv ...string) map[string]any {
		m := map[string]any{}
		for i := 0; i < len(kv); i += 2 {
			m[k+kv[i]] = kv[i+1]
		}
		return m
	}
	U, C := "UPDATE", "CREATE"
	cases := []struct {
		name     string
		op, user string
		old, new map[string]any
		admit    bool
	}{
		{"flux lowers", U, f, L("enforce", "restricted"), L("enforce", "privileged"), true},
		{"user lowers enforce", U, "alice", L("enforce", "restricted"), L("enforce", "baseline"), false},
		{"user removes enforce on restricted (default is restricted)", U, "alice", L("enforce", "restricted"), L(), true},
		{"user removes enforce on baseline (default is restricted)", U, "alice", L("enforce", "baseline"), L(), true},
		{"user lowers audit", U, "alice", L("audit", "restricted"), L("audit", "privileged"), false},
		{"user removes warn on restricted (default is restricted)", U, "alice", L("warn", "restricted"), L(), true},
		{"user lowers warn", U, "alice", L("warn", "restricted"), L("warn", "baseline"), false},
		{"user raises", U, "alice", L("enforce", "baseline"), L("enforce", "restricted"), true},
		{"user unchanged privileged", U, "alice", L("enforce", "privileged"), L("enforce", "privileged"), true},
		{"user adds privileged to unlabelled", U, "alice", L(), L("enforce", "privileged"), false},
		{"user adds restricted to unlabelled", U, "alice", L(), L("enforce", "restricted"), true},
		{"user unrelated label", U, "alice", L("enforce", "restricted"), map[string]any{k + "enforce": "restricted", "x": "y"}, true},
		{"user removes enforce on privileged ns", U, "alice", L("enforce", "privileged"), L(), true},
		{"other sa lowers", U, "system:serviceaccount:flux-system:helm-controller", L("enforce", "restricted"), L("enforce", "baseline"), false},
		{"flux creates privileged", C, f, nil, L("enforce", "privileged"), true},
		{"user creates restricted", C, "alice", nil, L("enforce", "restricted"), true},
		{"user creates restricted with a repo label", C, "alice", nil, map[string]any{k + "enforce": "restricted", "forge/repository": "r"}, true},
		{"user creates baseline", C, "alice", nil, L("enforce", "baseline"), false},
		{"user creates privileged", C, "alice", nil, L("enforce", "privileged"), false},
		{"user creates restricted but warns privileged", C, "alice", nil, L("enforce", "restricted", "warn", "privileged"), false},
		{"user creates restricted but audits baseline", C, "alice", nil, L("enforce", "restricted", "audit", "baseline"), false},
		{"user creates unlabelled (defaults are restricted)", C, "alice", nil, L(), true},
	}
	for _, c := range cases {
		mk := func(l map[string]any) map[string]any {
			return map[string]any{"metadata": map[string]any{"labels": l}}
		}
		in := map[string]any{"object": mk(c.new), "oldObject": nil,
			"request": map[string]any{"operation": c.op, "userInfo": map[string]any{"username": c.user}}}
		if c.old != nil {
			in["oldObject"] = mk(c.old)
		}
		applies := true
		for _, m := range pol.Spec.MatchConditions {
			applies = applies && eval(t, env, m.Expression, in).(bool)
		}
		vars := map[string]any{}
		in["variables"] = vars
		for _, v := range pol.Spec.Variables {
			vars[v.Name] = eval(t, env, v.Expression, in)
		}
		got := true
		if applies {
			for _, v := range pol.Spec.Validations {
				got = got && eval(t, env, v.Expression, in).(bool)
			}
		}
		if got != c.admit {
			t.Errorf("%s: admitted=%v want %v", c.name, got, c.admit)
		}
	}
}
