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

            self._selected_objects = []
            self._selected_knobs = []
            self.loaded_summary_label = QtWidgets.QLabel("Library: Not loaded")
            self.loaded_summary_label.setWordWrap(True)
            layout.addWidget(self.loaded_summary_label)

            self.selection_summary_label = QtWidgets.QLabel("Selected: None")
            self.selection_summary_label.setWordWrap(True)
            layout.addWidget(self.selection_summary_label)

            self.selection_detail_label = QtWidgets.QLabel()
            self.selection_detail_label.setWordWrap(True)
            self.selection_detail_label.setVisible(False)
            layout.addWidget(self.selection_detail_label)
            layout.addStretch(1)

        def set_library_empty(self) -> None:
            self.loaded_summary_label.setText("Library: Not loaded")
            self.selection_summary_label.setText("Selected: None")
            self.selection_detail_label.clear()
            self.selection_detail_label.setVisible(False)

        def set_library_objects(self, objects, group_labels: dict[str, str]) -> None:
            group_count = len({obj.group for obj in objects})
            group_word = "group" if group_count == 1 else "groups"
            self.loaded_summary_label.setText(
                f"Library: {len(objects)} read PVs | {group_count} {group_word}"
            )

        def set_selected_objects(self, objects) -> None:
            self._selected_objects = list(objects)
            self._update_selection_summary()

        def set_selected_knobs(self, knobs) -> None:
            self._selected_knobs = list(knobs)
            self._update_selection_summary()

        def _update_selection_summary(self) -> None:
            knobs = self._selected_knobs
            objects = self._selected_objects
            if not knobs and not objects:
                self.selection_summary_label.setText("Selected: None")
                self.selection_detail_label.clear()
                self.selection_detail_label.setVisible(False)
                return

            self.selection_summary_label.setText(
                f"Selected: {len(objects)} read | {len(knobs)} control"
            )

            details = []
            object_names = ", ".join(obj.name for obj in objects[:4])
            if len(knobs) > 3:
                knob_names = ", ".join(knob.name for knob in knobs[:3]) + ", ..."
            else:
                knob_names = ", ".join(knob.name for knob in knobs)
            if len(objects) > 4:
                object_names += ", ..."
            if objects:
                details.append(f"Read: {object_names}")
            if knobs:
                details.append(f"Control: {knob_names}")
            self.selection_detail_label.setText("\n".join(details))
            self.selection_detail_label.setVisible(True)

else:

    class ObjectPanel:  # pragma: no cover - fallback for non-GUI tests
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("PyQt5 is required to create ObjectPanel")
