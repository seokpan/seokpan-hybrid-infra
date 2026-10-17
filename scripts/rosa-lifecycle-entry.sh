#!/usr/bin/env bash
set +x
set -euo pipefail
umask 077
[[ $# == 1 || $# == 3 ]] || { echo 'Usage: rosa-{delete,create,restore}.sh [--config PRIVATE_JSON]'; exit 2; }
action=$1
[[ $action == delete || $action == create || $action == restore ]] || exit 2
config=${ROSA_LIFECYCLE_CONFIG:-$HOME/.config/seokpan/rosa-lifecycle.json}
if [[ $# == 3 ]]; then [[ $2 == --config ]] || exit 2; config=$3; fi
scripts=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repository=$(cd -- "$scripts/.." && pwd)
work=$(mktemp -d "${TMPDIR:-/tmp}/rosa-lifecycle-entry-XXXXXXXX")
trap 'unset RHCS_TOKEN AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN task_mfa' EXIT
if ! python3 "$scripts/rosa-lifecycle-entry.py" preflight --action "$action" --config "$config" --repository "$repository" >"$work/private-paths.json"; then
  cat "$work/private-paths.json"
  exit 1
fi
mapfile -t paths < <(python3 -I -c 'import json,sys; x=json.load(open(sys.argv[1]));print(x["rosa_cli"]);print(x["tf_session_helper"])' "$work/private-paths.json")
[[ ${#paths[@]} == 2 ]] || { echo STOP_ENTRY_PATHS; exit 1; }
rosa_cli=${paths[0]}
helper=${paths[1]}
for tool in aws terraform jq git python3; do command -v "$tool" >/dev/null; done
local_name=''
while IFS= read -r local_name; do
  case "$local_name" in TF_CLI_ARGS*|TF_VAR_*|TF_LOG*|RHCS_*) unset "$local_name" ;; esac
done < <(compgen -e)
unset AWS_PROFILE AWS_DEFAULT_PROFILE AWS_ROLE_ARN AWS_WEB_IDENTITY_TOKEN_FILE TF_CLI_CONFIG_FILE TF_SESSION_SECONDS
export AWS_REGION=ap-northeast-2 AWS_DEFAULT_REGION=ap-northeast-2 AWS_PAGER='' AWS_MAX_ATTEMPTS=1
if ! RHCS_TOKEN=$("$rosa_cli" token --generate 2>"$work/rhcs-private.log"); then
  "$rosa_cli" login --use-device-code </dev/tty >/dev/tty 2>/dev/tty
  RHCS_TOKEN=$("$rosa_cli" token --generate 2>"$work/rhcs-private.log")
fi
[[ -n "$RHCS_TOKEN" && "$RHCS_TOKEN" != *[[:space:]]* ]] || { echo STOP_RHCS_TOKEN; exit 1; }
export RHCS_TOKEN
read -r -s -p 'AWS MFA for ROSA lifecycle only (6 digits, hidden): ' task_mfa </dev/tty
printf '\n'
[[ "$task_mfa" =~ ^[0-9]{6}$ ]] || { echo STOP_MFA_FORMAT; exit 1; }
source "$helper" rosa <<<"$task_mfa" >"$work/aws-auth-private.log" 2>&1
unset task_mfa
python3 "$scripts/rosa-lifecycle-entry.py" run --action "$action" --config "$config" --repository "$repository"
