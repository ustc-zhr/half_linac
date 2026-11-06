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

        def __init__(self, parent=None, object_panel=None) -> None:
            super().__init__(parent)
            self.setObjectName("configInfoPanel")
            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(8)

            self.config_path_edit = QtWidgets.QLineEdit()
            self.config_path_edit.setReadOnly(True)
            self.config_path_edit.setPlaceholderText("No PV library loaded")
            self.load_button = QtWidgets.QPushButton("Load")
            self.edit_library_button = QtWidgets.QPushButton("Edit")
            self.load_setup_button = QtWidgets.QPushButton("Load")
            self.save_setup_button = QtWidgets.QPushButton("Save")

            self.save_dir_edit = QtWidgets.QLineEdit("runs")
            self.save_dir_edit.setPlaceholderText("Directory used for new runs")
            self.save_dir_browse_button = QtWidgets.QPushButton("Browse")
            self.operator_edit = QtWidgets.QLineEdit()
            self.notes_edit = QtWidgets.QPlainTextEdit()
            self.notes_edit.setPlaceholderText("Run notes and operator comments")
            self.notes_edit.setMinimumHeight(88)
            self.notes_edit.setMaximumHeight(112)

            for field in (self.config_path_edit, self.save_dir_edit, self.operator_edit):
                field.setProperty("runInfoControl", "true")
                field.setFixedHeight(34)
            for button in (
                self.load_button,
                self.edit_library_button,
                self.load_setup_button,
                self.save_setup_button,
                self.save_dir_browse_button,
            ):
                button.setProperty("compactControl", "true")
                button.setMinimumHeight(30)

            layout.addWidget(self._section_label("PV library"))
            layout.addWidget(self.config_path_edit)
            library_actions = QtWidgets.QHBoxLayout()
            library_actions.setContentsMargins(0, 0, 0, 0)
            library_actions.setSpacing(8)
            library_actions.addWidget(self.load_button)
            library_actions.addWidget(self.edit_library_button)
            layout.addLayout(library_actions)

            if object_panel is not None:
                layout.addWidget(self._divider())
                layout.addWidget(self._section_label("PV selection"))
                layout.addWidget(object_panel)

            layout.addWidget(self._divider())
            layout.addWidget(self._section_label("Run setup"))
            setup_actions = QtWidgets.QHBoxLayout()
            setup_actions.setContentsMargins(0, 0, 0, 0)
            setup_actions.setSpacing(8)
            setup_actions.addWidget(self.load_setup_button)
            setup_actions.addWidget(self.save_setup_button)
            layout.addLayout(setup_actions)

            layout.addWidget(self._divider())
            layout.addWidget(self._section_label("Run details"))
            layout.addWidget(self._detail_label("Save directory"))
            save_dir_layout = QtWidgets.QHBoxLayout()
            save_dir_layout.setContentsMargins(0, 0, 0, 0)
            save_dir_layout.setSpacing(8)
            save_dir_layout.addWidget(self.save_dir_edit, 1)
            save_dir_layout.addWidget(self.save_dir_browse_button)
            layout.addLayout(save_dir_layout)
            layout.addWidget(self._detail_label("Operator"))
            layout.addWidget(self.operator_edit)
            layout.addWidget(self._detail_label("Notes"))
            layout.addWidget(self.notes_edit)

        @staticmethod
        def _section_label(text: str):
            label = QtWidgets.QLabel(text)
            label.setProperty("role", "sidebarSectionTitle")
            return label

        @staticmethod
        def _detail_label(text: str):
            label = QtWidgets.QLabel(text)
            label.setProperty("role", "field")
            return label

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
