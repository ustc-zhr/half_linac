from __future__ import annotations

import json
from datetime import datetime
from typing import Callable

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTableWidgetSelectionRange,
    QVBoxLayout,
    QWidget,
)

try:
    from .ui_dialog_algorithm_detail import Ui_AlgorithmDetailDialog
    from .ui_dialog_bounds_tools import Ui_BoundsToolsDialog
    from .ui_dialog_pv_library_selector import Ui_PVLibrarySelectorDialog
    from .ui_dialog_policy_editor import Ui_PolicyEditorDialog
    from .ui_dialog_pv_monitor import Ui_PVMonitorDialog
except ImportError:  # pragma: no cover
    from ui_dialog_algorithm_detail import Ui_AlgorithmDetailDialog
    from ui_dialog_bounds_tools import Ui_BoundsToolsDialog
    from ui_dialog_pv_library_selector import Ui_PVLibrarySelectorDialog
    from ui_dialog_policy_editor import Ui_PolicyEditorDialog
    from ui_dialog_pv_monitor import Ui_PVMonitorDialog

try:
    from ..services.pv_library import PVLibraryItem
    from ..services.task_service import TaskService
except ImportError:  # pragma: no cover
    from pv_library import PVLibraryItem
    from task_service import TaskService


class PVLibrarySelectorDialog(QDialog):
    def __init__(
        self,
        entries: list[PVLibraryItem],
        *,
        title: str,
        intro_text: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.ui = Ui_PVLibrarySelectorDialog()
        self.ui.setupUi(self)
        self.setWindowTitle(title)
        self.ui.label_intro.setText(intro_text)

        self._all_entries = list(entries)
        self._visible_entries = list(entries)

        table = self.ui.tableWidget_library
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.MultiSelection)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        header = table.horizontalHeader()
        header.setStretchLastSection(True)
        for idx in range(table.columnCount() - 1):
            header.setSectionResizeMode(idx, header.Stretch)

        self.ui.lineEdit_filter.textChanged.connect(self._refresh_rows)
        self.ui.buttonBox.accepted.connect(self._accept_if_any)
        self.ui.buttonBox.rejected.connect(self.reject)

        self._refresh_rows()

    def _refresh_rows(self) -> None:
        query = self.ui.lineEdit_filter.text().strip().lower()

        def matches(entry: PVLibraryItem) -> bool:
            if not query:
                return True
            haystack = "\n".join(
                [
                    entry.name.lower(),
                    entry.pv_name.lower(),
                    entry.readback.lower(),
                    entry.group.lower(),
                    entry.note.lower(),
                ]
            )
            return query in haystack

        self._visible_entries = [entry for entry in self._all_entries if matches(entry)]
        table = self.ui.tableWidget_library
        table.setRowCount(len(self._visible_entries))
        for row, entry in enumerate(self._visible_entries):
            values = [entry.name, entry.pv_name, entry.readback, entry.group, entry.note]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                table.setItem(row, col, item)
        self.ui.label_summary.setText(
            f"Showing {len(self._visible_entries)} of {len(self._all_entries)} available PV rows."
        )

    def _accept_if_any(self) -> None:
        if not self.selected_entries():
            QMessageBox.information(self, self.windowTitle(), "Select at least one PV row first.")
            return
        self.accept()

    def selected_entries(self) -> list[PVLibraryItem]:
        selection_model = self.ui.tableWidget_library.selectionModel()
        if selection_model is None:
            return []
        rows = sorted({index.row() for index in selection_model.selectedRows()})
        return [self._visible_entries[row] for row in rows if 0 <= row < len(self._visible_entries)]


class PVMappingSelectorDialog(QDialog):
    ROLE_TITLES = {
        "knob": "Knobs",
        "objective": "Objectives",
        "constraint": "Constraints",
    }

    def __init__(
        self,
        *,
        knob_entries: list[PVLibraryItem],
        objective_entries: list[PVLibraryItem],
        constraint_entries: list[PVLibraryItem],
        current_keys: dict[str, set[str]] | None = None,
        source_label: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select PV Mapping")
        self.resize(980, 660)

        self._entries = {
            "knob": list(knob_entries),
            "objective": list(objective_entries),
            "constraint": list(constraint_entries),
        }
        self._current_keys = current_keys or {}
        self._tables: dict[str, QTableWidget] = {}

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Select PV rows for each role, then apply them into the Machine PV Mapping table. "
            "Leaving a role empty clears that role from the mapping.",
            self,
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        if source_label:
            source = QLabel(f"Library: {source_label}", self)
            source.setWordWrap(True)
            layout.addWidget(source)

        tabs = QTabWidget(self)
        for role in ("knob", "objective", "constraint"):
            tabs.addTab(self._build_role_tab(role), self.ROLE_TITLES[role])
        layout.addWidget(tabs)

        self.buttonBox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        self.buttonBox.accepted.connect(self._accept_with_confirmation)
        self.buttonBox.rejected.connect(self.reject)
        layout.addWidget(self.buttonBox)

    def _build_role_tab(self, role: str) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        table = QTableWidget(tab)
        table.setColumnCount(5)
        table.setHorizontalHeaderLabels(["Name", "PV Name", "Readback", "Group", "Note"])
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.MultiSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table)
        self._tables[role] = table
        self._populate_table(role)
        return tab

    def _populate_table(self, role: str) -> None:
        table = self._tables[role]
        entries = self._entries[role]
        table.setRowCount(len(entries))
        current_keys = self._current_keys.get(role, set())
        for row, entry in enumerate(entries):
            values = [entry.name, entry.pv_name, entry.readback, entry.group, entry.note]
            for col, value in enumerate(values):
                table.setItem(row, col, QTableWidgetItem(str(value)))
            if self._entry_matches_current(entry, current_keys):
                table.setRangeSelected(
                    QTableWidgetSelectionRange(row, 0, row, table.columnCount() - 1),
                    True,
                )
        table.resizeColumnsToContents()

    @staticmethod
    def _entry_matches_current(entry: PVLibraryItem, current_keys: set[str]) -> bool:
        return (
            str(entry.name).strip().lower() in current_keys
            or str(entry.pv_name).strip().lower() in current_keys
        )

    def selected_entries(self, role: str) -> list[PVLibraryItem]:
        table = self._tables.get(role)
        if table is None or table.selectionModel() is None:
            return []
        entries = self._entries.get(role, [])
        rows = sorted({index.row() for index in table.selectionModel().selectedRows()})
        return [entries[row] for row in rows if 0 <= row < len(entries)]

    def selected_entries_by_role(self) -> dict[str, list[PVLibraryItem]]:
        return {
            role: self.selected_entries(role)
            for role in ("knob", "objective", "constraint")
        }

    def _accept_with_confirmation(self) -> None:
        selected = self.selected_entries_by_role()
        if any(selected.values()):
            self.accept()
            return
        answer = QMessageBox.question(
            self,
            self.windowTitle(),
            "No PV rows are selected. Clear all PV Mapping roles?",
        )
        if answer == QMessageBox.Yes:
            self.accept()


class BoundsToolsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.ui = Ui_BoundsToolsDialog()
        self.ui.setupUi(self)
        self.ui.buttonBox.rejected.connect(self.reject)


class AlgorithmDetailDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.ui = Ui_AlgorithmDetailDialog()
        self.ui.setupUi(self)
        self.ui.buttonBox.accepted.connect(self.accept)
        self.ui.buttonBox.rejected.connect(self.reject)


class PVMonitorDialog(QDialog):
    def __init__(
        self,
        task_provider: Callable[[], dict],
        *,
        timeout_provider: Callable[[], float] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.ui = Ui_PVMonitorDialog()
        self.ui.setupUi(self)

        self._task_provider = task_provider
        self._timeout_provider = timeout_provider or (lambda: 1.0)
        self._rows: list[dict[str, str]] = []

        self.ui.buttonBox.rejected.connect(self.reject)
        self.ui.pushButton_refresh.clicked.connect(self.refresh_rows)
        self.ui.pushButton_readSelected.clicked.connect(self.read_selected)
        self.ui.pushButton_readAll.clicked.connect(self.read_all)

        self.refresh_rows()

    def _append_log(self, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.ui.plainTextEdit_log.appendPlainText(f"[{ts}] {message}")

    def _set_status(self, text: str) -> None:
        self.ui.label_status.setText(text)

    def refresh_rows(self) -> None:
        task = self._task_provider()
        rows = TaskService.extract_machine_pvs(task)
        self._rows = rows

        table = self.ui.tableWidget_pvs
        table.setRowCount(len(rows))
        for row_idx, row in enumerate(rows):
            values = [
                row.get("role", ""),
                row.get("name", ""),
                row.get("pvname", ""),
                "--",
                "Idle",
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if col in {0, 1, 4}:
                    item.setTextAlignment(Qt.AlignCenter)
                table.setItem(row_idx, col, item)

        if not rows:
            self._set_status("No online EPICS PVs are configured in the current task.")
        else:
            self._set_status(f"Loaded {len(rows)} configured PVs from the current task.")
        self._append_log("PV list refreshed.")

    def _read_indices(self, indices: list[int]) -> None:
        if not indices:
            QMessageBox.information(self, "PV Monitor", "Select at least one PV row first.")
            return
        if not self._rows:
            QMessageBox.information(self, "PV Monitor", "No PVs are configured for the current task.")
            return

        try:
            from epics import caget
        except ImportError as exc:
            self._set_status("pyepics is not installed or not available in this environment.")
            QMessageBox.critical(self, "PV Monitor", str(exc))
            return

        timeout = float(self._timeout_provider())
        table = self.ui.tableWidget_pvs
        success = 0
        for idx in indices:
            row = self._rows[idx]
            pvname = row["pvname"]
            try:
                value = caget(pvname, timeout=timeout)
                status = "OK" if value is not None else "No Data"
                if value is not None:
                    success += 1
            except Exception as exc:  # pragma: no cover - runtime read protection
                value = str(exc)
                status = "Error"
            table.setItem(idx, 3, QTableWidgetItem(str(value)))
            status_item = QTableWidgetItem(status)
            status_item.setTextAlignment(Qt.AlignCenter)
            table.setItem(idx, 4, status_item)
            self._append_log(f"{pvname} -> {value} ({status})")

        self._set_status(f"Read {success}/{len(indices)} PVs successfully.")

    def read_selected(self) -> None:
        row = self.ui.tableWidget_pvs.currentRow()
        if row < 0:
            QMessageBox.information(self, "PV Monitor", "Select one PV row first.")
            return
        self._read_indices([row])

    def read_all(self) -> None:
        self._read_indices(list(range(len(self._rows))))


class PolicyEditorDialog(QDialog):
    def __init__(self, policy_state: dict, parent=None) -> None:
        super().__init__(parent)
        self.ui = Ui_PolicyEditorDialog()
        self.ui.setupUi(self)

        self.ui.buttonBox.accepted.connect(self._accept_if_valid)
        self.ui.buttonBox.rejected.connect(self.reject)
        self.ui.comboBox_writePolicy.currentTextChanged.connect(self._refresh_preview)
        self.ui.comboBox_objectivePolicy.currentTextChanged.connect(self._refresh_preview)
        self.ui.spinBox_targetCol.valueChanged.connect(self._refresh_preview)
        self.ui.plainTextEdit_kwargs.textChanged.connect(self._refresh_preview)

        self.ui.comboBox_writePolicy.setCurrentText(str(policy_state.get("write_policy", "none")))
        self.ui.comboBox_objectivePolicy.setCurrentText(str(policy_state.get("objective_policy", "none")))
        self.ui.spinBox_targetCol.setValue(int(policy_state.get("target_col", 0)))
        kwargs_text = str(policy_state.get("policy_kwargs_text", "{}") or "{}")
        self.ui.plainTextEdit_kwargs.setPlainText(kwargs_text)
        self._refresh_preview()

    def _normalized_state(self) -> dict:
        kwargs_text = self.ui.plainTextEdit_kwargs.toPlainText().strip() or "{}"
        kwargs = TaskService._parse_json_text(kwargs_text)
        kwargs.setdefault("target_col", int(self.ui.spinBox_targetCol.value()))
        return {
            "write_policy": self.ui.comboBox_writePolicy.currentText(),
            "objective_policy": self.ui.comboBox_objectivePolicy.currentText(),
            "target_col": int(self.ui.spinBox_targetCol.value()),
            "policy_kwargs_text": json.dumps(kwargs, indent=2, ensure_ascii=False),
        }

    def _refresh_preview(self) -> None:
        try:
            state = self._normalized_state()
        except Exception as exc:
            self.ui.plainTextEdit_preview.setPlainText(f"Invalid JSON:\n{exc}")
            return
        preview = [
            f"Write Policy: {state['write_policy']}",
            f"Objective Policy: {state['objective_policy']}",
            f"Target Column: {state['target_col']}",
            "",
            "Normalized Objective Policy Kwargs:",
            state["policy_kwargs_text"],
        ]
        self.ui.plainTextEdit_preview.setPlainText("\n".join(preview))

    def _accept_if_valid(self) -> None:
        try:
            self._normalized_state()
        except Exception as exc:
            QMessageBox.critical(self, "Policy Editor", str(exc))
            return
        self.accept()

    def policy_state(self) -> dict:
        return self._normalized_state()
