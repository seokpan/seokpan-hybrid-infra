#!/usr/bin/env bash
# Human-run partial-Apply recovery for the reviewed ROSA source snapshot.
# Args: input-root prior-result-dir existing-cluster-plan-dir input-sha original-plan-sha contract-id oidc-id
# Preserve State and prior attempts. No init, destroy, import, or state edits.
set +x
set -Eeuo pipefail
umask 077
[[ "$#" == 7 ]] || { echo "Usage: $0 INPUT_ROOT PREVIOUS_RESULT CLUSTER_PLAN_DIR INPUT_SHA ORIGINAL_PLAN_SHA CONTRACT_ID OIDC_ID" >&2; exit 2; }
root="$1"
previous="$2"
remaining_dir="$3"
expected_input_sha="$4"
expected_plan_sha="$5"
expected_contract_id="$6"
expected_oidc_id="$7"
source_dir="$root/source"
input_file="$root/protected/inputs.tfvars.json"
marker="$previous/cluster-only-after-data-tag-apply-attempt.json"
phase=LOCAL_CONTEXT
result_dir=''
stop() {
  trap - ERR
  printf 'STOP: %s; phase=%s; no automatic retry.\n' "$1" "$phase" >&2
  [[ -z "$result_dir" ]] || printf 'Protected result directory: %s\n' "$result_dir" >&2
  exit 1
}
trap 'stop COMMAND_FAILED' ERR
trap 'unset RHCS_TOKEN AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN' EXIT
[[ "$EUID" != 0 ]] || stop USE_PERSONAL_CONTROLLER_ACCOUNT
for name in aws terraform jq python3 sha256sum tee; do command -v "$name" >/dev/null; done
[[ -d "$previous/tf-data" && ! -L "$previous/tf-data" ]] || stop EXISTING_TF_DATA_REQUIRED
[[ ! -e "$marker" && ! -L "$marker" ]] || stop RECOVERY_APPLY_ALREADY_ATTEMPTED
[[ "$(sha256sum "$previous/rosa.tfplan" | cut -d ' ' -f1)" == "$expected_plan_sha" ]] || stop ORIGINAL_PLAN_CHANGED
[[ "$(sha256sum "$input_file" | cut -d ' ' -f1)" == "$expected_input_sha" ]] || stop INPUT_CHANGED
[[ -f "$remaining_dir/remaining.tfplan" && ! -L "$remaining_dir/remaining.tfplan" ]] || stop STORED_CLUSTER_PLAN_REQUIRED
expected_remaining_sha=$(sha256sum "$remaining_dir/remaining.tfplan" | cut -d ' ' -f1)
# This protected source is a file snapshot; a .git directory is not required.
python3 - "$source_dir" <<'PY'
from pathlib import Path
import hashlib, json, sys
root = Path(sys.argv[1])
expected = {
    "terraform/rosa/.terraform.lock.hcl": "668098ad0f1e293982e9bcf8e931128346a00049",
    "terraform/rosa/backend.tf": "a77421ca8d10663483aba7bc25a01fb949a34ae9",
    "terraform/rosa/bindings.tf": "b0288ba515207445956e7283afa0a1acf34348e3",
    "terraform/rosa/cluster.tf": "74d13092a6cea05cd963779215a24417be3b03cc",
    "terraform/rosa/inputs.tf": "fb45e85d4343d2e3de2378d4099683c251ef30e8",
    "terraform/rosa/oidc.tf": "4e53bfd0fe9176aab8118a87226c59f7dd661fa2",
    "terraform/rosa/outputs.tf": "3a45ccdfc177aae6df384ff35eaff1920dcfca17",
    "terraform/rosa/providers.tf": "fe107451e734f48b16baed2d57b004cd487c8c8c",
    "terraform/rosa/variables.tf": "27b70f1d16700c8f74ea76442e5efce3aee26f5b",
    "terraform/rosa/versions.tf": "9eebe38d8dcc9243a310c3c6fd0a6b011c726da5",
    "scripts/tf-session.sh": "608a765e2ba40b472b2cb431223b48c396c00866"
}
for name, digest in expected.items():
    path = root / name
    if path.is_symlink() or not path.is_file():
        raise SystemExit('STOP: SOURCE_FILE_MISSING_OR_SYMLINK')
    raw = path.read_bytes()
    blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
    if blob != digest:
        raise SystemExit('STOP: PINNED_SOURCE_FILE_CHANGED')
folder = root / 'terraform/rosa'
actual = {p.name for p in folder.iterdir() if p.name.endswith(('.tf', '.tf.json'))}
wanted = {Path(name).name for name in expected if name.endswith(('.tf', '.tf.json'))}
if actual != wanted or any(folder.glob('*.auto.tfvars*')) or any((folder / n).exists() for n in ('terraform.tfvars', 'terraform.tfvars.json')):
    raise SystemExit('STOP: UNREVIEWED_TERRAFORM_INPUT_FILE')
print('PASS: reviewed source file contents; .git not required')
PY
while IFS= read -r name; do
  case "$name" in TF_CLI_ARGS*|TF_VAR_*|TF_LOG*|RHCS_*) unset "$name" ;; esac
done < <(compgen -e)
unset AWS_PROFILE AWS_DEFAULT_PROFILE AWS_ROLE_ARN AWS_WEB_IDENTITY_TOKEN_FILE TF_CLI_CONFIG_FILE TF_SESSION_SECONDS
export AWS_REGION=ap-northeast-2 AWS_DEFAULT_REGION=ap-northeast-2 AWS_PAGER='' AWS_MAX_ATTEMPTS=1
export TF_DATA_DIR="$previous/tf-data" TF_WORKSPACE=default TF_IN_AUTOMATION=1 CHECKPOINT_DISABLE=1
result_dir=$(mktemp -d "${previous%/*}/rosa-stored-cluster-apply-XXXXXXXX")

phase=LOCAL_STORED_PLAN_REVIEW
python3 - "$remaining_dir/original-plan.json" "$remaining_dir/remaining-plan.json" "$expected_contract_id" "$expected_oidc_id" <<'PY'
import json, re, sys
old, new = [json.load(open(p)) for p in sys.argv[1:3]]
errors, observed = [], []
def check(ok, reason, resource=None, field=None):
    if not ok:
        errors.append({k:v for k,v in {'reason':reason,'resource':resource,'field':field}.items() if v is not None})
def label(key):
    return key if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*', str(key)) else '<key>'
def known_deltas(a, b, unknown=False, path='after'):
    if unknown is True:
        return []
    if isinstance(a, dict) and isinstance(b, dict):
        mask = unknown if isinstance(unknown, dict) else {}
        result = [{'field':path+'.'+label(k),'class':'KNOWN_KEY_MISSING'} for k in set(a)-set(b)]
        result += [{'field':path+'.'+label(k),'class':'UNEXPECTED_STATE_FIELD'} for k in set(b)-set(a) if not mask.get(k)]
        for k in sorted(set(a)&set(b)):
            result += known_deltas(a[k],b[k],mask.get(k,False),path+'.'+label(k))
        return result
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [{'field':path,'class':'LIST_LENGTH_CHANGED'}]
        mask = unknown if isinstance(unknown,list) else []
        return [d for i,(x,y) in enumerate(zip(a,b)) for d in known_deltas(x,y,mask[i] if i<len(mask) else False,path+'['+str(i)+']')]
    if a == b and (type(a) is type(b) or (isinstance(a,(int,float)) and not isinstance(a,bool) and isinstance(b,(int,float)) and not isinstance(b,bool))):
        return []
    return [{'field':path,'class':'NULL_EMPTY_REPRESENTATION' if (a is None and b in ('',[],{})) or (b is None and a in ('',[],{})) else 'KNOWN_VALUE_CHANGED','old_type':type(a).__name__,'new_type':type(b).__name__}]
check(new.get('complete') is True and new.get('errored') is not True,'INCOMPLETE_PLAN')
check(old.get('variables') == new.get('variables'),'INPUT_DELTA')
check(old.get('configuration') == new.get('configuration'),'CONFIGURATION_DELTA')
o = {r['address']:r for r in old['resource_changes'] if r['mode']=='managed'}
n = {r['address']:r for r in new['resource_changes'] if r['mode']=='managed'}
cluster='rhcs_cluster_rosa_classic.cluster[0]'
check(len(o)==16 and set(o)==set(n) and cluster in n,'RESOURCE_SET_DELTA')
if errors:
    print(json.dumps({'status':'STOP_STORED_CLUSTER_PLAN','errors':errors,'api_calls':0,'apply':'NOT_RUN'},sort_keys=True))
    raise SystemExit(1)
account = new['variables']['foundation']['value']['account_id']
for address,r in n.items():
    c, original = r['change'],o[address]['change']
    resource=address.split('[')[0]
    check(original['actions']==['create'],'ORIGINAL_SCOPE_UNEXPECTED',resource)
    check(c['actions']==(['create'] if address==cluster else ['no-op']),'MANAGED_ACTION_CHANGED',resource)
    a,b,mask=original['after'],c['after'],original.get('after_unknown',{})
    if address==cluster:
        for d in known_deltas(a,b,mask):
            errors.append({'reason':'CLUSTER_APPROVED_VALUE_CHANGED','resource':resource,**d})
        check(b.get('sts',{}).get('oidc_config_id')==sys.argv[4],'CLUSTER_OIDC_REFERENCE_CHANGED',resource,'sts.oidc_config_id')
        continue
    # Existing resources were already approved and created. No mutation is allowed.
    check(c.get('before')==b,'EXISTING_BEFORE_AFTER_CHANGED',resource)
    for d in known_deltas(a,b,mask):
        observed.append({'resource':resource,**d})
    if r['type']=='aws_iam_role':
        for key in ('name','path'):
            if key in a and not mask.get(key):
                check(a[key]==b.get(key),'ROLE_IDENTITY_CHANGED',resource,key)
        normalize=lambda x: None if x in (None,'') else x
        check(normalize(a.get('permissions_boundary'))==normalize(b.get('permissions_boundary')),'ROLE_BOUNDARY_CHANGED',resource,'permissions_boundary')
    elif r['type']=='aws_iam_role_policy_attachment':
        for key in ('role','policy_arn'):
            if key in a and not mask.get(key):
                check(a[key]==b.get(key),'ROLE_POLICY_BINDING_CHANGED',resource,key)
    elif r['type']=='aws_iam_openid_connect_provider':
        check(set(b.get('client_id_list',[]))=={'openshift','sts.amazonaws.com'},'OIDC_CLIENT_IDS_CHANGED',resource)
        url=b.get('url','').removeprefix('https://')
        check(url.endswith('/'+sys.argv[4]) and b.get('arn')=='arn:aws:iam::'+account+':oidc-provider/'+url,'OIDC_PROVIDER_IDENTITY_CHANGED',resource)
    elif r['type']=='rhcs_rosa_oidc_config':
        check(b.get('id')==sys.argv[4] and b.get('managed') is True,'CREATED_OIDC_CHANGED',resource)
    elif r['type']=='terraform_data':
        check(address=='terraform_data.input_contract' and b.get('id')==sys.argv[3] and b.get('input')==a.get('input') and b.get('output')==b.get('input'),'CONTRACT_CHANGED',resource)
    else:
        check(False,'UNREVIEWED_EXISTING_RESOURCE_TYPE',resource)
    for key,value in (a.get('tags_all') or {}).items():
        check((b.get('tags_all') or {}).get(key)==value,'REQUIRED_RESOURCE_TAG_CHANGED',resource,'tags_all.'+label(key))
# Separately-created attachments appear on the Role at refresh. Accept only
# the exact role-specific attachment already present in this reviewed Plan.
attachments = {}
for address, r in n.items():
    if r['type'] != 'aws_iam_role_policy_attachment':
        continue
    values = r['change']['after']
    role, policy = values.get('role'), values.get('policy_arn')
    if isinstance(role, str) and isinstance(policy, str):
        attachments.setdefault(role, []).append(policy)
accepted_refresh = []
seen_drift = set()
for r in new.get('resource_drift', []):
    address, d = r['address'], r['change']
    resource = address.split('[')[0]
    before, after = d.get('before'), d.get('after')
    planned = n.get(address, {})
    changed = set(before or {}) ^ set(after or {})
    if isinstance(before, dict) and isinstance(after, dict):
        changed |= {k for k in set(before) & set(after) if before[k] != after[k]}
    eligible = (address not in seen_drift and planned.get('mode') == 'managed'
                and planned.get('type') == r.get('type')
                and planned.get('change', {}).get('actions') == ['no-op']
                and d.get('actions') == ['update']
                and isinstance(before, dict) and isinstance(after, dict)
                and after == planned.get('change', {}).get('after'))
    seen_drift.add(address)
    accepted = None
    if eligible and r['type'] == 'aws_iam_openid_connect_provider':
        if changed == {'tags'} and before.get('tags') in (None, {}) and after.get('tags') in (None, {}):
            accepted = 'OIDC_NULL_EMPTY_TAGS_ONLY'
    elif eligible and r['type'] == 'aws_iam_role' and changed == {'managed_policy_arns'}:
        role = after.get('name')
        expected = attachments.get(role, [])
        prior, current = before.get('managed_policy_arns'), after.get('managed_policy_arns')
        prior = [] if prior is None else prior
        if (len(expected) == 1 and isinstance(prior, list) and isinstance(current, list)
                and all(isinstance(v, str) for v in prior + current)
                and len(prior) == len(set(prior)) and len(current) == len(set(current))
                and set(prior) <= set(expected) and set(current) == set(expected)):
            accepted = 'EXACT_REVIEWED_ROLE_POLICY_ATTACHMENT_REFRESH'
    if accepted:
        accepted_refresh.append({'resource': resource, 'class': accepted})
    else:
        errors.append({'reason': 'MANAGED_RESOURCE_DRIFT', 'resource': resource,
                       'fields': [d['field'] for d in known_deltas(before, after)]})
checks=new.get('checks',[])
check(len(checks)==17 and all(c.get('status')=='pass' for c in checks),'PLAN_CHECK_NOT_PASS')
check(set(new.get('output_changes',{}))<=set(old.get('output_changes',{})),'OUTPUT_SCOPE_DELTA')
result={'status':'STOP_STORED_CLUSTER_PLAN' if errors else 'PASS_STORED_CLUSTER_ONLY_PLAN','errors':errors,'existing_state_representation_differences':observed[:100], 'accepted_refresh_differences':accepted_refresh, 'managed_actions':{'/'.join(action):sum(r['change']['actions']==action for r in n.values()) for action in (['create'],['no-op'],['update'],['delete'])}, 'api_calls':0,'apply':'NOT_RUN','values':'NOT_EMITTED'}
print(json.dumps(result,sort_keys=True))
if errors:
    raise SystemExit(1)
PY

phase=RHCS_OFFLINE_TOKEN
read -r -s -p 'Red Hat offline token (hidden; paste token only): ' RHCS_TOKEN
printf '\n'
[[ -n "$RHCS_TOKEN" && "$RHCS_TOKEN" != *[[:space:]]* ]] || stop TOKEN_FORMAT
export RHCS_TOKEN
phase=AWS_MFA_SESSION
read -r -s -p 'AWS MFA (6 digits, hidden): ' mfa
printf '\n'
[[ "$mfa" =~ ^[0-9]{6}$ ]] || stop MFA_FORMAT
source "$source_dir/scripts/tf-session.sh" rosa <<<"$mfa" >"$result_dir/aws-auth.log" 2>&1
unset mfa
phase=PURPOSE_CALLER
account=$(jq -er ' .foundation.account_id | select(type == "string" and test("^[0-9]{12}$")) ' "$input_file")
caller=$(aws sts get-caller-identity --query Arn --output text 2>"$result_dir/caller.log")
[[ "$caller" == "arn:aws:sts::${account}:assumed-role/seokpan-tf-rosa/"* ]] || stop WRONG_EXECUTION_ROLE
phase=STORED_PLAN_BINARY_BINDING
terraform -chdir="$source_dir/terraform/rosa" show -json "$previous/rosa.tfplan" >"$result_dir/original-verified.json" 2>"$result_dir/original-show.log"
terraform -chdir="$source_dir/terraform/rosa" show -json "$remaining_dir/remaining.tfplan" >"$result_dir/remaining-verified.json" 2>"$result_dir/show.log"
python3 - "$remaining_dir" "$result_dir" <<'PY'
from pathlib import Path
import json, sys
stored, verified=map(Path,sys.argv[1:])
for a,b in [('original-plan.json','original-verified.json'),('remaining-plan.json','remaining-verified.json')]:
    if json.loads((stored/a).read_text())!=json.loads((verified/b).read_text()):
        raise SystemExit('STOP: STORED_PLAN_BINARY_JSON_MISMATCH; APPLY_NOT_RUN')
print('PASS: existing stored plan binary and reviewed JSON match; no new Plan')
PY

plan_sha=$(sha256sum "$remaining_dir/remaining.tfplan" | cut -d ' ' -f1)
[[ "$plan_sha" == "$expected_remaining_sha" ]] || stop REMAINING_PLAN_CHANGED
(set -o noclobber; printf '{"plan_sha256":"%s","result_directory":"%s","started_at_utc":"%s"}\n' \
  "$plan_sha" "$result_dir" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$marker")
phase=STORED_CLUSTER_PLAN_APPLY
printf 'ROSA_STORED_CLUSTER_APPLY_STARTED_KST: %s\n' "$(TZ=Asia/Seoul date '+%F %T KST')"
if terraform -chdir="$source_dir/terraform/rosa" apply -input=false -no-color -lock-timeout=60s "$remaining_dir/remaining.tfplan" 2>&1 | tee "$result_dir/apply.log"; then
  phase=PROTECTED_HANDOFF_EXPORT
  terraform -chdir="$source_dir/terraform/rosa" output -json platform_handoff >"$result_dir/platform-handoff.json" 2>"$result_dir/output.log"
  printf '{"status":"STORED_CLUSTER_APPLY_EXIT_0","node_ready_management_access":"NOT_YET_VERIFIED"}\n' | tee "$result_dir/receipt.json"
  printf 'ROSA_APPLY_COMPLETED_KST: %s\n' "$(TZ=Asia/Seoul date '+%F %T KST')"
  printf 'Protected result directory: %s\n' "$result_dir"
else
  stop APPLY_OR_LOG_WRITE_FAILED_PARTIAL_STATE_POSSIBLE
fi
