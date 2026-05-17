#!/usr/bin/env bash

# Source this file to make the repository importable for direct script entrypoints.
SCRIPT_DIR="$(cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  echo "Environment configured for this subprocess only."
  echo "Run 'source scripts/setup.sh' to persist HALF_LINAC_ROOT and PYTHONPATH in your shell."
fi
