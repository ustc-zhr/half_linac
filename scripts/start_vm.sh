#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

VM_DIR="$HALF_LINAC_ROOT/src/virtual_machine/half_elegant"
cd "$VM_DIR"

exec python3 "$VM_DIR/start_VM.py"
