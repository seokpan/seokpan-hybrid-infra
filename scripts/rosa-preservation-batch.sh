#!/usr/bin/env bash
# Config backup, optional scoped destroy Plan, encrypted Plan supplement. No Apply.
set +x
set -euo pipefail
umask 077
[[ $# == 9 || ( $# == 11 && ${10} == --resume-plan-from ) ]] || { echo 'Usage: batch ROOT CONTRACT PREVIOUS_STOP KUBECONFIG KEY TF_DATA VAR_FILE CLUSTER_ID ROSA_CLI [--resume-plan-from PREVIOUS_BATCH]'; exit 2; }
project_root=$1 contract=$2 previous=$3 kubeconfig=$4 key_file=$5 tf_data=$6 var_file=$7 cluster_id=$8 rosa_cli=$9
resume_batch=${11:-}
tool_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
work=$(mktemp -d "$project_root/preservation-batch-XXXXXXXX")
trap 'unset RHCS_TOKEN AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN task_mfa' EXIT

backup_rc=0
if [[ -n "$resume_batch" ]]; then
  # RESUME_ARCHIVE_METADATA_ONLY: no decrypt, cluster read or backup repetition.
  python3 -I - "$resume_batch" "$key_file" "$work" <<'PY'
import hashlib,json,os,stat,sys
from pathlib import Path
def need(ok,code):
 if not ok: raise SystemExit(code)
def private(p,maximum):
 s=p.lstat()
 need(stat.S_ISREG(s.st_mode) and s.st_size <= maximum and (os.name!='posix' or (s.st_uid==os.getuid() and not s.st_mode & 0o077)),'STOP_PRIVATE_RESUME_INPUT')
 return p.read_bytes()
old,key,out=map(Path,sys.argv[1:])
need(old.is_dir() and not old.is_symlink(),'STOP_PREVIOUS_BATCH_DIRECTORY')
h=json.loads(private(old/'handoff.json',262144))
need(h.get('schema')==1 and h.get('deletion_ready') is False and h.get('plan_exit_code')==1 and h.get('plan_phase')=='EXISTING_ROSA_TOKEN_REFRESH','STOP_RESUME_ONLY_PREPLAN_TOKEN_FAILURE')
need(not list(old.glob('rosa-lifecycle-*')) and not (old/'plan-supplement.log').exists(),'STOP_PLAN_ALREADY_ATTEMPTED_RECONCILE')
need(len(h.get('archives',[]))==1,'STOP_SINGLE_EXISTING_CONFIG_ARCHIVE_REQUIRED')
row=json.JSONDecoder().raw_decode(private(old/'config-backup.log',1048576).decode().lstrip())[0]
a=h['archives'][0];archive=Path(a['path'])
need(a.get('kind')=='config-backup' and row.get('archive')==str(archive) and row.get('archive_sha256')==a.get('sha256') and row.get('local_decrypt_verified') is True and row.get('archive_written') is True,'STOP_ACCEPTED_BACKUP_RECEIPT_REQUIRED')
need(hashlib.sha256(private(archive,288*1024*1024)).hexdigest()==a['sha256'],'STOP_EXISTING_ARCHIVE_HASH')
need(str(key)==h.get('key_source') and hashlib.sha256(private(key,65536)).hexdigest()==h.get('key_sha256'),'STOP_EXISTING_KEY_BINDING')
(out/'config-backup.log').write_text(json.dumps(row)+'\n')
print('REUSED_ACCEPTED_CONFIG_ARCHIVE_NO_BACKUP_OR_DECRYPT_REPEATED')
PY
else
python3 "$tool_dir/prepare-rosa-checkpoint.py" backup \
  --contract "$contract" --resume-from "$previous" --kubeconfig "$kubeconfig" \
  --key-file "$key_file" --output-parent "$HOME/.local/share" \
  | tee "$work/config-backup.log" || backup_rc=$?
fi
if [[ $backup_rc != 0 ]]; then
  printf 'STOP_BATCH_BACKUP: %s\nPrivate batch directory: %s\n' "$backup_rc" "$work"
  exit "$backup_rc"
fi

prepare_plan() {
  plan_phase=LOCAL_RUNTIME_AND_INPUTS
  for tool in aws terraform jq git; do command -v "$tool" >/dev/null || return 1; done
  [[ -x "$rosa_cli" ]] || return 1
  local helper="$project_root/source/scripts/tf-session.sh"
  [[ $(git hash-object "$helper") == 608a765e2ba40b472b2cb431223b48c396c00866 ]] || return 1
  python3 -I - "$project_root/protected/inputs.tfvars.json" "$var_file" <<'PY' || return 1
import json,stat,sys
from pathlib import Path
values=[]
for name in sys.argv[1:]:
 p=Path(name);s=p.lstat()
 if not stat.S_ISREG(s.st_mode) or s.st_mode & 0o077: raise SystemExit('STOP_PRIVATE_INPUT_REQUIRED')
 values.append(json.loads(p.read_text()))
base,bound=values
if base.get('cluster_name') != bound.get('cluster_name'): raise SystemExit('STOP_CLUSTER_INPUT_MISMATCH')
for name in ('account_id','vpc_id'):
 if base.get('foundation',{}).get(name) != bound.get('foundation',{}).get(name): raise SystemExit('STOP_FOUNDATION_INPUT_MISMATCH')
if bound.get('cluster_enabled') is not True or not isinstance(bound.get('worker_sg_binding'),dict): raise SystemExit('STOP_CURRENT_BOUND_INPUT_REQUIRED')
print('PASS_CURRENT_BOUND_INPUT_SELECTION_NOT_PLAN')
PY
  local name
  while IFS= read -r name; do
    case "$name" in TF_CLI_ARGS*|TF_VAR_*|TF_LOG*|RHCS_*) unset "$name" ;; esac
  done < <(compgen -e)
  unset AWS_PROFILE AWS_DEFAULT_PROFILE AWS_ROLE_ARN AWS_WEB_IDENTITY_TOKEN_FILE TF_CLI_CONFIG_FILE TF_SESSION_SECONDS
  export AWS_REGION=ap-northeast-2 AWS_DEFAULT_REGION=ap-northeast-2 AWS_PAGER='' AWS_MAX_ATTEMPTS=1
  if [[ -n "$resume_batch" ]]; then
    plan_phase=EXPLICIT_ROSA_DEVICE_LOGIN
    [[ -r /dev/tty ]] || return 1
    "$rosa_cli" login --use-device-code </dev/tty >/dev/tty 2>/dev/tty || return 1
  fi
  plan_phase=ROSA_ACCESS_TOKEN_PREPARATION
  RHCS_TOKEN=$("$rosa_cli" token --generate 2>"$work/rhcs-private.log") || return 1
  [[ -n "$RHCS_TOKEN" && "$RHCS_TOKEN" != *[[:space:]]* ]] || return 1
  export RHCS_TOKEN
  [[ -r /dev/tty ]] || return 1
  plan_phase=AWS_MFA_INPUT
  read -r -s -p 'AWS MFA for scoped ROSA Plan only (6 digits, hidden): ' task_mfa </dev/tty || return 1
  printf '\n'
  [[ "$task_mfa" =~ ^[0-9]{6}$ ]] || { unset task_mfa; return 1; }
  plan_phase=SCOPED_AWS_SESSION
  source "$helper" rosa <<<"$task_mfa" >"$work/aws-auth-private.log" 2>&1 || { unset task_mfa; return 1; }
  unset task_mfa
  plan_phase=ROSA_ONLY_SAVED_DESTROY_PLAN
  python3 "$tool_dir/rosa-lifecycle-plan.py" plan --action delete \
    --tf-root "$project_root/source/terraform/rosa" --tf-data "$tf_data" \
    --var-file "$var_file" --cluster-id "$cluster_id" --output-parent "$work" \
    | tee "$work/plan-result.log" || return 1
  plan_phase=ROSA_ONLY_PLAN_PREPARED_NOT_APPLIED
}
plan_rc=0
plan_phase=NOT_STARTED
prepare_plan || plan_rc=$?

seal_rc=2
seal_status=NOT_RUN_NO_PLAN_RECEIPT
mapfile -t plan_dirs < <(find "$work" -mindepth 1 -maxdepth 1 -type d -name 'rosa-lifecycle-*')
if [[ ${#plan_dirs[@]} == 1 && -f "${plan_dirs[0]}/receipt.json" ]]; then
  seal_rc=0
  seal_status=ATTEMPTED
  python3 "$tool_dir/prepare-rosa-checkpoint.py" seal --contract "$contract" \
    --seal-dir "${plan_dirs[0]}" --key-file "$key_file" --output-parent "$HOME/.local/share" \
    | tee "$work/plan-supplement.log" || seal_rc=$?
  if [[ $seal_rc == 0 ]]; then seal_status=LOCAL_ROUNDTRIP_PASS; else seal_status=STOP; fi
fi

python3 -I - "$work" "$key_file" "$plan_rc" "$seal_rc" "$plan_phase" "$seal_status" <<'PY'
import hashlib,json,stat,sys
from pathlib import Path
work=Path(sys.argv[1]);key=Path(sys.argv[2])
archives=[]
for name in ('config-backup.log','plan-supplement.log'):
 p=work/name
 if not p.is_file(): continue
 row=json.JSONDecoder().raw_decode(p.read_text().lstrip())[0]
 if not row.get('local_decrypt_verified') or not row.get('archive_written'): continue
 a=Path(row['archive']);s=a.lstat()
 if not stat.S_ISREG(s.st_mode) or hashlib.sha256(a.read_bytes()).hexdigest()!=row['archive_sha256']: raise SystemExit('STOP_ARCHIVE_HANDOFF_HASH')
 archives.append({'path':str(a),'sha256':row['archive_sha256'],'kind':name.split('.')[0]})
if not archives: raise SystemExit('STOP_NO_VERIFIED_ARCHIVE_HANDOFF')
s=key.lstat()
if not stat.S_ISREG(s.st_mode) or s.st_mode & 0o077: raise SystemExit('STOP_PRIVATE_KEY_HANDOFF_METADATA')
result={'schema':1,'archives':archives,'key_source':str(key),'key_sha256':hashlib.sha256(key.read_bytes()).hexdigest(),
 'plan_exit_code':int(sys.argv[3]),'supplement_exit_code':int(sys.argv[4]),'plan_phase':sys.argv[5],
 'supplement_status':sys.argv[6],'deletion_ready':False}
(work/'handoff.json').write_text(json.dumps(result),encoding='utf-8')
print('HANDOFF_JSON_BEGIN');print(json.dumps(result,separators=(',',':')));print('HANDOFF_JSON_END')
print('PLAN_EXIT: '+sys.argv[3]+'; PLAN_PHASE: '+sys.argv[5]+'; SUPPLEMENT_EXIT: '+sys.argv[4])
print('Private batch directory: '+str(work))
PY
