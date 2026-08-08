from __future__ import annotations

from copy import deepcopy
import math
import re

try:
    from PyQt5 import QtCore, QtWidgets
except ImportError:  # pragma: no cover - optional runtime dependency
    QtCore = None
    QtWidgets = None


def _default_object() -> dict[str, object]:
    return {
        "id": "",
        "name": "",
        "group": "user",
        "read_pv": "",
        "unit": "",
        "precision": 6,
        "kind": "scalar",
        "access": "ro",
        "analysis": {"jitter": True, "correlation": True, "spectrum": True},
        "value_reducer": "none",
        "capture_mode": "scalar",
        "tags": [],
        "note": "",
    }


if QtWidgets is not None:

    class PVGroupEditorDialog(QtWidgets.QDialog):
        def __init__(self, *, existing_groups, parent=None) -> None:
            super().__init__(parent)
            self.setWindowTitle("Add Group")
            self.setMinimumWidth(440)
            self._existing_groups = list(existing_groups)
            self._id_was_edited = False
            self._color = "#607d8b"

            layout = QtWidgets.QVBoxLayout(self)
            form = QtWidgets.QFormLayout()
            self.name_edit = QtWidgets.QLineEdit()
            self.id_edit = QtWidgets.QLineEdit()
            self.color_button = QtWidgets.QPushButton()
            self.color_button.setFixedSize(52, 32)
            self.color_button.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
            self.color_button.setToolTip("Choose group color")
            self._update_color_swatch()
            form.addRow("Name", self.name_edit)
            form.addRow("ID", self.id_edit)
            form.addRow("Color", self.color_button)
            layout.addLayout(form)

            buttons = QtWidgets.QDialogButtonBox(
                QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel, self
            )
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)

            self.name_edit.textChanged.connect(self._sync_generated_id)
            self.id_edit.textEdited.connect(self._mark_id_edited)
            self.color_button.clicked.connect(self._choose_color)

        @staticmethod
        def _normalize_group_id(text: str) -> str:
            value = re.sub(r"[^a-z0-9]+", "_", text.strip().lower())
            return value.strip("_")

        def _sync_generated_id(self, text: str) -> None:
            if not self._id_was_edited:
                self.id_edit.setText(self._normalize_group_id(text))

        def _mark_id_edited(self) -> None:
            self._id_was_edited = True

        def _choose_color(self) -> None:
            color = QtWidgets.QColorDialog.getColor(parent=self)
            if color.isValid():
                self._color = color.name()
                self._update_color_swatch()

        def _update_color_swatch(self) -> None:
            self.color_button.setStyleSheet(
                f"background-color: {self._color}; border: 1px solid #71808a; "
                "min-height: 0px; max-height: 32px; padding: 0px; border-radius: 6px;"
            )

        def accept(self) -> None:
            name = self.name_edit.text().strip()
            group_id = self._normalize_group_id(self.id_edit.text())
            if not name or not group_id:
                QtWidgets.QMessageBox.warning(self, "Invalid Group", "Name and ID are required.")
                return
            existing_ids = {str(group.get("id", "")).lower() for group in self._existing_groups}
            if group_id.lower() in existing_ids:
                QtWidgets.QMessageBox.warning(self, "Duplicate Group", f"Group ID already exists: {group_id}")
                return
            existing_labels = {str(group.get("label", "")).strip().lower() for group in self._existing_groups}
            if name.lower() in existing_labels:
                QtWidgets.QMessageBox.warning(self, "Duplicate Group", f"Group name already exists: {name}")
                return
            self.id_edit.setText(group_id)
            super().accept()

        def group_data(self) -> dict[str, object]:
            orders = [int(group.get("order", 0)) for group in self._existing_groups]
            next_order = (max(orders) + 10) if orders else 10
            return {
                "id": self.id_edit.text().strip(),
                "label": self.name_edit.text().strip(),
                "kind": "object",
                "color": self._color,
                "order": next_order,
            }

    class PVObjectEditorDialog(QtWidgets.QDialog):
        def __init__(
            self,
            *,
            object_data=None,
            group_labels=None,
            add_group_callback=None,
            parent=None,
        ) -> None:
            super().__init__(parent)
            self.setWindowTitle("Add PV" if object_data is None else "Edit PV")
            self.resize(560, 700)
            self._object_data = deepcopy(object_data or _default_object())
            self._group_labels = group_labels or {}
            self._add_group_callback = add_group_callback

            layout = QtWidgets.QVBoxLayout(self)
            form = QtWidgets.QFormLayout()
            self.id_edit = QtWidgets.QLineEdit(str(self._object_data.get("id", "")))
            self.name_edit = QtWidgets.QLineEdit(str(self._object_data.get("name", "")))
            self.read_pv_edit = QtWidgets.QLineEdit(str(self._object_data.get("read_pv", "")))
            self.unit_edit = QtWidgets.QLineEdit(str(self._object_data.get("unit", "")))
            self.precision_spin = QtWidgets.QSpinBox()
            self.precision_spin.setRange(0, 15)
            self.precision_spin.setValue(int(self._object_data.get("precision", 6)))
            self.group_box = QtWidgets.QComboBox()
            self.group_box.setEditable(True)
            self.group_box.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
            self.group_box.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
            completer = self.group_box.completer()
            if completer is not None:
                completer.setCompletionMode(QtWidgets.QCompleter.PopupCompletion)
                completer.setFilterMode(QtCore.Qt.MatchContains)
                completer.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
            for group_id, label in self._group_labels.items():
                self.group_box.addItem(label, group_id)
            current_group = str(self._object_data.get("group", "user"))
            if object_data is None and self.group_box.findData(current_group) < 0 and self.group_box.count():
                current_group = str(self.group_box.itemData(0))
            elif object_data is not None and self.group_box.findData(current_group) < 0:
                self.group_box.addItem(current_group, current_group)
            current_group_index = self.group_box.findData(current_group)
            self.group_box.setCurrentIndex(current_group_index if current_group_index >= 0 else -1)
            if current_group_index >= 0:
                self.group_box.lineEdit().setText(self._group_labels.get(current_group, current_group))
            self.group_box.setFixedHeight(32)
            self.group_dropdown_button = QtWidgets.QToolButton()
            self.group_dropdown_button.setArrowType(QtCore.Qt.DownArrow)
            self.group_dropdown_button.setFixedSize(32, 32)
            self.group_dropdown_button.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
            self.group_dropdown_button.setProperty("groupActionControl", "true")
            self.group_dropdown_button.setToolTip("Choose existing group")
            self.add_group_button = QtWidgets.QToolButton()
            self.add_group_button.setText("+")
            self.add_group_button.setFixedSize(32, 32)
            self.add_group_button.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
            self.add_group_button.setProperty("groupActionControl", "true")
            self.add_group_button.setToolTip("Add group")
            self.add_group_button.setEnabled(callable(self._add_group_callback))
            group_control = QtWidgets.QWidget()
            group_layout = QtWidgets.QHBoxLayout(group_control)
            group_layout.setContentsMargins(0, 0, 0, 0)
            group_layout.setSpacing(8)
            group_layout.addWidget(self.group_box, 1)
            group_layout.addWidget(self.group_dropdown_button)
            group_layout.addWidget(self.add_group_button)
            group_layout.setAlignment(QtCore.Qt.AlignVCenter)
            group_control.setFixedHeight(34)
            group_control.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            self.kind_box = QtWidgets.QComboBox()
            self.kind_box.addItem("Scalar", "scalar")
            self.kind_box.addItem("Waveform", "waveform")
            self.kind_box.setCurrentIndex(
                max(0, self.kind_box.findData(str(self._object_data.get("kind", "scalar"))))
            )
            self.waveform_handling_box = QtWidgets.QComboBox()
            self.waveform_handling_box.addItem("Raw Waveform", "raw")
            self.waveform_handling_box.addItem("Mean", "mean")
            initial_handling = (
                "raw"
                if str(self._object_data.get("capture_mode", "scalar")) == "waveform"
                else "mean"
            )
            self.waveform_handling_box.setCurrentIndex(
                max(0, self.waveform_handling_box.findData(initial_handling))
            )
            self.waveform_interval_edit = QtWidgets.QLineEdit()
            interval = self._object_data.get("waveform_sample_interval_sec")
            self.waveform_interval_edit.setText("" if interval is None else f"{float(interval):.12g}")
            self.waveform_interval_edit.setPlaceholderText("e.g. 2.5e-9")
            self.tags_edit = QtWidgets.QLineEdit(", ".join(self._object_data.get("tags", [])))
            self.note_edit = QtWidgets.QPlainTextEdit(str(self._object_data.get("note", "")))
            self.note_edit.setMaximumHeight(90)
            for label, widget in (
                ("ID", self.id_edit), ("Name", self.name_edit), ("Read PV", self.read_pv_edit),
                ("Group", group_control), ("Unit", self.unit_edit), ("Precision", self.precision_spin),
                ("Value Type", self.kind_box),
                ("Waveform Handling", self.waveform_handling_box),
                ("Sample Interval [s]", self.waveform_interval_edit),
                ("Tags", self.tags_edit), ("Note", self.note_edit),
            ):
                form.addRow(label, widget)
            self.waveform_handling_label = form.labelForField(self.waveform_handling_box)
            self.waveform_interval_label = form.labelForField(self.waveform_interval_edit)
            layout.addLayout(form)

            analysis_box = QtWidgets.QGroupBox("Analysis")
            analysis_layout = QtWidgets.QHBoxLayout(analysis_box)
            self.analysis_checks = {}
            analysis = self._object_data.get("analysis", {})
            for key in ("jitter", "correlation", "spectrum"):
                check = QtWidgets.QCheckBox(key.title())
                check.setChecked(bool(analysis.get(key, True)))
                self.analysis_checks[key] = check
                analysis_layout.addWidget(check)
            layout.addWidget(analysis_box)

            buttons = QtWidgets.QDialogButtonBox(
                QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel, self
            )
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)
            self.waveform_handling_box.currentIndexChanged.connect(self._sync_waveform_fields)
            self.kind_box.currentIndexChanged.connect(self._sync_waveform_fields)
            self.group_dropdown_button.clicked.connect(self.group_box.showPopup)
            self.add_group_button.clicked.connect(self._add_group)
            self._sync_waveform_fields()

        def _add_group(self) -> None:
            if not callable(self._add_group_callback):
                return
            group = self._add_group_callback(self)
            if not group:
                return
            group_id = str(group["id"])
            label = str(group.get("label", group_id))
            self._group_labels[group_id] = label
            index = self.group_box.findData(group_id)
            if index < 0:
                self.group_box.addItem(label, group_id)
                index = self.group_box.findData(group_id)
            self.group_box.setCurrentIndex(index)

        def _sync_waveform_fields(self) -> None:
            waveform = self.kind_box.currentData() == "waveform"
            raw_waveform = waveform and self.waveform_handling_box.currentData() == "raw"
            self.waveform_handling_label.setVisible(waveform)
            self.waveform_handling_box.setVisible(waveform)
            self.waveform_interval_label.setVisible(raw_waveform)
            self.waveform_interval_edit.setVisible(raw_waveform)

        def accept(self) -> None:
            if not self.id_edit.text().strip() or not self.read_pv_edit.text().strip():
                QtWidgets.QMessageBox.warning(self, "Invalid PV", "ID and Read PV are required.")
                return
            if not self.group_box.currentData():
                QtWidgets.QMessageBox.warning(self, "Invalid PV", "Choose or add a group.")
                return
            if self._captures_raw_waveform():
                try:
                    interval = float(self.waveform_interval_edit.text().strip())
                except ValueError:
                    interval = 0.0
                if not math.isfinite(interval) or interval <= 0:
                    QtWidgets.QMessageBox.warning(
                        self, "Invalid PV", "Sample Interval must be a positive number."
                    )
                    return
            super().accept()

        def _captures_raw_waveform(self) -> bool:
            return (
                self.kind_box.currentData() == "waveform"
                and self.waveform_handling_box.currentData() == "raw"
            )

        def object_data(self) -> dict[str, object]:
            data = deepcopy(self._object_data)
            data.update(
                {
                    "id": self.id_edit.text().strip(),
                    "name": self.name_edit.text().strip() or self.read_pv_edit.text().strip(),
                    "group": self.group_box.currentData(),
                    "read_pv": self.read_pv_edit.text().strip(),
                    "unit": self.unit_edit.text().strip(),
                    "precision": self.precision_spin.value(),
                    "kind": str(self.kind_box.currentData() or "scalar"),
                    "access": "ro",
                    "value_reducer": (
                        "none"
                        if self.kind_box.currentData() == "scalar" or self._captures_raw_waveform()
                        else "mean"
                    ),
                    "capture_mode": "waveform" if self._captures_raw_waveform() else "scalar",
                    "waveform_sample_interval_sec": (
                        float(self.waveform_interval_edit.text().strip())
                        if self._captures_raw_waveform() else None
                    ),
                    "tags": [item.strip() for item in self.tags_edit.text().split(",") if item.strip()],
                    "note": self.note_edit.toPlainText().strip(),
                    "analysis": {key: check.isChecked() for key, check in self.analysis_checks.items()},
                }
            )
            if data["capture_mode"] != "waveform":
                data.pop("waveform_sample_interval_sec", None)
            return data


    class PVLibraryEditorDialog(QtWidgets.QDialog):
        def __init__(self, *, objects, groups, save_callback, save_as_callback, parent=None) -> None:
            super().__init__(parent)
            self.setWindowTitle("PV Library Editor")
            self.resize(1100, 680)
            self._objects = deepcopy(list(objects))
            self._group_items = deepcopy(list(groups))
            self._groups = {
                str(item["id"]): str(item.get("label", item["id"]))
                for item in self._group_items
            }
            self._save_callback = save_callback
            self._save_as_callback = save_as_callback

            layout = QtWidgets.QVBoxLayout(self)
            self.table = QtWidgets.QTableWidget(0, 6)
            self.table.setHorizontalHeaderLabels(
                ["Name", "ID", "Read PV", "Group", "Value Type", "Analysis"]
            )
            self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
            self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
            self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            self.table.verticalHeader().setVisible(False)
            self.table.horizontalHeader().setStretchLastSection(True)
            layout.addWidget(self.table)

            actions = QtWidgets.QHBoxLayout()
            self.add_button = QtWidgets.QPushButton("Add PV")
            self.edit_button = QtWidgets.QPushButton("Edit PV")
            self.delete_button = QtWidgets.QPushButton("Delete PV")
            actions.addWidget(self.add_button)
            actions.addWidget(self.edit_button)
            actions.addWidget(self.delete_button)
            actions.addStretch(1)
            layout.addLayout(actions)

            buttons = QtWidgets.QDialogButtonBox(self)
            self.save_button = buttons.addButton("Save", QtWidgets.QDialogButtonBox.AcceptRole)
            self.save_as_button = buttons.addButton("Save As...", QtWidgets.QDialogButtonBox.ActionRole)
            self.close_button = buttons.addButton("Close", QtWidgets.QDialogButtonBox.RejectRole)
            layout.addWidget(buttons)

            self.add_button.clicked.connect(self._add_object)
            self.edit_button.clicked.connect(self._edit_object)
            self.delete_button.clicked.connect(self._delete_object)
            self.save_button.clicked.connect(
                lambda: self._save_callback(self.objects(), self.groups())
            )
            self.save_as_button.clicked.connect(
                lambda: self._save_as_callback(self.objects(), self.groups())
            )
            self.close_button.clicked.connect(self.reject)
            self.table.itemSelectionChanged.connect(self._sync_actions)
            self._refresh_table()

        def objects(self) -> list[dict[str, object]]:
            return deepcopy(self._objects)

        def groups(self) -> list[dict[str, object]]:
            return deepcopy(self._group_items)

        def _object_group_labels(self) -> dict[str, str]:
            return {
                str(group["id"]): str(group.get("label", group["id"]))
                for group in self._group_items
                if str(group.get("kind", "object")) == "object"
            }

        def _add_group(self, parent) -> dict[str, object] | None:
            dialog = PVGroupEditorDialog(existing_groups=self._group_items, parent=parent)
            if dialog.exec_() != QtWidgets.QDialog.Accepted:
                return None
            group = dialog.group_data()
            self._group_items.append(group)
            self._groups[str(group["id"])] = str(group["label"])
            return group

        def _selected_row(self) -> int:
            rows = self.table.selectionModel().selectedRows()
            return rows[0].row() if rows else -1

        def _add_object(self) -> None:
            dialog = PVObjectEditorDialog(
                group_labels=self._object_group_labels(),
                add_group_callback=self._add_group,
                parent=self,
            )
            if dialog.exec_() != QtWidgets.QDialog.Accepted:
                return
            item = dialog.object_data()
            if self._has_duplicate(item, -1):
                return
            self._objects.append(item)
            self._refresh_table(len(self._objects) - 1)

        def _edit_object(self) -> None:
            row = self._selected_row()
            if row < 0:
                return
            dialog = PVObjectEditorDialog(
                object_data=self._objects[row],
                group_labels=self._object_group_labels(),
                add_group_callback=self._add_group,
                parent=self,
            )
            if dialog.exec_() != QtWidgets.QDialog.Accepted:
                return
            item = dialog.object_data()
            if self._has_duplicate(item, row):
                return
            self._objects[row] = item
            self._refresh_table(row)

        def _has_duplicate(self, item: dict[str, object], row: int) -> bool:
            for index, other in enumerate(self._objects):
                if index == row:
                    continue
                if item["id"] == other.get("id"):
                    QtWidgets.QMessageBox.warning(self, "Duplicate PV", f"Duplicate object ID: {item['id']}")
                    return True
                if item["read_pv"] == other.get("read_pv"):
                    QtWidgets.QMessageBox.warning(self, "Duplicate PV", f"Duplicate Read PV: {item['read_pv']}")
                    return True
            return False

        def _delete_object(self) -> None:
            row = self._selected_row()
            if row < 0:
                return
            item = self._objects[row]
            answer = QtWidgets.QMessageBox.question(
                self, "Delete PV", f"Delete {item.get('name', item.get('id', 'this PV'))}?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            )
            if answer == QtWidgets.QMessageBox.Yes:
                del self._objects[row]
                self._refresh_table(max(0, row - 1))

        def _refresh_table(self, select_row: int = -1) -> None:
            self.table.setRowCount(len(self._objects))
            for row, item in enumerate(self._objects):
                analysis = item.get("analysis", {})
                value_type = str(item.get("kind", "scalar"))
                if value_type == "waveform":
                    handling = (
                        "Raw"
                        if str(item.get("capture_mode", "scalar")) == "waveform"
                        else "Mean"
                    )
                    value_type = f"Waveform ({handling})"
                else:
                    value_type = "Scalar"
                values = [
                    item.get("name", ""), item.get("id", ""), item.get("read_pv", ""),
                    self._groups.get(str(item.get("group", "")), str(item.get("group", ""))),
                    value_type,
                    ", ".join(key for key in ("jitter", "correlation", "spectrum") if analysis.get(key)),
                ]
                for col, value in enumerate(values):
                    self.table.setItem(row, col, QtWidgets.QTableWidgetItem(str(value)))
            self.table.resizeColumnsToContents()
            if 0 <= select_row < self.table.rowCount():
                self.table.selectRow(select_row)
            self._sync_actions()

        def _sync_actions(self) -> None:
            selected = self._selected_row() >= 0
            self.edit_button.setEnabled(selected)
            self.delete_button.setEnabled(selected)

else:

    class PVGroupEditorDialog:  # pragma: no cover - fallback for non-GUI tests
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("PyQt5 is required to create PVGroupEditorDialog")

    class PVObjectEditorDialog:  # pragma: no cover - fallback for non-GUI tests
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("PyQt5 is required to create PVObjectEditorDialog")

    class PVLibraryEditorDialog:  # pragma: no cover - fallback for non-GUI tests
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("PyQt5 is required to create PVLibraryEditorDialog")
