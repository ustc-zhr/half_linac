#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

IOC_DIR="$(
  python3 -c 'from half_linac.src.shared.machine_profile import resolve_machine_runtime; print(resolve_machine_runtime().softioc.root)'
)"
RELEASE_FILE="$IOC_DIR/configure/RELEASE"

read_release_var() {
  local file="$1"
  local name="$2"

  awk -F'=' -v name="$name" '
    $0 ~ "^[[:space:]]*" name "[[:space:]]*=" {
      sub(/^[[:space:]]*[^=]+=[[:space:]]*/, "", $0)
      sub(/[[:space:]]*#.*/, "", $0)
      gsub(/[[:space:]]+$/, "", $0)
      print
      exit
    }
  ' "$file"
}

EPICS_BASE_VALUE="${EPICS_BASE:-$(read_release_var "$RELEASE_FILE" "EPICS_BASE")}"
if [[ -z "$EPICS_BASE_VALUE" ]]; then
  echo "EPICS_BASE is not set and could not be read from $RELEASE_FILE" >&2
  exit 1
fi

if [[ ! -f "$EPICS_BASE_VALUE/configure/CONFIG_BASE" ]]; then
  echo "EPICS_BASE does not point to a valid EPICS installation: $EPICS_BASE_VALUE" >&2
  exit 1
fi

export EPICS_BASE="$EPICS_BASE_VALUE"
if [[ -x "$EPICS_BASE/startup/EpicsHostArch" ]]; then
  export EPICS_HOST_ARCH="$("$EPICS_BASE/startup/EpicsHostArch")"
fi

echo "Rebuilding softIOC in $IOC_DIR"
echo "Using EPICS_BASE=$EPICS_BASE"

make -C "$IOC_DIR" rebuild
