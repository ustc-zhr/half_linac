from __future__ import annotations

import json
import math
import sys
from pathlib import Path

_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "repo_bootstrap.py").is_file())
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from repo_bootstrap import ensure_repo_import_path
ensure_repo_import_path(__file__)

from PyQt5.QtCore import QSignalBlocker
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QApplication, QFileDialog, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel,
    QMainWindow, QMessageBox, QPushButton, QStatusBar, QTableWidget,
    QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from half_linac.src.apps.hv_control.epics_client import BatchWorker, HvMonitor
from half_linac.src.apps.hv_control.profile_runtime import HvRuntime, load_hv_runtime
from half_linac.src.shared.app_theme import resolve_initial_theme
from half_linac.src.shared.machine_profile import RuntimeContextWidget


DARK = {"window":"#0f1519", "panel":"#172027", "input":"#10171c", "border":"#2a3943", "text":"#e6edf2", "muted":"#91a2ad", "accent":"#45d0bc", "warning":"#e4b86f", "danger":"#e37878"}
LIGHT = {"window":"#f2ede5", "panel":"#fffdf9", "input":"#fffdf9", "border":"#d7cec1", "text":"#2c3942", "muted":"#746c62", "accent":"#2d7f6d", "warning":"#a97118", "danger":"#b44141"}


def stylesheet(p):
    return f'''QMainWindow, QWidget {{ background:{p["window"]}; color:{p["text"]}; font-family:"IBM Plex Sans","Segoe UI",sans-serif; font-size:12px; }}
QFrame#panel {{ background:{p["panel"]}; border:1px solid {p["border"]}; border-radius:8px; }}
QLabel {{ background:transparent; }} QHeaderView::section {{ background:{p["panel"]}; color:{p["muted"]}; padding:6px; border:0; }}
QPushButton, QToolButton, QDoubleSpinBox {{ background:{p["input"]}; color:{p["text"]}; border:1px solid {p["border"]}; border-radius:6px; min-height:28px; padding:2px 8px; }}
QPushButton:hover, QToolButton:hover, QDoubleSpinBox:focus {{ border-color:{p["accent"]}; }} QPushButton:disabled {{ color:{p["muted"]}; }}
QTableWidget {{ background:{p["input"]}; alternate-background-color:{p["panel"]}; gridline-color:{p["border"]}; border:1px solid {p["border"]}; }}
QStatusBar {{ background:{p["panel"]}; color:{p["muted"]}; }}'''


class HvControlWindow(QMainWindow):
    def __init__(self, runtime: HvRuntime):
        super().__init__()
        self.runtime = runtime
        self._theme = resolve_initial_theme()
        self.monitor = HvMonitor(runtime.modulators, self)
        self.worker = None
        self.values = {}
        self.connected = {}
        self.status_labels = {}
        self.enable_buttons = {}
        self._build_ui()
        self._apply_theme()
        self.monitor.value_changed.connect(self._on_value)
        self.monitor.connection_changed.connect(self._on_connection)
        self.monitor.bind()

    def _build_ui(self):
        self.setWindowTitle(f"{self.runtime.context.machine.display_name} - HV Modulator Control")
        self.resize(1360, 760)
        self.setMinimumSize(1050, 620)
        root = QWidget(self)
        outer = QVBoxLayout(root); outer.setContentsMargins(12, 10, 12, 8); outer.setSpacing(8)
        heading = QHBoxLayout()
        title = QLabel("HV Modulator Control", root); title.setStyleSheet("font-size:22px;font-weight:700;")
        heading.addWidget(title); heading.addStretch(1)
        self.save_button = QPushButton("Save", root); self.restore_button = QPushButton("Restore", root)
        self.save_button.clicked.connect(self._save); self.restore_button.clicked.connect(self._restore)
        heading.addWidget(self.save_button); heading.addWidget(self.restore_button)
        heading.addWidget(RuntimeContextWidget(machine_id=self.runtime.context.machine.id, machine_display_name=self.runtime.context.machine.display_name, control_backend=self.runtime.context.control_backend.name, parent=root))
        self.theme_button = QToolButton(root); self.theme_button.setFixedSize(32, 32); self.theme_button.clicked.connect(self._toggle_theme); heading.addWidget(self.theme_button)
        outer.addLayout(heading)

        controls = QFrame(root); controls.setObjectName("panel"); row = QHBoxLayout(controls); row.setContentsMargins(10,8,10,8)
        row.addWidget(QLabel("Set all HV:"))
        self.global_spin = QDoubleSpinBox(controls); self.global_spin.setRange(runtime.low, runtime.high); self.global_spin.setDecimals(1); self.global_spin.setSuffix(f" {runtime.unit}"); self.global_spin.setKeyboardTracking(False); row.addWidget(self.global_spin)
        self.fill_button = QPushButton("Fill all", controls); self.fill_button.clicked.connect(self._fill_all); row.addWidget(self.fill_button)
        self.apply_button = QPushButton("Apply all", controls); self.apply_button.clicked.connect(self._apply_all); row.addWidget(self.apply_button)
        self.read_button = QPushButton("Read back", controls); self.read_button.clicked.connect(self._read_back); row.addWidget(self.read_button)
        row.addSpacing(14)
        for text, field, value in (("Enable all 1", "enable_1", 1), ("Disable all 1", "enable_1", 0), ("Enable all 2", "enable_2", 1), ("Disable all 2", "enable_2", 0)):
            button = QPushButton(text, controls); button.clicked.connect(lambda _=False, f=field, v=value, t=text: self._batch_enable(f, v, t)); row.addWidget(button)
        row.addStretch(1); outer.addWidget(controls)

        self.table = QTableWidget(len(self.runtime.modulators), 8, root)
        self.table.setHorizontalHeaderLabels(("Modulator", "HV set", "HV readback", "Enable 1", "Enable 2", "Connection", "Operation", ""))
        self.table.verticalHeader().setVisible(False); self.table.setAlternatingRowColors(True); self.table.setSelectionMode(QTableWidget.NoSelection)
        widths = (120, 150, 150, 150, 150, 120, 260, 100)
        for i, width in enumerate(widths): self.table.setColumnWidth(i, width)
        self.table.horizontalHeader().setStretchLastSection(True)
        for row_index, mod in enumerate(self.runtime.modulators): self._build_row(row_index, mod)
        outer.addWidget(self.table, 1)
        self.setCentralWidget(root); self.setStatusBar(QStatusBar(self)); self.statusBar().showMessage("Connecting to HV PVs")

    def _build_row(self, row, mod):
        self.table.setItem(row, 0, QTableWidgetItem(mod.name))
        set_spin = QDoubleSpinBox(self.table); set_spin.setRange(self.runtime.low, self.runtime.high); set_spin.setDecimals(1); set_spin.setSuffix(f" {self.runtime.unit}"); set_spin.setKeyboardTracking(False); set_spin.setEnabled(False); self.table.setCellWidget(row, 1, set_spin)
        for col in (2, 5, 6): self.table.setItem(row, col, QTableWidgetItem("--"))
        for field, col in (("enable_1",3),("enable_2",4)):
            cell = QWidget(self.table); layout = QHBoxLayout(cell); layout.setContentsMargins(3,2,3,2)
            label = QLabel("--", cell); button = QPushButton("Set", cell); button.setEnabled(False); button.clicked.connect(lambda _=False, n=mod.name, f=field: self._toggle_enable(n, f))
            layout.addWidget(label); layout.addWidget(button); self.table.setCellWidget(row, col, cell); self.status_labels[(mod.name, field)] = label; self.enable_buttons[(mod.name, field)] = button
        set_spin.valueChanged.connect(lambda value, n=mod.name: self._set_target(n, value))
        self.values[(mod.name, "target_widget")] = set_spin

    def _set_target(self, name, value): self.values[(name, "target")] = float(value)

    def _on_value(self, name, field, raw):
        try: value = float(raw)
        except (TypeError, ValueError): return
        if not math.isfinite(value): return
        self.values[(name, field)] = value
        row = self._row(name)
        if field == "voltage_set":
            widget = self.values[(name, "target_widget")]
            with QSignalBlocker(widget): widget.setValue(value)
            self.table.item(row, 1).setText(f"{value:.1f} {self.runtime.unit}") if False else None
        elif field == "voltage_readback": self.table.item(row, 2).setText(f"{value:.1f} {self.runtime.unit}")
        elif field.startswith("enable_"): self._refresh_enable(name, field, value)
        self._refresh_connection(name)

    def _on_connection(self, name, field, connected):
        self.connected[(name, field)] = connected; self._refresh_connection(name)
        if field.startswith("enable_"): self.enable_buttons[(name, field)].setEnabled(bool(connected) and self._can_write())
        if field == "voltage_set": self.values[(name, "target_widget")].setEnabled(bool(connected) and self._can_write())

    def _refresh_connection(self, name):
        states = [self.connected.get((name, f), False) for f in ("voltage_set","voltage_readback","enable_1","enable_2")]
        item = self.table.item(self._row(name), 5); item.setText("Connected" if any(states) else "Disconnected"); item.setForeground(QColor("#45d0bc" if any(states) else "#e37878"))

    def _refresh_enable(self, name, field, value):
        label = self.status_labels[(name, field)]; enabled = math.isclose(value, 1.0)
        label.setText("Enable" if enabled else "Disable"); label.setStyleSheet("color:#45d0bc;font-weight:700;" if enabled else "color:#e37878;font-weight:700;")
        self.enable_buttons[(name, field)].setText("Disable" if enabled else "Enable")

    def _row(self, name): return next(i for i, mod in enumerate(self.runtime.modulators) if mod.name == name)
    def _can_write(self): return self.runtime.context.control_backend.name == "real" and self.worker is None

    def _fill_all(self):
        value = self.global_spin.value()
        for mod in self.runtime.modulators:
            with QSignalBlocker(self.values[(mod.name, "target_widget")]): self.values[(mod.name, "target_widget")].setValue(value)
            self.values[(mod.name, "target")] = value
        self.statusBar().showMessage(f"Filled {value:g} {self.runtime.unit} into all rows")

    def _apply_all(self):
        operations = [(m.name, "voltage_set", m.voltage_set, self.values.get((m.name,"target"), self.values.get((m.name,"voltage_set"), 0.0))) for m in self.runtime.modulators]
        self._start_worker(operations, "write")

    def _read_back(self):
        for mod in self.runtime.modulators:
            self.values[(mod.name, "target_widget")].setEnabled(False)
        self.monitor.bind(); self.statusBar().showMessage("Refreshing HV readbacks")

    def _toggle_enable(self, name, field):
        current = self.values.get((name, field), 0.0); self._batch_enable(field, 0 if math.isclose(current,1) else 1, f"{name} {field}", names=[name])

    def _batch_enable(self, field, value, label, names=None):
        names = names or [m.name for m in self.runtime.modulators]
        if names == [m.name for m in self.runtime.modulators] and QMessageBox.question(self, "Confirm enable operation", f"{label} for {len(names)} modulators?", QMessageBox.Yes|QMessageBox.No, QMessageBox.No) != QMessageBox.Yes: return
        mods = {m.name:m for m in self.runtime.modulators}
        self._start_worker([(name, field, getattr(mods[name], field), value) for name in names], "write")

    def _start_worker(self, operations, mode, path=None):
        if self.worker is not None: return
        if not self._can_write() and mode == "write":
            QMessageBox.warning(self, "HV control", "Writes are blocked for the selected control backend.")
            return
        self._set_busy(True); self.worker = BatchWorker(operations, mode=mode, path=path, parent=self); self.worker.progress.connect(self._worker_progress); self.worker.finished_result.connect(self._worker_finished); self.worker.finished.connect(self._worker_deleted); self.worker.start()

    def _worker_progress(self, index, total, message):
        self.statusBar().showMessage(message)
        parts = message.split(": ", 1)
        if len(parts) == 2:
            name = parts[1].split(" ", 1)[0]
            if name in {m.name for m in self.runtime.modulators}:
                self.table.item(self._row(name), 6).setText(message.split(": ", 1)[1])

    def _worker_finished(self, success, message):
        self.statusBar().showMessage(message)
        for row in range(self.table.rowCount()):
            self.table.item(row, 6).setText("Completed" if success else "Failed")
        self.monitor.bind()
        if not success: QMessageBox.warning(self, "HV operation failed", message)

    def _worker_deleted(self):
        if self.worker is not None: self.worker.deleteLater(); self.worker = None
        self._set_busy(False)

    def _set_busy(self, busy):
        for widget in (self.save_button,self.restore_button,self.fill_button,self.apply_button,self.read_button): widget.setEnabled(not busy)
        self.global_spin.setEnabled(not busy)

    def _snapshot_dir(self):
        path = Path(__file__).resolve().parent / "runtime" / "snapshots"; path.mkdir(parents=True, exist_ok=True); return path

    def _save(self):
        directory = self._snapshot_dir(); path, _ = QFileDialog.getSaveFileName(self, "Save HV settings", str(directory / "hv_settings.json"), "JSON files (*.json)")
        if not path: return
        ops = [(m.name, "voltage_set", m.voltage_set, None) for m in self.runtime.modulators]
        self._start_worker(ops, "save", {"file":path,"machine":self.runtime.context.machine.id,"backend":self.runtime.context.control_backend.name})

    def _restore(self):
        directory = self._snapshot_dir(); path, _ = QFileDialog.getOpenFileName(self, "Restore HV settings", str(directory), "JSON files (*.json)")
        if not path: return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if data.get("version") != 1 or data.get("machine") != self.runtime.context.machine.id or data.get("backend") != self.runtime.context.control_backend.name:
                raise ValueError("Snapshot version, machine, or backend does not match this runtime")
            rows = data["modulators"]
            mods = {m.name:m for m in self.runtime.modulators}; operations=[]; changes=[]
            for name, raw in rows.items():
                if name not in mods: continue
                value=float(raw["voltage_set"])
                if not self.runtime.low <= value <= self.runtime.high: raise ValueError(f"{name}: voltage out of range")
                operations.append((name,"voltage_set",mods[name].voltage_set,value))
                current = self.values.get((name, "voltage_set"))
                if current is None or not math.isclose(current, value, abs_tol=1e-9): changes.append(f"{name}: {current if current is not None else '--'} -> {value:g} {self.runtime.unit}")
            if len(operations) != len(self.runtime.modulators): raise ValueError("Snapshot does not contain all configured modulators")
        except Exception as exc:
            QMessageBox.warning(self, "Invalid HV snapshot", str(exc)); return
        preview = "\n".join(changes[:8])
        if len(changes) > 8: preview += f"\n... and {len(changes) - 8} more"
        prompt = f"Restore {len(operations)} HV setpoints ({len(changes)} changed)?"
        if preview: prompt += "\n\n" + preview
        if QMessageBox.question(self, "Confirm restore", prompt, QMessageBox.Yes|QMessageBox.No, QMessageBox.No) == QMessageBox.Yes: self._start_worker(operations, "write")

    def _toggle_theme(self): self._theme = "light" if self._theme == "dark" else "dark"; self._apply_theme()
    def _apply_theme(self): self.setStyleSheet(stylesheet(DARK if self._theme == "dark" else LIGHT)); self.theme_button.setText("☀" if self._theme == "dark" else "☾")
    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning(): event.ignore(); self.statusBar().showMessage("Wait for the current operation to finish"); return
        self.monitor.close(); super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    try: runtime = load_hv_runtime()
    except Exception as exc: QMessageBox.critical(None, "HV Modulator Control", str(exc)); return 2
    window = HvControlWindow(runtime); window.show(); return app.exec_()

if __name__ == "__main__": raise SystemExit(main())
