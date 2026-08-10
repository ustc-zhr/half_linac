#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

IOC_ENTRY="$(
  python3 -c 'from half_linac.src.shared.machine_profile.loader import repo_root; print(repo_root() / "src/softIOC/mainIOC.py")'
)"

exec python3 "$IOC_ENTRY"
