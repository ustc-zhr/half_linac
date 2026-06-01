from __future__ import annotations

try:
    from PyQt5 import QtWidgets
except ImportError:  # pragma: no cover - optional runtime dependency
    QtWidgets = None


if QtWidgets is not None:

    class ConfigPanel(QtWidgets.QWidget):
        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(8)

            self.config_path_edit = QtWidgets.QLineEdit()
            self.config_path_edit.setReadOnly(True)
            self.config_path_edit.setPlaceholderText("No PV library loaded")
            self.load_button = QtWidgets.QPushButton("Load PV Library...")
            self.load_setup_button = QtWidgets.QPushButton("Setup Browser...")
            self.save_setup_button = QtWidgets.QPushButton("Save Setup...")
            path_row = QtWidgets.QWidget()
            path_layout = QtWidgets.QHBoxLayout(path_row)
            path_layout.setContentsMargins(0, 0, 0, 0)
            path_layout.addWidget(self.config_path_edit, 1)
            path_layout.addWidget(self.load_button)

            setup_row = QtWidgets.QWidget()
            setup_layout = QtWidgets.QHBoxLayout(setup_row)
            setup_layout.setContentsMargins(0, 0, 0, 0)
            setup_layout.addWidget(self.load_setup_button)
            setup_layout.addWidget(self.save_setup_button)
            setup_layout.addStretch(1)

            self.save_dir_edit = QtWidgets.QLineEdit("runs")
            self.save_dir_edit.setPlaceholderText("Directory used for new runs")
            self.save_dir_browse_button = QtWidgets.QPushButton("Browse...")
            save_dir_row = QtWidgets.QWidget()
            save_dir_layout = QtWidgets.QHBoxLayout(save_dir_row)
            save_dir_layout.setContentsMargins(0, 0, 0, 0)
            save_dir_layout.addWidget(self.save_dir_edit, 1)
            save_dir_layout.addWidget(self.save_dir_browse_button)

            self.operator_edit = QtWidgets.QLineEdit()
            self.notes_edit = QtWidgets.QPlainTextEdit()
            self.notes_edit.setPlaceholderText("Run notes and operator comments")
            self.notes_edit.setMaximumHeight(140)

            form = QtWidgets.QFormLayout()
            form.setContentsMargins(0, 0, 0, 0)
            form.setSpacing(8)
            form.addRow("PV Library", path_row)
            form.addRow("Setup", setup_row)
            form.addRow("Save Dir", save_dir_row)
            form.addRow("Operator", self.operator_edit)
            form.addRow("Notes", self.notes_edit)

            layout.addLayout(form)
            layout.addStretch(1)

else:

    class ConfigPanel:  # pragma: no cover - fallback for non-GUI tests
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("PyQt5 is required to create ConfigPanel")
