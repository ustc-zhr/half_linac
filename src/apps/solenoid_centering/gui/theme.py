from __future__ import annotations


HEADER_ACTION_HEIGHT = 32

DARK_THEME = {
    "window_bg": "#0f1519",
    "window_fg": "#e6edf2",
    "panel_bg": "#172027",
    "panel_border": "#24333d",
    "summary_bg": "#1b262d",
    "summary_border": "#2b3a45",
    "summary_title_fg": "#f3efe3",
    "muted_fg": "#90a1ad",
    "button_bg": "#11191f",
    "button_border": "#2b3d48",
    "button_fg": "#edf3f7",
    "button_hover_bg": "#18242c",
    "button_pressed_bg": "#0c1217",
    "button_disabled_fg": "#6f7f89",
    "button_disabled_border": "#22313a",
    "button_disabled_bg": "#0f1519",
    "input_bg": "#10171c",
    "input_border": "#31424d",
    "input_fg": "#edf3f7",
    "plot_card_bg": "#121a20",
    "plot_bg": "#11181e",
    "plot_grid": "#2a3943",
    "plot_spine": "#445764",
    "plot_text": "#d7e2ea",
    "plot_point": "#78d5e3",
    "plot_best": "#ffcf66",
    "status_strip_bg": "#131c22",
    "status_strip_border": "#2a3943",
    "status_separator": "#31424d",
    "status_item_idle_bar": "#4f6270",
    "status_title_fg": "#8ea0ad",
    "metric_active_fg": "#45d0bc",
    "metric_warning_fg": "#e4b86f",
    "metric_danger_fg": "#ff8585",
    "metric_idle_fg": "#c8d2da",
}

LIGHT_THEME = {
    "window_bg": "#f2ede5",
    "window_fg": "#2c3942",
    "panel_bg": "#fffdf9",
    "panel_border": "#d7cec1",
    "summary_bg": "#fcf9f3",
    "summary_border": "#ddd4c8",
    "summary_title_fg": "#2d3940",
    "muted_fg": "#7c7368",
    "button_bg": "#f8f3eb",
    "button_border": "#d9d0c3",
    "button_fg": "#2c3942",
    "button_hover_bg": "#efe6d9",
    "button_pressed_bg": "#e3d8c8",
    "button_disabled_fg": "#91897e",
    "button_disabled_border": "#ddd4c8",
    "button_disabled_bg": "#f1ece4",
    "input_bg": "#fffdf9",
    "input_border": "#d9d0c3",
    "input_fg": "#2c3942",
    "plot_card_bg": "#f6f1e8",
    "plot_bg": "#fffdf8",
    "plot_grid": "#ddd4c7",
    "plot_spine": "#b5aa9a",
    "plot_text": "#304049",
    "plot_point": "#2f9aad",
    "plot_best": "#9a6715",
    "status_strip_bg": "#f7f1e8",
    "status_strip_border": "#ddd2c4",
    "status_separator": "#ddd4c7",
    "status_item_idle_bar": "#c8bfb3",
    "status_title_fg": "#7c7368",
    "metric_active_fg": "#2d7f6d",
    "metric_warning_fg": "#a97118",
    "metric_danger_fg": "#b54545",
    "metric_idle_fg": "#4e5a62",
}


def theme_palette(name: str) -> dict[str, str]:
    return DARK_THEME if name == "dark" else LIGHT_THEME


def build_stylesheet(palette: dict[str, str]) -> str:
    values = dict(palette, header_action_height=HEADER_ACTION_HEIGHT)
    return """
QMainWindow, QWidget#centralRoot {{
    background: {window_bg};
    color: {window_fg};
    font-family: "IBM Plex Sans", "Source Han Sans SC", "Segoe UI", sans-serif;
    font-size: 12px;
}}
QLabel {{
    background: transparent;
    border: none;
    color: {window_fg};
}}
QFrame#summaryPanel {{
    background: {summary_bg};
    border: 1px solid {summary_border};
    border-radius: 14px;
}}
QFrame#controlCard, QFrame#plotCard, QFrame#resultCard {{
    background: {panel_bg};
    border: 1px solid {panel_border};
    border-radius: 14px;
}}
QFrame#workspacePanel, QFrame#statusStrip {{
    background: transparent;
    border: none;
}}
QLabel#summaryTitle {{
    color: {summary_title_fg};
    font-size: 22px;
    font-weight: 700;
    letter-spacing: 0.3px;
}}
QLabel#panelTitle {{
    color: {summary_title_fg};
    font-size: 15px;
    font-weight: 700;
}}
QLabel#sectionTitle {{
    color: {summary_title_fg};
    font-size: 12px;
    font-weight: 700;
    padding: 3px 0 2px 0;
}}
QLabel[role="field"], QLabel[muted="true"] {{
    color: {muted_fg};
    font-size: 11px;
    font-weight: 600;
}}
QFrame#sectionSeparator, QFrame#statusSeparator {{
    background: {status_separator};
    border: none;
}}
QFrame#sectionSeparator {{
    min-height: 1px;
    max-height: 1px;
}}
QFrame#statusSeparator {{
    min-width: 1px;
    max-width: 1px;
}}
QFrame#statusItem {{
    background: transparent;
    border: none;
    border-left: 4px solid {status_item_idle_bar};
    border-radius: 0;
}}
QFrame#statusItem[tone="success"] {{ border-left-color: {metric_active_fg}; }}
QFrame#statusItem[tone="warning"] {{ border-left-color: {metric_warning_fg}; }}
QFrame#statusItem[tone="danger"] {{ border-left-color: {metric_danger_fg}; }}
QLabel[role="statusTitle"] {{
    color: {status_title_fg};
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.8px;
}}
QLabel[role="statusValue"] {{
    color: {metric_idle_fg};
    font-size: 13px;
    font-weight: 700;
}}
QLabel[role="statusValue"][tone="success"] {{ color: {metric_active_fg}; }}
QLabel[role="statusValue"][tone="warning"] {{ color: {metric_warning_fg}; }}
QLabel[role="statusValue"][tone="danger"] {{ color: {metric_danger_fg}; }}
QScrollArea#configurationScroll,
QScrollArea#configurationScroll > QWidget#qt_scrollarea_viewport,
QWidget#configurationContent {{
    background: transparent;
    border: none;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {button_border};
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QComboBox, QDoubleSpinBox, QSpinBox {{
    background: {input_bg};
    border: 1px solid {input_border};
    border-radius: 10px;
    color: {input_fg};
    min-height: 28px;
    padding: 1px 8px;
    selection-background-color: {metric_active_fg};
}}
QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus {{
    border-color: {metric_active_fg};
}}
QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox QAbstractItemView {{
    background: {input_bg};
    color: {input_fg};
    border: 1px solid {input_border};
    selection-background-color: {button_hover_bg};
}}
QPushButton {{
    background: {button_bg};
    border: 1px solid {button_border};
    border-radius: 11px;
    color: {button_fg};
    min-height: 32px;
    padding: 5px 10px;
    font-weight: 700;
}}
QPushButton:hover {{ background: {button_hover_bg}; }}
QPushButton:pressed {{ background: {button_pressed_bg}; }}
QPushButton:disabled {{
    background: {button_disabled_bg};
    border-color: {button_disabled_border};
    color: {button_disabled_fg};
}}
QPushButton[role="primary"] {{
    background: {metric_active_fg};
    border-color: {metric_active_fg};
    color: {window_bg};
}}
QPushButton[role="danger"] {{
    border-color: {metric_danger_fg};
    color: {metric_danger_fg};
}}
QPushButton[compact="true"] {{
    min-height: 26px;
    padding: 3px 8px;
    font-size: 11px;
}}
QToolButton#themeToggleButton {{
    background: {button_bg};
    border: 1px solid {button_border};
    border-radius: 11px;
    color: {button_fg};
    min-width: {header_action_height}px;
    max-width: {header_action_height}px;
    min-height: {header_action_height}px;
    max-height: {header_action_height}px;
    font-size: 14px;
    font-weight: 700;
}}
QToolButton#themeToggleButton:hover {{
    background: {button_hover_bg};
}}
QProgressBar {{
    background: {input_bg};
    border: 1px solid {input_border};
    border-radius: 7px;
    color: {window_fg};
    text-align: center;
    min-height: 18px;
}}
QProgressBar::chunk {{
    background: {metric_active_fg};
    border-radius: 6px;
}}
QTabWidget::pane {{
    background: {plot_card_bg};
    border: 1px solid {panel_border};
    border-radius: 12px;
    top: -1px;
}}
QTabBar::tab {{
    background: {button_bg};
    border: 1px solid {button_border};
    border-top-left-radius: 10px;
    border-top-right-radius: 10px;
    color: {button_fg};
    font-weight: 700;
    min-width: 100px;
    padding: 8px 12px;
    margin-right: 5px;
}}
QTabBar::tab:selected {{
    background: {plot_card_bg};
    color: {summary_title_fg};
}}
QTabBar::tab:hover:!selected {{
    background: {button_hover_bg};
}}
QToolBar {{
    background: {plot_card_bg};
    border: none;
    spacing: 2px;
}}
QToolBar QToolButton {{
    background: transparent;
    border: none;
    border-radius: 7px;
    padding: 3px;
}}
QToolBar QToolButton:hover {{
    background: {button_hover_bg};
}}
QTableWidget {{
    background: {input_bg};
    alternate-background-color: {panel_bg};
    border: 1px solid {panel_border};
    border-radius: 10px;
    color: {window_fg};
    gridline-color: {panel_border};
    selection-background-color: {metric_active_fg};
    selection-color: {window_bg};
}}
QHeaderView::section {{
    background: {summary_bg};
    border: none;
    border-right: 1px solid {panel_border};
    border-bottom: 1px solid {panel_border};
    color: {muted_fg};
    padding: 6px;
    font-size: 11px;
    font-weight: 700;
}}
QSplitter::handle {{
    background: transparent;
}}
""".format_map(values)
