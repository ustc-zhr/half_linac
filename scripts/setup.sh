#!/usr/bin/env bash

# Source this file only if you want repo env vars in your current shell.
# Wrapper scripts and the main Python entrypoints already self-bootstrap.
SCRIPT_DIR="$(cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  echo "Environment configured for this subprocess only."
  echo "Run 'source scripts/setup.sh' only if you want HALF_LINAC_ROOT and PYTHONPATH in your current shell."
fi
