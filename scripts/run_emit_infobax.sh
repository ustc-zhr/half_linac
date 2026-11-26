#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"
export MPLCONFIGDIR="${MPLCONFIGDIR:-/tmp/half_linac_mpl_${UID}}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
exec python3 -m half_linac.src.apps.emit_measure.infobax_launcher "$@"
