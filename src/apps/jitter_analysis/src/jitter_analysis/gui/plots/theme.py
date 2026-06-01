from __future__ import annotations

try:
    from ..theme import DEFAULT_THEME_ID
except ImportError:  # pragma: no cover - fallback when imported out of package context
    DEFAULT_THEME_ID = "control_room"

try:
    import pyqtgraph as pg
except ImportError:  # pragma: no cover - optional runtime dependency
    pg = None


def apply_plot_theme(theme_id: str | None = None) -> None:
    if pg is None:
        return
    token = str(theme_id or DEFAULT_THEME_ID).strip().lower()
    if token == "night_shift":
        pg.setConfigOption("background", "#131b24")
        pg.setConfigOption("foreground", "#dce7f5")
    else:
        pg.setConfigOption("background", "#fbfbfd")
        pg.setConfigOption("foreground", "#1f2937")
    pg.setConfigOption("antialias", True)
