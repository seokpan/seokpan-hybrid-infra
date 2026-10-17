#!/usr/bin/env bash
set +x
set -euo pipefail
umask 077
[[ $# == 6 ]] || { echo 'Usage: ROOT RECONSTRUCTION_RECEIPT SCOPE_INPUTS SNAPSHOT_PERMISSION_RUN SESSION_HELPER PARTIAL_RUN'; exit 2; }
root=$1; accepted=$2; scope=$3; previous=$4; helper=$5; partial=$6
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
[[ "$(git hash-object "$helper")" == 608a765e2ba40b472b2cb431223b48c396c00866 ]] || { echo STOP_SESSION_HELPER_HASH; exit 1; }
work=$(mktemp -d "$root/restore-reconcile-auth-XXXXXXXX")
trap 'unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN task_mfa' EXIT
unset AWS_PROFILE AWS_DEFAULT_PROFILE AWS_ROLE_ARN AWS_WEB_IDENTITY_TOKEN_FILE TF_SESSION_SECONDS
export AWS_REGION=ap-northeast-2 AWS_DEFAULT_REGION=ap-northeast-2 AWS_PAGER='' AWS_MAX_ATTEMPTS=1
args=(--reconstruction-receipt "$accepted" --scope-inputs "$scope" --snapshot-permission-run "$previous"
      --restore-hcl "$here/rosa_monitoring_restore.tf" --output-parent "$root")
read -r -s -p 'AWS MFA for partial IAM reconciliation and only two missing creates (6 digits, hidden): ' task_mfa </dev/tty
printf '\n'
[[ "$task_mfa" =~ ^[0-9]{6}$ ]] || { echo STOP_BOOTSTRAP_MFA; exit 1; }
source "$helper" bootstrap <<<"$task_mfa" >"$work/bootstrap.private.log" 2>&1
unset task_mfa
python3 -I "$here/rosa-monitoring-restore-reconcile.py" "${args[@]}" --resume-from "$partial" --apply-remainder

read -r -s -p 'AWS MFA for restored-volume permission checks only (6 digits, hidden): ' task_mfa </dev/tty
printf '\n'
[[ "$task_mfa" =~ ^[0-9]{6}$ ]] || { echo STOP_ROSA_MFA_IAM_RESULT_PRESERVED; exit 1; }
source "$helper" rosa <<<"$task_mfa" >"$work/rosa.private.log" 2>&1
unset task_mfa
python3 -I "$here/rosa-monitoring-restore-permission.py" check "${args[@]}"
printf 'Private authentication batch: %s\n' "$work"
