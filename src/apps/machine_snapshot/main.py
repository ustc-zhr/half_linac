from __future__ import annotations

import os
import socket
import sys
import threading
from datetime import datetime, timezone
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
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from half_linac.src.apps.machine_snapshot.storage import (
    SnapshotHistoryItem,
    export_comparison_csv,
    export_snapshot_json,
    list_snapshot_history,
    save_capture_snapshot,
    save_restore_result,
)
from half_linac.src.apps.machine_snapshot.restore import build_restore_candidates, RestoreWorker
from half_linac.src.shared.app_theme import resolve_initial_theme
from half_linac.src.shared.machine_profile import RuntimeContextWidget, load_app_context
from half_linac.src.shared.machine_profile.app_runtime import make_runtime_run_id
from half_linac.src.shared.machine_state import (
    CAPTURE_GROUP_HIGH_VOLTAGE,
    CAPTURE_GROUP_LLRF,
    CAPTURE_GROUP_MAGNETS,
    CAPTURE_GROUP_TIMING,
    CAPTURE_GROUP_OBSERVATIONS,
    CAPTURE_GROUP_READBACKS,
    CAPTURE_GROUP_SETTINGS,
    DiffStatus,
    MachineStateError,
    MachineStateSnapshot,
    SampleQuality,
    SnapshotDiffRow,
    build_capture_plan,
    build_profile_signature,
    compare_snapshots,
    load_snapshot,
)
from half_linac.src.shared.pv_sampling import sample_capture_points
from half_linac.src.shared.window_activation import install_qt_window_raise_handler


APP_DIR = Path(__file__).resolve().parent
CAPTURE_TIMEOUT_S = 10.0
TABLE_COLUMNS = (
    "Device",
    "Channel",
    "Class",
    "A",
    "B / Current",
    "Delta",
    "Unit",
    "Status",
    "Quality",
)


PALETTES = {
    "dark": {
        "window": "#0f1519",
        "panel": "#172027",
        "input": "#10171c",
        "border": "#2a3943",
        "text": "#e6edf2",
        "muted": "#91a2ad",
        "accent": "#45d0bc",
        "success": "#54c99a",
        "warning": "#e0aa58",
        "danger": "#e37878",
    },
    "light": {
        "window": "#f4f1eb",
        "panel": "#fffdf9",
        "input": "#ffffff",
        "border": "#d6cec3",
        "text": "#293740",
        "muted": "#746f68",
        "accent": "#287866",
        "success": "#277a58",
        "warning": "#966312",
        "danger": "#b44141",
    },
}


def build_theme(colors: dict[str, str]) -> str:
    return f"""
QMainWindow, QWidget, QDialog {{
  background: {colors['window']}; color: {colors['text']};
  font-family: "IBM Plex Sans", "Source Han Sans SC", "Segoe UI", sans-serif;
}}
QFrame#header, QFrame#toolbar, QFrame#historyPanel, QFrame#detailPanel {{
  background: {colors['panel']}; border: 1px solid {colors['border']}; border-radius: 10px;
}}
QLabel#title {{ font-size: 21px; font-weight: 700; }}
QLabel, QCheckBox {{ background: transparent; border: none; }}
QLabel#banner {{ color: {colors['warning']}; font-weight: 600; }}
QLineEdit, QComboBox, QTextEdit, QListWidget, QTableWidget {{
  background: {colors['input']}; color: {colors['text']};
  border: 1px solid {colors['border']}; border-radius: 8px;
}}
QLineEdit, QComboBox {{ min-height: 24px; padding: 3px 7px; }}
QPushButton, QToolButton {{
  background: {colors['panel']}; color: {colors['text']}; border: 1px solid {colors['border']};
  border-radius: 8px; min-height: 26px; padding: 3px 10px; font-weight: 700;
}}
QPushButton:hover, QToolButton:hover {{ border-color: {colors['accent']}; }}
QPushButton#captureButton {{ background: {colors['accent']}; color: white; border-color: {colors['accent']}; }}
QPushButton#stopButton {{ color: {colors['danger']}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {colors['muted']}; }}
QHeaderView::section {{
  background: {colors['input']}; color: {colors['text']}; border: none;
  border-right: 1px solid {colors['border']}; border-bottom: 1px solid {colors['border']};
  padding: 6px 7px; font-weight: 700;
}}
QTableWidget {{ alternate-background-color: {colors['panel']}; gridline-color: {colors['border']}; }}
QTableWidget::item:selected, QListWidget::item:selected {{ background: {colors['accent']}; color: white; }}
"""


class CaptureDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Capture Machine State")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(self)
        self.name_edit.setPlaceholderText(datetime.now().astimezone().strftime("Snapshot %Y-%m-%d %H:%M:%S"))
        self.note_edit = QTextEdit(self)
        self.note_edit.setMaximumHeight(90)
        form.addRow("Name", self.name_edit)
        form.addRow("Operator note", self.note_edit)
        layout.addLayout(form)

        self.magnets_check = QCheckBox("Magnets", self)
        self.high_voltage_check = QCheckBox("High voltage", self)
        self.llrf_check = QCheckBox("LLRF", self)
        self.timing_check = QCheckBox("Timing", self)
        # Compatibility aliases for callers of the original capture dialog.
        self.settings_check = self.magnets_check
        self.readbacks_check = self.high_voltage_check
        self.observations_check = QCheckBox("Beam observations", self)
        for checkbox in (self.magnets_check, self.high_voltage_check, self.llrf_check, self.timing_check):
            checkbox.setChecked(True); layout.addWidget(checkbox)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept_if_valid(self):
        if not self.selected_groups():
            QMessageBox.warning(self, "Capture Machine State", "Select at least one capture group.")
            return
        self.accept()

    def selected_groups(self) -> tuple[str, ...]:
        groups = []
        for checkbox, group in ((self.magnets_check, CAPTURE_GROUP_MAGNETS), (self.high_voltage_check, CAPTURE_GROUP_HIGH_VOLTAGE), (self.llrf_check, CAPTURE_GROUP_LLRF), (self.timing_check, CAPTURE_GROUP_TIMING)):
            if checkbox.isChecked(): groups.append(group)
        return tuple(groups)

    def snapshot_name(self) -> str:
        return self.name_edit.text().strip() or datetime.now().astimezone().strftime(
            "Snapshot %Y-%m-%d %H:%M:%S"
        )


class RestoreDialog(QDialog):
    def __init__(self, snapshot, context, parent=None):
        super().__init__(parent); self.snapshot = snapshot; self.context = context
        self.setWindowTitle(f"Restore {snapshot.name}"); self.resize(900, 520)
        layout = QVBoxLayout(self); self.table = QTableWidget(0, 7, self)
        self.table.setHorizontalHeaderLabels(("Select", "Subsystem", "Device", "Parameter", "Saved", "Current", "PV")); layout.addWidget(self.table)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self); buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
        try:
            from epics import caget
        except Exception:
            caget = lambda _pv: None
        self.candidates = list(build_restore_candidates(snapshot, context.profile, context.control_backend.name, caget)); self.table.setRowCount(len(self.candidates))
        for row, candidate in enumerate(self.candidates):
            check = QTableWidgetItem(); check.setCheckState(Qt.Checked if candidate.selected else Qt.Unchecked); self.table.setItem(row, 0, check)
            values = ((candidate.entry.element_kind.title()), candidate.entry.display_name, candidate.entry.logical_channel, _format_value(candidate.entry), "—" if candidate.current_value is None else f"{candidate.current_value:.9g}", candidate.pv_name or "—")
            for col, value in enumerate(values, 1): self.table.setItem(row, col, QTableWidgetItem(str(value)))

    def selected_candidates(self):
        for row, candidate in enumerate(self.candidates): candidate.selected = self.table.item(row, 0).checkState() == Qt.Checked
        return tuple(candidate for candidate in self.candidates if candidate.selected)


class CaptureWorker(QThread):
    progress = pyqtSignal(object, int, int)
    capture_finished = pyqtSignal(object)
    capture_failed = pyqtSignal(str)

    def __init__(self, context, groups, name, note, parent=None):
        super().__init__(parent)
        self.context = context
        self.groups = tuple(groups)
        self.name = name
        self.note = note
        self._stop_requested = threading.Event()

    def request_stop(self):
        self._stop_requested.set()

    def run(self):
        started = datetime.now(timezone.utc)
        try:
            plan = build_capture_plan(
                self.context.profile,
                self.context.control_backend.name,
                self.groups,
            )
            result = sample_capture_points(
                plan,
                CAPTURE_TIMEOUT_S,
                on_progress=self.progress.emit,
                stop_requested=self._stop_requested.is_set,
            )
            finished = datetime.now(timezone.utc)
            snapshot = MachineStateSnapshot(
                snapshot_id=make_runtime_run_id("snapshot", self.name),
                name=self.name,
                operator_note=self.note,
                machine_id=self.context.profile.machine.id,
                machine_display_name=self.context.profile.machine.display_name,
                backend=self.context.control_backend.name,
                profile_schema_version=self.context.profile.schema_version,
                profile_signature=build_profile_signature(
                    self.context.profile,
                    self.context.control_backend.name,
                ),
                capture_started_at=started.isoformat(),
                capture_finished_at=finished.isoformat(),
                capture_status=result.status,
                hostname=socket.gethostname(),
                consistency="best_effort",
                requested_count=result.requested_count,
                entries=result.entries,
            )
            self.capture_finished.emit(snapshot)
        except Exception as exc:
            self.capture_failed.emit(f"{type(exc).__name__}: {exc}")


class MachineSnapshotWindow(QMainWindow):
    def __init__(self, *, context=None, app_dir: Path | None = None):
        super().__init__()
        self.context = context or load_app_context("machine_snapshot")
        self.app_dir = Path(app_dir or APP_DIR)
        self.current_theme = resolve_initial_theme()
        self.worker: CaptureWorker | None = None
        self.history_items: list[SnapshotHistoryItem] = []
        self.snapshot_a: MachineStateSnapshot | None = None
        self.snapshot_b: MachineStateSnapshot | None = None
        self.diff_rows: tuple[SnapshotDiffRow, ...] = ()
        self._build_ui()
        self._apply_theme()
        self._reload_history()
        install_qt_window_raise_handler(self)

    def _build_ui(self):
        machine = self.context.profile.machine
        backend = self.context.control_backend.name
        self.setWindowTitle(f"{machine.display_name} Machine Snapshot")
        self.resize(1380, 820)
        self.setMinimumSize(980, 620)
        root = QWidget(self)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(10, 9, 10, 9)
        root_layout.setSpacing(8)

        header = QFrame(root)
        header.setObjectName("header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(11, 8, 11, 8)
        title = QLabel("Machine Snapshot", header)
        title.setObjectName("title")
        header_layout.addWidget(title)
        header_layout.addStretch(1)
        self.runtime_context = RuntimeContextWidget(
            machine_id=machine.id,
            machine_display_name=machine.display_name,
            control_backend=backend,
            parent=header,
        )
        header_layout.addWidget(self.runtime_context)
        self.theme_button = QToolButton(header)
        self.theme_button.setFixedSize(31, 31)
        self.theme_button.clicked.connect(self._toggle_theme)
        header_layout.addWidget(self.theme_button)
        root_layout.addWidget(header)

        toolbar = QFrame(root)
        toolbar.setObjectName("toolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(9, 6, 9, 6)
        self.capture_button = QPushButton("Capture Current", toolbar)
        self.capture_button.setObjectName("captureButton")
        self.stop_button = QPushButton("Stop", toolbar)
        self.stop_button.setObjectName("stopButton")
        self.stop_button.setEnabled(False)
        self.open_button = QPushButton("Open External", toolbar)
        self.restore_button = QPushButton("Restore…", toolbar)
        self.restore_button.setEnabled(False)
        self.export_json_button = QPushButton("Export Snapshot JSON", toolbar)
        self.export_csv_button = QPushButton("Export Comparison CSV", toolbar)
        self.export_json_button.setEnabled(False)
        self.export_csv_button.setEnabled(False)
        toolbar_layout.addWidget(self.capture_button)
        toolbar_layout.addWidget(self.stop_button)
        toolbar_layout.addWidget(self.open_button)
        toolbar_layout.addWidget(self.restore_button)
        toolbar_layout.addWidget(self.export_json_button)
        toolbar_layout.addWidget(self.export_csv_button)
        toolbar_layout.addStretch(1)
        self.capture_status = QLabel("Idle", toolbar)
        toolbar_layout.addWidget(self.capture_status)
        root_layout.addWidget(toolbar)

        self.banner = QLabel("", root)
        self.banner.setObjectName("banner")
        self.banner.setWordWrap(True)
        self.banner.hide()
        root_layout.addWidget(self.banner)

        splitter = QSplitter(Qt.Horizontal, root)
        history_panel = QFrame(splitter)
        history_panel.setObjectName("historyPanel")
        history_layout = QVBoxLayout(history_panel)
        history_layout.setContentsMargins(8, 8, 8, 8)
        history_layout.addWidget(QLabel("Recent snapshots", history_panel))
        self.history_list = QListWidget(history_panel)
        history_layout.addWidget(self.history_list, 1)
        history_buttons = QHBoxLayout()
        self.use_a_button = QPushButton("Set Baseline A", history_panel)
        self.use_b_button = QPushButton("Compare as B", history_panel)
        history_buttons.addWidget(self.use_a_button)
        history_buttons.addWidget(self.use_b_button)
        history_layout.addLayout(history_buttons)
        self.history_summary = QLabel("No snapshots", history_panel)
        self.history_summary.setWordWrap(True)
        history_layout.addWidget(self.history_summary)

        content = QWidget(splitter)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        source_row = QHBoxLayout()
        self.source_a_label = QLabel("A: —", content)
        self.source_b_label = QLabel("B: —", content)
        source_row.addWidget(self.source_a_label)
        source_row.addWidget(self.source_b_label)
        source_row.addStretch(1)
        content_layout.addLayout(source_row)

        filters = QHBoxLayout()
        self.search_edit = QLineEdit(content)
        self.search_edit.setPlaceholderText("Filter device, channel, PV, or status")
        self.kind_combo = QComboBox(content)
        self.kind_combo.addItem("All kinds", "")
        self.class_combo = QComboBox(content)
        self.class_combo.addItem("All classes", "")
        self.quality_combo = QComboBox(content)
        self.quality_combo.addItem("All qualities", "")
        self.changed_check = QCheckBox("Changed only", content)
        self.unavailable_check = QCheckBox("Unavailable only", content)
        filters.addWidget(self.search_edit, 1)
        filters.addWidget(self.kind_combo)
        filters.addWidget(self.class_combo)
        filters.addWidget(self.quality_combo)
        filters.addWidget(self.changed_check)
        filters.addWidget(self.unavailable_check)
        content_layout.addLayout(filters)

        self.table = QTableWidget(0, len(TABLE_COLUMNS), content)
        self.table.setHorizontalHeaderLabels(TABLE_COLUMNS)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().setVisible(False)
        header_view = self.table.horizontalHeader()
        for column in (0, 1, 2, 6, 7, 8):
            header_view.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        for column in (3, 4, 5):
            header_view.setSectionResizeMode(column, QHeaderView.Stretch)
        content_layout.addWidget(self.table, 1)

        detail_panel = QFrame(content)
        detail_panel.setObjectName("detailPanel")
        detail_layout = QVBoxLayout(detail_panel)
        detail_layout.setContentsMargins(8, 6, 8, 6)
        detail_layout.addWidget(QLabel("Selected channel details", detail_panel))
        self.detail_text = QTextEdit(detail_panel)
        self.detail_text.setReadOnly(True)
        self.detail_text.setMaximumHeight(105)
        detail_layout.addWidget(self.detail_text)
        content_layout.addWidget(detail_panel)

        splitter.addWidget(history_panel)
        splitter.addWidget(content)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes((300, 1080))
        root_layout.addWidget(splitter, 1)
        self.setCentralWidget(root)

        self.capture_button.clicked.connect(self._start_capture)
        self.stop_button.clicked.connect(self._stop_capture)
        self.open_button.clicked.connect(self._open_external)
        self.restore_button.clicked.connect(self._restore_selected)
        self.export_json_button.clicked.connect(self._export_json)
        self.export_csv_button.clicked.connect(self._export_csv)
        self.use_a_button.clicked.connect(lambda: self._use_history("a"))
        self.use_b_button.clicked.connect(lambda: self._use_history("b"))
        self.history_list.itemDoubleClicked.connect(lambda _item: self._use_history("a"))
        self.search_edit.textChanged.connect(self._apply_filters)
        self.kind_combo.currentIndexChanged.connect(self._apply_filters)
        self.class_combo.currentIndexChanged.connect(self._apply_filters)
        self.quality_combo.currentIndexChanged.connect(self._apply_filters)
        self.changed_check.toggled.connect(self._apply_filters)
        self.unavailable_check.toggled.connect(self._apply_filters)
        self.table.itemSelectionChanged.connect(self._show_selected_detail)

    def _reload_history(self):
        history = list_snapshot_history(self.app_dir, self.context)
        self.history_items = list(history.items)
        self.history_list.clear()
        for item in self.history_items:
            snapshot = item.snapshot
            label = (
                f"{snapshot.name}\n{_local_time(snapshot.capture_finished_at)} · "
                f"{snapshot.capture_status} · {snapshot.ok_count} ok / "
                f"{snapshot.failed_count} failed / {snapshot.skipped_count} skipped"
            )
            list_item = QListWidgetItem(label)
            list_item.setToolTip(str(item.path))
            self.history_list.addItem(list_item)
        summary = f"{len(self.history_items)} snapshot(s)"
        if history.unreadable_count:
            summary += f" · {history.unreadable_count} unreadable"
        self.history_summary.setText(summary)

    def _selected_history(self) -> MachineStateSnapshot | None:
        row = self.history_list.currentRow()
        if row < 0 or row >= len(self.history_items):
            QMessageBox.information(self, "Machine Snapshot", "Select a history item first.")
            return None
        return self.history_items[row].snapshot

    def _use_history(self, slot: str):
        snapshot = self._selected_history()
        if snapshot is not None:
            self._set_snapshot(slot, snapshot)

    def _start_capture(self):
        if self.worker and self.worker.isRunning():
            return
        dialog = CaptureDialog(self)
        if dialog.exec_() != QDialog.Accepted:
            return
        self.worker = CaptureWorker(
            self.context,
            dialog.selected_groups(),
            dialog.snapshot_name(),
            dialog.note_edit.toPlainText().strip(),
            self,
        )
        self.worker.progress.connect(self._capture_progress)
        self.worker.capture_finished.connect(self._capture_completed)
        self.worker.capture_failed.connect(self._capture_failed)
        self._set_capture_running(True)
        self.capture_status.setText("Connecting…")
        self.worker.start()

    def _stop_capture(self):
        if self.worker and self.worker.isRunning():
            self.worker.request_stop()
            self.stop_button.setEnabled(False)
            self.capture_status.setText("Stopping…")

    def _capture_progress(self, _entry, index: int, total: int):
        self.capture_status.setText(f"Reading {index}/{total}")

    def _capture_completed(self, snapshot: MachineStateSnapshot):
        try:
            snapshot, path = save_capture_snapshot(self.app_dir, self.context, snapshot)
        except Exception as exc:
            self._set_capture_running(False)
            QMessageBox.critical(self, "Machine Snapshot", f"Could not save capture: {exc}")
            return
        self._set_capture_running(False)
        self.capture_status.setText(
            f"{snapshot.capture_status.title()} · {snapshot.ok_count} ok · "
            f"{snapshot.failed_count} failed · {snapshot.skipped_count} skipped"
        )
        self.statusBar().showMessage(f"Snapshot saved to {path}", 8000)
        self._reload_history()
        self._set_snapshot("b" if self.snapshot_a is not None else "a", snapshot)

    def _capture_failed(self, detail: str):
        self._set_capture_running(False)
        self.capture_status.setText("Failed")
        QMessageBox.critical(self, "Machine Snapshot", detail)

    def _set_capture_running(self, running: bool):
        self.capture_button.setEnabled(not running)
        self.open_button.setEnabled(not running)
        self.stop_button.setEnabled(running)

    def _open_external(self):
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Open machine snapshot",
            "",
            "Snapshot JSON (*.json);;All files (*)",
        )
        if not filename:
            return
        try:
            snapshot = load_snapshot(filename)
        except MachineStateError as exc:
            QMessageBox.warning(self, "Open machine snapshot", str(exc))
            return
        slot = "a" if self.snapshot_a is None else "b"
        self._set_snapshot(slot, snapshot)

    def _set_snapshot(self, slot: str, snapshot: MachineStateSnapshot):
        previous = self.snapshot_a if slot == "b" else self.snapshot_b
        if previous is not None and previous.machine_id != snapshot.machine_id:
            QMessageBox.warning(
                self,
                "Machine Snapshot",
                "Snapshots from different machines cannot be compared.",
            )
            return
        if slot == "a":
            self.snapshot_a = snapshot
        else:
            self.snapshot_b = snapshot
        self._refresh_comparison()

    def _refresh_comparison(self):
        self.source_a_label.setText(
            "A: —" if self.snapshot_a is None else f"A: {self.snapshot_a.name} [{self.snapshot_a.backend}]"
        )
        self.source_b_label.setText(
            "B: —" if self.snapshot_b is None else f"B: {self.snapshot_b.name} [{self.snapshot_b.backend}]"
        )
        if self.snapshot_a is not None and self.snapshot_b is not None:
            self.diff_rows = compare_snapshots(self.snapshot_a, self.snapshot_b)
        elif self.snapshot_a is not None:
            self.diff_rows = tuple(
                SnapshotDiffRow(
                    entry.key,
                    entry,
                    None,
                    None,
                    DiffStatus.ONLY_IN_A,
                )
                for entry in self.snapshot_a.entries
            )
        elif self.snapshot_b is not None:
            self.diff_rows = tuple(
                SnapshotDiffRow(
                    entry.key,
                    None,
                    entry,
                    None,
                    DiffStatus.ONLY_IN_B,
                )
                for entry in self.snapshot_b.entries
            )
        else:
            self.diff_rows = ()
        self._update_banner()
        self._populate_table()
        selected = self.snapshot_b or self.snapshot_a
        self.export_json_button.setEnabled(selected is not None)
        self.export_csv_button.setEnabled(
            self.snapshot_a is not None and self.snapshot_b is not None
        )
        self.restore_button.setEnabled(selected is not None)

    def _restore_selected(self):
        snapshot = self.snapshot_b or self.snapshot_a
        if snapshot is None:
            QMessageBox.information(self, "Machine Snapshot", "Select a history item first.")
            return
        dialog = RestoreDialog(snapshot, self.context, self)
        if dialog.exec_() != QDialog.Accepted:
            return
        candidates = dialog.selected_candidates()
        if not candidates:
            return
        prompt = f"Restore {len(candidates)} setpoints on {self.context.profile.machine.display_name} ({self.context.control_backend.name.upper()})?"
        if QMessageBox.question(self, "Confirm restore", prompt, QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        self.restore_worker = RestoreWorker(self.context, candidates, self)
        self.restore_worker.finished_result.connect(lambda result: self._restore_finished(result, snapshot))
        self.restore_worker.start()

    def _restore_finished(self, result, snapshot):
        save_restore_result(self.app_dir, self.context, result, snapshot.snapshot_id)
        self.statusBar().showMessage(f"Restore complete: {result.success_count} succeeded, {result.failed_count} failed, {result.skipped_count} skipped", 10000)

    def _update_banner(self):
        messages = []
        if self.snapshot_a and self.snapshot_b:
            if self.snapshot_a.backend != self.snapshot_b.backend:
                messages.append("Different backends: values are shown side by side; Delta is disabled.")
            if self.snapshot_a.profile_signature != self.snapshot_b.profile_signature:
                messages.append("Machine profile mapping changed between captures.")
        observations = any(
            (row.entry_a or row.entry_b).state_class.value == "observation"
            for row in self.diff_rows
            if row.entry_a or row.entry_b
        )
        if observations:
            messages.append("Beam observations are best-effort and are not guaranteed to be from the same shot.")
        self.banner.setText("  ·  ".join(messages))
        self.banner.setVisible(bool(messages))

    def _populate_table(self):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.diff_rows))
        kinds = set()
        classes = set()
        qualities = set()
        colors = PALETTES[self.current_theme]
        for row_index, row in enumerate(self.diff_rows):
            entry = row.entry_a or row.entry_b
            assert entry is not None
            kinds.add(entry.element_kind)
            classes.add(entry.state_class.value)
            quality_text = _quality_pair(row)
            qualities.update(
                item.quality.value for item in (row.entry_a, row.entry_b) if item is not None
            )
            values = (
                entry.display_name,
                entry.logical_channel,
                entry.state_class.value,
                _format_value(row.entry_a),
                _format_value(row.entry_b),
                _format_delta(row.delta),
                entry.unit or "—",
                row.status.value,
                quality_text,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole, row_index)
                if column == 7:
                    color = {
                        DiffStatus.CHANGED: colors["warning"],
                        DiffStatus.SAME: colors["success"],
                        DiffStatus.UNAVAILABLE: colors["danger"],
                        DiffStatus.NOT_COMPARABLE: colors["muted"],
                    }.get(row.status, colors["muted"])
                    item.setForeground(QColor(color))
                self.table.setItem(row_index, column, item)
        self.table.setSortingEnabled(True)
        _replace_combo_values(self.kind_combo, sorted(kinds), "All kinds")
        _replace_combo_values(self.class_combo, sorted(classes), "All classes")
        _replace_combo_values(self.quality_combo, sorted(qualities), "All qualities")
        self._apply_filters()

    def _apply_filters(self, *_args):
        needle = self.search_edit.text().strip().casefold()
        wanted_kind = self.kind_combo.currentData() or ""
        wanted_class = self.class_combo.currentData() or ""
        wanted_quality = self.quality_combo.currentData() or ""
        for visible_row in range(self.table.rowCount()):
            item = self.table.item(visible_row, 0)
            row = self.diff_rows[int(item.data(Qt.UserRole))]
            entry = row.entry_a or row.entry_b
            assert entry is not None
            haystack = " ".join(
                (
                    entry.element_id,
                    entry.display_name,
                    entry.logical_channel,
                    entry.pv_name,
                    row.status.value,
                    row.detail,
                )
            ).casefold()
            row_qualities = {
                side.quality.value for side in (row.entry_a, row.entry_b) if side is not None
            }
            match = (
                (not needle or needle in haystack)
                and (not wanted_kind or entry.element_kind == wanted_kind)
                and (not wanted_class or entry.state_class.value == wanted_class)
                and (not wanted_quality or wanted_quality in row_qualities)
                and (not self.changed_check.isChecked() or row.status == DiffStatus.CHANGED)
                and (
                    not self.unavailable_check.isChecked()
                    or row.status
                    in {
                        DiffStatus.UNAVAILABLE,
                        DiffStatus.ONLY_IN_A,
                        DiffStatus.ONLY_IN_B,
                        DiffStatus.UNIT_MISMATCH,
                        DiffStatus.TYPE_MISMATCH,
                        DiffStatus.NOT_COMPARABLE,
                    }
                )
            )
            self.table.setRowHidden(visible_row, not match)

    def _show_selected_detail(self):
        selected = self.table.selectedItems()
        if not selected:
            self.detail_text.clear()
            return
        row = self.diff_rows[int(selected[0].data(Qt.UserRole))]
        lines = [f"Key: {row.key}", f"Status: {row.status.value}"]
        if row.mapping_changed:
            lines.append("PV mapping changed between captures")
        if row.detail:
            lines.append(f"Detail: {row.detail}")
        for label, entry in (("A", row.entry_a), ("B", row.entry_b)):
            if entry is None:
                lines.append(f"{label}: unavailable")
                continue
            lines.append(
                f"{label}: PV={entry.pv_name} · quality={entry.quality.value} · "
                f"timestamp={entry.source_timestamp if entry.source_timestamp is not None else '—'} · "
                f"alarm={entry.alarm_status}/{entry.alarm_severity}"
            )
            if entry.detail:
                lines.append(f"{label} detail: {entry.detail}")
        self.detail_text.setPlainText("\n".join(lines))

    def _export_json(self):
        snapshot = self.snapshot_b or self.snapshot_a
        if snapshot is None:
            return
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Export machine snapshot",
            f"{snapshot.snapshot_id}.json",
            "Snapshot JSON (*.json)",
        )
        if not filename:
            return
        try:
            export_snapshot_json(Path(filename), snapshot)
        except Exception as exc:
            QMessageBox.critical(self, "Export machine snapshot", str(exc))
            return
        self.statusBar().showMessage(f"Snapshot exported to {filename}", 6000)

    def _export_csv(self):
        if self.snapshot_a is None or self.snapshot_b is None:
            return
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Export snapshot comparison",
            f"{self.snapshot_a.snapshot_id}_vs_{self.snapshot_b.snapshot_id}.csv",
            "CSV files (*.csv)",
        )
        if not filename:
            return
        try:
            export_comparison_csv(
                Path(filename),
                self.snapshot_a,
                self.snapshot_b,
                self.diff_rows,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Export snapshot comparison", str(exc))
            return
        self.statusBar().showMessage(f"Comparison exported to {filename}", 6000)

    def _toggle_theme(self):
        self.current_theme = "light" if self.current_theme == "dark" else "dark"
        self._apply_theme()
        self._populate_table()

    def _apply_theme(self):
        self.setStyleSheet(build_theme(PALETTES[self.current_theme]))
        self.theme_button.setText("☀" if self.current_theme == "dark" else "◐")
        self.theme_button.setToolTip(
            "Switch to light theme" if self.current_theme == "dark" else "Switch to dark theme"
        )

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.request_stop()
            self.worker.wait(int((CAPTURE_TIMEOUT_S + 1.5) * 1000))
        event.accept()


def _replace_combo_values(combo: QComboBox, values: list[str], all_label: str):
    current = combo.currentData() or ""
    combo.blockSignals(True)
    combo.clear()
    combo.addItem(all_label, "")
    for value in values:
        combo.addItem(value, value)
    index = combo.findData(current)
    combo.setCurrentIndex(max(0, index))
    combo.blockSignals(False)


def _format_value(entry) -> str:
    if entry is None or entry.value is None:
        return "—"
    if isinstance(entry.value, float):
        return f"{entry.value:.9g}"
    return str(entry.value)


def _format_delta(delta: float | None) -> str:
    return "—" if delta is None else f"{delta:+.9g}"


def _quality_pair(row: SnapshotDiffRow) -> str:
    quality_a = row.entry_a.quality.value if row.entry_a else "—"
    quality_b = row.entry_b.quality.value if row.entry_b else "—"
    return quality_a if quality_a == quality_b else f"{quality_a} / {quality_b}"


def _local_time(timestamp: str) -> str:
    try:
        return datetime.fromisoformat(timestamp).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return timestamp


def main() -> int:
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
    app = QApplication(sys.argv)
    window = MachineSnapshotWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
