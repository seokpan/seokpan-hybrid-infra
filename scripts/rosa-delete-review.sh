#!/usr/bin/env bash
set +x
set -euo pipefail
umask 077
[[ $# == 3 ]] || { echo 'Usage: ROOT CHECKPOINT_CONTRACT SESSION_HELPER'; exit 2; }
root=$1; contract=$2; helper=$3
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
[[ "$(git hash-object "$helper")" == 608a765e2ba40b472b2cb431223b48c396c00866 ]] || { echo STOP_SESSION_HELPER_HASH; exit 1; }
work=$(mktemp -d "$root/delete-review-auth-XXXXXXXX")
trap 'unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN task_mfa' EXIT
unset AWS_PROFILE AWS_DEFAULT_PROFILE AWS_ROLE_ARN AWS_WEB_IDENTITY_TOKEN_FILE TF_SESSION_SECONDS
export AWS_REGION=ap-northeast-2 AWS_DEFAULT_REGION=ap-northeast-2 AWS_PAGER='' AWS_MAX_ATTEMPTS=1
args=(--root "$root" --contract "$contract")
if [[ -x "$HOME/.local/share/rosa-checkpoint-m4iaot5r/age-keygen" ]]; then
  args+=(--age-keygen "$HOME/.local/share/rosa-checkpoint-m4iaot5r/age-keygen")
fi
read -r -s -p 'AWS MFA for latest Bootstrap State preservation only (6 digits, hidden): ' task_mfa </dev/tty
printf '\n'
[[ "$task_mfa" =~ ^[0-9]{6}$ ]] || { echo STOP_BOOTSTRAP_MFA; exit 1; }
source "$helper" bootstrap <<<"$task_mfa" >"$work/bootstrap.private.log" 2>&1
unset task_mfa
python3 -I "$here/prepare-rosa-delete-review.py" bootstrap "${args[@]}"
read -r -s -p 'AWS MFA for saved ROSA State binding and encrypted review only (6 digits, hidden): ' task_mfa </dev/tty
printf '\n'
[[ "$task_mfa" =~ ^[0-9]{6}$ ]] || { echo STOP_ROSA_MFA_BOOTSTRAP_CAPTURE_PRESERVED; exit 1; }
source "$helper" rosa <<<"$task_mfa" >"$work/rosa.private.log" 2>&1
unset task_mfa
python3 -I "$here/prepare-rosa-delete-review.py" finish "${args[@]}"
printf 'Private review authentication directory: %s\n' "$work"
