from __future__ import annotations

from dataclasses import dataclass

try:
    from ..theme import DEFAULT_THEME_ID, current_theme_id
except ImportError:  # pragma: no cover - fallback when imported out of package context
    DEFAULT_THEME_ID = "night_shift"
    current_theme_id = None

try:
    from PyQt5 import QtGui, QtWidgets
except ImportError:  # pragma: no cover - optional runtime dependency
    QtGui = None
    QtWidgets = None

try:
    import pyqtgraph as pg
except ImportError:  # pragma: no cover - optional runtime dependency
    pg = None


@dataclass(frozen=True, slots=True)
class PlotThemeSpec:
    background: str
    foreground: str
    axis: str
    grid_alpha: float
    legend_bg: str
    legend_border: str


def plot_theme_spec(theme_id: str | None = None) -> PlotThemeSpec:
    token = str(theme_id or _active_theme_id()).strip().lower()
    if token == "night_shift":
        return PlotThemeSpec(
            background="#11181e",
            foreground="#d7e2ea",
            axis="#445764",
            grid_alpha=0.2,
            legend_bg="#172027",
            legend_border="#31424d",
        )
    return PlotThemeSpec(
        background="#fffdf8",
        foreground="#304049",
        axis="#b5aa9a",
        grid_alpha=0.2,
        legend_bg="#fffdf9",
        legend_border="#d9d0c3",
    )


def _active_theme_id() -> str:
    if current_theme_id is not None and QtWidgets is not None:
        return current_theme_id(QtWidgets.QApplication.instance())
    return DEFAULT_THEME_ID


def apply_plot_theme(theme_id: str | None = None) -> None:
    if pg is None:
        return
    spec = plot_theme_spec(theme_id)
    pg.setConfigOption("background", spec.background)
    pg.setConfigOption("foreground", spec.foreground)
    pg.setConfigOption("antialias", True)


def style_plot_widgets_in_tree(root, theme_id: str | None = None) -> None:
    if pg is None or root is None:
        return
    try:
        plot_widgets = root.findChildren(pg.PlotWidget)
    except Exception:
        return
    for plot_widget in plot_widgets:
        style_plot_widget(plot_widget, theme_id)


def style_plot_widget(plot_widget, theme_id: str | None = None) -> None:
    if pg is None or plot_widget is None:
        return

    spec = plot_theme_spec(theme_id)
    _safe_call(plot_widget, "setBackground", spec.background)
    _safe_call(plot_widget, "setStyleSheet", f"background: {spec.background}; border: none;")

    plot_item = _safe_call(plot_widget, "getPlotItem")
    if plot_item is None:
        return

    view_box = _safe_call(plot_item, "getViewBox")
    if view_box is not None:
        _safe_call(view_box, "setBackgroundColor", spec.background)

    for axis_name in ("left", "bottom", "right", "top"):
        axis = _safe_call(plot_item, "getAxis", axis_name)
        if axis is None:
            continue
        _safe_call(axis, "setPen", pg.mkPen(spec.axis, width=1))
        _safe_call(axis, "setTextPen", pg.mkPen(spec.foreground))

    legend = getattr(plot_item, "legend", None)
    if legend is not None:
        _safe_call(legend, "setBrush", pg.mkBrush(spec.legend_bg))
        _safe_call(legend, "setPen", pg.mkPen(spec.legend_border, width=1))
        _style_legend_labels(legend, spec.foreground)

    title_label = getattr(plot_item, "titleLabel", None)
    if title_label is not None:
        _safe_call(title_label, "setAttr", "color", spec.foreground)

    _safe_call(plot_item, "showGrid", x=True, y=True, alpha=spec.grid_alpha)
    _safe_call(plot_widget, "update")


def _style_legend_labels(legend, color: str) -> None:
    _safe_call(legend, "setLabelTextColor", color)
    for item in getattr(legend, "items", []) or []:
        if not isinstance(item, tuple) or len(item) < 2:
            continue
        label = item[1]
        _style_legend_label(label, color)
    _safe_call(legend, "updateSize")


def _style_legend_label(label, color: str) -> None:
    if label is None:
        return
    opts = getattr(label, "opts", None)
    if isinstance(opts, dict):
        opts["color"] = color
    _safe_call(label, "setAttr", "color", color)
    text_value = getattr(label, "text", None)
    if isinstance(text_value, str):
        _safe_call(label, "setText", text_value, color=color)
    text_item = getattr(label, "item", None) or getattr(label, "textItem", None)
    if text_item is not None and QtGui is not None:
        _safe_call(text_item, "setDefaultTextColor", QtGui.QColor(color))
    _safe_call(label, "update")


def _safe_call(target, method_name: str, *args, **kwargs):
    method = getattr(target, method_name, None)
    if not callable(method):
        return None
    try:
        return method(*args, **kwargs)
    except TypeError:
        return None
    except RuntimeError:
        return None
