#!/usr/bin/env bash
# 실행 위치: Controller(ansible)의 jth 터미널
# 시험 종료와 증거 보존 이후 B 전용 삭제 Plan만 준비합니다.
(
  set +x
  set -euo pipefail
  umask 077
  [[ "$(id -un)" == jth ]] || exit 1
  taskMode=${1:---prepare-plan}
  case "$taskMode" in
    --prepare-plan|--apply-reviewed-plan) taskAuth=rosa ;;
    --inventory|--delete-reviewed-snapshots) taskAuth=personal ;;
    *) echo STOP_KNOWN_B_CLOSEOUT_MODE_REQUIRED; exit 1 ;;
  esac
  taskRoot="$HOME/.local/share/seokpan-rosa-plan-inputs-5psj2l62"
  taskRun="$taskRoot/controller-run-bundle-K2Tx7HgB"
  taskHelper="$taskRoot/source/scripts/tf-session.sh"
  [[ "$(git hash-object "$taskHelper")" == 608a765e2ba40b472b2cb431223b48c396c00866 ]] || exit 1
  if [[ "$taskMode" == --prepare-plan && -e "$taskRun/b-final-delete-plan.json" ]]; then
    echo STOP_EXISTING_DELETE_PLAN_REUSE_NO_REPEAT
    exit 1
  fi
  taskWork=$(mktemp -d "$taskRoot/b-final-delete-auth-XXXXXXXX")
  trap 'unset RHCS_TOKEN AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN taskMfa' EXIT
  taskRosa=$(python3 -I - "$taskRun/today-recreate-dispatch-20261020.json" <<'PY'
import json,sys
from pathlib import Path
pointer=json.loads(Path(sys.argv[1]).read_text())
config=json.loads(Path(pointer['config']).read_text())
print(config['auth']['rosa_cli'])
PY
)
  for taskName in $(compgen -e); do
    case "$taskName" in TF_CLI_ARGS*|TF_VAR_*|TF_LOG*|RHCS_*) unset "$taskName" ;; esac
  done
  unset AWS_PROFILE AWS_DEFAULT_PROFILE AWS_ROLE_ARN AWS_WEB_IDENTITY_TOKEN_FILE TF_CLI_CONFIG_FILE TF_SESSION_SECONDS
  export AWS_REGION=ap-northeast-2 AWS_DEFAULT_REGION=ap-northeast-2 AWS_PAGER='' AWS_MAX_ATTEMPTS=1
  if ! RHCS_TOKEN=$("$taskRosa" token --generate 2>"$taskWork/rhcs.private.log"); then
    "$taskRosa" login --use-device-code </dev/tty >/dev/tty 2>/dev/tty
    RHCS_TOKEN=$("$taskRosa" token --generate 2>"$taskWork/rhcs.private.log")
  fi
  [[ -n "$RHCS_TOKEN" && "$RHCS_TOKEN" != *[[:space:]]* ]] || exit 1
  export RHCS_TOKEN
  read -r -s -p 'AWS MFA for B ROSA delete Plan only: ' taskMfa </dev/tty
  printf '\n'
  [[ "$taskMfa" =~ ^[0-9]{6}$ ]] || exit 1
  source "$taskHelper" "$taskAuth" <<<"$taskMfa" >"$taskWork/auth.private.log" 2>&1
  unset taskMfa
  case "$taskMode" in
    --prepare-plan)
      # Plan만 준비합니다. 실제 삭제는 하지 않습니다.
      python3 -I "$taskRun/prestart-closeout/prepare_b_final_delete.py" --prepare-plan ;;
    --apply-reviewed-plan)
      # GitHub 보존과 새 Plan을 대조한 뒤 정확한 확인 문구를 요구합니다.
      python3 -I "$taskRun/prestart-closeout/apply_b_final_delete.py" --apply-reviewed-plan </dev/tty ;;
    --inventory)
      # ROSA 삭제 이후 B 소유 자원만 읽습니다.
      python3 -I "$taskRun/prestart-closeout/b_final_residuals.py" ;;
    --delete-reviewed-snapshots)
      # B Snapshot 네 개만 대조하고 별도의 확인 문구를 요구합니다.
      python3 -I "$taskRun/prestart-closeout/b_final_residuals.py" --delete-reviewed-snapshots </dev/tty ;;
  esac
)

