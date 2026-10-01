# shellcheck shell=bash
# Terraform 실행용 MFA 세션 (03 §3-F.4.2, §3-F.15)
#
# 사용법 (반드시 source로 실행):
#   source scripts/tf-session.sh personal     # 본인 IAM User + MFA 세션 (bootstrap 최초 apply 등)
#   source scripts/tf-session.sh bootstrap    # seokpan-tf-bootstrap Role (1시간)
#   source scripts/tf-session.sh foundation   # seokpan-tf-foundation Role (2시간)
#   source scripts/tf-session.sh rosa         # seokpan-tf-rosa Role (4시간)
#   source scripts/tf-session.sh clear        # 세션 환경변수 해제 → ~/.aws 기본 자격증명으로 복귀
#
# - 기본 자격증명(~/.aws)의 IAM User로 MFA 장치를 찾아 임시 자격증명을 발급하고 환경변수로 export
# - backend(S3 state)와 provider가 같은 환경변수 자격증명을 사용하므로 실행 주체가 하나로 맞춰짐
# - 임시 자격증명은 현재 셸에만 존재하며 파일로 저장하지 않음

(return 0 2>/dev/null) || {
  echo "source로 실행하세요: source scripts/tf-session.sh <personal|bootstrap|foundation|rosa|clear>" >&2
  exit 1
}

_seokpan_tf_session() {
  local mode="${1:-}" region="ap-northeast-2" duration

  case "$mode" in
    clear)
      unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN TF_SESSION_MODE TF_SESSION_EXPIRES
      echo "세션 해제 → 기본 자격증명: $(aws sts get-caller-identity --query Arn --output text 2>/dev/null)"
      return 0
      ;;
    personal)   duration="${TF_SESSION_SECONDS:-3600}" ;;
    bootstrap)  duration=3600 ;;
    foundation) duration=7200 ;;
    rosa)       duration=14400 ;;
    *)
      echo "사용법: source scripts/tf-session.sh <personal|bootstrap|foundation|rosa|clear>" >&2
      return 1
      ;;
  esac

  command -v jq >/dev/null 2>&1 || { echo "jq가 필요합니다." >&2; return 1; }

  # 이전 세션 환경변수를 무시하고 ~/.aws 기본 자격증명으로 호출
  local -a base=(env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN aws --region "$region")

  local caller user_arn user_name account mfa_serial code creds
  caller=$("${base[@]}" sts get-caller-identity --output json) || return 1
  user_arn=$(jq -r .Arn <<<"$caller")
  account=$(jq -r .Account <<<"$caller")

  case "$user_arn" in
    arn:aws:iam::*:user/*) ;;
    *) echo "기본 자격증명이 IAM User가 아닙니다: $user_arn" >&2; return 1 ;;
  esac
  user_name="${user_arn##*/}"

  mfa_serial=$("${base[@]}" iam list-mfa-devices --user-name "$user_name" \
    --query 'MFADevices[0].SerialNumber' --output text) || return 1
  if [ -z "$mfa_serial" ] || [ "$mfa_serial" = "None" ]; then
    echo "${user_name}에 등록된 MFA 장치가 없습니다. MFA 등록 후 다시 실행하세요." >&2
    return 1
  fi

  read -r -p "MFA 코드 (${user_name}): " code
  [[ "$code" =~ ^[0-9]{6}$ ]] || { echo "6자리 숫자를 입력하세요." >&2; return 1; }

  if [ "$mode" = "personal" ]; then
    creds=$("${base[@]}" sts get-session-token \
      --serial-number "$mfa_serial" --token-code "$code" \
      --duration-seconds "$duration" \
      --query Credentials --output json) || return 1
  else
    creds=$("${base[@]}" sts assume-role \
      --role-arn "arn:aws:iam::${account}:role/seokpan-tf-${mode}" \
      --role-session-name "${user_name}-${mode}" \
      --serial-number "$mfa_serial" --token-code "$code" \
      --duration-seconds "$duration" \
      --query Credentials --output json) || return 1
  fi

  AWS_ACCESS_KEY_ID=$(jq -r .AccessKeyId <<<"$creds")
  AWS_SECRET_ACCESS_KEY=$(jq -r .SecretAccessKey <<<"$creds")
  AWS_SESSION_TOKEN=$(jq -r .SessionToken <<<"$creds")
  TF_SESSION_MODE="$mode"
  TF_SESSION_EXPIRES=$(jq -r .Expiration <<<"$creds")
  export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN TF_SESSION_MODE TF_SESSION_EXPIRES

  echo "세션: ${mode}"
  echo "실행 주체: $(aws sts get-caller-identity --query Arn --output text)"
  echo "만료: ${TF_SESSION_EXPIRES}"
}

_seokpan_tf_session "$@"
_seokpan_tf_session_rc=$?
unset -f _seokpan_tf_session
return "$_seokpan_tf_session_rc"
