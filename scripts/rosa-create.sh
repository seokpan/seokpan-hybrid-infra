#!/usr/bin/env bash
# Recreate ROSA using private inputs and a pinned held-restoration driver.
set +x
set -euo pipefail
exec bash "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/rosa-lifecycle-entry.sh" create "$@"
