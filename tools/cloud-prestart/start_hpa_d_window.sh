(
  # 실행 위치: Controller(ansible)의 cyj 터미널
  # B의 PASS_D_START_WINDOW_BOUND_SAME_HPA_GO 확인 후 실행합니다.
  set +x
  set -euo pipefail
  umask 077
  [[ "$(id -un)" == cyj ]] || exit 1
  command -v tmux >/dev/null

  /usr/bin/python3.9 -I - <<'PY'
import datetime as dt
import json
import os
from pathlib import Path

rid = 'hpa-20261020-124957'
shared = Path('/var/tmp/seokpan-hpa-handoff')/rid/'d-start-window'
ready = json.loads((shared/'ready.json').read_text())
assert ready['status'] == 'PASS_D_START_WINDOW_BOUND_SAME_HPA_GO', 'B_TIME_COPY_REQUIRED'
plan = json.loads((shared/'plan.json').read_text())
assert plan['run_id'] == rid and plan['control_sha256'] == ready['config_sha256'], 'PLAN_READY_HASH_MISMATCH'
end = dt.datetime.fromisoformat(ready['observation_not_after'])
assert (end-dt.datetime.now(dt.timezone.utc)).total_seconds() >= 2400, 'TIME_COPY_START_WINDOW_CLOSED'
assert not (shared/'state/STOP.json').exists(), 'B_STOP_PRESENT'

for name in (rid, rid+'-attempt02', rid+'-attempt03'):
    old = Path.home()/'hpa-load/out'/name
    requests = old/'requests.jsonl'
    assert not requests.exists() or requests.stat().st_size == 0, 'PREVIOUS_LOAD_REQUESTS_REQUIRE_RECONCILIATION'
    events = old/'events.jsonl'
    if events.exists():
        assert events.stat().st_size < 1048576, 'BOUNDED_EVENTS_REQUIRED'
        for line in events.read_text().splitlines():
            assert json.loads(line).get('phase', 0) == 0, 'PREVIOUS_LOAD_PHASE_REQUIRE_RECONCILIATION'

for proc in Path('/proc').iterdir():
    if not proc.name.isdigit():
        continue
    try:
        if proc.stat().st_uid != os.getuid():
            continue
        argv = (proc/'cmdline').read_bytes().split(b'\0')
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        continue
    if any(b'load.mjs' in a or b'd_generator.py' in a for a in argv):
        if any(rid.encode() in a for a in argv):
            raise SystemExit('STOP_PREVIOUS_GENERATOR_STILL_RUNNING')
print('PASS_D_TIME_COPY_PREFLIGHT')
PY

  taskScript="$HOME/hpa-load/out/hpa-20261020-124957-attempt04.sh"
  [[ ! -e "$taskScript" ]] || {
    echo STOP_EXISTING_ATTEMPT03_PRESERVED
    exit 1
  }

  cat >"$taskScript" <<'SH'
#!/usr/bin/env bash
set +x
set -uo pipefail
umask 077
taskOut="$HOME/hpa-load/out/hpa-20261020-124957-attempt04"
taskReturnRoot="/var/tmp/seokpan-hpa-handoff/return/cyj/d-start-window"
taskReturn="$taskReturnRoot/hpa-20261020-124957"
mkdir -p -- "$taskReturnRoot"

# 원본 plan을 다시 실행하지 않고 시간 사본을 사용합니다.
/usr/bin/python3.9 -I /var/tmp/seokpan-hpa-handoff/d_generator.py \
  --preparation /var/tmp/seokpan-hpa-handoff/preparation.json \
  --plan /var/tmp/seokpan-hpa-handoff/hpa-20261020-124957/d-start-window/plan.json \
  --work "$taskOut" --return-root "$taskReturnRoot" --execute
taskExit=$?

# 결과 결합에 필요한 해시 목록도 읽기 권한을 붙여 반환합니다.
if [[ -f "$taskOut/SHA256SUMS.json" && -d "$taskReturn" ]]; then
  taskTmp=$(mktemp "$taskReturn/.manifest-XXXXXXXX")
  cat -- "$taskOut/SHA256SUMS.json" >"$taskTmp"
  chmod 600 "$taskTmp"
  setfacl -m u:jth:r--,m::r-- "$taskTmp" || exit 2
  if [[ ! -e "$taskReturn/SHA256SUMS.json" ]]; then
    mv -T --no-clobber -- "$taskTmp" "$taskReturn/SHA256SUMS.json"
  else
    cmp -- "$taskTmp" "$taskReturn/SHA256SUMS.json" || exit 2
    rm -- "$taskTmp"
  fi
fi
printf 'GENERATOR_EXIT=%s\nRESULT_DIRECTORY=%s\n' "$taskExit" "$taskReturn"
exit "$taskExit"
SH
  chmod 700 "$taskScript"

  # SSH가 끊겨도 계속 실행되도록 tmux에서 시작합니다.
  tmux new-session -d -s seokpan-hpa-final \
    "bash '$taskScript' > '$taskScript.log' 2>&1"
  printf 'STARTED_TMUX=seokpan-hpa-final\nLOG=%s.log\n' "$taskScript"
)
