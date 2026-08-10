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


def write_scan_result(target: MachineProfile | AppContext, result: dict[str, Any]) -> Path:
    paths = resolve_solenoid_centering_runtime_paths(target)
    latest_dir = paths["latest_dir"]
    archive_dir = paths["archive_dir"]
    latest_dir.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)

    payload = dict(result)
    payload.setdefault("created_at", datetime.now().astimezone().isoformat(timespec="seconds"))
    text = json.dumps(payload, indent=2, sort_keys=True)
    paths["latest_result_path"].write_text(text, encoding="utf-8")

    preset = str(payload.get("preset_id", "scan")).strip().lower().replace(" ", "_") or "scan"
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    archive_path = archive_dir / f"scan_{timestamp}_{preset}.json"
    archive_path.write_text(text, encoding="utf-8")
    return archive_path


def read_latest_scan_result(target: MachineProfile | AppContext) -> dict[str, Any] | None:
    path = resolve_solenoid_centering_runtime_paths(target)["latest_result_path"]
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))

