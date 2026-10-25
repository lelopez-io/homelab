// Runs one ValidatingAdmissionPolicy through the API server's own CEL
// compiler and validator, so a fixture proves what admission would do.
// Usage: go run . <policy.yaml> <cases.yaml> <registry-host>
package main

import (
	"context"
	"fmt"
	"os"
	"strings"

	admissionv1 "k8s.io/api/admissionregistration/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apiserver/pkg/admission"
	"k8s.io/apiserver/pkg/admission/plugin/cel"
	"k8s.io/apiserver/pkg/admission/plugin/policy/matching"
	"k8s.io/apiserver/pkg/admission/plugin/policy/validating"
	"k8s.io/apiserver/pkg/admission/plugin/webhook/matchconditions"
	celconfig "k8s.io/apiserver/pkg/apis/cel"
	"k8s.io/apiserver/pkg/authentication/user"
	"k8s.io/apiserver/pkg/cel/environment"
	"sigs.k8s.io/yaml"
)

var _ = matching.Matcher{}

type testCase struct {
	Name      string                 `json:"name"`
	Want      string                 `json:"want"`
	As        string                 `json:"as"`
	Namespace string                 `json:"namespace"`
	Resource  string                 `json:"resource"`
	Object    map[string]interface{} `json:"object"`
	Old       map[string]interface{} `json:"old"`
	Operation string                 `json:"operation"`
	// Labels sit on the namespace a namespaced object is created in.
	Labels map[string]string `json:"labels"`
}

var kinds = map[string][3]string{
	"namespaces":      {"", "Namespace", "namespaces"},
	"networkpolicies": {"networking.k8s.io", "NetworkPolicy", "networkpolicies"},
	"pods":            {"", "Pod", "pods"},
	"deployments":     {"apps", "Deployment", "deployments"},
	"statefulsets":    {"apps", "StatefulSet", "statefulsets"},
	"daemonsets":      {"apps", "DaemonSet", "daemonsets"},
	"jobs":            {"batch", "Job", "jobs"},
	"cronjobs":        {"batch", "CronJob", "cronjobs"},
}

func must(err error) {
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
}

func main() {
	raw, err := os.ReadFile(os.Args[1])
	must(err)
	text := strings.ReplaceAll(string(raw), "${FORGE_HOST}", os.Args[3])
	var policy admissionv1.ValidatingAdmissionPolicy
	for _, doc := range strings.Split(text, "\n---\n") {
		if strings.Contains(doc, "kind: ValidatingAdmissionPolicy\n") {
			must(yaml.UnmarshalStrict([]byte(doc), &policy))
		}
	}
	rawCases, err := os.ReadFile(os.Args[2])
	must(err)
	var cases []testCase
	must(yaml.UnmarshalStrict(rawCases, &cases))

	env, err := cel.NewCompositionEnv(cel.VariablesTypeName, environment.MustBaseEnvSet(environment.DefaultCompatibilityVersion(), true))
	must(err)
	opts := cel.OptionalVariableDeclarations{HasAuthorizer: true, StrictCost: true}
	msgOpts := cel.OptionalVariableDeclarations{StrictCost: true}
	fc := cel.NewCompositedCompilerFromTemplate(env)
	var vars []cel.NamedExpressionAccessor
	for _, v := range policy.Spec.Variables {
		vars = append(vars, &validating.Variable{Name: v.Name, Expression: v.Expression})
	}
	fc.CompileAndStoreVariables(vars, opts, environment.StoredExpressions)
	var mc []cel.ExpressionAccessor
	for i := range policy.Spec.MatchConditions {
		mc = append(mc, (*matchconditions.MatchCondition)(&policy.Spec.MatchConditions[i]))
	}
	matcher := matchconditions.NewMatcher(fc.CompileCondition(mc, opts, environment.StoredExpressions), policy.Spec.FailurePolicy, "policy", "validate", policy.Name)
	var vals, msgs []cel.ExpressionAccessor
	for _, v := range policy.Spec.Validations {
		vals = append(vals, &validating.ValidationCondition{Expression: v.Expression, Message: v.Message})
		if v.MessageExpression != "" {
			msgs = append(msgs, &validating.MessageExpressionCondition{MessageExpression: v.MessageExpression})
		} else {
			msgs = append(msgs, nil)
		}
	}
	validator := validating.NewValidator(
		fc.CompileCondition(vals, opts, environment.StoredExpressions), matcher,
		fc.CompileCondition(nil, opts, environment.StoredExpressions),
		fc.CompileCondition(msgs, msgOpts, environment.StoredExpressions),
		policy.Spec.FailurePolicy)

	failed := 0
	for _, c := range cases {
		k, ok := kinds[c.Resource]
		if !ok {
			must(fmt.Errorf("case %q: unknown resource %q", c.Name, c.Resource))
		}
		who := c.As
		if who == "" {
			who = "forge:repo:owner/site"
		}
		ns := c.Namespace
		if ns == "" {
			ns = "site-dev"
		}
		op, opts := admission.Create, runtime.Object(&metav1.CreateOptions{})
		var old runtime.Object
		if c.Operation == "UPDATE" {
			op, opts = admission.Update, &metav1.UpdateOptions{}
			old = &unstructured.Unstructured{Object: c.Old}
		}
		if c.Resource == "namespaces" {
			ns = ""
		}
		obj := &unstructured.Unstructured{Object: c.Object}
		gvk := schema.GroupVersionKind{Group: k[0], Version: "v1", Kind: k[1]}
		gvr := schema.GroupVersionResource{Group: k[0], Version: "v1", Resource: k[2]}
		attr := admission.NewAttributesRecord(obj, old, gvk, ns, "x", gvr, "", op, opts, false, &user.DefaultInfo{Name: who})
		va := &admission.VersionedAttributes{Attributes: attr, VersionedKind: gvk, VersionedObject: obj, VersionedOldObject: old}
		res := validator.Validate(context.Background(), gvr, va, nil, &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{Name: ns, Labels: c.Labels}}, celconfig.RuntimeCELCostBudget, nil)
		got, why := "admit", ""
		for _, d := range res.Decisions {
			if d.Action != validating.ActionAdmit {
				got, why = "deny", d.Message
				break
			}
		}
		mark := "ok  "
		if got != c.Want {
			mark, failed = "FAIL", failed+1
		}
		fmt.Printf("%s %-5s %s", mark, got, c.Name)
		if got == "deny" && (c.Want != "deny" || strings.Contains(why, "failed expression") || strings.Contains(why, "error")) {
			fmt.Printf("  [%s]", why)
		}
		fmt.Println()
	}
	if failed > 0 {
		fmt.Printf("%d of %d cases failed\n", failed, len(cases))
		os.Exit(1)
	}
}
