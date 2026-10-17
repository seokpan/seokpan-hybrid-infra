#!/usr/bin/env bash
set +x
set -euo pipefail
umask 077
scripts=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec python3 "$scripts/rosa-held-restoration.py" "$@"
