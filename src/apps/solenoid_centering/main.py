from __future__ import annotations

import sys
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

_REPO_BOOTSTRAP_ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / "repo_bootstrap.py").is_file()
)
if str(_REPO_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_BOOTSTRAP_ROOT))

from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPlainTextEdit,
    QPushButton,
    QFrame,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from half_linac.src.shared.app_theme import resolve_initial_theme
from half_linac.src.shared.machine_profile import (
    MachineProfileError,
    RuntimeContextWidget,
    SolenoidCenteringPreset,
    SolenoidCenteringScanRange,
    list_elements,
    load_app_context,
    resolve_channel,
    workflow_writes_allowed,
)
from half_linac.src.shared.window_activation import install_qt_window_raise_handler
from half_linac.src.apps.solenoid_centering.mplwidget import MplWidget
from half_linac.src.apps.solenoid_centering.gui.theme import (
    HEADER_ACTION_HEIGHT,
    build_stylesheet,
    theme_palette,
)
from half_linac.src.apps.solenoid_centering.gui.widgets import StatusStrip
from half_linac.src.apps.solenoid_centering.scan import (
    CenteringResult,
    MotionVerificationError,
    RestoreFailed,
    SCORING_MODE_SLOPE,
    SCORING_MODE_TRAJECTORY_LENGTH,
    StateDriftError,
    SolenoidCenteringScanner,
    StopRequested,
    normalize_scoring_mode,
)


@dataclass(frozen=True)
class ScanFailureReport:
    status: str
    termination_code: str
    reason: str
    restore_status: str
    restore_errors: tuple[str, ...] = ()


class ScanWorker(QThread):
    progress_changed = pyqtSignal(str, int, int)
    candidate_finished = pyqtSignal(object)
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(object)

    def __init__(self, context, preset, scoring_mode=SCORING_MODE_SLOPE, parent=None):
        super().__init__(parent)
        self.context = context
        self.preset = preset
        self.scoring_mode = scoring_mode
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    def run(self):
        try:
            scanner = SolenoidCenteringScanner(
                self.context,
                self.preset,
                progress=self.progress_changed.emit,
                candidate_finished=self.candidate_finished.emit,
                scoring_mode=self.scoring_mode,
                stop_requested=lambda: self._stop_requested,
            )
            result = scanner.run()
        except StopRequested as exc:
            self.failed.emit(
                ScanFailureReport(
                    status="stopped",
                    termination_code="operator_stopped",
                    reason=str(exc),
                    restore_status="verified",
                )
            )
            return
        except RestoreFailed as exc:
            operation_error = exc.operation_error
            if isinstance(operation_error, StopRequested):
                termination_code = "operator_stopped"
            elif isinstance(operation_error, MotionVerificationError):
                termination_code = "readback_verification_failed"
            elif operation_error is not None:
                termination_code = "scan_failed"
            else:
                termination_code = "scan_completed"
            self.failed.emit(
                ScanFailureReport(
                    status="restore_failed",
                    termination_code=termination_code,
                    reason=str(exc),
                    restore_status="failed",
                    restore_errors=(
                        exc.outcome.errors if exc.outcome is not None else (str(exc),)
                    ),
                )
            )
            return
        except MachineProfileError as exc:
            self.failed.emit(
                ScanFailureReport(
                    status="not_ready",
                    termination_code="preflight_failed",
                    reason=str(exc),
                    restore_status="not_attempted",
                )
            )
            return
        except Exception as exc:
            self.failed.emit(
                ScanFailureReport(
                    status="failed",
                    termination_code="scan_failed",
                    reason=str(exc),
                    restore_status="verified",
                )
            )
            return
        self.finished_ok.emit(result)


class PreflightWorker(QThread):
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, context, preset, parent=None):
        super().__init__(parent)
        self.context = context
        self.preset = preset

    def run(self):
        try:
            scanner = SolenoidCenteringScanner(self.context, self.preset)
            report = scanner.preflight()
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.finished_ok.emit(report)


class PreflightReportDialog(QDialog):
    def __init__(self, report_text: str, *, ready: bool, parent=None):
        super().__init__(parent)
        self.setObjectName("preflightReportDialog")
        self.setWindowTitle("Solenoid Centering Preflight")
        self.resize(820, 560)
        self.setMinimumSize(640, 420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        summary = QLabel("READY" if ready else "NOT READY", self)
        summary.setObjectName("preflightSummary")
        summary.setProperty("tone", "success" if ready else "danger")
        layout.addWidget(summary)

        self.report_view = QPlainTextEdit(self)
        self.report_view.setObjectName("preflightReportView")
        self.report_view.setReadOnly(True)
        self.report_view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.report_view.setPlainText(report_text)
        layout.addWidget(self.report_view, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close, parent=self)
        copy_button = buttons.addButton("Copy Report", QDialogButtonBox.ActionRole)
        copy_button.clicked.connect(self._copy_report)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _copy_report(self) -> None:
        QApplication.clipboard().setText(self.report_view.toPlainText())


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        install_qt_window_raise_handler(self)
        self.context = load_app_context("solenoid_centering")
        if self.context.solenoid_centering_workflow is None:
            raise MachineProfileError("Solenoid-centering workflow is not available.")
        self.workflow = self.context.solenoid_centering_workflow
        self.worker: ScanWorker | None = None
        self.preflight_worker: PreflightWorker | None = None
        self.preflight_ready = False
        self.configuration_revision = 0
        self.active_preflight_revision: int | None = None
        self.live_plot_failed = False
        self.last_result: CenteringResult | None = None
        self.last_result_preset: SolenoidCenteringPreset | None = None
        self.current_theme = resolve_initial_theme()

        self.setWindowTitle("Solenoid Centering")
        self.resize(1600, 960)
        self.setMinimumSize(1120, 720)
        self._build_ui()
        self._load_device_choices()
        self._load_presets()
        self._refresh_write_state()
        self._apply_theme()

    def _build_ui(self):
        central = QWidget(self)
        central.setObjectName("centralRoot")
        layout = QVBoxLayout(central)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        header = QFrame(central)
        header.setObjectName("summaryPanel")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(14, 11, 14, 10)
        header_layout.setSpacing(8)
        title_row = QHBoxLayout()
        title = QLabel("Solenoid Centering", header)
        title.setObjectName("summaryTitle")
        title_row.addWidget(title)
        title_row.addStretch(1)
        title_row.addWidget(
            RuntimeContextWidget(
                machine_id=self.context.machine.id,
                machine_display_name=self.context.machine.display_name,
                control_backend=self.context.control_backend.name,
                parent=header,
            )
        )
        self.log_button = QToolButton(header)
        self.log_button.setObjectName("headerLogButton")
        self.log_button.setText("Log")
        self.log_button.setCheckable(True)
        self.log_button.setFixedSize(48, HEADER_ACTION_HEIGHT)
        self.log_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.log_button.toggled.connect(self._toggle_log)
        title_row.addWidget(self.log_button)
        self.theme_toggle_button = QToolButton(header)
        self.theme_toggle_button.setObjectName("themeToggleButton")
        self.theme_toggle_button.setFixedSize(HEADER_ACTION_HEIGHT, HEADER_ACTION_HEIGHT)
        self.theme_toggle_button.clicked.connect(self._toggle_theme)
        title_row.addWidget(self.theme_toggle_button)
        header_layout.addLayout(title_row)
        self.status_strip = StatusStrip(
            (
                ("PRESET", "--"),
                ("ACCESS", "WRITE ENABLED"),
                ("WORKFLOW", "IDLE"),
                ("READINESS", "UNCHECKED"),
                ("READBACK VERIFIED", "UNCHECKED"),
                ("RESULT QUALITY", "NOT EVALUATED"),
                ("LAST RESULT", "--"),
            ),
            header,
        )
        header_layout.addWidget(self.status_strip)
        layout.addWidget(header)

        self.splitter = QSplitter(Qt.Horizontal, central)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(8)
        self.splitter.addWidget(self._build_control_panel(self.splitter))

        workspace = QFrame(self.splitter)
        workspace.setObjectName("workspacePanel")
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)
        self.workspace_splitter = QSplitter(Qt.Vertical, workspace)
        self.workspace_splitter.setChildrenCollapsible(False)
        self.workspace_splitter.setHandleWidth(8)

        self.plot_card = QFrame(self.workspace_splitter)
        self.plot_card.setObjectName("plotCard")
        self.plot_card.setMinimumHeight(240)
        plot_layout = QVBoxLayout(self.plot_card)
        plot_layout.setContentsMargins(10, 10, 10, 10)
        plot_layout.setSpacing(6)
        self.plot = MplWidget(self.plot_card)
        plot_layout.addWidget(self.plot)

        self.result_card = QFrame(self.workspace_splitter)
        self.result_card.setObjectName("resultCard")
        self.result_card.setMinimumHeight(190)
        result_layout = QVBoxLayout(self.result_card)
        result_layout.setContentsMargins(12, 10, 12, 10)
        result_layout.setSpacing(7)
        result_header = QHBoxLayout()
        result_title = QLabel("Scan Results", self.result_card)
        result_title.setObjectName("panelTitle")
        result_header.addWidget(result_title)
        self.status_label = QLabel("Idle", self.result_card)
        self.status_label.setObjectName("resultHint")
        self.status_label.setProperty("muted", True)
        self.status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.status_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        result_header.addWidget(self.status_label, 1)
        self.apply_button = QPushButton("Apply Recommended", self.result_card)
        self.restore_button = QPushButton("Restore Original", self.result_card)
        self.apply_button.setProperty("role", "primary")
        self.apply_button.setProperty("compact", True)
        self.restore_button.setProperty("compact", True)
        self.apply_button.setVisible(False)
        self.restore_button.setVisible(False)
        self.apply_button.clicked.connect(self.apply_recommended)
        self.restore_button.clicked.connect(self.restore_original)
        result_header.addWidget(self.apply_button)
        result_header.addWidget(self.restore_button)
        result_layout.addLayout(result_header)
        self.result_table = QTableWidget(0, 7, self.result_card)
        self.result_table.setHorizontalHeaderLabels(
            ["Axis", "Iteration", "Corrector", "Score", "Length", "Slope X", "Slope Y"]
        )
        self.result_table.setAlternatingRowColors(True)
        self.result_table.setMinimumHeight(135)
        self.result_table.verticalHeader().setVisible(False)
        self.result_table.horizontalHeader().setStretchLastSection(True)
        self.result_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        result_layout.addWidget(self.result_table)

        self.workspace_splitter.addWidget(self.plot_card)
        self.workspace_splitter.addWidget(self.result_card)
        self.workspace_splitter.setStretchFactor(0, 1)
        self.workspace_splitter.setStretchFactor(1, 0)
        self.workspace_splitter.setSizes([520, 240])
        workspace_layout.addWidget(self.workspace_splitter, 1)

        self.splitter.addWidget(workspace)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([410, 1000])
        layout.addWidget(self.splitter, 1)

        self.log_view = QPlainTextEdit(central)
        self.log_view.setObjectName("logView")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(700)
        self.log_view.setMaximumHeight(170)
        self.log_view.setPlaceholderText(
            "Preflight checks, blocking reasons, readback verification, and operation errors"
        )
        self.log_view.setVisible(False)
        layout.addWidget(self.log_view)

        self.setCentralWidget(central)

    def _build_control_panel(self, parent):
        panel = QFrame(parent)
        panel.setObjectName("controlCard")
        panel.setMinimumWidth(390)
        panel.setMaximumWidth(460)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(7)

        heading = QHBoxLayout()
        heading.setContentsMargins(2, 0, 2, 0)
        title = QLabel("Configuration", panel)
        title.setObjectName("panelTitle")
        heading.addWidget(title)
        heading.addStretch(1)
        self.check_button = QPushButton("Check PVs", panel)
        self.check_button.setProperty("compact", True)
        self.check_button.clicked.connect(self.run_preflight)
        heading.addWidget(self.check_button)
        layout.addLayout(heading)

        scroll = QScrollArea(panel)
        scroll.setObjectName("configurationScroll")
        scroll.setWidgetResizable(True)
        content = QWidget(scroll)
        content.setObjectName("configurationContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(8)

        setup_card = QFrame(content)
        setup_card.setObjectName("configSectionCard")
        setup_layout = QVBoxLayout(setup_card)
        setup_layout.setContentsMargins(10, 8, 10, 10)
        setup_layout.setSpacing(6)
        setup_title = QLabel("Devices", setup_card)
        setup_title.setObjectName("sectionTitle")
        setup_layout.addWidget(setup_title)
        device_layout = QFormLayout()
        device_layout.setContentsMargins(0, 0, 0, 0)
        device_layout.setVerticalSpacing(5)
        preset_label = QLabel("Preset", setup_card)
        preset_label.setProperty("role", "field")
        self.preset_combo = QComboBox(setup_card)
        self.preset_combo.setMinimumWidth(190)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        device_layout.addRow(preset_label, self.preset_combo)
        self.solenoid_pv_label = QLabel("--", setup_card)
        self.solenoid_pv_label.setWordWrap(True)
        self.hcorr_combo = QComboBox(setup_card)
        self.vcorr_combo = QComboBox(setup_card)
        self.bpm_combo = QComboBox(setup_card)
        for label, widget in (
            ("Solenoid PV", self.solenoid_pv_label),
            ("BPM", self.bpm_combo),
        ):
            field_label = QLabel(label, content)
            field_label.setProperty("role", "field")
            device_layout.addRow(field_label, widget)
        corrector_selector = QWidget(setup_card)
        corrector_layout = QHBoxLayout(corrector_selector)
        corrector_layout.setContentsMargins(0, 0, 0, 0)
        corrector_layout.setSpacing(5)
        for plane, combo in (("H", self.hcorr_combo), ("V", self.vcorr_combo)):
            plane_label = QLabel(plane, corrector_selector)
            plane_label.setProperty("role", "field")
            corrector_layout.addWidget(plane_label)
            corrector_layout.addWidget(combo, 1)
        corrector_label = QLabel("Correctors", setup_card)
        corrector_label.setProperty("role", "field")
        device_layout.insertRow(2, corrector_label, corrector_selector)
        setup_layout.addLayout(device_layout)
        content_layout.addWidget(setup_card)

        self.scan_card = QFrame(content)
        self.scan_card.setObjectName("configSectionCard")
        scan_card_layout = QVBoxLayout(self.scan_card)
        scan_card_layout.setContentsMargins(10, 8, 10, 10)
        scan_card_layout.setSpacing(6)
        scan_title = QLabel("Scan", self.scan_card)
        scan_title.setObjectName("sectionTitle")
        scan_card_layout.addWidget(scan_title)
        self.sol_from = self._double_spin(-1e6, 1e6, 0.01, 4)
        self.sol_to = self._double_spin(-1e6, 1e6, 0.01, 4)
        self.sol_steps = self._int_spin(2, 999)
        self.cor_from = self._double_spin(-1e6, 1e6, 0.0001, 6)
        self.cor_to = self._double_spin(-1e6, 1e6, 0.0001, 6)
        self.cor_steps = self._int_spin(2, 999)
        self.samples = self._int_spin(1, 999)
        self.settle = self._double_spin(0.0, 3600.0, 0.5, 2)
        self.sample_interval = self._double_spin(0.0, 3600.0, 0.1, 2)
        self.max_iters = self._int_spin(1, 99)
        self.scoring_mode_combo = QComboBox(content)
        self.scoring_mode_combo.addItem("Slope score", SCORING_MODE_SLOPE)
        self.scoring_mode_combo.addItem("Trajectory length", SCORING_MODE_TRAJECTORY_LENGTH)

        range_title = QLabel("Relative Scan Range", self.scan_card)
        range_title.setProperty("role", "groupTitle")
        scan_card_layout.addWidget(range_title)
        range_layout = QGridLayout()
        range_layout.setContentsMargins(0, 0, 0, 0)
        range_layout.setHorizontalSpacing(5)
        range_layout.setVerticalSpacing(5)
        for column, label in enumerate(("From", "To", "Steps"), start=1):
            header_label = QLabel(label, self.scan_card)
            header_label.setProperty("role", "columnHeader")
            header_label.setAlignment(Qt.AlignCenter)
            range_layout.addWidget(header_label, 0, column)
        for row, (label, widgets) in enumerate(
            (
                ("SOL", (self.sol_from, self.sol_to, self.sol_steps)),
                ("COR", (self.cor_from, self.cor_to, self.cor_steps)),
            ),
            start=1,
        ):
            row_label = QLabel(label, self.scan_card)
            row_label.setProperty("role", "field")
            range_layout.addWidget(row_label, row, 0)
            for column, widget in enumerate(widgets, start=1):
                widget.setMinimumWidth(0)
                widget.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
                range_layout.addWidget(widget, row, column)
        range_layout.setColumnStretch(1, 3)
        range_layout.setColumnStretch(2, 3)
        range_layout.setColumnStretch(3, 2)
        scan_card_layout.addLayout(range_layout)

        acquisition_title = QLabel("Acquisition", self.scan_card)
        acquisition_title.setProperty("role", "groupTitle")
        scan_card_layout.addWidget(acquisition_title)
        acquisition_layout = QFormLayout()
        acquisition_layout.setContentsMargins(0, 0, 0, 0)
        acquisition_layout.setVerticalSpacing(5)
        for label, widget in (
            ("Samples/Step", self.samples),
            ("Settle Time", self.settle),
            ("Sample interval s", self.sample_interval),
        ):
            field_label = QLabel(label, self.scan_card)
            field_label.setProperty("role", "field")
            acquisition_layout.addRow(field_label, widget)
        scan_card_layout.addLayout(acquisition_layout)
        content_layout.addWidget(self.scan_card)

        self.run_card = QFrame(content)
        self.run_card.setObjectName("configSectionCard")
        run_layout = QVBoxLayout(self.run_card)
        run_layout.setContentsMargins(10, 8, 10, 10)
        run_layout.setSpacing(7)
        run_title = QLabel("Run", self.run_card)
        run_title.setObjectName("sectionTitle")
        run_layout.addWidget(run_title)

        run_settings = QFormLayout()
        run_settings.setContentsMargins(0, 0, 0, 0)
        run_settings.setVerticalSpacing(5)
        for label, widget in (
            ("Score mode", self.scoring_mode_combo),
            ("Max iterations", self.max_iters),
        ):
            field_label = QLabel(label, self.run_card)
            field_label.setProperty("role", "field")
            run_settings.addRow(field_label, widget)
        run_layout.addLayout(run_settings)

        self.progress = QProgressBar(self.run_card)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        run_layout.addWidget(self.progress)

        buttons = QHBoxLayout()
        self.start_button = QPushButton("Start Scan", self.run_card)
        self.stop_button = QPushButton("Abort", self.run_card)
        self.start_button.setProperty("role", "primary")
        self.stop_button.setProperty("role", "danger")
        self.stop_button.setEnabled(False)
        self.stop_button.setVisible(False)
        self.start_button.clicked.connect(self.start_scan)
        self.stop_button.clicked.connect(self.stop_scan)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.stop_button)
        run_layout.addLayout(buttons)

        content_layout.addWidget(self.run_card)
        content_layout.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        self.preflight_inputs = (
            self.preset_combo,
            self.hcorr_combo,
            self.vcorr_combo,
            self.bpm_combo,
            self.scoring_mode_combo,
            self.sol_from,
            self.sol_to,
            self.sol_steps,
            self.cor_from,
            self.cor_to,
            self.cor_steps,
            self.samples,
            self.settle,
            self.sample_interval,
            self.max_iters,
        )
        for combo in (
            self.preset_combo,
            self.hcorr_combo,
            self.vcorr_combo,
            self.bpm_combo,
            self.scoring_mode_combo,
        ):
            combo.currentIndexChanged.connect(self._invalidate_preflight)
        for spin in (
            self.sol_from,
            self.sol_to,
            self.sol_steps,
            self.cor_from,
            self.cor_to,
            self.cor_steps,
            self.samples,
            self.settle,
            self.sample_interval,
            self.max_iters,
        ):
            spin.valueChanged.connect(self._invalidate_preflight)
        return panel

    @staticmethod
    def _double_spin(minimum, maximum, step, decimals):
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setSingleStep(step)
        spin.setDecimals(decimals)
        return spin

    @staticmethod
    def _int_spin(minimum, maximum):
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        return spin

    def _apply_theme(self):
        palette = theme_palette(self.current_theme)
        self.setStyleSheet(build_stylesheet(palette))
        self.plot.set_theme(palette)
        if self.current_theme == "dark":
            self.theme_toggle_button.setText("\u2600")
            self.theme_toggle_button.setToolTip("Switch to light theme.")
        else:
            self.theme_toggle_button.setText("\u263d")
            self.theme_toggle_button.setToolTip("Switch to dark theme.")

    def _toggle_theme(self):
        self.current_theme = "light" if self.current_theme == "dark" else "dark"
        self._apply_theme()

    def _load_device_choices(self):
        self._populate_element_combo(
            self.hcorr_combo,
            self._corrector_choices("x", ("HC", "HIC", "XCOR")),
        )
        self._populate_element_combo(
            self.vcorr_combo,
            self._corrector_choices("y", ("VC", "VIC", "YCOR")),
        )
        bpms = [
            element
            for element in list_elements(self.context, kind="bpm", logical_channel="x")
            if "y" in element.channels
        ]
        self._populate_element_combo(self.bpm_combo, bpms)

    def _corrector_choices(self, plane, fallback_tokens):
        correctors = list_elements(self.context, kind="corr", logical_channel="current_set")
        by_plane = [element for element in correctors if element.plane == plane]
        if by_plane:
            return by_plane
        tokens = tuple(token.upper() for token in fallback_tokens)
        by_name = [
            element
            for element in correctors
            if any(
                token in f"{element.id} {element.display_name}".upper()
                for token in tokens
            )
        ]
        return by_name or correctors

    @staticmethod
    def _populate_element_combo(combo, elements):
        combo.clear()
        for element in elements:
            combo.addItem(element.display_name or element.id, element.id)

    def _load_presets(self):
        self.preset_combo.clear()
        for preset in self.workflow.presets:
            self.preset_combo.addItem(preset.display_name, preset.id)
        index = self.preset_combo.findData(self.workflow.default_preset)
        if index >= 0:
            self.preset_combo.setCurrentIndex(index)
        self._on_preset_changed()

    def _current_preset(self) -> SolenoidCenteringPreset:
        preset_id = self.preset_combo.currentData()
        return self.workflow.presets_by_id[str(preset_id)]

    def _on_preset_changed(self):
        if self.preset_combo.count() <= 0:
            return
        preset = self._current_preset()
        self.status_strip.set_value("PRESET", preset.display_name)
        self.solenoid_pv_label.setText(self._solenoid_setpoint_label(preset))
        self._set_combo_value(self.hcorr_combo, preset.hcorr, "HCOR")
        self._set_combo_value(self.vcorr_combo, preset.vcorr, "VCOR")
        self._set_combo_value(self.bpm_combo, preset.bpm, "BPM")
        self.sol_from.setValue(preset.solenoid_scan.relative_from)
        self.sol_to.setValue(preset.solenoid_scan.relative_to)
        self.sol_steps.setValue(preset.solenoid_scan.steps)
        self.cor_from.setValue(preset.corrector_scan.relative_from)
        self.cor_to.setValue(preset.corrector_scan.relative_to)
        self.cor_steps.setValue(preset.corrector_scan.steps)
        self.samples.setValue(preset.samples_per_point)
        self.settle.setValue(preset.settle_time_s)
        self.sample_interval.setValue(preset.sample_interval_s)
        self.max_iters.setValue(preset.max_rounds)

    def _solenoid_setpoint_label(self, preset: SolenoidCenteringPreset) -> str:
        if preset.solenoid:
            try:
                return resolve_channel(self.context, preset.solenoid, "current_set")
            except MachineProfileError:
                return preset.solenoid
        return preset.solenoid_setpoint_pv or ""

    @staticmethod
    def _set_combo_value(combo, value, label):
        index = combo.findData(value)
        if index < 0:
            raise MachineProfileError(f"{label} {value!r} is not available for this machine/backend.")
        combo.setCurrentIndex(index)

    @staticmethod
    def _combo_value(combo, label) -> str:
        value = combo.currentData()
        if value is None:
            raise MachineProfileError(f"{label} selection is empty.")
        return str(value)

    def _scoring_mode(self) -> str:
        return normalize_scoring_mode(self.scoring_mode_combo.currentData())

    def _preset_with_overrides(self) -> SolenoidCenteringPreset:
        preset = self._current_preset()
        if self.sol_from.value() == self.sol_to.value():
            raise ValueError("Solenoid scan range must not be zero.")
        if self.cor_from.value() == self.cor_to.value():
            raise ValueError("Corrector scan range must not be zero.")
        return replace(
            preset,
            hcorr=self._combo_value(self.hcorr_combo, "HCOR"),
            vcorr=self._combo_value(self.vcorr_combo, "VCOR"),
            bpm=self._combo_value(self.bpm_combo, "BPM"),
            solenoid_scan=SolenoidCenteringScanRange(
                relative_from=self.sol_from.value(),
                relative_to=self.sol_to.value(),
                steps=self.sol_steps.value(),
            ),
            corrector_scan=SolenoidCenteringScanRange(
                relative_from=self.cor_from.value(),
                relative_to=self.cor_to.value(),
                steps=self.cor_steps.value(),
            ),
            samples_per_point=self.samples.value(),
            settle_time_s=self.settle.value(),
            sample_interval_s=self.sample_interval.value(),
            max_rounds=self.max_iters.value(),
        )

    def _refresh_write_state(self):
        allowed = workflow_writes_allowed(self.context, "solenoid_centering")
        self.start_button.setEnabled(allowed and self.preflight_ready)
        self.status_strip.set_value("ACCESS", "WRITE ENABLED" if allowed else "READ ONLY",
                                    "success" if allowed else "warning")
        if self.context.control_backend.name != "real":
            self._set_workflow_status("REAL ONLY", "danger")
        elif not allowed:
            self._set_workflow_status("READ ONLY", "warning")
        else:
            self._set_workflow_status("IDLE", "subtle")

    def _set_workflow_status(self, value: str, tone: str = "subtle") -> None:
        self.status_strip.set_value("WORKFLOW", value, tone)
        self.status_label.setText(value)

    def _invalidate_preflight(self, *_args) -> None:
        self.configuration_revision += 1
        self.preflight_ready = False
        self._set_result_action(None)
        self.status_strip.set_value("READINESS", "UNCHECKED", "warning")
        self.status_strip.set_value("READBACK VERIFIED", "UNCHECKED", "warning")
        self.start_button.setEnabled(False)

    def _set_preflight_inputs_enabled(self, enabled: bool) -> None:
        for widget in self.preflight_inputs:
            widget.setEnabled(enabled)

    def _set_result_action(self, action: str | None) -> None:
        self.apply_button.setVisible(action == "apply")
        self.apply_button.setEnabled(action == "apply")
        self.restore_button.setVisible(action == "restore")
        self.restore_button.setEnabled(action == "restore")

    def run_preflight(self):
        if self.worker is not None and self.worker.isRunning():
            return
        if self.preflight_worker is not None and self.preflight_worker.isRunning():
            return
        try:
            preset = self._preset_with_overrides()
        except Exception as exc:
            QMessageBox.warning(self, "Solenoid Centering", str(exc))
            return
        self._set_workflow_status("CHECKING PVs", "warning")
        self.status_strip.set_value("READINESS", "CHECKING", "warning")
        self.preflight_ready = False
        self.active_preflight_revision = self.configuration_revision
        self._append_log("Starting read-only preflight.")
        self.progress.setValue(0)
        self.preflight_worker = PreflightWorker(self.context, preset, self)
        self.preflight_worker.finished_ok.connect(self._on_preflight_finished)
        self.preflight_worker.failed.connect(self._on_preflight_failed)
        self.preflight_worker.finished.connect(self._on_preflight_done)
        self.preflight_worker.start()
        self.check_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self._set_preflight_inputs_enabled(False)

    def start_scan(self):
        if self.worker is not None and self.worker.isRunning():
            return
        if self.preflight_worker is not None and self.preflight_worker.isRunning():
            return
        if not self.preflight_ready:
            QMessageBox.warning(
                self,
                "Solenoid Centering",
                "Configuration changed or has not been checked. Run Check PVs again.",
            )
            return
        try:
            preset = self._preset_with_overrides()
        except Exception as exc:
            QMessageBox.warning(self, "Solenoid Centering", str(exc))
            return
        self.last_result = None
        self.last_result_preset = None
        self._set_result_action(None)
        self.status_strip.set_value("LAST RESULT", "--")
        self.result_table.setRowCount(0)
        self.live_plot_failed = False
        self.plot.clear()
        self.plot.start_live()
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self._set_workflow_status("RUNNING", "warning")
        self.status_strip.set_value("READINESS", "SCANNING", "warning")
        self.worker = ScanWorker(self.context, preset, self._scoring_mode(), self)
        self.worker.progress_changed.connect(self._on_progress)
        self.worker.candidate_finished.connect(self._on_candidate_finished)
        self.worker.finished_ok.connect(self._on_scan_finished)
        self.worker.failed.connect(self._on_scan_failed)
        self.worker.finished.connect(self._on_worker_done)
        self.worker.start()
        self.check_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self._set_scan_action_running(True)
        self._set_preflight_inputs_enabled(False)

    def stop_scan(self):
        if self.worker is not None and self.worker.isRunning():
            self._set_workflow_status("STOPPING", "warning")
            self._set_scan_action_running(True, stopping=True)
            self.worker.request_stop()

    def _set_scan_action_running(
        self,
        running: bool,
        *,
        stopping: bool = False,
    ) -> None:
        self.start_button.setVisible(not running)
        self.stop_button.setVisible(running)
        self.stop_button.setText("Stopping..." if stopping else "Abort")
        self.stop_button.setEnabled(running and not stopping)

    def apply_recommended(self):
        if self.last_result is None or self.last_result_preset is None:
            return
        if not self.last_result.recommendation_available:
            self._set_workflow_status("NO VALID RECOMMENDATION", "warning")
            self.status_strip.set_value("RESULT QUALITY", "NO VALID RECOMMENDATION", "warning")
            return
        if not self._confirm_result_action("Apply Recommended", self.last_result, apply=True):
            return
        try:
            scanner = SolenoidCenteringScanner(self.context, self.last_result_preset)
            scanner.apply_recommended(self.last_result)
        except StateDriftError as exc:
            self._set_workflow_status("STATE DRIFT", "danger")
            self.status_strip.set_value("READINESS", "STATE DRIFT", "danger")
            self._set_result_action(None)
            QMessageBox.warning(self, "Solenoid Centering", str(exc))
            return
        except MotionVerificationError as exc:
            self._set_workflow_status("APPLY ROLLED BACK", "warning")
            self.status_strip.set_value("READBACK VERIFIED", "FAILED", "danger")
            QMessageBox.warning(self, "Solenoid Centering", str(exc))
            return
        except Exception as exc:
            self._set_workflow_status("ERROR", "danger")
            QMessageBox.warning(self, "Solenoid Centering", str(exc))
            return
        self._set_workflow_status("RECOMMENDATION APPLIED", "success")
        self.status_strip.set_value("READBACK VERIFIED", "VERIFIED", "success")
        self._set_result_action("restore")

    def restore_original(self):
        if self.last_result is None or self.last_result_preset is None:
            return
        if not self._confirm_result_action("Restore Original", self.last_result, apply=False):
            return
        try:
            scanner = SolenoidCenteringScanner(self.context, self.last_result_preset)
            scanner.restore_original(self.last_result)
        except StateDriftError as exc:
            self._set_workflow_status("STATE DRIFT", "danger")
            self.status_strip.set_value("READINESS", "STATE DRIFT", "danger")
            self._set_result_action(None)
            QMessageBox.warning(self, "Solenoid Centering", str(exc))
            return
        except RestoreFailed as exc:
            self._set_workflow_status("RESTORE FAILED", "danger")
            self.status_strip.set_value("READBACK VERIFIED", "FAILED", "danger")
            QMessageBox.warning(self, "Solenoid Centering", str(exc))
            return
        except MotionVerificationError as exc:
            self._set_workflow_status("RESTORE ROLLBACK", "warning")
            self.status_strip.set_value("READBACK VERIFIED", "FAILED", "danger")
            QMessageBox.warning(self, "Solenoid Centering", str(exc))
            return
        except Exception as exc:
            self._set_workflow_status("ERROR", "danger")
            QMessageBox.warning(self, "Solenoid Centering", str(exc))
            return
        self._set_workflow_status("ORIGINAL RESTORED", "success")
        self.status_strip.set_value("READBACK VERIFIED", "VERIFIED", "success")
        self._set_result_action(None)

    def _on_progress(self, message, completed, total):
        percent = int(round(completed / total * 100)) if total else 0
        self.progress.setValue(max(0, min(100, percent)))
        self.status_label.setText(f"{message} ({completed}/{total})")

    def _on_candidate_finished(self, candidate):
        if self.live_plot_failed:
            return
        try:
            self.plot.add_live_candidate(candidate)
        except Exception as exc:
            self.live_plot_failed = True
            self._append_log(f"Live plot update failed; scan continues without plotting: {exc}")
            self.status_label.setText("Live plot unavailable; scan continues. See Log.")

    def _on_preflight_finished(self, report):
        report_text = report.as_text()
        self._append_log(report_text)
        revision_matches = self.active_preflight_revision == self.configuration_revision
        self.preflight_ready = report.is_ready and revision_matches
        if self.preflight_ready:
            self.progress.setValue(100)
            self._set_workflow_status("READY", "success")
            self.status_strip.set_value("READINESS", "READY", "success")
            self.status_strip.set_value("READBACK VERIFIED", "VERIFIED", "success")
            self._show_preflight_report(report_text, ready=True)
            return
        if report.is_ready and not revision_matches:
            report_text += "\nSTALE: configuration changed while preflight was running."
            self._append_log("Preflight result discarded because configuration changed.")
        self._set_workflow_status("NOT READY", "danger")
        self.status_strip.set_value("READINESS", "NOT READY", "danger")
        self.status_strip.set_value("READBACK VERIFIED", "FAILED", "danger")
        self.status_label.setText(self._preflight_blocker_summary(report_text))
        self._show_preflight_report(report_text, ready=False)

    def _on_preflight_failed(self, message):
        report_text = f"NOT READY\n\nPreflight execution failed:\n{message}"
        self._append_log(report_text)
        self.preflight_ready = False
        self._set_workflow_status("NOT READY", "danger")
        self.status_strip.set_value("READINESS", "NOT READY", "danger")
        self.status_strip.set_value("READBACK VERIFIED", "FAILED", "danger")
        self.status_label.setText(f"Preflight failed: {message}")
        self._show_preflight_report(report_text, ready=False)

    def _show_preflight_report(self, report_text: str, *, ready: bool) -> None:
        dialog = PreflightReportDialog(report_text, ready=ready, parent=self)
        dialog.exec_()

    @staticmethod
    def _preflight_blocker_summary(report_text: str) -> str:
        blocker_prefixes = (
            "LIMIT UNCONFIGURED",
            "OUT OF LIMIT",
            "INSUFFICIENT CANDIDATES",
            "NOT VERIFIED",
        )
        for line in report_text.splitlines():
            if line.startswith(blocker_prefixes):
                return f"Blocked: {line}"
        return "Preflight failed; see Log for details."

    def _toggle_log(self, checked: bool) -> None:
        self.log_view.setVisible(checked)

    def _append_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        lines = str(message).splitlines() or [""]
        self.log_view.appendPlainText(f"{timestamp} {lines[0]}")
        for line in lines[1:]:
            self.log_view.appendPlainText(f"         {line}")

    def _on_preflight_done(self):
        self._set_preflight_inputs_enabled(True)
        self.check_button.setEnabled(True)
        self.start_button.setEnabled(
            self.preflight_ready
            and workflow_writes_allowed(self.context, "solenoid_centering")
        )

    def _on_scan_finished(self, result):
        self.last_result = result
        self.last_result_preset = self.worker.preset if self.worker is not None else None
        self.progress.setValue(100)
        self._set_workflow_status("RESULT READY", "success")
        self.status_strip.set_value("READINESS", "RESULT READY", "success")
        self.status_strip.set_value("READBACK VERIFIED", "VERIFIED", "success")
        termination_reason = (
            result.termination.reason
            if result.termination is not None
            else "Scan completed without a termination record."
        )
        termination_code = result.termination.code if result.termination is not None else "unknown"
        self._append_log(
            f"Scan termination: {termination_code}\n"
            f"Reason: {termination_reason}\n"
            f"Restore status: {result.restore.status.upper() if result.restore else 'UNKNOWN'}"
        )
        self.status_strip.set_value(
            "LAST RESULT",
            f"H {result.recommended_hcorr:.5g}, V {result.recommended_vcorr:.5g}",
            "success",
        )
        if result.recommendation_available:
            self.status_strip.set_value("RESULT QUALITY", "VALID", "success")
            if result.termination is not None and result.termination.early:
                self._set_workflow_status("CONVERGED", "success")
            self.status_label.setText(
                f"Quality passed: {result.relative_improvement:.1%}. {termination_reason}"
            )
        else:
            quality_label = (
                "BOUNDARY LIMITED"
                if result.termination is not None
                and result.termination.code == "boundary_limited"
                else "NO VALID RECOMMENDATION"
            )
            self.status_strip.set_value("RESULT QUALITY", quality_label, "warning")
            self._set_workflow_status(quality_label, "warning")
            self.status_label.setText(result.recommendation_status)
        action = (
            "apply"
            if result.recommendation_available
            and workflow_writes_allowed(self.context, "solenoid_centering")
            else None
        )
        self._set_result_action(action)
        self._populate_result_table(result)
        try:
            self.plot.plot_result(result)
        except Exception as exc:
            self.live_plot_failed = True
            self._append_log(f"Final plot update failed; result data remains available: {exc}")
            self.status_label.setText("Result saved; plot unavailable. See Log.")

    def _on_scan_failed(self, report):
        if not isinstance(report, ScanFailureReport):
            report = ScanFailureReport(
                status="failed",
                termination_code="scan_failed",
                reason=str(report),
                restore_status="unknown",
            )
        self.preflight_ready = False
        labels = {
            "stopped": ("STOPPED / RESTORED", "warning", "STOPPED"),
            "failed": ("SCAN FAILED / RESTORED", "danger", "SCAN FAILED"),
            "restore_failed": ("RESTORE FAILED", "danger", "RESTORE FAILED"),
            "not_ready": ("NOT READY", "danger", "NOT READY"),
        }
        workflow_label, tone, readiness_label = labels.get(
            report.status,
            ("ERROR", "danger", "ERROR"),
        )
        self._set_workflow_status(workflow_label, tone)
        self.status_strip.set_value("READINESS", readiness_label, tone)
        readback_label = {
            "verified": "VERIFIED",
            "failed": "FAILED",
            "not_attempted": "NOT ATTEMPTED",
        }.get(report.restore_status, "UNKNOWN")
        readback_tone = "success" if report.restore_status == "verified" else "danger"
        self.status_strip.set_value("READBACK VERIFIED", readback_label, readback_tone)
        log_lines = [
            f"Scan termination: {report.termination_code}",
            f"Reason: {report.reason}",
            f"Restore status: {report.restore_status.upper()}",
        ]
        log_lines.extend(f"Restore error: {error}" for error in report.restore_errors)
        self._append_log("\n".join(log_lines))
        self.status_label.setText(workflow_label)
        QMessageBox.warning(self, "Solenoid Centering", "\n".join(log_lines))

    def _confirm_result_action(self, title: str, result: CenteringResult, *, apply: bool) -> bool:
        preflight = result.preflight or {}
        selected = result.selected_devices or {}
        targets = (
            (result.recommended_hcorr, result.recommended_vcorr)
            if apply
            else (result.original_hcorr, result.original_vcorr)
        )
        h_limit, v_limit = self._result_limits(preflight)
        text = (
            f"HCOR {selected.get('hcorr_setpoint_pv', preflight.get('hcorr_pv', '--'))}\n"
            f"  original {result.original_hcorr:.8g} -> target {targets[0]:.8g} "
            f"(delta {targets[0] - result.original_hcorr:+.8g}), limits {h_limit}\n"
            f"VCOR {selected.get('vcorr_setpoint_pv', preflight.get('vcorr_pv', '--'))}\n"
            f"  original {result.original_vcorr:.8g} -> target {targets[1]:.8g} "
            f"(delta {targets[1] - result.original_vcorr:+.8g}), limits {v_limit}\n\n"
            f"Quality: {result.recommendation_status}; "
            f"relative improvement {result.relative_improvement:.1%}\n"
            "Current setpoint and readback will be revalidated before writing."
        )
        return QMessageBox.question(
            self,
            title,
            text,
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        ) == QMessageBox.Yes

    @staticmethod
    def _result_limits(preflight: dict) -> tuple[str, str]:
        limits = [
            item for item in preflight.get("range_checks", []) if isinstance(item, dict)
        ]

        def display(label: str) -> str:
            item = next((item for item in limits if item.get("label") == label), {})
            low, high = item.get("limit_low"), item.get("limit_high")
            return f"[{low:g}, {high:g}]" if low is not None and high is not None else "unconfigured"

        return display("HCOR"), display("VCOR")

    def _on_worker_done(self):
        self._set_preflight_inputs_enabled(True)
        self.check_button.setEnabled(True)
        self.start_button.setEnabled(
            self.preflight_ready
            and workflow_writes_allowed(self.context, "solenoid_centering")
        )
        self._set_scan_action_running(False)
        self.progress.setVisible(False)

    def _populate_result_table(self, result):
        rows = [scan.best for scan in result.axis_scans]
        self.result_table.setRowCount(len(rows))
        for row, candidate in enumerate(rows):
            values = [
                candidate.axis.upper(),
                str(candidate.round_index + 1),
                f"{candidate.corrector_value:.8g}",
                f"{candidate.score.score:.6g}",
                f"{candidate.score.trajectory_length:.6g}",
                f"{candidate.score.slope_x:.6g}",
                f"{candidate.score.slope_y:.6g}",
            ]
            for col, value in enumerate(values):
                self.result_table.setItem(row, col, QTableWidgetItem(value))
        self.result_table.resizeColumnsToContents()


def main() -> int:
    app = QApplication(sys.argv)
    try:
        window = MainWindow()
    except MachineProfileError as exc:
        QMessageBox.critical(
            None,
            "Solenoid Centering Unavailable",
            str(exc),
        )
        return 2
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
