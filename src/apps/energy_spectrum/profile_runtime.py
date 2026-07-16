from __future__ import annotations

from pathlib import Path

from half_linac.src.shared.machine_profile import AppContext, MachineProfile, resolve_app_runtime_paths


APP_DIR = Path(__file__).resolve().parent
ENERGY_SPECTRUM_RUNTIME_ROOT = APP_DIR / "runtime"
MODEL_SNAPSHOT_FILE = "model_snapshot.json"
BACKGROUND_IMAGE_FILE = "background.npy"
BACKGROUND_METADATA_FILE = "background.json"


def resolve_energy_spectrum_runtime_paths(target: MachineProfile | AppContext) -> dict[str, Path]:
    base_paths = resolve_app_runtime_paths(APP_DIR, target)
    runtime_dir = base_paths["runtime_dir"]
    latest_dir = base_paths["latest_dir"]
    runs_dir = base_paths["runs_dir"]
    return {
        "runtime_dir": runtime_dir,
        "latest_dir": latest_dir,
        "result_archive_dir": runs_dir,
        "runs_dir": runs_dir,
        "latest_metadata_path": base_paths["latest_metadata_path"],
        "model_snapshot_path": latest_dir / MODEL_SNAPSHOT_FILE,
        "background_image_path": latest_dir / BACKGROUND_IMAGE_FILE,
        "background_metadata_path": latest_dir / BACKGROUND_METADATA_FILE,
    }
