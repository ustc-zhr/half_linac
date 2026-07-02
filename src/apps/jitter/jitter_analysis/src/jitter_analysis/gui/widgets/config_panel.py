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
            self.load_setup_button = QtWidgets.QPushButton("Setup Browser...")
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
            self._configure_fixed_action_button(self.save_dir_browse_button)
            self._configure_row_action_button(self.load_setup_button)
            self._configure_fixed_action_button(self.save_setup_button)

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
            grid.addWidget(self.config_path_edit, 0, 1)
            grid.addWidget(self.load_button, 0, 2)
            grid.addWidget(self._field_label("Setup"), 1, 0)
            grid.addWidget(self.load_setup_button, 1, 1)
            grid.addWidget(self.save_setup_button, 1, 2)
            grid.addWidget(self._divider(), 2, 0, 1, 3)
            grid.addWidget(self._field_label("Save Dir"), 3, 0)
            grid.addWidget(self.save_dir_edit, 3, 1)
            grid.addWidget(self.save_dir_browse_button, 3, 2)
            grid.addWidget(self._field_label("Operator"), 4, 0)
            grid.addWidget(self.operator_edit, 4, 1, 1, 2)
            grid.addWidget(self._field_label("Notes", align_top=True), 5, 0)
            grid.addWidget(self.notes_edit, 5, 1, 1, 2)

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

        def _configure_fixed_action_button(self, button) -> None:
            button.setProperty("runInfoControl", "true")
            button.setFixedSize(self.ACTION_BUTTON_WIDTH, self.ACTION_BUTTON_HEIGHT)
            button.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)

        def _configure_row_action_button(self, button) -> None:
            button.setProperty("runInfoControl", "true")
            button.setFixedHeight(self.ACTION_BUTTON_HEIGHT)
            button.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

        def _configure_line_field(self, field) -> None:
            field.setProperty("runInfoControl", "true")
            field.setFixedHeight(self.ACTION_BUTTON_HEIGHT)
            field.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

else:

    class ConfigPanel:  # pragma: no cover - fallback for non-GUI tests
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("PyQt5 is required to create ConfigPanel")
