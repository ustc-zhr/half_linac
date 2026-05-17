#!/usr/bin/env bash

SCRIPT_DIR="$(cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_PARENT="$(dirname "$REPO_ROOT")"

export HALF_LINAC_ROOT="$REPO_ROOT"
export halflinac_ROOT="$REPO_ROOT"

case ":${PYTHONPATH:-}:" in
  *":$REPO_PARENT:"*) ;;
  *)
    if [[ -n "${PYTHONPATH:-}" ]]; then
      export PYTHONPATH="$REPO_PARENT:$PYTHONPATH"
    else
      export PYTHONPATH="$REPO_PARENT"
    fi
    ;;
esac

case ":$PATH:" in
  *":$REPO_ROOT:"*) ;;
  *) export PATH="$PATH:$REPO_ROOT" ;;
esac
