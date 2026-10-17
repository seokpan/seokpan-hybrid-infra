#!/usr/bin/env bash
set +x
set -euo pipefail
umask 077
[[ $# == 5 ]] || { echo 'Usage: ROOT RECONSTRUCTION_RECEIPT CONTRACT AGE_KEY SESSION_HELPER'; exit 2; }
root=$1; accepted=$2; contract=$3; key=$4; helper=$5
scripts=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
work=$(mktemp -d "$root/monitoring-permission-batch-XXXXXXXX")
python3 -I - "$accepted" "$helper" "$work" <<'PY'
import hashlib,json,os,stat,sys
from pathlib import Path
accepted,helper,out=map(Path,sys.argv[1:])
for p in (accepted,helper):
    if not p.is_file() or p.is_symlink():raise SystemExit('STOP_REGULAR_INPUT')
if accepted.stat().st_uid!=os.getuid() or accepted.stat().st_mode&0o077:raise SystemExit('STOP_PRIVATE_RECEIPT')
raw=helper.read_bytes()
if hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()!='608a765e2ba40b472b2cb431223b48c396c00866':raise SystemExit('STOP_AUTH_HELPER')
r=json.loads(accepted.read_text());c=json.load(open(r['lifecycle_config']))
if r['status']!='PASS_DENIAL_CLASSIFIED_AND_ENCRYPTED_HELD_RECIPE_PREPARED':raise SystemExit('STOP_ACCEPTED_RECIPE')
v=Path(r['terraform_scope_inputs']);s=json.loads(v.read_text());gap_sha=s['rosa_monitoring_preservation_gap_sha256']
m=json.load(open(c['delete']['manifest']));source_root=Path(r['independent_handoff_file']).parent
handoff=json.load(open(r['independent_handoff_file']))
gap=[x for x in handoff['archives'] if x['kind']=='restoration-gaps']
if len(gap)!=1 or gap[0]['sha256']!=gap_sha:raise SystemExit('STOP_GAP_HANDOFF')
if (Path(gap[0]['path']).parent/'monitoring-snapshot-attempt.NO_AUTOMATIC_RETRY.json').exists():raise SystemExit('STOP_EXISTING_SNAPSHOT_ATTEMPT_USE_STATUS_OR_RECONCILE')
repository=Path(c['create']['restore_driver']).parent.parent
rows=[str(repository),str(c['delete']['manifest']),gap[0]['path'],gap_sha,c['restoration']['sops_binary'],
      str(Path(gap[0]['path']).parent/'emergency-kubeconfig')]
if any('\n' in x or '\r' in x for x in rows):raise SystemExit('STOP_SINGLE_LINE_PATHS')
(out/'paths.private.txt').write_text('\n'.join(rows)+'\n')
print('PASS_ACCEPTED_135_OBJECT_RECIPE_AND_NO_SNAPSHOT_ATTEMPT_REUSED')
PY
mapfile -t paths < "$work/paths.private.txt"
[[ ${#paths[@]} == 6 ]] || { echo STOP_PRIVATE_PATHS; exit 1; }
repository=${paths[0]}; manifest=${paths[1]}; gap=${paths[2]}; gap_sha=${paths[3]}; sops=${paths[4]}; kube=${paths[5]}
trap 'unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN task_mfa' EXIT
unset AWS_PROFILE AWS_DEFAULT_PROFILE AWS_ROLE_ARN AWS_WEB_IDENTITY_TOKEN_FILE TF_SESSION_SECONDS
export AWS_REGION=ap-northeast-2 AWS_DEFAULT_REGION=ap-northeast-2 AWS_PAGER='' AWS_MAX_ATTEMPTS=1
for tool in aws terraform jq python3; do command -v "$tool" >/dev/null; done
read -r -s -p 'AWS MFA for exact Bootstrap snapshot policy only (6 digits, hidden): ' task_mfa </dev/tty
printf '\n'
[[ "$task_mfa" =~ ^[0-9]{6}$ ]] || { echo STOP_BOOTSTRAP_MFA; exit 1; }
source "$helper" bootstrap <<<"$task_mfa" >"$work/bootstrap-auth.private.log" 2>&1
unset task_mfa
python3 -I "$scripts/rosa-monitoring-permission.py" \
  --reconstruction-receipt "$accepted" --output-parent "$root" --apply-exact-policy

# The exact policy has been applied and read back under Bootstrap State. Switch
# back to the existing ROSA role; never run volume writes under Bootstrap.
read -r -s -p 'AWS MFA for ROSA monitoring snapshots only (6 digits, hidden): ' task_mfa </dev/tty
printf '\n'
[[ "$task_mfa" =~ ^[0-9]{6}$ ]] || { echo STOP_ROSA_MFA_POLICY_ALREADY_PRESERVED; exit 1; }
source "$helper" rosa <<<"$task_mfa" >"$work/rosa-auth.private.log" 2>&1
unset task_mfa
extra=(--sops "$sops" --kubeconfig "$kube")
if [[ -n ${ROSA_PRESERVATION_AGE_KEYGEN:-} ]]; then extra+=(--age-keygen "$ROSA_PRESERVATION_AGE_KEYGEN"); fi
printf '%s\n' 'START_FIRST_REAL_MONITORING_SNAPSHOT_CAPTURE_AFTER_POLICY_READBACK'
rc=0
python3 -I "$repository/scripts/rosa-monitoring-volume-backup.py" capture \
  --gap-archive "$gap" --gap-sha256 "$gap_sha" --key-file "$key" \
  --contract "$contract" --delete-manifest "$manifest" --output-parent "$root" \
  "${extra[@]}" >"$work/snapshot.private.log" || rc=$?

python3 -I - "$accepted" "$work/snapshot.private.log" <<'PY'
import json,os,sys
from pathlib import Path
accepted=json.load(open(sys.argv[1]));r=json.loads(Path(sys.argv[2]).read_text())
r.pop('restoration_review',None)
r['restoration_classification_source']='ACCEPTED_135_OBJECT_RECIPE_NO_RECOMPILATION'
r['classified_objects']=accepted['classified_objects'];r['unclassified_objects']=accepted['unclassified_objects']
if r['status']=='PASS_FOUR_ENCRYPTED_EBS_SNAPSHOTS_AND_RESTORE_MAP':
    p=Path(accepted['lifecycle_config']);c=json.loads(p.read_text());old=c['restoration'].get('snapshot_map')
    if old not in (None,r['archive']):raise SystemExit('STOP_EXISTING_SNAPSHOT_MAP_NO_OVERWRITE')
    c['restoration']['snapshot_map']=r['archive'];c['restoration']['snapshot_map_sha256']=r['archive_sha256']
    temporary=p.with_suffix('.new.json');temporary.write_text(json.dumps(c,sort_keys=True));temporary.chmod(0o600);os.replace(temporary,p)
    r['snapshot_map_connected_to_lifecycle_config']=True
    r['independent_map_copy']='NOT_RUN';r['new_volume_restore_permissions']='NOT_ACCEPTED'
print(json.dumps(r,indent=2))
PY
printf 'SNAPSHOT_EXIT: %s\nPrivate batch directory: %s\n' "$rc" "$work"
exit "$rc"
