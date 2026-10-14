#!/usr/bin/env bash
# Human-run partial-Apply recovery for the reviewed ROSA source snapshot.
# Args: protected-input-root prior-result-dir input-sha256 plan-sha256 existing-contract-id
# Preserve State and prior attempts. No init, destroy, import, or state edits.
set +x
set -Eeuo pipefail
umask 077
[[ "$#" == 5 ]] || { echo "Usage: $0 INPUT_ROOT PREVIOUS_RESULT INPUT_SHA PLAN_SHA CONTRACT_ID" >&2; exit 2; }
root="$1"
previous="$2"
expected_input_sha="$3"
expected_plan_sha="$4"
expected_contract_id="$5"
source_dir="$root/source"
input_file="$root/protected/inputs.tfvars.json"
marker="$previous/offline-token-recovery-apply-attempt.json"
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
result_dir=$(mktemp -d "${previous%/*}/rosa-partial-recovery-XXXXXXXX")

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
phase=REMAINING_SCOPE_COMPARISON
python3 - "$result_dir/original-plan.json" "$result_dir/remaining-plan.json" "$expected_contract_id" <<'PY'
import json, sys
old, new = [json.load(open(p)) for p in sys.argv[1:3]]
def require(ok, reason):
    if not ok:
        raise SystemExit('STOP: ' + reason + '; APPLY_NOT_RUN')
def known_equal(a, b):
    if a is None:
        return True
    if isinstance(a, dict):
        return isinstance(b, dict) and set(a) == set(b) and all(known_equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return isinstance(b, list) and len(a) == len(b) and all(known_equal(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b
require(new.get('errored') is not True and new.get('complete') is True, 'INCOMPLETE_PLAN')
require(old.get('variables') == new.get('variables'), 'INPUT_DELTA')
require(old.get('configuration') == new.get('configuration'), 'CONFIGURATION_DELTA')
o = {r['address']: r for r in old['resource_changes'] if r['mode'] == 'managed'}
n = {r['address']: r for r in new['resource_changes'] if r['mode'] == 'managed'}
require(len(o) == 16 and set(o) == set(n), 'RESOURCE_SET_DELTA')
for address, r in n.items():
    require(o[address]['change']['actions'] == ['create'], 'ORIGINAL_SCOPE_UNEXPECTED')
    expected = ['no-op'] if address == 'terraform_data.input_contract' else ['create']
    require(r['change']['actions'] == expected, 'UNEXPECTED_ACTION')
    before, after = o[address]['change']['after'], r['change']['after']
    if address == 'terraform_data.input_contract':
        require(before.get('id') is None and before.get('output') is None, 'ORIGINAL_CONTRACT_UNEXPECTED')
        require(set(after) == set(before) | {'id', 'output'}, 'CONTRACT_FIELD_DELTA')
        require(after.get('id') == sys.argv[3], 'CONTRACT_STATE_MISMATCH')
        require('input' in after and after['output'] == after['input'], 'CONTRACT_OUTPUT_NOT_INPUT')
        strip = lambda value: {k: v for k, v in value.items() if k not in ('id', 'output')}
        require(known_equal(strip(before), strip(after)), 'CONTRACT_INPUT_DELTA')
    else:
        require(known_equal(before, after), 'KNOWN_VALUE_DELTA')
require(not new.get('resource_drift'), 'UNEXPECTED_DRIFT')
checks = new.get('checks', [])
require(len(checks) == 17 and all(c.get('status') == 'pass' for c in checks), 'PLAN_CHECK_NOT_PASS')
require(set(new.get('output_changes', {})) <= set(old.get('output_changes', {})), 'OUTPUT_SCOPE_DELTA')
print(json.dumps({'status':'PASS_REMAINING_APPROVED_SCOPE', 'create':15, 'update':0, 'delete':0, 'retained_contract':1}))
PY

plan_sha=$(sha256sum "$result_dir/remaining.tfplan" | cut -d ' ' -f1)
(set -o noclobber; printf '{"plan_sha256":"%s","result_directory":"%s","started_at_utc":"%s"}\n' \
  "$plan_sha" "$result_dir" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$marker")
phase=REMAINING_PLAN_APPLY
printf 'ROSA_RECOVERY_APPLY_STARTED_KST: %s\n' "$(TZ=Asia/Seoul date '+%F %T KST')"
if terraform -chdir="$source_dir/terraform/rosa" apply -input=false -no-color -lock-timeout=60s "$result_dir/remaining.tfplan" 2>&1 | tee "$result_dir/apply.log"; then
  phase=PROTECTED_HANDOFF_EXPORT
  terraform -chdir="$source_dir/terraform/rosa" output -json platform_handoff >"$result_dir/platform-handoff.json" 2>"$result_dir/output.log"
  printf '{"status":"RECOVERY_APPLY_EXIT_0","node_ready_management_access":"NOT_YET_VERIFIED"}\n' | tee "$result_dir/receipt.json"
  printf 'ROSA_APPLY_COMPLETED_KST: %s\n' "$(TZ=Asia/Seoul date '+%F %T KST')"
  printf 'Protected result directory: %s\n' "$result_dir"
else
  stop APPLY_OR_LOG_WRITE_FAILED_PARTIAL_STATE_POSSIBLE
fi
