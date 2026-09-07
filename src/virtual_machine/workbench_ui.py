"""English VM observation workbench; configuration actions remain in mainVM."""
from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QTabWidget,
    QScrollArea, QTreeWidget, QTreeWidgetItem, QLineEdit, QLabel, QComboBox, QPlainTextEdit,
    QToolButton, QSizePolicy)
from matplotlib.figure import Figure
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg, NavigationToolbar2QT

from half_linac.src.shared.runtime_state import read_runtime_state
from half_linac.src.virtual_machine.beam_source import bootstrap_runtime_state
from half_linac.src.virtual_machine.workbench_data import input_version, observation_dir, occurrences


class PlotToolbar(NavigationToolbar2QT):
    toolitems = tuple(item for item in NavigationToolbar2QT.toolitems
                      if item[0] in ('Home', 'Pan', 'Zoom', 'Save'))


class Plot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.figure = Figure(figsize=(4, 3), tight_layout=True)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.ax = self.figure.add_subplot(111)
        self.toolbar = PlotToolbar(self.canvas, self, coordinates=False)
        self.toolbar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas, 1)
        self.setMinimumSize(180, 150)

    def theme(self, dark):
        bg, fg = ('#10171c', '#d7e2ea') if dark else ('#fffdf9', '#314049')
        self.figure.set_facecolor(bg)
        self.ax.set_facecolor(bg)
        self.ax.tick_params(colors=fg, labelsize=8)
        self.ax.xaxis.label.set_color(fg)
        self.ax.yaxis.label.set_color(fg)
        self.ax.title.set_color(fg)
        for spine in self.ax.spines.values():
            spine.set_color(fg)
        self.toolbar.setStyleSheet(f'QToolButton {{ color: {fg}; background: {bg}; border: none; padding: 4px; font-size: 11px; }} QToolButton:checked {{ background: #35766c; }}')
        self.canvas.draw_idle()


def load_observation(runtime_json, initial_state=None):
    try:
        state = read_runtime_state(runtime_json)
    except FileNotFoundError:
        if initial_state is None:
            raise
        state = initial_state()
    directory = observation_dir(runtime_json)
    try:
        status = read_runtime_state(directory / 'status.json')
    except (OSError, ValueError):
        status = {}
    result = None
    if status.get('result'):
        try:
            candidate = read_runtime_state(directory / 'result.json')
            if (candidate.get('session') == status.get('session') and
                    candidate.get('input_version') == status.get('result_version') and
                    candidate.get('calculation') <= status.get('calculation', 0)):
                result = candidate
        except (OSError, ValueError):
            pass
    return state, input_version(state), status, result


class WorkbenchMixin:
    def _build_workbench(self):
        self._messages = queue.Queue()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='vm-observation')
        self._read_future = None
        self._session = None
        self._observation = {}
        self._result = None
        self._version = None
        self._elements = []
        self._selected_index = None
        self._stopping = False
        self._process_failures = {}
        self._close_pending = False
        self._last_error = None
        self._read_error = None
        self.textEdit.document().setMaximumBlockCount(1500)
        # Reparent the existing controls before retiring their old layout.
        central = QWidget(self)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(6)
        header = QHBoxLayout()
        title = QLabel(f'{self.machine_profile.machine.display_name} VM Workbench')
        title.setStyleSheet('font-size: 20px; font-weight: bold;')
        header.addWidget(title, 1)
        for widget in (self.start_ioc, self.start_vm, self.shutdown_VM, self.theme_toggle_button):
            widget.setMinimumWidth(0)
            if widget is not self.theme_toggle_button:
                widget.setStyleSheet('min-height: 26px; padding: 3px 10px;')
            header.addWidget(widget)
        outer.addLayout(header)
        self.status_panel._items['connection'][0].findChildren(QLabel)[0].setText('IOC STATUS')
        self.status_panel._items['mode'][0].findChildren(QLabel)[0].setText('SIMULATION STATUS')
        self.status_panel._items['config'][0].findChildren(QLabel)[0].setText('LATTICE')
        self.status_panel._items['current'][0].findChildren(QLabel)[0].setText('LAST SUCCESS')
        self.status_panel.setMinimumHeight(54)
        self.status_panel.setMaximumHeight(80)
        outer.addWidget(self.status_panel)
        self.body_splitter = QSplitter(Qt.Vertical)
        self.beamline = Plot()
        self.beamline.setMinimumHeight(115)
        self.beamline.toolbar.setMaximumHeight(28)
        self.beamline.canvas.mpl_connect('button_press_event', self._beamline_click)
        self.beamline.canvas.mpl_connect('scroll_event', self._beamline_scroll)
        self.body_splitter.addWidget(self.beamline)
        self.main_splitter = QSplitter(Qt.Horizontal)
        self.tabs = QTabWidget()
        self.tabs.setMinimumWidth(300)
        self.tabs.tabBar().setExpanding(False)
        device_page = QWidget()
        dl = QVBoxLayout(device_page)
        self.device_search = QLineEdit()
        self.device_search.setPlaceholderText('Search devices')
        self.devices = QTreeWidget()
        self.devices.setHeaderLabels(['Element', 'Type', 's (m)'])
        self.devices.setColumnWidth(0, 115)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(150)
        dl.addWidget(self.device_search)
        dl.addWidget(self.devices, 1)
        dl.addWidget(self.details)
        self.tabs.addTab(device_page, 'Devices')
        for widget, label in ((self.beam_source_group, 'Beam Source'),
                              (self.groupBox_2, 'Lattice'), (self.groupBox_3, 'Errors')):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(widget)
            self.tabs.addTab(scroll, label)
        # Preserve each field/label pairing while adapting the old wide forms.
        grid = self.bunched_page.layout()
        pairs = []
        for i in range(grid.count()):
            row, column, _, _ = grid.getItemPosition(i)
            if column % 2 == 0:
                pairs.append((row, column, grid.itemAt(i).widget(),
                              grid.itemAtPosition(row, column + 1).widget()))
        while grid.count():
            grid.takeAt(0)
        for column in range(8):
            grid.setColumnStretch(column, 0)
        for row, (_, _, label, field) in enumerate(sorted(pairs, key=lambda v: (v[0], v[1]))):
            label.setWordWrap(True)
            grid.addWidget(label, row, 0)
            grid.addWidget(field, row, 1)
        grid = self.sdds_page.layout()
        labels = [grid.itemAtPosition(1, column).widget() for column in (0, 2, 4)]
        input_label = grid.itemAtPosition(0, 0).widget()
        while grid.count():
            grid.takeAt(0)
        for column in range(8):
            grid.setColumnStretch(column, 0)
        grid.addWidget(input_label, 0, 0, 1, 2)
        grid.addWidget(self.sdds_input_edit, 1, 0, 1, 2)
        grid.addWidget(self.sdds_browse_button, 2, 0, 1, 2)
        for row, (label, key) in enumerate(zip(labels, ('sample_interval', 'p_lower', 'p_upper')), 3):
            grid.addWidget(label, row, 0)
            grid.addWidget(self.sdds_fields[key], row, 1)
        grid.addWidget(self.center_arrival_check, 6, 0, 1, 2)
        grid.addWidget(self.reuse_bunch_check, 7, 0, 1, 2)
        beam_header = self.beam_source_group.layout().itemAt(0).layout()
        source_label = beam_header.itemAt(0).widget()
        while beam_header.count():
            beam_header.takeAt(0)
        from PyQt5.QtWidgets import QGridLayout
        bg = QGridLayout()
        bg.addWidget(source_label, 0, 0)
        bg.addWidget(self.beam_source_combo, 0, 1)
        bg.addWidget(self.beam_source_status, 1, 0, 1, 2)
        bg.addWidget(self.reference_label, 2, 0, 1, 2)
        bg.addWidget(self.reference_edit, 3, 0, 1, 2)
        bg.addWidget(self.apply_beam_source_button, 4, 0, 1, 2)
        self.beam_source_group.layout().insertLayout(0, bg)
        self._beam_source_mode_changed()
        self.main_splitter.addWidget(self.tabs)
        curve_page = QWidget()
        cl = QVBoxLayout(curve_page)
        self.curve_choice = QComboBox()
        self.curve_choice.addItems(['Orbit', 'Twiss', 'Dispersion', 'Beam Size'])
        self.curve_plot = Plot()
        cl.addWidget(self.curve_choice)
        cl.addWidget(self.curve_plot, 1)
        self.main_splitter.addWidget(curve_page)
        screen_page = QWidget()
        sl = QVBoxLayout(screen_page)
        self.screen_choice = QComboBox()
        self.screen_plot = Plot()
        self.screen_metrics = QLabel('Unavailable: no simulation result')
        self.screen_metrics.setWordWrap(True)
        sl.addWidget(self.screen_choice)
        sl.addWidget(self.screen_plot, 1)
        sl.addWidget(self.screen_metrics)
        self.main_splitter.addWidget(screen_page)
        self.main_splitter.setSizes([330, 660, 300])
        self.body_splitter.addWidget(self.main_splitter)
        self.body_splitter.setSizes([145, 470])
        outer.addWidget(self.body_splitter, 1)
        self.result_status = QLabel('No simulation result')
        outer.addWidget(self.result_status)
        self.log_toggle = QToolButton()
        self.log_toggle.setText('Show Log')
        self.log_toggle.setCheckable(True)
        self.log_toggle.toggled.connect(self._toggle_log)
        outer.addWidget(self.log_toggle)
        self.textEdit.setMaximumHeight(140)
        outer.addWidget(self.textEdit)
        self.textEdit.hide()
        old = self.takeCentralWidget()
        self.setCentralWidget(central)
        old.hide()
        old.deleteLater()
        self.setMinimumSize(1050, 680)
        self.resize(1366, 768)
        self.setWindowTitle(f'{self.machine_profile.machine.display_name} VM Workbench')
        self.device_search.textChanged.connect(self._filter_devices)
        self.devices.currentItemChanged.connect(self._select_device)
        self.curve_choice.currentTextChanged.connect(self._draw_curve)
        self.screen_choice.currentIndexChanged.connect(self._draw_screen)
        self._apply_theme()
        self._draw_curve()
        self._draw_screen()

    def _toggle_log(self, checked):
        self.textEdit.setVisible(checked)
        self.log_toggle.setText('Hide Log' if checked else 'Show Log')

    def _filter_devices(self):
        text = self.device_search.text().lower()
        for i in range(self.devices.topLevelItemCount()):
            item = self.devices.topLevelItem(i)
            item.setHidden(text not in (item.text(0) + ' ' + item.text(1)).lower())

    def _select_device(self, current, previous=None):
        if current is None:
            return
        self._selected_index = current.data(0, Qt.UserRole)
        element = self._elements[self._selected_index]
        self.details.setPlainText(json.dumps(element['parameters'], indent=2))
        self._draw_curve()
        self._draw_beamline()

    def _beamline_click(self, event):
        if event.inaxes is not self.beamline.ax or not self._elements or self.beamline.toolbar.mode:
            return
        hits = [item['index'] for item in self._elements
                if item['s'] <= event.xdata <= item['s'] + max(item['length'], .025)]
        index = min(hits, key=lambda i: self._elements[i]['length']) if hits else min(
            range(len(self._elements)), key=lambda i: abs(self._elements[i]['s']-event.xdata))
        item = self.devices.topLevelItem(index)
        self.device_search.clear()
        self.devices.setCurrentItem(item)
        self.devices.scrollToItem(item)

    def _beamline_scroll(self, event):
        if event.inaxes is not self.beamline.ax:
            return
        lo, hi = self.beamline.ax.get_xlim()
        center = event.xdata
        factor = 0.8 if event.button == 'up' else 1.25
        self.beamline.ax.set_xlim(center+(lo-center)*factor, center+(hi-center)*factor)
        self.beamline.canvas.draw_idle()

    def _draw_beamline(self):
        ax = self.beamline.ax
        limits = ax.get_xlim() if getattr(self, '_preserve_beamline', False) else None
        ax.clear()
        colors = {'QUAD': '#45d0bc', 'KQUAD': '#45d0bc', 'WATCH': '#e4b86f',
                  'CSBEND': '#60a5fa', 'RFCA': '#c49bdf'}
        for element in self._elements:
            selected = element['index'] == self._selected_index
            ax.broken_barh([(element['s'], max(element['length'], .025))],
                          (-.3 if selected else -.2, .6 if selected else .4),
                          facecolors=colors.get(element['kind'], '#70838c'))
            if selected:
                ax.text(element['s'], .4, element['name'], color='#45d0bc', fontsize=9)
        ax.set_ylim(-.6, .8)
        ax.set_yticks([])
        ax.set_xlabel('s (m) · Select a device · Scroll to zoom · Pan with toolbar')
        if limits:
            ax.set_xlim(limits)
        if not limits:
            self.beamline.toolbar.update()
            self.beamline.toolbar.push_current()
        self._preserve_beamline = True
        self.beamline.theme(self.current_theme == 'dark')

    def _draw_curve(self, *args):
        ax = self.curve_plot.ax
        ax.clear()
        name = self.curve_choice.currentText()
        data = (self._result or {}).get('curves', {}).get(name, {'error': 'No simulation result'})
        if 'error' in data:
            ax.set_title('Unavailable: ' + data['error'], fontsize=9, wrap=True)
        else:
            ax.plot(data['s'], data['x'], color='#45bfa9', label='X')
            ax.plot(data['s'], data['y'], color='#609fea', label='Y')
            ax.set_ylabel(f"{name} ({data['unit']})")
            ax.legend(fontsize=8)
            ax.grid(alpha=.15)
        if self._selected_index is not None and self._selected_index < len(self._elements):
            ax.axvline(self._elements[self._selected_index]['s'], color='#b79964', linestyle=':')
        ax.set_xlabel('s (m)')
        self.curve_plot.toolbar.update()
        self.curve_plot.toolbar.push_current()
        self.curve_plot.theme(self.current_theme == 'dark')

    def _draw_screen(self, *args):
        ax = self.screen_plot.ax
        ax.clear()
        screen = (self._result or {}).get('screens', {}).get(self.screen_choice.currentData(), {})
        if 'image' in screen:
            ax.imshow(screen['image'], extent=screen['extent'], origin='lower', aspect='equal', cmap='viridis')
            self.screen_metrics.setText(f"Centroid (mm): {screen['cx']:.4g}, {screen['cy']:.4g}\n"
                                        f"RMS (mm): {screen['sx']:.4g}, {screen['sy']:.4g}")
        else:
            self.screen_metrics.setText('Unavailable: ' + screen.get('error', 'No screen output'))
        ax.set_xlabel('x (mm)')
        ax.set_ylabel('y (mm)')
        self.screen_plot.toolbar.update()
        self.screen_plot.toolbar.push_current()
        self.screen_plot.theme(self.current_theme == 'dark')

    def _refresh_process_state(self):
        if not hasattr(self, '_messages'):
            return
        while True:
            try:
                self._append_log(self._messages.get_nowait())
            except queue.Empty:
                break
        self._prune_finished_processes()
        if self._stopping:
            if time.monotonic() > self._stop_deadline:
                for proc in self.processes.values():
                    self._signal_process_group(proc, signal.SIGKILL)
            if not self.processes:
                self._stopping = False
                if self._close_pending:
                    self.close()
                    return
        if self._read_future is not None and self._read_future.done():
            try:
                state, version, status, result = self._read_future.result()
                self._read_error = None
                if version != self._version:
                    self._version = version
                    self._elements = occurrences(state)
                    self._selected_index = None
                    self.devices.clear()
                    for element in self._elements:
                        item = QTreeWidgetItem([element['name'], element['kind'], f"{element['s']:.3f}"])
                        item.setData(0, Qt.UserRole, element['index'])
                        self.devices.addTopLevelItem(item)
                    self._filter_devices()
                    self._preserve_beamline = False
                    self._draw_beamline()
                self._observation = status if status.get('session') == self._session and self._session else {}
                if result is not None and result != self._result:
                    self._result = result
                    selected = self.screen_choice.currentData()
                    self.screen_choice.blockSignals(True)
                    self.screen_choice.clear()
                    for key, screen in result['screens'].items():
                        self.screen_choice.addItem(f"{screen['name']} · {screen['s']:.3f} m", key)
                    index = self.screen_choice.findData(selected)
                    self.screen_choice.setCurrentIndex(max(index, 0))
                    self.screen_choice.blockSignals(False)
                    self._draw_curve()
                    self._draw_screen()
            except Exception as exc:
                self._read_error = str(exc)
                self._version = None
                self.result_status.setText(f'Unavailable: {exc}')
            self._read_future = None
        if self._read_future is None:
            self._read_future = self._executor.submit(load_observation, self.runtime.vm.runtime_json,
                                                       lambda: bootstrap_runtime_state(self.runtime))
        ioc = self._is_running('softioc')
        vm = self._is_running('vm')
        busy = self._is_running('vm_config') or self._stopping
        status = self._observation
        phase = status.get('phase', 'Starting') if vm else ('Failed' if 'vm' in self._process_failures else 'Stopped')
        if phase == 'Ready' and status.get('result_version') != self._version:
            phase = 'Pending'
        self.start_ioc.setEnabled(not ioc and not busy)
        self.start_vm.setEnabled(ioc and not vm and not busy)
        self.shutdown_VM.setEnabled(bool(self.processes) and not self._stopping)
        for button in (self.pushButton_ESAline, self.pushButton_simply_VM, self.pushButton_FULLline,
                       self.static_err, self.err_off):
            button.setEnabled(vm and phase != 'Starting' and not busy)
        self._refresh_beam_source_availability(config_running=busy)
        self.status_panel.set_item('connection', 'Process Running' if ioc else ('Failed' if 'softioc' in self._process_failures else 'Stopped'), 'info' if ioc else 'subtle')
        self.status_panel.set_item('mode', 'Stopping' if self._stopping else phase,
                                   'danger' if phase == 'Failed' else 'info')
        self.status_panel.set_item('config', self._current_usedline_summary())
        completed = status.get('completed_at', '')
        self.status_panel.set_item('current', f"{completed[11:19]} UTC / {status.get('elapsed', 0):.2f} s" if completed else 'None')
        if self._result:
            fresh = (vm and phase == 'Ready' and self._result.get('input_version') == self._version
                     and self._result.get('session') == self._session
                     and self._result.get('calculation') == status.get('calculation'))
            label = 'Latest Result' if fresh else ('Last Successful Result' if phase in ('Failed', 'Stopped') else 'Out of Date')
            failed = [key for key, ok in status.get('publication', {}).items() if not ok]
            if failed:
                label += ' · Publication incomplete: ' + ', '.join(failed)
            self.result_status.setText(label)
        if not self._result:
            self.result_status.setText(f'{phase} · No successful simulation result')
        if self._read_error:
            self.result_status.setText('Unavailable: ' + self._read_error)
        if not status.get('error'):
            self._last_error = None
        if status.get('error'):
            self.result_status.setText(('Last Successful Result' if self._result else 'No Successful Result') + ' · ' + status['error'])
        if status.get('error') and status['error'] != self._last_error:
            self._last_error = status['error']
            self._notify('Calculation failed: ' + status['error'])
            self.result_status.setText(('Last Successful Result' if self._result else 'No Successful Result') + ' · ' + status['error'])
            self.log_toggle.setChecked(True)

    def _prune_finished_processes(self):
        for key, proc in list(self.processes.items()):
            code = proc.poll()
            if code is None:
                continue
            self.processes.pop(key)
            self.process_start_times.pop(key, None)
            self._notify(f'{key} exited (code {code}).')
            if code and not self._stopping:
                self._process_failures[key] = f'Exit code {code}'
                self.log_toggle.setChecked(True)
            if key == 'vm_config':
                self._vm_config_label = None
                self._load_beam_source_fields()

    def _start_process(self, key, label, cmd, cwd, expect_running):
        if self._is_running(key) or self._stopping:
            return None
        self._process_failures.pop(key, None)
        env = dict(os.environ, PYTHONUNBUFFERED='1')
        if key == 'vm':
            self._session = uuid.uuid4().hex
            env['HALF_VM_SESSION'] = self._session
            self._observation = {}
        cmd = [sys.executable if cmd[0] == 'python3' else cmd[0], *cmd[1:]]
        try:
            proc = subprocess.Popen(cmd, cwd=cwd, env=env, start_new_session=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                    errors='replace', bufsize=1)
        except OSError as exc:
            self._process_failures[key] = str(exc)
            self._notify(f'Failed to start {label}: {exc}')
            self.log_toggle.setChecked(True)
            return None
        self.processes[key] = proc
        self.process_start_times[key] = time.monotonic()
        messages = self._messages
        def consume():
            with proc.stdout:
                for line in proc.stdout:
                    messages.put(f'{label}: {line.rstrip()}')
        threading.Thread(target=consume, daemon=True).start()
        return proc

    def _stop_subpro(self):
        self._stopping = True
        self._stop_deadline = time.monotonic() + 3
        for proc in self.processes.values():
            self._signal_process_group(proc, signal.SIGTERM)

    def closeEvent(self, event):
        if any(proc.poll() is None for proc in self.processes.values()):
            self._close_pending = True
            if not self._stopping:
                self._stop_subpro()
            event.ignore()
            return
        self.process_timer.stop()
        self._executor.shutdown(wait=False, cancel_futures=True)
        event.accept()

    def _handle_shutdown_signal(self, signum, frame):
        self.close()

    def _refresh_dynamic_layouts(self):
        # The workbench uses splitters and persistent layouts, not rebuild-on-resize.
        if hasattr(self, 'tabs'):
            self.beam_source_stack.setFixedHeight(self.beam_source_stack.currentWidget().sizeHint().height())
            self._update_routing_layout()
            self._update_error_action_layout()

    def _toggle_theme(self):
        self.current_theme = 'light' if self.current_theme == 'dark' else 'dark'
        self._apply_theme()
        self._draw_beamline()
        self._draw_curve()
        self._draw_screen()
