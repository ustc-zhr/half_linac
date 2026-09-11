"""Standalone HALF quadrupole energy reference panel."""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

_ROOT = next(parent for parent in Path(__file__).resolve().parents
             if (parent / "repo_bootstrap.py").is_file())
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from repo_bootstrap import ensure_repo_import_path
ensure_repo_import_path(__file__)
from half_linac.src.shared.app_theme import resolve_initial_theme

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
    QHBoxLayout, QHeaderView, QLabel, QMainWindow, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from control import LABELS, PVS, DemoBackend, EpicsBackend, energy, execute, same


class Worker(QThread):
    done = pyqtSignal(object)

    def __init__(self, operation):
        super().__init__()
        self.operation = operation

    def run(self):
        try:
            self.done.emit((self.operation(), None))
        except Exception as exc:
            self.done.emit((None, str(exc)))


class Window(QMainWindow):
    def __init__(self, live=False):
        super().__init__()
        self.backend = EpicsBackend() if live else DemoBackend()
        self.mode = "EPICS · Writes update quadrupole currents" if live else "DEMO · No PV connection"
        self.current, self.targets, self.baselines = {}, {}, {}
        self.previous = {}
        self.worker = None
        self.pending_nudges = []
        self.arrow_buttons = []
        self.setWindowTitle("HALF · Q Energy Reference")
        self.resize(860, 520)
        self.theme = resolve_initial_theme()
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        heading = QHBoxLayout()
        title = QLabel("Q Energy Reference")
        title.setObjectName("title")
        heading.addWidget(title)
        heading.addStretch()
        mode = QLabel(self.mode)
        mode.setObjectName("mode")
        heading.addWidget(mode)
        self.theme_button = QPushButton()
        self.theme_button.setFixedWidth(30)
        self.theme_button.clicked.connect(self.toggle_theme)
        heading.addWidget(self.theme_button)
        layout.addLayout(heading)
        bar = QHBoxLayout()
        self.controls = []

        def button(text, callback, row):
            widget = QPushButton(text)
            widget.clicked.connect(callback)
            row.addWidget(widget)
            self.controls.append(widget)
            return widget

        button("All", lambda: self.select(0), bar)
        button("None", lambda: self.select(9), bar)
        button("Downstream", self.select_downstream, bar)
        self.operation = QComboBox()
        self.operation.addItems(["Offset / MeV", "Scale ×"])
        bar.addWidget(self.operation)
        self.amount = QDoubleSpinBox()
        self.amount.setRange(-100000, 100000)
        self.amount.setDecimals(4)
        self.amount.setMaximumWidth(110)
        self.operation.currentIndexChanged.connect(
            lambda index: self.amount.setValue(1 if index else 0))
        bar.addWidget(self.amount)
        button("Set targets", self.generate, bar)
        layout.addLayout(bar)
        nudge_bar = QHBoxLayout()
        nudge_bar.addWidget(QLabel("Step / MeV"))
        self.step = QDoubleSpinBox()
        self.step.setDecimals(4)
        self.step.setRange(0.0001, 100000)
        self.step.setValue(1)
        self.step.setMaximumWidth(110)
        self.controls.append(self.step)
        nudge_bar.addWidget(self.step)
        self.decrease = button("◀", lambda: self.nudge(-1), nudge_bar)
        self.increase = button("▶", lambda: self.nudge(1), nudge_bar)
        for arrow in (self.decrease, self.increase):
            self.configure_arrow(arrow)
            arrow.setToolTip("Write selected energies immediately: current value ± step")
        nudge_bar.addStretch()
        nudge_bar.addWidget(QLabel("Arrows write immediately"))
        layout.addLayout(nudge_bar)
        self.table = QTableWidget(9, 7)
        self.table.setHorizontalHeaderLabels(["Use", "Section", "Current / MeV", "Target / MeV", "Δ / MeV", "Status", "Adjust"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        # Keep logical column indices stable while placing nudges next to targets.
        self.table.horizontalHeader().moveSection(6, 4)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setMinimumHeight(310)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.checks = []
        for row, name in enumerate(PVS):
            check = QCheckBox()
            check.setChecked(True)
            check.stateChanged.connect(self.summary)
            self.checks.append(check)
            self.table.setCellWidget(row, 0, check)
            for col, value in enumerate([LABELS[name], "—", "", "—", "Connecting"], 1):
                item = QTableWidgetItem(value)
                item.setToolTip(name)
                if col != 3:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if col in (2, 3, 4):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(row, col, item)
            arrows = QWidget()
            arrow_layout = QHBoxLayout(arrows)
            arrow_layout.setContentsMargins(2, 0, 2, 0)
            arrow_layout.setSpacing(2)
            for symbol, direction in (("◀", -1), ("▶", 1)):
                arrow = button(symbol, lambda checked=False, n=name, d=direction: self.nudge(d, [n]), arrow_layout)
                arrow.setFixedWidth(30)
                self.configure_arrow(arrow)
                arrow.setToolTip(f"{'Decrease' if direction < 0 else 'Increase'} {name} immediately by one step")
            self.table.setCellWidget(row, 6, arrows)
        self.table.cellChanged.connect(self.edited)
        layout.addWidget(self.table)
        self.status = QLabel("Reading energy references…")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        bottom = QHBoxLayout()
        button("Load…", self.load_scheme, bottom)
        button("Save…", self.save_scheme, bottom)
        button("Restore last", self.restore_previous, bottom)
        button("Reset edits", self.clear, bottom)
        self.apply_button = button("Apply", self.apply, bottom)
        self.apply_button.setObjectName("primary")
        layout.addLayout(bottom)
        layout.addWidget(QLabel("Energy PV confirmation only. Check magnet readbacks in the control panel."))
        self.setCentralWidget(root)
        self.apply_theme()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1500)
        self.refresh()

    def configure_arrow(self, arrow):
        arrow.setAutoRepeat(True)
        arrow.setAutoRepeatDelay(400)
        arrow.setAutoRepeatInterval(150)
        self.arrow_buttons.append(arrow)

    def toggle_theme(self):
        self.theme = "light" if self.theme == "dark" else "dark"
        self.apply_theme()

    def apply_theme(self):
        dark = self.theme == "dark"
        window, panel, border, text, muted, accent, pending = (
            ("#0f1519", "#172027", "#2a3943", "#e6edf2", "#91a2ad", "#45d0bc", "#393322")
            if dark else
            ("#f2ede5", "#fffdf9", "#d7cec1", "#2c3942", "#746c62", "#2d7f6d", "#fff2cc")
        )
        self.pending_color = pending
        self.panel_color = panel
        self.setStyleSheet(f'''
            QMainWindow, QWidget {{ background: {window}; color: {text};
                font-family: "IBM Plex Sans", "Segoe UI", sans-serif; font-size: 12px; }}
            QLabel {{ background: transparent; }}
            QLabel#title {{ font-size: 19px; font-weight: 700; }}
            QLabel#mode {{ color: {muted}; font-size: 11px; }}
            QPushButton, QComboBox, QDoubleSpinBox {{ background: {panel};
                border: 1px solid {border}; border-radius: 5px; min-height: 24px; padding: 1px 6px; }}
            QPushButton:hover, QComboBox:hover, QDoubleSpinBox:focus {{ border-color: {accent}; }}
            QPushButton:disabled {{ color: {muted}; }}
            QPushButton#primary {{ background: {accent}; color: {window}; font-weight: 700; padding: 1px 18px; }}
            QTableWidget {{ background: {panel}; alternate-background-color: {window};
                border: 1px solid {border}; border-radius: 6px; selection-background-color: {border};
                selection-color: {text}; }}
            QTableWidget::item {{ padding: 3px 5px; }}
            QHeaderView::section {{ background: {window}; color: {muted};
                border: none; border-bottom: 1px solid {border}; padding: 7px 5px; font-weight: 600; }}
            QLineEdit {{ background: {panel}; color: {text}; selection-background-color: {border}; }}
        ''')
        self.theme_button.setText("☀" if dark else "☾")
        self.theme_button.setToolTip("Switch to light theme" if dark else "Switch to dark theme")
        for row in range(9):
            self.update_delta(row)

    def run_job(self, operation, callback, writing=False):
        if self.worker is not None:
            return
        if writing:
            self.table.setEditTriggers(QTableWidget.NoEditTriggers)
            for check in self.checks:
                check.setEnabled(False)
            for control in self.controls + [self.operation, self.amount]:
                if control not in self.arrow_buttons:
                    control.setEnabled(False)
        self.worker = Worker(operation)
        self.worker.done.connect(callback)
        self.worker.finished.connect(self.job_finished)
        self.worker.start()

    def job_finished(self):
        self.worker.deleteLater()
        self.worker = None
        self.table.setEnabled(True)
        self.table.setEditTriggers(QTableWidget.DoubleClicked | QTableWidget.EditKeyPressed | QTableWidget.AnyKeyPressed)
        for check in self.checks:
            check.setEnabled(True)
        for control in self.controls + [self.operation, self.amount]:
            control.setEnabled(True)
        if self.pending_nudges:
            names, delta = self.pending_nudges.pop(0)
            self.nudge(1, names, delta=delta)

    def refresh(self):
        def read():
            values = {}
            for name in PVS:
                try:
                    values[name] = (self.backend.read(name), "")
                except Exception as exc:
                    values[name] = (None, str(exc))
            return values
        self.run_job(read, self.read_done)

    def read_done(self, result):
        values, error = result
        if error:
            self.status.setText(error)
            return
        for row, name in enumerate(PVS):
            value, error = values[name]
            self.current[name] = value
            self.set_cell(row, 2, "—" if value is None else f"{value:.4f}")
            if error:
                self.set_cell(row, 5, error)
            elif name not in self.targets and self.table.item(row, 5).text() not in ("Confirmed", "Not executed") and not self.table.item(row, 5).text().startswith("Unconfirmed"):
                self.set_cell(row, 5, "Ready")
            self.update_delta(row)

    def set_cell(self, row, col, text):
        self.table.blockSignals(True)
        self.table.item(row, col).setText(text)
        self.table.blockSignals(False)

    def edited(self, row, col):
        if col != 3:
            return
        name = PVS[row]
        text = self.table.item(row, col).text().strip()
        if not text:
            self.targets.pop(name, None)
            self.baselines.pop(name, None)
        else:
            self.targets[name] = text
            self.baselines.setdefault(name, self.current.get(name))
        self.set_cell(row, 5, "Pending")
        self.update_delta(row)
        self.summary()

    def update_delta(self, row):
        name = PVS[row]
        text = "—"
        try:
            if name in self.targets:
                target = energy(self.targets[name])
                current = self.current.get(name)
                text = f"{target - current:+.4f}" if current is not None else "—"
                state = self.table.item(row, 5).text()
                if state != "Not executed" and not state.startswith("Unconfirmed"):
                    self.set_cell(row, 5, "Pending" if current == self.baselines.get(name) else "Changed externally — reset targets")
        except ValueError:
            self.set_cell(row, 5, "Target must be finite and positive")
        self.set_cell(row, 4, text)
        self.table.item(row, 3).setBackground(QColor(self.pending_color if name in self.targets else self.panel_color))

    def select(self, start):
        for row, check in enumerate(self.checks):
            check.setChecked(row >= start)

    def select_downstream(self):
        self.select(max(0, self.table.currentRow()))

    def selected(self):
        return [name for name, check in zip(PVS, self.checks) if check.isChecked()]

    def summary(self):
        if hasattr(self, "status"):
            count = sum(name in self.targets for name in self.selected())
            self.status.setText(f"{len(self.selected())} selected · {count} staged")

    def stage(self, values):
        for name, value in values.items():
            row = PVS.index(name)
            self.baselines[name] = self.current.get(name)
            self.table.item(row, 3).setText(f"{value:.4f}")

    def generate(self):
        try:
            values = {}
            for name in self.selected():
                current = self.current.get(name)
                if current is None:
                    raise ValueError(f"{name} has no valid current value")
                amount = self.amount.value()
                values[name] = energy(current * amount if self.operation.currentIndex() else current + amount)
            self.stage(values)
        except ValueError as exc:
            self.status.setText(str(exc))

    def nudge(self, direction, names=None, delta=None):
        names = tuple(self.selected() if names is None else names)
        delta = direction * self.step.value() if delta is None else delta
        if self.worker is not None:
            if self.pending_nudges and self.pending_nudges[-1][0] == names:
                self.pending_nudges[-1] = (names, round(self.pending_nudges[-1][1] + delta, 4))
            else:
                self.pending_nudges.append((names, delta))
            self.status.setText("Adjusting… additional steps queued")
            return
        if delta == 0:
            if self.pending_nudges:
                next_names, next_delta = self.pending_nudges.pop(0)
                self.nudge(1, next_names, delta=next_delta)
            return
        try:
            if not names:
                raise ValueError("Select an energy reference first")
            plan = []
            for name in names:
                current = self.current.get(name)
                if current is None:
                    raise ValueError(f"{name} has no valid current value")
                target = energy(round(current + delta, 4))
                plan.append((name, current, target))
        except ValueError as exc:
            self.pending_nudges.clear()
            for arrow in self.arrow_buttons:
                arrow.setDown(False)
            self.status.setText(str(exc))
            return
        self.execute_plan(plan, preserve_targets=True)

    def clear(self):
        self.targets.clear()
        self.baselines.clear()
        for row in range(9):
            self.set_cell(row, 3, "")
            self.set_cell(row, 5, "Edits cleared")
            self.update_delta(row)
        self.summary()

    def apply(self):
        if self.worker is not None:
            self.status.setText("Reading; please retry Apply shortly")
            return
        try:
            plan = []
            for name in self.selected():
                if name not in self.targets:
                    continue
                target = energy(self.targets[name])
                before = self.baselines.get(name)
                if before is None:
                    raise ValueError(f"{name} has no baseline; regenerate targets")
                if not same(before, target):
                    plan.append((name, before, target))
            if not plan:
                raise ValueError("No changes to apply")
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.execute_plan(plan)

    def execute_plan(self, plan, preserve_targets=False):
        self.previous = {name: before for name, before, target in plan}
        for name, _, _ in plan:
            self.set_cell(PVS.index(name), 5, "Not executed")

        def write():
            outcomes = {}
            error = None
            try:
                execute(self.backend, plan, lambda name, state: outcomes.update({name: state}))
            except Exception as exc:
                error = str(exc)
            record = {"time": datetime.now().isoformat(), "mode": self.mode,
                      "plan": plan, "results": outcomes, "error": error}
            try:
                folder = Path(__file__).resolve().parents[3] / "logs" / "energy_buttons"
                folder.mkdir(parents=True, exist_ok=True)
                with (folder / "operations.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            except OSError as exc:
                error = f"{error or 'Completed'}; Could not save operation log: {exc}"
            return outcomes, error

        def completed(result):
            payload, failure = result
            outcomes, error = payload if payload else ({}, failure)
            if error:
                self.pending_nudges.clear()
                for arrow in self.arrow_buttons:
                    arrow.setDown(False)
            requested = {name: target for name, _, target in plan}
            for name, state in outcomes.items():
                row = PVS.index(name)
                if state == "Confirmed":
                    self.current[name] = requested[name]
                    self.set_cell(row, 2, f"{requested[name]:.4f}")
                    if not preserve_targets:
                        self.targets.pop(name, None)
                        self.baselines.pop(name, None)
                        self.set_cell(row, 3, "")
                else:
                    # An uncertain write must be read again before another nudge.
                    self.current[name] = None
                    self.set_cell(row, 2, "—")
                self.set_cell(row, 5, state)
                self.update_delta(row)
            self.status.setText(error or f"Confirmed {len(outcomes)} energy PVs.")
        self.status.setText("Checking and applying…")
        self.run_job(write, completed, writing=True)

    def restore_previous(self):
        if not self.previous:
            self.status.setText("No previous settings in this session")
            return
        self.clear()
        for name, check in zip(PVS, self.checks):
            check.setChecked(name in self.previous)
        self.stage(self.previous)
        self.status.setText("Previous values staged. Apply to restore energy references.")

    def save_scheme(self):
        try:
            values = {name: energy(self.targets.get(name, self.current.get(name))) for name in PVS}
        except (ValueError, TypeError):
            self.status.setText("All nine energy values must be valid")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save energy targets", "energy.json", "JSON (*.json)")
        if path:
            try:
                Path(path).write_text(json.dumps({"version": 1, "energies": values}, indent=2), encoding="utf-8")
            except OSError as exc:
                self.status.setText(str(exc))

    def load_scheme(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load energy scheme", "", "JSON (*.json)")
        if path:
            try:
                data = json.loads(Path(path).read_text(encoding="utf-8"))
                if data["version"] != 1 or set(data["energies"]) != set(PVS):
                    raise ValueError("Scheme must contain all nine energy PVs")
                values = {name: energy(value) for name, value in data["energies"].items()}
                self.clear()
                self.select(0)
                self.stage(values)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.status.setText(f"Load failed：{exc}")

    def closeEvent(self, event):
        if self.worker is not None:
            self.status.setText("Read/write in progress; close after completion")
            event.ignore()
        else:
            event.accept()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--epics", action="store_true", help="Connect to IN:Lxx:ENG PVs; Apply updates quadrupole currents")
    args = parser.parse_args()
    app = QApplication(sys.argv)
    window = Window(args.epics)
    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
