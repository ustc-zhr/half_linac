from __future__ import annotations


DARK_THEME = {
    "window_bg": "#0f1519",
    "panel_bg": "#172027",
    "section_bg": "#172027",
    "section_border": "#24333d",
    "summary_bg": "#1b262d",
    "summary_border": "#2b3a45",
    "input_bg": "#10171c",
    "plot_bg": "#11191f",
    "text_primary": "#e6edf2",
    "text_muted": "#99a9b5",
    "focus": "#45d0bc",
    "warning": "#e4b86f",
    "danger": "#ff6b6b",
    "button_bg": "#22313a",
    "button_border": "#48606e",
    "button_hover": "#2b3f4b",
    "button_pressed": "#19262e",
    "toggle_bg": "#11191f",
    "toggle_border": "#2b3d48",
    "toggle_hover": "#18242c",
    "toggle_pressed": "#0c1217",
    "status_strip_bg": "#131c22",
    "status_strip_border": "#2a3943",
    "status_item_bar": "#4f6270",
    "status_success": "#45d0bc",
    "status_warning": "#e4b86f",
    "status_danger": "#ef8a7e",
    "status_subtle": "#c8d2da",
}

LIGHT_THEME = {
    "window_bg": "#f2ede5",
    "panel_bg": "#fffaf3",
    "section_bg": "#fffdf9",
    "section_border": "#ddd4c7",
    "summary_bg": "#fcf9f3",
    "summary_border": "#ddd4c8",
    "input_bg": "#fffdf8",
    "plot_bg": "#f7f1e8",
    "text_primary": "#102033",
    "text_muted": "#6f6253",
    "focus": "#2d7f6d",
    "warning": "#a97118",
    "danger": "#8f3d28",
    "button_bg": "#eee5d8",
    "button_border": "#cdbfac",
    "button_hover": "#e4d8c8",
    "button_pressed": "#d8c9b6",
    "toggle_bg": "#f8f3eb",
    "toggle_border": "#d9d0c3",
    "toggle_hover": "#efe6d9",
    "toggle_pressed": "#e3d8c8",
    "status_strip_bg": "#f7f1e8",
    "status_strip_border": "#ddd2c4",
    "status_item_bar": "#c8bfb3",
    "status_success": "#2d7f6d",
    "status_warning": "#a97118",
    "status_danger": "#b91c1c",
    "status_subtle": "#475569",
}


def theme_tokens(name: str) -> dict[str, str]:
    return LIGHT_THEME if name == "control_room" else DARK_THEME


def build_stylesheet(name: str) -> str:
    t = theme_tokens(name)
    return f"""
    QMainWindow, QWidget#centralRoot {{
        background: {t['window_bg']};
        color: {t['text_primary']};
        font-family: "IBM Plex Sans", "Source Han Sans SC", "Segoe UI", sans-serif;
        font-size: 12px;
    }}
    QDialog#bpmSelectionDialog, QDialog#knobSelectionDialog {{
        background: {t['panel_bg']};
        color: {t['text_primary']};
    }}
    QDialog#bpmSelectionDialog QLabel#bpmSelectionPrompt,
    QDialog#knobSelectionDialog QLabel#knobSelectionPrompt {{
        color: {t['text_muted']};
        font-weight: 600;
    }}
    QListWidget#bpmSelectionList {{
        background: {t['plot_bg']};
        border: 1px solid {t['section_border']};
        border-radius: 8px;
        color: {t['text_primary']};
        outline: 0px;
        padding: 4px;
    }}
    QListWidget#bpmSelectionList::item {{
        color: {t['text_primary']};
        min-height: 26px;
        padding: 2px 5px;
        border-radius: 5px;
    }}
    QListWidget#bpmSelectionList::item:hover {{
        background: {t['button_hover']};
    }}
    QListWidget#bpmSelectionList::item:selected {{
        background: {t['focus']};
        color: {t['window_bg']};
    }}
    QWidget {{
        color: {t['text_primary']};
        font-family: "IBM Plex Sans", "Source Han Sans SC", "Segoe UI", sans-serif;
        font-size: 12px;
    }}
    QLabel {{
        background: transparent;
        border: none;
    }}
    QFrame#summaryPanel {{
        background: {t['summary_bg']};
        border: 1px solid {t['summary_border']};
        border-radius: 14px;
    }}
    QFrame#controlCard {{
        background: {t['panel_bg']};
        border: 1px solid {t['section_border']};
        border-radius: 14px;
    }}
    QFrame#plotCard {{
        background: {t['panel_bg']};
        border: 1px solid {t['section_border']};
        border-radius: 14px;
    }}
    QScrollArea#workflowScroll,
    QScrollArea#workflowScroll > QWidget#qt_scrollarea_viewport,
    QWidget#workflowContent {{
        background: {t['panel_bg']};
        border: none;
    }}
    QFrame#statusStrip {{
        background: transparent;
        border: none;
        border-radius: 0px;
    }}
    QLabel#titleLabel {{
        font-size: 22px;
        font-weight: 700;
        letter-spacing: 0.3px;
    }}
    QLabel[muted="true"] {{
        color: {t['text_muted']};
    }}
    QLabel#configTitle {{
        font-size: 15px;
        font-weight: 800;
    }}
    QLabel#workspaceIntro {{
        color: {t['text_muted']};
        font-size: 12px;
        font-weight: 600;
        padding: 2px 2px 5px 2px;
    }}
    QLabel#workflowState {{
        color: {t['text_primary']};
        font-size: 15px;
        font-weight: 800;
        padding: 3px 2px 0px 2px;
    }}
    QLabel#workflowHint {{
        color: {t['text_muted']};
        font-size: 11px;
        font-weight: 600;
        padding: 0px 2px 5px 2px;
    }}
    QLabel#modelSafetyNotice {{
        color: {t['status_success']};
        font-size: 11px;
        font-weight: 700;
        padding: 3px 2px;
    }}
    QLabel#energyStepSummary {{
        background: {t['status_strip_bg']};
        border: 1px solid {t['status_strip_border']};
        border-radius: 9px;
        color: {t['text_muted']};
        font-size: 11px;
        font-weight: 600;
        padding: 7px 9px;
    }}
    QLabel#operationBanner {{
        background: {t['status_strip_bg']};
        border: 1px solid {t['status_strip_border']};
        border-left: 4px solid {t['status_warning']};
        border-radius: 9px;
        color: {t['text_primary']};
        font-size: 11px;
        font-weight: 600;
        padding: 8px 9px;
    }}
    QLabel#operationBanner[tone="success"] {{
        border-left-color: {t['status_success']};
    }}
    QLabel#operationBanner[tone="danger"] {{
        border-left-color: {t['status_danger']};
    }}
    QLabel#operationBanner[tone="subtle"] {{
        border-left-color: {t['status_item_bar']};
    }}
    QLabel[role="configSection"] {{
        color: {t['text_primary']};
        border: none;
        border-bottom: 1px solid {t['section_border']};
        font-size: 9px;
        font-weight: 700;
        padding: 3px 0px;
    }}
    QPushButton#configLoadButton {{
        border-radius: 9px;
        min-height: 26px;
        padding: 2px 10px;
    }}
    QPushButton#preflightButton, QPushButton#bpmSelectButton,
    QPushButton#knobSelectButton {{
        border-radius: 10px;
        min-height: 28px;
        max-height: 28px;
        padding: 2px 10px;
    }}
    QToolButton#advancedSettingsButton {{
        background: transparent;
        border: none;
        border-bottom: 1px solid {t['section_border']};
        border-radius: 0px;
        color: {t['text_muted']};
        min-height: 26px;
        padding: 2px 0px;
        text-align: left;
    }}
    QToolButton#advancedSettingsButton:hover {{
        color: {t['text_primary']};
        background: transparent;
    }}
    QToolButton#detailSectionButton {{
        background: transparent;
        border: none;
        border-bottom: 1px solid {t['section_border']};
        border-radius: 0px;
        color: {t['text_muted']};
        min-height: 28px;
        padding: 3px 2px;
        text-align: left;
    }}
    QToolButton#detailSectionButton:checked {{
        color: {t['text_primary']};
    }}
    QToolButton#detailSectionButton:hover {{
        color: {t['text_primary']};
        background: {t['button_hover']};
    }}
    QFrame#statusItem {{
        background: transparent;
        border: none;
        border-left: 4px solid {t['status_item_bar']};
        border-radius: 0px;
    }}
    QFrame#statusItem[tone="success"] {{
        border-left-color: {t['status_success']};
    }}
    QFrame#statusItem[tone="warning"] {{
        border-left-color: {t['status_warning']};
    }}
    QFrame#statusItem[tone="danger"] {{
        border-left-color: {t['status_danger']};
    }}
    QFrame#statusSeparator {{
        background: {t['section_border']};
        min-width: 1px;
        max-width: 1px;
        border: none;
    }}
    QLabel#statusTitle {{
        color: {t['text_muted']};
        font-size: 9px;
        font-weight: 700;
    }}
    QLabel#statusValue {{
        font-size: 13px;
        font-weight: 700;
    }}
    QLabel#statusValue[tone="subtle"] {{
        color: {t['status_subtle']};
    }}
    QLabel#statusValue[tone="success"] {{
        color: {t['status_success']};
    }}
    QLabel#statusValue[tone="warning"] {{
        color: {t['status_warning']};
    }}
    QLabel#statusValue[tone="danger"] {{
        color: {t['status_danger']};
    }}
    QPushButton, QToolButton {{
        background: {t['button_bg']};
        border: 1px solid {t['button_border']};
        border-radius: 12px;
        color: {t['text_primary']};
        font-weight: 700;
        min-height: 30px;
        padding: 4px 10px;
    }}
    QPushButton:hover, QToolButton:hover {{
        background: {t['button_hover']};
    }}
    QPushButton:pressed, QToolButton:pressed {{
        background: {t['button_pressed']};
    }}
    QPushButton:disabled, QToolButton:disabled {{
        color: {t['text_muted']};
        border-color: {t['section_border']};
    }}
    QPushButton[role="control"]:enabled {{
        border-color: {t['focus']};
    }}
    QPushButton#nextWorkflowAction {{
        min-height: 38px;
        font-size: 14px;
    }}
    QPushButton[role="danger"]:enabled {{
        border-color: {t['danger']};
        color: {t['danger']};
    }}
    QLabel#operationStage, QLabel#operationProgressPercent {{
        color: {t['text_muted']};
        font-size: 10px;
        font-weight: 600;
    }}
    QProgressBar#operationProgress {{
        background: {t['section_border']};
        border: none;
        border-radius: 2px;
    }}
    QProgressBar#operationProgress::chunk {{
        background: {t['focus']};
        border-radius: 2px;
    }}
    QToolButton#themeToggleButton {{
        background: {t['toggle_bg']};
        border: 1px solid {t['toggle_border']};
        border-radius: 11px;
        color: {t['text_primary']};
        padding: 0px;
        min-width: 32px;
        max-width: 32px;
        min-height: 32px;
        max-height: 32px;
        font-size: 14px;
        font-weight: 700;
    }}
    QToolButton#headerLogButton {{
        background: {t['toggle_bg']};
        border: 1px solid {t['toggle_border']};
        border-radius: 11px;
        color: {t['text_primary']};
        padding: 0px;
        min-width: 48px;
        max-width: 48px;
        min-height: 32px;
        max-height: 32px;
        font-size: 12px;
        font-weight: 700;
    }}
    QToolButton#headerLogButton:checked {{
        border-color: {t['focus']};
    }}
    QToolButton#headerLogButton:hover {{
        background: {t['toggle_hover']};
    }}
    QToolButton#headerLogButton:pressed {{
        background: {t['toggle_pressed']};
    }}
    QToolButton#themeToggleButton:hover {{
        background: {t['toggle_hover']};
    }}
    QToolButton#themeToggleButton:pressed {{
        background: {t['toggle_pressed']};
    }}
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background: {t['input_bg']};
        border: 1px solid {t['section_border']};
        border-radius: 10px;
        min-height: 28px;
        padding: 2px 7px;
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
        border-color: {t['focus']};
    }}
    QComboBox QAbstractItemView {{
        background: {t['input_bg']};
        border: 1px solid {t['section_border']};
        color: {t['text_primary']};
        outline: 0px;
        selection-background-color: {t['focus']};
        selection-color: {t['window_bg']};
    }}
    QComboBox QAbstractItemView::item {{
        min-height: 26px;
        padding: 3px 7px;
    }}
    QFileDialog {{
        background: {t['panel_bg']};
        color: {t['text_primary']};
    }}
    QFileDialog QListView, QFileDialog QTreeView {{
        background: {t['plot_bg']};
        border: 1px solid {t['section_border']};
        color: {t['text_primary']};
        outline: 0px;
        selection-background-color: {t['focus']};
        selection-color: {t['window_bg']};
    }}
    QFileDialog QListView::item, QFileDialog QTreeView::item {{
        min-height: 24px;
        padding: 2px;
    }}
    QFileDialog QListView::item:hover, QFileDialog QTreeView::item:hover {{
        background: {t['button_hover']};
    }}
    QFileDialog QLabel {{
        color: {t['text_muted']};
    }}
    QLabel[role="field"] {{
        color: {t['text_muted']};
        font-size: 11px;
        font-weight: 600;
    }}
    QTabWidget::pane {{
        background: {t['panel_bg']};
        border-left: 1px solid {t['section_border']};
        border-right: 1px solid {t['section_border']};
        border-bottom: 1px solid {t['section_border']};
        border-radius: 14px;
        top: -1px;
    }}
    QTabBar::base {{
        border: none;
        background: transparent;
        height: 0px;
    }}
    QTabBar::tab {{
        background: {t['button_bg']};
        border: 1px solid {t['button_border']};
        border-top-left-radius: 10px;
        border-top-right-radius: 10px;
        color: {t['text_primary']};
        font-weight: 700;
        min-width: 82px;
        padding: 8px 9px;
        margin-right: 4px;
    }}
    QTabBar::tab:selected {{
        background: {t['panel_bg']};
        border-bottom-color: {t['panel_bg']};
        color: {t['text_primary']};
    }}
    QTabBar::tab:hover:!selected {{
        background: {t['button_hover']};
    }}
    QPlainTextEdit, QTableWidget {{
        background: {t['plot_bg']};
        border: 1px solid {t['section_border']};
        border-radius: 10px;
        color: {t['text_primary']};
        selection-background-color: {t['focus']};
        selection-color: {t['window_bg']};
    }}
    QTableWidget#knobTable {{
        background: transparent;
        border: none;
        border-radius: 0px;
        color: {t['text_primary']};
        selection-background-color: transparent;
    }}
    QTableWidget#knobTable::item {{
        border: none;
        border-bottom: 1px solid {t['section_border']};
        padding: 3px 4px;
    }}
    QTableWidget#knobTable QHeaderView {{
        background: transparent;
    }}
    QTableWidget#knobTable QHeaderView::section {{
        background: transparent;
        border: none;
        border-bottom: 1px solid {t['section_border']};
        color: {t['text_muted']};
        font-size: 9px;
        font-weight: 700;
        padding: 4px;
    }}
    QHeaderView {{
        background: {t['plot_bg']};
        color: {t['text_primary']};
        border: none;
    }}
    QHeaderView::section {{
        background: {t['button_bg']};
        border: 1px solid {t['section_border']};
        color: {t['text_primary']};
        font-weight: 700;
        padding: 4px;
    }}
    QTableCornerButton::section {{
        background: {t['button_bg']};
        border: 1px solid {t['section_border']};
    }}
    QPlainTextEdit#logView {{
        font-family: "JetBrains Mono", "Cascadia Mono", "Consolas", monospace;
        font-size: 12px;
    }}
    """
