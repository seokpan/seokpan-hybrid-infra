#!/usr/bin/env bash
# Explicit user-approved saved ROSA Plan only; no automatic retry or residual deletion.
set +x
set -euo pipefail
umask 077
[[ $(id -un) == jth ]] || { echo STOP_USE_CONTROLLER_JTH; exit 1; }
scripts=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repository=$(cd -- "$scripts/.." && pwd)
root="$HOME/.local/share/seokpan-rosa-plan-inputs-5psj2l62"
work=$(mktemp -d "$root/approved-delete-XXXXXXXX")
trap 'unset RHCS_TOKEN AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN task_mfa' EXIT
python3 -I "$scripts/rosa-approved-delete.py" prepare "$@" --out "$work"
mapfile -t paths < <(python3 -I -c 'import json,sys;x=json.load(open(sys.argv[1]));print(x["rosa_cli"]);print(x["tf_session_helper"]);print(x["config"])' "$work/paths.json")
[[ ${#paths[@]} == 3 ]] || { echo STOP_AUTH_PATHS; exit 1; }
rosa_cli=${paths[0]}
helper=${paths[1]}
config=${paths[2]}
for tool in aws terraform jq git python3; do command -v "$tool" >/dev/null; done
while IFS= read -r local_name; do
  case "$local_name" in TF_CLI_ARGS*|TF_VAR_*|TF_LOG*|RHCS_*) unset "$local_name" ;; esac
done < <(compgen -e)
unset AWS_PROFILE AWS_DEFAULT_PROFILE AWS_ROLE_ARN AWS_WEB_IDENTITY_TOKEN_FILE TF_CLI_CONFIG_FILE TF_SESSION_SECONDS
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
export AWS_REGION=ap-northeast-2 AWS_DEFAULT_REGION=ap-northeast-2 AWS_PAGER='' AWS_MAX_ATTEMPTS=1 AWS_RETRY_MODE=standard
if ! RHCS_TOKEN=$("$rosa_cli" token --generate 2>"$work/rhcs-private.log"); then
  "$rosa_cli" login --use-device-code </dev/tty >/dev/tty 2>/dev/tty
  RHCS_TOKEN=$("$rosa_cli" token --generate 2>"$work/rhcs-private.log")
fi
[[ -n "$RHCS_TOKEN" && "$RHCS_TOKEN" != *[[:space:]]* ]] || { echo STOP_RHCS_TOKEN; exit 1; }
export RHCS_TOKEN
read -r -s -p 'AWS MFA for approved ROSA deletion and read-only residual inventory (6 digits, hidden): ' task_mfa </dev/tty
printf '\n'
[[ "$task_mfa" =~ ^[0-9]{6}$ ]] || { echo STOP_MFA_FORMAT; exit 1; }
source "$helper" rosa <<<"$task_mfa" >"$work/auth.private.log" 2>&1
unset task_mfa
set +e
python3 -I "$scripts/rosa-lifecycle-entry.py" run --action delete --config "$config" --repository "$repository" | tee "$work/delete-result.log"
delete_rc=${PIPESTATUS[0]}
set -e
# The role session is reused; these calls only inspect ownership and retained snapshots.
python3 -I "$scripts/rosa-approved-delete.py" inventory --config "$config" --out "$work" --delete-exit-code "$delete_rc"
printf 'DELETE_ENTRY_EXIT: %s\nPrivate approval/inventory directory: %s\n' "$delete_rc" "$work"
exit "$delete_rc"
