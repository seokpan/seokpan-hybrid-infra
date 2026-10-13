#!/usr/bin/env bash
# 작성자: 김상희
# 작성 날짜: 2026/10/13
# check_data_handoff.sh 회귀 시험 — 가짜 aws 명령 · 가짜 인계 폴더만 사용 (실제 AWS · 인계 파일 접근 없음)
#
# 사용: bash ansible/data/tests/check_data_handoff_test.sh     (root가 아닌 계정에서 — root는 [ -w ]가 항상 참)
# 종료 코드: 0 = 모든 Case 기대대로, 1 = 하나 이상 다름
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
SUT="$HERE/../controller/check_data_handoff.sh"
[ "$(id -u)" -ne 0 ] || { echo "root가 아닌 계정에서 실행" >&2; exit 2; }
T=$(mktemp -d); trap 'chmod -R u+w "$T" 2>/dev/null; rm -rf "$T"' EXIT

# ---- 가짜 aws (MOCK_FAIL에 rds · cache · ec2 · sts를 넣으면 그 조회가 실패) ----
mkdir -p "$T/bin"
cat > "$T/bin/aws" <<'AWS'
#!/usr/bin/env bash
a="$*"; f="${MOCK_FAIL:-}"
fail(){ [[ " $f " == *" $1 "* ]] && { echo "An error occurred (AccessDenied)" >&2; exit 254; }; }
case "$a" in
  *sts\ get-caller-identity*) fail sts; echo 123456789012 ;;
  *rds\ describe-db-instances*) fail rds
    echo '{"Endpoint":{"Address":"db1.abc.ap-northeast-2.rds.amazonaws.com","Port":3306},"MasterUserSecret":{"SecretArn":"arn:aws:secretsmanager:ap-northeast-2:123456789012:secret:rds!db-x"},"VpcSecurityGroups":[{"VpcSecurityGroupId":"sg-rds"}]}' ;;
  *elasticache\ describe-replication-groups*) fail cache
    echo '{"NodeGroups":[{"PrimaryEndpoint":{"Address":"master.r.abc.apn2.cache.amazonaws.com","Port":6379}}]}' ;;
  *elasticache\ describe-cache-clusters*) fail cache; echo '["sg-redis","sg-redis"]' ;;
  *ec2\ describe-vpcs*) fail ec2; echo vpc-1 ;;
  *ec2\ describe-security-groups*seokpan-fnd-rds*) fail ec2; echo sg-rds ;;
  *ec2\ describe-security-groups*seokpan-fnd-redis*) fail ec2; echo sg-redis ;;
  *) echo "mock: 모르는 호출 $a" >&2; exit 99 ;;
esac
AWS
chmod +x "$T/bin/aws"

# ---- 가짜 인계 폴더 ----
NAMES='["backup_bucket_name","backup_user_name","network_subnets","rds_address","rds_master_secret_arn","rds_port","rds_security_group_id","redis_port","redis_primary_endpoint","redis_security_group_id","vpc_id"]'
mkcase(){ # mkcase <폴더> <c-data 추가 jq> <manifest jq>
  local d="$T/$1"; mkdir -p "$d"
  jq -n '{backup_bucket_name:{value:"seokpan-fnd-backup-123456789012"},backup_user_name:{value:"seokpan-fnd-backup"},
          rds_address:{value:"db1.abc.ap-northeast-2.rds.amazonaws.com"},rds_port:{value:3306},
          rds_master_secret_arn:{value:"arn:aws:secretsmanager:ap-northeast-2:123456789012:secret:rds!db-x"},
          rds_security_group_id:{value:"sg-rds"},redis_security_group_id:{value:"sg-redis"},
          redis_primary_endpoint:{value:"master.r.abc.apn2.cache.amazonaws.com"},redis_port:{value:6379},
          vpc_id:{value:"vpc-1"},network_subnets:{value:{data_private_id:["s1","s2","s3"]}}} | '"$2" > "$d/c-data-outputs.json"
  local h; h=$(sha256sum "$d/c-data-outputs.json" | cut -d' ' -f1)
  jq -n --arg h "$h" --argjson n "$NAMES" '{file_sha256:{"c-data-outputs.json":$h},c_output_names:$n} | '"$3" > "$d/handoff-manifest.json"
  chmod 444 "$d"/*.json; chmod 555 "$d"
}
mkcase ok            '.'                                        '.'
mkcase hash-other    '.'                                        '.other = .file_sha256["c-data-outputs.json"] | .file_sha256 = {}'
mkcase field-miss    '.'                                        'del(.file_sha256["c-data-outputs.json"])'
mkcase field-diff    '.'                                        '.file_sha256["c-data-outputs.json"] = ("0" * 64)'
mkcase names-diff    '.'                                        '.c_output_names |= .[1:]'
mkcase names-dup     '.'                                        '.c_output_names += ["vpc_id"]'
mkcase secret-key    '.x = {value:{auth_token:"t"}}'            '.c_output_names += ["x"]'
mkcase writable      '.'                                        '.'; chmod 755 "$T/writable"

PASS=0; FAIL=0
run(){ # run <설명> <폴더> <기대 종료 코드> <출력에 있어야 할 문자열> [MOCK_FAIL]
  local before after out rc
  before=$(ls -A "$T/$2" | paste -sd' ')
  out=$(PATH="$T/bin:$PATH" MOCK_FAIL="${5:-}" bash "$SUT" "$T/$2" 2>&1); rc=$?
  after=$(ls -A "$T/$2" | paste -sd' ')
  if [ "$rc" = "$3" ] && grep -qF -- "$4" <<<"$out" && [ "$before" = "$after" ] && ! grep -qE '123456789012|db1\.abc|master\.r\.abc|sg-rds|sg-redis|vpc-1' <<<"$out"; then
    PASS=$((PASS+1)); printf 'PASS  %-34s rc=%s\n' "$1" "$rc"
  else
    FAIL=$((FAIL+1)); printf 'FAIL  %-34s rc=%s (기대 %s) 폴더변경=%s\n' "$1" "$rc" "$3" "$([ "$before" = "$after" ] && echo 없음 || echo 있음)"
    sed 's/^/      /' <<<"$out" | grep -E 'NG|\?\?|결과'
  fi
}
run "정상"                          ok          0 "NG 0개 · 판정 불가 0개"
run "같은 해시가 다른 필드에만"      hash-other  1 "NG  manifest file_sha256[c-data-outputs.json]"
run "대상 필드 없음"                field-miss  1 "NG  manifest file_sha256[c-data-outputs.json]"
run "대상 필드 해시 다름"           field-diff  1 "NG  manifest file_sha256[c-data-outputs.json]"
run "c_output_names 하나 빠짐"      names-diff  1 "NG  manifest c_output_names"
run "c_output_names 중복"           names-dup   1 "NG  manifest c_output_names"
run "비밀값 키 (auth_token)"        secret-key  1 "NG  비밀값으로 보이는 키가 있음"
run "폴더 쓰기 가능 (파일 안 만듦)" writable    1 "NG  폴더 쓰기 가능"
run "RDS 조회 실패 → 판정 불가"      ok          2 "판정 불가 2개"  rds
run "Valkey 조회 실패 → 판정 불가"   ok          2 "판정 불가 2개"  cache
run "자격증명 없음"                 ok          2 "AWS 자격증명 없음" sts
echo "== PASS ${PASS} · FAIL ${FAIL}"
[ "$FAIL" -eq 0 ]
