from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from half_linac.src.shared.machine_profile import (
    AppContext,
    MachineProfile,
    MachineProfileError,
    get_workflow,
)


APP_DIR = Path(__file__).resolve().parent
CORRECTOR_STATE_PATH = APP_DIR / "cor_temp.txt"
CORRECT_LOG_PATH = APP_DIR / "correct.log"
FINDRESPONSE_LOG_PATH = APP_DIR / "findresponse.log"
ORBIT_RUNTIME_ROOT = APP_DIR / "runtime"

DEFAULT_RESPONSE_WAIT_S = 8.0
DEFAULT_CORRECTOR_UPPERLIMIT_RAD = 0.001
DEFAULT_RUNTIME_DEFAULTS: dict[str, Any] = {
    "method": "one-to-one",
    "sampling_interval_s": 6.0,
    "accuracy_um": 10.0,
    "samples_per_step": 2,
    "global_max_iter": 20,
    "one_to_one_max_iter": 20,
    "correction_gain": 0.5,
    "correction_max_step_pct": 25.0,
    "local_response_kick_fraction": 0.02,
    "matrix_response_kick_fraction": 0.02,
    "matrix_samples_per_step": 2,
}
RESPONSE_MATRIX_MIN_COLUMN_NORM = 1e-12
RESPONSE_MATRIX_RANK_TOL = 1e-12
RESPONSE_MATRIX_MAX_CONDITION = 1e12


def load_orbit_runtime_settings(target: MachineProfile | AppContext) -> dict[str, Any]:
    profile = target.profile if isinstance(target, AppContext) else target
    workflow = get_workflow(profile, "orbit")
    backend = target.control_backend.name if isinstance(target, AppContext) else profile.machine.default_mode
    paths = resolve_orbit_runtime_paths(target)
    corrector_limit, corrector_limit_unit = _select_corrector_upperlimit(workflow, backend)
    return {
        "response_wait_s": _select_backend_float(
            workflow,
            "response_wait_s_by_backend",
            backend,
            DEFAULT_RESPONSE_WAIT_S,
        ),
        "response_sample_interval_s": _select_response_sample_interval_s(
            workflow,
            backend,
        ),
        "corrector_upperlimit": corrector_limit,
        "corrector_upperlimit_unit": corrector_limit_unit,
        "runtime_defaults": _select_runtime_defaults(workflow),
        **paths,
    }


def display_unit(unit: str) -> str:
    normalized = str(unit).strip()
    if normalized.lower() == "rad":
        return "rad"
    if normalized.lower() == "a":
        return "A"
    return normalized


def resolve_orbit_runtime_paths(target: MachineProfile | AppContext) -> dict[str, Path]:
    profile = target.profile if isinstance(target, AppContext) else target
    backend = target.control_backend.name if isinstance(target, AppContext) else profile.machine.default_mode
    runtime_dir = ORBIT_RUNTIME_ROOT / profile.machine.id / backend
    return {
        "runtime_dir": runtime_dir,
        "response_matrix_path": runtime_dir / "response.txt",
        "response_matrix_dir": runtime_dir / "matrices",
        "active_response_path": runtime_dir / "active_response.json",
        "response_progress_path": runtime_dir / "response_progress.json",
        "corrector_state_path": runtime_dir / "cor_temp.txt",
        "correct_log_path": runtime_dir / "correct.log",
        "findresponse_log_path": runtime_dir / "findresponse.log",
    }


def expected_response_matrix_shape(target: MachineProfile | AppContext) -> tuple[int, int]:
    bpms, xcors, _ycors = _orbit_ids(target)
    return (2 * len(bpms), 2 * len(xcors))


def validate_response_matrix_quality(
    target: MachineProfile | AppContext,
    matrix,
    *,
    source: str = "response matrix",
) -> None:
    matrix_array = np.asarray(matrix, dtype=float)
    expected_shape = expected_response_matrix_shape(target)
    if matrix_array.shape != expected_shape:
        raise ValueError(
            f"Response matrix shape mismatch for {source}: "
            f"got {matrix_array.shape}, expected {expected_shape}."
        )
    if not np.all(np.isfinite(matrix_array)):
        raise ValueError(f"Response matrix quality check failed for {source}: contains NaN or Inf.")

    bpms, xcors, ycors = _orbit_ids(target)
    n_bpm = len(bpms)
    n_xcor = len(xcors)
    n_ycor = len(ycors)
    x_block = matrix_array[0:n_bpm, 0:n_xcor]
    y_block = matrix_array[n_bpm : 2 * n_bpm, n_xcor : n_xcor + n_ycor]

    _validate_response_block_quality(source, "X", x_block, xcors)
    _validate_response_block_quality(source, "Y", y_block, ycors)


def write_response_matrix_snapshot(
    target: MachineProfile | AppContext,
    matrix,
    *,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    paths = resolve_orbit_runtime_paths(target)
    matrix_dir = paths["response_matrix_dir"]
    matrix_dir.mkdir(parents=True, exist_ok=True)

    matrix_array = np.asarray(matrix, dtype=float)
    expected_shape = expected_response_matrix_shape(target)
    if matrix_array.shape != expected_shape:
        raise ValueError(
            f"Response matrix shape mismatch: got {matrix_array.shape}, expected {expected_shape}."
        )
    validate_response_matrix_quality(target, matrix_array, source="new response matrix")

    created_at = created_at or datetime.now().astimezone()
    stem = _unique_response_stem(matrix_dir, created_at)
    matrix_path = matrix_dir / f"{stem}.txt"
    metadata_path = matrix_dir / f"{stem}.json"

    np.savetxt(matrix_path, matrix_array)
    metadata = _build_response_matrix_metadata(target, matrix_path, metadata_path, created_at)
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    set_active_response_matrix(target, metadata_path)
    return _enrich_response_metadata(target, metadata, metadata_path)


def list_response_matrix_records(target: MachineProfile | AppContext) -> list[dict[str, Any]]:
    matrix_dir = resolve_orbit_runtime_paths(target)["response_matrix_dir"]
    if not matrix_dir.is_dir():
        return []

    records: list[dict[str, Any]] = []
    for metadata_path in matrix_dir.glob("response_*.json"):
        try:
            metadata = _read_json(metadata_path)
            _validate_response_metadata(target, metadata, metadata_path, require_matrix=False)
        except (OSError, ValueError, MachineProfileError):
            continue
        records.append(_enrich_response_metadata(target, metadata, metadata_path))

    return sorted(records, key=lambda item: str(item.get("created_at", "")), reverse=True)


def get_active_response_matrix_record(target: MachineProfile | AppContext) -> dict[str, Any] | None:
    active_path = resolve_orbit_runtime_paths(target)["active_response_path"]
    if not active_path.is_file():
        return None

    pointer = _read_json(active_path)
    metadata_path = _resolve_runtime_path(target, pointer.get("metadata_file", ""))
    metadata = _read_json(metadata_path)
    _validate_response_metadata(target, metadata, metadata_path, require_matrix=True)
    return _enrich_response_metadata(target, metadata, metadata_path)


def set_active_response_matrix(
    target: MachineProfile | AppContext,
    metadata_path: str | Path,
) -> dict[str, Any]:
    metadata_path = Path(metadata_path)
    metadata = _read_json(metadata_path)
    _validate_response_metadata(target, metadata, metadata_path, require_matrix=True)

    runtime_dir = resolve_orbit_runtime_paths(target)["runtime_dir"]
    runtime_dir.mkdir(parents=True, exist_ok=True)
    pointer = {
        "metadata_file": _relative_to_runtime(target, metadata_path),
        "matrix_file": metadata["matrix_file"],
        "selected_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    resolve_orbit_runtime_paths(target)["active_response_path"].write_text(
        json.dumps(pointer, indent=2),
        encoding="utf-8",
    )
    return _enrich_response_metadata(target, metadata, metadata_path)


def resolve_active_response_matrix(target: MachineProfile | AppContext) -> Path:
    active = get_active_response_matrix_record(target)
    if active is not None:
        return _resolve_runtime_path(target, active["matrix_file"])

    paths = resolve_orbit_runtime_paths(target)
    raise FileNotFoundError(
        "No active response matrix is configured for "
        f"{_machine_id(target)}/{_backend_name(target)}. "
        f"Measure or load a response matrix first. Active pointer: {paths['active_response_path']}"
    )


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


def _select_response_sample_interval_s(
    workflow: Mapping[str, Any],
    backend: str,
) -> float:
    settle_time_s = _select_backend_float(
        workflow,
        "response_wait_s_by_backend",
        backend,
        DEFAULT_RESPONSE_WAIT_S,
    )
    return _select_backend_float(
        workflow,
        "response_sample_interval_s_by_backend",
        backend,
        settle_time_s,
    )


def _select_corrector_upperlimit(
    workflow: Mapping[str, Any],
    backend: str,
) -> tuple[float, str]:
    raw_limits = workflow.get("corrector_upperlimit_by_backend")
    if isinstance(raw_limits, Mapping):
        raw_value = raw_limits.get(backend, raw_limits.get("default"))
        if raw_value is not None:
            return _parse_corrector_upperlimit(raw_value, backend)

    legacy_value = _optional_float(
        workflow.get("corrector_upperlimit_rad"),
        DEFAULT_CORRECTOR_UPPERLIMIT_RAD,
    )
    return legacy_value, "rad"


def _parse_corrector_upperlimit(raw_value: Any, backend: str) -> tuple[float, str]:
    if isinstance(raw_value, Mapping):
        value = _optional_float(raw_value.get("value"), DEFAULT_CORRECTOR_UPPERLIMIT_RAD)
        unit = str(raw_value.get("unit", "")).strip()
        if not unit:
            unit = "rad" if backend == "vm" else ""
        return value, unit

    return _optional_float(raw_value, DEFAULT_CORRECTOR_UPPERLIMIT_RAD), ""


def _select_runtime_defaults(workflow: Mapping[str, Any]) -> dict[str, Any]:
    selected = dict(DEFAULT_RUNTIME_DEFAULTS)
    raw_defaults = workflow.get("runtime_defaults")
    if not isinstance(raw_defaults, Mapping):
        return selected

    for key in selected:
        if key in raw_defaults:
            selected[key] = raw_defaults[key]
    selected["method"] = str(selected["method"]).strip() or DEFAULT_RUNTIME_DEFAULTS["method"]
    selected["sampling_interval_s"] = float(selected["sampling_interval_s"])
    selected["accuracy_um"] = float(selected["accuracy_um"])
    selected["samples_per_step"] = int(selected["samples_per_step"])
    selected["global_max_iter"] = int(selected["global_max_iter"])
    selected["one_to_one_max_iter"] = int(selected["one_to_one_max_iter"])
    selected["correction_gain"] = float(selected["correction_gain"])
    selected["correction_max_step_pct"] = float(selected["correction_max_step_pct"])
    selected["local_response_kick_fraction"] = float(selected["local_response_kick_fraction"])
    selected["matrix_response_kick_fraction"] = float(selected["matrix_response_kick_fraction"])
    selected["matrix_samples_per_step"] = int(selected["matrix_samples_per_step"])
    return selected


def _optional_float(value: Any, default: float) -> float:
    if value is None:
        return default
    return float(value)


def _build_response_matrix_metadata(
    target: MachineProfile | AppContext,
    matrix_path: Path,
    metadata_path: Path,
    created_at: datetime,
) -> dict[str, Any]:
    bpms, xcors, ycors = _orbit_ids(target)
    shape = expected_response_matrix_shape(target)
    return {
        "machine_id": _machine_id(target),
        "backend": _backend_name(target),
        "created_at": created_at.isoformat(timespec="seconds"),
        "matrix_file": _relative_to_runtime(target, matrix_path),
        "metadata_file": _relative_to_runtime(target, metadata_path),
        "bpms": bpms,
        "xcors": xcors,
        "ycors": ycors,
        "shape": list(shape),
    }


def _enrich_response_metadata(
    target: MachineProfile | AppContext,
    metadata: Mapping[str, Any],
    metadata_path: Path,
) -> dict[str, Any]:
    enriched = dict(metadata)
    enriched["metadata_path"] = str(metadata_path)
    matrix_file = enriched.get("matrix_file")
    if isinstance(matrix_file, str):
        enriched["matrix_path"] = str(_resolve_runtime_path(target, matrix_file))
    return enriched


def _validate_response_metadata(
    target: MachineProfile | AppContext,
    metadata: Mapping[str, Any],
    metadata_path: Path,
    *,
    require_matrix: bool,
) -> None:
    expected_machine = _machine_id(target)
    expected_backend = _backend_name(target)
    if metadata.get("machine_id") != expected_machine:
        raise ValueError(
            f"Response matrix {metadata_path} belongs to machine {metadata.get('machine_id')!r}, "
            f"expected {expected_machine!r}."
        )
    if metadata.get("backend") != expected_backend:
        raise ValueError(
            f"Response matrix {metadata_path} belongs to backend {metadata.get('backend')!r}, "
            f"expected {expected_backend!r}."
        )

    bpms, xcors, ycors = _orbit_ids(target)
    if list(metadata.get("bpms", ())) != bpms:
        raise ValueError(f"Response matrix {metadata_path} BPM list does not match current profile.")
    if list(metadata.get("xcors", ())) != xcors:
        raise ValueError(f"Response matrix {metadata_path} X corrector list does not match current profile.")
    if list(metadata.get("ycors", ())) != ycors:
        raise ValueError(f"Response matrix {metadata_path} Y corrector list does not match current profile.")

    expected_shape = expected_response_matrix_shape(target)
    if tuple(metadata.get("shape", ())) != expected_shape:
        raise ValueError(
            f"Response matrix {metadata_path} metadata shape is {metadata.get('shape')}, "
            f"expected {list(expected_shape)}."
        )

    if require_matrix:
        matrix_file = metadata.get("matrix_file")
        if not isinstance(matrix_file, str) or not matrix_file.strip():
            raise ValueError(f"Response matrix metadata {metadata_path} is missing matrix_file.")
        matrix_path = _resolve_runtime_path(target, matrix_file)
        matrix = _validate_response_matrix_shape(target, matrix_path)
        validate_response_matrix_quality(target, matrix, source=str(matrix_path))


def _validate_response_matrix_shape(target: MachineProfile | AppContext, matrix_path: Path) -> np.ndarray:
    if not matrix_path.is_file():
        raise FileNotFoundError(f"Response matrix file not found: {matrix_path}")

    matrix = np.loadtxt(matrix_path)
    expected_shape = expected_response_matrix_shape(target)
    if matrix.shape != expected_shape:
        raise ValueError(
            f"Response matrix {matrix_path} has shape {matrix.shape}, expected {expected_shape}."
        )
    return matrix


def _validate_response_block_quality(
    source: str,
    plane: str,
    block: np.ndarray,
    corrector_ids: list[str],
) -> None:
    if block.size == 0:
        raise ValueError(f"Response matrix quality check failed for {source}: {plane} plane block is empty.")

    column_norms = np.linalg.norm(block, axis=0)
    zero_columns = [
        f"{name} (norm={norm:.3e})"
        for name, norm in zip(corrector_ids, column_norms)
        if norm <= RESPONSE_MATRIX_MIN_COLUMN_NORM
    ]
    if zero_columns:
        raise ValueError(
            f"Response matrix quality check failed for {source}: "
            f"{plane} plane has zero-response corrector column(s): "
            f"{', '.join(zero_columns)}. Re-measure the response matrix before global correction."
        )

    singular_values = np.linalg.svd(block, compute_uv=False)
    expected_rank = min(block.shape)
    rank = int(np.count_nonzero(singular_values > RESPONSE_MATRIX_RANK_TOL))
    min_singular = float(singular_values[-1]) if singular_values.size else 0.0
    max_singular = float(singular_values[0]) if singular_values.size else 0.0
    if rank < expected_rank:
        raise ValueError(
            f"Response matrix quality check failed for {source}: "
            f"{plane} plane is rank deficient ({rank}/{expected_rank}), "
            f"min singular value={min_singular:.3e}. Re-measure the response matrix before global correction."
        )

    condition = max_singular / min_singular if min_singular > 0 else float("inf")
    if condition > RESPONSE_MATRIX_MAX_CONDITION:
        raise ValueError(
            f"Response matrix quality check failed for {source}: "
            f"{plane} plane condition number is too large ({condition:.3e}). "
            "Re-measure the response matrix before global correction."
        )


def _orbit_ids(target: MachineProfile | AppContext) -> tuple[list[str], list[str], list[str]]:
    if isinstance(target, AppContext) and target.orbit_workflow is not None:
        workflow = target.orbit_workflow
        return list(workflow.bpms), list(workflow.xcors), list(workflow.ycors)

    profile = target.profile if isinstance(target, AppContext) else target
    workflow = get_workflow(profile, "orbit")
    return (
        list(workflow.get("bpms", ())),
        list(workflow.get("xcors", ())),
        list(workflow.get("ycors", ())),
    )


def _machine_id(target: MachineProfile | AppContext) -> str:
    profile = target.profile if isinstance(target, AppContext) else target
    return profile.machine.id


def _backend_name(target: MachineProfile | AppContext) -> str:
    if isinstance(target, AppContext):
        return target.control_backend.name
    return target.machine.default_mode


def _unique_response_stem(matrix_dir: Path, created_at: datetime) -> str:
    base = f"response_{created_at:%Y%m%d_%H%M%S}"
    stem = base
    counter = 1
    while (matrix_dir / f"{stem}.txt").exists() or (matrix_dir / f"{stem}.json").exists():
        stem = f"{base}_{counter:02d}"
        counter += 1
    return stem


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}.")
    return payload


def _resolve_runtime_path(target: MachineProfile | AppContext, value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return resolve_orbit_runtime_paths(target)["runtime_dir"] / path


def _relative_to_runtime(target: MachineProfile | AppContext, path: Path) -> str:
    runtime_dir = resolve_orbit_runtime_paths(target)["runtime_dir"]
    try:
        return str(path.relative_to(runtime_dir))
    except ValueError:
        return str(path)
