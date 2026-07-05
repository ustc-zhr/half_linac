from __future__ import annotations

from pathlib import Path

from half_linac.src.shared.machine_profile import (
    AppContext,
    MachineProfile,
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
        "legacy_archive_dir": runtime_dir / "scans",
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
