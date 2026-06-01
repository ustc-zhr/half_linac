from __future__ import annotations

from pathlib import Path

try:
    from PyQt5 import QtCore, QtWidgets
except ImportError:  # pragma: no cover - optional runtime dependency
    QtCore = None
    QtWidgets = None


if QtWidgets is not None:

    class SetupBrowserDialog(QtWidgets.QDialog):
        def __init__(self, root_dir: str = "", parent=None) -> None:
            super().__init__(parent)
            self._entries: list[dict[str, object]] = []
            self.setWindowTitle("Setup Browser")
            self.resize(920, 540)

            layout = QtWidgets.QVBoxLayout(self)

            controls = QtWidgets.QHBoxLayout()
            controls.addWidget(QtWidgets.QLabel("Setup Root"))
            self.root_edit = QtWidgets.QLineEdit()
            self.root_edit.setReadOnly(True)
            self.browse_button = QtWidgets.QPushButton("Browse...")
            self.refresh_button = QtWidgets.QPushButton("Refresh")
            controls.addWidget(self.root_edit, 1)
            controls.addWidget(self.browse_button)
            controls.addWidget(self.refresh_button)
            layout.addLayout(controls)

            self.summary_label = QtWidgets.QLabel("No setups loaded.")
            self.summary_label.setWordWrap(True)
            layout.addWidget(self.summary_label)

            self.table = QtWidgets.QTableWidget(0, 7, self)
            self.table.setHorizontalHeaderLabels(
                ["Saved", "Task Mode", "Operator", "Read PVs", "Control PVs", "Save Dir", "PV Library"]
            )
            self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
            self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
            self.table.setAlternatingRowColors(True)
            self.table.verticalHeader().setVisible(False)
            self.table.horizontalHeader().setStretchLastSection(True)
            self.table.horizontalHeader().setSectionsClickable(False)
            layout.addWidget(self.table, 1)

            self.detail_label = QtWidgets.QLabel("Select a saved setup to inspect its details.")
            self.detail_label.setWordWrap(True)
            layout.addWidget(self.detail_label)

            button_box = QtWidgets.QDialogButtonBox(self)
            self.open_button = button_box.addButton("Load Setup", QtWidgets.QDialogButtonBox.AcceptRole)
            self.cancel_button = button_box.addButton(QtWidgets.QDialogButtonBox.Cancel)
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

        def set_setups(self, entries: list[dict[str, object]]) -> None:
            self._entries = list(entries)
            self.table.setRowCount(len(self._entries))
            self.table.clearContents()

            for row_index, entry in enumerate(self._entries):
                values = [
                    str(entry.get("saved_at_text", "")),
                    str(entry.get("task_mode", "")),
                    str(entry.get("operator", "")),
                    str(entry.get("object_count", "")),
                    str(entry.get("knob_count", "")),
                    str(entry.get("save_dir", "")),
                    Path(str(entry.get("config_path", ""))).name if str(entry.get("config_path", "")) else "",
                ]
                for col_index, value in enumerate(values):
                    self.table.setItem(row_index, col_index, QtWidgets.QTableWidgetItem(value))

            self.table.resizeColumnsToContents()
            self.summary_label.setText(
                f"{len(self._entries)} setup(s) found in {self.root_dir() or '(no root selected)'}."
            )
            if self._entries:
                blockers = [QtCore.QSignalBlocker(self.table)]
                try:
                    self.table.setCurrentCell(0, 0)
                finally:
                    del blockers
                self._show_row_details(0)
            else:
                self.detail_label.setText("No saved setups found in the current root.")
                self.open_button.setEnabled(False)

        def selected_setup_path(self) -> str | None:
            row_index = self.table.currentRow()
            if row_index < 0 or row_index >= len(self._entries):
                return None
            return str(self._entries[row_index].get("path", "")) or None

        def _accept_if_possible(self) -> None:
            if self.selected_setup_path():
                self.accept()

        def _on_current_cell_changed(self, current_row: int, _current_col: int, _old_row: int, _old_col: int) -> None:
            self._show_row_details(current_row)

        def _show_row_details(self, row_index: int) -> None:
            if row_index < 0 or row_index >= len(self._entries):
                self.detail_label.setText("Select a saved setup to inspect its details.")
                self.open_button.setEnabled(False)
                return

            entry = self._entries[row_index]
            self.open_button.setEnabled(True)
            details = [
                f"Saved: {entry.get('saved_at_text', '')}",
                f"Task Mode: {entry.get('task_mode', '')}",
                f"Operator: {entry.get('operator', '') or '--'}",
                f"Read PVs: {entry.get('object_count', 0)}",
                f"Control PVs: {entry.get('knob_count', 0)}",
                f"Save Dir: {entry.get('save_dir', '') or '--'}",
            ]
            config_path = str(entry.get("config_path", "")).strip()
            if config_path:
                details.append(f"PV Library: {config_path}")
            notes = str(entry.get("notes", "")).strip()
            if notes:
                details.append(f"Notes: {notes}")
            self.detail_label.setText(" | ".join(details))

else:

    class SetupBrowserDialog:  # pragma: no cover - fallback for non-GUI tests
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("PyQt5 is required to create SetupBrowserDialog")
