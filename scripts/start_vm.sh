#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

VM_ENTRY="$(
  python3 -c 'from half_linac.src.shared.machine_profile import resolve_machine_runtime; print(resolve_machine_runtime().vm.manager_entrypoint)'
)"
VM_DIR="$(dirname "$VM_ENTRY")"
cd "$VM_DIR"

exec python3 "$VM_ENTRY"
