from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from half_linac.src.shared.machine_profile import AppContext, MachineProfile, get_workflow


APP_DIR = Path(__file__).resolve().parent
RESPONSE_MATRIX_PATH = APP_DIR / "response.txt"
CORRECTOR_STATE_PATH = APP_DIR / "cor_temp.txt"
CORRECT_LOG_PATH = APP_DIR / "correct.log"
FINDRESPONSE_LOG_PATH = APP_DIR / "findresponse.log"

DEFAULT_RESPONSE_WAIT_S = 8.0
DEFAULT_CORRECTOR_UPPERLIMIT_RAD = 0.001


def load_orbit_runtime_settings(target: MachineProfile | AppContext) -> dict[str, float]:
    profile = target.profile if isinstance(target, AppContext) else target
    workflow = get_workflow(profile, "orbit")
    backend = target.control_backend.name if isinstance(target, AppContext) else profile.machine.default_mode
    return {
        "response_wait_s": _select_backend_float(
            workflow,
            "response_wait_s_by_backend",
            backend,
            DEFAULT_RESPONSE_WAIT_S,
        ),
        "corrector_upperlimit_rad": _optional_float(
            workflow.get("corrector_upperlimit_rad"),
            DEFAULT_CORRECTOR_UPPERLIMIT_RAD,
        ),
    }


def _select_backend_float(
    workflow: Mapping[str, Any],
    key: str,
    backend: str,
    default: float,
) -> float:
    raw_value = workflow.get(key)
    if not isinstance(raw_value, Mapping):
        return default
    value = raw_value.get(backend)
    if value is None:
        return default
    return float(value)


def _optional_float(value: Any, default: float) -> float:
    if value is None:
        return default
    return float(value)
