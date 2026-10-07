from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from half_linac.src.shared.machine_profile import AppContext, MachineProfile


APP_DIR = Path(__file__).resolve().parent
SOLENOID_CENTERING_RUNTIME_ROOT = APP_DIR / "runtime"
LATEST_RESULT_FILE = "latest_result.json"


def resolve_solenoid_centering_runtime_paths(
    target: MachineProfile | AppContext,
) -> dict[str, Path]:
    profile = target.profile if isinstance(target, AppContext) else target
    backend = target.control_backend.name if isinstance(target, AppContext) else profile.machine.default_mode
    runtime_dir = SOLENOID_CENTERING_RUNTIME_ROOT / profile.machine.id / backend
    latest_dir = runtime_dir / "latest"
    archive_dir = runtime_dir / "scans"
    return {
        "runtime_dir": runtime_dir,
        "latest_dir": latest_dir,
        "archive_dir": archive_dir,
        "latest_result_path": latest_dir / LATEST_RESULT_FILE,
    }


def write_scan_result(
    target: MachineProfile | AppContext,
    result: dict[str, Any],
    *,
    namespace: str | None = None,
    archive_path: str | Path | None = None,
    event: str | None = None,
) -> Path:
    """Write a new scan, or revise its existing archive after a state change.

    Updating requires the original archive path and appends an audit event while
    preserving ``created_at``. This keeps apply/restore actions from looking like
    independent scans.
    """
    paths = resolve_solenoid_centering_runtime_paths(target)
    if namespace is not None:
        if namespace != "joint":
            raise ValueError(f"Unknown scan namespace: {namespace}")
        root = paths["runtime_dir"] / namespace
        paths.update(latest_dir=root / "latest", archive_dir=root / "scans",
                     latest_result_path=root / "latest" / LATEST_RESULT_FILE)
    latest_dir = paths["latest_dir"]
    archive_dir = paths["archive_dir"]
    latest_dir.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now().astimezone()
    now_text = now.isoformat(timespec="seconds")
    payload = dict(result)
    previous: dict[str, Any] = {}
    if archive_path is None:
        payload.setdefault("created_at", now_text)
        preset = str(payload.get("preset_id", "scan")).strip().lower().replace(" ", "_") or "scan"
        timestamp = now.strftime("%Y%m%d_%H%M%S_%f")
        resolved_archive_path = archive_dir / f"scan_{timestamp}_{preset}.json"
    else:
        resolved_archive_path = Path(archive_path).resolve()
        resolved_archive_dir = archive_dir.resolve()
        if (resolved_archive_path.parent != resolved_archive_dir
                or resolved_archive_path.suffix != ".json"):
            raise ValueError(
                "Existing scan archive must be a JSON file in the configured scans directory."
            )
        if not resolved_archive_path.is_file():
            raise FileNotFoundError(f"Scan archive does not exist: {resolved_archive_path}")
        previous = json.loads(resolved_archive_path.read_text(encoding="utf-8"))
        payload["created_at"] = previous.get("created_at", payload.get("created_at", now_text))

    events = list(previous.get("archive_events", payload.get("archive_events", [])))
    if event is not None:
        entry = {"event": event, "recorded_at": now_text}
        for key in ("applied", "restore", "operation_status", "recommendation_available",
                    "apply_error", "restore_error"):
            if key in payload:
                entry[key] = payload[key]
        events.append(entry)
    if events:
        payload["archive_events"] = events
    payload["archive_path"] = str(resolved_archive_path)
    payload["archive_revision"] = int(previous.get("archive_revision", 0)) + 1
    payload["updated_at"] = now_text

    text = json.dumps(payload, indent=2, sort_keys=True)
    paths["latest_result_path"].write_text(text, encoding="utf-8")
    resolved_archive_path.write_text(text, encoding="utf-8")
    return resolved_archive_path


def read_latest_scan_result(target: MachineProfile | AppContext) -> dict[str, Any] | None:
    path = resolve_solenoid_centering_runtime_paths(target)["latest_result_path"]
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
