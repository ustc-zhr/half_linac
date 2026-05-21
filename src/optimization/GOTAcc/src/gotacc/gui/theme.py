from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from string import Template

from PyQt5.QtWidgets import QApplication


@dataclass(frozen=True)
class ThemeSpec:
    key: str
    label: str
    palette: dict[str, str]


DEFAULT_THEME_KEY = "ocean_blueprint"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _theme_store_path() -> Path:
    cache_dir = _repo_root() / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "gui_theme.json"


THEME_TEMPLATE = Template(
    """
QWidget {
    background: $app_bg;
    color: $text_main;
    selection-background-color: $accent;
    selection-color: $selection_text;
    font-size: 13px;
}

QMainWindow, QDialog, QMenuBar, QMenu, QStatusBar, QDockWidget {
    background: $window_bg;
}

QFrame#frame_leftNav {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 $nav_start, stop:1 $nav_end);
    border: 1px solid $nav_border;
    border-radius: 20px;
}

QLabel#label_appTag {
    background: transparent;
    color: $nav_tag;
    font-size: 10px;
    font-weight: 700;
}

QLabel#label_appTitle {
    background: transparent;
    color: $nav_title;
    font-size: 26px;
    font-weight: 700;
}

QLabel#label_appSubtitle {
    background: transparent;
    color: $nav_subtitle;
}

QListWidget#listWidget_navPages {
    background: transparent;
    border: none;
    color: $nav_item_text;
    outline: 0;
    padding: 4px 0;
}

QListWidget#listWidget_navPages::item {
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.10);
    border-radius: 16px;
    color: $nav_item_text;
    padding: 16px 18px;
    margin: 3px 0;
    font-size: 21px;
    font-weight: 900;
    min-height: 60px;
}

QListWidget#listWidget_navPages::item:hover {
    background: $nav_item_hover;
    border-color: rgba(255, 255, 255, 0.22);
}

QListWidget#listWidget_navPages::item:selected {
    background: $nav_item_selected_bg;
    border-color: $nav_item_selected_bg;
    color: $nav_item_selected_text;
    font-weight: 950;
}

QGroupBox {
    background: $panel_bg;
    border: 1px solid $panel_border;
    border-radius: 16px;
    margin-top: 16px;
    font-weight: 700;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: $panel_title;
}

QGroupBox#groupBox_primaryNav {
    background: rgba(255, 255, 255, 0.08);
    border: 1px solid rgba(255, 255, 255, 0.16);
    border-radius: 16px;
}

QGroupBox#groupBox_primaryNav::title {
    color: $nav_subtitle;
    font-weight: 700;
}

QFrame#frame_dashboardHero,
QFrame#frame_builderHero,
QFrame#frame_machineHero,
QFrame#frame_runHero,
QFrame#frame_cardCurrentTask,
QFrame#frame_cardMode,
QFrame#frame_cardAlgorithm,
QFrame#frame_cardStatus,
QFrame#frame_eval,
QFrame#frame_elapsed,
QFrame#frame_best,
QFrame#frame_feasibility,
QFrame#frame_phase {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 $hero_start, stop:1 $hero_end);
    border: 1px solid $hero_border;
    border-radius: 18px;
}

QFrame#frame_variablesToolbar {
    background: $panel_bg;
    border: 1px solid $panel_border;
    border-radius: 12px;
}

QLabel#label_dashboardHeroTitle,
QLabel#label_builderTitle,
QLabel#label_machineTitle,
QLabel#label_runTitle {
    background: transparent;
    color: $hero_title;
    font-size: 24px;
    font-weight: 700;
}

QLabel#label_dashboardHeroText,
QLabel#label_builderSubtitle,
QLabel#label_builderSummary,
QLabel#label_machineSubtitle,
QLabel#label_machineSummary,
QLabel#label_runSummary {
    background: transparent;
    color: $hero_text;
}

QLabel#label_cardCurrentTaskTitle,
QLabel#label_cardModeTitle,
QLabel#label_cardAlgorithmTitle,
QLabel#label_cardStatusTitle,
QLabel#label_evalTitle,
QLabel#label_elapsedTitle,
QLabel#label_bestTitle,
QLabel#label_feasibilityTitle,
QLabel#label_phaseTitle {
    background: transparent;
    color: $card_title;
    font-size: 11px;
    font-weight: 700;
}

QLabel#label_cardCurrentTaskValue,
QLabel#label_cardModeValue,
QLabel#label_cardAlgorithmValue,
QLabel#label_cardStatusValue,
QLabel#label_evalValue,
QLabel#label_elapsedValue,
QLabel#label_bestValue,
QLabel#label_feasibilityValue,
QLabel#label_phaseValue {
    background: transparent;
    color: $card_value;
    font-size: 20px;
    font-weight: 700;
}

QLabel#label_budgetHint,
QLabel#label_dynamicHint,
QLabel#label_builderWorkflowHint,
QLabel#label_previewHint,
QLabel#label_connectionHint,
QLabel#label_guardHint,
QLabel#label_machineWorkflowHint,
QLabel#label_mappingHint,
QLabel#label_writePolicyHint,
QLabel#label_objectivePolicyHint,
QLabel#label_templateHint,
QLabel#label_templateSubHint,
QLabel#label_templateLibraryHint,
QLabel#label_templateDetailsHint,
QLabel#label_templateActionsHint,
QLabel#label_actionsHint {
    background: $hint_bg;
    border: 1px solid $hint_border;
    border-radius: 12px;
    color: $hint_text;
    padding: 10px;
}

QLabel#label_selectedTemplateSummary {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 $summary_start, stop:1 $summary_end);
    border: 1px solid $summary_border;
    border-radius: 12px;
    color: $summary_text;
    font-weight: 700;
    padding: 10px 12px;
}

QPushButton {
    background: $button_bg;
    border: 1px solid $button_border;
    border-radius: 12px;
    color: $button_text;
    padding: 8px 14px;
}

QPushButton:hover {
    background: $button_hover_bg;
    border-color: $button_hover_border;
}

QPushButton:pressed {
    background: $button_pressed_bg;
}

QPushButton#pushButton_startRun,
QPushButton#pushButton_start,
QPushButton#pushButton_applyTemplate {
    background: $primary_button_bg;
    border-color: $primary_button_bg;
    color: $primary_button_text;
}

QPushButton#pushButton_startRun:hover,
QPushButton#pushButton_start:hover,
QPushButton#pushButton_applyTemplate:hover {
    background: $primary_button_hover;
    border-color: $primary_button_hover;
}

QPushButton#pushButton_applyTemplate {
    font-size: 14px;
    font-weight: 700;
    padding: 10px 16px;
}

QPushButton#pushButton_stopRun,
QPushButton#pushButton_stop {
    background: $danger_button_bg;
    border-color: $danger_button_bg;
    color: $primary_button_text;
}

QPushButton#pushButton_stopRun:hover,
QPushButton#pushButton_stop:hover {
    background: $danger_button_hover;
    border-color: $danger_button_hover;
}

QLineEdit,
QPlainTextEdit,
QSpinBox,
QDoubleSpinBox,
QComboBox,
QTableWidget,
QTreeWidget,
QListWidget,
QTabWidget::pane {
    background: $input_bg;
    border: 1px solid $input_border;
    border-radius: 12px;
}

QLineEdit,
QSpinBox,
QDoubleSpinBox,
QComboBox {
    padding: 6px 8px;
    min-height: 20px;
}

QPlainTextEdit {
    padding: 8px;
}

QTableWidget,
QTreeWidget,
QListWidget {
    alternate-background-color: $table_alt_bg;
    gridline-color: $table_grid;
}

QHeaderView::section {
    background: $header_bg;
    border: none;
    border-right: 1px solid $header_border;
    border-bottom: 1px solid $header_border;
    color: $header_text;
    font-weight: 700;
    padding: 6px 8px;
}

QTabWidget::pane {
    top: -1px;
}

QTabBar::tab {
    background: $tab_bg;
    border: 1px solid $tab_border;
    border-bottom: none;
    border-top-left-radius: 10px;
    border-top-right-radius: 10px;
    color: $tab_text;
    padding: 8px 12px;
    margin-right: 4px;
}

QTabBar::tab:selected {
    background: $tab_selected_bg;
    color: $tab_selected_text;
}

QProgressBar {
    background: $progress_bg;
    border: 1px solid $progress_border;
    border-radius: 10px;
    text-align: center;
    min-height: 14px;
}

QProgressBar::chunk {
    background: $accent;
    border-radius: 8px;
}

QMenuBar::item:selected,
QMenu::item:selected {
    background: $menu_hover_bg;
}
"""
)


THEMES: dict[str, ThemeSpec] = {
    "warm_studio": ThemeSpec(
        key="warm_studio",
        label="Warm Studio",
        palette={
            "app_bg": "#f4efe6",
            "window_bg": "#efe7da",
            "text_main": "#1f2c35",
            "selection_text": "#ffffff",
            "accent": "#c7742b",
            "nav_start": "#173042",
            "nav_end": "#234861",
            "nav_border": "#102634",
            "nav_tag": "#f2c66b",
            "nav_title": "#ffffff",
            "nav_subtitle": "#cfe0e7",
            "nav_workflow_bg": "rgba(242, 198, 107, 0.14)",
            "nav_workflow_border": "rgba(242, 198, 107, 0.45)",
            "nav_workflow_title": "#f3d38a",
            "nav_workflow_text": "#eef5f7",
            "nav_item_text": "#eef5f7",
            "nav_item_hover": "rgba(255, 255, 255, 0.10)",
            "nav_item_selected_bg": "#f2c66b",
            "nav_item_selected_text": "#173042",
            "panel_bg": "#fbf8f3",
            "panel_border": "#d8cdbd",
            "panel_title": "#4b5c69",
            "hero_start": "#fffaf1",
            "hero_end": "#f0e3d0",
            "hero_border": "#dcccb5",
            "hero_title": "#18324a",
            "hero_text": "#52626e",
            "card_title": "#6a7a86",
            "card_value": "#173042",
            "hint_bg": "#f3ecdf",
            "hint_border": "#dfcfb6",
            "hint_text": "#51606a",
            "summary_start": "#fff9ee",
            "summary_end": "#f1e4cf",
            "summary_border": "#ddc8a9",
            "summary_text": "#2d4456",
            "button_bg": "#fffdf9",
            "button_border": "#ccbfa8",
            "button_text": "#18324a",
            "button_hover_bg": "#f8f0e3",
            "button_hover_border": "#c7742b",
            "button_pressed_bg": "#eed9be",
            "primary_button_bg": "#173042",
            "primary_button_hover": "#20455c",
            "primary_button_text": "#ffffff",
            "danger_button_bg": "#a64f45",
            "danger_button_hover": "#8f4339",
            "input_bg": "#fffdf9",
            "input_border": "#d2c4ae",
            "table_alt_bg": "#f7f1e6",
            "table_grid": "#eadfce",
            "header_bg": "#ede2d0",
            "header_border": "#ddcfbc",
            "header_text": "#51606a",
            "tab_bg": "#e9dfd2",
            "tab_border": "#d3c5b0",
            "tab_text": "#52626e",
            "tab_selected_bg": "#fffdf9",
            "tab_selected_text": "#18324a",
            "progress_bg": "#ece3d8",
            "progress_border": "#d0c2ad",
            "menu_hover_bg": "#eadcc7",
        },
    ),
    "crisp_lab": ThemeSpec(
        key="crisp_lab",
        label="Crisp Lab",
        palette={
            "app_bg": "#eef4f6",
            "window_bg": "#e7eef1",
            "text_main": "#1e2e32",
            "selection_text": "#ffffff",
            "accent": "#1f8f7a",
            "nav_start": "#203844",
            "nav_end": "#315260",
            "nav_border": "#162934",
            "nav_tag": "#8ee2bf",
            "nav_title": "#f9fcfd",
            "nav_subtitle": "#d2e4e9",
            "nav_workflow_bg": "rgba(142, 226, 191, 0.14)",
            "nav_workflow_border": "rgba(142, 226, 191, 0.42)",
            "nav_workflow_title": "#a6f0d0",
            "nav_workflow_text": "#edf7f8",
            "nav_item_text": "#edf7f8",
            "nav_item_hover": "rgba(255, 255, 255, 0.12)",
            "nav_item_selected_bg": "#8ee2bf",
            "nav_item_selected_text": "#203844",
            "panel_bg": "#f8fbfc",
            "panel_border": "#c9d9df",
            "panel_title": "#48606a",
            "hero_start": "#fbfefe",
            "hero_end": "#ddecf0",
            "hero_border": "#c4d9e0",
            "hero_title": "#163541",
            "hero_text": "#50666f",
            "card_title": "#688088",
            "card_value": "#153642",
            "hint_bg": "#edf6f5",
            "hint_border": "#c7dfdb",
            "hint_text": "#4c666a",
            "summary_start": "#f6fffb",
            "summary_end": "#d8efe6",
            "summary_border": "#bddfcd",
            "summary_text": "#1f4f50",
            "button_bg": "#fbfefe",
            "button_border": "#bfd2d9",
            "button_text": "#163541",
            "button_hover_bg": "#eef7f8",
            "button_hover_border": "#1f8f7a",
            "button_pressed_bg": "#d7eceb",
            "primary_button_bg": "#1b5864",
            "primary_button_hover": "#246c7a",
            "primary_button_text": "#ffffff",
            "danger_button_bg": "#b95b47",
            "danger_button_hover": "#a44f3d",
            "input_bg": "#ffffff",
            "input_border": "#c5d5db",
            "table_alt_bg": "#f2f8fa",
            "table_grid": "#d9e7eb",
            "header_bg": "#ddebef",
            "header_border": "#cadce3",
            "header_text": "#4c666d",
            "tab_bg": "#dce9ee",
            "tab_border": "#c0d2d8",
            "tab_text": "#4b656c",
            "tab_selected_bg": "#ffffff",
            "tab_selected_text": "#173844",
            "progress_bg": "#e4eef1",
            "progress_border": "#c5d5db",
            "menu_hover_bg": "#dfecee",
        },
    ),
    "ocean_blueprint": ThemeSpec(
        key="ocean_blueprint",
        label="Ocean Blueprint",
        palette={
            "app_bg": "#edf3f8",
            "window_bg": "#e5edf5",
            "text_main": "#22313f",
            "selection_text": "#ffffff",
            "accent": "#2b89c7",
            "nav_start": "#102b46",
            "nav_end": "#1d4a6f",
            "nav_border": "#0d2136",
            "nav_tag": "#90d7ff",
            "nav_title": "#fbfdff",
            "nav_subtitle": "#d5e8f6",
            "nav_workflow_bg": "rgba(144, 215, 255, 0.13)",
            "nav_workflow_border": "rgba(144, 215, 255, 0.40)",
            "nav_workflow_title": "#b3e5ff",
            "nav_workflow_text": "#eef7fd",
            "nav_item_text": "#eef7fd",
            "nav_item_hover": "rgba(255, 255, 255, 0.10)",
            "nav_item_selected_bg": "#90d7ff",
            "nav_item_selected_text": "#11314d",
            "panel_bg": "#f8fbfe",
            "panel_border": "#c8d7e5",
            "panel_title": "#4b6278",
            "hero_start": "#fafdff",
            "hero_end": "#dce8f5",
            "hero_border": "#c6d7e7",
            "hero_title": "#173a59",
            "hero_text": "#55697a",
            "card_title": "#6a7d8f",
            "card_value": "#173a59",
            "hint_bg": "#edf4fb",
            "hint_border": "#cddceb",
            "hint_text": "#53697d",
            "summary_start": "#f5fbff",
            "summary_end": "#dbeeff",
            "summary_border": "#bfd9ef",
            "summary_text": "#214562",
            "button_bg": "#fbfdff",
            "button_border": "#c2d2e0",
            "button_text": "#173a59",
            "button_hover_bg": "#eff6fb",
            "button_hover_border": "#2b89c7",
            "button_pressed_bg": "#dceaf6",
            "primary_button_bg": "#15486e",
            "primary_button_hover": "#1c5b88",
            "primary_button_text": "#ffffff",
            "danger_button_bg": "#b3594d",
            "danger_button_hover": "#9f4e44",
            "input_bg": "#ffffff",
            "input_border": "#c7d5e3",
            "table_alt_bg": "#f1f6fb",
            "table_grid": "#d9e4ef",
            "header_bg": "#dde8f3",
            "header_border": "#cad9e6",
            "header_text": "#53687b",
            "tab_bg": "#dce7f2",
            "tab_border": "#c1d1df",
            "tab_text": "#55697a",
            "tab_selected_bg": "#ffffff",
            "tab_selected_text": "#173a59",
            "progress_bg": "#e4edf6",
            "progress_border": "#c4d3e1",
            "menu_hover_bg": "#dfe8f3",
        },
    ),
}


def normalize_theme_key(theme_key: str | None) -> str:
    key = str(theme_key or DEFAULT_THEME_KEY).strip().lower()
    return key if key in THEMES else DEFAULT_THEME_KEY


def available_themes() -> list[ThemeSpec]:
    return list(THEMES.values())


def theme_label(theme_key: str | None) -> str:
    key = normalize_theme_key(theme_key)
    return THEMES[key].label


def stylesheet_for(theme_key: str | None) -> str:
    key = normalize_theme_key(theme_key)
    return THEME_TEMPLATE.substitute(THEMES[key].palette)


def load_saved_theme_key() -> str:
    path = _theme_store_path()
    if not path.exists():
        return DEFAULT_THEME_KEY
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return DEFAULT_THEME_KEY
    return normalize_theme_key(payload.get("theme"))


def save_theme_key(theme_key: str | None) -> str:
    key = normalize_theme_key(theme_key)
    path = _theme_store_path()
    path.write_text(json.dumps({"theme": key}, indent=2), encoding="utf-8")
    return key


def current_theme_key(app: QApplication | None) -> str:
    if app is None:
        return load_saved_theme_key()
    return normalize_theme_key(app.property("gotacc_theme_key"))


def apply_theme(app: QApplication, theme_key: str | None = None) -> str:
    key = normalize_theme_key(theme_key or load_saved_theme_key())
    app.setStyle("Fusion")
    app.setStyleSheet(stylesheet_for(key))
    app.setProperty("gotacc_theme_key", key)
    return key
