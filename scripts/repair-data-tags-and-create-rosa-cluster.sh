#!/usr/bin/env bash
# Human-run partial-Apply recovery for the reviewed ROSA source snapshot.
# Args: input-root prior-result-dir input-sha plan-sha contract-id oidc-id data-subnet-ids-csv
# Preserve State and prior attempts. No init, destroy, import, or state edits.
set +x
set -Eeuo pipefail
umask 077
[[ "$#" == 7 ]] || { echo "Usage: $0 INPUT_ROOT PREVIOUS_RESULT INPUT_SHA PLAN_SHA CONTRACT_ID OIDC_ID DATA_SUBNET_IDS_CSV" >&2; exit 2; }
root="$1"
previous="$2"
expected_input_sha="$3"
expected_plan_sha="$4"
expected_contract_id="$5"
expected_oidc_id="$6"
data_subnet_csv="$7"
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
result_dir=$(mktemp -d "${previous%/*}/rosa-cluster-tagfix-XXXXXXXX")

phase=DATA_SUBNET_TAG_SCOPE
account=$(jq -er '.foundation.account_id | select(type == "string" and test("^[0-9]{12}$"))' "$input_file")
base_aws=(env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN aws)
base_caller=$("${base_aws[@]}" sts get-caller-identity --query Arn --output text 2>"$result_dir/base-caller.log")
[[ "$base_caller" == "arn:aws:iam::${account}:user/"* ]] || stop OWN_IAM_USER_REQUIRED_FOR_TAG_ONLY_REPAIR
IFS=',' read -r -a data_ids <<<"$data_subnet_csv"
[[ "${#data_ids[@]}" == 3 ]] || stop EXACT_THREE_DATA_SUBNETS_REQUIRED
"${base_aws[@]}" ec2 describe-subnets --region ap-northeast-2 --subnet-ids "${data_ids[@]}"   --output json --cli-connect-timeout 10 --cli-read-timeout 20   >"$result_dir/subnets-before.json" 2>"$result_dir/subnets-before.log"
python3 - "$input_file" "$result_dir/subnets-before.json" "$data_subnet_csv" <<'PY'
import json, sys
inputs, response = [json.load(open(p)) for p in sys.argv[1:3]]
f = inputs['foundation']
ids = sys.argv[3].split(',')
subnets = response['Subnets']
if len(set(ids)) != 3 or len(subnets) != 3 or {s['SubnetId'] for s in subnets} != set(ids):
    raise SystemExit('STOP: DATA_SUBNET_SET_MISMATCH')
install = {s[k] for s in f['subnets'].values() for k in ('public_id', 'rosa_private_id')}
expected = {'192.168.70.0/24': 'az_a', '192.168.71.0/24': 'az_b', '192.168.72.0/24': 'az_c'}
if {s['CidrBlock'] for s in subnets} != set(expected) or set(ids) & install:
    raise SystemExit('STOP: DATA_CIDR_OR_INSTALL_SUBNET_MISMATCH')
for s in subnets:
    slot = expected[s['CidrBlock']]
    tags = {t['Key']: t['Value'] for t in s.get('Tags', [])}
    if (s['VpcId'] != f['vpc_id'] or s['AvailabilityZone'] != f['subnets'][slot]['availability_zone']
        or tags.get('Name') != 'seokpan-fnd-data-' + slot or tags.get('Project') != 'seokpan'
        or tags.get('kubernetes.io/cluster/unmanaged') not in (None, 'true')):
        raise SystemExit('STOP: DATA_SUBNET_OWNERSHIP_OR_TAG_CHANGED')
print('PASS: only the three reviewed Data subnets; install subnets excluded')
PY
phase=DATA_SUBNET_TAG_WRITE
need_tags=$(python3 -c 'import json,sys; print(int(any(not any(t["Key"]=="kubernetes.io/cluster/unmanaged" and t["Value"]=="true" for t in s.get("Tags",[])) for s in json.load(open(sys.argv[1]))["Subnets"])))' "$result_dir/subnets-before.json")
if [[ "$need_tags" == 1 ]]; then
  "${base_aws[@]}" ec2 create-tags --region ap-northeast-2 --resources "${data_ids[@]}"     --tags Key=kubernetes.io/cluster/unmanaged,Value=true     --cli-connect-timeout 10 --cli-read-timeout 20     >"$result_dir/tag-write.json" 2>"$result_dir/tag-write.log" || stop DATA_SUBNET_TAG_WRITE_FAILED
fi
phase=DATA_SUBNET_TAG_VERIFY
"${base_aws[@]}" ec2 describe-subnets --region ap-northeast-2 --subnet-ids "${data_ids[@]}"   --output json --cli-connect-timeout 10 --cli-read-timeout 20   >"$result_dir/subnets-after.json" 2>"$result_dir/subnets-after.log"
python3 - "$result_dir/subnets-before.json" "$result_dir/subnets-after.json" <<'PY'
import json, sys
before, after = [{s['SubnetId']: s for s in json.load(open(p))['Subnets']} for p in sys.argv[1:]]
key = 'kubernetes.io/cluster/unmanaged'
if set(before) != set(after):
    raise SystemExit('STOP: DATA_SUBNET_SET_CHANGED')
for id, new in after.items():
    old = before[id]
    old_tags = {t['Key']: t['Value'] for t in old.get('Tags', [])}
    new_tags = {t['Key']: t['Value'] for t in new.get('Tags', [])}
    if (new_tags.get(key) != 'true' or {k:v for k,v in old_tags.items() if k != key} != {k:v for k,v in new_tags.items() if k != key}
        or any(old[k] != new[k] for k in ('VpcId', 'CidrBlock', 'AvailabilityZone'))):
        raise SystemExit('STOP: DATA_SUBNET_TAG_VERIFY_FAILED')
print('PASS_DATA_SUBNET_EXCLUSION_TAGS: 3/3; only the exclusion tag changed')
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
phase=REMAINING_PLAN
terraform -chdir="$source_dir/terraform/rosa" show -json "$previous/rosa.tfplan" >"$result_dir/original-plan.json" 2>"$result_dir/original-show.log"
code=0
terraform -chdir="$source_dir/terraform/rosa" plan -input=false -no-color -lock-timeout=60s \
  -detailed-exitcode -var-file="$input_file" -out="$result_dir/remaining.tfplan" \
  >"$result_dir/plan.log" 2>&1 || code=$?
[[ "$code" == 2 ]] || stop REMAINING_PLAN_NOT_EXPECTED_CHANGES
terraform -chdir="$source_dir/terraform/rosa" show -json "$result_dir/remaining.tfplan" >"$result_dir/remaining-plan.json" 2>"$result_dir/show.log"
phase=CLUSTER_ONLY_SCOPE_COMPARISON
python3 - "$result_dir/original-plan.json" "$result_dir/remaining-plan.json" "$expected_contract_id" "$expected_oidc_id" <<'PY'
import json, sys
old, new = [json.load(open(p)) for p in sys.argv[1:3]]
def require(ok, reason):
    if not ok:
        raise SystemExit('STOP: ' + reason + '; CLUSTER_APPLY_NOT_RUN')
def known_equal(a, b, unknown=False, unordered=False):
    if unknown is True:
        return True
    if isinstance(a, dict):
        if not isinstance(b, dict):
            return False
        mask = unknown if isinstance(unknown, dict) else {}
        if set(a) - set(b) or any(not mask.get(k) for k in set(b) - set(a)):
            return False
        return all(known_equal(v, b[k], mask.get(k, False)) for k, v in a.items())
    if isinstance(a, list):
        if not isinstance(b, list) or len(a) != len(b):
            return False
        if unordered:
            return sorted(a) == sorted(b)
        mask = unknown if isinstance(unknown, list) else []
        return all(known_equal(x, y, mask[i] if i < len(mask) else False) for i, (x, y) in enumerate(zip(a, b)))
    if isinstance(a, (int, float)) and not isinstance(a, bool):
        return isinstance(b, (int, float)) and not isinstance(b, bool) and a == b
    return type(a) is type(b) and a == b
require(new.get('complete') is True and new.get('errored') is not True, 'INCOMPLETE_PLAN')
require(old.get('variables') == new.get('variables'), 'INPUT_DELTA')
require(old.get('configuration') == new.get('configuration'), 'CONFIGURATION_DELTA')
o = {r['address']: r for r in old['resource_changes'] if r['mode'] == 'managed'}
n = {r['address']: r for r in new['resource_changes'] if r['mode'] == 'managed'}
cluster = 'rhcs_cluster_rosa_classic.cluster[0]'
require(len(o) == 16 and set(o) == set(n) and cluster in n, 'RESOURCE_SET_DELTA')
for address, r in n.items():
    c, original = r['change'], o[address]['change']
    require(original['actions'] == ['create'], 'ORIGINAL_SCOPE_UNEXPECTED')
    require(c['actions'] == (['create'] if address == cluster else ['no-op']), 'EXISTING_RESOURCE_CHANGE')
    if address != cluster:
        require(c.get('before') == c['after'], 'NOOP_RESOURCE_VALUE_DELTA')
    a, b, mask = original['after'], c['after'], original.get('after_unknown', {})
    if address == 'aws_iam_openid_connect_provider.cluster':
        # client_id_list is an AWS Terraform set, so its display order is immaterial.
        require(set(a['client_id_list']) == set(b['client_id_list']), 'OIDC_CLIENT_IDS_CHANGED')
        a = {k:v for k,v in a.items() if k != 'client_id_list'}
        b = {k:v for k,v in b.items() if k != 'client_id_list'}
        mask = {k:v for k,v in mask.items() if k != 'client_id_list'}
    require(known_equal(a, b, mask), 'APPROVED_KNOWN_VALUE_DELTA')
contract = n['terraform_data.input_contract']['change']['after']
require(contract.get('id') == sys.argv[3] and contract.get('output') == contract.get('input'), 'CONTRACT_STATE_MISMATCH')
require(n['rhcs_rosa_oidc_config.cluster']['change']['after'].get('id') == sys.argv[4], 'CREATED_OIDC_CHANGED')
require(not new.get('resource_drift'), 'UNEXPECTED_MANAGED_RESOURCE_DRIFT')
checks = new.get('checks', [])
require(len(checks) == 17 and all(c.get('status') == 'pass' for c in checks), 'PLAN_CHECK_NOT_PASS')
require(set(new.get('output_changes', {})) <= set(old.get('output_changes', {})), 'OUTPUT_SCOPE_DELTA')
print(json.dumps({'status':'PASS_CLUSTER_ONLY_PLAN', 'create':1, 'update':0, 'delete':0, 'retained_resources':15, 'checks_pass':17}))
PY

plan_sha=$(sha256sum "$result_dir/remaining.tfplan" | cut -d ' ' -f1)
(set -o noclobber; printf '{"plan_sha256":"%s","result_directory":"%s","started_at_utc":"%s"}\n' \
  "$plan_sha" "$result_dir" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$marker")
phase=CLUSTER_ONLY_PLAN_APPLY
printf 'ROSA_CLUSTER_ONLY_APPLY_STARTED_KST: %s\n' "$(TZ=Asia/Seoul date '+%F %T KST')"
if terraform -chdir="$source_dir/terraform/rosa" apply -input=false -no-color -lock-timeout=60s "$result_dir/remaining.tfplan" 2>&1 | tee "$result_dir/apply.log"; then
  phase=PROTECTED_HANDOFF_EXPORT
  terraform -chdir="$source_dir/terraform/rosa" output -json platform_handoff >"$result_dir/platform-handoff.json" 2>"$result_dir/output.log"
  printf '{"status":"CLUSTER_ONLY_APPLY_EXIT_0","node_ready_management_access":"NOT_YET_VERIFIED"}\n' | tee "$result_dir/receipt.json"
  printf 'ROSA_APPLY_COMPLETED_KST: %s\n' "$(TZ=Asia/Seoul date '+%F %T KST')"
  printf 'Protected result directory: %s\n' "$result_dir"
else
  stop APPLY_OR_LOG_WRITE_FAILED_PARTIAL_STATE_POSSIBLE
fi
