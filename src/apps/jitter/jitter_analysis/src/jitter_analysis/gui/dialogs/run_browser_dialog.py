from __future__ import annotations

from pathlib import Path

try:
    from PyQt5 import QtCore, QtWidgets
except ImportError:  # pragma: no cover - optional runtime dependency
    QtCore = None
    QtWidgets = None


if QtWidgets is not None:

    class RunBrowserDialog(QtWidgets.QDialog):
        def __init__(self, root_dir: str = "", parent=None) -> None:
            super().__init__(parent)
            self._entries: list[dict[str, object]] = []
            self._scope_kind = "root"
            self.setWindowTitle("Run Browser")
            self.resize(940, 560)

            layout = QtWidgets.QVBoxLayout(self)

            controls = QtWidgets.QHBoxLayout()
            controls.addWidget(QtWidgets.QLabel("Run Root"))
            self.root_edit = QtWidgets.QLineEdit()
            self.root_edit.setReadOnly(True)
            self.browse_button = QtWidgets.QPushButton("Browse...")
            self.refresh_button = QtWidgets.QPushButton("Refresh")
            self.browse_button.setProperty("role", "diagnostic")
            self.refresh_button.setProperty("role", "diagnostic")
            self.browse_button.setMinimumHeight(40)
            self.refresh_button.setMinimumHeight(40)
            controls.addWidget(self.root_edit, 1)
            controls.addWidget(self.browse_button)
            controls.addWidget(self.refresh_button)
            layout.addLayout(controls)

            self.scope_label = QtWidgets.QLabel("Scope: Run Root")
            self.scope_label.setProperty("role", "statusPill")
            layout.addWidget(self.scope_label)

            self.summary_label = QtWidgets.QLabel("No runs loaded.")
            self.summary_label.setWordWrap(True)
            layout.addWidget(self.summary_label)

            self.table = QtWidgets.QTableWidget(0, 8, self)
            self.table.setHorizontalHeaderLabels(
                ["Run ID", "Created", "Mode", "Status", "Operator", "Samples", "Steps", "Machine"]
            )
            self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
            self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
            self.table.setAlternatingRowColors(True)
            self.table.verticalHeader().setVisible(False)
            self.table.horizontalHeader().setStretchLastSection(True)
            self.table.horizontalHeader().setSectionsClickable(False)
            layout.addWidget(self.table, 1)

            self.detail_label = QtWidgets.QLabel("Select a saved run to inspect its details.")
            self.detail_label.setWordWrap(True)
            layout.addWidget(self.detail_label)

            button_box = QtWidgets.QDialogButtonBox(self)
            self.open_button = button_box.addButton("Open Run", QtWidgets.QDialogButtonBox.AcceptRole)
            self.cancel_button = button_box.addButton(QtWidgets.QDialogButtonBox.Cancel)
            self.open_button.setProperty("role", "control")
            self.cancel_button.setProperty("role", "subtle")
            self.open_button.setMinimumHeight(40)
            self.cancel_button.setMinimumHeight(40)
            self.open_button.setEnabled(False)
            layout.addWidget(button_box)

            self.table.currentCellChanged.connect(self._on_current_cell_changed)
            self.table.itemDoubleClicked.connect(lambda _item: self._accept_if_possible())
            self.open_button.clicked.connect(self._accept_if_possible)
            self.cancel_button.clicked.connect(self.reject)

            if root_dir:
                self.set_root_dir(root_dir)

        def set_root_dir(self, root_dir: str | Path) -> None:
            self.root_edit.setText(str(Path(root_dir)))

        def root_dir(self) -> str:
            return self.root_edit.text().strip()

        def set_scope_kind(self, scope_kind: str) -> None:
            token = str(scope_kind or "root").strip().lower()
            self._scope_kind = "single_run" if token == "single_run" else "root"
            scope_text = "Single Run Directory" if self._scope_kind == "single_run" else "Run Root"
            self.scope_label.setText(f"Scope: {scope_text}")

        def set_runs(self, entries: list[dict[str, object]], scope_kind: str = "root") -> None:
            self.set_scope_kind(scope_kind)
            self._entries = list(entries)
            self.table.setRowCount(len(self._entries))
            self.table.clearContents()

            for row_index, entry in enumerate(self._entries):
                values = [
                    str(entry.get("run_id", "")),
                    str(entry.get("created_at_text", "")),
                    self._mode_label(str(entry.get("mode", ""))),
                    self._status_label(str(entry.get("status", ""))),
                    str(entry.get("operator", "")),
                    str(entry.get("sample_count", "")),
                    str(entry.get("step_count", "")),
                    str(entry.get("machine", "")),
                ]
                for col_index, value in enumerate(values):
                    self.table.setItem(row_index, col_index, QtWidgets.QTableWidgetItem(value))
            self.table.resizeColumnsToContents()
            scope_text = "single run directory" if self._scope_kind == "single_run" else "run root"
            openable_count = sum(1 for entry in self._entries if self._entry_has_raw(entry))
            summary = (
                f"{len(self._entries)} run(s) found in {self.root_dir() or '(no root selected)'} "
                f"using {scope_text} mode."
            )
            if openable_count != len(self._entries):
                summary += f" {openable_count} run(s) can be opened for offline analysis."
            self.summary_label.setText(summary)
            if self._entries:
                blockers = [QtCore.QSignalBlocker(self.table)]
                try:
                    self.table.setCurrentCell(0, 0)
                finally:
                    del blockers
                self._show_row_details(0)
            else:
                if self._scope_kind == "single_run":
                    self.detail_label.setText("No saved run was found in the selected run directory.")
                else:
                    self.detail_label.setText("No saved runs were found under the selected run root.")
                self.open_button.setEnabled(False)

        def selected_run_path(self) -> str | None:
            row_index = self.table.currentRow()
            if row_index < 0 or row_index >= len(self._entries):
                return None
            if not self._entry_has_raw(self._entries[row_index]):
                return None
            return str(self._entries[row_index].get("path", "")) or None

        def _accept_if_possible(self) -> None:
            if self.selected_run_path():
                self.accept()

        def _on_current_cell_changed(self, current_row: int, _current_col: int, _old_row: int, _old_col: int) -> None:
            self._show_row_details(current_row)

        def _show_row_details(self, row_index: int) -> None:
            if row_index < 0 or row_index >= len(self._entries):
                self.detail_label.setText("Select a saved run to inspect its details.")
                self.open_button.setEnabled(False)
                return

            entry = self._entries[row_index]
            has_raw = self._entry_has_raw(entry)
            self.open_button.setEnabled(has_raw)
            details = [
                f"Run ID: {entry.get('run_id', '')}",
                f"Status: {self._status_label(str(entry.get('status', '')))}",
                f"Mode: {self._mode_label(str(entry.get('mode', '')))}",
                f"Operator: {entry.get('operator', '') or '--'}",
                f"Samples: {entry.get('sample_count', 0)}",
                f"Steps: {entry.get('step_count', 0)}",
                f"Raw HDF5: {'yes' if entry.get('has_raw') else 'no'}",
            ]
            notes = str(entry.get("notes", "")).strip()
            if notes:
                details.append(f"Notes: {notes}")
            config_path = str(entry.get("config_path", "")).strip()
            if config_path:
                details.append(f"PV Library: {config_path}")
            if not has_raw:
                details.append("This entry cannot be opened because raw.h5 is missing.")
            self.detail_label.setText(" | ".join(details))

        @staticmethod
        def _entry_has_raw(entry: dict[str, object]) -> bool:
            return bool(entry.get("has_raw"))

        @staticmethod
        def _mode_label(mode: str) -> str:
            labels = {
                "timed_acquisition": "Monitor",
                "knob_scan": "Single Knob",
                "single_knob_scan": "Single Knob",
                "multi_knob_random": "Random Multi-Knob",
            }
            token = str(mode or "").strip()
            return labels.get(token, token.replace("_", " ").title() if token else "--")

        @staticmethod
        def _status_label(status: str) -> str:
            token = str(status or "").strip()
            return token.replace("_", " ").title() if token else "--"

else:

    class RunBrowserDialog:  # pragma: no cover - fallback for non-GUI tests
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("PyQt5 is required to create RunBrowserDialog")
