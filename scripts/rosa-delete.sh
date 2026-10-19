#!/usr/bin/env bash
# Apply an already reviewed ROSA-only delete Plan after explicit approval.
set +x
set -euo pipefail
exec bash "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/rosa-lifecycle-entry.sh" delete "$@"
