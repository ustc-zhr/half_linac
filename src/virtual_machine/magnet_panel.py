"""Qt adapter for VM magnet editing and one in-memory comparison baseline."""
from __future__ import annotations

import copy
import hashlib
import math
import threading
from concurrent.futures import ThreadPoolExecutor

from PyQt5.QtCore import QObject, Qt, pyqtSignal
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QLineEdit, QPushButton, QCheckBox, QMessageBox, QDialog, QTableWidget,
    QTableWidgetItem, QDialogButtonBox, QHeaderView)

from half_linac.src.shared.runtime_state import read_runtime_state
from half_linac.src.shared.machine_profile.loader import machine_root
from half_linac.src.virtual_machine.workbench_data import observation_dir, input_version
from half_linac.src.virtual_machine.magnet_control import (
    magnets_for, model_value, capture_baseline, MagnetOperation,
)


class VmClient:
    def __init__(self, notify):
        self.pvs = {}
        self.notify = notify

    def subscribe(self, magnets):
        import epics
        epics.ca.use_initial_context()
        wanted = {m.pv for m in magnets}
        for name in list(self.pvs):
            if name not in wanted:
                self.pvs.pop(name).disconnect()
        for magnet in magnets:
            if magnet.pv in self.pvs:
                continue
            self.pvs[magnet.pv] = epics.PV(magnet.pv, auto_monitor=True,
                callback=lambda pvname=None, value=None, **kw: self.notify(pvname, value),
                connection_callback=lambda pvname=None, conn=False, **kw:
                    None if conn else self.notify(pvname, None))
        for name, pv in self.pvs.items():
            self.notify(name, pv.get(use_monitor=True, timeout=.05) if pv.connected else None)

    def read(self, magnet):
        pv = self.pvs.get(magnet.pv)
        if pv is None or not pv.connected:
            raise ConnectionError(f'{magnet.element_id}: PV disconnected')
        value = pv.get(use_monitor=False, timeout=.5)
        if value is None or not math.isfinite(float(value)):
            raise ConnectionError(f'{magnet.element_id}: no finite PV setting')
        return float(value)

    def write(self, magnet, value, timeout):
        pv = self.pvs.get(magnet.pv)
        if pv is None or not pv.connected:
            raise ConnectionError(f'{magnet.element_id}: PV disconnected')
        if pv.put(float(value), wait=True, timeout=timeout) != 1:
            raise TimeoutError(f'{magnet.element_id}: PV write timed out or failed')

    def close(self):
        for pv in self.pvs.values():
            pv.disconnect()
        self.pvs.clear()


class MagnetController(QObject):
    pv_changed = pyqtSignal(str, object)
    event = pyqtSignal(str, object)

    def __init__(self, parent, client=None):
        super().__init__(parent)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='vm-magnets')
        self.client = client or VmClient(lambda name, value: self.pv_changed.emit(name, value))
        self.cancel = threading.Event()
        self.pending = False
        self.closed = False

    def submit(self, name, function):
        if self.pending or self.closed:
            return False
        self.pending = True
        self.cancel.clear()
        def run():
            try:
                result = function()
            except Exception as exc:
                result = dict(phase='Failed', detail=str(exc), items=[])
            self.event.emit(name, result)
        self.pool.submit(run)
        return True

    def close(self):
        self.closed = True
        self.cancel.set()
        self.pool.submit(self.client.close)
        self.pool.shutdown(wait=False)


class MagnetPanel(QWidget):
    def __init__(self, window, client=None):
        super().__init__(window)
        self.window = window
        self.controller = MagnetController(self, client)
        self.controller.pv_changed.connect(self._pv_changed)
        self.controller.event.connect(self._event)
        self.magnets = []
        self.state = None
        self.status = {}
        self.result = None
        self.profile_key = ''
        self.selected = None
        self.pv_values = {}
        self.drafts = {}
        self.baseline = None
        self.compatible = False
        self.busy = False
        self.enabled_session = False
        self._profile_stamp = None
        self._context_version = None
        self.steps = {}
        self._last_poll = 0
        self._calculation_started = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        self.title = QLabel('Select a writable magnet')
        self.title.setWordWrap(True)
        self.values = QLabel('PV: Unavailable · Model: Unavailable')
        self.values.setWordWrap(True)
        layout.addWidget(self.title)
        layout.addWidget(self.values)
        grid = QGridLayout()
        grid.setVerticalSpacing(3)
        self.value_edit = QLineEdit()
        self.value_edit.setPlaceholderText('Pending value')
        self.step_edit = QLineEdit('0.01')
        self.step_edit.setMaximumWidth(95)
        self.minus = QPushButton('−')
        self.plus = QPushButton('+')
        for button in (self.minus, self.plus):
            button.setMaximumWidth(36)
        grid.addWidget(self.value_edit, 0, 0, 1, 4)
        grid.addWidget(QLabel('Step'), 1, 0)
        grid.addWidget(self.step_edit, 1, 1)
        grid.addWidget(self.minus, 1, 2)
        grid.addWidget(self.plus, 1, 3)
        layout.addLayout(grid)
        actions = QHBoxLayout()
        self.apply_button = QPushButton('Apply')
        self.discard_button = QPushButton('Discard')
        actions.addWidget(self.apply_button)
        actions.addWidget(self.discard_button)
        layout.addLayout(actions)
        self.message = QLabel('Read-only')
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.operation_label = QLabel('')
        self.operation_label.setWordWrap(True)
        layout.addWidget(self.operation_label)
        for field in (self.value_edit, self.step_edit):
            field.setStyleSheet('min-height: 18px; padding: 2px 6px; font-size: 12px;')
        for button in self.findChildren(QPushButton):
            button.setStyleSheet('min-height: 20px; padding: 2px 6px; font-size: 11px;')
        self.value_edit.textEdited.connect(self._edit)
        self.step_edit.textEdited.connect(self._step_changed)
        self.minus.clicked.connect(lambda: self._step(-1))
        self.plus.clicked.connect(lambda: self._step(1))
        self.apply_button.clicked.connect(self.apply)
        self.discard_button.clicked.connect(self.discard)
        # This compact row lives above the plots, not inside the scrolling device pane.
        self.baseline_bar = QWidget()
        row = QHBoxLayout(self.baseline_bar)
        row.setContentsMargins(0, 0, 0, 0)
        self.set_baseline_button = QPushButton('Set Baseline')
        self.show_baseline = QCheckBox('Show Baseline')
        self.restore_button = QPushButton('Restore Magnets')
        self.cancel_button = QPushButton('Cancel Operation')
        self.baseline_label = QLabel('No baseline')
        for widget in (self.set_baseline_button, self.show_baseline, self.restore_button,
                       self.cancel_button, self.baseline_label):
            row.addWidget(widget)
        row.addStretch(1)
        for button in self.baseline_bar.findChildren(QPushButton):
            button.setStyleSheet('min-height: 22px; padding: 3px 8px; font-size: 11px;')
        self.set_baseline_button.clicked.connect(self.set_baseline)
        self.restore_button.clicked.connect(self.prepare_restore)
        self.cancel_button.clicked.connect(self.controller.cancel.set)
        self.show_baseline.toggled.connect(self.redraw)
        self._render()

    def _profile_fingerprint(self):
        root = machine_root(self.window.machine_profile.machine.id)
        paths = sorted(root.rglob('*.json'))
        stamp = [(str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in paths]
        if stamp != self._profile_stamp:
            digest = hashlib.sha256()
            for path in paths:
                digest.update(str(path.relative_to(root)).encode())
                digest.update(path.read_bytes())
            key = digest.hexdigest()
            if self.profile_key and key != self.profile_key:
                self.enabled_session = False
                self._profile_changed = True
            self.profile_key, self._profile_stamp = key, stamp
        return self.profile_key

    def refresh(self, state, status, result, enabled):
        self.state, self.status, self.result = state, status, result
        self.enabled_session = enabled and not getattr(self, '_profile_changed', False)
        version = input_version(state)
        if version != self._context_version:
            self.magnets = magnets_for(self.window.machine_profile, state)
            self._context_version = version
        old = self.compatible
        try:
            self._profile_fingerprint()
        except OSError:
            self.enabled_session = False
            self._profile_changed = True
        self.compatible = bool(not getattr(self, '_profile_changed', False) and self.baseline and self.baseline.compatible(state, self.magnets, self.profile_key))
        if not self.compatible:
            self.show_baseline.setChecked(False)
        if old != self.compatible:
            self.redraw()
        import time
        if self.enabled_session and not self.busy and not self.controller.pending and time.monotonic()-self._last_poll > 1:
            self._last_poll = time.monotonic()
            self.controller.submit('poll', lambda: self.controller.client.subscribe(tuple(self.magnets)))
        if self.selected:
            self.selected = next((m for m in self.magnets if m.pv == self.selected.pv), None)
        if self.busy and self._calculation_started is not None:
            self.operation_label.setText(f'Calculating · {time.monotonic()-self._calculation_started:.1f} s')
        self._render()

    def select(self, element):
        magnet = next((m for m in self.magnets if m.element_id == element['name']), None)
        changed = magnet != self.selected
        self.selected = magnet
        if changed and magnet:
            self.window.curve_choice.setCurrentText('Twiss' if magnet.kind == 'quad' else 'Orbit')
        self._render()

    def _draft(self):
        if not self.selected:
            return None
        return self.drafts.get(self.selected.pv)

    def _pv_changed(self, name, value):
        try:
            value = float(value)
            if not math.isfinite(value):
                value = None
        except (TypeError, ValueError):
            value = None
        self.pv_values[name] = value
        draft = self.drafts.get(name)
        magnet = next((m for m in self.magnets if m.pv == name), None)
        if draft and magnet and not self.busy:
            if value is None or not magnet.close(value, draft['anchor']):
                draft['conflict'] = True
        self._render()

    def _edit(self, text):
        magnet = self.selected
        if not magnet or self.pv_values.get(magnet.pv) is None:
            return
        draft = self.drafts.setdefault(magnet.pv, dict(anchor=self.pv_values[magnet.pv],
            conflict=False, step=self.steps.get(magnet.pv, self.step_edit.text())))
        draft['text'] = text
        self._render()

    def _step_changed(self, text):
        if self.selected:
            self.steps[self.selected.pv] = text
        if self._draft():
            self._draft()['step'] = text

    def _step(self, direction):
        try:
            step = float(self.step_edit.text())
            value = float(self.value_edit.text())
            if not math.isfinite(step) or step <= 0:
                raise ValueError()
            self._edit(format(value + direction*step, '.12g'))
        except ValueError:
            self.message.setText('Step must be finite and positive; enter a numeric value')

    def discard(self):
        if self.selected:
            self.drafts.pop(self.selected.pv, None)
        self._render()

    def _render(self):
        if not hasattr(self, 'baseline_label'):
            return
        magnet, draft = self.selected, self._draft()
        connected = magnet is not None and self.pv_values.get(magnet.pv) is not None
        editable = connected and self.enabled_session and not self.busy
        for widget in (self.value_edit, self.step_edit, self.plus, self.minus):
            widget.setEnabled(bool(editable))
        self.discard_button.setEnabled(bool(draft and not self.busy))
        valid = False
        if magnet:
            repeat = self.state['usedline'].count(magnet.element_id)
            note = f' · Shared across {repeat} occurrences' if repeat > 1 else ''
            self.title.setText(f'{magnet.element_id} · {magnet.field} ({magnet.unit}){note}')
            pv = self.pv_values.get(magnet.pv)
            model = model_value(self.state, magnet)*magnet.scale
            self.values.setText(f"PV: {pv*magnet.scale:.12g} · Model: {model:.12g}" if pv is not None else f'PV: Disconnected · Model: {model:.12g}')
            self.values.setToolTip(magnet.pv)
            limits = 'Limits not configured' if magnet.low is None and magnet.high is None else (
                f"Limits: {magnet.low*magnet.scale if magnet.low is not None else '-∞'} to "
                f"{magnet.high*magnet.scale if magnet.high is not None else '∞'} {magnet.unit}")
            text = draft['text'] if draft else format(pv*magnet.scale, '.12g') if pv is not None else ''
            if self.value_edit.text() != text:
                self.value_edit.setText(text)
            if not self.step_edit.hasFocus():
                self.step_edit.setText(self.steps.get(magnet.pv, '0.01'))
            self.message.setText('External Change · Discard to reload' if draft and draft['conflict'] else limits)
            if draft:
                try:
                    target = magnet.validate(float(draft['text'])/magnet.scale)
                    valid = not draft['conflict'] and not magnet.close(target, draft['anchor'])
                except ValueError as exc:
                    if not draft['conflict']:
                        self.message.setText(str(exc))
        else:
            self.title.setText('Select a writable magnet')
            self.values.setText('PV: Unavailable · Model: Unavailable')
            self.value_edit.clear()
            self.message.setText('Read-only: no VM magnet channel')
        if getattr(self, '_profile_changed', False):
            self.message.setText('Machine configuration changed · Restart this workbench')
        self.apply_button.setEnabled(bool(editable and valid and not self.controller.pending))
        self.set_baseline_button.setEnabled(bool(self.enabled_session and not self.busy and
            not self.controller.pending and self.result and self.result.get('input_state') and
            self.status.get('phase') == 'Ready' and self.status.get('result_version') == self._context_version))
        self.restore_button.setEnabled(bool(self.enabled_session and self.compatible and not self.busy and not self.controller.pending))
        self.show_baseline.setEnabled(self.compatible)
        self.cancel_button.setEnabled(self.busy)
        self.baseline_label.setText('Baseline active' if self.compatible else 'Baseline incompatible' if self.baseline else 'No baseline')

    def _start(self, name, function):
        if self.controller.submit(name, function):
            self.busy = True
            self.operation_label.setText('Checking settings')
            self.window._refresh_process_state()
            self._render()

    def _operation(self, items, initial, session):
        return MagnetOperation(self.controller.client,
            lambda: read_runtime_state(self.window.runtime.vm.runtime_json),
            lambda: read_runtime_state(observation_dir(self.window.runtime.vm.runtime_json)/'status.json'),
            emit=lambda phase, detail: self.controller.event.emit('progress', (phase, detail)),
            cancelled=self.controller.cancel.is_set,
            context_valid=lambda: self.enabled_session and self.window._session == session).execute(items, initial, session)

    def apply(self):
        if not self.apply_button.isEnabled():
            return
        magnet, draft = self.selected, self._draft()
        target = magnet.validate(float(draft['text'])/magnet.scale)
        items = [(magnet, draft['anchor'], target)]
        initial, session = copy.deepcopy(self.state), self.window._session
        self._start('operation', lambda: self._operation(items, initial, session))

    def set_baseline(self):
        if not self.set_baseline_button.isEnabled():
            return
        result, state, status = copy.deepcopy((self.result, self.state, self.status))
        magnets, key, session = tuple(self.magnets), self.profile_key, self.window._session
        def capture():
            def checked_read(magnet):
                if self.controller.cancel.is_set():
                    raise ValueError('Cancelled')
                return self.controller.client.read(magnet)
            baseline = capture_baseline(result, state, status, session, magnets, key, checked_read)
            fresh = read_runtime_state(observation_dir(self.window.runtime.vm.runtime_json)/'status.json')
            if any(fresh.get(k) != status.get(k) for k in ('session', 'phase', 'result_version', 'calculation')):
                raise ValueError('Simulation status changed during baseline capture')
            if input_version(read_runtime_state(self.window.runtime.vm.runtime_json)) != input_version(state):
                raise ValueError('Input changed during baseline capture')
            if not self.enabled_session or self.controller.cancel.is_set():
                raise ValueError('VM context changed during capture')
            return baseline
        self._start('baseline', capture)

    def prepare_restore(self):
        if not self.restore_button.isEnabled():
            return
        initial, baseline, magnets = copy.deepcopy(self.state), self.baseline, tuple(self.magnets)
        def prepare():
            if not baseline.compatible(initial, magnets, self.profile_key):
                raise ValueError('Baseline incompatible')
            items = []
            for magnet in magnets:
                if self.controller.cancel.is_set():
                    raise ValueError('Cancelled')
                current = self.controller.client.read(magnet)
                target = magnet.validate(model_value(baseline.result['input_state'], magnet))
                if not magnet.close(current, model_value(initial, magnet)):
                    raise ValueError(f'{magnet.element_id}: PV/model mismatch')
                if not magnet.close(current, target):
                    items.append((magnet, current, target))
            return dict(items=items, initial=initial, session=self.window._session)
        self._start('restore_preview', prepare)

    def _event(self, name, payload):
        if self.controller.closed:
            return
        if name == 'progress':
            if payload[0] == 'Calculating':
                import time
                self._calculation_started = time.monotonic()
            self.operation_label.setText(f'{payload[0]} · {payload[1][:30]}')
            return
        self.controller.pending = False
        if name == 'poll':
            self._render()
            return
        self.busy = False
        self._calculation_started = None
        if name == 'baseline' and not isinstance(payload, dict):
            self.baseline = payload
            self.compatible = payload.compatible(self.state, self.magnets, self.profile_key)
            self.show_baseline.setChecked(self.compatible)
            self.operation_label.setText('Baseline captured')
            self.redraw()
        elif name == 'restore_preview' and 'phase' not in payload:
            if payload['items']:
                self._confirm_restore(payload)
            else:
                self.operation_label.setText('Magnets already match baseline')
        else:
            self.operation_label.setText(f"{payload['phase']} · {payload.get('detail', '')}")
            self.window._append_log(self.operation_label.text())
            for item in payload.get('items', []):
                self.window._append_log(f"{item['element']}: {item['status']}")
                if payload['phase'] == 'Applied':
                    self.drafts.pop(item['pv'], None)
                elif item['pv'] in self.drafts:
                    self.drafts[item['pv']]['conflict'] = True
            if payload.get('items') and (len(payload['items']) > 1 or payload['phase'] != 'Applied'):
                self._report(payload)
        for pv, draft in self.drafts.items():
            magnet = next((m for m in self.magnets if m.pv == pv), None)
            actual = self.pv_values.get(pv)
            if magnet and (actual is None or not magnet.close(actual, draft['anchor'])):
                draft['conflict'] = True
        self.window._refresh_process_state()
        self._render()

    def _confirm_restore(self, payload):
        dialog = QDialog(self.window)
        dialog.setWindowTitle('Restore Magnets')
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('Restore these virtual PV settings? Other model settings remain unchanged.'))
        table = QTableWidget(len(payload['items']), 4)
        table.setHorizontalHeaderLabels(['Magnet', 'Current', 'Baseline', 'Unit'])
        for row, (magnet, old, target) in enumerate(payload['items']):
            for col, text in enumerate((magnet.element_id, f'{old*magnet.scale:.12g}', f'{target*magnet.scale:.12g}', magnet.unit)):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                table.setItem(row, col, item)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(570, 360)
        # Keep configuration actions locked while the user reviews the concrete list.
        self.busy = True
        if dialog.exec_() == QDialog.Accepted:
            self.busy = False
            if not self.compatible or not self.enabled_session:
                self.operation_label.setText('Restore cancelled: context changed')
                return
            self._start('operation', lambda: self._operation(payload['items'], payload['initial'], payload['session']))
        else:
            self.busy = False

    def _report(self, payload):
        box = QMessageBox(self.window)
        box.setWindowTitle('Magnet Operation')
        box.setText(payload['phase'] + ' · ' + payload.get('detail', ''))
        box.setDetailedText('\n'.join(f"{i['element']}: {i['status']}" for i in payload['items']))
        box.setAttribute(Qt.WA_DeleteOnClose)
        box.open()
        self._report_box = box

    def redraw(self):
        self.window._draw_curve()
        self.window._draw_screen()

    def baseline_curve(self, name):
        if not self.compatible or not self.show_baseline.isChecked():
            return None
        return self.baseline.result.get('curves', {}).get(name)

    def screen_comparison(self, key, screen):
        if not self.compatible or not self.show_baseline.isChecked():
            return None
        base = self.baseline.result.get('screens', {}).get(key, {})
        rows = ['Metric (mm) | Current | Baseline | Δ']
        for label, name in (('Cx', 'cx'), ('Cy', 'cy'), ('σx', 'sx'), ('σy', 'sy')):
            current, reference = screen.get(name), base.get(name)
            if current is None or reference is None:
                rows.append(f'{label}: Unavailable')
            else:
                rows.append(f'{label}: {current:.4g} | {reference:.4g} | {current-reference:+.4g}')
        return '\n'.join(rows)
