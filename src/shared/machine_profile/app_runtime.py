from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .models import AppContext, MachineProfile


LATEST_DIRNAME = "latest"
RUNS_DIRNAME = "runs"
METADATA_FILENAME = "metadata.json"


def _profile_backend(target: MachineProfile | AppContext) -> tuple[MachineProfile, str]:
    if isinstance(target, AppContext):
        return target.profile, target.control_backend.name
    return target, target.machine.default_mode


def sanitize_runtime_token(value: object, *, fallback: str = "run") -> str:
    token = str(value or "").strip().lower().replace(" ", "_")
    token = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in token)
    token = "_".join(part for part in token.split("_") if part)
    return token or fallback


def make_runtime_run_id(kind: str = "run", suffix: object | None = None) -> str:
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    parts = [sanitize_runtime_token(kind), timestamp]
    if suffix is not None:
        parts.append(sanitize_runtime_token(suffix, fallback="data"))
    return "_".join(parts)


def resolve_app_runtime_paths(app_dir: Path | str, target: MachineProfile | AppContext) -> dict[str, Path]:
    profile, backend = _profile_backend(target)
    runtime_dir = Path(app_dir) / "runtime" / profile.machine.id / backend
    latest_dir = runtime_dir / LATEST_DIRNAME
    runs_dir = runtime_dir / RUNS_DIRNAME
    return {
        "runtime_dir": runtime_dir,
        "latest_dir": latest_dir,
        "runs_dir": runs_dir,
        "latest_metadata_path": latest_dir / METADATA_FILENAME,
    }


def new_app_run_dir(
    app_dir: Path | str,
    target: MachineProfile | AppContext,
    *,
    kind: str = "run",
    suffix: object | None = None,
) -> Path:
    paths = resolve_app_runtime_paths(app_dir, target)
    return paths["runs_dir"] / make_runtime_run_id(kind, suffix)
