#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_NAME="half_linac"
WITH_MODEL_DEPS=1
RUN_CHECK=0

usage() {
  cat <<'EOF'
Usage: bash scripts/install_env.sh [--core-only] [--check]

Create or update the half_linac Conda environment from environment.yml. The
default installation includes Python sdds and checks for the external elegant
executable used by VM and model-backend workflows.

Options:
  --core-only
           Skip sdds installation and the elegant check. Use this only for
           workflows that connect to an existing IOC without model calculations.
  --sdds   Deprecated compatibility option; model dependencies are now enabled
           by default.
  --check  Run the repository static checks after installation.
  -h, --help
           Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --core-only)
      WITH_MODEL_DEPS=0
      ;;
    --sdds)
      WITH_MODEL_DEPS=1
      ;;
    --check)
      RUN_CHECK=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if ! command -v conda >/dev/null 2>&1; then
  echo "conda was not found. Install Miniconda/Anaconda first, then rerun this script." >&2
  exit 1
fi

if conda env list | awk '{print $1}' | grep -Fxq "$ENV_NAME"; then
  echo "Updating Conda environment: $ENV_NAME"
  conda env update -n "$ENV_NAME" -f "$REPO_ROOT/environment.yml"
else
  echo "Creating Conda environment: $ENV_NAME"
  conda env create -f "$REPO_ROOT/environment.yml"
fi

if [[ "$WITH_MODEL_DEPS" -eq 1 ]]; then
  echo "Installing Python sdds binding for VM and model workflows."
  conda install -y -n "$ENV_NAME" soliday::sdds
fi

echo "Checking basic Python dependencies."
conda run -n "$ENV_NAME" python3 - <<'PY'
import sys
assert sys.version_info >= (3, 10), sys.version
import PyQt5
import epics
import h5py
import matplotlib
import numpy
import pandas
import pyqtgraph
import scipy
import skimage
print("Basic Python environment OK:", sys.version)
PY

if [[ "$WITH_MODEL_DEPS" -eq 1 ]]; then
  conda run -n "$ENV_NAME" python3 - <<'PY'
import sdds
print("sdds OK:", getattr(sdds, "__file__", "built-in"))
PY

  if ! command -v elegant >/dev/null 2>&1; then
    cat >&2 <<EOF
elegant was not found in PATH. VM and model-backend workflows require both
elegant and Python sdds.

Install or load elegant, then rerun this script. See:
  $REPO_ROOT/docs/getting_started/ELEGANT_INSTALL.md

For an IOC-only environment without model calculations, rerun with --core-only.
EOF
    exit 1
  fi
  echo "elegant OK: $(command -v elegant)"
fi

if [[ "$RUN_CHECK" -eq 1 ]]; then
  conda run -n "$ENV_NAME" bash "$REPO_ROOT/scripts/check.sh"
fi

cat <<EOF

Environment is ready.

Activate it with:
  conda activate $ENV_NAME

Start the Control Room GUI with:
  bash scripts/runMe
EOF
