#!/usr/bin/env bash
set +x
set -euo pipefail
umask 077
[[ $# == 7 ]] || { echo 'Usage: rosa-monitoring-preservation.sh ROOT DELETE_MANIFEST GAP_ARCHIVE GAP_SHA CONTRACT AGE_KEY SESSION_HELPER'; exit 2; }
root=$1; manifest=$2; gap=$3; gap_sha=$4; contract=$5; key=$6; helper=$7
scripts=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
[[ -d "$root" && ! -L "$root" ]] || { echo STOP_PRIVATE_ROOT; exit 1; }
# Publish the accepted gap ciphertext first: an AWS session/permission failure must
# not prevent independent preservation of the completed missing-input supplement.
python3 -I - "$root" "$gap" "$gap_sha" "$helper" <<'PY'
import hashlib,json,os,stat,sys,tempfile
from pathlib import Path
root,gap,expected,helper=Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3],Path(sys.argv[4])
for path in (gap,helper):
    if path.is_symlink() or not path.is_file(): raise SystemExit('STOP_REGULAR_INPUT')
if gap.stat().st_uid!=os.getuid() or stat.S_IMODE(gap.stat().st_mode)&0o077: raise SystemExit('STOP_PRIVATE_GAP')
if hashlib.sha256(gap.read_bytes()).hexdigest()!=expected: raise SystemExit('STOP_GAP_HASH')
raw=helper.read_bytes()
if hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()!='608a765e2ba40b472b2cb431223b48c396c00866': raise SystemExit('STOP_AUTH_HELPER_HASH')
target=root/'rosa-preservation-handoff-latest.json'
if target.is_symlink(): raise SystemExit('STOP_HANDOFF_SYMLINK')
fd,path=tempfile.mkstemp(prefix='.preservation-handoff-',dir=root)
with os.fdopen(fd,'w') as f:
    json.dump({'schema':2,'archives':[{'path':str(gap.resolve()),'sha256':expected,'kind':'restoration-gaps'}],
        'snapshots_status':'NOT_RUN','deletion_ready':False},f)
os.replace(path,target)
print('PASS_ACCEPTED_GAP_HANDOFF_READY_NO_REDECRYPT')
PY
auth_log=$(mktemp "$root/snapshot-auth-private-XXXXXXXX.log")
trap 'unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN task_mfa' EXIT
unset AWS_PROFILE AWS_DEFAULT_PROFILE AWS_ROLE_ARN AWS_WEB_IDENTITY_TOKEN_FILE TF_SESSION_SECONDS
export AWS_REGION=ap-northeast-2 AWS_DEFAULT_REGION=ap-northeast-2 AWS_PAGER='' AWS_MAX_ATTEMPTS=1
read -r -s -p 'AWS MFA for ROSA monitoring backup only (6 digits, hidden): ' task_mfa </dev/tty
printf '\n'
[[ "$task_mfa" =~ ^[0-9]{6}$ ]] || { echo STOP_MFA_FORMAT_GAP_HANDOFF_PRESERVED; exit 1; }
if ! source "$helper" rosa <<<"$task_mfa" >"$auth_log" 2>&1; then
  echo STOP_ROSA_BACKUP_SESSION_PRIVATE_LOG_GAP_HANDOFF_PRESERVED
  exit 1
fi
unset task_mfa
extra=()
if [[ -n ${ROSA_PRESERVATION_SOPS:-} ]]; then
  printf '%s  %s\n' 'e5bec3346a873ae91d871550f3e698c1aad962aff462a080e40f25fde17fef6b' "$ROSA_PRESERVATION_SOPS" | sha256sum --check
  extra+=(--sops "$ROSA_PRESERVATION_SOPS")
fi
if [[ -n ${ROSA_PRESERVATION_AGE_KEYGEN:-} ]]; then extra+=(--age-keygen "$ROSA_PRESERVATION_AGE_KEYGEN"); fi
if [[ -n ${ROSA_PRESERVATION_KUBECONFIG:-} ]]; then extra+=(--kubeconfig "$ROSA_PRESERVATION_KUBECONFIG"); fi
python3 -I "$scripts/rosa-monitoring-volume-backup.py" capture \
  --gap-archive "$gap" --gap-sha256 "$gap_sha" --key-file "$key" \
  --contract "$contract" --delete-manifest "$manifest" --output-parent "$root" \
  "${extra[@]}"
