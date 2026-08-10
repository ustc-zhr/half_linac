from __future__ import annotations

try:
    from PyQt5 import QtCore, QtWidgets
except ImportError:  # pragma: no cover - optional runtime dependency
    QtCore = None
    QtWidgets = None


if QtWidgets is not None:

    class ConfigPanel(QtWidgets.QWidget):
        ACTION_BUTTON_WIDTH = 132
        ACTION_BUTTON_HEIGHT = 40
        BROWSE_BUTTON_WIDTH = 96
        LABEL_WIDTH = 82

        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(8)

            self.config_path_edit = QtWidgets.QLineEdit()
            self.config_path_edit.setReadOnly(True)
            self.config_path_edit.setPlaceholderText("No PV library loaded")
            self.load_button = QtWidgets.QPushButton("Load PVs")
            self.edit_library_button = QtWidgets.QPushButton("Edit PVs")
            self.load_setup_button = QtWidgets.QPushButton("Load Setup...")
            self.save_setup_button = QtWidgets.QPushButton("Save Setup...")

            self.save_dir_edit = QtWidgets.QLineEdit("runs")
            self.save_dir_edit.setPlaceholderText("Directory used for new runs")
            self.save_dir_browse_button = QtWidgets.QPushButton("Browse...")

            self.operator_edit = QtWidgets.QLineEdit()
            self.notes_edit = QtWidgets.QPlainTextEdit()
            self.notes_edit.setPlaceholderText("Run notes and operator comments")
            self.notes_edit.setMinimumHeight(108)
            self.notes_edit.setMaximumHeight(150)

            for field in (
                self.config_path_edit,
                self.save_dir_edit,
                self.operator_edit,
            ):
                self._configure_line_field(field)
            self._configure_fixed_action_button(self.load_button)
            self._configure_fixed_action_button(self.edit_library_button)
            self._configure_fixed_action_button(
                self.save_dir_browse_button,
                width=self.BROWSE_BUTTON_WIDTH,
            )
            self._configure_fixed_action_button(self.load_setup_button)
            self._configure_fixed_action_button(self.save_setup_button)

            save_dir_layout = QtWidgets.QHBoxLayout()
            save_dir_layout.setContentsMargins(0, 0, 0, 0)
            save_dir_layout.setSpacing(8)
            save_dir_layout.addWidget(self.save_dir_edit, 1)
            save_dir_layout.addWidget(self.save_dir_browse_button)

            grid = QtWidgets.QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(8)
            grid.setVerticalSpacing(8)
            grid.setColumnMinimumWidth(0, self.LABEL_WIDTH)
            grid.setColumnMinimumWidth(2, self.ACTION_BUTTON_WIDTH)
            grid.setColumnStretch(0, 0)
            grid.setColumnStretch(1, 1)
            grid.setColumnStretch(2, 0)

            grid.addWidget(self._field_label("PV Library"), 0, 0)
            grid.addWidget(self.config_path_edit, 0, 1, 1, 2)
            grid.addWidget(self.load_button, 1, 1)
            grid.addWidget(self.edit_library_button, 1, 2)
            grid.addWidget(self._field_label("Run Setup"), 2, 0, QtCore.Qt.AlignVCenter)
            grid.addWidget(
                self.load_setup_button,
                2,
                1,
                QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
            )
            grid.addWidget(self.save_setup_button, 2, 2, QtCore.Qt.AlignVCenter)
            grid.addWidget(self._divider(), 3, 0, 1, 3)
            grid.addWidget(self._field_label("Save Dir"), 4, 0, QtCore.Qt.AlignVCenter)
            grid.addLayout(save_dir_layout, 4, 1, 1, 2)
            grid.addWidget(self._field_label("Operator"), 5, 0)
            grid.addWidget(self.operator_edit, 5, 1, 1, 2)
            grid.addWidget(self._field_label("Notes", align_top=True), 6, 0)
            grid.addWidget(self.notes_edit, 6, 1, 1, 2)

            layout.addLayout(grid)
            layout.addStretch(1)

        def _field_label(self, text: str, *, align_top: bool = False):
            label = QtWidgets.QLabel(text)
            label.setProperty("role", "field")
            label.setFixedWidth(self.LABEL_WIDTH)
            label.setContentsMargins(0, 0, 0, 0)
            label.setAlignment(
                QtCore.Qt.AlignLeft | (QtCore.Qt.AlignTop if align_top else QtCore.Qt.AlignVCenter)
            )
            return label

        def _divider(self):
            line = QtWidgets.QFrame()
            line.setObjectName("runInfoDivider")
            line.setFrameShape(QtWidgets.QFrame.HLine)
            line.setFrameShadow(QtWidgets.QFrame.Plain)
            return line

        def _configure_fixed_action_button(self, button, *, width: int | None = None) -> None:
            button.setProperty("runInfoControl", "true")
            button.setFixedSize(width or self.ACTION_BUTTON_WIDTH, self.ACTION_BUTTON_HEIGHT)
            button.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)

        def _configure_line_field(self, field) -> None:
            field.setProperty("runInfoControl", "true")
            field.setFixedHeight(self.ACTION_BUTTON_HEIGHT)
            field.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

else:

    class ConfigPanel:  # pragma: no cover - fallback for non-GUI tests
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("PyQt5 is required to create ConfigPanel")
