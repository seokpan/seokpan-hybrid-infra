#!/usr/bin/env bash
# Only local checks by default. This entry never invokes ROSA recreation.
set +x
set -euo pipefail
umask 077
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
exec "${CLOUD_READY_PYTHON:-python3}" -I "$root/tools/cloud-prestart/prestart.py" "$@"
