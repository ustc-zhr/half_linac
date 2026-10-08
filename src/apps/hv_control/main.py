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

from PyQt5.QtCore import QSignalBlocker, Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QApplication, QFileDialog, QCheckBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel,
    QMainWindow, QMessageBox, QPushButton, QStatusBar, QTableWidget,
    QTableWidgetItem, QToolButton, QVBoxLayout, QWidget, QSizePolicy,
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
QLabel {{ background:transparent; }} QLabel#subtitle, QLabel#sectionLabel {{ color:{p["muted"]}; }} QHeaderView::section {{ background:{p["panel"]}; color:{p["muted"]}; padding:8px 10px; border:0; border-bottom:1px solid {p["border"]}; font-weight:600; }}
QPushButton, QToolButton, QDoubleSpinBox {{ background:{p["input"]}; color:{p["text"]}; border:1px solid {p["border"]}; border-radius:6px; min-height:30px; padding:2px 10px; }}
QPushButton#toolbarAction {{ font-weight:700; }}
QPushButton#enableToggleButton {{ min-height:22px; padding:0 6px; }}
QCheckBox#selectionCheck {{ spacing:7px; font-weight:700; color:{p["text"]}; }}
QCheckBox#selectionCheck::indicator {{ width:18px; height:18px; border-radius:4px; border:1px solid {p["border"]}; background:{p["input"]}; }}
QCheckBox#selectionCheck::indicator:hover {{ border-color:{p["accent"]}; }}
QCheckBox#selectionCheck::indicator:checked {{ background:{p["accent"]}; border-color:{p["accent"]}; }}
QCheckBox#selectionCheck::indicator:indeterminate {{ background:{p["accent"]}; border-color:{p["accent"]}; }}
QPushButton:hover, QToolButton:hover, QDoubleSpinBox:focus {{ border-color:{p["accent"]}; }} QPushButton:disabled {{ color:{p["muted"]}; }}
QTableWidget {{ background:{p["input"]}; alternate-background-color:{p["panel"]}; gridline-color:{p["border"]}; border:1px solid {p["border"]}; selection-background-color:{p["panel"]}; }}
QTableWidget::item {{ padding:4px 8px; }}
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
        self.batch_enable_buttons = {}
        self.selection_boxes = {}
        self.selection_count = None
        self.select_all_box = None
        self._build_ui()
        self._apply_theme()
        self.monitor.value_changed.connect(self._on_value)
        self.monitor.connection_changed.connect(self._on_connection)
        self.monitor.bind()

    def _build_ui(self):
        self.setWindowTitle(f"{self.runtime.context.machine.display_name} - HV Modulator Control")
        self.resize(1420, 800)
        self.setMinimumSize(1180, 650)
        root = QWidget(self)
        outer = QVBoxLayout(root); outer.setContentsMargins(12, 10, 12, 8); outer.setSpacing(8)
        heading = QHBoxLayout()
        title_box = QVBoxLayout(); title_box.setSpacing(1)
        title = QLabel("HV Modulator Control", root); title.setStyleSheet("font-size:22px;font-weight:700;")
        subtitle = QLabel("20 modulators  ·  setpoints, readback, and enable state", root); subtitle.setObjectName("subtitle"); subtitle.setStyleSheet("font-size:11px;")
        title_box.addWidget(title); title_box.addWidget(subtitle); heading.addLayout(title_box); heading.addStretch(1)
        self.save_button = QPushButton("Save", root); self.restore_button = QPushButton("Restore", root)
        self.save_button.setObjectName("toolbarAction"); self.restore_button.setObjectName("toolbarAction")
        self.save_button.clicked.connect(self._save); self.restore_button.clicked.connect(self._restore)
        heading.addWidget(self.save_button); heading.addWidget(self.restore_button)
        heading.addWidget(RuntimeContextWidget(machine_id=self.runtime.context.machine.id, machine_display_name=self.runtime.context.machine.display_name, control_backend=self.runtime.context.control_backend.name, parent=root))
        self.theme_button = QToolButton(root); self.theme_button.setFixedSize(32, 32); self.theme_button.clicked.connect(self._toggle_theme); heading.addWidget(self.theme_button)
        outer.addLayout(heading)

        controls = QFrame(root); controls.setObjectName("panel"); row = QHBoxLayout(controls); row.setContentsMargins(12,10,12,10); row.setSpacing(7)
        set_label = QLabel("SETPOINT", controls); set_label.setObjectName("sectionLabel"); set_label.setStyleSheet("font-weight:700;font-size:10px;"); row.addWidget(set_label)
        self.select_all_box = QCheckBox("Select all", controls); self.select_all_box.setObjectName("selectionCheck"); self.select_all_box.setTristate(True); self.select_all_box.setChecked(True); self.select_all_box.clicked.connect(self._set_all_selection); row.addWidget(self.select_all_box)
        self.selection_count = QLabel("20 selected", controls); self.selection_count.setObjectName("sectionLabel"); row.addWidget(self.selection_count)
        self.global_spin = QDoubleSpinBox(controls); self.global_spin.setRange(self.runtime.low, self.runtime.high); self.global_spin.setDecimals(1); self.global_spin.setSuffix(f" {self.runtime.unit}"); self.global_spin.setKeyboardTracking(False); row.addWidget(self.global_spin)
        self.fill_button = QPushButton("Fill selected", controls); self.fill_button.setObjectName("toolbarAction"); self.fill_button.clicked.connect(self._fill_all); row.addWidget(self.fill_button)
        self.apply_button = QPushButton("Apply selected", controls); self.apply_button.setObjectName("toolbarAction"); self.apply_button.clicked.connect(self._apply_all); row.addWidget(self.apply_button)
        self.read_button = QPushButton("Refresh", controls); self.read_button.setObjectName("toolbarAction"); self.read_button.clicked.connect(self._read_back); row.addWidget(self.read_button)
        divider = QFrame(controls); divider.setFrameShape(QFrame.VLine); divider.setStyleSheet("color:#2a3943;"); row.addWidget(divider)
        enable_label = QLabel("ENABLE", controls); enable_label.setObjectName("sectionLabel"); enable_label.setStyleSheet("font-weight:700;font-size:10px;"); row.addWidget(enable_label)
        for field, title in (("enable_1", "Modulators"), ("enable_2", "HV")):
            button = QPushButton(f"{title}: All ON", controls)
            button.setObjectName("toolbarAction")
            button.setEnabled(False)
            button.clicked.connect(lambda _=False, f=field: self._toggle_all(f))
            row.addWidget(button)
            self.batch_enable_buttons[field] = button
        row.addStretch(1); outer.addWidget(controls)

        self.table = QTableWidget(len(self.runtime.modulators), 7, root)
        self.table.setHorizontalHeaderLabels(("MODULATOR", "HV SET", "HV READBACK", "MODULATOR ENABLE", "HV ENABLE", "CONNECTION", "OPERATION"))
        self.table.verticalHeader().setVisible(False); self.table.verticalHeader().setDefaultSectionSize(42); self.table.setAlternatingRowColors(True); self.table.setSelectionMode(QTableWidget.NoSelection); self.table.setFocusPolicy(Qt.NoFocus)
        header = self.table.horizontalHeader(); header.setFixedHeight(36); header.setDefaultAlignment(Qt.AlignCenter | Qt.AlignVCenter)
        widths = (180, 190, 190, 205, 205, 150, 300)
        for i, width in enumerate(widths): self.table.setColumnWidth(i, width)
        header.setStretchLastSection(True)
        for row_index, mod in enumerate(self.runtime.modulators): self._build_row(row_index, mod)
        outer.addWidget(self.table, 1)
        self.setCentralWidget(root); self.setStatusBar(QStatusBar(self)); self.statusBar().showMessage("Connecting to HV PVs")

    def _build_row(self, row, mod):
        select = QCheckBox(self.table)
        select.setObjectName("selectionCheck")
        select.setChecked(True)
        select.stateChanged.connect(lambda _state, n=mod.name: self._selection_changed(n))
        select_cell = QWidget(self.table); select_layout = QHBoxLayout(select_cell)
        select_layout.setContentsMargins(8, 0, 8, 0); select_layout.setSpacing(8); select_layout.addWidget(select)
        select_cell_label = QLabel(mod.name, select_cell); select_cell_label.setStyleSheet("font-weight:700;"); select_layout.addWidget(select_cell_label); select_layout.addStretch(1)
        self.table.setCellWidget(row, 0, select_cell); self.selection_boxes[mod.name] = select
        set_spin = QDoubleSpinBox(self.table); set_spin.setAlignment(Qt.AlignLeft | Qt.AlignVCenter); set_spin.setRange(self.runtime.low, self.runtime.high); set_spin.setDecimals(1); set_spin.setSuffix(f" {self.runtime.unit}"); set_spin.setKeyboardTracking(False); set_spin.setEnabled(False); self.table.setCellWidget(row, 1, set_spin)
        for col in (2, 5, 6): self.table.setItem(row, col, QTableWidgetItem("--"))
        for field, col in (("enable_1",3),("enable_2",4)):
            cell = QWidget(self.table); layout = QHBoxLayout(cell); layout.setContentsMargins(8,3,8,3); layout.setSpacing(6)
            label = QLabel("--", cell); label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter); button = QPushButton("ON", cell); button.setObjectName("enableToggleButton"); button.setFixedSize(82, 26); button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed); button.setEnabled(False); button.clicked.connect(lambda _=False, n=mod.name, f=field: self._toggle_enable(n, f))
            layout.addWidget(label, 1); layout.addWidget(button, 0); self.table.setCellWidget(row, col, cell); self.status_labels[(mod.name, field)] = label; self.enable_buttons[(mod.name, field)] = button
        set_spin.valueChanged.connect(lambda value, n=mod.name: self._set_target(n, value))
        self.values[(mod.name, "target_widget")] = set_spin

    def _selected_names(self):
        return [name for name, box in self.selection_boxes.items() if box.isChecked()]

    def _selection_changed(self, _name=None):
        selected = self._selected_names()
        self.selection_count.setText(f"{len(selected)} selected")
        if self.select_all_box is not None:
            self.select_all_box.blockSignals(True)
            self.select_all_box.setCheckState(Qt.Checked if len(selected) == len(self.runtime.modulators) else Qt.PartiallyChecked if selected else Qt.Unchecked)
            self.select_all_box.blockSignals(False)
        for field in self.batch_enable_buttons:
            self._refresh_batch_enable(field)
        self.apply_button.setEnabled(bool(selected) and self.worker is None)
        self.fill_button.setEnabled(bool(selected) and self.worker is None)

    def _set_all_selection(self, checked):
        checked = bool(checked)
        for box in self.selection_boxes.values():
            box.blockSignals(True)
            box.setChecked(checked)
            box.blockSignals(False)
        self._selection_changed()

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
        elif field == "voltage_readback": self.table.item(row, 2).setText(f"{value:.1f} {self.runtime.unit}")
        elif field in ("enable_1_state", "enable_2_state"):
            command = field.removesuffix("_state")
            self._refresh_enable(name, command, value)
            self._refresh_enable_button(name, command)
            self._refresh_batch_enable(command)
        self._refresh_connection(name)

    def _on_connection(self, name, field, connected):
        self.connected[(name, field)] = connected
        if not connected and field in ("enable_1_state", "enable_2_state"):
            self.values.pop((name, field), None)
            label = self.status_labels[(name, field.removesuffix("_state"))]
            label.setText("--")
            label.setStyleSheet("")
            self.enable_buttons[(name, field.removesuffix("_state"))].setText("ON")
        self._refresh_connection(name)
        if field in ("enable_1", "enable_2", "enable_1_state", "enable_2_state"):
            command = field.removesuffix("_state")
            self._refresh_enable_button(name, command)
            self._refresh_batch_enable(command)
        if field == "voltage_set": self.values[(name, "target_widget")].setEnabled(bool(connected) and self._can_write())

    def _refresh_connection(self, name):
        states = [self.connected.get((name, f), False) for f in ("voltage_set","voltage_readback","enable_1_state","enable_2_state")]
        item = self.table.item(self._row(name), 5); item.setText("Connected" if any(states) else "Disconnected"); item.setForeground(QColor("#45d0bc" if any(states) else "#e37878"))

    def _refresh_enable(self, name, field, value):
        label = self.status_labels[(name, field)]
        if value not in (0.0, 1.0):
            label.setText("Unknown")
            label.setStyleSheet("")
            self.enable_buttons[(name, field)].setText("ON")
            return
        enabled = value == 1.0
        label.setText("ON" if enabled else "OFF")
        label.setStyleSheet("color:#45d0bc;font-weight:700;" if enabled else "color:#e37878;font-weight:700;")
        self.enable_buttons[(name, field)].setText("OFF" if enabled else "ON")

    def _refresh_enable_button(self, name, field):
        state = self.values.get((name, f"{field}_state"))
        self.enable_buttons[(name, field)].setEnabled(
            self.connected.get((name, field), False)
            and self.connected.get((name, f"{field}_state"), False)
            and state in (0.0, 1.0)
            and self._can_write()
        )

    def _refresh_batch_enable(self, field):
        button = self.batch_enable_buttons[field]
        names = self._selected_names()
        states = [self.values.get((name, f"{field}_state")) for name in names]
        ready = all(
            self.connected.get((name, field), False)
            and self.connected.get((name, f"{field}_state"), False)
            and state in (0.0, 1.0)
            for name, state in zip(names, states)
        )
        title = "Modulators" if field == "enable_1" else "HV"
        button.setText(f"{title}: All {'OFF' if ready and all(state == 1.0 for state in states) else 'ON'}")
        button.setEnabled(bool(names) and ready and self._can_write())
        button.setToolTip(f"{sum(state == 1.0 for state in states)} of {len(names)} selected ON" if ready else "Select connected modulators with valid status PVs")

    def _row(self, name): return next(i for i, mod in enumerate(self.runtime.modulators) if mod.name == name)
    def _can_write(self): return self.runtime.context.control_backend.name == "real" and self.worker is None

    def _fill_all(self):
        selected = set(self._selected_names())
        value = self.global_spin.value()
        for mod in self.runtime.modulators:
            if mod.name not in selected:
                continue
            with QSignalBlocker(self.values[(mod.name, "target_widget")]): self.values[(mod.name, "target_widget")].setValue(value)
            self.values[(mod.name, "target")] = value
        self.statusBar().showMessage(f"Filled {value:g} {self.runtime.unit} into {len(selected)} selected rows")

    def _apply_all(self):
        selected = set(self._selected_names())
        operations = [(m.name, "voltage_set", m.voltage_set, self.values.get((m.name,"target"), self.values.get((m.name,"voltage_set"), 0.0))) for m in self.runtime.modulators if m.name in selected]
        self._start_worker(operations, "write")

    def _read_back(self):
        for mod in self.runtime.modulators:
            self.values[(mod.name, "target_widget")].setEnabled(False)
        self.monitor.bind(); self.statusBar().showMessage("Refreshing HV readbacks")

    def _toggle_all(self, field):
        if not self.batch_enable_buttons[field].isEnabled(): return
        names = self._selected_names()
        states = [self.values[(name, f"{field}_state")] for name in names]
        target = 0 if all(state == 1.0 for state in states) else 1
        group = "modulator" if field == "enable_1" else "high voltage"
        current = "all ON" if all(state == 1.0 for state in states) else "mixed" if any(state == 1.0 for state in states) else "all OFF"
        self._batch_enable(field, target, f"Turn selected {group} enables {'ON' if target else 'OFF'} (currently {current})", names=names)

    def _toggle_enable(self, name, field):
        current = self.values.get((name, f"{field}_state"))
        if current not in (0.0, 1.0): return
        self._batch_enable(field, 0 if current == 1.0 else 1, f"{name} {field}", names=[name])

    def _batch_enable(self, field, value, label, names=None):
        names = self._selected_names() if names is None else names
        if len(names) > 1 and QMessageBox.question(self, "Confirm enable operation", f"{label} for {len(names)} selected modulators?", QMessageBox.Yes|QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
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
        for widget in (self.save_button, self.restore_button, self.fill_button, self.apply_button, self.read_button):
            widget.setEnabled(not busy)
        self.global_spin.setEnabled(not busy)
        if self.select_all_box is not None:
            self.select_all_box.setEnabled(not busy)
        if not busy:
            self._selection_changed()
        if busy:
            for button in (*self.batch_enable_buttons.values(), *self.enable_buttons.values()):
                button.setEnabled(False)
        else:
            for field in self.batch_enable_buttons:
                self._refresh_batch_enable(field)
            for name, field in self.enable_buttons:
                self._refresh_enable_button(name, field)

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
