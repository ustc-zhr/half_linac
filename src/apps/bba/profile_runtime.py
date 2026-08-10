from __future__ import annotations

from pathlib import Path

import numpy as np

from half_linac.src.shared.machine_profile import (
    AppContext,
    LimitRange,
    MachineProfile,
    MachineProfileError,
    WriteTarget,
    effective_limit,
    resolve_write_target,
    new_app_run_dir,
    resolve_app_runtime_paths,
)


APP_DIR = Path(__file__).resolve().parent
BBA_RUNTIME_ROOT = APP_DIR / "runtime"

BBA1_DATA_FILE = "m1S.txt"
BBA1_QUAD_SCAN_FILE = "bba1_quad_scan.txt"
BBA_METADATA_FILE = "metadata.json"
BBA2_METADATA_FILE = "bba2_metadata.json"
BBA2_QUAD_SCAN_FILE = "bba2_k1Lqm2.txt"
BBA2_BPM1_FILE = "bba2_m1.txt"
BBA2_CORRECTOR_SCAN_FILE = "bba2_thetam2.txt"


def resolve_scan_values(low: float, high: float, steps: int, mode: str, center: float) -> np.ndarray:
    values = np.linspace(low, high, steps)
    normalized_mode = str(mode or "absolute").strip().lower()
    if normalized_mode == "relative":
        try:
            center_value = float(center)
        except (TypeError, ValueError) as exc:
            raise ValueError("Relative scan requires a finite current setpoint.") from exc
        if not np.isfinite(center_value):
            raise ValueError("Relative scan requires a finite current setpoint.")
        values = values + center_value
    elif normalized_mode != "absolute":
        raise ValueError(f"Unsupported scan mode: {mode!r}.")
    if not np.all(np.isfinite(values)):
        raise ValueError("Scan values must be finite.")
    return values


def resolve_limited_scan_values(
    target: MachineProfile | AppContext,
    element_id: str,
    logical_channel: str,
    low: float,
    high: float,
    steps: int,
    mode: str,
    unit: str,
    center: float,
    *,
    write_target: WriteTarget | None = None,
) -> np.ndarray:
    """Resolve an application scan range and intersect it with channel limits."""
    try:
        resolved_target = write_target or resolve_write_target(
            target,
            element_id,
            logical_channel=logical_channel,
            unit=unit,
        )
        application_limit = LimitRange(low, high, unit)
        if str(mode or "absolute").strip().lower() == "relative":
            application_limit = application_limit.relative_to_absolute(center)
        elif str(mode or "absolute").strip().lower() != "absolute":
            raise MachineProfileError(f"Unsupported scan mode: {mode!r}.")

        machine_limit = resolved_target.machine_limit
        if machine_limit is not None and not machine_limit.contains(center):
            raise MachineProfileError(
                f"Current value {float(center):g} is outside physical limit "
                f"{machine_limit.describe()}."
            )
        selected = effective_limit(application_limit, machine_limit)
    except (TypeError, ValueError, MachineProfileError) as exc:
        raise MachineProfileError(
            f"Invalid BBA scan range for {element_id}.{logical_channel}: {exc}"
        ) from exc

    assert selected.low is not None and selected.high is not None
    return np.linspace(selected.low, selected.high, steps)


def resolve_bba_runtime_paths(target: MachineProfile | AppContext) -> dict[str, Path]:
    base_paths = resolve_app_runtime_paths(APP_DIR, target)
    runtime_dir = base_paths["runtime_dir"]
    latest_dir = base_paths["latest_dir"]
    runs_dir = base_paths["runs_dir"]
    return {
        "runtime_dir": runtime_dir,
        "latest_dir": latest_dir,
        "archive_dir": runs_dir,
        "runs_dir": runs_dir,
        "latest_metadata_path": base_paths["latest_metadata_path"],
        "bba1_data_path": latest_dir / BBA1_DATA_FILE,
        "bba1_quad_scan_path": latest_dir / BBA1_QUAD_SCAN_FILE,
        "bba1_metadata_path": latest_dir / BBA_METADATA_FILE,
        "bba2_quad_scan_path": latest_dir / BBA2_QUAD_SCAN_FILE,
        "bba2_bpm1_path": latest_dir / BBA2_BPM1_FILE,
        "bba2_corrector_scan_path": latest_dir / BBA2_CORRECTOR_SCAN_FILE,
        "bba2_metadata_path": latest_dir / BBA2_METADATA_FILE,
    }


def new_bba_scan_archive_dir(target: MachineProfile | AppContext, family: str) -> Path:
    return new_app_run_dir(APP_DIR, target, kind="scan", suffix=family)
