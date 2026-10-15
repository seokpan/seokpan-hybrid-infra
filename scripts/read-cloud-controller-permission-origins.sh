#!/usr/bin/env bash
set +x
set -euo pipefail
umask 077

root="$HOME/.local/share/seokpan-rosa-plan-inputs-5psj2l62"
source "$root/management/2tejeb2farjaaoef1e74asdf6amrtpu3/activate.sh"
server=$(oc whoami --show-server --request-timeout=20s)
[[ "${server%/}" == 'https://api.seokpan-cloud.8zrv.p1.openshiftapps.com:6443' ]]
audit=$(mktemp -d "$root/controller-permission-origins-XXXXXXXX")

route_allowed=false
route_code=0
oc auth can-i create routes.route.openshift.io --subresource=custom-host \
  -n seokpan-cloud \
  --as=system:serviceaccount:openshift-gitops:openshift-gitops-argocd-application-controller \
  --as-group=system:serviceaccounts --as-group=system:serviceaccounts:openshift-gitops \
  --as-group=system:authenticated --request-timeout=25s \
  >"$audit/custom-host.stdout" 2>"$audit/custom-host.stderr" || route_code=$?
route_text=$(cat "$audit/custom-host.stdout")
if [[ "$route_code" == 0 && "$route_text" == yes ]]; then
  route_allowed=true
elif [[ "$route_code" == 1 && ( "$route_text" == no || "$route_text" == 'no - '* ) ]]; then
  route_allowed=false
else
  echo 'STOP_CUSTOM_HOST_AUTH_QUERY; raw output is in the private result directory.'
  printf 'Private permission inventory directory: %s\n' "$audit"
  exit 1
fi

for kind in rolebindings roles; do
  oc get "$kind" -n seokpan-cloud --request-timeout=25s \
    --show-managed-fields=true -o json >"$audit/$kind.json"
done
for kind in clusterrolebindings clusterroles; do
  oc get "$kind" --request-timeout=25s \
    --show-managed-fields=true -o json >"$audit/$kind.json"
done

jq -n --argjson custom_host_allowed "$route_allowed" \
  --slurpfile rb "$audit/rolebindings.json" \
  --slurpfile cb "$audit/clusterrolebindings.json" \
  --slurpfile nr "$audit/roles.json" \
  --slurpfile cr "$audit/clusterroles.json" '
  def subject_matches:
    any(.subjects[]?;
      (.kind=="ServiceAccount" and .namespace=="openshift-gitops"
       and .name=="openshift-gitops-argocd-application-controller")
      or (.kind=="User" and .name=="system:serviceaccount:openshift-gitops:openshift-gitops-argocd-application-controller")
      or (.kind=="Group" and (.name as $name |
        ["system:authenticated","system:serviceaccounts","system:serviceaccounts:openshift-gitops"]
        | index($name)!=null)));
  def resolve_role($b):
    (if $b.roleRef.kind=="ClusterRole" then $cr[0].items else $nr[0].items end)[]
    | select(.metadata.name==$b.roleRef.name);
  def includes($values; $value):
    ((($values // []) | index($value))!=null)
    or ((($values // []) | index("*"))!=null);
  def permits($verb; $group; $resource):
    any(.rules[]?;
      includes(.verbs; $verb) and includes(.apiGroups; $group)
      and includes(.resources; $resource)
      and (((.resourceNames // [])|length)==0));
  [($rb[0].items+$cb[0].items)[] | select(subject_matches)] as $bindings
  | {
      status:"CONTROLLER_EXTRA_PERMISSION_ORIGIN_INVENTORY",
      route_custom_host_allowed:$custom_host_allowed,
      sources:[
        $bindings[] as $b | resolve_role($b) as $r
        | {binding_kind:$b.kind,binding:$b.metadata.name,
           role_kind:$b.roleRef.kind,role:$r.metadata.name,
           secret_get:($r|permits("get";"";"secrets")),
           rolebinding_create:($r|permits("create";"rbac.authorization.k8s.io";"rolebindings")),
           owners:[$r.metadata.ownerReferences[]?|{kind,name}],
           managers:([$r.metadata.managedFields[]?.manager]|unique),
           olm_owner:($r.metadata.labels["olm.owner"] // null),
           aggregated:($r.aggregationRule!=null)}
        | select(.secret_get or .rolebinding_create)
      ],
      unresolved_role_references:[
        $bindings[] as $b | select(([resolve_role($b)]|length)!=1)
        | {binding:$b.metadata.name,role:$b.roleRef.name}
      ],
      resource_mutations:0,controller_45_checks_repeated:false,
      ingress_apply_repeated:false,application_started:false
    }
' | tee "$audit/receipt.json"
printf 'Private permission inventory directory: %s\n' "$audit"
