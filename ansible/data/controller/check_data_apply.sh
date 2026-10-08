#!/usr/bin/env bash
# 작성자: 김상희
# 작성 날짜: 2026/10/08
# foundation Apply 직후 Data 자원 읽기 전용 확인 (infra #19 · #23, rosa Data SG 인계)
#
# - AWS 조회(describe · get · list)만 한다. 아무것도 만들거나 바꾸지 않는다.
# - SG ID · 계정 번호 · Endpoint 주소를 화면에 출력하지 않는다 → 결과를 그대로 Issue에 붙여도 된다.
# - SG는 ID가 아니라 이름(seokpan-fnd-rds / -redis)으로 찾는다.
#   이유빈 님이 정태훈 님께 넘긴 ID를 RDS_SG_ID · REDIS_SG_ID로 주면 대응(서로 바뀌지 않았는지)도 비교한다.
#
# 사용 (controller, 본인 계정, personal MFA 세션):
#   source scripts/tf-session.sh personal
#   bash check_data_apply.sh                                   # 기본 확인
#   RDS_SG_ID=sg-... REDIS_SG_ID=sg-... bash check_data_apply.sh   # 인계 ID 대응까지 확인
#   ROSA_APPLIED=1 bash check_data_apply.sh                    # rosa Apply 뒤 (Worker → Data 규칙 1개씩 기대)
#
# 종료 코드: 0 = 모두 OK, 1 = NG가 하나 이상, 2 = 실행 조건 미충족(도구 · 자격증명)

set -uo pipefail

REGION="ap-northeast-2"
P="seokpan-fnd"
ONPREM_CIDR="192.168.52.50/32"
EXPECT_WORKER_RULES=$([ "${ROSA_APPLIED:-0}" = "1" ] && echo 1 || echo 0)

NG=0
ok() { printf '  OK  %s\n' "$1"; }
ng() { printf '  NG  %s\n' "$1"; NG=$((NG + 1)); }
chk() { # chk "설명" "실제값" "기대값"
  if [ "$2" = "$3" ]; then ok "$1"; else ng "$1 (실제: $2 / 기대: $3)"; fi
}
aws_() { aws --region "$REGION" --output json "$@"; }

for t in aws jq; do
  command -v "$t" >/dev/null || { echo "필요한 도구 없음: $t" >&2; exit 2; }
done
ACCT=$(aws_ sts get-caller-identity --query Account --output text 2>/dev/null) \
  || { echo "AWS 자격증명 없음 — tf-session.sh personal 먼저" >&2; exit 2; }

echo "== Data Apply 확인 $(date -u +%FT%TZ) (ROSA_APPLIED=${ROSA_APPLIED:-0})"

# ---------------------------------------------------------------- 1. VPC
echo "[1] VPC"
VPC_ID=$(aws_ ec2 describe-vpcs --filters "Name=tag:Name,Values=${P}-vpc" \
  --query 'Vpcs[].VpcId' | jq -r 'if length==1 then .[0] else "" end')
[ -n "$VPC_ID" ] && ok "${P}-vpc 1개" || ng "${P}-vpc를 1개로 찾지 못함"

# ---------------------------------------------------------------- 2. Data SG
echo "[2] Data SG"
sg_id_by_name() {
  aws_ ec2 describe-security-groups --filters "Name=group-name,Values=$1" \
    --query 'SecurityGroups[].GroupId' | jq -r 'if length==1 then .[0] else "" end'
}
check_sg() { # check_sg <이름> <포트> <온프렘 규칙 기대 수> <ID를 받을 변수 이름>
  local name=$1 port=$2 want_cidr=$3 id sg rules
  id=$(sg_id_by_name "$name")
  printf -v "$4" '%s' "$id"
  if [ -z "$id" ]; then ng "$name 를 1개로 찾지 못함"; return; fi
  sg=$(aws_ ec2 describe-security-groups --group-ids "$id" --query 'SecurityGroups[0]')
  chk "$name VPC = ${P}-vpc"   "$(jq -r --arg v "$VPC_ID" 'if .VpcId == $v then "같음" else "다름" end' <<<"$sg")" "같음"
  chk "$name 태그 Component"    "$(jq -r '[.Tags[]? | select(.Key=="Component") | .Value][0] // "-"' <<<"$sg")" "data"

  rules=$(aws_ ec2 describe-security-group-rules --filters "Name=group-id,Values=$id" \
    --query 'SecurityGroupRules')
  chk "$name egress 규칙 수" "$(jq '[.[] | select(.IsEgress)] | length' <<<"$rules")" "0"
  chk "$name 온프렘 $ONPREM_CIDR → $port 규칙 수" \
    "$(jq --arg c "$ONPREM_CIDR" --argjson p "$port" \
      '[.[] | select((.IsEgress|not) and .CidrIpv4==$c and .IpProtocol=="tcp" and .FromPort==$p and .ToPort==$p)] | length' <<<"$rules")" \
    "$want_cidr"
  chk "$name Worker SG → $port 규칙 수" \
    "$(jq --argjson p "$port" \
      '[.[] | select((.IsEgress|not) and .ReferencedGroupInfo != null and .IpProtocol=="tcp" and .FromPort==$p and .ToPort==$p)] | length' <<<"$rules")" \
    "$EXPECT_WORKER_RULES"
  chk "$name 그 밖의 ingress 규칙 수 (없어야 함)" \
    "$(jq --arg c "$ONPREM_CIDR" --argjson p "$port" \
      '[.[] | select((.IsEgress|not)
        and ((.CidrIpv4==$c and .IpProtocol=="tcp" and .FromPort==$p and .ToPort==$p)
          or (.ReferencedGroupInfo != null and .IpProtocol=="tcp" and .FromPort==$p and .ToPort==$p)) | not)] | length' <<<"$rules")" \
    "0"
}
RDS_SG="" REDIS_SG=""
check_sg "${P}-rds"   3306 1 RDS_SG
check_sg "${P}-redis" 6379 0 REDIS_SG

if [ -n "${RDS_SG_ID:-}" ] || [ -n "${REDIS_SG_ID:-}" ]; then
  echo "[2-1] 인계 ID 대응 (rosa 입력 mariadb ← rds_security_group_id, redis ← redis_security_group_id)"
  if [ -z "${RDS_SG_ID:-}" ] || [ -z "${REDIS_SG_ID:-}" ] || [ -z "$RDS_SG" ] || [ -z "$REDIS_SG" ]; then
    ng "인계 ID 2개와 이름으로 찾은 SG 2개가 모두 있어야 비교 가능"
  else
    [ "$RDS_SG_ID"   = "$RDS_SG" ]   && ok "mariadb 키 = ${P}-rds"   || ng "mariadb 키가 ${P}-rds 가 아님"
    [ "$REDIS_SG_ID" = "$REDIS_SG" ] && ok "redis 키 = ${P}-redis"   || ng "redis 키가 ${P}-redis 가 아님"
    [ "$RDS_SG_ID" = "$REDIS_SG" ] && [ "$REDIS_SG_ID" = "$RDS_SG" ] && ng "두 ID가 서로 바뀌어 있음"
  fi
fi

# ---------------------------------------------------------------- 3. RDS
echo "[3] RDS ${P}-mariadb"
DB=$(aws_ rds describe-db-instances --db-instance-identifier "${P}-mariadb" --query 'DBInstances[0]' 2>/dev/null || echo null)
if [ "$DB" = "null" ]; then ng "RDS를 찾지 못함"; else
  chk "상태"              "$(jq -r '.DBInstanceStatus' <<<"$DB")" "available"
  chk "엔진 · 버전"       "$(jq -r '"\(.Engine) \(.EngineVersion)"' <<<"$DB")" "mariadb 11.8.9"
  chk "클래스"            "$(jq -r '.DBInstanceClass' <<<"$DB")" "db.t4g.small"
  chk "Multi-AZ"          "$(jq -r '.MultiAZ' <<<"$DB")" "true"
  chk "Primary · Standby AZ 다름" "$(jq -r '(.AvailabilityZone != .SecondaryAvailabilityZone) and (.SecondaryAvailabilityZone != null)' <<<"$DB")" "true"
  chk "스토리지"          "$(jq -r '"\(.StorageType) \(.AllocatedStorage)GiB 암호화=\(.StorageEncrypted) 자동확장=\(.MaxAllocatedStorage // "끔")"' <<<"$DB")" "gp3 20GiB 암호화=true 자동확장=끔"
  chk "공개 접근"         "$(jq -r '.PubliclyAccessible' <<<"$DB")" "false"
  chk "CA"                "$(jq -r '.CACertificateIdentifier' <<<"$DB")" "rds-ca-rsa2048-g1"
  chk "SG = ${P}-rds 하나" "$(jq -r --arg s "$RDS_SG" '[.VpcSecurityGroups[].VpcSecurityGroupId] == [$s]' <<<"$DB")" "true"
  chk "파라미터 그룹 · 적용" "$(jq -r '.DBParameterGroups[0] | "\(.DBParameterGroupName) \(.ParameterApplyStatus)"' <<<"$DB")" "${P}-mariadb118 in-sync"
  chk "자동 백업 보관(일)" "$(jq -r '.BackupRetentionPeriod' <<<"$DB")" "7"
  chk "삭제 보호"         "$(jq -r '.DeletionProtection' <<<"$DB")" "true"
  chk "마스터 Secret 상태" "$(jq -r '.MasterUserSecret.SecretStatus // "-"' <<<"$DB")" "active"
  chk "자동 마이너 업그레이드" "$(jq -r '.AutoMinorVersionUpgrade' <<<"$DB")" "false"

  PARAMS=$(aws_ rds describe-db-parameters --db-parameter-group-name "${P}-mariadb118" --source user \
    --query 'Parameters[].[ParameterName,ParameterValue]' | jq -r 'map("\(.[0])=\(.[1])") | sort | join(" ")')
  chk "사용자 지정 파라미터 5개" "$PARAMS" \
    "character_set_server=utf8mb4 collation_server=utf8mb4_unicode_ci require_secure_transport=1 sql_mode=STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION time_zone=Asia/Seoul"
fi

# ---------------------------------------------------------------- 4. Valkey
echo "[4] Valkey ${P}-redis"
RG=$(aws_ elasticache describe-replication-groups --replication-group-id "${P}-redis" --query 'ReplicationGroups[0]' 2>/dev/null || echo null)
if [ "$RG" = "null" ]; then ng "Replication Group을 찾지 못함"; else
  chk "상태"              "$(jq -r '.Status' <<<"$RG")" "available"
  chk "엔진"              "$(jq -r '.Engine // "-"' <<<"$RG")" "valkey"
  chk "노드 타입"         "$(jq -r '.CacheNodeType' <<<"$RG")" "cache.t4g.small"
  chk "노드 수"           "$(jq -r '.MemberClusters | length' <<<"$RG")" "2"
  chk "Multi-AZ · 자동 승격" "$(jq -r '"\(.MultiAZ) \(.AutomaticFailover)"' <<<"$RG")" "enabled enabled"
  chk "전송 · 저장 암호화" "$(jq -r '"\(.TransitEncryptionEnabled) \(.AtRestEncryptionEnabled)"' <<<"$RG")" "true true"
  chk "AUTH Token 사용"   "$(jq -r '.AuthTokenEnabled' <<<"$RG")" "true"
  chk "스냅샷 보관"       "$(jq -r '.SnapshotRetentionLimit' <<<"$RG")" "0"

  NODES=$(aws_ elasticache describe-cache-clusters --show-cache-node-info \
    --query "CacheClusters[?ReplicationGroupId=='${P}-redis']")
  chk "엔진 버전 7.2.x (노드 2개)" \
    "$(jq -r '[.[] | select(.EngineVersion|startswith("7.2"))] | length' <<<"$NODES")" "2"
  chk "두 노드 AZ 다름" "$(jq -r '[.[].PreferredAvailabilityZone] | unique | length' <<<"$NODES")" "2"
  chk "두 노드 SG = ${P}-redis 하나" \
    "$(jq -r --arg s "$REDIS_SG" 'all(.[]; [.SecurityGroups[].SecurityGroupId] == [$s])' <<<"$NODES")" "true"
  chk "파라미터 그룹" "$(jq -r '[.[].CacheParameterGroup.CacheParameterGroupName] | unique | join(",")' <<<"$NODES")" "${P}-redis"

  chk "maxmemory-policy" \
    "$(aws_ elasticache describe-cache-parameters --cache-parameter-group-name "${P}-redis" --source user \
      --query "Parameters[?ParameterName=='maxmemory-policy'].ParameterValue | [0]" | jq -r '.')" "noeviction"
fi

# ---------------------------------------------------------------- 5. Backup S3
echo "[5] Backup S3 ${P}-backup-<계정>"
B="${P}-backup-${ACCT}"
if ! aws_ s3api head-bucket --bucket "$B" >/dev/null 2>&1; then ng "버킷을 찾지 못함"; else
  chk "버전 관리" "$(aws_ s3api get-bucket-versioning --bucket "$B" | jq -r '.Status // "-"')" "Enabled"
  chk "공개 차단 4개" "$(aws_ s3api get-public-access-block --bucket "$B" \
    | jq -r '.PublicAccessBlockConfiguration | [.[]] | all')" "true"
  chk "기본 암호화" "$(aws_ s3api get-bucket-encryption --bucket "$B" \
    | jq -r '.ServerSideEncryptionConfiguration.Rules[0].ApplyServerSideEncryptionByDefault.SSEAlgorithm')" "AES256"
  chk "소유권" "$(aws_ s3api get-bucket-ownership-controls --bucket "$B" \
    | jq -r '.OwnershipControls.Rules[0].ObjectOwnership')" "BucketOwnerEnforced"
  LC=$(aws_ s3api get-bucket-lifecycle-configuration --bucket "$B")
  chk "수명 주기 규칙" "$(jq -r '[.Rules[] | "\(.ID):\(.Status)"] | sort | join(" ")' <<<"$LC")" \
    "cleanup:Enabled expire-periodic:Enabled protected-noncurrent-only:Enabled"
  chk "periodic 만료(일)" "$(jq -r '.Rules[] | select(.ID=="expire-periodic") | .Expiration.Days' <<<"$LC")" "7"
  if aws_ s3api get-bucket-policy --bucket "$B" >/dev/null 2>&1; then
    ng "버킷 정책이 있음 (없어야 함, PR #34)"
  else ok "버킷 정책 없음"; fi
fi

# ---------------------------------------------------------------- 6. Backup User
echo "[6] Backup User ${P}-backup"
U=$(aws_ iam get-user --user-name "${P}-backup" --query 'User' 2>/dev/null || echo null)
if [ "$U" = "null" ]; then ng "Backup User를 찾지 못함"; else
  chk "Permissions Boundary" "$(jq -r '.PermissionsBoundary.PermissionsBoundaryArn // "-" | split(":policy/")[1] // "-"' <<<"$U")" "seokpan-fnd-backup-boundary"
  chk "태그 Component" "$(jq -r '[.Tags[]? | select(.Key=="Component") | .Value][0] // "-"' <<<"$U")" "data"
  chk "inline 정책" "$(aws_ iam list-user-policies --user-name "${P}-backup" | jq -r '.PolicyNames | join(",")')" "${P}-backup-s3"
  chk "관리형 정책 연결 수" "$(aws_ iam list-attached-user-policies --user-name "${P}-backup" | jq -r '.AttachedPolicies | length')" "0"
  if aws_ iam get-login-profile --user-name "${P}-backup" >/dev/null 2>&1; then
    ng "콘솔 로그인 프로필이 있음 (없어야 함)"
  else ok "콘솔 로그인 없음"; fi
  printf '  --  Access Key 수: %s (발급 전 0, 발급 후 1)\n' \
    "$(aws_ iam list-access-keys --user-name "${P}-backup" | jq -r '.AccessKeyMetadata | length')"
fi

echo "== 결과: NG ${NG}개"
[ "$NG" -eq 0 ]
