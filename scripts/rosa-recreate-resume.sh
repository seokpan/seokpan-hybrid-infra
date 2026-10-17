#!/usr/bin/env bash
# Local input gate, then existing reviewed create/restore. No Foundation/NAT/EIP/RDS mutation commands.
set +x
set -euo pipefail
umask 077
[[ $(id -un) == jth ]] || { echo STOP_USE_CONTROLLER_JTH; exit 1; }
scripts=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
root="$HOME/.local/share/seokpan-rosa-plan-inputs-5psj2l62"
work=$(mktemp -d "$root/recreate-readiness-XXXXXXXX")
python3 -I "$scripts/rosa-recreate-readiness.py" "$@" --out "$work"
mapfile -t dispatch < <(python3 -I -c 'import json,sys;x=json.load(open(sys.argv[1]));print(x["action"]);print(x["config"])' "$work/dispatch.json")
[[ ${#dispatch[@]} == 2 ]] || { echo STOP_RECREATE_DISPATCH; exit 1; }
action=${dispatch[0]}
config=${dispatch[1]}
[[ "$action" == create || "$action" == restore ]] || exit 1
printf 'DISPATCH_EXISTING_ROSA_ENTRY: %s\n' "$action"
bash "$scripts/rosa-$action.sh" --config "$config"
