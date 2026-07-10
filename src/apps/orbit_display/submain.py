import sys
from pathlib import Path

_REPO_BOOTSTRAP_ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / "repo_bootstrap.py").is_file()
)
if str(_REPO_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_BOOTSTRAP_ROOT))

from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

import numpy as np

from half_linac.src.shared.machine_profile import (
    get_workflow,
    list_elements,
    load_app_context,
    resolve_channel,
)
from subgui import Ui_Form 
from PyQt5.QtWidgets import QApplication, QLabel, QLineEdit, QMainWindow
from PyQt5.QtCore import QTimer

from epics import caget_many

class myWindow(QMainWindow, Ui_Form):
    def __init__(self, refresh_interval_ms=1000):
        super().__init__()
        self.setupUi(self)
        self.app_context = load_app_context("orbit_display")
        self.machine_profile = self.app_context.profile
        self.control_backend = self.app_context.control_backend.name
        self.refresh_interval_ms = max(100, int(refresh_interval_ms))
        self.bpm_position_scale_to_mm = self._resolve_bpm_position_scale_to_mm()
        self.bpm_elements = list_elements(self.app_context, kind="bpm")
        self.bpm_ids = [element.id for element in self.bpm_elements]
        self.bpm_x_pvs = [resolve_channel(self.app_context, bpm_id, "x") for bpm_id in self.bpm_ids]
        self.bpm_y_pvs = [resolve_channel(self.app_context, bpm_id, "y") for bpm_id in self.bpm_ids]
        self.pvlx_val = [None] * len(self.bpm_x_pvs)
        self.pvly_val = [None] * len(self.bpm_y_pvs)
        self._configure_bpm_widgets()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.bpmvalue_dis)
        self.timer.start(self.refresh_interval_ms)
        self.bpmvalue_dis()

    def set_refresh_interval_ms(self, refresh_interval_ms):
        self.refresh_interval_ms = max(100, int(refresh_interval_ms))
        self.timer.start(self.refresh_interval_ms)

    def _resolve_bpm_position_scale_to_mm(self):
        workflow = get_workflow(self.machine_profile, "orbit")
        scale_by_backend = workflow.get("bpm_position_scale_to_mm", {})
        if isinstance(scale_by_backend, dict):
            try:
                return float(scale_by_backend.get(self.control_backend, 1000.0))
            except (TypeError, ValueError):
                pass
        return 1000.0

    def _configure_bpm_widgets(self):
        self.setWindowTitle(f"{self.machine_profile.machine.display_name} BPM Detail (mm)")
        self.x_value_widgets = []
        self.y_value_widgets = []
        self.x_label_widgets = []
        self.y_label_widgets = []

        for index in range(1, 44):
            x_label = getattr(self, f"bPMx{index:02d}Label")
            y_label = getattr(self, f"bPMy{index:02d}Label")
            x_value = getattr(self, f"bPMx{index:02d}LineEdit")
            y_value = getattr(self, f"bPMy{index:02d}LineEdit")
            self.x_label_widgets.append(x_label)
            self.y_label_widgets.append(y_label)
            self.x_value_widgets.append(x_value)
            self.y_value_widgets.append(y_value)

        while len(self.bpm_ids) > len(self.x_label_widgets):
            self._append_dynamic_bpm_row(len(self.x_label_widgets) + 1)

        visible_count = min(len(self.bpm_ids), len(self.x_label_widgets))
        for index, bpm_id in enumerate(self.bpm_ids[:visible_count]):
            self.x_label_widgets[index].setText(f"{bpm_id} x (mm)")
            self.y_label_widgets[index].setText(f"{bpm_id} y (mm)")
            self.x_label_widgets[index].show()
            self.y_label_widgets[index].show()
            self.x_value_widgets[index].show()
            self.y_value_widgets[index].show()

        for index in range(visible_count, len(self.x_label_widgets)):
            self.x_label_widgets[index].hide()
            self.y_label_widgets[index].hide()
            self.x_value_widgets[index].hide()
            self.y_value_widgets[index].hide()

    def _append_dynamic_bpm_row(self, index):
        row = len(self.x_label_widgets)

        x_label = QLabel(self.horizontalLayoutWidget)
        x_label.setObjectName(f"bPMx{index:02d}Label_dynamic")
        self.formLayout.setWidget(row, self.formLayout.LabelRole, x_label)

        x_value = QLineEdit(self.horizontalLayoutWidget)
        x_value.setReadOnly(True)
        x_value.setObjectName(f"bPMx{index:02d}LineEdit_dynamic")
        self.formLayout.setWidget(row, self.formLayout.FieldRole, x_value)

        y_label = QLabel(self.horizontalLayoutWidget)
        y_label.setObjectName(f"bPMy{index:02d}Label_dynamic")
        self.formLayout_3.setWidget(row, self.formLayout_3.LabelRole, y_label)

        y_value = QLineEdit(self.horizontalLayoutWidget)
        y_value.setReadOnly(True)
        y_value.setObjectName(f"bPMy{index:02d}LineEdit_dynamic")
        self.formLayout_3.setWidget(row, self.formLayout_3.FieldRole, y_value)

        self.x_label_widgets.append(x_label)
        self.y_label_widgets.append(y_label)
        self.x_value_widgets.append(x_value)
        self.y_value_widgets.append(y_value)
        
    def _format_bpm_value(self, value):
        if value is None:
            return "--"
        try:
            scaled_value = float(value) * self.bpm_position_scale_to_mm
        except (TypeError, ValueError):
            return "--"
        if not np.isfinite(scaled_value):
            return "--"
        return f"{scaled_value:.3f}"

    def bpmvalue_dis(self):
        self.init_pv()

        visible_count = min(
            len(self.bpm_ids),
            len(self.x_value_widgets),
            len(self.pvlx_val),
            len(self.pvly_val),
        )
        for index in range(visible_count):
            self.x_value_widgets[index].setText(self._format_bpm_value(self.pvlx_val[index]))
            self.y_value_widgets[index].setText(self._format_bpm_value(self.pvly_val[index]))

    def init_pv(self):
        try:
            self.pvlx_val = caget_many(self.bpm_x_pvs)
            self.pvly_val = caget_many(self.bpm_y_pvs)
            self.statusBar().clearMessage()
        except Exception:
            self.pvlx_val = [None] * len(self.bpm_x_pvs)
            self.pvly_val = [None] * len(self.bpm_y_pvs)
            self.statusBar().showMessage("PV connection unavailable.", 5000)


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = myWindow()
    window.show()
    sys.exit(app.exec_())
