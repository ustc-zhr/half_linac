#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common.sh"

export HALF_LINAC_MACHINE_ID=irfel
export HALF_LINAC_CONTROL_BACKEND=vm
export HALF_MACHINE_ID="$HALF_LINAC_MACHINE_ID"
export HALF_CONTROL_BACKEND="$HALF_LINAC_CONTROL_BACKEND"
export PYTHONUNBUFFERED=1

VM_TIMEOUT="${IRFEL_VM_SMOKE_TIMEOUT:-10}"
IOC_TIMEOUT="${IRFEL_IOC_SMOKE_TIMEOUT:-35}"
IOC_READY_TIMEOUT="${IRFEL_IOC_READY_TIMEOUT:-20}"
IOC_LOG="$(mktemp "${TMPDIR:-/tmp}/irfel-ioc-smoke.XXXXXX.log")"
IOC_PID=""

cleanup() {
  local status=$?
  if [[ -n "$IOC_PID" ]] && kill -0 "$IOC_PID" 2>/dev/null; then
    kill "$IOC_PID" 2>/dev/null || true
    wait "$IOC_PID" 2>/dev/null || true
  fi
  rm -f "$IOC_LOG"
  return "$status"
}
trap cleanup EXIT

run_timeout_smoke() {
  local label="$1"
  local timeout_s="$2"
  shift 2

  echo "Starting $label smoke for ${timeout_s}s ..."
  set +e
  timeout "$timeout_s" "$@"
  local status=$?
  set -e

  if [[ "$status" -eq 124 ]]; then
    echo "$label stayed up until timeout."
    return 0
  fi
  if [[ "$status" -eq 0 ]]; then
    echo "$label exited cleanly before timeout."
    return 0
  fi

  echo "$label failed with exit code $status."
  return "$status"
}

start_ioc_background() {
  echo "Starting IRFEL IOC manager smoke for up to ${IOC_TIMEOUT}s ..."
  timeout "$IOC_TIMEOUT" bash "$SCRIPT_DIR/start_ioc_manager.sh" >"$IOC_LOG" 2>&1 &
  IOC_PID=$!

  local deadline=$((SECONDS + IOC_READY_TIMEOUT))
  while (( SECONDS < deadline )); do
    if grep -q "softIOC PVs are reachable" "$IOC_LOG"; then
      echo "IRFEL IOC manager reported reachable PVs."
      return 0
    fi
    if ! kill -0 "$IOC_PID" 2>/dev/null; then
      echo "IRFEL IOC manager exited before PVs were reachable."
      cat "$IOC_LOG"
      wait "$IOC_PID"
      return "$?"
    fi
    sleep 1
  done

  echo "IRFEL IOC manager did not report reachable PVs within ${IOC_READY_TIMEOUT}s."
  cat "$IOC_LOG"
  return 1
}

bash "$SCRIPT_DIR/check_irfel_vm.sh"
start_ioc_background
run_timeout_smoke "IRFEL VM manager" "$VM_TIMEOUT" bash "$SCRIPT_DIR/start_vm.sh"

if [[ -n "$IOC_PID" ]] && kill -0 "$IOC_PID" 2>/dev/null; then
  echo "IRFEL IOC manager stayed up during VM smoke."
else
  echo "IRFEL IOC manager stopped during VM smoke."
  cat "$IOC_LOG"
  exit 1
fi

echo "IRFEL VM runtime smoke passed."
