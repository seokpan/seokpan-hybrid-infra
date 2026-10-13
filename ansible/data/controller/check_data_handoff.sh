#!/usr/bin/env bash
# 작성자: 김상희
# 작성 날짜: 2026/10/13
# foundation Data 출력 인계 수신 확인 (infra #19 · #23, 이관 Runbook #44 0-1)
#
# - 이유빈 님 인계 폴더의 c-data-outputs.json · handoff-manifest.json을 읽고 AWS 실제 자원과 대조한다.
# - AWS 조회(describe · get)와 파일 읽기만 한다. 인계 폴더와 그 안의 파일에 아무것도 쓰지 않는다.
#   쓰기 가능 여부도 파일을 만들어 보지 않고 권한 검사([ -w ], ACL 반영)로만 판정한다.
# - 실제 값(주소 · ARN · ID · 계정 번호)은 출력하지 않는다 → 결과를 그대로 Issue에 붙여도 된다.
#   파일은 SHA-256 앞 12자리, manifest는 키 구조만 보여 준다.
#
# 사용 (controller, 본인 계정, personal MFA 세션):
#   source scripts/tf-session.sh personal
#   bash check_data_handoff.sh                     # 기본 인계 폴더
#   bash check_data_handoff.sh <인계 폴더 경로>     # foundation을 다시 Apply해 폴더가 바뀐 경우
#
# 종료 코드: 0 = 모두 OK, 1 = NG가 하나 이상,
#           2 = NG 없이 판정 불가(도구 · 자격증명 없음, 또는 AWS 조회 실패로 대조하지 못한 묶음이 있음)
set -uo pipefail
exec 2> >(sed -u -E 's/[0-9]{12}/<계정>/g' >&2)
REGION=ap-northeast-2; P=seokpan-fnd
D=${1:-/home/ansible/.local/share/seokpan-hybrid/foundation-execution/actual-output-handoff-1r02rz2l}
O="$D/c-data-outputs.json"; M="$D/handoff-manifest.json"
NG=0; UNK=0
ok(){ printf '  OK  %s\n' "$1"; }; ng(){ printf '  NG  %s\n' "$1"; NG=$((NG+1)); }
unk(){ printf '  ??  %s (판정 불가)\n' "$1"; UNK=$((UNK+1)); }
same(){ [ -n "$2" ] && [ "$2" = "$3" ] && ok "$1" || ng "$1"; }   # 값은 출력하지 않음
aws_(){ aws --region "$REGION" --output json "$@"; }
v(){ jq -r --arg k "$1" '.[$k] | if type=="object" and has("value") then .value else . end | if .==null then "" else tostring end' "$O" 2>/dev/null; }
finish(){
  echo "== 결과: NG ${NG}개 · 판정 불가 ${UNK}개"
  if [ "$NG" -gt 0 ]; then exit 1; elif [ "$UNK" -gt 0 ]; then exit 2; else exit 0; fi
}

for t in aws jq sha256sum sort comm; do
  command -v "$t" >/dev/null || { echo "필요한 도구 없음: $t" >&2; exit 2; }
done

# Data 출력 9개는 모두 있어야 하고, 그 밖에는 이유빈 님 인계 형식의 공통 출력 2개만 허용
WANT="backup_bucket_name backup_user_name rds_address rds_master_secret_arn rds_port rds_security_group_id redis_port redis_primary_endpoint redis_security_group_id"
EXTRA_OK="network_subnets vpc_id"
ALLOWED=$(tr ' ' '\n' <<<"$WANT $EXTRA_OK" | sort | paste -sd' ')

echo "== Data 출력 인계 확인 $(date -u +%FT%TZ)"
echo "[0] 수신 파일 · 권한 (쓰기 시도 없이 권한만 검사)"
for f in "$O" "$M"; do b=$(basename "$f")
  [ -r "$f" ] && ok "$b 읽기 가능" || ng "$b 읽기 불가"
  [ -w "$f" ] && ng "$b 쓰기 가능 (불가여야 함)" || ok "$b 쓰기 불가"
  jq -e . "$f" >/dev/null 2>&1 && ok "$b JSON 형식" || ng "$b JSON 아님"
  printf '  --  %s SHA-256 %s\n' "$b" "$(sha256sum "$f" 2>/dev/null | cut -c1-12)"
done
[ -w "$D" ] && ng "폴더 쓰기 가능 (불가여야 함)" || ok "폴더 쓰기 불가"

# manifest의 정해진 필드(file_sha256["c-data-outputs.json"])가 받은 파일의 전체 SHA-256과 정확히 같아야 함
OSUM=$(sha256sum "$O" 2>/dev/null | cut -d' ' -f1)
if [ -n "$OSUM" ] && jq -e --arg h "$OSUM" '(.file_sha256 | type) == "object" and .file_sha256["c-data-outputs.json"] == $h' "$M" >/dev/null 2>&1; then
  ok "manifest file_sha256[c-data-outputs.json] = 받은 파일 SHA-256"
else
  ng "manifest file_sha256[c-data-outputs.json] ≠ 받은 파일 SHA-256 (없음 · 다른 값 · 형식 오류)"
fi
echo "  --  manifest 키 구조 (값 제외):"
jq -r '[paths(scalars) | map(tostring) | join(".")] | .[]' "$M" 2>/dev/null | sed 's/^/        /'

echo "[1] 출력 이름 (Data 9개 + 공통 2개)"
HAVE=$(jq -r 'keys[]' "$O" 2>/dev/null | sort)
MISS=$(comm -23 <(tr ' ' '\n' <<<"$WANT" | sort) <(printf '%s\n' "$HAVE") | paste -sd' ')
EXTRA=$(comm -13 <(tr ' ' '\n' <<<"$ALLOWED" | sort) <(printf '%s\n' "$HAVE") | paste -sd' ')
[ -z "$MISS" ] && ok "Data 출력 9개 모두 있음" || ng "Data 출력 빠짐: $MISS"
[ -z "$EXTRA" ] && ok "그 밖의 이름은 공통 출력($EXTRA_OK)뿐" || ng "예상 밖 이름: $EXTRA"
# manifest c_output_names = 받은 파일의 출력 이름 = 허용 11개 (순서 무관, 중복 없음)
MNAMES=$(jq -r 'if (.c_output_names | type) == "array" then .c_output_names[] | tostring else empty end' "$M" 2>/dev/null | sort)
MDUP=$(printf '%s\n' "$MNAMES" | uniq -d | paste -sd' ')
if [ -n "$MNAMES" ] && [ -z "$MDUP" ] && [ "$(paste -sd' ' <<<"$MNAMES")" = "$(paste -sd' ' <<<"$HAVE")" ] && [ "$(paste -sd' ' <<<"$MNAMES")" = "$ALLOWED" ]; then
  ok "manifest c_output_names = 받은 출력 이름 = 허용 11개"
else
  ng "manifest c_output_names가 받은 출력 이름 · 허용 11개와 다름 (없음 · 중복 · 차이)"
fi
# 비밀값 키: 키 경로의 한 구간이 비밀번호 · Token · 개인키 · 비밀 키 이름일 때만 (data_private_id 같은 서브넷 이름은 제외)
jq -r '[paths | map(tostring) | join(".")] | .[]' "$O" 2>/dev/null \
  | grep -qiE '(^|[._])(pass(word|wd)?|[a-z_]*token|secret_?string|private_?key|secret_?access_?key)([._]|$)' \
  && ng "비밀값으로 보이는 키가 있음" || ok "비밀값 키 없음"

echo "[2] AWS 실제 값과 대조"
ACCT=$(aws_ sts get-caller-identity --query Account --output text 2>/dev/null) || { echo "AWS 자격증명 없음 — tf-session.sh personal 먼저" >&2; exit 2; }
if DB=$(aws_ rds describe-db-instances --db-instance-identifier "$P-mariadb" --query 'DBInstances[0]') && [ "$(jq -r 'type' <<<"$DB")" = object ]; then
  same "rds_address = RDS Endpoint"               "$(v rds_address)"           "$(jq -r '.Endpoint.Address' <<<"$DB")"
  same "rds_port = 3306 = RDS 포트"               "$(v rds_port)"              "$(jq -r '.Endpoint.Port' <<<"$DB")"
  same "rds_master_secret_arn = RDS 마스터 Secret" "$(v rds_master_secret_arn)" "$(jq -r '.MasterUserSecret.SecretArn' <<<"$DB")"
else
  DB=""; unk "RDS $P-mariadb 조회 실패 — RDS 3개 항목"
fi
[ "$(v rds_port)" = 3306 ] || ng "rds_port가 3306 아님"
if RG=$(aws_ elasticache describe-replication-groups --replication-group-id "$P-redis" --query 'ReplicationGroups[0]') && [ "$(jq -r 'type' <<<"$RG")" = object ]; then
  same "redis_primary_endpoint = Valkey Primary"  "$(v redis_primary_endpoint)" "$(jq -r '.NodeGroups[0].PrimaryEndpoint.Address' <<<"$RG")"
  same "redis_port = 6379 = Valkey 포트"          "$(v redis_port)"            "$(jq -r '.NodeGroups[0].PrimaryEndpoint.Port' <<<"$RG")"
else
  unk "Valkey $P-redis 조회 실패 — Valkey 2개 항목"
fi
[ "$(v redis_port)" = 6379 ] || ng "redis_port가 6379 아님"
same "backup_bucket_name = $P-backup-<계정>"    "$(v backup_bucket_name)"    "$P-backup-$ACCT"
same "backup_user_name = $P-backup"             "$(v backup_user_name)"      "$P-backup"

echo "[3] SG 대응"
sg(){ aws_ ec2 describe-security-groups --filters "Name=group-name,Values=$1" "Name=vpc-id,Values=$VPC" --query 'SecurityGroups[0].GroupId' --output text; }
if VPC=$(aws_ ec2 describe-vpcs --filters "Name=tag:Name,Values=$P-vpc" --query 'Vpcs[0].VpcId' --output text) \
   && [[ $VPC == vpc-* ]] \
   && RDS_SG=$(sg "$P-rds") && REDIS_SG=$(sg "$P-redis") \
   && NODE_SG=$(aws_ elasticache describe-cache-clusters --query "CacheClusters[?ReplicationGroupId=='$P-redis'].SecurityGroups[].SecurityGroupId" | jq -r 'unique | if length==1 then .[0] else "" end'); then
  same "rds_security_group_id = 이름 $P-rds"          "$(v rds_security_group_id)"   "$RDS_SG"
  if [ -n "$DB" ]; then
    same "rds_security_group_id = RDS에 붙은 SG"      "$(v rds_security_group_id)"   "$(jq -r '[.VpcSecurityGroups[].VpcSecurityGroupId] | if length==1 then .[0] else "" end' <<<"$DB")"
  else
    unk "RDS 조회 실패 — RDS에 붙은 SG 비교"
  fi
  same "redis_security_group_id = 이름 $P-redis"      "$(v redis_security_group_id)" "$REDIS_SG"
  same "redis_security_group_id = Valkey 노드 2개 SG" "$(v redis_security_group_id)" "$NODE_SG"
else
  unk "VPC · SG · Valkey 노드 조회 실패 — SG 대응 4개 항목"
fi
[ "$(v rds_security_group_id)" != "$(v redis_security_group_id)" ] && ok "두 SG ID가 서로 다름" || ng "두 SG ID가 같음"

echo "[4] Data 계약 형식 (2.1절)"
[[ $(v rds_address) == *.rds.amazonaws.com ]] && ok "RDS 주소 = AWS Endpoint 형식 (CNAME 아님)" || ng "RDS 주소 형식"
[[ $(v redis_primary_endpoint) == *.cache.amazonaws.com ]] && ok "Valkey 주소 = AWS Endpoint 형식 (CNAME 아님)" || ng "Valkey 주소 형식"
[[ $(v redis_primary_endpoint) == master.* ]] && ok "Primary Endpoint (노드 · Replica 주소 아님)" || ng "Primary Endpoint가 아님"

finish
