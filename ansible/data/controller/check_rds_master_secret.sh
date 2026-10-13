#!/usr/bin/env bash
# check_rds_master_secret.sh — RDS 마스터 Secret 읽기 사전 확인
#   조회만 함. Secret 값 · ARN · 계정 번호는 출력하지 않음 (ARN은 SHA-256 앞 12자리만)
#   종료 코드: 0 = NG 없음 · 1 = NG 하나라도 · 2 = NG 없이 판정 불가 (자격증명 없음 등)
#   실행 예: source scripts/tf-session.sh personal && bash ~/recovery/check_rds_master_secret.sh
set -euo pipefail
export AWS_PAGER=""

REGION="${REGION:-ap-northeast-2}"
DB_ID="${DB_ID:-seokpan-fnd-mariadb}"
HANDOFF_JSON="${HANDOFF_JSON:-/home/ansible/.local/share/seokpan-hybrid/foundation-execution/actual-output-handoff-1r02rz2l/c-data-outputs.json}"
PROJECT_END_KST="${PROJECT_END_KST:-2026-10-27 00:00}"

NG=0; UNK=0
ok()   { printf 'OK       %s\n' "$*"; }
ng()   { printf 'NG       %s\n' "$*"; NG=$((NG+1)); }
unk()  { printf '판정불가 %s\n' "$*"; UNK=$((UNK+1)); }
info() { printf 'INFO     %s\n' "$*"; }
mask() { sed -E 's/[0-9]{12}/<계정>/g'; }
h12()  { printf '%s' "$1" | sha256sum | cut -c1-12; }
aws_q() { aws --region "$REGION" "$@" 2> >(mask >&2); }
finish() {
  echo "----"
  echo "NG ${NG} · 판정불가 ${UNK}"
  if [ "$NG" -gt 0 ]; then exit 1; fi
  if [ "$UNK" -gt 0 ]; then exit 2; fi
  exit 0
}
to_kst() { TZ=Asia/Seoul date -d "$1" '+%F %H:%M KST' 2>/dev/null || echo "$1"; }

for c in aws jq sha256sum date; do
  command -v "$c" >/dev/null || { echo "필요 도구 없음: $c" >&2; exit 2; }
done

# 0. 실행 주체 (계정 번호 제외)
if ! CALLER=$(aws_q sts get-caller-identity --query Arn --output text); then
  echo "AWS 자격증명 없음" >&2; exit 2
fi
info "실행 주체: ${CALLER##*:}"

# 1. RDS → MasterUserSecret
if ! DB_JSON=$(aws_q rds describe-db-instances --db-instance-identifier "$DB_ID" \
      --query 'DBInstances[0].{st:DBInstanceStatus,mu:MasterUsername,arn:MasterUserSecret.SecretArn,sst:MasterUserSecret.SecretStatus}' \
      --output json); then
  unk "RDS ${DB_ID} 조회 실패"; finish
fi
DB_ST=$(jq -r '.st // ""'  <<<"$DB_JSON")
MU=$(jq -r '.mu // ""'     <<<"$DB_JSON")
ARN=$(jq -r '.arn // ""'   <<<"$DB_JSON")
SST=$(jq -r '.sst // ""'   <<<"$DB_JSON")
info "RDS 상태: ${DB_ST} (Secret 읽기는 상태와 무관)"

if [ -z "$ARN" ]; then
  ng "MasterUserSecret 없음 (manage_master_user_password 미적용?)"; finish
fi
ok "MasterUserSecret 있음 (ARN 해시 $(h12 "$ARN"))"
if [ "$SST" = "active" ]; then ok "SecretStatus = active"; else ng "SecretStatus = ${SST:-없음} (active 아님)"; fi

# 2. Secret 메타데이터
if ! SEC=$(aws_q secretsmanager describe-secret --secret-id "$ARN" --output json); then
  unk "describe-secret 실패 (권한?)"; finish
fi
NAME=$(jq -r '.Name // ""' <<<"$SEC")
if [[ "$NAME" == rds\!db-* ]]; then ok "이름 형식 rds!db-… (RDS 관리 Secret)"; else ng "이름 형식이 rds!db-… 아님"; fi
if [ "$(jq -r '.OwningService // ""' <<<"$SEC")" = "rds" ]; then ok "OwningService = rds"; else ng "OwningService ≠ rds"; fi
if [ "$(jq -r '.DeletedDate // ""' <<<"$SEC")" = "" ]; then ok "삭제 예약 없음"; else ng "삭제 예약됨 (DeletedDate 있음)"; fi

CUR=$(jq '[.VersionIdsToStages // {} | to_entries[] | select(.value | index("AWSCURRENT"))] | length' <<<"$SEC")
PEND=$(jq '[.VersionIdsToStages // {} | to_entries[] | select(.value | index("AWSPENDING"))] | length' <<<"$SEC")
if [ "$CUR" = "1" ]; then ok "AWSCURRENT 버전 1개"; else ng "AWSCURRENT 버전 ${CUR}개"; fi
if [ "$PEND" = "0" ]; then ok "AWSPENDING 없음 (회전 진행 중 아님)"; else ng "AWSPENDING 있음 — 회전 진행 중, 끝난 뒤 다시 확인"; fi

# 3. 자동 회전 일정
ROT=$(jq -r '.RotationEnabled // false' <<<"$SEC")
DAYS=$(jq -r '.RotationRules.AutomaticallyAfterDays // ""' <<<"$SEC")
SCHED=$(jq -r '.RotationRules.ScheduleExpression // ""' <<<"$SEC")
WIN=$(jq -r '.RotationRules.Duration // ""' <<<"$SEC")
NEXT=$(jq -r '.NextRotationDate // ""' <<<"$SEC")
LAST=$(jq -r '.LastRotatedDate // ""' <<<"$SEC")
info "자동 회전: ${ROT} · 주기 ${DAYS:-없음}일 · 일정식 ${SCHED:-없음} · 회전 창 ${WIN:-없음}"
[ -n "$LAST" ] && info "마지막 회전: $(to_kst "$LAST")"
if [ "$ROT" = "true" ] && [ -n "$NEXT" ]; then
  info "다음 회전: $(to_kst "$NEXT")"
  if [ "$(date -d "$NEXT" +%s)" -lt "$(TZ=Asia/Seoul date -d "$PROJECT_END_KST" +%s)" ]; then
    info "프로젝트 기간 안에 비밀번호가 바뀜 → 쓸 때마다 직전에 다시 읽고, 이관 쓰기 중지 구간과 겹치는지 확인"
  fi
fi

# 4. 암호화 키
KMS=$(jq -r '.KmsKeyId // ""' <<<"$SEC")
if [ -z "$KMS" ]; then
  ok "KMS = aws/secretsmanager (기본 AWS 관리형 키)"
elif KM=$(aws_q kms describe-key --key-id "$KMS" --query KeyMetadata.KeyManager --output text); then
  if [ "$KM" = "AWS" ]; then ok "KMS = AWS 관리형 키"; else info "KMS = 고객 관리형 키 — 읽는 사람에게 kms:Decrypt 필요"; fi
else
  unk "KMS 키 조회 실패"
fi

# 5. 인계 파일과 대조 (ARN 문자열 일치만)
if [ -r "$HANDOFF_JSON" ]; then
  M=$(jq --arg a "$ARN" '[.. | strings | select(. == $a)] | length' "$HANDOFF_JSON")
  S=$(jq '[.. | strings | select(startswith("arn:aws:secretsmanager:"))] | length' "$HANDOFF_JSON")
  if [ "$M" -ge 1 ]; then ok "인계 파일의 Secret ARN = RDS 실제 값"
  elif [ "$S" -ge 1 ]; then ng "인계 파일의 Secret ARN ≠ RDS 실제 값"
  else info "인계 파일에 Secret ARN 없음 (RDS 조회로 찾아야 함)"; fi
else
  unk "인계 파일 읽기 불가: ${HANDOFF_JSON##*/}"
fi

# 6. 값 읽기 — 값은 파이프 안에서만, 요약(참/거짓)만 남김
JQ_PROG=$(cat <<'JQ'
{
  keys_ok: ((keys | sort) == ["password","username"]),
  user_ok: (.username == $mu),
  pw_ok:   ((.password | type) == "string" and (.password | length) >= 16),
  dq:      (.password | test("\"")),
  bs:      (.password | test("\\\\")),
  sq:      (.password | test("'")),
  hash:    (.password | test("#")),
  sp:      (.password | test("\\s")),
  shell:   (.password | test("[$`!]"))
}
JQ
)
if ! SUM=$(aws_q secretsmanager get-secret-value --secret-id "$ARN" --version-stage AWSCURRENT \
            --query SecretString --output text \
          | jq -c --arg mu "$MU" "$JQ_PROG" 2>/dev/null); then
  unk "Secret 값 읽기 실패 (secretsmanager:GetSecretValue · kms:Decrypt 권한 또는 형식)"; finish
fi
if [ "$(jq -r .keys_ok <<<"$SUM")" = "true" ]; then ok "값의 키 = username · password"; else ng "값의 키가 username · password 2개가 아님"; fi
if [ "$(jq -r .user_ok <<<"$SUM")" = "true" ]; then ok "username = RDS MasterUsername"; else ng "username ≠ RDS MasterUsername"; fi
if [ "$(jq -r .pw_ok <<<"$SUM")" = "true" ]; then ok "password 비어 있지 않음 (16자 이상)"; else ng "password 형식 이상"; fi

FLAGS=$(jq -r '[ (if .dq then "큰따옴표" else empty end), (if .bs then "역슬래시" else empty end),
                 (if .sq then "작은따옴표" else empty end), (if .hash then "#" else empty end),
                 (if .sp then "공백" else empty end), (if .shell then "셸 특수문자($ ` !)" else empty end) ] | join(", ")' <<<"$SUM")
if [ -z "$FLAGS" ]; then
  info "옵션 파일 · 셸에서 주의할 문자 없음"
else
  info "주의할 문자 있음: ${FLAGS} → .cnf에는 password=\"…\"로 감싸고, 명령줄 인자로 넘기지 않음"
fi

finish
