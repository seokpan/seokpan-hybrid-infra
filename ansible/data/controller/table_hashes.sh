#!/bin/bash
# 사용: ssh <host> 'bash -s -- [mariadb 접속 인자]' < table_hashes.sh
# 출력: 테이블 · 행 수 · 행 해시 · Schema 해시 (앞 16자리)
set -o pipefail
M=(mariadb "$@" --default-character-set=utf8mb4 -N)
for t in $("${M[@]}" -e "SELECT table_name FROM information_schema.tables WHERE table_schema='stone_game' AND table_type='BASE TABLE' ORDER BY table_name"); do
  pk=$("${M[@]}" -e "SELECT GROUP_CONCAT(column_name ORDER BY ordinal_position) FROM information_schema.key_column_usage WHERE table_schema='stone_game' AND table_name='$t' AND constraint_name='PRIMARY'")
  printf '%s\t%s\t%s\t%s\n' "$t" \
    "$("${M[@]}" -e "SELECT COUNT(*) FROM stone_game.\`$t\`")" \
    "$("${M[@]}" --raw -e "SELECT * FROM stone_game.\`$t\` ORDER BY $pk" | sha256sum | cut -c1-16)" \
    "$("${M[@]}" --raw -e "SHOW CREATE TABLE stone_game.\`$t\`" | sha256sum | cut -c1-16)"
done
