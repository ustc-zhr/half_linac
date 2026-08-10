from __future__ import annotations

from pathlib import Path

from half_linac.src.shared.beam_diagnostics import resolve_beam_background_paths
from half_linac.src.shared.machine_profile import AppContext, MachineProfile


def resolve_beam_monitor_background_paths(
    target: MachineProfile | AppContext,
    flag_id: str,
) -> dict[str, Path]:
    return resolve_beam_background_paths(target, flag_id)
