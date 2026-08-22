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


DEFAULT_THEME_KEY = "control_room_dark"
DARK_THEME_KEY = "control_room_dark"
LIGHT_THEME_KEY = "control_room_light"


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
    font-family: "IBM Plex Sans", "Source Han Sans SC", "Segoe UI", sans-serif;
    font-size: 13px;
}

QMainWindow, QDialog, QMenuBar, QMenu, QStatusBar, QDockWidget {
    background: $window_bg;
}

QLabel {
    background: transparent;
    border: none;
}

QFrame#frame_leftNav {
    background: transparent;
    border: none;
    border-radius: 0px;
}

QScrollArea#scrollArea_leftTools,
QScrollArea#scrollArea_leftTools QWidget#qt_scrollarea_viewport,
QWidget#scrollAreaWidgetContents_leftTools {
    background: transparent;
    border: none;
}

QFrame#summaryPanel {
    background: $summary_panel_bg;
    border: 1px solid $summary_panel_border;
    border-radius: 14px;
}

QFrame#summaryPanel QWidget {
    background: transparent;
}

QWidget#statusStrip,
QFrame#statusStrip {
    background: transparent;
    border: none;
    border-radius: 0px;
}

QFrame#statusItem {
    background: transparent;
    border: none;
    border-left: 4px solid $status_item_idle_bar;
    border-radius: 0px;
}

QFrame#statusItem[tone="info"] {
    border-left-color: $status_tone_info_bar;
}

QFrame#statusItem[tone="success"] {
    border-left-color: $status_tone_success_bar;
}

QFrame#statusItem[tone="warning"] {
    border-left-color: $status_tone_warning_bar;
}

QFrame#statusItem[tone="danger"] {
    border-left-color: $status_tone_danger_bar;
}

QFrame#statusSeparator {
    background: $status_separator;
    min-width: 1px;
    max-width: 1px;
    border: none;
}

QLabel[role="title"] {
    color: $status_title_fg;
    background: transparent;
    border: none;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.8px;
}

QLabel[role="value"][tone="subtle"] {
    color: $status_tone_subtle_fg;
    background: transparent;
    border: none;
    font-size: 14px;
    font-weight: 700;
}

QLabel[role="value"][tone="info"] {
    color: $status_tone_info_fg;
    background: transparent;
    border: none;
    font-size: 14px;
    font-weight: 700;
}

QLabel[role="value"][tone="success"] {
    color: $status_tone_success_fg;
    background: transparent;
    border: none;
    font-size: 14px;
    font-weight: 700;
}

QLabel[role="value"][tone="warning"] {
    color: $status_tone_warning_fg;
    background: transparent;
    border: none;
    font-size: 14px;
    font-weight: 700;
}

QLabel[role="value"][tone="danger"] {
    color: $status_tone_danger_fg;
    background: transparent;
    border: none;
    font-size: 14px;
    font-weight: 700;
}

QLabel#summaryTitle {
    background: transparent;
    color: $hero_title;
    font-size: 23px;
    font-weight: 700;
}

QLabel#summarySubtitle {
    background: transparent;
    color: $hero_text;
    font-size: 12px;
}

QLabel[role="field"] {
    color: $card_title;
    background: transparent;
    border: none;
    font-size: 11px;
    font-weight: 600;
}

QLabel[role="statusPill"] {
    background: $button_bg;
    border: 1px solid $button_border;
    border-radius: 10px;
    color: $button_text;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 700;
}

QToolButton#themeToggleButton,
QToolButton#logToggleButton {
    background: $button_bg;
    border: 1px solid $button_border;
    border-radius: 8px;
    color: $button_text;
    padding: 0;
    font-size: 12px;
    font-weight: 700;
}

QToolButton#themeToggleButton {
    min-width: 26px;
    max-width: 26px;
    min-height: 26px;
    max-height: 26px;
}

QToolButton#logToggleButton {
    min-width: 38px;
    max-width: 38px;
    min-height: 26px;
    max-height: 26px;
}

QToolButton#themeToggleButton:hover,
QToolButton#logToggleButton:hover {
    background: $button_hover_bg;
    border-color: $button_hover_border;
}

QToolButton#themeToggleButton:pressed,
QToolButton#logToggleButton:pressed {
    background: $button_pressed_bg;
}

QToolButton#logToggleButton[active="true"] {
    background: $button_hover_bg;
    border-color: $accent;
    color: $accent;
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
    font-size: 20px;
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
    padding: 0;
}

QListWidget#listWidget_navPages::item {
    background: $button_bg;
    border: 1px solid $button_border;
    border-radius: 11px;
    color: $nav_item_text;
    padding: 8px 12px;
    margin: 3px 0;
    font-size: 12px;
    font-weight: 700;
    min-height: 28px;
}

QListWidget#listWidget_navPages::item:hover {
    background: $button_hover_bg;
    border-color: $button_border;
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
    border-radius: 14px;
    margin-top: 0px;
    padding-top: 30px;
    font-size: 14px;
    font-weight: 700;
}

QGroupBox::title {
    subcontrol-origin: padding;
    subcontrol-position: top left;
    left: 16px;
    top: 7px;
    padding: 0px;
    background: transparent;
    color: $panel_title;
    border: none;
    font-size: 15px;
    font-weight: 800;
}

QGroupBox#groupBox_primaryNav {
    background: $panel_bg;
    border: 1px solid $panel_border;
    border-radius: 12px;
}

QGroupBox#groupBox_primaryNav::title {
    color: $nav_subtitle;
    font-weight: 700;
}

QFrame#frame_dashboardHero,
QFrame#frame_builderHero,
QFrame#frame_machineHero,
QFrame#frame_runHero {
    background: $panel_bg;
    border: 1px solid $panel_border;
    border-radius: 14px;
}

QFrame#frame_cardCurrentTask,
QFrame#frame_cardMode,
QFrame#frame_cardAlgorithm,
QFrame#frame_cardStatus,
QFrame#frame_eval,
QFrame#frame_elapsed,
QFrame#frame_best,
QFrame#frame_feasibility,
QFrame#frame_phase {
    background: $status_strip_bg;
    border: 1px solid $status_strip_border;
    border-radius: 12px;
}

QFrame#frame_variablesToolbar {
    background: $panel_bg;
    border: 1px solid $panel_border;
    border-radius: 12px;
}

QFrame#frame_pvPresetLibrary {
    background: transparent;
    border: none;
    border-radius: 0px;
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
    font-size: 17px;
    font-weight: 700;
}

QFrame#statusItem QLabel[role="title"] {
    color: $status_title_fg;
    background: transparent;
    border: none;
    font-size: 9px;
    font-weight: 700;
}

QFrame#statusItem QLabel[role="value"] {
    color: $status_tone_subtle_fg;
    background: transparent;
    border: none;
    font-size: 14px;
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
QLabel#label_actionsHint {
    background: $hint_bg;
    border: 1px solid $hint_border;
    border-radius: 12px;
    color: $hint_text;
    padding: 10px;
}

QPushButton {
    background: $button_bg;
    border: 1px solid $button_border;
    border-radius: 12px;
    color: $button_text;
    padding: 6px 12px;
    min-height: 32px;
    font-size: 12px;
    font-weight: 700;
}

QPushButton:hover {
    background: $button_hover_bg;
    border-color: $button_hover_border;
}

QPushButton:pressed {
    background: $button_pressed_bg;
}

QPushButton[compact="true"] {
    padding: 3px 10px;
    min-height: 22px;
    max-height: 26px;
    font-size: 11px;
}

QPushButton[inlineAction="true"] {
    padding: 1px 9px;
    min-height: 20px;
    max-height: 24px;
    font-size: 11px;
}

QPushButton[primary="true"] {
    background: $accent;
    border-color: $accent;
    color: $window_bg;
}

QPushButton[primary="true"]:hover {
    background: $accent;
    border-color: $accent;
}

QPushButton[danger="true"] {
    color: $danger_button_bg;
    border-color: $danger_button_bg;
}

QPushButton#pushButton_startRun,
QPushButton#pushButton_start {
    background: $accent;
    border-color: $accent;
    color: $window_bg;
}

QPushButton#pushButton_startRun:hover,
QPushButton#pushButton_start:hover {
    background: $primary_button_hover;
    border-color: $primary_button_hover;
}

QPushButton#pushButton_stopRun,
QPushButton#pushButton_stop {
    background: $button_bg;
    border-color: $danger_button_bg;
    color: $danger_button_bg;
}

QPushButton#pushButton_stopRun:hover,
QPushButton#pushButton_stop:hover {
    background: $danger_button_hover;
    border-color: $danger_button_hover;
}

QPushButton#pushButton_startRun[compact="true"],
QPushButton#pushButton_stopRun[compact="true"] {
    padding: 3px 10px;
    min-height: 22px;
    max-height: 26px;
    font-size: 11px;
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
    color: $button_text;
    padding: 5px 10px;
    min-height: 16px;
}

QComboBox::drop-down,
QDoubleSpinBox::drop-down {
    border: none;
    width: 20px;
}

QPlainTextEdit {
    color: $button_text;
    padding: 12px;
    font-family: "JetBrains Mono", "Cascadia Mono", "Consolas", monospace;
    font-size: 12px;
}

QTableWidget,
QTreeWidget,
QListWidget {
    alternate-background-color: $table_alt_bg;
    gridline-color: $table_grid;
    color: $button_text;
    selection-background-color: $selection_row_bg;
    selection-color: $selection_row_text;
}

QTableWidget::item:selected,
QTreeWidget::item:selected,
QListWidget::item:selected {
    background: $selection_row_bg;
    color: $selection_row_text;
}

QTableWidget::item:selected:active,
QTreeWidget::item:selected:active,
QListWidget::item:selected:active {
    background: $selection_row_active_bg;
    color: $selection_row_text;
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

QCheckBox {
    background: transparent;
    border: none;
    spacing: 8px;
    font-size: 12px;
    font-weight: 600;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid $panel_border;
    border-radius: 4px;
    background: $input_bg;
}

QCheckBox::indicator:checked {
    background: $accent;
    border: 1px solid $accent;
}

QTabWidget::pane {
    border-left: 1px solid $panel_border;
    border-right: 1px solid $panel_border;
    border-bottom: 1px solid $panel_border;
    border-radius: 14px;
    background: $panel_bg;
    top: -1px;
}

QTabBar::tab {
    background: $button_bg;
    border: 1px solid $button_border;
    border-bottom: none;
    border-top-left-radius: 10px;
    border-top-right-radius: 10px;
    color: $button_text;
    padding: 8px 14px;
    margin-right: 6px;
    font-size: 12px;
    font-weight: 700;
    min-width: 88px;
}

QTabWidget#tabWidget_configure QTabBar::tab {
    min-width: 118px;
}

QTabWidget#tabWidget_bottomOutput QTabBar::tab,
QTabWidget#tabWidget_resultsViews QTabBar::tab,
QTabWidget#tabWidget_machine QTabBar::tab,
QTabWidget#tabWidget_machineAdvanced QTabBar::tab {
    min-width: 132px;
}

QTabWidget#tabWidget_tables QTabBar::tab {
    min-width: 154px;
}

QTabBar::tab:selected {
    background: $panel_bg;
    color: $hero_title;
    border-bottom-color: $panel_bg;
}

QTabBar::tab:hover:!selected {
    background: $button_hover_bg;
}

QProgressBar {
    background: $progress_bg;
    border: 1px solid $progress_border;
    border-radius: 10px;
    color: $hero_title;
    text-align: center;
    min-height: 20px;
}

QProgressBar::chunk {
    background: $accent;
    border-radius: 8px;
}

QStatusBar,
QDockWidget,
QWidget#dockWidgetContents_runtimeStatus,
QTabWidget#tabWidget_bottomOutput::pane {
    background: $hint_bg;
    color: $hint_text;
    border: 1px solid $hint_border;
}

QMenuBar::item:selected,
QMenu::item:selected {
    background: $menu_hover_bg;
}
"""
)


THEMES: dict[str, ThemeSpec] = {
    "control_room_dark": ThemeSpec(
        key="control_room_dark",
        label="Control Room Dark",
        palette={
            "app_bg": "#0f1519",
            "window_bg": "#0f1519",
            "text_main": "#e6edf2",
            "selection_text": "#0f1519",
            "accent": "#45d0bc",
            "nav_start": "#0f1519",
            "nav_end": "#172027",
            "nav_border": "#22303a",
            "nav_tag": "#7dd7c5",
            "nav_title": "#f3efe3",
            "nav_subtitle": "#99a9b5",
            "nav_workflow_bg": "rgba(69, 208, 188, 0.12)",
            "nav_workflow_border": "rgba(69, 208, 188, 0.35)",
            "nav_workflow_title": "#7dd7c5",
            "nav_workflow_text": "#d7e2ea",
            "nav_item_text": "#d7e2ea",
            "nav_item_hover": "rgba(255, 255, 255, 0.08)",
            "nav_item_selected_bg": "#45d0bc",
            "nav_item_selected_text": "#0f1519",
            "panel_bg": "#172027",
            "panel_border": "#24333d",
            "panel_title": "#7dd7c5",
            "hero_start": "#1b262d",
            "hero_end": "#152028",
            "hero_border": "#2b3a45",
            "hero_title": "#f3efe3",
            "hero_text": "#99a9b5",
            "card_title": "#8ea0ad",
            "card_value": "#f3efe3",
            "hint_bg": "#131c22",
            "hint_border": "#2a3943",
            "hint_text": "#c9d5dc",
            "summary_panel_bg": "#1b262d",
            "summary_panel_border": "#2b3a45",
            "status_strip_bg": "#131c22",
            "status_strip_border": "#2a3943",
            "status_separator": "#31424d",
            "status_item_idle_bar": "#4f6270",
            "status_title_fg": "#8ea0ad",
            "summary_start": "#1b262d",
            "summary_end": "#152028",
            "summary_border": "#2b3a45",
            "summary_text": "#f3efe3",
            "button_bg": "#22313a",
            "button_border": "#48606e",
            "button_text": "#edf3f7",
            "button_hover_bg": "#2b3f4b",
            "button_hover_border": "#48606e",
            "button_pressed_bg": "#19262e",
            "primary_button_bg": "#45d0bc",
            "primary_button_hover": "#45d0bc",
            "primary_button_text": "#ffffff",
            "danger_button_bg": "#e4b86f",
            "danger_button_hover": "#e4b86f",
            "input_bg": "#10171c",
            "input_border": "#24343f",
            "table_alt_bg": "#131c22",
            "table_grid": "#2a3943",
            "selection_row_bg": "#24564f",
            "selection_row_active_bg": "#2f6f65",
            "selection_row_text": "#ffffff",
            "header_bg": "#131c22",
            "header_border": "#2a3943",
            "header_text": "#8ea0ad",
            "tab_bg": "#22313a",
            "tab_border": "#48606e",
            "tab_text": "#edf3f7",
            "tab_selected_bg": "#172027",
            "tab_selected_text": "#f3efe3",
            "progress_bg": "#10171c",
            "progress_border": "#24343f",
            "menu_hover_bg": "#2b3f4b",
        },
    ),
    "control_room_light": ThemeSpec(
        key="control_room_light",
        label="Control Room Light",
        palette={
            "app_bg": "#f2ede5",
            "window_bg": "#f2ede5",
            "text_main": "#2c3942",
            "selection_text": "#ffffff",
            "accent": "#2d7f6d",
            "nav_start": "#f2ede5",
            "nav_end": "#faf7f1",
            "nav_border": "#d7cec1",
            "nav_tag": "#2d7f6d",
            "nav_title": "#2d3940",
            "nav_subtitle": "#746c62",
            "nav_workflow_bg": "rgba(45, 127, 109, 0.10)",
            "nav_workflow_border": "rgba(45, 127, 109, 0.30)",
            "nav_workflow_title": "#2d7f6d",
            "nav_workflow_text": "#314049",
            "nav_item_text": "#314049",
            "nav_item_hover": "rgba(45, 127, 109, 0.08)",
            "nav_item_selected_bg": "#dcede3",
            "nav_item_selected_text": "#28483e",
            "panel_bg": "#fffdf9",
            "panel_border": "#d7cec1",
            "panel_title": "#2d7f6d",
            "hero_start": "#fcf9f3",
            "hero_end": "#f1eadf",
            "hero_border": "#ddd4c8",
            "hero_title": "#2d3940",
            "hero_text": "#746c62",
            "card_title": "#7c7368",
            "card_value": "#2d3940",
            "hint_bg": "#f7f1e8",
            "hint_border": "#ddd2c4",
            "hint_text": "#625b52",
            "summary_panel_bg": "#fcf9f3",
            "summary_panel_border": "#ddd4c8",
            "status_strip_bg": "#f7f1e8",
            "status_strip_border": "#ddd2c4",
            "status_separator": "#ddd4c7",
            "status_item_idle_bar": "#c8bfb3",
            "status_title_fg": "#7c7368",
            "summary_start": "#fcf9f3",
            "summary_end": "#f1eadf",
            "summary_border": "#ddd4c8",
            "summary_text": "#2d3940",
            "button_bg": "#f8f3eb",
            "button_border": "#d9d0c3",
            "button_text": "#2c3942",
            "button_hover_bg": "#efe6d9",
            "button_hover_border": "#d9d0c3",
            "button_pressed_bg": "#e3d8c8",
            "primary_button_bg": "#2d7f6d",
            "primary_button_hover": "#2d7f6d",
            "primary_button_text": "#ffffff",
            "danger_button_bg": "#a97118",
            "danger_button_hover": "#a97118",
            "input_bg": "#fffdf9",
            "input_border": "#d9d0c3",
            "table_alt_bg": "#f7f1e8",
            "table_grid": "#ddd4c7",
            "selection_row_bg": "#b9dff7",
            "selection_row_active_bg": "#8ec8f0",
            "selection_row_text": "#12314a",
            "header_bg": "#f7f1e8",
            "header_border": "#ddd2c4",
            "header_text": "#7c7368",
            "tab_bg": "#f8f3eb",
            "tab_border": "#d9d0c3",
            "tab_text": "#2c3942",
            "tab_selected_bg": "#fffdf9",
            "tab_selected_text": "#2d3940",
            "progress_bg": "#fffdf9",
            "progress_border": "#d9d0c3",
            "menu_hover_bg": "#efe6d9",
        },
    ),
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
    palette = dict(THEMES[key].palette)
    dark = palette.get("window_bg") == "#0f1519"
    palette.setdefault("summary_panel_bg", palette.get("summary_start", palette["panel_bg"]))
    palette.setdefault("summary_panel_border", palette.get("summary_border", palette["panel_border"]))
    palette.setdefault("status_strip_bg", palette.get("hint_bg", palette["panel_bg"]))
    palette.setdefault("status_strip_border", palette.get("hint_border", palette["panel_border"]))
    palette.setdefault("status_separator", palette.get("table_grid", palette["panel_border"]))
    palette.setdefault("status_item_idle_bar", palette.get("card_title", palette["panel_border"]))
    palette.setdefault("status_title_fg", palette.get("card_title", palette["panel_title"]))
    palette.setdefault("status_tone_info_bar", "#60a5fa")
    palette.setdefault("status_tone_success_bar", palette["accent"])
    palette.setdefault("status_tone_warning_bar", palette["danger_button_bg"])
    palette.setdefault("status_tone_danger_bar", "#ef8a7e" if dark else "#f87171")
    palette.setdefault("status_tone_info_fg", "#8bc5ff" if dark else "#1d4ed8")
    palette.setdefault("status_tone_success_fg", palette["accent"] if dark else "#166534")
    palette.setdefault("status_tone_warning_fg", palette["danger_button_bg"])
    palette.setdefault("status_tone_danger_fg", "#ef8a7e" if dark else "#b91c1c")
    palette.setdefault("status_tone_subtle_fg", palette.get("card_value", palette["text_main"]))
    palette.setdefault("selection_row_bg", "#24564f" if dark else "#b9dff7")
    palette.setdefault("selection_row_active_bg", "#2f6f65" if dark else "#8ec8f0")
    palette.setdefault("selection_row_text", "#ffffff" if dark else "#12314a")
    return THEME_TEMPLATE.substitute(palette)


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


def theme_palette(theme_key: str | None = None) -> dict[str, str]:
    key = normalize_theme_key(theme_key or load_saved_theme_key())
    return dict(THEMES[key].palette)
