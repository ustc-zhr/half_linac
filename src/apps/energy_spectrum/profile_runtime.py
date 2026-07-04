from __future__ import annotations

from pathlib import Path

from half_linac.src.shared.machine_profile import AppContext, MachineProfile


APP_DIR = Path(__file__).resolve().parent
ENERGY_SPECTRUM_RUNTIME_ROOT = APP_DIR / "runtime"
MODEL_SNAPSHOT_FILE = "latest_model_snapshot.json"


def resolve_energy_spectrum_runtime_paths(target: MachineProfile | AppContext) -> dict[str, Path]:
    profile = target.profile if isinstance(target, AppContext) else target
    backend = target.control_backend.name if isinstance(target, AppContext) else profile.machine.default_mode
    runtime_dir = ENERGY_SPECTRUM_RUNTIME_ROOT / profile.machine.id / backend
    latest_dir = runtime_dir / "latest"
    return {
        "runtime_dir": runtime_dir,
        "latest_dir": latest_dir,
        "model_snapshot_path": latest_dir / MODEL_SNAPSHOT_FILE,
    }
