#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
exec python3 "$HALF_LINAC_ROOT/src/apps/magnet_cycle/main.py" "$@"
