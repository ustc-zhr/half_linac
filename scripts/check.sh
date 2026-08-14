#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

python3 -m compileall "$HALF_LINAC_ROOT/src"
python3 -m py_compile "$HALF_LINAC_ROOT/scripts/smoke_gui_layouts.py"
bash -n "$HALF_LINAC_ROOT/scripts/common.sh"
bash -n "$HALF_LINAC_ROOT/scripts/setup.sh"
bash -n "$HALF_LINAC_ROOT/scripts/install_env.sh"
bash -n "$HALF_LINAC_ROOT/scripts/runMe"
bash -n "$HALF_LINAC_ROOT/scripts/check.sh"
bash -n "$HALF_LINAC_ROOT/scripts/check_irfel_vm.sh"
bash -n "$HALF_LINAC_ROOT/scripts/check_machine.sh"
bash -n "$HALF_LINAC_ROOT/scripts/smoke_irfel_vm_runtime.sh"
bash -n "$HALF_LINAC_ROOT/scripts/build_ioc.sh"
bash -n "$HALF_LINAC_ROOT/scripts/configure_softioc.sh"
bash -n "$HALF_LINAC_ROOT/scripts/start_vm.sh"
bash -n "$HALF_LINAC_ROOT/scripts/start_ioc_manager.sh"
bash -n "$HALF_LINAC_ROOT/src/softIOC/halflinac/runMe"
bash -n "$HALF_LINAC_ROOT/src/softIOC/irfel/runMe"
bash "$HALF_LINAC_ROOT/scripts/check_machine.sh" half
bash "$HALF_LINAC_ROOT/scripts/check_machine.sh" irfel
bash "$HALF_LINAC_ROOT/scripts/check_irfel_vm.sh"

echo "Static checks passed."
