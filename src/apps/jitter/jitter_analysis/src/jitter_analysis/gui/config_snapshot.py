from __future__ import annotations

from pathlib import Path

from ..config.models import PvListConfig


def config_snapshot_text(config: PvListConfig | None) -> str:
    if config is None:
        raise RuntimeError("No PV library is loaded.")

    source_text = str(getattr(config, "source_text", "") or "").strip()
    if source_text:
        return source_text

    source_path = str(config.source_path or "").strip()
    if not source_path:
        raise RuntimeError("The loaded PV library has no source path and cannot be snapshotted.")

    return Path(source_path).read_text(encoding="utf-8")
