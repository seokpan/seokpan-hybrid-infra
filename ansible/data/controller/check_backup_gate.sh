#!/usr/bin/env bash
# 작성자: 김상희
# 작성 날짜: 2026/10/08
# RDS 원본 첫 OK 회차 운영 Gate 확인 (PR #53 승인 조건, infra #44 10단계 · #48)
#
# 확인하는 것
#   1. last-success 3개 값이 한 시각을 가리키는가: Backup ID 시각 = T_DUMP(dump=) = last-success 1열
#   2. 같은 사본의 SHA-256: history 기록 = last-success = S3 객체 = 복구 DB VM 사본 · .sha256 파일
#   3. 최근 OK 회차 간격이 15분(± 허용 오차)인가
#   4. 로컬 확보 지연(T_COPY - T_DUMP)과 지금 기준 데이터 나이(RPO 30분 안인가)
#
# - 조회만 한다. 백업 작업 VM · S3 · 복구 DB VM 어디에도 쓰지 않는다.
# - 백업 작업 VM은 비밀번호 SSH라 한 번만 접속해 필요한 기록을 모두 읽는다.
# - S3 객체는 암호문을 내려받아 해시만 계산한다(파일로 저장하지 않음).
# - 출력에 계정 번호 · 비밀값은 없다. Backup ID · 해시 앞 12자리 · 시각만 나온다.
#   AWS 오류 메시지(stderr)에는 ARN이 섞일 수 있어, 12자리 숫자를 <계정>으로 가려서 내보낸다.
#
# 사용 (controller, ksh, personal MFA 세션 + ssh-agent에 seokpan-recovery 키)
#   source ~/work/seokpan-hybrid-infra/scripts/tf-session.sh personal
#   eval "$(ssh-agent)"; ssh-add ~/.ssh/seokpan-recovery_ed25519     # 암호 문구 — 단독 실행
#   bash ~/recovery/check_backup_gate.sh            # 최근 OK 2회 = 간격 1개 (Timer 회차 2번 뒤)
#   MIN_OK=5 bash ~/recovery/check_backup_gate.sh   # 최근 OK 5회 = 간격 4개 = 1시간
#
# 종료 코드: 0 = 모두 OK, 1 = NG가 하나 이상,
#            2 = 판정 불가 (도구 · 자격증명 · 접속 실패, MIN_OK가 2 미만, OK 회차가 아직 MIN_OK개 안 됨)

set -uo pipefail
exec 2> >(sed -u -E 's/[0-9]{12}/<계정>/g' >&2)   # AccessDenied 등 오류 메시지의 계정 번호 가리기

REGION="ap-northeast-2"
BACKUP_VM="root@192.168.52.50"
RECOVERY="seokpan-recovery"
ST=/srv/seokpan-hybrid-backup/state
COPIES=/srv/seokpan-hybrid-recovery/copies
MIN_OK=${MIN_OK:-2}          # 간격을 보려면 OK 회차가 최소 2개 (간격 수 = MIN_OK - 1)
INTERVAL=900                 # 15분
TOL=${TOL:-60}               # 간격 허용 오차(초) — 사전 검사 소요만큼 덤프 시작이 밀림
COPY_MAX=${COPY_MAX:-300}    # 로컬 확보 지연 상한(초) — 넘으면 NG (RPO 여유 확인용)
RPO_MAX=1800                 # 30분

NG=0
ok() { printf '  OK  %s\n' "$1"; }
ng() { printf '  NG  %s\n' "$1"; NG=$((NG + 1)); }
info() { printf '  --  %s\n' "$1"; }
epoch() { # 빈 값 · 잘못된 값은 빈 문자열 (date -d ""는 오늘 00:00이 되므로 먼저 막음)
  [ -n "${1:-}" ] || { echo ""; return; }
  date -u -d "$1" +%s 2>/dev/null || echo ""
}
id_epoch() { # periodic-YYYYMMDDTHHMMSSZ → epoch
  local s=${1#periodic-}
  [[ $s =~ ^([0-9]{4})([0-9]{2})([0-9]{2})T([0-9]{2})([0-9]{2})([0-9]{2})Z$ ]] || { echo ""; return; }
  epoch "${BASH_REMATCH[1]}-${BASH_REMATCH[2]}-${BASH_REMATCH[3]}T${BASH_REMATCH[4]}:${BASH_REMATCH[5]}:${BASH_REMATCH[6]}Z"
}
field() { # field <detail 문자열> <키> → 값 (예: dump=2026-...Z)
  tr ' ' '\n' <<<"$1" | sed -n "s/^$2=//p" | head -n 1
}

[[ $MIN_OK =~ ^[0-9]+$ ]] && [ "$MIN_OK" -ge 2 ] || { echo "MIN_OK는 2 이상 정수 (간격을 보려면 회차 2개 필요): $MIN_OK" >&2; exit 2; }
for t in aws ssh sha256sum date; do
  command -v "$t" >/dev/null || { echo "필요한 도구 없음: $t" >&2; exit 2; }
done
ACCT=$(aws --region "$REGION" sts get-caller-identity --query Account --output text 2>/dev/null) \
  || { echo "AWS 자격증명 없음 — tf-session.sh personal 먼저" >&2; exit 2; }
BUCKET="seokpan-fnd-backup-${ACCT}"
NOW=$(date -u +%s)

echo "== 백업 운영 Gate 확인 $(date -u +%FT%TZ)"

# ---------------------------------------------------------------- 백업 작업 VM 기록 (SSH 1회)
echo "[0] 백업 작업 VM 기록 읽기 (root 비밀번호 1회)"
RAW=$(ssh -o ConnectTimeout=10 "$BACKUP_VM" \
  "cat $ST/last-success 2>/dev/null || echo NO-LAST-SUCCESS; echo '#HISTORY'; tail -n 200 $ST/history.tsv 2>/dev/null; echo '#TIMER'; systemctl is-enabled seokpan-hybrid-backup.timer 2>&1; systemctl is-active seokpan-hybrid-backup.timer 2>&1; test -e /etc/seokpan-hybrid-backup/paused && echo PAUSED || echo NOT-PAUSED") \
  || { echo "백업 작업 VM 접속 실패" >&2; exit 2; }
LAST=$(sed -n '1p' <<<"$RAW")
HIST=$(sed -n '/^#HISTORY$/,/^#TIMER$/p' <<<"$RAW" | sed '1d;$d')
TIMER=$(sed -n '/^#TIMER$/,$p' <<<"$RAW" | sed '1d' | paste -sd' ')

[ "$TIMER" = "enabled active NOT-PAUSED" ] && ok "Timer enabled · active · paused 없음" \
  || ng "Timer 상태: $TIMER (기대: enabled active NOT-PAUSED)"

# ---------------------------------------------------------------- 1. 한 시각
echo "[1] last-success · Backup ID · T_DUMP가 같은 시각인가"
if [ "$LAST" = "NO-LAST-SUCCESS" ]; then
  echo "  --  last-success 없음 — OK 회차가 아직 없음 (history.tsv의 FAIL 이유 확인)"
  echo "== 결과: 판정 불가"; exit 2
fi
IFS=$'\t' read -r L_TIME L_ID L_SUM <<<"$LAST"
info "last-success: $L_ID · ${L_SUM:0:12}"
[[ $L_ID == periodic-* ]] && ok "운영 사본(periodic-*)" || ng "운영 사본 이름이 아님: $L_ID"

L_LINE=$(awk -F'\t' -v id="$L_ID" '$2==id && $3=="OK"' <<<"$HIST" | tail -n 1)
if [ -z "$L_LINE" ]; then
  ng "history.tsv에 $L_ID 의 OK 줄이 없음"
else
  L_DET=$(cut -f4 <<<"$L_LINE")
  T_DUMP=$(field "$L_DET" dump)
  E_ID=$(id_epoch "$L_ID"); E_DUMP=$(epoch "$T_DUMP"); E_LAST=$(epoch "$L_TIME")
  [ -n "$E_ID" ] && [ -n "$E_DUMP" ] && [ "$E_ID" = "$E_DUMP" ] && [ "$E_DUMP" = "$E_LAST" ] \
    && ok "ID 시각 = T_DUMP = last-success ($T_DUMP)" \
    || ng "시각 불일치 — ID:$L_ID / dump:$T_DUMP / last-success:$L_TIME"
  [ "$(field "$L_DET" sha)" = "$L_SUM" ] && ok "history sha = last-success sha" || ng "history sha ≠ last-success sha"
  S3F=$(field "$L_DET" s3); CPF=$(field "$L_DET" copy)
  [[ $S3F == ok@* ]] && ok "S3 업로드 ok" || ng "S3 기록: $S3F"
  [[ $CPF == ok/s3@* ]] && ok "복구 DB VM 확보 = S3 객체 경유" || ng "확보 기록: $CPF (기대: ok/s3@…)"
fi

# ---------------------------------------------------------------- 2. 같은 사본
echo "[2] SHA-256: S3 객체 · 복구 DB VM 사본"
S3_SUM=$(aws --region "$REGION" s3 cp "s3://$BUCKET/periodic/$L_ID.sql.gz.age" - 2>/dev/null | sha256sum | cut -d' ' -f1)
[ "$S3_SUM" = "$L_SUM" ] && ok "S3 객체 = last-success" || ng "S3 객체 해시 다름 또는 조회 실패 (${S3_SUM:0:12})"

# 두 값은 라벨을 붙여 따로 읽는다 (한쪽이 없어도 줄이 밀리지 않도록)
RC=$(ssh -o BatchMode=yes -o ConnectTimeout=10 "$RECOVERY" \
  "echo FILE \$(cut -d' ' -f1 $COPIES/$L_ID.sql.gz.age.sha256 2>/dev/null); echo CALC \$(sha256sum $COPIES/$L_ID.sql.gz.age 2>/dev/null | cut -d' ' -f1)") \
  || { ng "복구 DB VM 접속 실패 (ssh-agent 확인)"; RC=""; }
RC_FILE=$(awk '$1=="FILE"{print $2}' <<<"$RC"); RC_CALC=$(awk '$1=="CALC"{print $2}' <<<"$RC")
[ -n "$RC_FILE" ] && [ "$RC_FILE" = "$L_SUM" ] && ok "복구 VM .sha256 파일 = last-success" || ng "복구 VM .sha256 다름 또는 없음"
[ -n "$RC_CALC" ] && [ "$RC_CALC" = "$L_SUM" ] && ok "복구 VM 사본 재계산 = last-success" || ng "복구 VM 사본 재계산 다름 또는 없음"

# ---------------------------------------------------------------- 3. 간격
echo "[3] 최근 OK 회차 간격 (기대 ${INTERVAL}초 ± ${TOL}초)"
mapfile -t OKS < <(awk -F'\t' '$3=="OK" && $2 ~ /^periodic-/' <<<"$HIST" | tail -n "$MIN_OK")
if [ "${#OKS[@]}" -lt "$MIN_OK" ]; then
  echo "  --  OK 회차 ${#OKS[@]}개 — ${MIN_OK}개 이상 쌓인 뒤 다시 실행"
  echo "== 결과: 판정 불가 (NG ${NG}개는 위 항목 참고)"; exit 2
else
  prev=""
  for line in "${OKS[@]}"; do
    id=$(cut -f2 <<<"$line"); e=$(id_epoch "$id")
    if [ -z "$e" ]; then ng "$id 이름에서 시각을 읽지 못함"; prev=""; continue; fi
    if [ -n "$prev" ]; then
      d=$((e - prev))
      if [ "$d" -ge $((INTERVAL - TOL)) ] && [ "$d" -le $((INTERVAL + TOL)) ]; then ok "$id 앞 회차와 ${d}초"
      else ng "$id 앞 회차와 ${d}초 (중간 회차 실패 · 생략 여부 history 확인)"; fi
    fi
    prev=$e
  done
fi
if [ "${#OKS[@]}" -gt 0 ]; then
  SINCE=$(cut -f1 <<<"${OKS[0]}")     # 구간 첫 OK 회차의 시도 시작 시각 (ISO라 문자열 비교로 순서가 맞음)
  OTHER=$(awk -F'\t' -v s="$SINCE" '$1 >= s && $3 != "OK" {print $3}' <<<"$HIST" | sort | uniq -c | awk '{printf "%s %s개 ", $2, $1}')
  [ -z "$OTHER" ] && ok "이 구간에 OK 아닌 회차 없음" || info "이 구간의 OK 아닌 회차: $OTHER"
fi

# ---------------------------------------------------------------- 4. 지연 · 데이터 나이
echo "[4] 로컬 확보 지연 · 데이터 나이"
if [ -n "${L_DET:-}" ] && [ -n "${E_DUMP:-}" ] && [ -n "$(epoch "${CPF#*@}")" ]; then
  T_COPY=${CPF#*@}
  CD=$(( $(epoch "$T_COPY") - E_DUMP ))
  [ "$CD" -le "$COPY_MAX" ] && ok "로컬 확보 지연 ${CD}초 (상한 ${COPY_MAX}초)" || ng "로컬 확보 지연 ${CD}초 > ${COPY_MAX}초"
  AGE=$((NOW - E_DUMP))
  [ "$AGE" -le "$RPO_MAX" ] && ok "지금 기준 데이터 나이 $((AGE / 60))분 $((AGE % 60))초 (RPO 30분 안)" \
    || ng "지금 기준 데이터 나이 $((AGE / 60))분 — 30분 초과"
else
  ng "덤프 · 확보 시각을 읽지 못해 지연 · 데이터 나이를 계산하지 못함"
fi

echo "== 결과: NG ${NG}개"
[ "$NG" -eq 0 ]
