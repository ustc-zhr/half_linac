from __future__ import annotations

from pathlib import Path

from half_linac.src.shared.machine_profile import (
    AppContext,
    LimitRange,
    MachineProfile,
    MachineProfileError,
    effective_limit,
    resolve_app_runtime_paths,
)


APP_DIR = Path(__file__).resolve().parent
ENERGY_SPECTRUM_RUNTIME_ROOT = APP_DIR / "runtime"
MODEL_SNAPSHOT_FILE = "model_snapshot.json"
BACKGROUND_IMAGE_FILE = "background.npy"
BACKGROUND_METADATA_FILE = "background.json"


def effective_auto_tune_limit(
    target: MachineProfile | AppContext,
    element_id: str,
    logical_channel: str,
    low: float,
    high: float,
    mode: str,
    unit: str,
    center: float,
) -> LimitRange:
    """Intersect an Energy Spectrum scan range with its actuator-channel limit."""
    profile = target.profile if isinstance(target, AppContext) else target
    element = profile.get_element(element_id)
    try:
        application_limit = LimitRange(low, high, unit)
        normalized_mode = str(mode or "absolute").strip().lower()
        if normalized_mode == "relative":
            application_limit = application_limit.relative_to_absolute(center)
        elif normalized_mode != "absolute":
            raise MachineProfileError(f"Unsupported scan mode: {mode!r}.")

        raw_machine_limit = element.limits_for(logical_channel)
        machine_limit = LimitRange.from_mapping(raw_machine_limit) if raw_machine_limit else None
        if machine_limit is not None and not machine_limit.contains(center):
            raise MachineProfileError(
                f"Current value {float(center):g} is outside physical limit "
                f"{machine_limit.describe()}."
            )
        return effective_limit(application_limit, machine_limit)
    except (TypeError, ValueError, MachineProfileError) as exc:
        raise MachineProfileError(
            f"Invalid Energy Spectrum scan range for "
            f"{element_id}.{logical_channel}: {exc}"
        ) from exc


def resolve_energy_spectrum_runtime_paths(
    target: MachineProfile | AppContext,
    *,
    station_id: str | None = None,
) -> dict[str, Path]:
    base_paths = resolve_app_runtime_paths(APP_DIR, target)
    runtime_dir = base_paths["runtime_dir"]
    latest_dir = base_paths["latest_dir"]
    runs_dir = base_paths["runs_dir"]
    if station_id is not None:
        normalized_station = str(station_id).strip()
        if (
            not normalized_station
            or normalized_station in {".", ".."}
            or "/" in normalized_station
            or "\\" in normalized_station
        ):
            raise ValueError(f"Invalid energy-spectrum station id: {station_id!r}")
        latest_dir = latest_dir / "stations" / normalized_station
        runs_dir = runs_dir / "stations" / normalized_station
    return {
        "runtime_dir": runtime_dir,
        "latest_dir": latest_dir,
        "result_archive_dir": runs_dir,
        "runs_dir": runs_dir,
        "latest_metadata_path": latest_dir / "metadata.json",
        "model_snapshot_path": latest_dir / MODEL_SNAPSHOT_FILE,
        "background_image_path": latest_dir / BACKGROUND_IMAGE_FILE,
        "background_metadata_path": latest_dir / BACKGROUND_METADATA_FILE,
    }
