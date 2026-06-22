from __future__ import annotations

from datetime import datetime
from pathlib import Path

from half_linac.src.shared.machine_profile import AppContext, MachineProfile


APP_DIR = Path(__file__).resolve().parent
BBA_RUNTIME_ROOT = APP_DIR / "runtime"

BBA1_DATA_FILE = "m1S.txt"
BBA1_QUAD_SCAN_FILE = "bba1_quad_scan.txt"
BBA_METADATA_FILE = "metadata.json"
BBA2_METADATA_FILE = "bba2_metadata.json"
BBA2_QUAD_SCAN_FILE = "bba2_k1Lqm2.txt"
BBA2_BPM1_FILE = "bba2_m1.txt"
BBA2_CORRECTOR_SCAN_FILE = "bba2_thetam2.txt"


def resolve_bba_runtime_paths(target: MachineProfile | AppContext) -> dict[str, Path]:
    profile = target.profile if isinstance(target, AppContext) else target
    backend = target.control_backend.name if isinstance(target, AppContext) else profile.machine.default_mode
    runtime_dir = BBA_RUNTIME_ROOT / profile.machine.id / backend
    latest_dir = runtime_dir / "latest"
    archive_dir = runtime_dir / "scans"
    return {
        "runtime_dir": runtime_dir,
        "latest_dir": latest_dir,
        "archive_dir": archive_dir,
        "bba1_data_path": latest_dir / BBA1_DATA_FILE,
        "bba1_quad_scan_path": latest_dir / BBA1_QUAD_SCAN_FILE,
        "bba1_metadata_path": latest_dir / BBA_METADATA_FILE,
        "bba2_quad_scan_path": latest_dir / BBA2_QUAD_SCAN_FILE,
        "bba2_bpm1_path": latest_dir / BBA2_BPM1_FILE,
        "bba2_corrector_scan_path": latest_dir / BBA2_CORRECTOR_SCAN_FILE,
        "bba2_metadata_path": latest_dir / BBA2_METADATA_FILE,
    }


def new_bba_scan_archive_dir(target: MachineProfile | AppContext, family: str) -> Path:
    paths = resolve_bba_runtime_paths(target)
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    family_slug = str(family).strip().lower().replace(" ", "_") or "bba"
    return paths["archive_dir"] / f"scan_{timestamp}_{family_slug}"
