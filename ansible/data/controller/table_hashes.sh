#!/bin/bash
# 사용: ssh <host> 'bash -s -- [--expect-tables N] [mariadb 접속 인자]' < table_hashes.sh
# 출력: 테이블 · 행 수 · 행 해시 · Schema 해시 (앞 16자리)
# 조회 하나라도 실패하거나 값이 비정상이면 stderr에 이유를 쓰고 종료 코드 1
set -euo pipefail

EXPECT=""
if [ "${1:-}" = --expect-tables ]; then
  [[ ${2:-} =~ ^[0-9]+$ ]] || { echo "table_hashes: --expect-tables 값 이상: ${2:-}" >&2; exit 2; }
  EXPECT=$2; shift 2
fi
M=(mariadb "$@" --default-character-set=utf8mb4 -N)
fail() { echo "table_hashes: $*" >&2; exit 1; }

# 결과를 변수에 받은 뒤 확인한다 (printf 안의 $(...) 실패는 종료 코드로 전달되지 않음)
tables=$("${M[@]}" -e "SELECT table_name FROM information_schema.tables WHERE table_schema='stone_game' AND table_type='BASE TABLE' ORDER BY table_name") \
  || fail "테이블 목록 조회 실패"
[ -n "$tables" ] || fail "stone_game 테이블 없음"

n=0
for t in $tables; do
  [[ $t =~ ^[A-Za-z0-9_]+$ ]] || fail "예상 밖 테이블 이름: $t"
  pk=$("${M[@]}" -e "SELECT GROUP_CONCAT(column_name ORDER BY ordinal_position) FROM information_schema.key_column_usage WHERE table_schema='stone_game' AND table_name='$t' AND constraint_name='PRIMARY'") \
    || fail "$t PK 조회 실패"
  [ -n "$pk" ] && [ "$pk" != NULL ] || fail "$t PK 없음"
  cnt=$("${M[@]}" -e "SELECT COUNT(*) FROM stone_game.\`$t\`") || fail "$t 행 수 조회 실패"
  [[ $cnt =~ ^[0-9]+$ ]] || fail "$t 행 수 값 이상: $cnt"
  rh=$("${M[@]}" --raw -e "SELECT * FROM stone_game.\`$t\` ORDER BY $pk" | sha256sum) || fail "$t 행 해시 실패"
  sh=$("${M[@]}" --raw -e "SHOW CREATE TABLE stone_game.\`$t\`" | sha256sum) || fail "$t Schema 해시 실패"
  printf '%s\t%s\t%s\t%s\n' "$t" "$cnt" "${rh:0:16}" "${sh:0:16}"
  n=$((n + 1))
done
[ -z "$EXPECT" ] || [ "$n" = "$EXPECT" ] || fail "테이블 수 $n (기대 $EXPECT)"
