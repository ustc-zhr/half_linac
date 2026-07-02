from __future__ import annotations

try:
    from PyQt5 import QtWidgets
except ImportError:  # pragma: no cover - optional runtime dependency
    QtWidgets = None


if QtWidgets is not None:

    class ObjectPanel(QtWidgets.QWidget):
        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(8)

            controls = QtWidgets.QHBoxLayout()
            self.select_button = QtWidgets.QPushButton("Choose PVs...")
            self.clear_button = QtWidgets.QPushButton("Clear Selection")
            controls.addWidget(self.select_button)
            controls.addWidget(self.clear_button)
            layout.addLayout(controls)

            self.loaded_summary_label = QtWidgets.QLabel("No PV library loaded. Load a PV library to begin.")
            self.loaded_summary_label.setWordWrap(True)
            layout.addWidget(self.loaded_summary_label)

            self.selection_summary_label = QtWidgets.QLabel("No PVs selected yet.")
            self.selection_summary_label.setWordWrap(True)
            layout.addWidget(self.selection_summary_label)

            self.selection_detail_label = QtWidgets.QLabel(
                "Choose read PVs and control PVs after the library is loaded."
            )
            self.selection_detail_label.setWordWrap(True)
            layout.addWidget(self.selection_detail_label)
            layout.addStretch(1)

        def set_library_empty(self) -> None:
            self.loaded_summary_label.setText("No PV library loaded. Load a PV library to begin.")
            self.selection_summary_label.setText("No PVs selected yet.")
            self.selection_detail_label.setText(
                "Choose read PVs and control PVs after the library is loaded."
            )

        def set_library_objects(self, objects, group_labels: dict[str, str]) -> None:
            group_count = len({obj.group for obj in objects})
            group_names = sorted({group_labels.get(obj.group, obj.group) for obj in objects})
            preview = ", ".join(group_names[:4]) if group_names else "none"
            if len(group_names) > 4:
                preview += ", ..."
            self.loaded_summary_label.setText(
                f"PV library: {len(objects)} read PVs across {group_count} groups. {preview}"
            )

        def set_selected_objects(self, objects) -> None:
            self._selected_objects = list(objects)
            self._update_selection_summary()

        def set_selected_knobs(self, knobs) -> None:
            self._selected_knobs = list(knobs)
            self._update_selection_summary()

        def _update_selection_summary(self) -> None:
            knobs = getattr(self, "_selected_knobs", [])
            objects = getattr(self, "_selected_objects", [])
            if not knobs and not objects:
                self.selection_summary_label.setText("No PVs selected yet.")
                self.selection_detail_label.setText(
                    "Choose read PVs and control PVs with 'Choose PVs...'."
                )
                return

            self.selection_summary_label.setText(
                f"Control PVs: {len(knobs)} selected  |  Read PVs: {len(objects)} selected"
            )

            knob_names = ", ".join(knob.name for knob in knobs[:3]) if knobs else "none"
            object_names = ", ".join(obj.name for obj in objects[:4]) if objects else "none"
            if len(knobs) > 3:
                knob_names += ", ..."
            if len(objects) > 4:
                object_names += ", ..."
            self.selection_detail_label.setText(
                f"Controls: {knob_names}\nReads: {object_names}"
            )

else:

    class ObjectPanel:  # pragma: no cover - fallback for non-GUI tests
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("PyQt5 is required to create ObjectPanel")
