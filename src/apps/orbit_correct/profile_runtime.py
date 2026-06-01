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
RESPONSE_MATRIX_PATH = APP_DIR / "response.txt"
CORRECTOR_STATE_PATH = APP_DIR / "cor_temp.txt"
CORRECT_LOG_PATH = APP_DIR / "correct.log"
FINDRESPONSE_LOG_PATH = APP_DIR / "findresponse.log"
ORBIT_RUNTIME_ROOT = APP_DIR / "runtime"

DEFAULT_RESPONSE_WAIT_S = 8.0
DEFAULT_CORRECTOR_UPPERLIMIT_RAD = 0.001


def load_orbit_runtime_settings(target: MachineProfile | AppContext) -> dict[str, Any]:
    profile = target.profile if isinstance(target, AppContext) else target
    workflow = get_workflow(profile, "orbit")
    backend = target.control_backend.name if isinstance(target, AppContext) else profile.machine.default_mode
    paths = resolve_orbit_runtime_paths(target)
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
        **paths,
    }


def resolve_orbit_runtime_paths(target: MachineProfile | AppContext) -> dict[str, Path]:
    profile = target.profile if isinstance(target, AppContext) else target
    backend = target.control_backend.name if isinstance(target, AppContext) else profile.machine.default_mode
    runtime_dir = ORBIT_RUNTIME_ROOT / profile.machine.id / backend
    return {
        "runtime_dir": runtime_dir,
        "response_matrix_path": runtime_dir / "response.txt",
        "response_matrix_dir": runtime_dir / "matrices",
        "active_response_path": runtime_dir / "active_response.json",
        "corrector_state_path": runtime_dir / "cor_temp.txt",
        "correct_log_path": runtime_dir / "correct.log",
        "findresponse_log_path": runtime_dir / "findresponse.log",
    }


def expected_response_matrix_shape(target: MachineProfile | AppContext) -> tuple[int, int]:
    bpms, xcors, _ycors = _orbit_ids(target)
    return (2 * len(bpms), 2 * len(xcors))


def write_response_matrix_snapshot(
    target: MachineProfile | AppContext,
    matrix,
    *,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    paths = resolve_orbit_runtime_paths(target)
    matrix_dir = paths["response_matrix_dir"]
    matrix_dir.mkdir(parents=True, exist_ok=True)

    matrix_array = np.asarray(matrix)
    expected_shape = expected_response_matrix_shape(target)
    if matrix_array.shape != expected_shape:
        raise ValueError(
            f"Response matrix shape mismatch: got {matrix_array.shape}, expected {expected_shape}."
        )

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


def resolve_active_response_matrix(
    target: MachineProfile | AppContext,
    *,
    legacy_matrix_path: str | Path | None = None,
) -> Path:
    active = get_active_response_matrix_record(target)
    if active is not None:
        return _resolve_runtime_path(target, active["matrix_file"])

    if legacy_matrix_path is not None:
        legacy_path = Path(legacy_matrix_path)
        if legacy_path.is_file():
            _validate_response_matrix_shape(target, legacy_path)
            return legacy_path

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
        _validate_response_matrix_shape(target, _resolve_runtime_path(target, matrix_file))


def _validate_response_matrix_shape(target: MachineProfile | AppContext, matrix_path: Path) -> None:
    if not matrix_path.is_file():
        raise FileNotFoundError(f"Response matrix file not found: {matrix_path}")

    matrix = np.loadtxt(matrix_path)
    expected_shape = expected_response_matrix_shape(target)
    if matrix.shape != expected_shape:
        raise ValueError(
            f"Response matrix {matrix_path} has shape {matrix.shape}, expected {expected_shape}."
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
