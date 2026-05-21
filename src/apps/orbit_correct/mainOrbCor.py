import sys
import re
from pathlib import Path

_REPO_BOOTSTRAP_ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / "repo_bootstrap.py").is_file()
)
if str(_REPO_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_BOOTSTRAP_ROOT))

from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

from PyQt5.QtWidgets import (
    QMainWindow,
    QApplication,
    QCheckBox,
    QDoubleSpinBox,
    QMessageBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtCore import QRegExp, QTimer
from OrbCorgui import Ui_MainWindow


import half_linac.runtime_config as st
from half_linac.src.shared.process_runtime import ManagedProcessGroup

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
    "status_strip_bg": "#131c22",
    "status_strip_border": "#2a3943",
    "status_separator": "#31424d",
    "status_item_idle_bar": "#4f6270",
    "status_title_fg": "#8ea0ad",
    "metric_active_fg": "#45d0bc",
    "metric_warning_fg": "#e4b86f",
    "metric_idle_fg": "#c8d2da",
    "progress_chunk": "#45d0bc",
    "scroll_bg": "#121a20",
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
    "status_strip_bg": "#f7f1e8",
    "status_strip_border": "#ddd2c4",
    "status_separator": "#ddd4c7",
    "status_item_idle_bar": "#c8bfb3",
    "status_title_fg": "#7c7368",
    "metric_active_fg": "#2d7f6d",
    "metric_warning_fg": "#a97118",
    "metric_idle_fg": "#4e5a62",
    "progress_chunk": "#2f9aad",
    "scroll_bg": "#f6f1e8",
}


def build_orbit_correct_theme(palette):
    theme_values = dict(palette, header_action_height=HEADER_ACTION_HEIGHT)
    return """
QWidget {{
    background-color: {window_bg};
    color: {window_fg};
    font-family: "IBM Plex Sans", "Source Han Sans SC", "Segoe UI", sans-serif;
}}

QFrame#summaryPanel {{
    background-color: {summary_bg};
    border: 1px solid {summary_border};
    border-radius: 14px;
}}

QFrame#sectionCard, QFrame#toolbarPanel, QFrame#commandPane {{
    background-color: {panel_bg};
    border: 1px solid {panel_border};
    border-radius: 14px;
}}

QTabWidget::pane {{
    border: 1px solid {panel_border};
    border-radius: 14px;
    background: {panel_bg};
    top: -1px;
}}

QTabBar::tab {{
    background: {button_bg};
    border: 1px solid {button_border};
    color: {button_fg};
    min-width: 116px;
    padding: 8px 14px;
    margin-right: 6px;
    border-top-left-radius: 10px;
    border-top-right-radius: 10px;
    font-size: 12px;
    font-weight: 700;
}}

QTabBar::tab:selected {{
    background: {panel_bg};
    color: {summary_title_fg};
}}

QTabBar::tab:hover:!selected {{
    background: {button_hover_bg};
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

QLabel[role="field"] {{
    color: {muted_fg};
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    background: transparent;
    border: none;
}}

QLabel {{
    color: {window_fg};
    font-size: 12px;
    font-weight: 600;
    background: transparent;
    border: none;
}}

QPushButton {{
    background-color: {button_bg};
    border: 1px solid {button_border};
    border-radius: 12px;
    color: {button_fg};
    padding: 6px 12px;
    min-height: 32px;
    font-size: 12px;
    font-weight: 700;
}}

QPushButton:hover {{
    background-color: {button_hover_bg};
}}

QPushButton:pressed {{
    background-color: {button_pressed_bg};
}}

QPushButton[compact="true"] {{
    padding: 3px 10px;
    min-height: 22px;
    font-size: 11px;
}}

QLineEdit, QComboBox, QDoubleSpinBox {{
    background-color: {input_bg};
    border: 1px solid {input_border};
    border-radius: 10px;
    color: {input_fg};
    padding: 5px 10px;
    min-height: 16px;
    selection-background-color: {metric_active_fg};
}}

QComboBox::drop-down, QDoubleSpinBox::drop-down {{
    border: none;
    width: 20px;
}}

QComboBox QAbstractItemView {{
    background-color: {input_bg};
    color: {input_fg};
    border: 1px solid {input_border};
    selection-background-color: {button_hover_bg};
}}

QCheckBox {{
    background: transparent;
    border: none;
    spacing: 8px;
    font-size: 12px;
    font-weight: 600;
}}

QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {panel_border};
    border-radius: 4px;
    background-color: {input_bg};
}}

QCheckBox::indicator:checked {{
    background-color: {metric_active_fg};
    border: 1px solid {metric_active_fg};
}}

QProgressBar {{
    background-color: {input_bg};
    border: 1px solid {input_border};
    border-radius: 10px;
    color: {summary_title_fg};
    text-align: center;
    min-height: 20px;
}}

QProgressBar::chunk {{
    background-color: {progress_chunk};
    border-radius: 8px;
}}

QScrollArea#targetsScroll {{
    border: 1px solid {panel_border};
    border-radius: 12px;
    background-color: {scroll_bg};
}}

QWidget#targetsContent {{
    background-color: {panel_bg};
}}

QToolButton#themeToggleButton {{
    background-color: {button_bg};
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
    background-color: {button_hover_bg};
}}

QToolButton#themeToggleButton:pressed {{
    background-color: {button_pressed_bg};
}}
""".format_map(theme_values)


def build_status_strip_theme(palette):
    theme_values = dict(
        palette,
        status_tone_success_bar=palette["metric_active_fg"],
        status_tone_warning_bar=palette["metric_warning_fg"],
        status_tone_success_fg=palette["metric_active_fg"],
        status_tone_warning_fg=palette["metric_warning_fg"],
        status_tone_subtle_fg=palette["metric_idle_fg"],
    )
    return """
QWidget#statusStrip {{
    background: {status_strip_bg};
    border: 1px solid {status_strip_border};
    border-radius: 10px;
}}
QFrame#statusItem {{
    background: transparent;
    border: none;
    border-left: 4px solid {status_item_idle_bar};
    border-radius: 0px;
}}
QFrame#statusItem[tone="success"] {{
    border-left-color: {status_tone_success_bar};
}}
QFrame#statusItem[tone="warning"] {{
    border-left-color: {status_tone_warning_bar};
}}
QFrame#statusSeparator {{
    background: {status_separator};
    min-width: 1px;
    max-width: 1px;
    border: none;
}}
QLabel[role="title"] {{
    color: {status_title_fg};
    background: transparent;
    border: none;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.8px;
}}
QLabel[role="value"][tone="subtle"] {{
    color: {status_tone_subtle_fg};
    background: transparent;
    border: none;
    font-size: 13px;
    font-weight: 700;
}}
QLabel[role="value"][tone="success"] {{
    color: {status_tone_success_fg};
    background: transparent;
    border: none;
    font-size: 13px;
    font-weight: 700;
}}
QLabel[role="value"][tone="warning"] {{
    color: {status_tone_warning_fg};
    background: transparent;
    border: none;
    font-size: 13px;
    font-weight: 700;
}}
""".format_map(theme_values)


class OrbitStatusStrip(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = {}
        self.setObjectName("statusStrip")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(0)
        self._layout = layout

    def add_item(self, key, title, value):
        if self._items:
            separator = QFrame(self)
            separator.setObjectName("statusSeparator")
            separator.setFrameShape(QFrame.VLine)
            self._layout.addWidget(separator)

        container = QFrame(self)
        container.setObjectName("statusItem")
        container.setProperty("tone", "subtle")
        container.setMinimumWidth(112)

        inner = QVBoxLayout(container)
        inner.setContentsMargins(8, 0, 6, 0)
        inner.setSpacing(2)

        title_label = QLabel(title, container)
        title_label.setProperty("role", "title")
        value_label = QLabel(value, container)
        value_label.setProperty("role", "value")
        value_label.setProperty("tone", "subtle")
        value_label.setWordWrap(True)

        inner.addWidget(title_label)
        inner.addWidget(value_label)
        self._layout.addWidget(container)
        self._items[key] = (container, value_label)

    def finish(self):
        self._layout.addStretch(1)

    def apply_theme(self, palette):
        self.setStyleSheet(build_status_strip_theme(palette))
        for container, value_label in self._items.values():
            self._refresh_tone(container, value_label)

    def set_item(self, key, text, tone="subtle"):
        item = self._items.get(key)
        if item is None:
            return
        container, value_label = item
        container.setProperty("tone", tone)
        value_label.setProperty("tone", tone)
        value_label.setText(text)
        self._refresh_tone(container, value_label)

    @staticmethod
    def _refresh_tone(container, value_label):
        container.style().unpolish(container)
        container.style().polish(container)
        value_label.style().unpolish(value_label)
        value_label.style().polish(value_label)
        container.update()
        value_label.update()

class myWindow(QMainWindow, Ui_MainWindow):
    def __init__(self):
        super().__init__()
        self.setupUi(self)
        self.current_theme = "dark"
        self.last_notice = "Idle"
        self.process_manager = ManagedProcessGroup(notify=self._notify)
        self.process_manager.install_signal_handlers()

        self.all_checkboxes = self.findChildren(QCheckBox)
        self._configure_window()
        self._clear_inline_styles()
        self._build_shell()
        self._configure_form_content()

        # connect button
        self.pushButton.clicked.connect(self.measure_res)
        self.pushButton_4.clicked.connect(self.start_cor)
        self.pushButton_2.clicked.connect(self.cor_off)
        self.pushButton_3.clicked.connect(self.stop_cor)
        self.pushButton_7.clicked.connect(self.cor_recover)

        # other button
        self.pushButton_5.clicked.connect(self.selectall)
        self.pushButton_6.clicked.connect(self.cancelall)

        self.comboBox.currentIndexChanged.connect(self._refresh_status)
        self.tabWidget.currentChanged.connect(self._refresh_status)
        for cb in self.all_checkboxes:
            cb.stateChanged.connect(self._refresh_status)

        # initial parameters
        self.samplingIntervalSLineEdit.setText('6') # s
        self.correctorAccuracyUmLineEdit.setText('10') # um
        self.sampPerStepLineEdit.setText('2') # 

        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self._refresh_status)
        self.status_timer.start(700)

        self._apply_theme()
        self._refresh_status()

    def _configure_window(self):
        self.setWindowTitle("HALF Linac Orbit Correction")
        self.resize(1240, 1000)
        self.setMinimumSize(1100, 820)

    def _clear_inline_styles(self):
        widget_types = (
            QLabel,
            QPushButton,
            QLineEdit,
            QComboBox,
            QDoubleSpinBox,
            QTabWidget,
            QCheckBox,
            QProgressBar,
            QScrollArea,
        )
        for widget_type in widget_types:
            for widget in self.findChildren(widget_type):
                widget.setStyleSheet("")

    def _build_shell(self):
        self.verticalLayout_2.setContentsMargins(10, 10, 10, 10)
        self.verticalLayout_2.setSpacing(12)
        self.horizontalLayout_9.setContentsMargins(0, 0, 0, 0)
        self.horizontalLayout_9.setSpacing(14)

        self._build_summary_panel()
        self._wrap_main_sections()
        self._build_tab_layouts()

    def _build_summary_panel(self):
        panel = QFrame(self.centralwidget)
        panel.setObjectName("summaryPanel")
        panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        outer_layout = QVBoxLayout(panel)
        outer_layout.setContentsMargins(12, 10, 12, 10)
        outer_layout.setSpacing(6)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(12)

        title = QLabel("Orbit Correction", panel)
        title.setObjectName("summaryTitle")
        header_layout.addWidget(title)
        header_layout.addStretch(1)

        self.theme_toggle_button = QToolButton(panel)
        self.theme_toggle_button.setObjectName("themeToggleButton")
        self.theme_toggle_button.setFixedSize(HEADER_ACTION_HEIGHT, HEADER_ACTION_HEIGHT)
        self.theme_toggle_button.clicked.connect(self._toggle_theme)
        header_layout.addWidget(self.theme_toggle_button)
        outer_layout.addLayout(header_layout)

        self.status_panel = OrbitStatusStrip(panel)
        self.status_panel.add_item("tab", "TAB", "Run Correct")
        self.status_panel.add_item("method", "METHOD", self.comboBox.currentText())
        self.status_panel.add_item("targets", "TARGETS", "0/0")
        self.status_panel.add_item("process", "PROCESS", "Idle")
        self.status_panel.finish()
        self.status_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        outer_layout.addWidget(self.status_panel)

        self.verticalLayout_2.insertWidget(0, panel)

    def _wrap_main_sections(self):
        self.horizontalLayout_9.removeItem(self.verticalLayout_4)
        self.horizontalLayout_9.removeItem(self.verticalLayout_5)

        self.left_panel = QFrame(self.centralwidget)
        self.left_panel.setObjectName("sectionCard")
        self.left_panel.setLayout(self.verticalLayout_4)
        self.left_panel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.verticalLayout_4.setContentsMargins(12, 12, 12, 12)
        self.verticalLayout_4.setSpacing(12)
        self.verticalLayout_4.insertWidget(0, self._make_panel_title("Correction Setup", self.left_panel))

        self.right_panel = QFrame(self.centralwidget)
        self.right_panel.setObjectName("sectionCard")
        self.right_panel.setLayout(self.verticalLayout_5)
        self.right_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.verticalLayout_5.setContentsMargins(12, 12, 12, 12)
        self.verticalLayout_5.setSpacing(12)
        self.verticalLayout_5.insertWidget(0, self._make_panel_title("Target BPMs", self.right_panel))

        self.horizontalLayout_9.addWidget(self.left_panel, 2)
        self.horizontalLayout_9.addWidget(self.right_panel, 3)

        self.tabWidget.setDocumentMode(True)
        self.tabWidget.setElideMode(False)

        self.scrollArea.setObjectName("targetsScroll")
        self.scrollAreaWidgetContents_2.setObjectName("targetsContent")

    def _build_tab_layouts(self):
        self.gridLayout_4.setHorizontalSpacing(10)
        self.gridLayout_4.setVerticalSpacing(8)
        self.gridLayout_5.setHorizontalSpacing(10)
        self.gridLayout_5.setVerticalSpacing(8)
        self.gridLayout_2.setHorizontalSpacing(10)
        self.gridLayout_2.setVerticalSpacing(6)
        self.gridLayout.setHorizontalSpacing(0)
        self.gridLayout.setVerticalSpacing(10)

        self.horizontalLayout.setContentsMargins(0, 0, 0, 0)
        self.horizontalLayout.setSpacing(0)
        self.horizontalLayout.removeItem(self.gridLayout)
        self.command_pane = QFrame(self.tab)
        self.command_pane.setObjectName("commandPane")
        command_layout = QVBoxLayout(self.command_pane)
        command_layout.setContentsMargins(14, 14, 14, 14)
        command_layout.setSpacing(10)
        command_layout.addWidget(self._make_panel_title("Correction Session", self.command_pane))
        command_layout.addLayout(self.gridLayout)
        command_layout.addStretch(1)
        self.horizontalLayout.addWidget(self.command_pane)

        self.response_pane = QFrame(self.tab_2)
        self.response_pane.setObjectName("commandPane")
        response_outer = QVBoxLayout(self.tab_2)
        response_outer.setContentsMargins(0, 0, 0, 0)
        response_outer.setSpacing(0)
        response_outer.addWidget(self.response_pane)
        response_layout = QVBoxLayout(self.response_pane)
        response_layout.setContentsMargins(14, 14, 14, 14)
        response_layout.setSpacing(12)
        response_layout.addWidget(self._make_panel_title("Response Matrix", self.response_pane))
        response_layout.addStretch(1)
        self.pushButton.setParent(self.response_pane)
        self.pushButton.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        response_layout.addWidget(self.pushButton, 0)
        response_layout.addStretch(2)

    def _configure_form_content(self):
        self.label_6.setProperty("role", "field")
        self.samplingIntervalSLabel.setProperty("role", "field")
        self.correctorAccuracyUmLabel.setProperty("role", "field")
        self.sampPerStepLabel.setProperty("role", "field")
        self.label_45.setProperty("role", "field")
        self.label_46.setProperty("role", "field")

        self.label_6.setText("Method")
        self.samplingIntervalSLabel.setText("Sampling Interval (s)")
        self.correctorAccuracyUmLabel.setText("Accuracy (um)")
        self.sampPerStepLabel.setText("Samples / Step")
        self.label_45.setText("BPM X")
        self.label_46.setText("BPM Y")

        self.tabWidget.setTabText(self.tabWidget.indexOf(self.tab), "Run Correct")
        self.tabWidget.setTabText(self.tabWidget.indexOf(self.tab_2), "Response Matrix")

        for button in (
            self.pushButton,
            self.pushButton_2,
            self.pushButton_3,
            self.pushButton_4,
            self.pushButton_5,
            self.pushButton_6,
            self.pushButton_7,
        ):
            button.setProperty("compact", True)

        self.pushButton.setText("Measure Response")
        self.pushButton_4.setText("Start Correction")
        self.pushButton_3.setText("Stop Correction")
        self.pushButton_2.setText("Zero Correctors")
        self.pushButton_7.setText("Recover Correctors")
        self.pushButton_5.setText("All BPMs")
        self.pushButton_6.setText("Clear Selection")

        self.progressBar.setRange(0, len(self.all_checkboxes))
        self.progressBar.setTextVisible(True)
        self.progressBar.setFormat("%v/%m")

    def _make_panel_title(self, text, parent):
        label = QLabel(text, parent)
        label.setObjectName("panelTitle")
        return label

    def _palette(self):
        return DARK_THEME if self.current_theme == "dark" else LIGHT_THEME

    def _apply_theme(self):
        palette = self._palette()
        self.centralwidget.setStyleSheet(build_orbit_correct_theme(palette))
        self.status_panel.apply_theme(palette)
        self.status_panel.setFixedHeight(self.status_panel.sizeHint().height())
        self._update_theme_toggle_button()

    def _update_theme_toggle_button(self):
        if self.current_theme == "dark":
            self.theme_toggle_button.setText("\u2600")
            self.theme_toggle_button.setToolTip("Switch to light theme.")
        else:
            self.theme_toggle_button.setText("\u263D")
            self.theme_toggle_button.setToolTip("Switch to dark theme.")

    def _toggle_theme(self):
        self.current_theme = "light" if self.current_theme == "dark" else "dark"
        self._apply_theme()
        self._refresh_status()

    def _notify(self, message):
        print(message)
        self.last_notice = message
        self._refresh_status()

    def _selected_bpm_count(self):
        return sum(1 for cb in self.all_checkboxes if cb.isChecked())

    def _current_process_status(self):
        if self.process_manager.is_running("orbit_correction"):
            return "Correction Running", "success"
        if self.process_manager.is_running("response_matrix"):
            return "Response Scan", "success"
        if self.process_manager.is_running("cor_off"):
            return "Zeroing", "warning"
        if self.process_manager.is_running("cor_recover"):
            return "Recovering", "warning"
        return "Idle", "subtle"

    def _refresh_status(self):
        if not hasattr(self, "status_panel"):
            return
        total = len(self.all_checkboxes)
        selected = self._selected_bpm_count()
        process_text, process_tone = self._current_process_status()
        self.status_panel.set_item("tab", self.tabWidget.tabText(self.tabWidget.currentIndex()), "subtle")
        self.status_panel.set_item("method", self.comboBox.currentText(), "subtle")
        self.status_panel.set_item("targets", f"{selected}/{total}", "success" if selected else "warning")
        self.status_panel.set_item("process", process_text, process_tone)
        self.progressBar.setValue(selected)

    def _extract_number(self, s):
        # 提取字符串中的第一个连续数字并转为整数
        match = re.search(r'\d+', s)
        return int(match.group()) if match else 0

    def selectall(self):
        for cb in self.all_checkboxes:
            cb.setChecked(True)

    def cancelall(self):
        for cb in self.all_checkboxes:
            cb.setChecked(False)
    
    def all_BPM_target_value(self):
        # 将BPM的目标值按照1~43依次排列
        all_bpmx_spinboxes = self.findChildren(QDoubleSpinBox, QRegExp("bpmx_*"))
        # 组合数据（索引、名称、值）
        combined_data = []
        for sb in all_bpmx_spinboxes:
            index = self._extract_number(sb.objectName())
            combined_data.append( (index, sb.objectName(), sb.value()) )
        # 按索引排序
        combined_data.sort(key=lambda x: x[0])
        # 解包排序后的数据
        indeics, all_bpmx_spinboxes_names, all_bpmx_target_values = zip(*combined_data)
        # 转换为列表（如果后续需要修改）
        # all_bpmx_spinboxes_names = list(all_bpmx_spinboxes_names)
        all_bpmx_target_values = list(all_bpmx_target_values)

        all_bpmy_spinboxes = self.findChildren(QDoubleSpinBox, QRegExp("bpmy_*"))
        # 组合数据（索引、名称、值）
        combined_data = []
        for sb in all_bpmy_spinboxes:
            index = self._extract_number(sb.objectName())
            combined_data.append( (index, sb.objectName(), sb.value()) )
        # 按索引排序
        combined_data.sort(key=lambda x: x[0])
        # 解包排序后的数据
        indeics, all_bpmy_spinboxes_names, all_bpmy_target_values = zip(*combined_data)
        # 转换为列表（如果后续需要修改）
        # all_bpmy_spinboxes_names = list(all_bpmy_spinboxes_names)
        all_bpmy_target_values = list(all_bpmy_target_values)

        return all_bpmx_target_values, all_bpmy_target_values

    def target_BPMs(self):
        all_bpmx_target_values, all_bpmy_target_values = self.all_BPM_target_value()

        # print(all_bpmx_target_values)
        # print(all_bpmy_target_values)
        all_checkboxes = self.findChildren(QCheckBox)
        bpm_target_list = [cb.text() for cb in all_checkboxes if cb.isChecked()]
        bpm_target_list.sort(key=self._extract_number)

        indices = []
        for cb in bpm_target_list:
            indices.append(self._extract_number(cb))
        # print(indices)
        bpmx_target_values = [all_bpmx_target_values[i-1] for i in indices]
        bpmy_target_values = [all_bpmy_target_values[i-1] for i in indices]
        # print(bpmx_target_values)
        # print(bpmy_target_values)

        # target_list = list(zip(bpm_target_list, bpmx_target_values, bpmy_target_values))
        
        return bpm_target_list, bpmx_target_values, bpmy_target_values
        
    def measure_res(self): #measure response matrix
        self.process_manager.start_process(
            key="response_matrix",
            label="Response Matrix Measurement",
            cmd=["python3", "findresponse.py"],
            cwd=st.rootpath + "/src/apps/orbit_correct",
        )

    def start_cor(self):
        
        # prepare the target paras. 
        bpm_target_list, bpmx_target_values, bpmy_target_values = self.target_BPMs()
        if not bpm_target_list:
            QMessageBox.warning(
                self,
                "Orbit Correct",
                "Select at least one BPM before starting correction.",
            )
            return
        bpmx_target_values = [str(i) for i in bpmx_target_values]
        bpmy_target_values = [str(i) for i in bpmy_target_values]
        
        cmd = [
            "python3", "correct.py",                  #0
            "start_cor",                              #1
            self.comboBox.currentText(),              #2   method
            self.samplingIntervalSLineEdit.text(),    #3   samp_interval
            self.correctorAccuracyUmLineEdit.text(),  #4   cor_accuracy
            self.sampPerStepLineEdit.text(),          #5   samples_perstep
            ",".join(bpm_target_list),                     #6   target_BPMlist
            ",".join(bpmx_target_values),                     #7   target_BPMlist
            ",".join(bpmy_target_values)                     #8   target_BPMlist
        ]
        self.process_manager.start_process(
            key="orbit_correction",
            label="Orbit Correction",
            cmd=cmd,
            cwd=st.rootpath + "/src/apps/orbit_correct",
        )
 



    # cor_off
    # def cor_off(self):
    #     Popen("python3 correct.py cor_off",cwd=st.rootpath+"/apps/orbit_correct",shell=True)
    def cor_off(self):
        bpm_target_list, bpmx_target_values, bpmy_target_values = self.target_BPMs()
        cmd = [
            "python3", "correct.py",                  #0
            "cor_off",                                 #1
            ",".join(bpm_target_list)                  #2
        ]
        self.process_manager.start_process(
            key="cor_off",
            label="Corrector Reset",
            cmd=cmd,
            cwd=st.rootpath + "/src/apps/orbit_correct",
            expect_running=False,
        )

    def cor_recover(self):
        # bpm_target_list, bpmx_target_values, bpmy_target_values = self.target_BPMs()
        cmd = [
            "python3", "correct.py",                  #0
            "cor_recover",                             #1
            # ",".join(bpm_target_list)                  #2
        ]
        self.process_manager.start_process(
            key="cor_recover",
            label="Corrector Recover",
            cmd=cmd,
            cwd=st.rootpath + "/src/apps/orbit_correct",
            expect_running=False,
        )


    # stop_cor
    def stop_cor(self):
        self.process_manager.stop_all()
    
    # 窗口关闭事件
    def closeEvent(self, event):
        self.process_manager.shutdown()
        event.accept()




if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = myWindow()
    window.show()
    sys.exit(app.exec_())
