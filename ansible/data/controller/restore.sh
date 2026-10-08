#!/bin/bash
# seokpan-hybrid 복구 DB VM 복원 — #48 0~4단계를 한 번에 실행
# 사용: ~/recovery/restore.sh [--id <Backup ID>] [--replace] [--compare-src]
#   --id           복원할 사본 (없으면 가장 최근 사본부터, SHA-256 실패 시 이전 사본)
#   --replace      복구 DB에 stone_game이 있으면 지우고 진행 (없으면 멈춤)
#   --compare-src  예행용: 백업 작업 VM의 source.cnf 원본과 해시 비교 (root 비밀번호 1회)
# 전제: ssh-agent에 seokpan-recovery 키, 해독 키, ~/recovery/table_hashes.sh
set -euo pipefail
umask 077

RCV=seokpan-recovery
C=/srv/seokpan-hybrid-recovery/copies
KEY=~/secrets/seokpan/backup-age/backup-kimsanghee.key
HASH=~/recovery/table_hashes.sh
BK=root@192.168.52.50
SRC_CNF=/etc/seokpan-hybrid-backup/source.cnf
EXPECT_REV=20260902_0002
EXPECT_TABLES=8
EXPECT_ACCOUNTS=6

ID=""; REPLACE=0; CMP=0; T=""
while [ $# -gt 0 ]; do
  case "$1" in
    --id) ID=$2; shift 2 ;;
    --replace) REPLACE=1; shift ;;
    --compare-src) CMP=1; shift ;;
    *) echo "알 수 없는 인자: $1" >&2; exit 2 ;;
  esac
done

now() { date -u +%FT%TZ; }
die() { echo "중단: $*" >&2; [ -n "$T" ] && echo "FAIL $(now) $*" >> "$T"; exit 1; }
q()   { ssh "$RCV" "mariadb -N -e \"$1\""; }

# 0. 실행 전 조건
keys=$(ssh-add -l 2>/dev/null || true)
[[ $keys == *seokpan-recovery* ]] || die "ssh-agent에 seokpan-recovery 키가 없음 (eval \"\$(ssh-agent -s)\" 후 ssh-add 단독 실행)"
[ -r "$KEY" ]  || die "해독 키 없음: $KEY"
[ -r "$HASH" ] || die "비교 스크립트 없음: $HASH"
st=$(ssh "$RCV" "systemctl is-active mariadb; mariadb -N -e 'SELECT @@require_secure_transport'" | paste -sd' ')
[ "$st" = "active 1" ] || die "복구 DB 상태 이상: $st"

SRC_TSV=""
if [ "$CMP" = 1 ]; then
  SRC_TSV=$(mktemp ~/recovery/src-XXXXXX.tsv)
  echo "원본 해시 받기 (백업 작업 VM root 비밀번호)"
  ssh "$BK" "bash -s -- --defaults-extra-file=$SRC_CNF" < "$HASH" > "$SRC_TSV"
fi

RUN=restore-$(date -u +%Y%m%dT%H%M%SZ)
T=~/recovery/$RUN.times
echo "T0 복구 시작 $(now)" >> "$T"

# 1. 사본 선택 · SHA-256
if [ -n "$ID" ]; then CANDS=$ID
else CANDS=$(ssh "$RCV" "cd $C && ls -1t *.sql.gz.age 2>/dev/null | sed 's/\.sql\.gz\.age\$//'" || true); fi
[ -n "$CANDS" ] || die "사본 없음 ($C)"
SEL=""
for c in $CANDS; do
  if ssh "$RCV" "cd $C && sha256sum -c --quiet $c.sql.gz.age.sha256" >/dev/null 2>&1; then SEL=$c; break; fi
  echo "사본 확인 실패 — 건너뜀: $c" | tee -a "$T"
done
[ -n "$SEL" ] || die "SHA-256이 맞는 사본 없음"
ID=$SEL
echo "ID $ID" >> "$T"
echo "T1 사본 확인 $(now)" >> "$T"

# 2. 복구 DB 상태
if [ "$(q "SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name='stone_game'")" != 0 ]; then
  [ "$REPLACE" = 1 ] || die "복구 DB에 stone_game이 이미 있음 — 지우고 진행하려면 --replace"
  ssh "$RCV" "mariadb -e 'DROP DATABASE stone_game'"
  echo "기존 stone_game 삭제 $(now)" >> "$T"
fi

# 3. 스트림 해독 · 가져오기
echo "T2 가져오기 시작 $(now)" >> "$T"
set +e
ssh "$RCV" "cat $C/$ID.sql.gz.age" \
  | age -d -i "$KEY" \
  | ssh "$RCV" "set -o pipefail; gunzip | mariadb --default-character-set=utf8mb4"
PS=("${PIPESTATUS[@]}")
set -e
[ "${PS[*]}" = "0 0 0" ] || die "가져오기 실패 (종료 코드 ${PS[*]}) — 원인 확인 후 --replace로 다시 실행"
echo "T3 가져오기 끝 $(now)" >> "$T"

# 4. 검증
rev=$(q "SELECT version_num FROM stone_game.alembic_version")
nt=$(q "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='stone_game' AND table_type='BASE TABLE'")
[ "$rev" = "$EXPECT_REV" ]   || die "Revision 다름: $rev"
[ "$nt"  = "$EXPECT_TABLES" ] || die "테이블 수 다름: $nt"
ssh "$RCV" 'bash -s' < "$HASH" > ~/recovery/"$ID"-rcv.tsv
if [ "$CMP" = 1 ]; then
  mv "$SRC_TSV" ~/recovery/"$ID"-src.tsv
  diff ~/recovery/"$ID"-src.tsv ~/recovery/"$ID"-rcv.tsv || die "원본과 다름"
  echo "원본 = 복구 DB (행 수 · 행 해시 · Schema 해시)" | tee -a "$T"
fi
na=$(q "SELECT COUNT(*) FROM mysql.user WHERE user IN ('db_admin','identity_svc','game_svc')")
[ "$na" = "$EXPECT_ACCOUNTS" ] || echo "주의: 복구 계정 $na/$EXPECT_ACCOUNTS — #48 복구 계정 사전 준비 확인" | tee -a "$T"
echo "T4 검증 끝 $(now)" >> "$T"

# 요약
bt=$(grep -oE '[0-9]{8}T[0-9]{6}Z$' <<< "$ID")
bts=$(date -u -d "${bt:0:4}-${bt:4:2}-${bt:6:2}T${bt:9:2}:${bt:11:2}:${bt:13:2}Z" +%s)
t0=$(date -u -d "$(awk '/^T0/{print $NF}' "$T")" +%s)
t4=$(date -u -d "$(awk '/^T4/{print $NF}' "$T")" +%s)
echo "결과 ID $ID · Revision $rev · 테이블 $nt · 계정 $na · T0→T4 $((t4-t0))초 · RPO $(( (t0-bts)/60 ))분 $(( (t0-bts)%60 ))초" | tee -a "$T"
echo "기록: $T"
