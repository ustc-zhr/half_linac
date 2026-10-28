from __future__ import annotations

try:
    from PyQt5 import QtCore, QtWidgets
except ImportError:  # pragma: no cover - optional runtime dependency
    QtCore = None
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
            self._scalar_objects = [
                entry for entry in self._objects if not self._is_waveform_entry(entry)
            ]
            self._waveform_objects = [
                entry for entry in self._objects if self._is_waveform_entry(entry)
            ]
            self._group_labels = group_labels or {}
            self._current_knob_ids = current_knob_ids or set()
            self._current_object_ids = current_object_ids or set()
            self._tables: dict[str, QtWidgets.QTableWidget] = {}
            self._search_boxes: dict[str, QtWidgets.QLineEdit] = {}
            self._group_boxes: dict[str, QtWidgets.QComboBox] = {}
            self._status_labels: dict[str, QtWidgets.QLabel] = {}
            self._select_visible_buttons: dict[str, QtWidgets.QPushButton] = {}
            self._clear_visible_buttons: dict[str, QtWidgets.QPushButton] = {}
            self._entries = {
                "knob": self._knobs,
                "scalar": self._scalar_objects,
                "waveform": self._waveform_objects,
            }

            layout = QtWidgets.QVBoxLayout(self)

            intro = QtWidgets.QLabel(
                "Load a PV library if needed, then choose control PVs, scalar read PVs, and raw waveforms."
                " Scalar read PVs also include derived control readbacks; raw waveform capture is available"
                " in Monitor mode only.",
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
            self.tabs.addTab(self._build_role_tab("scalar"), "Scalar Read PVs")
            self.tabs.addTab(self._build_role_tab("waveform"), "Waveform PVs · Monitor only")
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
            select_visible_button = QtWidgets.QPushButton("Select Visible", tab)
            select_visible_button.setProperty("role", "diagnostic")
            select_visible_button.setMinimumHeight(40)
            clear_visible_button = QtWidgets.QPushButton("Clear Visible", tab)
            clear_visible_button.setProperty("role", "subtle")
            clear_visible_button.setMinimumHeight(40)
            filters.addWidget(search, 1)
            filters.addWidget(group_box)
            filters.addWidget(select_visible_button)
            filters.addWidget(clear_visible_button)
            filters.addWidget(clear_button)
            layout.addLayout(filters)

            table = QtWidgets.QTableWidget(tab)
            if role == "knob":
                table.setColumnCount(4)
                table.setHorizontalHeaderLabels(["Name", "Write PV", "Readback", "Group"])
                table.setSelectionMode(QtWidgets.QAbstractItemView.MultiSelection)
            elif role == "scalar":
                table.setColumnCount(6)
                table.setHorizontalHeaderLabels(["Name", "Read PV", "Group", "Unit", "Type", "Tags"])
                table.setSelectionMode(QtWidgets.QAbstractItemView.MultiSelection)
            else:
                table.setColumnCount(6)
                table.setHorizontalHeaderLabels(
                    ["Name", "Read PV", "Group", "Unit", "Sample Interval", "Tags"]
                )
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
            self._select_visible_buttons[role] = select_visible_button
            self._clear_visible_buttons[role] = clear_visible_button

            search.textChanged.connect(lambda _text, target=role: self._apply_filters(target))
            group_box.currentIndexChanged.connect(lambda _index, target=role: self._apply_filters(target))
            table.itemSelectionChanged.connect(lambda target=role: self._update_status(target))
            table.doubleClicked.connect(
                lambda index, target=role: self._toggle_row_selection(target, index.row())
            )
            select_visible_button.clicked.connect(
                lambda _checked=False, target=role: self._select_visible(target)
            )
            clear_visible_button.clicked.connect(
                lambda _checked=False, target=role: self._clear_visible(target)
            )
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
                if role in {"scalar", "waveform"} and entry.id in self._current_object_ids:
                    table.selectRow(row)
            table.resizeColumnsToContents()
            self._apply_filters(role)

        def _row_values(self, role: str, entry) -> list[str]:
            group_label = self._group_labels.get(entry.group, entry.group)
            if role == "knob":
                return [entry.name, entry.write_pv, entry.readback_pv, group_label]
            tags = list(getattr(entry, "tags", []))
            if role == "waveform":
                return [
                    entry.name,
                    entry.read_pv,
                    group_label,
                    entry.unit,
                    self._format_sample_interval(entry),
                    ", ".join(tags),
                ]
            type_label = "Derived" if "knob_readback" in tags else "Scalar"
            return [entry.name, entry.read_pv, group_label, entry.unit, type_label, ", ".join(tags)]

        @staticmethod
        def _is_waveform_entry(entry) -> bool:
            capture_mode = str(
                getattr(entry, "capture_mode", "scalar") or "scalar"
            ).strip().lower()
            return capture_mode == "waveform"

        @staticmethod
        def _format_sample_interval(entry) -> str:
            try:
                interval_sec = float(
                    getattr(entry, "waveform_sample_interval_sec", 0.0) or 0.0
                )
            except (TypeError, ValueError):
                return ""
            if interval_sec <= 0.0:
                return ""
            if interval_sec < 1.0e-6:
                return f"{interval_sec * 1.0e9:.6g} ns"
            if interval_sec < 1.0e-3:
                return f"{interval_sec * 1.0e6:.6g} µs"
            if interval_sec < 1.0:
                return f"{interval_sec * 1.0e3:.6g} ms"
            return f"{interval_sec:.6g} s"

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

        def _select_visible(self, role: str) -> None:
            table = self._tables[role]
            selection_model = table.selectionModel()
            model = table.model()
            if selection_model is None or model is None:
                return
            for row in range(table.rowCount()):
                if not table.isRowHidden(row):
                    selection_model.select(
                        model.index(row, 0),
                        QtCore.QItemSelectionModel.Select | QtCore.QItemSelectionModel.Rows,
                    )
            self._update_status(role)

        def _clear_visible(self, role: str) -> None:
            table = self._tables[role]
            selection_model = table.selectionModel()
            model = table.model()
            if selection_model is None or model is None:
                return
            for row in range(table.rowCount()):
                if not table.isRowHidden(row):
                    selection_model.select(
                        model.index(row, 0),
                        QtCore.QItemSelectionModel.Deselect | QtCore.QItemSelectionModel.Rows,
                    )
            self._update_status(role)

        def _toggle_row_selection(self, role: str, row: int) -> None:
            table = self._tables[role]
            selection_model = table.selectionModel()
            model = table.model()
            if selection_model is None or model is None or row < 0:
                return
            selection_model.select(
                model.index(row, 0),
                QtCore.QItemSelectionModel.Toggle | QtCore.QItemSelectionModel.Rows,
            )
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
            selected_ids = {
                entry.id
                for role in ("scalar", "waveform")
                for entry in self.selected_entries(role)
            }
            return [entry.id for entry in self._objects if entry.id in selected_ids]

else:

    class PVSelectorDialog:  # pragma: no cover - fallback for non-GUI tests
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("PyQt5 is required to create PVSelectorDialog")
