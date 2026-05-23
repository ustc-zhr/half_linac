import time
import sys
from pathlib import Path
from subprocess import Popen

_REPO_BOOTSTRAP_ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / "repo_bootstrap.py").is_file()
)
if str(_REPO_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_BOOTSTRAP_ROOT))

from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

import numpy as np

from half_linac.src.shared.machine_profile import (
    list_elements,
    load_app_context,
    resolve_channel,
)
from subgui import Ui_Form 
from PyQt5.QtWidgets import QMainWindow, QApplication, QLabel, QHBoxLayout
from PyQt5.QtCore import QTimer

from epics import caget, caget_many

import half_linac.runtime_config as st

class myWindow(QMainWindow, Ui_Form):
    def __init__(self):
        super().__init__()
        self.setupUi(self)
        self.app_context = load_app_context("orbit_display")
        self.machine_profile = self.app_context.profile
        self.control_backend = self.app_context.control_backend.name
        self.bpm_elements = list_elements(self.app_context, kind="bpm")
        self.bpm_ids = [element.id for element in self.bpm_elements]
        self.bpm_x_pvs = [resolve_channel(self.app_context, bpm_id, "x") for bpm_id in self.bpm_ids]
        self.bpm_y_pvs = [resolve_channel(self.app_context, bpm_id, "y") for bpm_id in self.bpm_ids]
        self._configure_bpm_widgets()
        
        # init pv
        # self.init_pv()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.bpmvalue_dis)
        self.timer.start(1000) #every 1s

    def _configure_bpm_widgets(self):
        self.setWindowTitle(f"{self.machine_profile.machine.display_name} BPM Detail")
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

        visible_count = min(len(self.bpm_ids), len(self.x_label_widgets))
        for index, bpm_id in enumerate(self.bpm_ids[:visible_count]):
            self.x_label_widgets[index].setText(f"{bpm_id} X")
            self.y_label_widgets[index].setText(f"{bpm_id} Y")

        for index in range(visible_count, len(self.x_label_widgets)):
            self.x_label_widgets[index].hide()
            self.y_label_widgets[index].hide()
            self.x_value_widgets[index].hide()
            self.y_value_widgets[index].hide()
        
    def bpmvalue_dis(self):
        # init pv
        self.init_pv()

        self.pvlx_val = [round(num*1000, 3) for num in self.pvlx_val]
        self.pvly_val = [round(num*1000, 3) for num in self.pvly_val]
        visible_count = min(len(self.bpm_ids), len(self.x_value_widgets))
        for index in range(visible_count):
            self.x_value_widgets[index].setText(str(self.pvlx_val[index]))
            self.y_value_widgets[index].setText(str(self.pvly_val[index]))


    def init_pv(self):
        # get the values
        self.pvlx_val = caget_many(self.bpm_x_pvs) 
        self.pvly_val = caget_many(self.bpm_y_pvs) 



if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = myWindow()
    window.show()
    sys.exit(app.exec_())


