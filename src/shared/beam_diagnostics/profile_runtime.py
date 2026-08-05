from __future__ import annotations

from pathlib import Path

from half_linac.src.shared.machine_profile import (
    AppContext,
    MachineProfile,
    resolve_app_runtime_paths,
    sanitize_runtime_token,
)


DEFAULT_BACKGROUND_APP_DIR = Path(__file__).resolve().parents[2] / "apps" / "beam_monitor"


def resolve_beam_background_paths(
    target: MachineProfile | AppContext,
    flag_id: str,
    *,
    runtime_owner_dir: Path | str = DEFAULT_BACKGROUND_APP_DIR,
) -> dict[str, Path]:
    """Resolve the shared per-camera background reference paths.

    The default location deliberately preserves the existing Beam Monitor runtime
    layout while allowing every beam-diagnostics client to use the same files.
    """
    base_paths = resolve_app_runtime_paths(runtime_owner_dir, target)
    flag_token = sanitize_runtime_token(flag_id, fallback="flag")
    background_dir = base_paths["latest_dir"] / "backgrounds" / flag_token
    return {
        **base_paths,
        "background_dir": background_dir,
        "background_image_path": background_dir / "background.npy",
        "background_metadata_path": background_dir / "background.json",
    }
