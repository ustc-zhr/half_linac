from __future__ import annotations

try:
    from PyQt5 import QtWidgets
except ImportError:  # pragma: no cover - optional runtime dependency
    QtWidgets = None


if QtWidgets is not None:

    class PVSelectorDialog(QtWidgets.QDialog):
        def __init__(
            self,
            *,
            knobs,
            objects,
            group_labels: dict[str, str] | None = None,
            current_knob_ids: set[str] | None = None,
            current_object_ids: set[str] | None = None,
            source_label: str = "",
            parent=None,
        ) -> None:
            super().__init__(parent)
            self.setWindowTitle("Choose PVs")
            self.resize(980, 680)

            self._knobs = list(knobs)
            self._objects = list(objects)
            self._group_labels = group_labels or {}
            self._current_knob_ids = current_knob_ids or set()
            self._current_object_ids = current_object_ids or set()
            self._tables: dict[str, QtWidgets.QTableWidget] = {}
            self._search_boxes: dict[str, QtWidgets.QLineEdit] = {}
            self._group_boxes: dict[str, QtWidgets.QComboBox] = {}
            self._status_labels: dict[str, QtWidgets.QLabel] = {}
            self._entries = {
                "knob": self._knobs,
                "object": self._objects,
            }

            layout = QtWidgets.QVBoxLayout(self)

            intro = QtWidgets.QLabel(
                "Load a PV library if needed, then choose one or more control PVs and one or more read PVs."
                " Read PVs also include derived control readbacks when a knob defines readback_pv.",
                self,
            )
            intro.setWordWrap(True)
            layout.addWidget(intro)

            if source_label:
                source = QtWidgets.QLabel(f"PV Library: {source_label}", self)
                source.setWordWrap(True)
                layout.addWidget(source)

            self.tabs = QtWidgets.QTabWidget(self)
            self.tabs.addTab(self._build_role_tab("knob"), "Control PVs")
            self.tabs.addTab(self._build_role_tab("object"), "Read PVs")
            layout.addWidget(self.tabs)

            button_box = QtWidgets.QDialogButtonBox(
                QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel,
                self,
            )
            ok_button = button_box.button(QtWidgets.QDialogButtonBox.Ok)
            cancel_button = button_box.button(QtWidgets.QDialogButtonBox.Cancel)
            if ok_button is not None:
                ok_button.setProperty("role", "control")
                ok_button.setMinimumHeight(40)
            if cancel_button is not None:
                cancel_button.setProperty("role", "subtle")
                cancel_button.setMinimumHeight(40)
            button_box.accepted.connect(self.accept)
            button_box.rejected.connect(self.reject)
            layout.addWidget(button_box)

        def _build_role_tab(self, role: str):
            tab = QtWidgets.QWidget(self)
            layout = QtWidgets.QVBoxLayout(tab)

            filters = QtWidgets.QHBoxLayout()
            search = QtWidgets.QLineEdit(tab)
            search.setPlaceholderText("Search name, PV, group, or tags")
            group_box = QtWidgets.QComboBox(tab)
            group_box.addItem("All Groups", "")
            for group_id in self._group_ids(role):
                group_box.addItem(self._group_labels.get(group_id, group_id), group_id)
            clear_button = QtWidgets.QPushButton("Clear Selection", tab)
            clear_button.setProperty("role", "danger")
            clear_button.setMinimumHeight(40)
            filters.addWidget(search, 1)
            filters.addWidget(group_box)
            filters.addWidget(clear_button)
            layout.addLayout(filters)

            table = QtWidgets.QTableWidget(tab)
            if role == "knob":
                table.setColumnCount(4)
                table.setHorizontalHeaderLabels(["Name", "Write PV", "Readback", "Group"])
                table.setSelectionMode(QtWidgets.QAbstractItemView.MultiSelection)
            else:
                table.setColumnCount(5)
                table.setHorizontalHeaderLabels(["Name", "Read PV", "Group", "Unit", "Tags"])
                table.setSelectionMode(QtWidgets.QAbstractItemView.MultiSelection)
            table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
            table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            table.setAlternatingRowColors(True)
            table.verticalHeader().setVisible(False)
            table.horizontalHeader().setStretchLastSection(True)
            layout.addWidget(table)

            status = QtWidgets.QLabel(tab)
            layout.addWidget(status)

            self._search_boxes[role] = search
            self._group_boxes[role] = group_box
            self._tables[role] = table
            self._status_labels[role] = status

            search.textChanged.connect(lambda _text, target=role: self._apply_filters(target))
            group_box.currentIndexChanged.connect(lambda _index, target=role: self._apply_filters(target))
            table.itemSelectionChanged.connect(lambda target=role: self._update_status(target))
            clear_button.clicked.connect(lambda _checked=False, target=role: self._clear_selection(target))

            self._populate_table(role)
            return tab

        def _group_ids(self, role: str) -> list[str]:
            return sorted({entry.group for entry in self._entries[role]})

        def _populate_table(self, role: str) -> None:
            table = self._tables[role]
            entries = self._entries[role]
            table.setRowCount(len(entries))
            for row, entry in enumerate(entries):
                values = self._row_values(role, entry)
                for col, value in enumerate(values):
                    table.setItem(row, col, QtWidgets.QTableWidgetItem(str(value)))
                if role == "knob" and entry.id in self._current_knob_ids:
                    table.selectRow(row)
                if role == "object" and entry.id in self._current_object_ids:
                    table.selectRow(row)
            table.resizeColumnsToContents()
            self._apply_filters(role)

        def _row_values(self, role: str, entry) -> list[str]:
            group_label = self._group_labels.get(entry.group, entry.group)
            if role == "knob":
                return [entry.name, entry.write_pv, entry.readback_pv, group_label]
            return [entry.name, entry.read_pv, group_label, entry.unit, ", ".join(entry.tags)]

        def _entry_search_text(self, role: str, entry) -> str:
            values = self._row_values(role, entry)
            parts = [*values, entry.group, entry.id]
            return " ".join(str(part).strip().lower() for part in parts if str(part).strip())

        def _apply_filters(self, role: str) -> None:
            table = self._tables[role]
            search_text = self._search_boxes[role].text().strip().lower()
            group_id = str(self._group_boxes[role].currentData() or "").strip()
            entries = self._entries[role]

            for row, entry in enumerate(entries):
                matches_search = not search_text or search_text in self._entry_search_text(role, entry)
                matches_group = not group_id or entry.group == group_id
                table.setRowHidden(row, not (matches_search and matches_group))

            self._update_status(role)

        def _update_status(self, role: str) -> None:
            table = self._tables[role]
            total = table.rowCount()
            visible = sum(not table.isRowHidden(row) for row in range(total))
            selected = len(self.selected_entries(role))
            self._status_labels[role].setText(
                f"Visible: {visible}/{total}    Selected: {selected}"
            )

        def _clear_selection(self, role: str) -> None:
            table = self._tables[role]
            table.clearSelection()
            self._update_status(role)

        def selected_entries(self, role: str):
            table = self._tables[role]
            model = table.selectionModel()
            if model is None:
                return []
            rows = sorted({index.row() for index in model.selectedRows()})
            entries = self._entries[role]
            return [entries[row] for row in rows if 0 <= row < len(entries)]

        def selected_knob_ids(self) -> list[str]:
            return [entry.id for entry in self.selected_entries("knob")]

        def selected_object_ids(self) -> list[str]:
            return [entry.id for entry in self.selected_entries("object")]

else:

    class PVSelectorDialog:  # pragma: no cover - fallback for non-GUI tests
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("PyQt5 is required to create PVSelectorDialog")
