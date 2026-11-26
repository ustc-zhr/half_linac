from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path
import sys
import threading
import time

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'repo_bootstrap.py').is_file())
sys.path.insert(0, str(ROOT))
from repo_bootstrap import ensure_repo_import_path
ensure_repo_import_path(__file__)

from PyQt5.QtCore import QSignalBlocker, Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QFontDatabase, QPalette
from PyQt5.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)
from half_linac.src.shared.app_theme import resolve_initial_theme
from half_linac.src.shared.machine_profile import load_app_context, require_workflow_write_allowed
from half_linac.src.shared.machine_profile.control_lock import EnergyControlLock
from half_linac.src.shared.machine_profile.app_runtime import new_app_run_dir
from half_linac.src.shared.window_activation import install_qt_window_raise_handler
from half_linac.src.apps.magnet_cycle.model import COMPLETE, READY, Parameters
from half_linac.src.apps.magnet_cycle.profile_runtime import KINDS, group_magnets, load_magnets
from half_linac.src.apps.magnet_cycle.runner import Simulator, EpicsIO, Stopped, run_cycles


LOWER_COLUMN = 2
UPPER_COLUMN = 3
STATUS_COLUMN = 7
ENDPOINT_COLUMNS = {LOWER_COLUMN: 'lower', UPPER_COLUMN: 'upper'}
EMPTY = '—'

THEMES = {
    'dark': {
        'window': '#0f1519', 'panel': '#172027', 'base': '#121b21',
        'border': '#31434e', 'text': '#e6edf2', 'muted': '#9eafbb',
        'accent': '#45d0bc', 'action': '#1a5851', 'warning': '#e4b86f',
    },
    'light': {
        'window': '#f2ede5', 'panel': '#f5f0e8', 'base': '#fffdf9',
        'border': '#d5cbbd', 'text': '#2c3942', 'muted': '#5d686b',
        'accent': '#216b5c', 'action': '#275e52', 'warning': '#875513',
    },
}


class Worker(QThread):
    progress = pyqtSignal(str, float, float, float, str)
    outcome = pyqtSignal(str)

    def __init__(self, context, magnets, parameters, simulate):
        super().__init__()
        self.context, self.magnets, self.parameters, self.simulate = context, magnets, parameters, simulate
        self.stop = threading.Event()

    def run(self):
        io = None
        lock = None
        message = 'Cycle complete. Initial setpoints restored.'
        try:
            mode = self.context.control_backend.name
            if not self.simulate:
                require_workflow_write_allowed(self.context, 'magnet_cycle', 'Magnet Cycle')
                lock = EnergyControlLock(Path('/tmp') / f'half_linac_{self.context.machine.id}_{mode}_magnet_cycle.lock', {'app': 'magnet_cycle'})
                lock.acquire()
            folder = new_app_run_dir(Path(__file__).parent, self.context, kind='simulation' if self.simulate else 'cycle')
            folder.mkdir(parents=True, exist_ok=False)
            with (folder / 'events.jsonl').open('w', encoding='utf-8') as stream:
                def record(event):
                    stream.write(json.dumps({'time': time.time(), **event}, ensure_ascii=False) + '\n')
                    stream.flush()
                record(dict(event='start', simulation=self.simulate, backend=mode,
                            machine=self.context.machine.id, parameters=asdict(self.parameters)))
                try:
                    io = Simulator(self.magnets) if self.simulate else EpicsIO()
                    run_cycles(self.magnets, self.parameters, io, self.stop, self.progress.emit, record)
                except Exception as exc:
                    record(dict(event='stopped' if isinstance(exc, Stopped) else 'error', reason=str(exc)))
                    raise
                record(dict(event='complete'))
            message += f'\nLog: {folder}'
        except Stopped as exc:
            message = 'Simulation stopped.' if self.simulate else str(exc)
        except Exception as exc:
            message = f'Cycle stopped. {exc}\nAutomatic restore was skipped.'
        finally:
            try:
                if io is not None:
                    io.close()
            except Exception as exc:
                message += f'\nConnection cleanup failed: {exc}'
            finally:
                try:
                    if lock is not None:
                        lock.release()
                except Exception as exc:
                    message += f'\nControl lock release failed: {exc}'
                self.outcome.emit(message)


class CycleWindow(QMainWindow):
    def __init__(self, context, simulate=True):
        super().__init__()
        self.context, self.simulate = context, simulate
        self.worker = None
        self.magnets = load_magnets(context, simulate=simulate)
        self.rows = {m.name: i for i, m in enumerate(self.magnets)}
        self.live = not simulate and context.control_backend.name == 'real'
        self.mode = 'Offline simulation' if simulate else ('Real machine · EPICS' if self.live else 'VM · EPICS')
        self.setWindowTitle('Magnet Cycle')
        self.resize(1120, 720)
        root = QWidget()
        root.setObjectName('cycleRoot')
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel('Magnet Cycle')
        title.setObjectName('title')
        header.addWidget(title)
        header.addStretch()
        header.addWidget(QLabel(context.machine.display_name))
        mode = QLabel(self.mode)
        mode.setObjectName('modeLabel')
        mode.setProperty('live', self.live)
        header.addWidget(mode)
        layout.addLayout(header)
        sequence = QLabel('Lower → [Upper → Lower] × cycles → Initial setpoint')
        sequence.setProperty('muted', True)
        layout.addWidget(sequence)

        controls = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search magnets by name')
        self.search.setAccessibleName('Search magnets by name')
        self.search.setClearButtonEnabled(True)
        controls.addWidget(self.search, 1)
        self.filter = QComboBox()
        self.filter.setAccessibleName('Magnet type')
        self.filter.addItem('All types', '')
        for kind, label in KINDS.items():
            self.filter.addItem(label, kind)
        controls.addWidget(self.filter)
        self.select = QPushButton('Select Visible')
        self.select.clicked.connect(lambda: self.select_visible(True))
        controls.addWidget(self.select)
        self.clear = QPushButton('Clear Selection')
        self.clear.clicked.connect(lambda: self.select_visible(False, all_rows=True))
        controls.addWidget(self.clear)
        controls.addStretch()
        self.selection_label = QLabel()
        self.selection_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        controls.addWidget(self.selection_label)
        layout.addLayout(controls)

        self.build_table()
        layout.addWidget(self.table, 1)
        layout.addWidget(self.build_settings())

        actions = QHBoxLayout()
        status = READY
        if not any(not m.error for m in self.magnets):
            status = 'No magnets are available in this mode. Hover over a Status cell for details.'
        self.status = QLabel(status)
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        actions.addWidget(self.status, 1)
        self.start = QPushButton('Start Simulation' if simulate else 'Start Cycle')
        self.start.setObjectName('startButton')
        self.start.clicked.connect(self.start_cycle)
        self.stop = QPushButton('Stop')
        self.stop.setObjectName('stopButton')
        self.stop.setEnabled(False)
        self.stop.clicked.connect(self.stop_cycle)
        actions.addWidget(self.start)
        actions.addWidget(self.stop)
        layout.addLayout(actions)
        if not self.simulate:
            stop_note = QLabel(
                'Stop halts further writes and leaves the last commanded setpoints in place. '
                'Supplies may still move toward those setpoints.'
            )
            stop_note.setProperty('muted', True)
            stop_note.setWordWrap(True)
            layout.addWidget(stop_note)

        self.search.textChanged.connect(self.filter_rows)
        self.filter.currentIndexChanged.connect(self.filter_rows)
        self.table.itemChanged.connect(self.selection_changed)
        self.refresh_selection()
        self.apply_appearance()

    def build_table(self):
        self.table = QTableWidget(len(self.magnets), 8)
        self.table.setHorizontalHeaderLabels(
            ['Magnet', 'Type', 'Lower (A)', 'Upper (A)', 'Initial (A)', 'Setpoint (A)', 'Readback (A)', 'Status']
        )
        self.table.horizontalHeaderItem(STATUS_COLUMN).setToolTip('Hover over a status to read the full message.')
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(32)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(STATUS_COLUMN, QHeaderView.Stretch)
        for col, endpoint in ENDPOINT_COLUMNS.items():
            header.setSectionResizeMode(col, QHeaderView.Interactive)
            self.table.setColumnWidth(col, 112)
            self.table.horizontalHeaderItem(col).setToolTip(f'Edit the {endpoint} endpoint for each magnet.')
        for row, m in enumerate(self.magnets):
            status = 'Unavailable' if m.error else READY
            values = [m.name, KINDS[m.kind], m.low, m.high, None, None, None, status]
            for col, value in enumerate(values):
                if col in ENDPOINT_COLUMNS and not m.error:
                    editor = QLineEdit(str(value))
                    editor.setObjectName('cycleEndpoint')
                    editor.setAlignment(Qt.AlignRight)
                    editor.setAccessibleName(f'{m.name} {ENDPOINT_COLUMNS[col]} endpoint in amperes')
                    editor.setToolTip(
                        f'Configured range: {m.low} to {m.high} A. '
                        'The lower endpoint must be below the upper endpoint.'
                    )
                    self.table.setCellWidget(row, col, editor)
                    continue
                item = QTableWidgetItem(EMPTY if value is None else str(value))
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                if col == 0:
                    if not m.error:
                        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                        item.setCheckState(Qt.Unchecked)
                if LOWER_COLUMN <= col < STATUS_COLUMN:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                item.setToolTip(m.error or f'Setpoint PV: {m.set_pv}\nReadback PV: {m.read_pv}')
                if col == STATUS_COLUMN and m.error:
                    item.setData(Qt.AccessibleDescriptionRole, m.error)
                self.table.setItem(row, col, item)

    def build_settings(self):
        panel = QFrame()
        panel.setObjectName('settingsPanel')
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)
        basic = QWidget()
        self.parameters = basic
        self.cycles = QSpinBox()
        self.cycles.setRange(1, 100)
        self.cycles.setValue(3)
        self.rate = self.spin(1, 0.001, 10000)
        self.hold = self.spin(2, 0.01, 3600)
        self.toggle = QPushButton('Advanced Settings')
        self.toggle.setCheckable(True)
        basic_fields = self.settings_row(basic, [
            ('Cycles', self.cycles), ('Ramp rate (A/s)', self.rate), ('Endpoint hold (s)', self.hold),
        ])
        basic_fields.addWidget(self.toggle)
        layout.addWidget(basic)
        self.advanced = QWidget()
        self.tolerance = self.spin(0.05, 0.000001, 100, 6)
        self.stable = self.spin(1, 0.01, 3600)
        self.timeout = self.spin(30, 0.1, 10000)
        self.settings_row(self.advanced, [
            ('Readback tolerance (A)', self.tolerance), ('Stable time (s)', self.stable), ('Timeout (s)', self.timeout),
        ])
        self.advanced.setVisible(False)
        self.toggle.toggled.connect(self.advanced.setVisible)
        layout.addWidget(self.advanced)
        return panel

    @staticmethod
    def settings_row(parent, fields):
        layout = QHBoxLayout(parent)
        layout.setContentsMargins(0, 0, 0, 0)
        for name, widget in fields:
            label = QLabel(name)
            label.setBuddy(widget)
            widget.setAccessibleName(name)
            layout.addWidget(label)
            layout.addWidget(widget)
            layout.addSpacing(12)
        layout.addStretch()
        return layout

    def apply_appearance(self):
        colors = THEMES[resolve_initial_theme()]
        palette = QPalette(self.palette())
        for role, name in (
            (QPalette.Window, 'window'), (QPalette.Base, 'base'), (QPalette.AlternateBase, 'panel'),
            (QPalette.Button, 'panel'), (QPalette.WindowText, 'text'), (QPalette.Text, 'text'),
            (QPalette.ButtonText, 'text'), (QPalette.PlaceholderText, 'muted'),
            (QPalette.Highlight, 'action'), (QPalette.ToolTipBase, 'panel'), (QPalette.ToolTipText, 'text'),
        ):
            palette.setColor(role, QColor(colors[name]))
        palette.setColor(QPalette.HighlightedText, QColor('#ffffff'))
        for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
            palette.setColor(QPalette.Disabled, role, QColor(colors['muted']))
        self.setPalette(palette)
        self.setStyleSheet(f"""
            QWidget#cycleRoot, QDialog#cycleConfirmation {{ background: {colors['window']}; }}
            QLabel#title {{ font-size: 23px; font-weight: 700; }}
            QLabel[muted="true"] {{ color: {colors['muted']}; }}
            QLabel#modeLabel {{ color: {colors['accent']}; border: 1px solid {colors['border']};
                                border-radius: 6px; padding: 4px 9px; font-weight: 700; }}
            QLabel#modeLabel[live="true"] {{ color: {colors['warning']}; border-color: {colors['warning']}; }}
            QFrame#settingsPanel {{ background: {colors['panel']}; border: 1px solid {colors['border']};
                                     border-radius: 7px; }}
            QTableWidget {{ border: 1px solid {colors['border']}; font-size: 14px; }}
            QTableWidget::indicator:unchecked {{
                width: 13px; height: 13px; border: 1px solid {colors['muted']};
                border-radius: 3px; background: {colors['base']};
            }}
            QHeaderView::section {{ background: {colors['panel']}; color: {colors['text']};
                                    border: none; border-bottom: 1px solid {colors['border']};
                                    padding: 7px; font-size: 14px; font-weight: 600; }}
            QLineEdit#cycleEndpoint {{ background: {colors['base']}; color: {colors['accent']};
                                       border: 1px solid {colors['border']}; border-radius: 4px;
                                       margin: 3px 7px; padding: 1px 6px; font-size: 14px; }}
            QLineEdit#cycleEndpoint:focus {{ border-color: {colors['accent']}; }}
            QLineEdit#cycleEndpoint:read-only {{ color: {colors['muted']}; border-color: transparent; }}
            QPushButton {{ min-height: 27px; padding: 1px 10px; }}
            QPushButton#startButton:disabled {{ background: {colors['panel']}; color: {colors['muted']};
                                               border: 1px solid {colors['border']}; border-radius: 5px;
                                               font-weight: 700; }}
            QPushButton#startButton:enabled {{ background: {colors['action']}; color: #fff;
                                              border: 1px solid {colors['accent']}; border-radius: 5px;
                                              font-weight: 700; }}
            QPushButton#startButton:enabled:hover, QPushButton#startButton:enabled:focus {{
                border: 2px solid {colors['accent']}; padding: 0 9px;
            }}
            QPushButton#stopButton:enabled {{ color: {colors['warning']}; font-weight: 700; }}
        """)

    @staticmethod
    def spin(value, low, high, decimals=3):
        widget = QDoubleSpinBox()
        widget.setDecimals(decimals)
        widget.setRange(low, high)
        widget.setValue(value)
        return widget

    def filter_rows(self):
        kind = self.filter.currentData()
        query = self.search.text().strip().casefold()
        for row, magnet in enumerate(self.magnets):
            matches = (not kind or kind == magnet.kind) and query in magnet.name.casefold()
            self.table.setRowHidden(row, not matches)
        self.refresh_selection()

    def select_visible(self, checked, all_rows=False):
        with QSignalBlocker(self.table):
            for row, magnet in enumerate(self.magnets):
                if not magnet.error and (all_rows or not self.table.isRowHidden(row)):
                    self.table.item(row, 0).setCheckState(Qt.Checked if checked else Qt.Unchecked)
        self.refresh_selection()

    def checked_magnets(self):
        return [
            magnet for row, magnet in enumerate(self.magnets)
            if not magnet.error and self.table.item(row, 0).checkState() == Qt.Checked
        ]

    def selection_changed(self, item):
        if item.column() == 0:
            self.refresh_selection()

    def refresh_selection(self):
        selected = self.checked_magnets()
        hidden = sum(self.table.isRowHidden(self.rows[magnet.name]) for magnet in selected)
        visible = [magnet for row, magnet in enumerate(self.magnets) if not self.table.isRowHidden(row)]
        summary = f'{len(selected)} selected'
        if hidden:
            summary += f' · {hidden} hidden'
        if not visible and self.magnets:
            summary += ' · No matches'
        self.selection_label.setText(summary)
        self.selection_label.setToolTip('Every selected magnet is included, even when a filter hides it.')
        idle = self.worker is None
        self.start.setEnabled(idle and bool(selected))
        self.clear.setEnabled(idle and bool(selected))
        self.select.setEnabled(idle and any(not magnet.error for magnet in visible))

    def with_cycle_endpoints(self, magnet):
        row = self.rows[magnet.name]
        values = []
        for col, endpoint in ENDPOINT_COLUMNS.items():
            editor = self.table.cellWidget(row, col)
            try:
                value = float(editor.text())
            except ValueError:
                raise ValueError(f'Enter a numeric {endpoint} endpoint in amperes for {magnet.name}.') from None
            if not magnet.low <= value <= magnet.high:
                raise ValueError(
                    f'{magnet.name} {endpoint} endpoint must be within '
                    f'the configured range [{magnet.low}, {magnet.high}] A.'
                )
            values.append(value)
        low, high = values
        if low >= high:
            raise ValueError(f'{magnet.name} lower endpoint must be below the upper endpoint.')
        return replace(magnet, low=low, high=high)

    def confirm_cycle(self, magnets, parameters):
        dialog = QDialog(self)
        dialog.setAttribute(Qt.WA_WindowPropagation)
        dialog.setObjectName('cycleConfirmation')
        dialog.setWindowTitle('Confirm Magnet Cycle')
        dialog.resize(540, 310)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(f'{self.context.machine.display_name} · {self.mode}'))
        p = parameters
        settings = QLabel(
            f'{p.cycles} cycles · Ramp rate {p.rate:g} A/s · Endpoint hold {p.hold:g} s\n'
            f'Tolerance {p.tolerance:g} A · Stable time {p.stable:g} s · Timeout {p.timeout:g} s'
        )
        settings.setWordWrap(True)
        layout.addWidget(settings)
        endpoints = QPlainTextEdit()
        endpoints.setReadOnly(True)
        endpoints.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        endpoints.setLineWrapMode(QPlainTextEdit.NoWrap)
        endpoints.setAccessibleName('Selected magnets and cycle endpoints')
        endpoints.setPlainText('\n'.join(f'{m.name}: {m.low} → {m.high} A' for m in magnets))
        layout.addWidget(endpoints, 1)
        note = QLabel('Check the rate and tolerance for these supplies. A complete cycle restores the initial setpoints.')
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        begin = buttons.addButton('Start Cycle', QDialogButtonBox.AcceptRole)
        begin.setObjectName('startButton')
        begin.setAutoDefault(False)
        cancel = buttons.button(QDialogButtonBox.Cancel)
        cancel.setDefault(True)
        cancel.setFocus()
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        confirmed = dialog.exec_() == QDialog.Accepted
        dialog.deleteLater()
        return confirmed

    def start_cycle(self):
        selected = self.checked_magnets()
        try:
            if not selected:
                raise ValueError('Select at least one magnet.')
            group_magnets(selected)
            selected = [self.with_cycle_endpoints(m) for m in selected]
            group_magnets(selected)
            p = Parameters(
                self.cycles.value(), self.rate.value(), self.hold.value(),
                self.tolerance.value(), self.stable.value(), self.timeout.value(),
            )
            if not self.simulate:
                require_workflow_write_allowed(self.context, 'magnet_cycle', 'Magnet Cycle')
                if not self.confirm_cycle(selected, p):
                    return
            self.selected = selected
            for m in selected:
                row = self.rows[m.name]
                for col in range(UPPER_COLUMN + 1, STATUS_COLUMN):
                    self.table.item(row, col).setText(EMPTY)
                self.set_row_status(row, 'Checking initial setpoint')
            self.worker = Worker(self.context, selected, p, self.simulate)
            self.worker.progress.connect(self.update_row)
            self.worker.outcome.connect(self.finished_message)
            self.worker.finished.connect(self.worker_finished)
            self.set_running(True)
            count = len(selected)
            self.status.setText(f'Running on {count} {"magnet" if count == 1 else "magnets"}…')
            self.worker.start()
        except Exception as exc:
            QMessageBox.warning(self, 'Could Not Start', str(exc))

    def set_running(self, running):
        self.parameters.setEnabled(not running)
        self.advanced.setEnabled(not running)
        with QSignalBlocker(self.table):
            for row, magnet in enumerate(self.magnets):
                if magnet.error:
                    continue
                item = self.table.item(row, 0)
                flags = item.flags()
                item.setFlags(flags & ~Qt.ItemIsUserCheckable if running else flags | Qt.ItemIsUserCheckable)
                for col in ENDPOINT_COLUMNS:
                    self.table.cellWidget(row, col).setReadOnly(running)
        self.refresh_selection()
        self.stop.setEnabled(running)

    def set_row_status(self, row, status):
        item = self.table.item(row, STATUS_COLUMN)
        item.setText(status)
        item.setToolTip(status)

    def update_row(self, name, initial, setpoint, readback, status):
        row = self.rows[name]
        for col, value in enumerate((initial, setpoint, readback), UPPER_COLUMN + 1):
            self.table.item(row, col).setText(f'{value:.6g}')
        self.set_row_status(row, status)

    def stop_cycle(self):
        if self.worker:
            self.worker.stop.set()
            self.stop.setEnabled(False)
            self.status.setText('Stopping after the current operation returns…')

    def finished_message(self, message):
        self.status.setText(message)
        for m in self.selected:
            row = self.rows[m.name]
            if self.table.item(row, STATUS_COLUMN).text() != COMPLETE:
                self.set_row_status(row, 'Stopped')

    def worker_finished(self):
        self.worker.deleteLater()
        self.worker = None
        self.set_running(False)

    def closeEvent(self, event):
        if self.worker is not None:
            self.stop_cycle()
            event.ignore()
            self.status.setText('Stopping. Close the window once the run has stopped.')
        else:
            event.accept()


def main():
    parser = argparse.ArgumentParser(description='Cycle magnet currents. Defaults to offline simulation.')
    parser.add_argument('--machine')
    parser.add_argument('--backend', choices=['vm', 'real'], help='EPICS: inherit Control Room/environment/profile; simulation: default vm')
    parser.add_argument('--epics', action='store_true', help='Enable actual PV connections and writes for the selected backend')
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setAttribute(Qt.AA_UseStyleSheetPropagationInWidgetStyles)
    app.setStyle('Fusion')
    try:
        context = load_app_context('magnet_cycle', machine_id=args.machine, control_backend=args.backend or (None if args.epics else "vm"))
        window = CycleWindow(context, simulate=not args.epics)
    except Exception as exc:
        QMessageBox.critical(None, 'Magnet Cycle', str(exc))
        return 1
    install_qt_window_raise_handler(window)
    window.show()
    return app.exec_()


if __name__ == '__main__':
    raise SystemExit(main())
