from __future__ import annotations

import csv
import sys
import threading
from datetime import datetime
from pathlib import Path

_REPO_BOOTSTRAP_ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / "repo_bootstrap.py").is_file()
)
if str(_REPO_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_BOOTSTRAP_ROOT))

from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

from PyQt5.QtCore import QThread, Qt, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from half_linac.src.shared.app_theme import resolve_initial_theme
from half_linac.src.shared.machine_profile import (
    RuntimeContextWidget,
    load_profile,
)
from half_linac.src.shared.machine_profile.loader import resolve_control_backend
from half_linac.src.shared.pv_connection import (
    PvConnectionResult,
    PvEndpoint,
    collect_pv_endpoints,
    probe_pv_connections,
    unique_pv_names,
)
from half_linac.src.shared.window_activation import install_qt_window_raise_handler


DARK = {
    "window": "#0f1519",
    "panel": "#172027",
    "border": "#2a3943",
    "text": "#e6edf2",
    "muted": "#91a2ad",
    "input": "#10171c",
    "accent": "#45d0bc",
    "success": "#54c99a",
    "danger": "#e37878",
    "pending": "#e0aa58",
}

LIGHT = {
    "window": "#f4f1eb",
    "panel": "#fffdf9",
    "border": "#d6cec3",
    "text": "#293740",
    "muted": "#746f68",
    "input": "#ffffff",
    "accent": "#287866",
    "success": "#277a58",
    "danger": "#b44141",
    "pending": "#966312",
}


def build_theme(colors: dict[str, str]) -> str:
    return f"""
QMainWindow, QWidget {{
    background: {colors['window']};
    color: {colors['text']};
    font-family: "IBM Plex Sans", "Source Han Sans SC", "Segoe UI", sans-serif;
}}
QFrame#headerPanel {{
    background: {colors['panel']}; border: 1px solid {colors['border']}; border-radius: 14px;
}}
QFrame#tableToolbar {{
    background: {colors['panel']}; border: 1px solid {colors['border']}; border-radius: 10px;
}}
QLabel#title {{ background: transparent; font-size: 22px; font-weight: 700; }}
QLabel[role="field"] {{
    background: transparent;
    border: none;
    color: {colors['muted']};
    font-size: 11px;
    font-weight: 600;
}}
QFrame#statusStrip {{
    background: transparent; border: none; border-radius: 0px;
}}
QFrame#statusItem {{
    background: transparent; border: none; border-left: 4px solid {colors['border']}; border-radius: 0px;
}}
QFrame#statusItem[tone="success"] {{ border-left-color: {colors['success']}; }}
QFrame#statusItem[tone="warning"] {{ border-left-color: {colors['pending']}; }}
QFrame#statusItem[tone="danger"] {{ border-left-color: {colors['danger']}; }}
QFrame#statusSeparator {{ background: {colors['border']}; border: none; max-width: 1px; }}
QLabel[role="statusTitle"] {{
    background: transparent; color: {colors['muted']}; font-size: 9px; font-weight: 700;
}}
QLabel[role="statusValue"] {{
    background: transparent; color: {colors['muted']}; font-size: 13px; font-weight: 700;
}}
QLabel[role="statusValue"][tone="success"] {{ color: {colors['success']}; }}
QLabel[role="statusValue"][tone="warning"] {{ color: {colors['pending']}; }}
QLabel[role="statusValue"][tone="danger"] {{ color: {colors['danger']}; }}
QLabel#fieldLabel {{ background: transparent; color: {colors['muted']}; font-size: 11px; font-weight: 600; }}
QLabel#tableTitle {{ background: transparent; font-size: 13px; font-weight: 700; }}
QWidget#runtimeContext {{ background: transparent; border: none; }}
QWidget#runtimeContext QLabel#runtimeBackendLabel {{
    background: transparent;
}}
QFrame#headerPanel QLineEdit, QFrame#headerPanel QComboBox, QFrame#headerPanel QDoubleSpinBox,
QFrame#tableToolbar QLineEdit, QFrame#tableToolbar QComboBox {{
    background: {colors['input']};
}}
QLineEdit, QComboBox, QDoubleSpinBox {{
    background: {colors['input']}; border: 1px solid {colors['border']};
    border-radius: 10px; padding: 5px 9px; min-height: 20px;
}}
QPushButton, QToolButton {{
    background: {colors['panel']}; border: 1px solid {colors['border']};
    border-radius: 10px; padding: 6px 11px; min-height: 20px; font-weight: 700;
}}
QToolButton#themeToggleButton {{ border-radius: 11px; }}
QPushButton:hover, QToolButton:hover {{ border-color: {colors['accent']}; }}
QPushButton#checkButton {{ background: {colors['accent']}; color: white; border-color: {colors['accent']}; }}
QPushButton#stopButton {{ color: {colors['danger']}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {colors['muted']}; }}
QTableWidget {{
    background: {colors['panel']}; alternate-background-color: {colors['input']};
    border: 1px solid {colors['border']}; gridline-color: {colors['border']};
    selection-background-color: {colors['accent']}; selection-color: white;
}}
QHeaderView::section {{
    background: {colors['input']}; color: {colors['text']};
    border: none; border-right: 1px solid {colors['border']};
    border-bottom: 1px solid {colors['border']}; padding: 7px 8px; font-weight: 700;
}}
"""


class StatusStrip(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statusStrip")
        self._items = {}
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(8, 4, 8, 4)
        self._layout.setSpacing(0)

    def add_item(self, key: str, title: str, value: str):
        if self._items:
            separator = QFrame(self)
            separator.setObjectName("statusSeparator")
            separator.setFrameShape(QFrame.VLine)
            self._layout.addWidget(separator)

        item = QFrame(self)
        item.setObjectName("statusItem")
        item.setProperty("tone", "subtle")
        item.setMinimumWidth(112)
        item_layout = QVBoxLayout(item)
        item_layout.setContentsMargins(8, 0, 6, 0)
        item_layout.setSpacing(2)

        title_label = QLabel(title, item)
        title_label.setProperty("role", "statusTitle")
        value_label = QLabel(value, item)
        value_label.setProperty("role", "statusValue")
        value_label.setProperty("tone", "subtle")
        value_label.setWordWrap(True)
        item_layout.addWidget(title_label)
        item_layout.addWidget(value_label)
        self._layout.addWidget(item)
        self._items[key] = (item, value_label)

    def finish(self):
        self._layout.addStretch(1)

    def set_item(self, key: str, value: str, tone: str = "subtle", tooltip: str = ""):
        item, value_label = self._items[key]
        item.setProperty("tone", tone)
        value_label.setProperty("tone", tone)
        value_label.setText(value)
        item.setToolTip(tooltip)
        value_label.setToolTip(tooltip)
        for widget in (item, value_label):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()


class ConnectionWorker(QThread):
    result_ready = pyqtSignal(object)
    scan_finished = pyqtSignal(bool)

    def __init__(self, pv_names: tuple[str, ...], timeout_s: float, parent=None):
        super().__init__(parent)
        self.pv_names = pv_names
        self.timeout_s = timeout_s
        self._stop_requested = threading.Event()

    def request_stop(self):
        self._stop_requested.set()

    def run(self):
        try:
            cancelled = probe_pv_connections(
                self.pv_names,
                self.timeout_s,
                on_result=self.result_ready.emit,
                stop_requested=self._stop_requested.is_set,
            )
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            for pv_name in self.pv_names:
                self.result_ready.emit(PvConnectionResult(pv_name, False, detail))
            cancelled = False
        self.scan_finished.emit(cancelled)


class PvConnectionWindow(QMainWindow):
    COLUMN_NAMES = ("Backend", "Element", "Type", "Channel", "PV", "Status", "Detail")

    def __init__(self):
        super().__init__()
        self.machine_profile = load_profile()
        self.control_backend = resolve_control_backend(
            None,
            self.machine_profile.machine.default_mode,
        )
        self.current_theme = resolve_initial_theme()
        self.endpoints: tuple[PvEndpoint, ...] = ()
        self.results: dict[str, PvConnectionResult] = {}
        self.worker: ConnectionWorker | None = None
        self.scan_state = "Idle"
        self._build_ui()
        self._apply_theme()
        self._load_endpoints()
        install_qt_window_raise_handler(self)

    def _build_ui(self):
        self.setWindowTitle(f"{self.machine_profile.machine.display_name} PV Connection Check")
        self.resize(1120, 720)
        self.setMinimumSize(780, 520)

        root = QWidget(self)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        header_panel = QFrame(root)
        header_panel.setObjectName("headerPanel")
        header_layout = QVBoxLayout(header_panel)
        header_layout.setContentsMargins(12, 10, 12, 10)
        header_layout.setSpacing(6)

        self.header_grid = QGridLayout()
        self.header_grid.setContentsMargins(0, 0, 0, 0)
        self.header_grid.setHorizontalSpacing(8)
        self.header_grid.setVerticalSpacing(6)
        self.header_title = QLabel("PV Connection Check", header_panel)
        self.header_title.setObjectName("title")
        self.timeout_spin = QDoubleSpinBox(header_panel)
        self.timeout_spin.setRange(0.1, 10.0)
        self.timeout_spin.setSingleStep(0.5)
        self.timeout_spin.setValue(1.0)
        self.timeout_spin.setSuffix(" s")
        self.timeout_spin.setFixedWidth(92)
        self.check_button = QPushButton("Check", header_panel)
        self.check_button.setObjectName("checkButton")
        self.stop_button = QPushButton("Stop", header_panel)
        self.stop_button.setObjectName("stopButton")
        self.stop_button.setEnabled(False)
        self.export_button = QPushButton("Export", root)
        self.export_button.setToolTip(
            "Export all mappings for the active machine and backend with their current connection status."
        )
        timeout_label = QLabel("Timeout", header_panel)
        timeout_label.setObjectName("fieldLabel")
        self.runtime_context = RuntimeContextWidget(
            machine_id=self.machine_profile.machine.id,
            machine_display_name=self.machine_profile.machine.display_name,
            control_backend=self.control_backend,
            parent=header_panel,
        )
        self.theme_button = QToolButton(header_panel)
        self.theme_button.setObjectName("themeToggleButton")
        self.theme_button.setFixedSize(32, 32)
        self.theme_button.clicked.connect(self._toggle_theme)
        header_layout.addLayout(self.header_grid)

        self.status_panel = StatusStrip(header_panel)
        self.status_panel.add_item("scan", "Scan", "Idle")
        self.status_panel.add_item("mappings", "Mappings", "0")
        self.status_panel.add_item("unique", "Unique PVs", "0")
        self.status_panel.add_item("connected", "Connected", "0")
        self.status_panel.add_item("unavailable", "Unavailable", "0")
        self.status_panel.add_item("remaining", "Not Checked", "0")
        self.status_panel.finish()
        header_layout.addWidget(self.status_panel)
        layout.addWidget(header_panel)

        table_toolbar = QFrame(root)
        table_toolbar.setObjectName("tableToolbar")
        self.table_toolbar_grid = QGridLayout(table_toolbar)
        filters = self.table_toolbar_grid
        filters.setContentsMargins(9, 6, 9, 6)
        filters.setSpacing(7)
        table_title = QLabel("PV Inventory", table_toolbar)
        table_title.setObjectName("tableTitle")
        self.search_edit = QLineEdit(table_toolbar)
        self.search_edit.setPlaceholderText("Filter element, channel, or PV")
        self.status_combo = QComboBox(table_toolbar)
        self.status_combo.addItems(("All statuses", "Connected", "Unavailable", "Not checked"))
        self.table_title = table_title
        self.timeout_label = timeout_label
        self.table_toolbar_widgets = (
            self.table_title,
            self.search_edit,
            self.status_combo,
            self.timeout_label,
            self.timeout_spin,
            self.export_button,
            self.stop_button,
            self.check_button,
        )
        layout.addWidget(table_toolbar)

        self.table = QTableWidget(0, len(self.COLUMN_NAMES), root)
        self.table.setHorizontalHeaderLabels(self.COLUMN_NAMES)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        for column in (0, 1, 2, 3, 5):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        header.setSectionResizeMode(6, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)
        self.setCentralWidget(root)
        self._update_responsive_layouts()

        self.search_edit.textChanged.connect(self._apply_filters)
        self.status_combo.currentTextChanged.connect(self._apply_filters)
        self.check_button.clicked.connect(self._start_scan)
        self.stop_button.clicked.connect(self._stop_scan)
        self.export_button.clicked.connect(self._export_report)

    def _load_endpoints(self):
        self.endpoints = collect_pv_endpoints(
            self.machine_profile,
            (self.control_backend,),
        )
        self.results.clear()
        self._populate_table()

    def _populate_table(self):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.endpoints))
        for row, endpoint in enumerate(self.endpoints):
            values = (
                endpoint.backend,
                endpoint.element_id,
                endpoint.element_kind,
                endpoint.logical_channel,
                endpoint.pv_name,
                "Not checked",
                "",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 4:
                    item.setToolTip(value)
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(True)
        self._apply_filters()
        self._update_summary("Idle")

    def _start_scan(self):
        if self.worker and self.worker.isRunning():
            return
        pv_names = unique_pv_names(self.endpoints)
        if not pv_names:
            return
        self.results.clear()
        self._set_scan_controls(True)
        for row in range(self.table.rowCount()):
            self._set_status_item(row, "Checking", "Waiting for connection result")
        self.worker = ConnectionWorker(pv_names, self.timeout_spin.value(), self)
        self.worker.result_ready.connect(self._record_result)
        self.worker.scan_finished.connect(self._finish_scan)
        self.worker.start()
        self._update_summary("Checking")

    def _stop_scan(self):
        if self.worker and self.worker.isRunning():
            self.worker.request_stop()
            self.stop_button.setEnabled(False)
            self._update_summary("Stopping")

    def _record_result(self, result: PvConnectionResult):
        self.results[result.pv_name] = result
        for row in range(self.table.rowCount()):
            if self.table.item(row, 4).text() == result.pv_name:
                self._set_status_item(
                    row,
                    "Connected" if result.connected else "Unavailable",
                    result.detail,
                )
        self._apply_filters()
        self._update_summary()

    def _finish_scan(self, cancelled: bool):
        self._set_scan_controls(False)
        if cancelled:
            for row in range(self.table.rowCount()):
                if self.table.item(row, 5).text() == "Checking":
                    self._set_status_item(row, "Not checked", "Scan stopped")
        self._apply_filters()
        self._update_summary("Stopped" if cancelled else "Complete")

    def _set_scan_controls(self, scanning: bool):
        self.timeout_spin.setEnabled(not scanning)
        self.check_button.setEnabled(not scanning)
        self.stop_button.setEnabled(scanning)

    def _set_status_item(self, row: int, status: str, detail: str):
        colors = DARK if self.current_theme == "dark" else LIGHT
        color = {
            "Connected": colors["success"],
            "Unavailable": colors["danger"],
            "Checking": colors["pending"],
        }.get(status, colors["muted"])
        status_item = self.table.item(row, 5)
        status_item.setText(status)
        status_item.setForeground(QColor(color))
        self.table.item(row, 6).setText(detail)

    def _apply_filters(self, *_args):
        needle = self.search_edit.text().strip().lower()
        wanted_status = self.status_combo.currentText()
        for row in range(self.table.rowCount()):
            text_match = not needle or any(
                needle in self.table.item(row, column).text().lower() for column in range(5)
            )
            status = self.table.item(row, 5).text()
            status_match = wanted_status == "All statuses" or status == wanted_status
            self.table.setRowHidden(row, not (text_match and status_match))

    def _update_summary(self, prefix: str | None = None):
        if prefix is not None:
            self.scan_state = prefix
        unique_count = len(unique_pv_names(self.endpoints))
        connected = sum(result.connected for result in self.results.values())
        unavailable = len(self.results) - connected
        pending = unique_count - len(self.results)
        scan_tone = {
            "Checking": "warning",
            "Stopping": "warning",
            "Complete": "success" if unavailable == 0 else "danger",
            "Stopped": "warning",
        }.get(self.scan_state, "subtle")
        self.status_panel.set_item("scan", self.scan_state, scan_tone)
        self.status_panel.set_item("mappings", str(len(self.endpoints)))
        self.status_panel.set_item("unique", str(unique_count))
        self.status_panel.set_item(
            "connected",
            str(connected),
            "success" if connected else "subtle",
        )
        self.status_panel.set_item(
            "unavailable",
            str(unavailable),
            "danger" if unavailable else "subtle",
        )
        self.status_panel.set_item(
            "remaining",
            str(pending),
            "warning" if self.scan_state in {"Checking", "Stopping"} and pending else "subtle",
        )

    def _export_report(self):
        machine_id = self.machine_profile.machine.id
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = (
            f"{machine_id}_{self.control_backend}_pv_connection_{timestamp}.csv"
        )
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export PV connection report", default_name, "CSV files (*.csv)"
        )
        if not filename:
            return
        with Path(filename).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("machine",) + tuple(name.lower() for name in self.COLUMN_NAMES))
            for row in range(self.table.rowCount()):
                writer.writerow(
                    (machine_id,)
                    + tuple(self.table.item(row, column).text() for column in range(len(self.COLUMN_NAMES)))
                )
        self.statusBar().showMessage(f"Report exported to {filename}", 6000)

    def _toggle_theme(self):
        self.current_theme = "light" if self.current_theme == "dark" else "dark"
        self._apply_theme()

    def _apply_theme(self):
        colors = DARK if self.current_theme == "dark" else LIGHT
        self.setStyleSheet(build_theme(colors))
        self.theme_button.setText("\u2600" if self.current_theme == "dark" else "\u263d")
        self.theme_button.setToolTip(
            "Switch to light theme" if self.current_theme == "dark" else "Switch to dark theme"
        )
        for row in range(self.table.rowCount()):
            status = self.table.item(row, 5).text()
            detail = self.table.item(row, 6).text()
            self._set_status_item(row, status, detail)

    @staticmethod
    def _clear_layout(layout):
        while layout.count():
            layout.takeAt(0)

    def _update_responsive_layouts(self):
        if not hasattr(self, "header_grid"):
            return

        self._clear_layout(self.header_grid)
        self._clear_layout(self.table_toolbar_grid)
        narrow = self.width() < 900

        if narrow:
            self.header_grid.addWidget(self.header_title, 0, 0)
            self.header_grid.setColumnStretch(1, 1)
            self.header_grid.addWidget(self.theme_button, 0, 2, Qt.AlignRight)
            self.header_grid.addWidget(self.runtime_context, 1, 0, 1, 3)

            self.table_toolbar_grid.addWidget(self.table_title, 0, 0)
            self.table_toolbar_grid.addWidget(self.search_edit, 0, 1)
            self.table_toolbar_grid.addWidget(self.status_combo, 0, 2)
            self.table_toolbar_grid.setColumnStretch(1, 1)
            self.table_toolbar_grid.addWidget(self.timeout_label, 1, 0)
            self.table_toolbar_grid.addWidget(self.timeout_spin, 1, 1, Qt.AlignLeft)
            self.table_toolbar_grid.addWidget(self.export_button, 1, 2)
            self.table_toolbar_grid.addWidget(self.stop_button, 1, 3)
            self.table_toolbar_grid.addWidget(self.check_button, 1, 4)
            return

        self.header_grid.addWidget(self.header_title, 0, 0)
        self.header_grid.setColumnStretch(1, 1)
        self.header_grid.addWidget(self.runtime_context, 0, 2)
        self.header_grid.addWidget(self.theme_button, 0, 3)

        for column, widget in enumerate(self.table_toolbar_widgets):
            self.table_toolbar_grid.addWidget(widget, 0, column)
        self.table_toolbar_grid.setColumnStretch(1, 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_responsive_layouts()

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.request_stop()
            self.worker.wait(int((self.timeout_spin.value() + 1.5) * 1000))
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    window = PvConnectionWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
