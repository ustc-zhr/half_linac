"""Qt bridge: the worker requests scans, the GUI owns each scan QThread."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from threading import Event
from uuid import uuid4

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractButton, QAbstractItemView, QComboBox, QDialog, QDoubleSpinBox,
    QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
    QProgressBar, QScrollArea, QSizePolicy, QSplitter,
    QSpinBox, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)
from matplotlib.figure import Figure
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg

from half_linac.src.shared.machine_profile import LimitRange, resolve_channel, resolve_app_runtime_paths
from .optimization import OptimizationConfig, OptimizationVariable, OptimizationSession, EpicsVariableGroup


class CompactScrollContent(QWidget):
    def sizeHint(self):
        return self.minimumSizeHint()


class OptimizationWorker(QThread):
    requested = pyqtSignal(object)
    cancel_scan = pyqtSignal()
    progress = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, session, parent=None, *, action=None):
        super().__init__(parent)
        self.session, self.action = session, action
        self.session.measure = self.measure
        self.session.progress = self.progress.emit

    def measure(self, path, checkpoint):
        request = {'path': path, 'done': Event(), 'result': None}
        self.requested.emit(request)
        pending_error = None
        while not request['done'].wait(0.05):
            if pending_error is None:
                try:
                    checkpoint()
                except Exception as exc:
                    pending_error = exc
                    self.cancel_scan.emit()
        result = request['result']
        # Return the final result even on cancellation so the controller archives it.
        # Its checkpoint handles stop/time, and restoration failure takes precedence.
        return result

    def run(self):
        try:
            if self.action is None:
                self.session.run()
            else:
                self.session.manual_move(best=self.action == 'best')
        except Exception as exc:
            self.failed.emit(str(exc))


class CurrentReadWorker(QThread):
    completed = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, context, names, parent=None):
        super().__init__(parent)
        self.context, self.names = context, names

    def run(self):
        import epics
        import math
        values = {}
        try:
            for name in self.names:
                if self.isInterruptionRequested():
                    return
                pv = resolve_channel(self.context, name, 'current_set')
                value = epics.caget(pv, timeout=2, use_monitor=False)
                if value is None or not math.isfinite(float(value)):
                    raise ValueError(f'{name}: current setpoint unavailable.')
                values[name] = float(value)
            self.completed.emit(values)
        except Exception as exc:
            self.failed.emit(str(exc))


class OptimizationDialog(QDialog):
    def __init__(self, host):
        super().__init__(host)
        self.host = host
        self.worker = self.scan = self.session = None
        self.reader = None
        self.request = None
        self.fault = False
        self.worker_error = None
        self.close_pending = False
        self.locked_widgets = []
        self.locked_tables = []
        self.locked_items = []
        self.stopped_timers = []
        self.setWindowTitle('Emittance Optimization · HALF')
        self.setObjectName('emitOptimization')
        self.resize(1120, 940)
        self.setMinimumSize(800, 720)
        palette = host._palette()
        self.setStyleSheet(f"""
            QDialog#emitOptimization QLabel {{ font-weight: normal; }}
            QDialog#emitOptimization QLabel[role="caption"] {{ color: {palette['muted_fg']}; }}
            QDialog#emitOptimization QLabel#optTitle {{ font-size: 20px; font-weight: bold; }}
            QDialog#emitOptimization QPushButton#optStart:enabled {{
                background: {palette['metric_active_fg']}; color: {palette['window_bg']};
                border-color: {palette['metric_active_fg']};
            }}
            QDialog#emitOptimization QPushButton {{
                min-height: 20px; max-height: 20px; padding: 2px 12px;
            }}
            QDialog#emitOptimization QSplitter::handle {{ background: {palette['panel_border']}; }}
            QDialog#emitOptimization QTableWidget {{ font-size: 12px; }}
            QDialog#emitOptimization QHeaderView::section {{ font-size: 11px; }}
            QDialog#emitOptimization QFrame[optimizationCard="true"] {{
                background: {palette['panel_bg']};
                border: 1px solid {palette['panel_border']}; border-radius: 10px;
            }}
            QDialog#emitOptimization QLabel[role="cardTitle"] {{
                color: {palette['window_fg']}; font-size: 13px; font-weight: bold;
                background: transparent; border: none;
            }}
            QDialog#emitOptimization QLabel {{ background: transparent; border: none; }}
            QDialog#emitOptimization QWidget#optSettings,
            QDialog#emitOptimization QWidget#optAlgorithmSettings {{ background: transparent; }}
        """)
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_content = CompactScrollContent(self.scroll_area)
        self.scroll_content.setObjectName('optScrollContent')
        self.scroll_content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = QVBoxLayout(self.scroll_content)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)
        self.scroll_area.setWidget(self.scroll_content)
        outer_layout.addWidget(self.scroll_area)
        header = QHBoxLayout()
        title = QLabel('Emittance Optimization', self)
        title.setObjectName('optTitle')
        header.addWidget(title)
        header.addStretch()
        layout.addLayout(header)
        self.context_label = QLabel(self)
        self.context_label.setProperty('role', 'caption')
        self.context_label.setWordWrap(True)
        layout.addWidget(self.context_label)

        setup_card, setup_layout = self.make_card('Search Setup')
        layout.addWidget(setup_card)
        self.settings = QWidget(self)
        self.settings.setObjectName('optSettings')
        settings_layout = QHBoxLayout(self.settings)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(18)
        self.variables = QTableWidget(0, 5, self.settings)
        self.variables.setHorizontalHeaderLabels(['Use', 'Element', 'Initial (A)', 'Lower (A)', 'Upper (A)'])
        self.variables.verticalHeader().hide()
        self.variables.verticalHeader().setDefaultSectionSize(27)
        self.variables.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.variables.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.variables.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.variables.setMinimumHeight(148)
        self.variables.setMaximumHeight(165)
        self.variable_rows = {}
        default_selected = False
        for element in host.machine_profile.elements:
            if element.kind != 'solenoid':
                continue
            error = None
            try:
                for channel in ('current_set', 'current_readback'):
                    resolve_channel(host.app_context, element.id, channel)
            except Exception as exc:
                error = str(exc)
            row = self.variables.rowCount()
            self.variables.insertRow(row)
            self.variable_rows[element.id] = row
            for column, text in enumerate(('', element.id, '—', '', '')):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignCenter)
                if column < 3:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if error:
                    item.setFlags(item.flags() & ~Qt.ItemIsEnabled & ~Qt.ItemIsEditable)
                    item.setToolTip(error)
                self.variables.setItem(row, column, item)
            use = self.variables.item(row, 0)
            if not error:
                use.setFlags(use.flags() | Qt.ItemIsUserCheckable)
            checked = not error and not default_selected
            use.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            default_selected = default_selected or checked
            limit = element.limits.get('current_set')
            if limit is not None and not error:
                limit = LimitRange.from_mapping(limit)
                for column in (3, 4):
                    self.variables.item(row, column).setToolTip(f'Absolute current in A. Machine range: {limit.describe()}')
        self.variable_panel = QWidget(self.settings)
        variable_layout = QVBoxLayout(self.variable_panel)
        variable_layout.setContentsMargins(0, 0, 0, 0)
        variable_layout.setSpacing(6)
        variable_layout.addWidget(self.variables)
        self.read_button = QPushButton('Read Currents', self.settings)
        self.read_button.setToolTip('Read selected setpoints only. Initial values are captured again at run start.')
        self.read_button.clicked.connect(self.read_currents)
        self.budget_note = QLabel(self.settings)
        self.budget_note.setProperty('role', 'caption')
        self.budget_note.setWordWrap(True)
        variable_actions = QHBoxLayout()
        variable_actions.addWidget(self.read_button)
        variable_actions.addWidget(self.budget_note, 1)
        variable_layout.addLayout(variable_actions)
        settings_layout.addWidget(self.variable_panel, 3)

        self.options_panel = QWidget(self.settings)
        options_grid = QGridLayout(self.options_panel)
        options_grid.setContentsMargins(0, 0, 0, 0)
        options_grid.setHorizontalSpacing(14)
        options_grid.setVerticalSpacing(4)
        self.plane = QComboBox(self.settings)
        self.plane.addItem('Horizontal X', 'x')
        self.plane.addItem('Vertical Y', 'y')
        self.algorithm = QComboBox(self.settings)
        self.algorithm.addItem('RCDS', 'rcds')
        self.algorithm.addItem('Bayesian Optimization (BO)', 'bo')
        self.algorithm.setToolTip('RCDS is the default local search. BO uses a Matérn Gaussian process with expected improvement.')
        self.other_limit = QLineEdit(self.settings)
        self.other_limit.setPlaceholderText('Required')
        self.other_limit.setToolTip('Upper limit for the other plane, in mm·mrad.')
        self.count = QSpinBox(self.settings)
        self.count.setRange(5, 1000)
        self.count.setValue(20)
        self.count.setToolTip('Includes baseline scans, retries, and best-point verification scans.')
        self.minutes = QDoubleSpinBox(self.settings)
        self.minutes.setRange(1, 1440)
        self.minutes.setDecimals(1)
        self.minutes.setValue(120)
        self.solenoid_settle = QDoubleSpinBox(self.settings)
        self.solenoid_settle.setRange(0, 3600)
        self.solenoid_settle.setDecimals(1)
        self.solenoid_settle.setValue(OptimizationConfig.__dataclass_fields__['settle_time'].default)
        self.solenoid_settle.setToolTip('Wait after all selected solenoids reach their targets, then verify again. Independent of the main-window scan quadrupole settle time.')
        self.energy = QLabel(self.settings)
        fields = (
            ('Algorithm', self.algorithm), ('Minimize', self.plane),
            ('Other-plane limit (mm·mrad)', self.other_limit),
            ('Max. scans', self.count), ('Time budget (min)', self.minutes),
            ('Main-window energy (MeV)', self.energy), ('Solenoid settle time (s)', self.solenoid_settle),
        )
        for index, (text, control) in enumerate(fields):
            row, column = divmod(index, 2)
            label = QLabel(text, self.settings)
            label.setProperty('role', 'caption')
            label.setBuddy(control)
            options_grid.addWidget(label, row * 2, column, alignment=Qt.AlignBottom)
            control.setMinimumWidth(0)
            control.setFixedHeight(32)
            control.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            options_grid.addWidget(control, row * 2 + 1, column, alignment=Qt.AlignTop)
            options_grid.setColumnStretch(column, 1)
        settings_layout.addWidget(self.options_panel, 2)
        setup_layout.addWidget(self.settings)

        self.algorithm_settings = QWidget(self)
        self.algorithm_settings.setObjectName('optAlgorithmSettings')
        algorithm_layout = QHBoxLayout(self.algorithm_settings)
        algorithm_layout.setContentsMargins(0, 0, 0, 0)
        algorithm_layout.setSpacing(10)
        self.rcds_settings = QWidget(self.algorithm_settings)
        rcds_layout = QHBoxLayout(self.rcds_settings)
        rcds_layout.setContentsMargins(0, 0, 0, 0)
        rcds_label = QLabel('Initial step fraction', self.rcds_settings)
        rcds_label.setProperty('role', 'caption')
        self.rcds_step = QDoubleSpinBox(self.rcds_settings)
        self.rcds_step.setRange(0.01, 1.0)
        self.rcds_step.setSingleStep(0.05)
        self.rcds_step.setDecimals(2)
        self.rcds_step.setValue(OptimizationConfig.__dataclass_fields__['rcds_initial_step'].default)
        self.rcds_step.setFixedHeight(32)
        self.rcds_step.setToolTip('Initial RCDS step as a fraction of each variable range.')
        rcds_layout.addWidget(rcds_label)
        rcds_layout.addWidget(self.rcds_step)

        self.bo_settings = QWidget(self.algorithm_settings)
        bo_layout = QHBoxLayout(self.bo_settings)
        bo_layout.setContentsMargins(0, 0, 0, 0)
        initial_label = QLabel('Initial samples', self.bo_settings)
        initial_label.setProperty('role', 'caption')
        self.bo_initial_samples = QSpinBox(self.bo_settings)
        self.bo_initial_samples.setRange(3, 996)
        self.bo_initial_samples.setFixedHeight(32)
        self.bo_initial_samples.setToolTip('Includes the current machine point. Must not exceed Max. scans minus 4.')
        exploration_label = QLabel('Exploration', self.bo_settings)
        exploration_label.setProperty('role', 'caption')
        self.bo_exploration = QDoubleSpinBox(self.bo_settings)
        self.bo_exploration.setRange(0, 10)
        self.bo_exploration.setSingleStep(0.01)
        self.bo_exploration.setDecimals(3)
        self.bo_exploration.setValue(OptimizationConfig.__dataclass_fields__['bo_exploration'].default)
        self.bo_exploration.setFixedHeight(32)
        self.bo_exploration.setToolTip('Expected Improvement exploration offset. Larger values favor less-sampled regions.')
        for widget in (initial_label, self.bo_initial_samples, exploration_label, self.bo_exploration):
            bo_layout.addWidget(widget)

        self.advanced_button = QToolButton(self.algorithm_settings)
        self.advanced_button.setText('Advanced')
        self.advanced_button.setCheckable(True)
        self.advanced_button.setArrowType(Qt.RightArrow)
        self.advanced_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.advanced_button.setFixedHeight(26)
        self.bo_advanced = QWidget(self.algorithm_settings)
        advanced_layout = QHBoxLayout(self.bo_advanced)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        seed_label = QLabel('Random seed', self.bo_advanced)
        seed_label.setProperty('role', 'caption')
        self.bo_seed = QSpinBox(self.bo_advanced)
        self.bo_seed.setRange(0, 2147483647)
        self.bo_seed.setFixedHeight(32)
        self.bo_seed.setValue(OptimizationConfig.__dataclass_fields__['bo_random_seed'].default)
        self.bo_seed.setToolTip('Controls repeatable initial and candidate sampling.')
        advanced_layout.addWidget(seed_label)
        advanced_layout.addWidget(self.bo_seed)
        algorithm_layout.addWidget(self.rcds_settings)
        algorithm_layout.addWidget(self.bo_settings)
        algorithm_layout.addWidget(self.advanced_button)
        algorithm_layout.addWidget(self.bo_advanced)
        algorithm_layout.addStretch()
        setup_layout.addWidget(self.algorithm_settings)
        self.measurement_note = QLabel(self)
        self.measurement_note.setProperty('role', 'caption')
        self.measurement_note.setWordWrap(True)
        setup_layout.addWidget(self.measurement_note)

        run_card, run_layout = self.make_card('Run Control')
        layout.addWidget(run_card)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.start_button = QPushButton('Start Optimization', self)
        self.start_button.setObjectName('optStart')
        self.stop_button = QPushButton('Stop and Restore', self)
        self.apply_button = QPushButton('Apply Best', self)
        self.restore_button = QPushButton('Restore Initial', self)
        for button in (self.start_button, self.stop_button):
            buttons.addWidget(button)
        buttons.addStretch()
        for button in (self.apply_button, self.restore_button):
            buttons.addWidget(button)
        run_layout.addLayout(buttons)
        self.status = QLabel('Ready. The run restores initial currents; Apply Best requires two verification scans.', self)
        self.status.setWordWrap(True)
        run_layout.addWidget(self.status)
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, self.count.value())
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat('0 / %m scans')
        self.progress_bar.setFixedHeight(18)
        run_layout.addWidget(self.progress_bar)
        results_card, results_layout = self.make_card('Results')
        results_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        layout.addWidget(results_card, 1)
        self.results = QLabel('Initial currents: —    ·    Baseline: —    ·    Best candidate: —', self)
        self.results.setWordWrap(True)
        results_layout.addWidget(self.results)

        splitter = QSplitter(Qt.Vertical, self)
        splitter.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        splitter.setMinimumHeight(250)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(5)
        self.figure = Figure(figsize=(7, 3), tight_layout=True)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setMinimumHeight(140)
        self.axes = self.figure.add_subplot(111)
        self.style_plot()
        self.draw_empty_plot()
        splitter.addWidget(self.canvas)
        self.table = QTableWidget(0, 6, self)
        self.table.setMinimumHeight(90)
        self.table.setHorizontalHeaderLabels(['Scan / Stage', 'Currents (A)', 'εnx', 'εny', 'Status', 'Archive'])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        splitter.addWidget(self.table)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([175, 100])
        results_layout.addWidget(splitter, 1)
        self.start_button.clicked.connect(self.start)
        self.stop_button.clicked.connect(self.stop)
        self.apply_button.clicked.connect(lambda: self.manual('best'))
        self.restore_button.clicked.connect(lambda: self.manual('initial'))
        for edit in (self.other_limit,):
            edit.textChanged.connect(self.settings_changed)
        self.algorithm.currentIndexChanged.connect(self.algorithm_changed)
        self.plane.currentIndexChanged.connect(self.settings_changed)
        self.variables.itemChanged.connect(self.settings_changed)
        self.count.valueChanged.connect(self.settings_changed)
        self.minutes.valueChanged.connect(self.settings_changed)
        self.solenoid_settle.valueChanged.connect(self.settings_changed)
        self.rcds_step.valueChanged.connect(self.settings_changed)
        self.bo_initial_samples.valueChanged.connect(self.bo_initial_samples_changed)
        self.bo_exploration.valueChanged.connect(self.settings_changed)
        self.bo_seed.valueChanged.connect(self.settings_changed)
        self.advanced_button.toggled.connect(self.advanced_changed)
        self._bo_initial_samples_custom = False
        for widget in host.findChildren(QWidget):
            if self.isAncestorOf(widget):
                continue
            if isinstance(widget, QLineEdit):
                widget.textChanged.connect(self.measurement_changed)
            elif isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self.measurement_changed)
            elif isinstance(widget, QAbstractButton) and widget.isCheckable():
                widget.toggled.connect(self.measurement_changed)
        self.refresh_note()
        self.update_algorithm_settings()
        self.update_budget_note()
        self.update_buttons()
        layout.activate()
        self.scroll_content.setMinimumHeight(max(880, layout.minimumSize().height()))

    def make_card(self, title):
        card = QFrame(self)
        card.setProperty('optimizationCard', True)
        box = QVBoxLayout(card)
        box.setContentsMargins(14, 10, 14, 12)
        box.setSpacing(8)
        heading = QLabel(title, card)
        heading.setProperty('role', 'cardTitle')
        box.addWidget(heading)
        return card, box

    def busy(self):
        return bool((self.worker is not None and self.worker.isRunning()) or
                    (self.reader is not None and self.reader.isRunning()))

    def settings_changed(self, *_args):
        if not self._bo_initial_samples_custom:
            default = max(3, 2 * len(self.selected_names()) + 1)
            blocked = self.bo_initial_samples.blockSignals(True)
            self.bo_initial_samples.setValue(default)
            self.bo_initial_samples.blockSignals(blocked)
        self.update_budget_note()
        if self.session and self.session.confirmed and not self.busy():
            self.session.confirmed = False
            self.status.setText('Settings changed. Run optimization again before applying a best point. Restore Initial remains available.')
        self.update_buttons()

    def algorithm_changed(self, *_args):
        self.update_algorithm_settings()
        self.settings_changed()

    def update_algorithm_settings(self):
        is_bo = self.algorithm.currentData() == 'bo'
        self.rcds_settings.setVisible(not is_bo)
        self.bo_settings.setVisible(is_bo)
        self.advanced_button.setVisible(is_bo)
        self.bo_advanced.setVisible(is_bo and self.advanced_button.isChecked())

    def advanced_changed(self, checked):
        self.advanced_button.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)
        self.bo_advanced.setVisible(bool(checked) and self.algorithm.currentData() == 'bo')

    def bo_initial_samples_changed(self, *_args):
        self._bo_initial_samples_custom = True
        self.settings_changed()

    @staticmethod
    def format_currents(values):
        return ' · '.join(f'{name}={value:.5g}' for name, value in values.items())

    def selected_names(self):
        return [name for name, row in self.variable_rows.items()
                if self.variables.item(row, 0).checkState() == Qt.Checked
                and self.variables.item(row, 0).flags() & Qt.ItemIsEnabled]

    def selected_variables(self):
        variables = []
        for name in self.selected_names():
            row = self.variable_rows[name]
            try:
                low, high = (float(self.variables.item(row, column).text()) for column in (3, 4))
            except ValueError as exc:
                raise ValueError(f'{name}: enter both current bounds in A.') from exc
            variable = OptimizationVariable(name, low, high)
            variable.validate()
            variables.append(variable)
        return tuple(variables)

    def update_budget_note(self):
        n = len(self.selected_names())
        search = max(0, self.count.value() - 4)
        bo_note = (f' · {self.bo_initial_samples.value()} initial samples'
                   if self.algorithm.currentData() == 'bo' else '')
        invalid_note = (' Initial samples exceed the search budget.'
                        if self.algorithm.currentData() == 'bo'
                        and self.bo_initial_samples.value() > search else '')
        self.budget_note.setText(f'{n} selected · {self.algorithm.currentText()}{bo_note} · up to {search} search scans; 4 reserved for baseline / verification.'
                                + invalid_note
                                + (' More variables usually need more scans.' if n > 1 else ''))

    def display_initial(self, values):
        blocked = self.variables.blockSignals(True)
        try:
            for name, value in values.items():
                self.variables.item(self.variable_rows[name], 2).setText(f'{value:.6g}')
        finally:
            self.variables.blockSignals(blocked)

    def read_currents(self):
        if self.busy() or self.fault or self.host.machine_type != 'real':
            return
        names = self.selected_names()
        if not names:
            return
        if self.session and self.session.confirmed:
            self.session.confirmed = False
        self.reader = CurrentReadWorker(self.host.app_context, names, self)
        self.reader.completed.connect(self.currents_read)
        self.reader.failed.connect(self.status.setText)
        self.reader.finished.connect(self.read_finished)
        self.status.setText('Reading selected setpoints…')
        self.reader.start()
        self.update_buttons()

    def currents_read(self, values):
        self.display_initial(values)
        self.status.setText('Setpoints read. Run start captures initial currents again; enter bounds for every selected row.')

    def read_finished(self):
        self.reader.wait()
        self.update_buttons()
        if self.close_pending:
            self.close()

    def measurement_changed(self, *_args):
        if not self.busy() and not self.fault:
            self.refresh_note()
            self.settings_changed()

    def refresh_note(self):
        if self.busy() or self.fault:
            return
        quad, screen = self.host.comboBox.currentText(), self.host.comboBox_4.currentText()
        strategy = self.host.scan_strategy_combo.currentText()
        self.setWindowTitle(f'Emittance Optimization · HALF · {quad} → {screen}')
        self.context_label.setText(f'Measurement: {quad} → {screen} · {strategy} · Normalized projected emittance')
        self.energy.setText(self.host.lineEdit_2.text())
        self.measurement_note.setText(
            f'{self.host._beam_width_method()} · '
            'Main-window scan mode, settings, energy, ROI and background frozen at start. '
            'Select solenoids upstream of the scan quadrupole.')

    def style_plot(self):
        palette = self.host._palette()
        self.figure.set_facecolor(palette['plot_card_bg'])
        self.axes.set_facecolor(palette['plot_bg'])
        self.axes.tick_params(colors=palette['plot_text'], labelsize=8)
        for spine in self.axes.spines.values():
            spine.set_color(palette['plot_spine'])
        self.axes.set_xlabel('Measurement', color=palette['plot_text'])
        self.axes.set_ylabel('εn (mm·mrad)', color=palette['plot_text'])
        self.axes.grid(axis='y', color=palette['plot_grid'], alpha=0.5)
        self.axes.set_axisbelow(True)
        legend = self.axes.get_legend()
        if legend:
            legend.get_frame().set_facecolor(palette['plot_bg'])
            for text in legend.get_texts():
                text.set_color(palette['plot_text'])

    def draw_empty_plot(self):
        self.axes.text(
            0.5, 0.5, 'Emittance trend will appear after the first valid scan',
            ha='center', va='center', transform=self.axes.transAxes,
            color=self.host._palette()['muted_fg'], fontsize=10,
        )
        self.axes.set_xticks([])
        self.axes.set_yticks([])

    def update_buttons(self):
        busy = self.busy()
        self.settings.setEnabled(not busy and not self.fault)
        self.algorithm_settings.setEnabled(not busy and not self.fault)
        self.start_button.setEnabled(not busy and not self.fault and self.host.machine_type == 'real')
        self.stop_button.setEnabled(bool(self.worker and self.worker.isRunning() and self.worker.action is None))
        self.read_button.setEnabled(not busy and not self.fault and self.host.machine_type == 'real' and bool(self.selected_names()))
        self.apply_button.setEnabled(not busy and not self.fault and bool(self.session and self.session.confirmed))
        self.restore_button.setEnabled(not busy and bool(self.session and self.session.initial is not None))
        if self.host.machine_type != 'real' and not self.session:
            self.status.setText('Unavailable in VM: solenoid current channels are not configured.')

    def error(self, text):
        self.status.setText(str(text))
        QMessageBox.warning(self, 'Emittance Optimization', str(text))

    def check_idle(self):
        host = self.host
        matching = getattr(host, 'matching_workspace', None)
        if host._scan_is_running() or host._twiss_is_running() or (
            matching and matching.worker and matching.worker.isRunning()
        ):
            raise ValueError('Finish the current scan, Twiss calculation, or matching task first.')

    def lock_host(self):
        if self.locked_widgets:
            return
        host = self.host
        host._optimization_locked = True
        for timer in host.findChildren(QTimer):
            if timer not in self.findChildren(QTimer) and timer.isActive():
                self.stopped_timers.append((timer, timer.interval()))
                timer.stop()
        for widget in host.findChildren(QWidget):
            if not isinstance(widget, (QAbstractButton, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox)):
                continue
            if self.isAncestorOf(widget) or widget is host.optimization_button:
                continue
            self.locked_widgets.append((widget, widget.isEnabled()))
        for widget, _enabled in self.locked_widgets:
            widget.setEnabled(False)
        for table in host.findChildren(QTableWidget):
            if self.isAncestorOf(table):
                continue
            self.locked_tables.append((table, table.editTriggers()))
            table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            for row in range(table.rowCount()):
                for column in range(table.columnCount()):
                    item = table.item(row, column)
                    if item is not None:
                        self.locked_items.append((table, item, item.flags()))
                        blocked = table.blockSignals(True)
                        item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable & ~Qt.ItemIsEditable)
                        table.blockSignals(blocked)

    def unlock_host(self):
        self.host._optimization_locked = False
        for widget, enabled in self.locked_widgets:
            widget.setEnabled(enabled)
        for table, triggers in self.locked_tables:
            table.setEditTriggers(triggers)
        for table, item, flags in self.locked_items:
            blocked = table.blockSignals(True)
            item.setFlags(flags)
            table.blockSignals(blocked)
        for timer, interval in self.stopped_timers:
            timer.start(interval)
        self.locked_widgets.clear()
        self.locked_tables.clear()
        self.locked_items.clear()
        self.stopped_timers.clear()

    def start(self):
        if self.busy() or self.fault:
            return
        try:
            self.check_idle()
            config = OptimizationConfig(
                self.selected_variables(), self.plane.currentData(), float(self.other_limit.text()),
                self.count.value(), self.minutes.value(), settle_time=self.solenoid_settle.value(),
                algorithm=self.algorithm.currentData(), rcds_initial_step=self.rcds_step.value(),
                bo_initial_samples=self.bo_initial_samples.value(),
                bo_exploration=self.bo_exploration.value(), bo_random_seed=self.bo_seed.value())
            config.validate()
            self.paras = self.host.optimization_parameters(config.variables)
            root = resolve_app_runtime_paths(Path(__file__).parent, self.host.app_context)['runs_dir']
            run_dir = root / ('optimization_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid4().hex[:8])
            self.session = OptimizationSession(config, EpicsVariableGroup(self.host.app_context, config),
                                               None, run_dir, metadata=self.paras.scan_metadata,
                                               background=self.paras.background_image,
                                               recover_quad=lambda: self.paras.restore_quad(self.paras.restore_quad.initial_k1))
            self.table.setRowCount(0)
            self.progress_bar.setRange(0, config.max_measurements)
            self.progress_bar.setValue(0)
            self.results.setText('Initial currents: —    ·    Baseline: —    ·    Best candidate: —')
            self.axes.clear()
            self.draw_empty_plot()
            self.style_plot()
            self.refresh_note()
            self.launch()
        except Exception as exc:
            self.error(exc)

    def launch(self, action=None):
        self.lock_host()
        self.worker_error = None
        self.worker = OptimizationWorker(self.session, self, action=action)
        self.worker.requested.connect(self.start_measurement)
        self.worker.cancel_scan.connect(self.cancel_measurement)
        self.worker.progress.connect(self.show_progress)
        self.worker.failed.connect(self.worker_failed)
        self.worker.finished.connect(self.finished)
        self.worker.start()
        self.update_buttons()

    def worker_failed(self, message):
        self.worker_error = message
        self.status.setText(message)
        # File errors must not silently enable a potentially unverified application.
        self.session.confirmed = False

    def start_measurement(self, request):
        self.request = request
        try:
            if self.session.cancelled.is_set():
                request['result'] = {'restored': True, 'error': 'Stopped before measurement.'}
                request['done'].set()
                self.request = None
                return
            from .main import scanThread
            paras = deepcopy(self.paras)
            paras.scan_latest_dir = request['path'] / 'latest'
            paras.scan_archive_dir = request['path'] / 'runs'
            self.scan = scanThread(paras)
            self.scan.trigger.connect(self.scan_progress)
            self.scan.finished.connect(self.measurement_finished)
            self.scan.start()
        except Exception as exc:
            request['result'] = {'restored': True, 'error': str(exc)}
            request['done'].set()
            self.request = None

    def scan_progress(self, payload):
        if payload.get('error'):
            self.status.setText(str(payload['error']))
        elif payload.get('scan_progress'):
            progress = payload['scan_progress']
            self.status.setText(f"Scan {len(self.session.records)} · {progress.get('stage', '')} · "
                                f"{progress.get('completed_points', 0)} scan points")

    def measurement_finished(self):
        # QThread.finished can precede native thread-local cleanup; join before release.
        self.scan.wait()
        self.request['result'] = self.scan.terminal_result
        self.request['done'].set()
        self.scan.deleteLater()
        self.scan = self.request = None

    def cancel_measurement(self):
        if self.scan is not None:
            self.scan.stop()

    def stop(self):
        if self.session and self.busy():
            self.session.cancelled.set()
            self.cancel_measurement()
            self.status.setText('Stopping. Waiting for the scan quadrupole and all selected solenoids to restore.')

    def show_progress(self, payload):
        self.status.setText(f"{payload['stage']} · {payload['count']}/{self.session.config.max_measurements}")
        self.progress_bar.setRange(0, self.session.config.max_measurements)
        self.progress_bar.setValue(payload['count'])
        self.progress_bar.setFormat('%v / %m scans')
        records = payload.get('records', [])
        self.table.setRowCount(len(records))
        for row, record in enumerate(records):
            values = record.get('values', {})
            state = ('Valid' if record.get('feasible') else 'Other-plane limit exceeded') if record.get('valid') else record.get('error', 'Measuring')
            cells = [f"{row + 1} / {record['stage']}", self.format_currents(record['currents']),
                     f"{values['x']:.4g}" if 'x' in values else '—',
                     f"{values['y']:.4g}" if 'y' in values else '—', state, Path(record['archive']).name]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setToolTip(record['archive'] if column == 5 else text)
                if column in (1, 2, 3):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(row, column, item)
        valid = [r for r in records if r.get('valid')]
        self.axes.clear()
        for plane in ('x', 'y'):
            self.axes.plot([r['index'] for r in valid], [r['values'][plane] for r in valid], '.-', label=plane.upper())
        if valid:
            self.axes.legend(loc='upper right')
        else:
            self.draw_empty_plot()
        self.style_plot()
        self.canvas.draw_idle()
        self.show_results()

    def show_results(self):
        s = self.session
        baseline = s.baseline
        best = s.best
        initial = s.config.named(s.initial) if s.initial is not None else {}
        self.display_initial(initial)
        text = f'Initial currents: {self.format_currents(initial) if initial else "—"} A'
        if baseline:
            text += f' · Baseline εnx={baseline["x"]:.4g}, εny={baseline["y"]:.4g}'
        if best:
            text += (f'\nBest candidate: {self.format_currents(best["currents"])} A · '
                     f'εnx={best["values"]["x"]:.4g}, εny={best["values"]["y"]:.4g}')
        if 'verification_mean' in s.summary:
            text += f' · Verified objective mean={s.summary["verification_mean"]:.4g}'
        self.results.setText(text)

    def finished(self):
        self.worker.wait()
        summary = self.session.summary
        self.show_results()
        self.fault = summary.get('status') == 'restore_failed' or bool(summary.get('quad_restore_error'))
        detail = self.worker_error or summary.get('restore_error') or summary.get('quad_restore_error') or summary.get('error', '')
        self.status.setText(
            f"{summary['status']} · {'Initial currents restored' if summary.get('restored') and not self.fault else 'Check device state'}"
            f" · {'Improvement verified; Apply Best is available' if self.session.confirmed else 'No verified improvement to apply'}"
            f" {detail}")
        self.status.setToolTip(f'Run archive: {self.session.run_dir}')
        if not self.fault:
            self.unlock_host()
        self.update_buttons()
        if self.close_pending and not self.fault:
            self.close()

    def manual(self, action):
        if self.busy():
            return
        try:
            self.check_idle()
            if self.session.device.context is not self.host.app_context:
                raise ValueError('Runtime context changed; the previous result cannot be applied.')
            self.launch(action)
        except Exception as exc:
            self.error(exc)

    def shutdown(self):
        if self.reader and self.reader.isRunning():
            self.close_pending = True
            self.reader.requestInterruption()
            return False
        if self.busy():
            self.close_pending = True
            self.stop()
            return False
        return not self.fault

    def closeEvent(self, event):
        if not self.shutdown():
            event.ignore()
        else:
            event.accept()

    def reject(self):
        self.close()
