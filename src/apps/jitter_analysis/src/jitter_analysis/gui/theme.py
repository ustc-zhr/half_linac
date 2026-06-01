from __future__ import annotations

from dataclasses import dataclass

try:
    from PyQt5 import QtWidgets
except ImportError:  # pragma: no cover - optional runtime dependency
    QtWidgets = None


@dataclass(frozen=True, slots=True)
class ThemeSpec:
    theme_id: str
    label: str
    window_bg: str
    panel_bg: str
    section_bg: str
    section_border: str
    section_title: str
    input_bg: str
    border: str
    border_soft: str
    text_primary: str
    text_muted: str
    text_disabled: str
    focus: str
    button_bg: str
    button_hover: str
    subtle_bg: str
    subtle_hover: str
    subtle_text: str
    primary_bg: str
    primary_hover: str
    primary_text: str
    primary_border: str
    danger_bg: str
    danger_hover: str
    danger_text: str
    danger_border: str
    tab_bg: str
    tab_hover: str
    tab_selected_bg: str
    tab_text: str
    tab_selected_text: str
    tab_disabled_bg: str
    tab_disabled_text: str
    menu_selected_bg: str
    menu_selected_text: str
    mode_toggle_bg: str
    mode_toggle_hover: str
    mode_toggle_checked_bg: str
    mode_toggle_checked_text: str
    mode_toggle_checked_border: str
    scroll_bg: str
    scroll_handle: str
    tooltip_bg: str
    tooltip_fg: str
    splitter: str


DEFAULT_THEME_ID = "control_room"


THEMES: dict[str, ThemeSpec] = {
    "control_room": ThemeSpec(
        theme_id="control_room",
        label="Control Room",
        window_bg="#f2ede6",
        panel_bg="#fffaf3",
        section_bg="#fffdf9",
        section_border="#ddd4c7",
        section_title="#1f2937",
        input_bg="#fffdf8",
        border="#d8d0c3",
        border_soft="#e7dfd3",
        text_primary="#102033",
        text_muted="#6f6253",
        text_disabled="#94a3b8",
        focus="#60a5fa",
        button_bg="#f8f4ed",
        button_hover="#f1eade",
        subtle_bg="#fffdf8",
        subtle_hover="#f6efe5",
        subtle_text="#475569",
        primary_bg="#dfe9f7",
        primary_hover="#d2e1f3",
        primary_text="#15324d",
        primary_border="#a9bfd7",
        danger_bg="#f9ebe6",
        danger_hover="#f5dfd6",
        danger_text="#8f3d28",
        danger_border="#d9b0a1",
        tab_bg="#ece5da",
        tab_hover="#f5efe6",
        tab_selected_bg="#fffdf9",
        tab_text="#475569",
        tab_selected_text="#102033",
        tab_disabled_bg="#f7f2eb",
        tab_disabled_text="#a8b0bb",
        menu_selected_bg="#e0ecff",
        menu_selected_text="#102033",
        mode_toggle_bg="#f7f1e8",
        mode_toggle_hover="#efe7db",
        mode_toggle_checked_bg="#dbe8f6",
        mode_toggle_checked_text="#173857",
        mode_toggle_checked_border="#8eb1d2",
        scroll_bg="#efe7db",
        scroll_handle="#c8bba8",
        tooltip_bg="#102033",
        tooltip_fg="#f8fafc",
        splitter="#e6ddd1",
    ),
    "mist_blue": ThemeSpec(
        theme_id="mist_blue",
        label="Mist Blue",
        window_bg="#ebf1f6",
        panel_bg="#f7fbff",
        section_bg="#fcfeff",
        section_border="#cbd9e6",
        section_title="#163047",
        input_bg="#ffffff",
        border="#bfd0df",
        border_soft="#dbe7f0",
        text_primary="#11283c",
        text_muted="#5e7387",
        text_disabled="#8ea0b2",
        focus="#4f8fd6",
        button_bg="#eff5fb",
        button_hover="#e2edf8",
        subtle_bg="#ffffff",
        subtle_hover="#eef4fb",
        subtle_text="#496178",
        primary_bg="#d7e7f8",
        primary_hover="#cadef4",
        primary_text="#173d64",
        primary_border="#92b6d9",
        danger_bg="#f8eceb",
        danger_hover="#f1dedd",
        danger_text="#944839",
        danger_border="#d7b0ab",
        tab_bg="#e3edf6",
        tab_hover="#eef5fb",
        tab_selected_bg="#fcfeff",
        tab_text="#4a6379",
        tab_selected_text="#11283c",
        tab_disabled_bg="#f3f7fa",
        tab_disabled_text="#9eb0c0",
        menu_selected_bg="#d9ebff",
        menu_selected_text="#10273c",
        mode_toggle_bg="#eef4fa",
        mode_toggle_hover="#e2ecf6",
        mode_toggle_checked_bg="#d7e6f7",
        mode_toggle_checked_text="#17446d",
        mode_toggle_checked_border="#84add2",
        scroll_bg="#e3edf6",
        scroll_handle="#afc4d7",
        tooltip_bg="#14324a",
        tooltip_fg="#f8fbff",
        splitter="#d7e3ee",
    ),
    "sage_light": ThemeSpec(
        theme_id="sage_light",
        label="Sage Light",
        window_bg="#eef1ea",
        panel_bg="#fafcf7",
        section_bg="#fefffc",
        section_border="#cfd8c8",
        section_title="#223127",
        input_bg="#ffffff",
        border="#c8d3c0",
        border_soft="#dde5d8",
        text_primary="#1e2b22",
        text_muted="#647264",
        text_disabled="#93a192",
        focus="#6aab88",
        button_bg="#f2f6ef",
        button_hover="#e7eee2",
        subtle_bg="#ffffff",
        subtle_hover="#f1f5ee",
        subtle_text="#516351",
        primary_bg="#dcebdc",
        primary_hover="#d0e3d0",
        primary_text="#26472f",
        primary_border="#9ec09e",
        danger_bg="#f9ece8",
        danger_hover="#f3dfd8",
        danger_text="#914f38",
        danger_border="#d5b2a5",
        tab_bg="#e5ece0",
        tab_hover="#eff4eb",
        tab_selected_bg="#fefffc",
        tab_text="#5a6957",
        tab_selected_text="#1e2b22",
        tab_disabled_bg="#f4f7f1",
        tab_disabled_text="#a1aea0",
        menu_selected_bg="#e0f0e0",
        menu_selected_text="#1d2b21",
        mode_toggle_bg="#eef4ea",
        mode_toggle_hover="#e3ecde",
        mode_toggle_checked_bg="#dae9d8",
        mode_toggle_checked_text="#2e5234",
        mode_toggle_checked_border="#91b691",
        scroll_bg="#e4ecdf",
        scroll_handle="#b5c4b0",
        tooltip_bg="#223127",
        tooltip_fg="#f7fbf6",
        splitter="#d9e2d4",
    ),
    "rose_desk": ThemeSpec(
        theme_id="rose_desk",
        label="Rose Desk",
        window_bg="#f4eceb",
        panel_bg="#fff8f7",
        section_bg="#fffdfc",
        section_border="#dfcfcb",
        section_title="#342324",
        input_bg="#fffefe",
        border="#d7c7c3",
        border_soft="#eadfdd",
        text_primary="#2c1f22",
        text_muted="#796268",
        text_disabled="#a18e95",
        focus="#c77b95",
        button_bg="#faf1f0",
        button_hover="#f4e5e3",
        subtle_bg="#fffefe",
        subtle_hover="#f9efee",
        subtle_text="#6b5560",
        primary_bg="#efd8df",
        primary_hover="#e8ccd5",
        primary_text="#65384b",
        primary_border="#caa0af",
        danger_bg="#f9e8e3",
        danger_hover="#f3dad2",
        danger_text="#924935",
        danger_border="#d9ae9f",
        tab_bg="#efdfdc",
        tab_hover="#f7ecea",
        tab_selected_bg="#fffdfc",
        tab_text="#6f5960",
        tab_selected_text="#2c1f22",
        tab_disabled_bg="#f8f1f0",
        tab_disabled_text="#b09ea5",
        menu_selected_bg="#f4dee6",
        menu_selected_text="#2d2023",
        mode_toggle_bg="#f8efee",
        mode_toggle_hover="#f1e1de",
        mode_toggle_checked_bg="#edd7de",
        mode_toggle_checked_text="#693c50",
        mode_toggle_checked_border="#c294a7",
        scroll_bg="#efe0dd",
        scroll_handle="#cab6b2",
        tooltip_bg="#302125",
        tooltip_fg="#fff8f8",
        splitter="#e2d3d0",
    ),
    "night_shift": ThemeSpec(
        theme_id="night_shift",
        label="Night Shift",
        window_bg="#0f141b",
        panel_bg="#161d26",
        section_bg="#1b2430",
        section_border="#2b3645",
        section_title="#e8eef7",
        input_bg="#131b24",
        border="#334155",
        border_soft="#243142",
        text_primary="#e5edf8",
        text_muted="#93a4bb",
        text_disabled="#63748a",
        focus="#5fa8ff",
        button_bg="#1a2330",
        button_hover="#223041",
        subtle_bg="#131b24",
        subtle_hover="#1b2634",
        subtle_text="#b1c0d4",
        primary_bg="#173451",
        primary_hover="#204264",
        primary_text="#e8f3ff",
        primary_border="#35628f",
        danger_bg="#3b1f27",
        danger_hover="#4c2731",
        danger_text="#ffccd4",
        danger_border="#7d4656",
        tab_bg="#1a2330",
        tab_hover="#233042",
        tab_selected_bg="#1f2b3a",
        tab_text="#a8b6c9",
        tab_selected_text="#f4f7fb",
        tab_disabled_bg="#141c26",
        tab_disabled_text="#5d6b7c",
        menu_selected_bg="#1f3852",
        menu_selected_text="#f3f8ff",
        mode_toggle_bg="#17212d",
        mode_toggle_hover="#202d3b",
        mode_toggle_checked_bg="#22415f",
        mode_toggle_checked_text="#eff6ff",
        mode_toggle_checked_border="#5a8ec3",
        scroll_bg="#111823",
        scroll_handle="#415368",
        tooltip_bg="#f3f7fc",
        tooltip_fg="#0f1720",
        splitter="#253140",
    ),
}


def available_themes() -> tuple[ThemeSpec, ...]:
    return tuple(THEMES.values())


def theme_spec(theme_id: str | None = None) -> ThemeSpec:
    token = str(theme_id or DEFAULT_THEME_ID).strip()
    return THEMES.get(token, THEMES[DEFAULT_THEME_ID])


def theme_label(theme_id: str | None = None) -> str:
    return theme_spec(theme_id).label


def current_theme_id(app=None) -> str:
    target_app = app
    if target_app is None and QtWidgets is not None:
        target_app = QtWidgets.QApplication.instance()
    if target_app is None:
        return DEFAULT_THEME_ID
    value = target_app.property("gui_theme_id")
    return str(value).strip() if value else DEFAULT_THEME_ID


def build_app_stylesheet(theme_id: str | ThemeSpec | None = None) -> str:
    spec = theme_id if isinstance(theme_id, ThemeSpec) else theme_spec(theme_id)
    return f"""
QWidget {{
    color: {spec.text_primary};
    font-family: "Segoe UI", "PingFang SC", "Noto Sans CJK SC", sans-serif;
    font-size: 13px;
}}
QMainWindow, QDialog {{
    background: {spec.window_bg};
}}
QLabel {{
    background: transparent;
}}
QLabel[role="pageHint"] {{
    color: {spec.text_muted};
}}
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {spec.input_bg};
    border: 1px solid {spec.border};
    border-radius: 8px;
    padding: 6px 8px;
    selection-background-color: {spec.focus};
    selection-color: #ffffff;
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border: 1px solid {spec.focus};
}}
QPushButton, QToolButton {{
    background: {spec.button_bg};
    color: {spec.text_primary};
    border: 1px solid {spec.border};
    border-radius: 8px;
    padding: 6px 12px;
}}
QPushButton:hover, QToolButton:hover {{
    background: {spec.button_hover};
}}
QPushButton:disabled, QToolButton:disabled {{
    color: {spec.text_disabled};
    background: {spec.panel_bg};
    border-color: {spec.border_soft};
}}
QPushButton[role="subtle"], QToolButton[role="subtle"] {{
    background: {spec.subtle_bg};
    color: {spec.subtle_text};
    border: 1px solid {spec.border};
    font-weight: 600;
}}
QPushButton[role="subtle"]:hover, QToolButton[role="subtle"]:hover {{
    background: {spec.subtle_hover};
}}
QPushButton[role="primary"], QToolButton[role="primary"] {{
    background: {spec.primary_bg};
    color: {spec.primary_text};
    border: 1px solid {spec.primary_border};
    font-weight: 700;
}}
QPushButton[role="primary"]:hover, QToolButton[role="primary"]:hover {{
    background: {spec.primary_hover};
}}
QPushButton[role="danger"], QToolButton[role="danger"] {{
    background: {spec.danger_bg};
    color: {spec.danger_text};
    border: 1px solid {spec.danger_border};
    font-weight: 600;
}}
QPushButton[role="danger"]:hover, QToolButton[role="danger"]:hover {{
    background: {spec.danger_hover};
}}
QToolButton[themeRole="modeToggle"] {{
    background: {spec.mode_toggle_bg};
    color: {spec.text_primary};
    border: 1px solid {spec.border};
    border-radius: 8px;
    padding: 6px 12px;
    font-weight: 600;
}}
QToolButton[themeRole="modeToggle"]:hover {{
    background: {spec.mode_toggle_hover};
}}
QToolButton[themeRole="modeToggle"]:checked {{
    background: {spec.mode_toggle_checked_bg};
    color: {spec.mode_toggle_checked_text};
    border: 1px solid {spec.mode_toggle_checked_border};
}}
QToolButton[themeRole="modeToggle"]:disabled {{
    background: {spec.panel_bg};
    color: {spec.text_disabled};
    border: 1px solid {spec.border_soft};
}}
QToolButton[themeRole="modeToggle"]:checked:disabled {{
    background: {spec.panel_bg};
    color: {spec.text_disabled};
    border: 1px solid {spec.border_soft};
}}
QTableWidget, QTreeWidget, QListWidget {{
    background: {spec.section_bg};
    alternate-background-color: {spec.panel_bg};
    border: 1px solid {spec.section_border};
    border-radius: 10px;
    gridline-color: {spec.border_soft};
}}
QHeaderView::section {{
    background: {spec.panel_bg};
    color: {spec.text_primary};
    border: none;
    border-right: 1px solid {spec.section_border};
    border-bottom: 1px solid {spec.section_border};
    padding: 8px 10px;
    font-weight: 700;
}}
QGroupBox[themeSection="main"] {{
    font-size: 15px;
    font-weight: 700;
    color: {spec.section_title};
    border: 1px solid {spec.section_border};
    border-radius: 12px;
    background: {spec.section_bg};
    margin-top: 14px;
    padding-top: 6px;
}}
QGroupBox[themeSection="main"]::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
}}
QTabWidget#workspaceTabs::pane {{
    border: none;
    background: transparent;
    top: -1px;
}}
QTabBar#workspaceTabBar::tab {{
    background: {spec.tab_bg};
    color: {spec.tab_text};
    border: 1px solid {spec.border};
    border-bottom: none;
    border-top-left-radius: 10px;
    border-top-right-radius: 10px;
    padding: 10px 18px;
    margin-right: 6px;
    min-width: 96px;
    font-size: 13px;
    font-weight: 600;
}}
QTabBar#workspaceTabBar::tab:selected {{
    background: {spec.tab_selected_bg};
    color: {spec.tab_selected_text};
    border-color: {spec.section_border};
}}
QTabBar#workspaceTabBar::tab:hover:!selected {{
    background: {spec.tab_hover};
}}
QTabBar#workspaceTabBar::tab:disabled {{
    background: {spec.tab_disabled_bg};
    color: {spec.tab_disabled_text};
    border-color: {spec.border_soft};
}}
QTabWidget#analysisTabs::pane {{
    border: 1px solid {spec.section_border};
    border-radius: 12px;
    background: {spec.section_bg};
    top: -1px;
}}
QTabBar#analysisTabBar::tab {{
    background: {spec.tab_bg};
    color: {spec.tab_text};
    border: 1px solid {spec.border};
    border-bottom: none;
    border-top-left-radius: 10px;
    border-top-right-radius: 10px;
    padding: 8px 12px;
    margin-right: 4px;
    min-width: 0px;
    font-weight: 600;
}}
QTabBar#analysisTabBar::tab:selected {{
    background: {spec.tab_selected_bg};
    color: {spec.tab_selected_text};
    border-color: {spec.section_border};
}}
QTabBar#analysisTabBar::tab:hover:!selected {{
    background: {spec.tab_hover};
}}
QTabBar#analysisTabBar::tab:disabled {{
    background: {spec.tab_disabled_bg};
    color: {spec.tab_disabled_text};
    border-color: {spec.border_soft};
}}
QMenuBar {{
    background: {spec.panel_bg};
    color: {spec.text_primary};
    border-bottom: 1px solid {spec.section_border};
}}
QMenuBar::item {{
    padding: 6px 12px;
    border-radius: 6px;
    background: transparent;
}}
QMenuBar::item:selected {{
    background: {spec.menu_selected_bg};
    color: {spec.menu_selected_text};
}}
QMenu {{
    background: {spec.section_bg};
    border: 1px solid {spec.section_border};
    padding: 6px;
}}
QMenu::item {{
    padding: 6px 20px 6px 12px;
    border-radius: 6px;
}}
QMenu::item:selected {{
    background: {spec.menu_selected_bg};
    color: {spec.menu_selected_text};
}}
QScrollBar:vertical {{
    background: {spec.scroll_bg};
    width: 12px;
    margin: 10px 0 10px 0;
    border-radius: 6px;
}}
QScrollBar::handle:vertical {{
    background: {spec.scroll_handle};
    min-height: 28px;
    border-radius: 6px;
}}
QScrollBar:horizontal {{
    background: {spec.scroll_bg};
    height: 12px;
    margin: 0 10px 0 10px;
    border-radius: 6px;
}}
QScrollBar::handle:horizontal {{
    background: {spec.scroll_handle};
    min-width: 28px;
    border-radius: 6px;
}}
QScrollBar::add-line, QScrollBar::sub-line,
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
    border: none;
}}
QSplitter::handle {{
    background: {spec.splitter};
}}
QToolTip {{
    background: {spec.tooltip_bg};
    color: {spec.tooltip_fg};
    border: 1px solid {spec.border};
    padding: 6px 8px;
}}
"""


def apply_app_theme(app, theme_id: str | None = None) -> str:
    if app is None:
        return DEFAULT_THEME_ID
    spec = theme_spec(theme_id)
    set_style = getattr(app, "setStyle", None)
    if callable(set_style):
        set_style("Fusion")
    app.setProperty("gui_theme_id", spec.theme_id)
    app.setProperty("gui_theme_label", spec.label)
    app.setStyleSheet(build_app_stylesheet(spec))
    return spec.theme_id
