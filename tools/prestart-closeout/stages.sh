#!/usr/bin/env bash
# 생성 후 같은 설정을 사용하는 단계별 진입점입니다. 기본 실행은 없습니다.
set +x
set -euo pipefail
umask 077
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
source "$here/../session-paths.sh"
source "$here/inputs/session-closeout.sh"
action=${1:-help}
stage=${2:-target}
case "$stage" in single|shared|target) ;; *) echo STOP_KNOWN_STAGE_REQUIRED; exit 2;; esac
clockdir="$B_RUN/clocks"
mkdir -p -- "$clockdir"
clock="$clockdir/$stage.jsonl"
case "$action" in
  stage)
    # 최초 실제 배포 단계 진입부터 시각을 기록합니다. 생성은 호출하지 않습니다.
    extra=()
    if [[ "$stage" != single ]]; then
      : "${B_PRIOR_STAGE_BUSINESS_EVIDENCE:?이전 단계의 실제 정상 업무 결과 파일 필요}"
      extra+=(--business-evidence "$B_PRIOR_STAGE_BUSINESS_EVIDENCE")
    fi
    exec python3 -I "$B_MEASURE/stage_clock.py" entry --infra "$B_REPO_APP" \
      --work "$B_CLOUD_READY_WORK" --ledger "$clock" --stage "$stage" \
      -- --config "$B_READY_CONFIG" "${extra[@]}" --execute
    ;;
  observe)
    exec python3 -I "$B_MEASURE/stage_clock.py" observe --infra "$B_REPO_APP" \
      --work "$B_CLOUD_READY_WORK" --ledger "$clock" --stage "$stage" \
      --kubeconfig "$B_PERSONAL_KUBECONFIG" --oc "$(command -v oc)" --interval 2 --seconds 600
    ;;
  accept-external)
    exec python3 -I "$B_MEASURE/stage_clock.py" external --ledger "$clock" \
      --result "$B_RUN/external-results/$stage.json"
    ;;
  after-sync)
    exec python3 -I "$B_REPO_APP/tools/cloud-prestart/prestart.py" --config "$B_READY_CONFIG" \
      --work "$B_CLOUD_READY_WORK" --stage "$stage" --after-sync --execute
    ;;
  db-baseline|db-rolling)
    scenario=${action#db-}
    exec python3 -I "$here/pod_db_observer.py" sample \
      --config "$B_BOUND_OUT/db-$scenario.json" --out "$B_BOUND_OUT/db-$scenario.jsonl" --execute
    ;;
  hpa-plan)
    exec python3 -I "$B_HPA/control.py" prepare --config "$B_HPA_CONFIG" --work "$B_HPA_WORK" --execute
    ;;
  hpa-activate)
    # 상태 전달을 먼저 시작하고 같은 config/plan으로 관측을 활성화합니다.
    [[ ! -e "$B_HPA_WORK/publisher-started.json" ]] || { echo STOP_PUBLISHER_ALREADY_ATTEMPTED; exit 2; }
    printf '{"started":true}\n' > "$B_HPA_WORK/publisher-started.json"
    nohup python3 -I "$here/readonly_handoff.py" publish --settings "$B_HANDOFF_SETTINGS" \
      > "$B_HPA_WORK/publisher.private.log" 2>&1 < /dev/null &
    publisher_pid=$!
    for index in {1..20}; do
      [[ ! -f "$B_HPA_WORK/publisher-ready.json" ]] || break
      kill -0 "$publisher_pid" 2>/dev/null || { echo STOP_METADATA_PUBLISHER_NOT_READY; exit 2; }
      sleep 0.5
    done
    [[ -f "$B_HPA_WORK/publisher-ready.json" ]] || { echo STOP_METADATA_PUBLISHER_TIMEOUT; exit 2; }
    exec python3 -I "$B_HPA/run-controller.py" activate --config "$B_HPA_CONFIG" --work "$B_HPA_WORK" --execute
    ;;
  hpa-stop)
    exec python3 -I "$B_HPA/run-controller.py" stop --config "$B_HPA_CONFIG" --work "$B_HPA_WORK" --execute
    ;;
  hpa-reconcile)
    exec python3 -I "$B_HPA/control.py" reconcile --config "$B_HPA_CONFIG" --work "$B_HPA_WORK" --execute
    ;;
  hpa-finish)
    : "${B_STOPPED_LOAD_RECEIPT:?D가 전달한 같은 Run의 종료 결과 경로 필요}"
    exec python3 -I "$B_HPA/run-controller.py" finish --config "$B_HPA_CONFIG" \
      --work "$B_HPA_WORK" --load-receipt "$B_STOPPED_LOAD_RECEIPT" --execute
    ;;
  hpa-verify-restore)
    exec python3 -I "$B_HPA/control.py" verify-restore --config "$B_HPA_CONFIG" --work "$B_HPA_WORK" --execute
    ;;
  *)
    printf '%s\n' 'PREPARED_STAGE_ENTRY_ONLY: stage / observe / after-sync / accept-external / db-baseline / db-rolling / hpa-plan / hpa-activate / hpa-stop / hpa-reconcile / hpa-finish / hpa-verify-restore'
    ;;
esac
