from __future__ import annotations

from pathlib import Path

from half_linac.src.shared.machine_profile import (
    AppContext,
    MachineProfile,
    resolve_app_runtime_paths,
    sanitize_runtime_token,
)


APP_DIR = Path(__file__).resolve().parent


def resolve_beam_monitor_background_paths(
    target: MachineProfile | AppContext,
    flag_id: str,
) -> dict[str, Path]:
    base_paths = resolve_app_runtime_paths(APP_DIR, target)
    flag_token = sanitize_runtime_token(flag_id, fallback="flag")
    latest_dir = base_paths["latest_dir"] / "backgrounds" / flag_token
    return {
        **base_paths,
        "background_dir": latest_dir,
        "background_image_path": latest_dir / "background.npy",
        "background_metadata_path": latest_dir / "background.json",
    }
