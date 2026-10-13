#!/bin/bash
# seokpan-hybrid 복구 DB VM 복원 — #48 0~4단계를 한 번에 실행
# 사용: ~/recovery/restore.sh [--id <Backup ID>] [--rehearsal] [--replace] [--compare-src] [--select-only]
#                              [--key <해독 키 경로>] [--local]
#   (없음)         운영 periodic 사본 중 Backup ID의 데이터 시각이 가장 최근인 것부터,
#                  SHA-256 실패 시 이전 periodic 사본 (파일 도착 시각은 보지 않음)
#   --id           복원할 사본을 직접 지정 (형식 periodic|rehearsal-YYYYMMDDTHHMMSSZ만 허용, 대체 없음)
#   --rehearsal    예행(TEST) 모드 — 자동 선택 대상을 rehearsal 사본으로 바꿈
#   --replace      복구 DB에 stone_game이 있으면 지우고 진행 (없으면 멈춤)
#   --compare-src  예행용: 백업 작업 VM의 source.cnf 원본과 해시 비교 (root 비밀번호 1회)
#   --select-only  1단계(사본 선택 · SHA-256)까지만 실행하고 선택 결과만 출력 (복구 DB 변경 없음)
#   --key          해독 키 경로 (기본: 김상희 키). 이유빈 키 · 오프라인 사본을 쓸 때 지정
#   --local        controller 없이 복구 DB VM에서 root로 직접 실행 (SSH 없음, --compare-src 불가)
#                  키는 메모리 파일시스템(/dev/shm)에 두고 끝나면 지움 (#48 C장)
# rehearsal 사본은 --rehearsal 또는 --id로 명시했을 때만 선택되고, 결과에 TEST로 표시
# 전제(controller): ssh-agent에 seokpan-recovery 키, 해독 키, 같은 폴더의 table_hashes.sh
# 전제(--local):     복구 DB VM root, 해독 키, 같은 폴더의 table_hashes.sh
set -euo pipefail
umask 077

RCV=seokpan-recovery
C=/srv/seokpan-hybrid-recovery/copies
KEY=~/secrets/seokpan/backup-age/backup-kimsanghee.key
HASH=$(dirname "$(readlink -f "$0")")/table_hashes.sh
LOG=~/recovery
RCV_HOST=hybrid-recovery-db-01
BK=root@192.168.52.50
SRC_CNF=/etc/seokpan-hybrid-backup/source.cnf
EXPECT_REV=20260902_0002
EXPECT_TABLES=8
EXPECT_ACCOUNTS=6
ID_RE='^(periodic|rehearsal)-[0-9]{8}T[0-9]{6}Z$'

# Backup ID 검사: 형식 + 실제 있는 날짜 + 미래 시각 아님(시계 오차 5분 허용)
valid_id() {
  [[ $1 =~ $ID_RE ]] || return 1
  local ts=${1##*-} s
  s=$(date -u -d "${ts:0:4}-${ts:4:2}-${ts:6:2}T${ts:9:2}:${ts:11:2}:${ts:13:2}Z" +%s 2>/dev/null) || return 1
  [ "$(date -u -d @"$s" +%Y%m%dT%H%M%SZ)" = "$ts" ] || return 1
  [ "$s" -le $(( $(date -u +%s) + 300 )) ]
}

ID=""; PREFIX=periodic; REPLACE=0; CMP=0; SELONLY=0; LOCAL=0; T=""
while [ $# -gt 0 ]; do
  case "$1" in
    --id)
      [ $# -ge 2 ] || { echo "--id 값이 없음" >&2; exit 2; }
      valid_id "$2" || { echo "잘못된 Backup ID: $2 (형식 periodic|rehearsal-YYYYMMDDTHHMMSSZ, 미래 시각 불가)" >&2; exit 2; }
      ID=$2; shift 2 ;;
    --rehearsal) PREFIX=rehearsal; shift ;;
    --replace) REPLACE=1; shift ;;
    --compare-src) CMP=1; shift ;;
    --select-only) SELONLY=1; shift ;;
    --key)
      [ $# -ge 2 ] && [ -n "$2" ] || { echo "--key 값이 없음" >&2; exit 2; }
      KEY=$2; shift 2 ;;
    --local) LOCAL=1; shift ;;
    *) echo "알 수 없는 인자: $1" >&2; exit 2 ;;
  esac
done
[ "$LOCAL" = 1 ] && [ "$CMP" = 1 ] && { echo "--local에서는 --compare-src를 쓸 수 없음 (백업 작업 VM 접속 수단 없음)" >&2; exit 2; }
MODE=PROD
if [ -n "$ID" ]; then [[ $ID == rehearsal-* ]] && MODE=TEST
else [ "$PREFIX" = rehearsal ] && MODE=TEST; fi

now() { date -u +%FT%TZ; }
die() { echo "중단: $*" >&2; [ -n "$T" ] && echo "FAIL $(now) $*" >> "$T"; exit 1; }
# 복구 DB VM에서 명령 실행: controller는 SSH, --local은 이 호스트의 bash (stdin은 그대로 전달)
rcv() { if [ "$LOCAL" = 1 ]; then bash -c "$1"; else ssh "$RCV" "$1"; fi; }
q()   { rcv "mariadb -N -e \"$1\""; }

# 0. 실행 전 조건
if [ "$LOCAL" = 1 ]; then
  [ "$(id -u)" = 0 ] || die "--local은 복구 DB VM root로 실행"
  [ "$(hostname -s)" = "$RCV_HOST" ] || die "--local은 $RCV_HOST에서만 실행 (현재 $(hostname -s))"
  case "$(readlink -f "$KEY")" in
    /dev/shm/*|/run/*) ;;
    *) echo "주의: 해독 키가 메모리 파일시스템 밖에 있음 — 끝나면 직접 지울 것: $KEY" >&2 ;;
  esac
else
  keys=$(ssh-add -l 2>/dev/null || true)
  [[ $keys == *seokpan-recovery* ]] || die "ssh-agent에 seokpan-recovery 키가 없음 (eval \"\$(ssh-agent -s)\" 후 ssh-add 단독 실행)"
fi
[ -f "$KEY" ] && [ -r "$KEY" ] || die "해독 키 없음: $KEY"
[ -z "$(find "$KEY" -perm /077)" ] || die "해독 키 권한이 넓음 (600 또는 400만 허용): $KEY"
grep -q '^AGE-SECRET-KEY-1' "$KEY" || die "해독 키 형식 이상 (age 개인키 줄 없음): $KEY"
command -v age >/dev/null || die "age 없음"
[ -r "$HASH" ] || die "비교 스크립트 없음: $HASH"
st=$(rcv "systemctl is-active mariadb; mariadb -N -e 'SELECT @@require_secure_transport'" | paste -sd' ') || true
[ "$st" = "active 1" ] || die "복구 DB 상태 이상: $st"

SRC_TSV=""
if [ "$CMP" = 1 ] && [ "$SELONLY" = 0 ]; then
  SRC_TSV=$(mktemp "$LOG"/src-XXXXXX.tsv)
  echo "원본 해시 받기 (백업 작업 VM root 비밀번호)"
  ssh "$BK" "bash -s -- --expect-tables $EXPECT_TABLES --defaults-extra-file=$SRC_CNF" < "$HASH" > "$SRC_TSV" \
    || die "원본 해시 실패 (table_hashes.sh 종료 코드 확인)"
fi

if [ "$SELONLY" = 1 ]; then T=/dev/null
else mkdir -p -m 700 "$LOG"
  RUN=restore-$(date -u +%Y%m%dT%H%M%SZ); T=$LOG/$RUN.times
  [ ! -e "$T" ] || { T=""; die "같은 이름의 실행 기록이 이미 있음 — 1초 뒤 다시 실행"; }; fi
echo "T0 복구 시작 $(now)" >> "$T"
echo "MODE $MODE" >> "$T"
if [ "$LOCAL" = 1 ]; then echo "HOST local $(hostname -s)" >> "$T"; else echo "HOST controller $(hostname -s)" >> "$T"; fi
echo "KEY $(sha256sum "$KEY" | cut -c1-12)" >> "$T"
[ "$MODE" = TEST ] && echo "예행(TEST) 복원 — 운영 판정에 쓰지 않음" | tee -a "$T"

# 1. 사본 선택 · SHA-256
if [ -n "$ID" ]; then
  CANDS=$ID
else
  LIST=$(rcv "find $C -maxdepth 1 -type f -name '*.sql.gz.age' -printf '%f\n'") || die "사본 목록 조회 실패 ($C)"
  CANDS=""
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    c=${f%.sql.gz.age}
    [[ $c == "$PREFIX"-* ]] || continue
    if valid_id "$c"; then CANDS+="$c"$'\n'
    else echo "형식 · 시각 이상 — 제외: $c" | tee -a "$T"; fi
  done <<< "$LIST"
  # 같은 접두사 + 고정 길이 UTC 시각이므로 이름 역순 = 데이터 시각 최신순
  CANDS=$(printf '%s' "$CANDS" | LC_ALL=C sort -r)
fi
[ -n "$CANDS" ] || die "$PREFIX 사본 없음 ($C)"
SEL=""
for c in $CANDS; do
  if rcv "cd $C && sha256sum -c --quiet $c.sql.gz.age.sha256" >/dev/null 2>&1; then SEL=$c; break; fi
  echo "사본 확인 실패 — 건너뜀: $c" | tee -a "$T"
done
[ -n "$SEL" ] || die "SHA-256이 맞는 사본 없음"
ID=$SEL
echo "ID $ID" >> "$T"
echo "T1 사본 확인 $(now)" >> "$T"
if [ "$SELONLY" = 1 ]; then echo "선택 $ID ($MODE)"; exit 0; fi

# 2. 복구 DB 상태
ns=$(q "SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name='stone_game'") || die "복구 DB 조회 실패"
if [ "$ns" != 0 ]; then
  [ "$REPLACE" = 1 ] || die "복구 DB에 stone_game이 이미 있음 — 지우고 진행하려면 --replace"
  rcv "mariadb -e 'DROP DATABASE stone_game'" || die "기존 stone_game 삭제 실패"
  echo "기존 stone_game 삭제 $(now)" >> "$T"
fi

# 3. 스트림 해독 · 가져오기
echo "T2 가져오기 시작 $(now)" >> "$T"
set +e
rcv "cat $C/$ID.sql.gz.age" \
  | age -d -i "$KEY" \
  | rcv "set -o pipefail; gunzip | mariadb --default-character-set=utf8mb4"
PS=("${PIPESTATUS[@]}")
set -e
[ "${PS[*]}" = "0 0 0" ] || die "가져오기 실패 (종료 코드 ${PS[*]}) — 원인 확인 후 --replace로 다시 실행"
echo "T3 가져오기 끝 $(now)" >> "$T"

# 4. 검증
rev=$(q "SELECT version_num FROM stone_game.alembic_version") || die "Revision 조회 실패"
nt=$(q "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='stone_game' AND table_type='BASE TABLE'") || die "테이블 수 조회 실패"
[ "$rev" = "$EXPECT_REV" ]   || die "Revision 다름: $rev"
[ "$nt"  = "$EXPECT_TABLES" ] || die "테이블 수 다름: $nt"
RCV_TSV=$LOG/"$ID"-rcv.tsv
rcv "bash -s -- --expect-tables $EXPECT_TABLES" < "$HASH" > "$RCV_TSV" \
  || die "복구 DB 해시 실패 (table_hashes.sh 종료 코드 확인)"
[ "$(grep -c '' "$RCV_TSV")" = "$EXPECT_TABLES" ] || die "해시 결과 줄 수 다름: $RCV_TSV"
if [ "$CMP" = 1 ]; then
  mv "$SRC_TSV" "$LOG"/"$ID"-src.tsv
  diff "$LOG"/"$ID"-src.tsv "$RCV_TSV" || die "원본과 다름"
  echo "원본 = 복구 DB (행 수 · 행 해시 · Schema 해시)" | tee -a "$T"
fi
na=$(q "SELECT COUNT(*) FROM mysql.user WHERE user IN ('db_admin','identity_svc','game_svc')") || die "복구 계정 조회 실패"
[ "$na" = "$EXPECT_ACCOUNTS" ] || echo "주의: 복구 계정 $na/$EXPECT_ACCOUNTS — #48 복구 계정 사전 준비 확인" | tee -a "$T"
echo "T4 검증 끝 $(now)" >> "$T"

# 요약
bt=${ID##*-}
bts=$(date -u -d "${bt:0:4}-${bt:4:2}-${bt:6:2}T${bt:9:2}:${bt:11:2}:${bt:13:2}Z" +%s)
t0=$(date -u -d "$(awk '/^T0/{print $NF}' "$T")" +%s)
t4=$(date -u -d "$(awk '/^T4/{print $NF}' "$T")" +%s)
echo "결과 $MODE · ID $ID · Revision $rev · 테이블 $nt · 계정 $na · T0→T4 $((t4-t0))초 · RPO $(( (t0-bts)/60 ))분 $(( (t0-bts)%60 ))초" | tee -a "$T"
echo "기록: $T"
