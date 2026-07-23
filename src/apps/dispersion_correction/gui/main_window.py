from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import math
from pathlib import Path
import re

from PyQt5.QtCore import QPointF, QRectF, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen, QPolygonF
from PyQt5.QtWidgets import (
    QFileDialog,
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtCore import Qt

from half_linac.src.apps.dispersion_correction.calibration import load_phase_calibration_csv
from half_linac.src.apps.dispersion_correction.config import load_config
from half_linac.src.apps.dispersion_correction.dryrun import build_operation_plan, format_operation_plan
from half_linac.src.apps.dispersion_correction.gui.theme import build_stylesheet, theme_tokens
from half_linac.src.apps.dispersion_correction.gui.widgets import StatusStrip
from half_linac.src.apps.dispersion_correction.models import (
    CorrectionResult,
    DispersionMeasurement,
    ImportedDispersionDataset,
    KnobConfig,
    ModelResponseResult,
    ResponseMatrixResult,
    RunConfig,
)
from half_linac.src.apps.dispersion_correction.measurement_import import load_dispersion_csv
from half_linac.src.apps.dispersion_correction.model_response import (
    calculate_model_response,
    format_model_response,
)
from half_linac.src.apps.dispersion_correction.preflight import run_live_preflight, run_preflight
from half_linac.src.apps.dispersion_correction.profile_runtime import (
    default_offline_config,
    apply_profile_selection,
    load_profile_run_config,
    profile_section_choices,
    selectable_profile_bpms,
    selectable_profile_quadrupoles,
    write_profile_operation,
)
from half_linac.src.apps.dispersion_correction.reports import result_to_markdown
from half_linac.src.apps.dispersion_correction.workflow import AchromatWorkflow
from half_linac.src.shared.app_theme import resolve_initial_theme
from half_linac.src.shared.machine_profile import AppContext, workflow_writes_allowed
from half_linac.src.shared.window_activation import install_qt_window_raise_handler


class WorkflowWorker(QThread):
    log = pyqtSignal(str)
    progress = pyqtSignal(str, int, int)
    failed = pyqtSignal(str)
    completed = pyqtSignal(str, object)
    preflight = pyqtSignal(object)

    def __init__(self, task: str, config: RunConfig) -> None:
        super().__init__()
        self.task = task
        self.config = config

    def run(self) -> None:
        try:
            workflow = AchromatWorkflow(
                self.config,
                log_callback=self.log.emit,
                cancellation_callback=self.isInterruptionRequested,
                progress_callback=self._emit_progress,
                preflight_callback=self.preflight.emit,
            )
            if self.task == "measure":
                result = workflow.measure_dispersion(self.config.measurement.final_samples)
            elif self.task == "response":
                result = workflow.build_response_matrix()
            elif self.task == "run":
                result = workflow.run()
            else:
                raise ValueError(f"Unknown task: {self.task}")
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.completed.emit(self.task, result)

    def _emit_progress(self, stage: str, current: int, total: int) -> None:
        self.progress.emit(stage, current, total)
        if self.config.backend.type.lower() != "offline":
            return
        delay_s = float(self.config.backend.options.get("gui_progress_delay_s", 0.0))
        if delay_s > 0:
            self.msleep(round(1000.0 * delay_s))


class LivePreflightWorker(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, config: RunConfig) -> None:
        super().__init__()
        self.config = config

    def run(self) -> None:
        try:
            result = run_live_preflight(self.config)
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.completed.emit(result)


class ModelResponseWorker(QThread):
    progress = pyqtSignal(str, int, int)
    failed = pyqtSignal(str)
    completed = pyqtSignal(object)

    def __init__(self, context: AppContext, config: RunConfig, model_source: str) -> None:
        super().__init__()
        self.context = context
        self.config = config
        self.model_source = model_source

    def run(self) -> None:
        try:
            result = calculate_model_response(
                self.context,
                self.config,
                model_source=self.model_source,
                progress_callback=self.progress.emit,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.completed.emit(result)


class FullWidthTabWidget(QTabWidget):
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.tabBar().setFixedWidth(self.contentsRect().width())


class DispersionCurveWidget(QWidget):
    DEFAULT_TOOLTIP = (
        "Dotted: design reference; solid: selected baseline; dashed: correction preview. "
        "Circles: imported eta_x. Red: horizontal; blue: vertical. "
        "Move over the lattice strip for element details."
    )

    def __init__(self) -> None:
        super().__init__()
        self.result: ModelResponseResult | None = None
        self.measurement: ImportedDispersionDataset | None = None
        self.theme_name = "night_shift"
        self._lattice_geometry: tuple[QRectF, float, float] | None = None
        self.setMinimumHeight(300)
        self.setMouseTracking(True)
        self.setToolTip(self.DEFAULT_TOOLTIP)

    def set_result(self, result: ModelResponseResult | None) -> None:
        self.result = result
        if result is None:
            self._lattice_geometry = None
            self.setToolTip(self.DEFAULT_TOOLTIP)
        self.update()

    def set_measurement(self, measurement: ImportedDispersionDataset | None) -> None:
        self.measurement = measurement
        self.update()

    def set_theme(self, name: str) -> None:
        self.theme_name = name
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        tokens = theme_tokens(self.theme_name)
        painter.fillRect(self.rect(), QColor(tokens["plot_bg"]))
        plot = self.rect().adjusted(58, 24, -18, -112)
        painter.setPen(QColor(tokens["text_muted"]))
        painter.drawText(12, 18, "Dispersion η (mm)")
        if self.result is None or plot.width() <= 0 or plot.height() <= 0:
            painter.drawText(plot, Qt.AlignCenter, "Calculate model response to display optics")
            return

        curves = (
            self.result.baseline_curve.dx_mm,
            self.result.baseline_curve.dy_mm,
            self.result.preview_curve.dx_mm,
            self.result.preview_curve.dy_mm,
        )
        if self.result.design_curve is not None:
            curves = curves + (
                self.result.design_curve.dx_mm,
                self.result.design_curve.dy_mm,
            )
        limit = max((abs(float(value)) for curve in curves for value in curve), default=1.0)
        if self.measurement is not None:
            for value, sigma in zip(
                self.measurement.etax_mm,
                self.measurement.etax_sigma_mm,
            ):
                uncertainty = float(sigma) if math.isfinite(float(sigma)) else 0.0
                limit = max(limit, abs(float(value)) + uncertainty)
        limit = max(limit * 1.1, 1.0e-6)
        s_values = self.result.baseline_curve.s_m
        s_min = float(s_values[0])
        s_max = float(s_values[-1])
        s_span = max(s_max - s_min, 1.0e-12)

        grid_pen = QPen(QColor(tokens["section_border"]))
        grid_pen.setWidth(1)
        painter.setPen(grid_pen)
        for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
            y = plot.top() + fraction * plot.height()
            painter.drawLine(plot.left(), round(y), plot.right(), round(y))
        zero_y = plot.center().y()
        painter.setPen(QPen(QColor(tokens["text_muted"]), 1))
        painter.drawLine(plot.left(), zero_y, plot.right(), zero_y)

        def points(s_axis, values) -> QPolygonF:
            return QPolygonF(
                [
                    QPointF(
                        plot.left() + (float(s) - s_min) / s_span * plot.width(),
                        plot.center().y() - float(value) / limit * plot.height() / 2.0,
                    )
                    for s, value in zip(s_axis, values)
                ]
            )

        horizontal = QColor("#e66b5b")
        vertical = QColor("#4c9be8")
        if self.result.design_curve is not None:
            for color, curve in (
                (horizontal, self.result.design_curve.dx_mm),
                (vertical, self.result.design_curve.dy_mm),
            ):
                design_color = QColor(color)
                design_color.setAlpha(120)
                painter.setPen(QPen(design_color, 2, Qt.DotLine))
                painter.drawPolyline(points(self.result.design_curve.s_m, curve))
        for color, curve in (
            (horizontal, self.result.baseline_curve.dx_mm),
            (vertical, self.result.baseline_curve.dy_mm),
        ):
            painter.setPen(QPen(color, 1))
            painter.drawPolyline(points(self.result.baseline_curve.s_m, curve))
        for color, curve in (
            (horizontal, self.result.preview_curve.dx_mm),
            (vertical, self.result.preview_curve.dy_mm),
        ):
            pen = QPen(color, 2, Qt.DashLine)
            painter.setPen(pen)
            painter.drawPolyline(points(self.result.preview_curve.s_m, curve))
        if self.measurement is not None:
            s_by_name = {
                name: float(self.result.baseline_curve.s_m[index])
                for index, name in enumerate(self.result.baseline_curve.element_names)
            }
            measured_color = QColor("#f2c14e")
            painter.setPen(QPen(measured_color, 2))
            painter.setBrush(measured_color)
            for bpm, value, sigma in zip(
                self.measurement.bpm_names,
                self.measurement.etax_mm,
                self.measurement.etax_sigma_mm,
            ):
                if bpm not in s_by_name:
                    continue
                x = plot.left() + (s_by_name[bpm] - s_min) / s_span * plot.width()
                y = plot.center().y() - float(value) / limit * plot.height() / 2.0
                if math.isfinite(float(sigma)):
                    half_height = float(sigma) / limit * plot.height() / 2.0
                    painter.drawLine(QPointF(x, y - half_height), QPointF(x, y + half_height))
                    painter.drawLine(
                        QPointF(x - 3.0, y - half_height),
                        QPointF(x + 3.0, y - half_height),
                    )
                    painter.drawLine(
                        QPointF(x - 3.0, y + half_height),
                        QPointF(x + 3.0, y + half_height),
                    )
                painter.drawEllipse(QPointF(x, y), 4.0, 4.0)

        painter.setPen(QColor(tokens["text_muted"]))
        painter.drawText(4, plot.top() + 5, f"{limit:.3g}")
        painter.drawText(4, plot.bottom(), f"{-limit:.3g}")
        painter.setPen(horizontal)
        painter.drawText(plot.left() + 8, plot.top() + 16, "ηx")
        painter.setPen(vertical)
        painter.drawText(plot.left() + 38, plot.top() + 16, "ηy")
        painter.setPen(QColor(tokens["text_muted"]))
        painter.drawText(
            plot.left() + 75,
            plot.top() + 16,
            "design ···  baseline —  preview --  measured ●",
        )
        lattice_rect = QRectF(
            float(plot.left()),
            float(plot.bottom() + 20),
            float(plot.width()),
            58.0,
        )
        self._lattice_geometry = (lattice_rect, s_min, s_max)
        self._draw_lattice(
            painter,
            lattice_rect,
            s_min,
            s_max,
            float(plot.top()),
            tokens,
        )
        painter.setPen(QColor(tokens["text_muted"]))
        painter.drawText(plot.left(), self.height() - 10, f"{s_min:.3g} m")
        painter.drawText(plot.right() - 45, self.height() - 10, f"{s_max:.3g} m")

    def _draw_lattice(
        self,
        painter: QPainter,
        rect: QRectF,
        s_min: float,
        s_max: float,
        constraint_top: float,
        tokens: dict[str, str],
    ) -> None:
        if self.result is None:
            return
        curve = self.result.baseline_curve
        span = max(s_max - s_min, 1.0e-12)
        center_y = rect.top() + 28.0

        def x_at(s_value: float) -> float:
            return rect.left() + (s_value - s_min) / span * rect.width()

        painter.setPen(QPen(QColor(tokens["text_muted"]), 1))
        painter.drawLine(
            QPointF(rect.left(), center_y),
            QPointF(rect.right(), center_y),
        )
        self._draw_lattice_legend(painter, rect, tokens)
        constraint_elements = set(self.result.observable_elements)
        for index in self._visible_element_indices():
            name = curve.element_names[index]
            element_type = curve.element_types[index].upper()
            s_exit = float(curve.s_m[index])
            length = max(0.0, float(curve.element_lengths_m[index]))
            left = x_at(max(s_min, s_exit - length))
            right = x_at(min(s_max, s_exit))
            center_x = (left + right) / 2.0
            width = right - left
            if width < 3.0:
                width = 3.0
                left = center_x - width / 2.0

            if "BEND" in element_type:
                tilt = float(curve.element_tilts_rad[index])
                vertical_bend = abs(math.sin(tilt)) > 0.7
                color = QColor("#3aa6b9" if vertical_bend else "#db8b3d")
                element_rect = QRectF(left, center_y - 10.0, width, 20.0)
            elif "QUAD" in element_type:
                k1 = float(curve.element_k1_m2[index])
                color = QColor("#9b72cf")
                top = center_y - 15.0 if k1 >= 0 else center_y
                element_rect = QRectF(left, top, width, 15.0)
            elif self._is_bpm(name, element_type):
                color = QColor("#4dbb83")
                painter.setPen(QPen(color, 2))
                painter.drawLine(
                    QPointF(center_x, center_y - 13.0),
                    QPointF(center_x, center_y + 13.0),
                )
                triangle = QPolygonF(
                    [
                        QPointF(center_x - 4.0, center_y - 14.0),
                        QPointF(center_x + 4.0, center_y - 14.0),
                        QPointF(center_x, center_y - 20.0),
                    ]
                )
                painter.setBrush(color)
                painter.drawPolygon(triangle)
                element_rect = None
            elif self._is_rf(element_type):
                color = QColor("#b27ad8")
                element_rect = QRectF(left, center_y - 7.0, width, 14.0)
            else:
                color = QColor(tokens["text_muted"])
                element_rect = QRectF(left, center_y - 3.0, width, 6.0)

            if element_rect is not None:
                painter.setPen(QPen(color.darker(125), 1))
                painter.setBrush(color)
                painter.drawRect(element_rect)

            if name in constraint_elements:
                marker_color = QColor(tokens["focus"])
                marker_color.setAlpha(150)
                painter.setPen(QPen(marker_color, 2, Qt.DotLine))
                painter.drawLine(
                    QPointF(center_x, constraint_top),
                    QPointF(center_x, rect.bottom()),
                )
                painter.setPen(QColor(tokens["focus"]))
                painter.drawText(
                    QRectF(center_x - 35.0, rect.bottom() - 14.0, 70.0, 14.0),
                    Qt.AlignCenter,
                    name,
                )
            elif "BEND" in element_type:
                painter.setPen(QColor(tokens["text_primary"]))
                painter.drawText(
                    QRectF(center_x - 35.0, rect.top(), 70.0, 14.0),
                    Qt.AlignCenter,
                    name,
                )

        painter.setPen(QColor(tokens["text_muted"]))
        painter.drawText(QRectF(rect.left(), rect.bottom() - 14.0, 150.0, 14.0), "Lattice")

    def _draw_lattice_legend(
        self,
        painter: QPainter,
        rect: QRectF,
        tokens: dict[str, str],
    ) -> None:
        if self.result is None:
            return
        curve = self.result.baseline_curve
        indices = self._visible_element_indices()
        bend_indices = [
            index for index in indices if "BEND" in curve.element_types[index].upper()
        ]
        entries = []
        if any(abs(math.sin(float(curve.element_tilts_rad[index]))) <= 0.7 for index in bend_indices):
            entries.append(("Bend-H", QColor("#db8b3d")))
        if any(abs(math.sin(float(curve.element_tilts_rad[index]))) > 0.7 for index in bend_indices):
            entries.append(("Bend-V", QColor("#3aa6b9")))
        if any("QUAD" in curve.element_types[index].upper() for index in indices):
            entries.extend(
                (
                    ("Quad +", QColor("#9b72cf")),
                    ("Quad -", QColor("#9b72cf")),
                )
            )
        if any(
            self._is_bpm(curve.element_names[index], curve.element_types[index].upper())
            for index in indices
        ):
            entries.append(("BPM", QColor("#4dbb83")))
        if any(self._is_rf(curve.element_types[index].upper()) for index in indices):
            entries.append(("RF", QColor("#b27ad8")))
        x = rect.left()
        y = rect.top() - 14.0
        for label, color in entries:
            painter.fillRect(QRectF(x, y + 3.0, 9.0, 7.0), color)
            painter.setPen(QColor(tokens["text_muted"]))
            painter.drawText(QRectF(x + 12.0, y, 45.0, 14.0), label)
            x += 56.0

    def _visible_element_indices(self) -> list[int]:
        if self.result is None:
            return []
        curve = self.result.baseline_curve
        return [
            index
            for index, element_type in enumerate(curve.element_types)
            if self._is_visible_optics_element(
                curve.element_names[index],
                element_type.upper(),
            )
        ]

    @classmethod
    def _is_visible_optics_element(cls, name: str, element_type: str) -> bool:
        return bool(
            "BEND" in element_type
            or "QUAD" in element_type
            or cls._is_bpm(name, element_type)
            or cls._is_rf(element_type)
        )

    @staticmethod
    def _is_bpm(name: str, element_type: str) -> bool:
        return name.upper().startswith("BPM") or element_type == "MONI"

    @staticmethod
    def _is_rf(element_type: str) -> bool:
        return "RF" in element_type

    def mouseMoveEvent(self, event) -> None:
        if self.result is None or self._lattice_geometry is None:
            self.setToolTip(self.DEFAULT_TOOLTIP)
            return super().mouseMoveEvent(event)
        rect, s_min, s_max = self._lattice_geometry
        if not rect.adjusted(-4.0, -8.0, 4.0, 8.0).contains(event.localPos()):
            self.setToolTip(self.DEFAULT_TOOLTIP)
            return super().mouseMoveEvent(event)
        curve = self.result.baseline_curve
        span = max(s_max - s_min, 1.0e-12)
        indices = self._visible_element_indices()
        if not indices:
            return super().mouseMoveEvent(event)
        nearest = min(
            indices,
            key=lambda index: abs(
                rect.left()
                + (
                    float(curve.s_m[index])
                    - 0.5 * max(0.0, float(curve.element_lengths_m[index]))
                    - s_min
                )
                / span
                * rect.width()
                - event.localPos().x()
            ),
        )
        name = curve.element_names[nearest]
        element_type = curve.element_types[nearest]
        details = [
            f"{name} [{element_type}]",
            f"s={float(curve.s_m[nearest]):.6g} m",
            f"L={float(curve.element_lengths_m[nearest]):.6g} m",
        ]
        k1 = float(curve.element_k1_m2[nearest])
        angle = float(curve.element_angles_rad[nearest])
        if math.isfinite(k1):
            details.append(f"K1={k1:.6g} 1/m²")
        if math.isfinite(angle):
            details.append(f"angle={angle:.6g} rad")
        self.setToolTip("\n".join(details))
        super().mouseMoveEvent(event)


class MainWindow(QMainWindow):
    def __init__(
        self,
        config: RunConfig | None = None,
        app_context: AppContext | None = None,
    ) -> None:
        super().__init__()
        install_qt_window_raise_handler(self)
        self.app_context = app_context
        self.setWindowTitle(self._window_title())
        self.setMinimumSize(1120, 760)
        self.resize(1440, 920)
        self.theme_name = (
            "control_room" if resolve_initial_theme() == "light" else "night_shift"
        )
        self.worker: WorkflowWorker | None = None
        self.preflight_worker: LivePreflightWorker | None = None
        self.model_worker: ModelResponseWorker | None = None
        self.imported_dispersion: ImportedDispersionDataset | None = None
        self.last_live_preflight = None
        self._loading_widgets = False
        self.config_path: Path | None = None
        self.config = config or default_offline_config()
        self.selected_knobs = tuple(self.config.knobs)
        self.knob_hard_limits = tuple(knob.limit for knob in self.config.knobs)
        self.available_bpms = (
            selectable_profile_bpms(app_context) if app_context is not None else self.config.target_bpms
        )
        self.available_quadrupoles = (
            selectable_profile_quadrupoles(app_context)
            if app_context is not None
            else tuple(dict.fromkeys(device for knob in self.config.knobs for device in knob.devices))
        )
        self._close_when_finished = False

        self._build_ui()
        self._configure_profile_mode()
        self._load_config_to_widgets()
        self._set_running(False, "")
        self._apply_theme()
        self._refresh_status("Ready")

    def _window_title(self) -> str:
        if self.app_context is None:
            return "Dispersion Correction"
        return (
            f"Dispersion Correction · {self.app_context.profile.machine.display_name} · "
            f"{self.app_context.control_backend.name.upper()}"
        )

    def _configure_profile_mode(self) -> None:
        if self.app_context is None:
            return
        self.load_button.hide()
        self.load_button.setToolTip("Runtime configuration is managed by the selected machine profile.")
        self.bpm_edit.setReadOnly(True)
        self.config_title_label.setText("Machine Profile")
        fixed_selection = self.config.section.model_only
        self.bpm_select_button.setVisible(not fixed_selection)
        self.knob_select_button.setVisible(not fixed_selection)
        self.model_response_button.setVisible(self._model_analysis_available())

    def _model_analysis_available(self) -> bool:
        return bool(
            self.app_context is not None
            and self.app_context.model_backend is not None
            and self.config.section.model_entrance
            and self.config.section.model_exit
            and self.config.section.model_observables
        )

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("centralRoot")
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        root.addWidget(self._build_summary_panel())

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self._build_config_panel())
        self.splitter.addWidget(self._build_workspace())
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([400, 1020])
        root.addWidget(self.splitter, 1)

        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("logView")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(700)
        self.log_view.setMaximumHeight(150)
        self.log_view.setPlaceholderText("Warnings, caput results, timeout, disconnected PVs")
        self.log_view.setVisible(False)
        root.addWidget(self.log_view)

        self.setCentralWidget(central)

    def _build_summary_panel(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("summaryPanel")
        outer_layout = QVBoxLayout(frame)
        outer_layout.setContentsMargins(14, 12, 14, 10)
        outer_layout.setSpacing(10)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(10)
        title = QLabel("Dispersion Correction")
        title.setObjectName("titleLabel")
        header_layout.addWidget(title)
        header_layout.addStretch(1)

        self.log_button = QToolButton()
        self.log_button.setObjectName("headerLogButton")
        self.log_button.setText("Log")
        self.log_button.setCheckable(True)
        self.log_button.setFixedSize(48, 32)
        self.log_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.log_button.toggled.connect(self._toggle_log)
        header_layout.addWidget(self.log_button)

        self.theme_button = QToolButton()
        self.theme_button.setObjectName("themeToggleButton")
        self.theme_button.setFixedSize(32, 32)
        self.theme_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.theme_button.clicked.connect(self._toggle_theme)
        header_layout.addWidget(self.theme_button)
        outer_layout.addLayout(header_layout)

        self.status_strip = StatusStrip(
            [
                ("BACKEND", "-"),
                ("PLANE", "X"),
                ("BPMS", "-"),
                ("KNOBS", "-"),
                ("SAFETY", "UNCHECKED"),
                ("LAST RESULT", "-"),
            ]
        )
        outer_layout.addWidget(self.status_strip)
        return frame

    def _build_config_panel(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("controlCard")
        frame.setMinimumWidth(390)
        frame.setMaximumWidth(460)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(7)

        heading_layout = QHBoxLayout()
        heading_layout.setContentsMargins(0, 0, 0, 0)
        heading_layout.setSpacing(8)
        self.config_title_label = QLabel("Configuration")
        self.config_title_label.setObjectName("configTitle")
        self.config_title_label.setFixedHeight(34)
        heading_layout.addWidget(self.config_title_label, 0, Qt.AlignVCenter)
        heading_layout.addStretch(1)
        self.load_button = QPushButton("Load Config")
        self.load_button.setObjectName("configLoadButton")
        self.load_button.clicked.connect(self._load_config_dialog)
        heading_layout.addWidget(self.load_button)
        self.preflight_button = QPushButton("Check PVs")
        self.preflight_button.setObjectName("preflightButton")
        self.preflight_button.setFixedHeight(34)
        self.preflight_button.clicked.connect(self._start_live_preflight)
        heading_layout.addWidget(self.preflight_button, 0, Qt.AlignVCenter)
        layout.addLayout(heading_layout)

        layout.addWidget(self._config_section_label("MACHINE"))
        machine_form = self._config_form()

        self.section_combo = QComboBox()
        self.section_combo.setFixedHeight(34)
        if self.app_context is None:
            self.section_combo.addItem(self.config.section.display_name, self.config.section.id)
        else:
            for section_id, display_name in profile_section_choices(self.app_context):
                self.section_combo.addItem(display_name, section_id)
        self.section_combo.currentIndexChanged.connect(self._section_changed)
        self._add_form_row(machine_form, "Section", self.section_combo)

        self.bpm_edit = QLineEdit()
        self.bpm_edit.setFixedHeight(34)
        self.bpm_edit.setReadOnly(self.app_context is not None)
        bpm_selector = QWidget()
        bpm_selector.setFixedHeight(34)
        bpm_selector_layout = QHBoxLayout(bpm_selector)
        bpm_selector_layout.setContentsMargins(0, 0, 0, 0)
        bpm_selector_layout.setSpacing(6)
        bpm_selector_layout.addWidget(self.bpm_edit, 1)
        self.bpm_select_button = QPushButton("Select")
        self.bpm_select_button.setObjectName("bpmSelectButton")
        self.bpm_select_button.setFixedHeight(34)
        self.bpm_select_button.setVisible(self.app_context is not None)
        self.bpm_select_button.clicked.connect(self._select_bpms)
        bpm_selector_layout.addWidget(self.bpm_select_button, 0, Qt.AlignVCenter)
        self._add_form_row(machine_form, "BPMs", bpm_selector)

        self.knob_edit = QLineEdit()
        self.knob_edit.setFixedHeight(34)
        self.knob_edit.setReadOnly(True)
        knob_selector = QWidget()
        knob_selector.setFixedHeight(34)
        knob_selector_layout = QHBoxLayout(knob_selector)
        knob_selector_layout.setContentsMargins(0, 0, 0, 0)
        knob_selector_layout.setSpacing(6)
        knob_selector_layout.addWidget(self.knob_edit, 1)
        self.knob_select_button = QPushButton("Select")
        self.knob_select_button.setObjectName("knobSelectButton")
        self.knob_select_button.setFixedHeight(34)
        self.knob_select_button.setVisible(self.app_context is not None)
        self.knob_select_button.clicked.connect(self._select_knobs)
        knob_selector_layout.addWidget(self.knob_select_button, 0, Qt.AlignVCenter)
        self._add_form_row(machine_form, "Knobs", knob_selector)

        self.delta_spin = QDoubleSpinBox()
        self.delta_spin.setDecimals(8)
        self.delta_spin.setRange(1.0e-7, 1.0e-2)
        self.delta_spin.setSingleStep(1.0e-4)
        self.delta_spin.setToolTip("Relative momentum perturbation used at +dp/p and -dp/p to measure D_eff.")
        self._add_form_row(machine_form, "Energy Step (Δp/p)", self.delta_spin)
        layout.addLayout(machine_form)

        layout.addWidget(self._config_section_label("SAMPLING"))
        sampling_form = self._config_form()

        self.samples_per_step_spin = QSpinBox()
        self.samples_per_step_spin.setRange(1, 100)
        self.samples_per_step_spin.setToolTip("BPM samples collected at each measurement step.")
        self._add_form_row(sampling_form, "Samples/step", self.samples_per_step_spin)

        self.sample_interval_spin = QDoubleSpinBox()
        self.sample_interval_spin.setDecimals(3)
        self.sample_interval_spin.setRange(0.0, 60.0)
        self.sample_interval_spin.setSingleStep(0.05)
        self.sample_interval_spin.setToolTip("Wait between consecutive BPM samples; no wait follows the final sample.")
        self._add_form_row(sampling_form, "Sample Interval (s)", self.sample_interval_spin)

        self.final_samples_spin = QSpinBox()
        self.final_samples_spin.setRange(1, 200)
        self.final_samples_spin.setToolTip("BPM samples used for the final acceptance measurement.")
        self._add_form_row(sampling_form, "Final Samples", self.final_samples_spin)

        self.settle_time_spin = QDoubleSpinBox()
        self.settle_time_spin.setDecimals(2)
        self.settle_time_spin.setRange(0.0, 120.0)
        self.settle_time_spin.setSingleStep(0.5)
        self.settle_time_spin.setToolTip("Wait after each machine setting change before reading BPMs.")
        self._add_form_row(sampling_form, "Settle Time (s)", self.settle_time_spin)
        layout.addLayout(sampling_form)

        layout.addWidget(self._config_section_label("SOLVER"))
        solver_form = self._config_form()

        self.max_iter_spin = QSpinBox()
        self.max_iter_spin.setRange(1, 20)
        self._add_form_row(solver_form, "Max Iter", self.max_iter_spin)

        self.gain_spin = QDoubleSpinBox()
        self.gain_spin.setDecimals(3)
        self.gain_spin.setRange(0.001, 1.0)
        self.gain_spin.setSingleStep(0.05)
        self._add_form_row(solver_form, "Gain", self.gain_spin)

        self.max_step_pct_spin = QDoubleSpinBox()
        self.max_step_pct_spin.setDecimals(1)
        self.max_step_pct_spin.setRange(0.1, 100.0)
        self.max_step_pct_spin.setSingleStep(5.0)
        self.max_step_pct_spin.valueChanged.connect(lambda _value: self._update_knob_summary())
        self._add_form_row(solver_form, "Max Step (%)", self.max_step_pct_spin)

        self.response_update_combo = QComboBox()
        self.response_update_combo.addItems(["once", "every_iteration"])
        self._add_form_row(solver_form, "Response", self.response_update_combo)
        layout.addLayout(solver_form)

        self.run_button = QPushButton("Run Correction")
        self.run_button.setProperty("role", "control")
        self.run_button.clicked.connect(lambda: self._start_task("run"))

        self.abort_button = QPushButton("Abort")
        self.abort_button.setProperty("role", "danger")
        self.abort_button.setEnabled(False)
        self.abort_button.clicked.connect(self._request_abort)

        self.primary_action_stack = QStackedWidget()
        self.primary_action_stack.addWidget(self.run_button)
        self.primary_action_stack.addWidget(self.abort_button)
        self.primary_action_stack.setCurrentWidget(self.run_button)
        layout.addSpacing(5)
        layout.addWidget(self.primary_action_stack)

        self.progress_widget = QWidget()
        progress_layout = QVBoxLayout(self.progress_widget)
        progress_layout.setContentsMargins(0, 2, 0, 0)
        progress_layout.setSpacing(4)
        progress_line = QHBoxLayout()
        progress_line.setContentsMargins(0, 0, 0, 0)
        self.progress_stage_label = QLabel("Starting")
        self.progress_stage_label.setObjectName("operationStage")
        self.progress_stage_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.progress_percent_label = QLabel("0%")
        self.progress_percent_label.setObjectName("operationProgressPercent")
        progress_line.addWidget(self.progress_stage_label, 1)
        progress_line.addWidget(self.progress_percent_label)
        progress_layout.addLayout(progress_line)
        self.operation_progress = QProgressBar()
        self.operation_progress.setObjectName("operationProgress")
        self.operation_progress.setRange(0, 100)
        self.operation_progress.setValue(0)
        self.operation_progress.setTextVisible(False)
        self.operation_progress.setFixedHeight(4)
        progress_layout.addWidget(self.operation_progress)
        self.progress_widget.setVisible(False)
        layout.addWidget(self.progress_widget)
        layout.addStretch(1)
        return frame

    def _build_workspace(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("plotCard")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.tabs = FullWidthTabWidget()
        self.tabs.setUsesScrollButtons(False)
        self.tabs.tabBar().setExpanding(True)
        self.tabs.tabBar().setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self.measure_table = self._table(
            ["BPM", "Measured mm", "Target mm", "Residual mm", "Valid"]
        )
        self.measure_page = QWidget()
        measure_layout = QVBoxLayout(self.measure_page)
        measure_layout.setContentsMargins(8, 8, 8, 8)
        measure_actions = QHBoxLayout()
        measure_actions.addStretch(1)
        self.measure_button = QPushButton("Measure D_eff")
        self.measure_button.clicked.connect(lambda: self._start_task("measure"))
        measure_actions.addWidget(self.measure_button)
        measure_layout.addLayout(measure_actions)
        measure_layout.addWidget(self.measure_table, 1)

        self.response_table = self._table([])
        self.response_info = QPlainTextEdit()
        self.response_info.setReadOnly(True)
        self.response_page = QWidget()
        response_layout = QVBoxLayout(self.response_page)
        response_layout.setContentsMargins(8, 8, 8, 8)
        response_actions = QHBoxLayout()
        response_actions.addWidget(QLabel("Model source"))
        self.model_source_combo = QComboBox()
        self.model_source_combo.addItem("Design lattice", "design")
        if self.app_context is not None:
            backend_name = self.app_context.control_backend.name.lower()
            self.model_source_combo.addItem(
                f"Current {backend_name.upper()} snapshot",
                "live",
            )
        self.model_source_combo.setToolTip(
            "Current snapshot reads quadrupole K1 PVs without writing machine state."
        )
        self.model_source_combo.currentIndexChanged.connect(self._model_source_changed)
        response_actions.addWidget(self.model_source_combo)
        self.model_response_button = QPushButton("Analyze Model + Preview")
        self.model_response_button.clicked.connect(self._start_model_response)
        self.model_response_button.setVisible(self._model_analysis_available())
        response_actions.addWidget(self.model_response_button)
        self.model_boundary_label = QLabel()
        self.model_boundary_label.setObjectName("modelBoundaryLabel")
        response_actions.addWidget(self.model_boundary_label)
        self.import_measurement_button = QPushButton("Import ηx CSV")
        self.import_measurement_button.clicked.connect(self._import_measurement_csv)
        self.import_measurement_button.setToolTip(
            "Import bpm, etax_mm, and optional etax_sigma_mm columns for comparison only."
        )
        response_actions.addWidget(self.import_measurement_button)
        self.clear_measurement_button = QPushButton("Clear")
        self.clear_measurement_button.clicked.connect(self._clear_imported_measurement)
        response_actions.addWidget(self.clear_measurement_button)
        response_actions.addStretch(1)
        self.response_button = QPushButton("Measure Response")
        self.response_button.clicked.connect(lambda: self._start_task("response"))
        response_actions.addWidget(self.response_button)
        response_layout.addLayout(response_actions)
        response_layout.addWidget(self.response_table, 2)
        self.dispersion_curve = DispersionCurveWidget()
        response_layout.addWidget(self.dispersion_curve, 3)
        response_layout.addWidget(self.response_info, 1)

        self.correction_table = self._table(["Iter", "Gain", "Accepted", "RMS Before", "RMS After", "Reason"])
        self.plan_text = QPlainTextEdit()
        self.plan_text.setReadOnly(True)
        self.calibration_text = QPlainTextEdit()
        self.calibration_text.setReadOnly(True)
        self.calibration_page = QWidget()
        calibration_layout = QVBoxLayout(self.calibration_page)
        calibration_layout.setContentsMargins(8, 8, 8, 8)
        calibration_actions = QHBoxLayout()
        calibration_actions.addStretch(1)
        self.calibration_button = QPushButton("Load Calibration CSV")
        self.calibration_button.clicked.connect(self._load_calibration_dialog)
        calibration_actions.addWidget(self.calibration_button)
        calibration_layout.addLayout(calibration_actions)
        calibration_layout.addWidget(self.calibration_text, 1)
        self.report_text = QPlainTextEdit()
        self.report_text.setReadOnly(True)

        self.tabs.addTab(self.plan_text, "Plan")
        self.tabs.addTab(self.calibration_page, "Calibration")
        self.tabs.addTab(self.measure_page, "Measure")
        self.tabs.addTab(self.response_page, "Response")
        self.tabs.addTab(self.correction_table, "Correction")
        self.tabs.addTab(self.report_text, "Report")
        layout.addWidget(self.tabs)
        return frame

    def _table(self, headers: list[str]) -> QTableWidget:
        table = QTableWidget()
        table.setAlternatingRowColors(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        if headers:
            table.setColumnCount(len(headers))
            table.setHorizontalHeaderLabels(headers)
        return table

    def _add_form_row(self, form: QFormLayout, label_text: str, widget) -> None:
        label = QLabel(label_text)
        label.setProperty("role", "field")
        label.setFixedWidth(124)
        label.setMinimumHeight(34)
        label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.addRow(label, widget)

    def _config_form(self) -> QFormLayout:
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setFormAlignment(Qt.AlignTop)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(4)
        return form

    def _config_section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("role", "configSection")
        return label

    def _load_config_to_widgets(self) -> None:
        self.last_live_preflight = None
        self._loading_widgets = True
        try:
            self.selected_knobs = tuple(self.config.knobs)
            section_index = self.section_combo.findData(self.config.section.id)
            if section_index >= 0:
                self.section_combo.setCurrentIndex(section_index)
            self.bpm_edit.setText(", ".join(self.config.target_bpms))
            self.delta_spin.setValue(self.config.energy_knob.delta)
            self.samples_per_step_spin.setValue(self.config.measurement.samples_per_step)
            self.sample_interval_spin.setValue(self.config.measurement.sample_interval_s)
            self.final_samples_spin.setValue(self.config.measurement.final_samples)
            self.settle_time_spin.setValue(self.config.measurement.settle_time_s)
            self.max_iter_spin.setValue(self.config.solver.max_iter)
            self.gain_spin.setValue(self.config.solver.gain)
            self.max_step_pct_spin.setValue(100.0 * self.config.solver.max_step_fraction)
            self.response_update_combo.setCurrentText(self.config.solver.response_update)
            entrance = self.config.section.model_entrance or "section entrance"
            self.model_boundary_label.setText(f"Assume D=D'=0 at {entrance}")
            self.model_boundary_label.setToolTip(
                "The isolated Elegant section starts with zero horizontal and vertical "
                "dispersion and slope."
            )
            self._update_knob_summary()
        finally:
            self._loading_widgets = False
        self._show_plan()
        self._show_calibration_summary()
        self._update_static_safety_status()
        self._refresh_status("Config loaded")

    def _update_knob_summary(self) -> None:
        summaries = []
        tooltip_lines = ["Symmetric device weights: +1 / +1"]
        unit = self._knob_control_unit()
        unit_suffix = f" {unit}" if unit else ""
        step_fraction = float(self.max_step_pct_spin.value()) / 100.0
        for knob in self.selected_knobs:
            devices = tuple(knob.devices)
            summaries.append("/".join(devices))
            step_limit = knob.limit * step_fraction
            tooltip_lines.append(
                f"{knob.name}: scan ±{knob.scan_step:g}{unit_suffix}, "
                f"limit ±{knob.limit:g}{unit_suffix}, "
                f"step ±{step_limit:g}{unit_suffix}"
            )
        self.knob_edit.setText("; ".join(summaries))
        self.knob_edit.setCursorPosition(0)
        self.knob_edit.setToolTip("\n".join(tooltip_lines))

    def _section_changed(self, _index: int | None = None) -> None:
        if self._loading_widgets or self.app_context is None:
            return
        section_id = str(self.section_combo.currentData() or "").strip()
        if not section_id or section_id == self.config.section.id:
            return
        try:
            _, config = load_profile_run_config(self.app_context, section_id=section_id)
        except Exception as exc:
            QMessageBox.warning(self, "Dispersion Section", str(exc))
            return
        self.config = config
        self.selected_knobs = tuple(config.knobs)
        self.knob_hard_limits = tuple(knob.limit for knob in config.knobs)
        self.dispersion_curve.set_result(None)
        self.imported_dispersion = None
        self.dispersion_curve.set_measurement(None)
        self.response_info.clear()
        self.response_table.setRowCount(0)
        self.measure_table.setRowCount(0)
        self._configure_profile_mode()
        self._load_config_to_widgets()
        self._set_running(False, "")

    def _model_source_changed(self, _index: int | None = None) -> None:
        if not hasattr(self, "dispersion_curve"):
            return
        self.dispersion_curve.set_result(None)
        self.response_info.clear()
        self.response_table.setRowCount(0)
        self._set_running(False, "")

    def _import_measurement_csv(self) -> None:
        response = self.dispersion_curve.result
        if response is None:
            QMessageBox.warning(
                self,
                "Import ηx",
                "Analyze the selected model before importing measurement points.",
            )
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import external ηx measurement",
            str(Path.cwd()),
            "CSV files (*.csv)",
            options=QFileDialog.Options() | QFileDialog.DontUseNativeDialog,
        )
        if not path:
            return
        allowed_bpms = tuple(
            name
            for name, element_type in zip(
                response.baseline_curve.element_names,
                response.baseline_curve.element_types,
            )
            if DispersionCurveWidget._is_bpm(name, element_type.upper())
        )
        try:
            imported = load_dispersion_csv(
                path,
                section_id=self.config.section.id,
                allowed_bpms=allowed_bpms,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Import ηx", str(exc))
            return
        self.imported_dispersion = imported
        self.dispersion_curve.set_measurement(imported)
        self._show_imported_comparison(response, imported)
        self._append_log(
            f"Imported {len(imported.bpm_names)} eta_x point(s) from {imported.source_path}"
        )
        self._refresh_status(f"Imported {len(imported.bpm_names)} ηx points")
        self._set_running(False, "")

    def _clear_imported_measurement(self) -> None:
        self.imported_dispersion = None
        self.dispersion_curve.set_measurement(None)
        self.measure_table.setHorizontalHeaderLabels(
            ["BPM", "Measured mm", "Target mm", "Residual mm", "Valid"]
        )
        self.measure_table.setRowCount(0)
        response = self.dispersion_curve.result
        if response is not None:
            self.response_info.setPlainText(format_model_response(response))
        self._set_running(False, "")

    def _select_knobs(self) -> None:
        if self.app_context is None:
            return
        dialog, table, buttons = self._build_knob_selection_dialog()
        accepted_knobs: dict[str, tuple[KnobConfig, ...]] = {}

        def accept_selection() -> None:
            try:
                accepted_knobs["value"] = self._knobs_from_table(table)
            except Exception as exc:
                QMessageBox.warning(dialog, "Knob Selection", str(exc))
                return
            dialog.accept()

        buttons.accepted.connect(accept_selection)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec_() != QDialog.Accepted:
            return
        self.selected_knobs = accepted_knobs["value"]
        self._update_knob_summary()
        self._selection_changed()

    def _build_knob_selection_dialog(self):
        dialog = QDialog(self)
        dialog.setObjectName("knobSelectionDialog")
        dialog.setStyleSheet(build_stylesheet(self.theme_name))
        dialog.setWindowTitle("Configure Dispersion Knobs")
        dialog.resize(720, 300)
        layout = QVBoxLayout(dialog)
        prompt = QLabel(
            "Choose two distinct quadrupoles for each symmetric knob. "
            "Session scan and limit values cannot exceed profile limits."
        )
        prompt.setObjectName("knobSelectionPrompt")
        prompt.setWordWrap(True)
        layout.addWidget(prompt)

        unit = self._knob_control_unit()
        suffix = f" ({unit})" if unit else ""
        table = QTableWidget(len(self.selected_knobs), 5)
        table.setObjectName("knobSelectionTable")
        table.setHorizontalHeaderLabels(
            ["Knob", "Q1", "Q2", f"Scan ±{suffix}", f"Limit ±{suffix}"]
        )
        table.verticalHeader().setVisible(False)
        table.setSelectionMode(QAbstractItemView.NoSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        for row, knob in enumerate(self.selected_knobs):
            devices = tuple(knob.devices)
            first = devices[0] if devices else ""
            second = devices[1] if len(devices) > 1 else ""
            table.setItem(row, 0, QTableWidgetItem(self._knob_name(first, second)))
            first_combo = self._quad_combo(first)
            second_combo = self._quad_combo(second)
            table.setCellWidget(row, 1, first_combo)
            table.setCellWidget(row, 2, second_combo)
            hard_limit = (
                self.knob_hard_limits[row]
                if row < len(self.knob_hard_limits)
                else knob.limit
            )
            table.setCellWidget(row, 3, self._knob_value_spin(knob.scan_step, hard_limit))
            table.setCellWidget(row, 4, self._knob_value_spin(knob.limit, hard_limit))
            first_combo.currentTextChanged.connect(
                lambda _text, selected_row=row: self._update_dialog_knob_name(
                    table,
                    selected_row,
                )
            )
            second_combo.currentTextChanged.connect(
                lambda _text, selected_row=row: self._update_dialog_knob_name(
                    table,
                    selected_row,
                )
            )
            table.setRowHeight(row, 42)
        layout.addWidget(table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        layout.addWidget(buttons)
        return dialog, table, buttons

    def _update_dialog_knob_name(self, table: QTableWidget, row: int) -> None:
        first = self._table_combo_text(table, row, 1)
        second = self._table_combo_text(table, row, 2)
        item = table.item(row, 0)
        if item is not None:
            item.setText(self._knob_name(first, second))

    def _knobs_from_table(self, table: QTableWidget) -> tuple[KnobConfig, ...]:
        selected_knobs = []
        selected_devices = []
        for row in range(table.rowCount()):
            first = self._table_combo_text(table, row, 1)
            second = self._table_combo_text(table, row, 2)
            if not first or not second:
                raise ValueError(f"Knob row {row + 1} requires two quadrupoles")
            if first == second:
                raise ValueError(f"Knob row {row + 1} must use two different quadrupoles")
            scan_step = self._table_spin_value(table, row, 3)
            limit = self._table_spin_value(table, row, 4)
            if scan_step > limit:
                raise ValueError(
                    f"Knob row {row + 1} requires response scan step <= cumulative limit"
                )
            selected_devices.extend((first, second))
            selected_knobs.append(
                KnobConfig(
                    name=self._knob_name(first, second),
                    devices={first: 1.0, second: 1.0},
                    scan_step=scan_step,
                    limit=limit,
                )
            )
        duplicates = sorted(
            name for name in set(selected_devices) if selected_devices.count(name) > 1
        )
        if duplicates:
            raise ValueError(
                "A quadrupole can only belong to one correction knob: "
                + ", ".join(duplicates)
            )
        return tuple(selected_knobs)

    @staticmethod
    def _table_combo_text(table: QTableWidget, row: int, column: int) -> str:
        widget = table.cellWidget(row, column)
        return widget.currentText().strip() if isinstance(widget, QComboBox) else ""

    @staticmethod
    def _table_spin_value(table: QTableWidget, row: int, column: int) -> float:
        widget = table.cellWidget(row, column)
        if not isinstance(widget, QDoubleSpinBox):
            raise ValueError(f"Knob row {row + 1} has no numeric value in column {column + 1}")
        return float(widget.value())

    def _quad_combo(self, selected: str) -> QComboBox:
        combo = QComboBox()
        combo.addItems(self.available_quadrupoles)
        index = combo.findText(selected)
        if index >= 0:
            combo.setCurrentIndex(index)
        return combo

    @staticmethod
    def _knob_value_spin(value: float, hard_limit: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setDecimals(6)
        spin.setRange(1.0e-6, hard_limit)
        spin.setSingleStep(max(1.0e-6, hard_limit / 100.0))
        spin.setValue(value)
        return spin

    @staticmethod
    def _knob_name(first: str, second: str) -> str:
        return f"{first}_{second}_sym" if first and second else "Unconfigured"

    def _select_bpms(self) -> None:
        if self.app_context is None:
            return
        dialog = QDialog(self)
        dialog.setObjectName("bpmSelectionDialog")
        dialog.setStyleSheet(build_stylesheet(self.theme_name))
        dialog.setWindowTitle("Select Dispersion BPMs")
        dialog.resize(360, 480)
        layout = QVBoxLayout(dialog)
        prompt = QLabel("Select BPMs used for D_eff measurement (machine order):")
        prompt.setObjectName("bpmSelectionPrompt")
        prompt.setWordWrap(True)
        layout.addWidget(prompt)
        choices = QListWidget()
        choices.setObjectName("bpmSelectionList")
        selected = {
            item for item in re.split(r"[\s,]+", self.bpm_edit.text().strip()) if item
        }
        for name in self.available_bpms:
            item = QListWidgetItem()
            self._set_bpm_choice_item(item, name, name in selected)
            choices.addItem(item)
        choices.itemClicked.connect(self._toggle_bpm_choice_item)
        layout.addWidget(choices, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec_() != QDialog.Accepted:
            return
        selected_bpms = tuple(
            str(choices.item(index).data(Qt.UserRole + 1))
            for index in range(choices.count())
            if bool(choices.item(index).data(Qt.UserRole))
        )
        if not selected_bpms:
            QMessageBox.warning(self, "BPM Selection", "Select at least one BPM.")
            return
        self.bpm_edit.setText(", ".join(selected_bpms))
        self._selection_changed()

    @staticmethod
    def _set_bpm_choice_item(item: QListWidgetItem, name: str, checked: bool) -> None:
        item.setData(Qt.UserRole, bool(checked))
        item.setData(Qt.UserRole + 1, name)
        item.setText(f"{'✓' if checked else ' '}  {name}")
        item.setToolTip("Selected" if checked else "Not selected")

    def _toggle_bpm_choice_item(self, item: QListWidgetItem) -> None:
        name = str(item.data(Qt.UserRole + 1))
        self._set_bpm_choice_item(item, name, not bool(item.data(Qt.UserRole)))

    def _selection_changed(self) -> None:
        if self._loading_widgets:
            return
        self.last_live_preflight = None
        try:
            self.config = self._config_from_widgets()
            plan = build_operation_plan(self.config)
        except Exception as exc:
            self.plan_text.setPlainText(f"Selection error: {exc}")
            self.status_strip.set_value("SAFETY", "NOT READY", "danger")
            self.measure_button.setEnabled(False)
            self.response_button.setEnabled(False)
            self.run_button.setEnabled(False)
            return
        self.plan_text.setPlainText(format_operation_plan(plan))
        self._update_static_safety_status()
        self._refresh_status("Selection updated")
        self._set_running(False, "")

    def _knob_control_unit(self) -> str:
        pv_map = self.config.backend.options.get("pv_map", {})
        quadrupoles = pv_map.get("quadrupoles", {}) if isinstance(pv_map, dict) else {}
        controls = {
            str(quadrupoles.get(device, {}).get("control", "k1")).lower()
            for knob in self.config.knobs
            for device in knob.devices
            if isinstance(quadrupoles.get(device), dict)
        }
        if controls == {"current"}:
            return "A"
        if controls == {"k1"}:
            return "K1"
        return ""

    def _config_from_widgets(self) -> RunConfig:
        bpms = tuple(item for item in re.split(r"[\s,]+", self.bpm_edit.text().strip()) if item)
        if not bpms:
            raise ValueError("At least one BPM is required")
        knobs = tuple(self.selected_knobs)

        config = replace(
            self.config,
            energy_knob=replace(self.config.energy_knob, delta=float(self.delta_spin.value())),
            target_bpms=bpms,
            knobs=knobs,
            measurement=replace(
                self.config.measurement,
                samples_per_step=int(self.samples_per_step_spin.value()),
                sample_interval_s=float(self.sample_interval_spin.value()),
                final_samples=int(self.final_samples_spin.value()),
                settle_time_s=float(self.settle_time_spin.value()),
            ),
            solver=replace(
                self.config.solver,
                max_iter=int(self.max_iter_spin.value()),
                gain=float(self.gain_spin.value()),
                max_step_fraction=float(self.max_step_pct_spin.value()) / 100.0,
                response_update=self.response_update_combo.currentText(),
            ),
        )
        if self.app_context is not None:
            config = apply_profile_selection(
                self.app_context,
                config,
                target_bpms=bpms,
                knobs=knobs,
            )
        return config

    def _start_task(self, task: str) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        try:
            config = self._config_from_widgets()
        except Exception as exc:
            QMessageBox.warning(self, "Configuration", str(exc))
            return
        self.config = config
        self.last_live_preflight = None
        self._update_static_safety_status()
        blocked_reason = self._operation_block_reason()
        if blocked_reason is not None:
            QMessageBox.warning(self, "Dispersion Correction", blocked_reason)
            self._set_running(False, "")
            return
        if config.backend.type.lower() == "epics":
            preflight = run_preflight(config)
            if not preflight.ok:
                QMessageBox.warning(self, "EPICS Preflight", "\n".join(preflight.blockers))
                return
            if preflight.warnings and config.backend.mode == "write_enabled":
                answer = QMessageBox.question(
                    self,
                    "EPICS Preflight Warning",
                    "\n".join(preflight.warnings) + "\n\nContinue to live read-only preflight?",
                    QMessageBox.Yes | QMessageBox.Cancel,
                    QMessageBox.Cancel,
                )
                if answer != QMessageBox.Yes:
                    return
        self.worker = WorkflowWorker(task, config)
        self.worker.log.connect(self._append_log)
        self.worker.progress.connect(self._update_progress)
        self.worker.preflight.connect(self._live_preflight_completed)
        self.worker.failed.connect(self._task_failed)
        self.worker.completed.connect(self._task_completed)
        self.worker.finished.connect(self._task_finished)
        self._set_running(True, task)
        self._update_progress("Starting", 0, 1)
        if task == "run":
            self.tabs.setCurrentWidget(self.correction_table)
        self.worker.start()

    def _task_completed(self, task: str, result: object) -> None:
        if isinstance(result, DispersionMeasurement):
            self._show_measurement(result)
            self.tabs.setCurrentWidget(self.measure_page)
            self._refresh_status(f"RMS {result.rms_mm:.4g} mm")
        elif isinstance(result, ResponseMatrixResult):
            self._show_response(result)
            self._show_measurement(result.measurement)
            self.tabs.setCurrentIndex(3)
            self._refresh_status(f"Cond {result.condition_number:.4g}")
        elif isinstance(result, CorrectionResult):
            self._show_result(result)
            self.tabs.setCurrentWidget(self.correction_table)
            status = "Accepted" if result.success else "Aborted" if result.reason.startswith("Aborted") else "Not accepted"
            self._refresh_status(status)
            self.status_strip.set_value("SAFETY", result.safety.reason, "success" if result.safety.ok else "danger")
        if self.app_context is not None and isinstance(
            result,
            (DispersionMeasurement, ResponseMatrixResult, CorrectionResult),
        ):
            paths = write_profile_operation(
                self.app_context,
                task,
                result,
                config=self.config,
                live_preflight=self.last_live_preflight,
            )
            self._append_log(f"Operation archived in {paths['run_metadata'].parent}")
        self._append_log(f"{task} completed")

    def _task_failed(self, message: str) -> None:
        if message == "Operation aborted":
            self._append_log("Operation aborted; temporary state restored")
            self._refresh_status("Aborted")
            return
        self._append_log(f"ERROR: {message}")
        self.status_strip.set_value("SAFETY", "NOT READY", "danger")
        self._refresh_status("Failed")
        QMessageBox.warning(self, "Workflow", message)

    def _task_finished(self) -> None:
        self._set_running(False, "")

    def _start_model_response(self) -> None:
        if self.app_context is None or self.app_context.model_backend is None:
            QMessageBox.warning(self, "Model Response", "No Elegant model backend is configured.")
            return
        if self.model_worker is not None and self.model_worker.isRunning():
            return
        try:
            self.config = self._config_from_widgets()
        except Exception as exc:
            QMessageBox.warning(self, "Configuration", str(exc))
            return
        model_source = str(self.model_source_combo.currentData() or "design")
        self.model_worker = ModelResponseWorker(
            self.app_context,
            self.config,
            model_source,
        )
        self.model_worker.progress.connect(self._update_progress)
        self.model_worker.failed.connect(self._task_failed)
        self.model_worker.completed.connect(self._model_response_completed)
        self.model_worker.finished.connect(self._task_finished)
        self._set_running(True, "model-response")
        self.tabs.setCurrentWidget(self.response_page)
        self.model_worker.start()

    def _model_response_completed(self, result: object) -> None:
        if not isinstance(result, ModelResponseResult):
            self._task_failed("Unexpected model response result")
            return
        self._show_model_response(result)
        self._refresh_status(f"Model rank {result.retained_rank}")
        self._append_log(
            f"Model response completed from {result.model_source} without machine writes"
        )

    def _show_measurement(self, measurement: DispersionMeasurement) -> None:
        self.measure_table.setHorizontalHeaderLabels(
            ["BPM", "Measured mm", "Target mm", "Residual mm", "Valid"]
        )
        self.measure_table.setRowCount(len(measurement.bpm_names))
        for row, name in enumerate(measurement.bpm_names):
            self.measure_table.setItem(row, 0, QTableWidgetItem(name))
            self.measure_table.setItem(row, 1, QTableWidgetItem(f"{measurement.values_mm[row]:.6g}"))
            self.measure_table.setItem(row, 2, QTableWidgetItem(f"{measurement.target_values_mm[row]:.6g}"))
            self.measure_table.setItem(row, 3, QTableWidgetItem(f"{measurement.residual_values_mm[row]:.6g}"))
            self.measure_table.setItem(row, 4, QTableWidgetItem("yes" if measurement.valid[row] else "no"))
        self.measure_table.resizeColumnsToContents()

    def _show_imported_comparison(
        self,
        response: ModelResponseResult,
        imported: ImportedDispersionDataset,
    ) -> None:
        model_etax = {
            name: float(response.baseline_curve.dx_mm[index])
            for index, name in enumerate(response.baseline_curve.element_names)
        }
        self.measure_table.setHorizontalHeaderLabels(
            ["BPM", "Imported ηx (mm)", "Model ηx (mm)", "Residual (mm)", "σηx (mm)"]
        )
        self.measure_table.setRowCount(len(imported.bpm_names))
        for row, (bpm, measured, sigma) in enumerate(
            zip(imported.bpm_names, imported.etax_mm, imported.etax_sigma_mm)
        ):
            model_value = model_etax[bpm]
            sigma_text = f"{sigma:.6g}" if math.isfinite(float(sigma)) else ""
            self.measure_table.setItem(row, 0, QTableWidgetItem(bpm))
            self.measure_table.setItem(row, 1, QTableWidgetItem(f"{measured:.6g}"))
            self.measure_table.setItem(row, 2, QTableWidgetItem(f"{model_value:.6g}"))
            self.measure_table.setItem(
                row,
                3,
                QTableWidgetItem(f"{float(measured) - model_value:.6g}"),
            )
            self.measure_table.setItem(row, 4, QTableWidgetItem(sigma_text))
        self.measure_table.resizeColumnsToContents()
        self.response_info.setPlainText(format_model_response(response))
        self.response_info.appendPlainText(
            "\nImported effective ηx:\n"
            f"  file: {imported.source_path}\n"
            f"  BPM points: {len(imported.bpm_names)}\n"
            "  warning: imported measurement may not correspond to the current lattice snapshot"
        )

    def _show_response(self, response: ResponseMatrixResult) -> None:
        self.response_table.setRowCount(len(response.bpm_names))
        self.response_table.setColumnCount(len(response.knob_names) + 1)
        self.response_table.setHorizontalHeaderLabels(["BPM", *response.knob_names])
        for row, bpm in enumerate(response.bpm_names):
            self.response_table.setItem(row, 0, QTableWidgetItem(bpm))
            for col, value in enumerate(response.matrix[row, :], start=1):
                self.response_table.setItem(row, col, QTableWidgetItem(f"{value:.6g}"))
        self.response_table.resizeColumnsToContents()
        self.response_info.setPlainText(
            "Singular values: "
            + ", ".join(f"{value:.6g}" for value in response.singular_values)
            + f"\nCondition number: {response.condition_number:.6g}"
        )

    def _show_model_response(self, response: ModelResponseResult) -> None:
        self.response_table.setRowCount(len(response.observable_names))
        self.response_table.setColumnCount(len(response.knob_names) + 1)
        self.response_table.setHorizontalHeaderLabels(["Observable", *response.knob_names])
        for row, observable in enumerate(response.observable_names):
            self.response_table.setItem(row, 0, QTableWidgetItem(observable))
            for col, value in enumerate(response.response_matrix[row, :], start=1):
                self.response_table.setItem(row, col, QTableWidgetItem(f"{value:.6g}"))
        self.response_table.resizeColumnsToContents()
        self.dispersion_curve.set_result(response)
        self.response_info.setPlainText(format_model_response(response))
        if self.imported_dispersion is not None:
            self.dispersion_curve.set_measurement(self.imported_dispersion)
            self._show_imported_comparison(response, self.imported_dispersion)

    def _show_result(self, result: CorrectionResult) -> None:
        self._show_measurement(result.final)
        if result.response is not None:
            self._show_response(result.response)
        self.correction_table.setRowCount(len(result.steps))
        for row, step in enumerate(result.steps):
            self.correction_table.setItem(row, 0, QTableWidgetItem(str(step.iteration)))
            self.correction_table.setItem(row, 1, QTableWidgetItem(f"{step.gain:.3g}"))
            self.correction_table.setItem(row, 2, QTableWidgetItem("yes" if step.accepted else "no"))
            self.correction_table.setItem(row, 3, QTableWidgetItem(f"{step.rms_before_mm:.6g}"))
            after = "" if step.rms_after_mm is None else f"{step.rms_after_mm:.6g}"
            self.correction_table.setItem(row, 4, QTableWidgetItem(after))
            self.correction_table.setItem(row, 5, QTableWidgetItem(step.reason))
        self.correction_table.resizeColumnsToContents()
        self.report_text.setPlainText(result_to_markdown(result))

    def _refresh_status(self, last_result: str) -> None:
        backend = (
            self.app_context.control_backend.name.upper()
            if self.app_context is not None
            else self.config.backend.type.upper()
        )
        backend_tone = "warning" if backend == "VM" else "success"
        safety_value = self.status_strip.items["SAFETY"].value_label.text()
        safety_tone = "success" if safety_value in {"READY", "OK"} else "warning"
        if safety_value == "NOT READY":
            safety_tone = "danger"
        result_tone = "success" if last_result in {"Accepted", "Plan ready", "Ready", "Config loaded"} else "subtle"
        if "Fail" in last_result or "Not accepted" in last_result:
            result_tone = "danger"
        self.status_strip.set_value("BACKEND", backend, backend_tone)
        self.status_strip.set_value("PLANE", "X", "success")
        self.status_strip.set_value("BPMS", str(len(self.config.target_bpms)), "subtle")
        self.status_strip.set_value("KNOBS", str(len(self.config.knobs)), "subtle")
        self.status_strip.set_value("SAFETY", self.status_strip.items["SAFETY"].value_label.text(), safety_tone)
        self.status_strip.set_value("LAST RESULT", last_result, result_tone)

    def _set_running(self, running: bool, task: str) -> None:
        profile_managed = self.app_context is not None
        operation_allowed = self._operation_block_reason() is None
        self.load_button.setEnabled(not running and not profile_managed)
        self.calibration_button.setEnabled(not running)
        self.preflight_button.setEnabled(not running and self.config.backend.type.lower() == "epics")
        self.model_response_button.setEnabled(
            not running and self._model_analysis_available()
        )
        self.model_source_combo.setEnabled(not running)
        self.import_measurement_button.setEnabled(
            not running and self.dispersion_curve.result is not None
        )
        self.clear_measurement_button.setEnabled(
            not running and self.imported_dispersion is not None
        )
        self.measure_button.setEnabled(not running and operation_allowed)
        self.response_button.setEnabled(not running and operation_allowed)
        self.run_button.setEnabled(not running and operation_allowed)
        abortable = running and task != "preflight"
        self.abort_button.setEnabled(abortable)
        self.primary_action_stack.setCurrentWidget(self.abort_button if abortable else self.run_button)
        self.progress_widget.setVisible(running)
        if running:
            self._refresh_status(task)

    def _update_progress(self, stage: str, current: int, total: int) -> None:
        self.progress_stage_label.setText(stage)
        self.progress_stage_label.setToolTip(stage)
        if total <= 0:
            self.operation_progress.setRange(0, 0)
            self.progress_percent_label.clear()
            return
        percent = max(0, min(100, round(100.0 * current / total)))
        self.operation_progress.setRange(0, 100)
        self.operation_progress.setValue(percent)
        self.progress_percent_label.setText(f"{percent}%")

    def _show_plan(self) -> None:
        try:
            self.config = self._config_from_widgets() if hasattr(self, "bpm_edit") else self.config
            plan = build_operation_plan(self.config)
            self.plan_text.setPlainText(format_operation_plan(plan))
            if hasattr(self, "tabs"):
                self.tabs.setCurrentWidget(self.plan_text)
            self._refresh_status("Plan ready")
        except Exception as exc:
            if hasattr(self, "plan_text"):
                self.plan_text.setPlainText(f"Plan error: {exc}")

    def _show_calibration_summary(self) -> None:
        calibration = self.config.energy_knob.calibration
        lines = [
            "Energy Calibration",
            "",
            f"Actuator: {self.config.energy_knob.actuator}",
            f"Unit: {self.config.energy_knob.actuator_unit}",
            f"Target momentum perturbation: {self.config.energy_knob.delta:g} dp/p",
        ]
        if calibration:
            lines.extend(["", "Current calibration:"])
            for key, value in calibration.items():
                lines.append(f"  {key}: {value}")
        else:
            lines.extend(["", "Current calibration: missing"])
        if hasattr(self, "calibration_text"):
            self.calibration_text.setPlainText("\n".join(lines) + "\n")

    def _load_calibration_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Phase Calibration CSV",
            str(Path.cwd() / "configs"),
            "CSV Files (*.csv)",
            options=QFileDialog.Options() | QFileDialog.DontUseNativeDialog,
        )
        if not path:
            return
        try:
            fit = load_phase_calibration_csv(path)
            base_config = self._config_from_widgets()
            self.config = replace(
                base_config,
                energy_knob=replace(
                    base_config.energy_knob,
                    calibration={"kind": "linear", "phase_per_delta": fit.phase_per_delta},
                ),
            )
        except Exception as exc:
            QMessageBox.warning(self, "Calibration", str(exc))
            return
        self.calibration_text.setPlainText(
            "Phase calibration fit\n\n"
            f"slope_delta_per_phase: {fit.slope_delta_per_phase:.12g}\n"
            f"phase_per_delta: {fit.phase_per_delta:.12g}\n"
            f"intercept_delta: {fit.intercept_delta:.12g}\n"
            f"r_squared: {fit.r_squared:.12g}\n"
            f"n_samples: {fit.n_samples}\n\n"
            "Current session config updated.\n"
        )
        self._show_plan()
        self.last_live_preflight = None
        self._update_static_safety_status()
        self._set_running(False, "")
        self.tabs.setCurrentWidget(self.calibration_page)
        self._refresh_status("Calibration loaded")

    def _toggle_theme(self) -> None:
        self.theme_name = "control_room" if self.theme_name == "night_shift" else "night_shift"
        self._apply_theme()

    def _apply_theme(self) -> None:
        self.setStyleSheet(build_stylesheet(self.theme_name))
        self.dispersion_curve.set_theme(self.theme_name)
        self._update_theme_button()

    def _update_theme_button(self) -> None:
        if self.theme_name == "night_shift":
            self.theme_button.setText("☀")
            self.theme_button.setToolTip("Switch to light theme.")
        else:
            self.theme_button.setText("☽")
            self.theme_button.setToolTip("Switch to dark theme.")

    def _toggle_log(self, checked: bool) -> None:
        self.log_view.setVisible(checked)

    def _append_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"{timestamp} {message}")

    def _request_abort(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.requestInterruption()
            self.abort_button.setEnabled(False)
            self._refresh_status("Aborting")
            self._append_log("Abort requested; restoring the operation snapshot")

    def _operation_block_reason(self) -> str | None:
        if self.config.section.model_only:
            return (
                "This HALF section is model-only. Energy modulation is not commissioned, "
                "so Measure, Response, and Correction remain disabled."
            )
        if self.config.backend.type.lower() == "offline":
            return None
        preflight = run_preflight(self.config)
        if not preflight.ok:
            return "Static preflight is not ready:\n" + "\n".join(
                f"- {item}" for item in preflight.blockers
            )
        if self.config.backend.mode != "write_enabled":
            return (
                "Dispersion measurement changes the configured energy actuator. "
                "The selected profile is read-only, so Measure, Response, and Correction are disabled."
            )
        if self.app_context is not None and not workflow_writes_allowed(
            self.app_context,
            "dispersion_correction",
        ):
            return "Machine-profile write_control blocks dispersion-correction operations."
        return None

    def _update_static_safety_status(self) -> None:
        result = run_preflight(self.config)
        if self.config.section.model_only:
            self.status_strip.set_value("SAFETY", "MODEL ONLY", "warning")
        elif self.config.backend.type.lower() == "offline":
            self.status_strip.set_value("SAFETY", "READY", "success")
        elif not result.ok:
            self.status_strip.set_value("SAFETY", "NOT READY", "danger")
        else:
            self.status_strip.set_value("SAFETY", "UNCHECKED", "warning")

    def _start_live_preflight(self) -> None:
        if self.preflight_worker is not None and self.preflight_worker.isRunning():
            return
        try:
            self.config = self._config_from_widgets()
        except Exception as exc:
            QMessageBox.warning(self, "Configuration", str(exc))
            return
        self.last_live_preflight = None
        self.status_strip.set_value("SAFETY", "CHECKING", "warning")
        self.preflight_worker = LivePreflightWorker(self.config)
        self.preflight_worker.completed.connect(self._live_preflight_completed)
        self.preflight_worker.failed.connect(self._live_preflight_failed)
        self.preflight_worker.finished.connect(lambda: self._set_running(False, ""))
        self._set_running(True, "preflight")
        self.preflight_worker.start()

    def _live_preflight_completed(self, result) -> None:
        self.last_live_preflight = result
        ready = result.ok
        self.status_strip.set_value(
            "SAFETY",
            "READY" if ready else "NOT READY",
            "success" if ready else "danger",
        )
        messages = [*result.static.blockers, *result.blockers]
        warnings = [*result.static.warnings, *result.warnings]
        if messages:
            self._append_log("Live preflight blockers: " + "; ".join(messages))
        if warnings:
            self._append_log("Live preflight warnings: " + "; ".join(warnings))
        if ready:
            self._append_log("Live read-only preflight passed; no setpoint was changed")

    def _live_preflight_failed(self, message: str) -> None:
        self.last_live_preflight = None
        self.status_strip.set_value("SAFETY", "NOT READY", "danger")
        self._append_log(f"Live preflight failed: {message}")
        QMessageBox.warning(self, "PV Preflight", message)

    def _load_config_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Config",
            str(self.config_path or Path.cwd()),
            "JSON Files (*.json);;All Config Files (*.json *.yaml *.yml);;YAML Files (*.yaml *.yml)",
            options=QFileDialog.Options() | QFileDialog.DontUseNativeDialog,
        )
        if not path:
            return
        try:
            self.config_path = Path(path)
            self.config = load_config(self.config_path)
        except Exception as exc:
            QMessageBox.warning(self, "Configuration", str(exc))
            return
        self._load_config_to_widgets()

    def closeEvent(self, event) -> None:
        if self.worker is not None and self.worker.isRunning():
            self._close_when_finished = True
            self._request_abort()
            self.worker.finished.connect(self.close)
            event.ignore()
            return
        event.accept()


if __name__ == "__main__":
    import sys

    from PyQt5.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    raise SystemExit(app.exec_())
