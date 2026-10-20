#!/usr/bin/env bash
# 경로는 settings.json에서 읽습니다. 실제 생성은 이 진입점에 없습니다.
set +x
set -euo pipefail
umask 077
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
action=${1:-prepare}
case "$action" in
  prepare)
    exec python3 -I "$here/finish_prestart.py" --settings "$here/settings.json"
    ;;
  *)
    printf '%s\n' 'STOP_PREPARATION_ENTRY_ONLY_NO_RECREATE'
    exit 2
    ;;
esac
