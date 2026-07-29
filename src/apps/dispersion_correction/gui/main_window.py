from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import math
from pathlib import Path
import re

import numpy as np
from PyQt5.QtCore import QPointF, QRectF, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen, QPolygonF
from PyQt5.QtWidgets import (
    QFileDialog,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtCore import Qt

from half_linac.src.apps.dispersion_correction.calibration import (
    actuator_step_for_delta,
    is_direct_delta_actuator,
)
from half_linac.src.apps.dispersion_correction.config import (
    load_config,
    validate_config,
)
from half_linac.src.apps.dispersion_correction.dryrun import build_operation_plan
from half_linac.src.apps.dispersion_correction.gui.calibration_editor import (
    CalibrationEditorDialog,
)
from half_linac.src.apps.dispersion_correction.gui.theme import build_stylesheet, theme_tokens
from half_linac.src.apps.dispersion_correction.gui.widgets import StatusStrip
from half_linac.src.apps.dispersion_correction.joint_analysis import (
    JointResponseAnalyzer,
)
from half_linac.src.apps.dispersion_correction.models import (
    CorrectionRecommendation,
    CorrectionResult,
    DispersionMeasurement,
    JointCorrectionResult,
    JointDispersionTargetConfig,
    JointResponseAnalysisResult,
    KnobConfig,
    ModelOpticsCurve,
    ModelResponseResult,
    MultiPlaneDispersionMeasurement,
    ResponseMatrixResult,
    RunConfig,
)
from half_linac.src.apps.dispersion_correction.recommendation import (
    build_correction_recommendation,
)
from half_linac.src.apps.dispersion_correction.model_response import (
    calculate_model_response,
    format_model_response,
)
from half_linac.src.apps.dispersion_correction.preflight import run_live_preflight, run_preflight
from half_linac.src.apps.dispersion_correction.profile_runtime import (
    default_offline_config,
    energy_calibration_draft_directory,
    apply_profile_selection,
    load_profile_run_config,
    profile_section_choices,
    selectable_profile_bpms,
    selectable_profile_quadrupoles,
    write_profile_operation,
)
from half_linac.src.apps.dispersion_correction.reports import result_to_markdown
from half_linac.src.apps.dispersion_correction.solver import (
    automatic_response_block_reason,
    rank_reduced_response_warning,
    response_mode_counts,
)
from half_linac.src.apps.dispersion_correction.workflow import AchromatWorkflow
from half_linac.src.shared.app_theme import resolve_initial_theme
from half_linac.src.shared.machine_profile import (
    AppContext,
    RuntimeContextWidget,
    workflow_writes_allowed,
)
from half_linac.src.shared.window_activation import install_qt_window_raise_handler


class WorkflowWorker(QThread):
    log = pyqtSignal(str)
    progress = pyqtSignal(str, int, int)
    correction_measurement = pyqtSignal(int, int, str, object)
    failed = pyqtSignal(str)
    completed = pyqtSignal(str, object)
    preflight = pyqtSignal(object)

    def __init__(
        self,
        task: str,
        config: RunConfig,
        recommendation: CorrectionRecommendation | None = None,
        joint_recommendation: JointResponseAnalysisResult | None = None,
        design_k1_request: DesignK1Request | None = None,
        restore_request: CorrectionRestoreRequest | None = None,
    ) -> None:
        super().__init__()
        self.task = task
        self.config = config
        self.recommendation = recommendation
        self.joint_recommendation = joint_recommendation
        self.design_k1_request = design_k1_request
        self.restore_request = restore_request

    def run(self) -> None:
        try:
            workflow = AchromatWorkflow(
                self.config,
                log_callback=self.log.emit,
                cancellation_callback=self.isInterruptionRequested,
                progress_callback=self._emit_progress,
                preflight_callback=self.preflight.emit,
                correction_measurement_callback=(
                    self.correction_measurement.emit
                    if self.task == "run"
                    else None
                ),
            )
            if self.task == "measure":
                result = workflow.measure_dispersion(
                    self.config.measurement.samples_per_step
                )
            elif self.task == "response":
                result = workflow.build_response_matrix()
            elif self.task == "joint-response":
                result = JointResponseAnalyzer(
                    self.config,
                    log_callback=self.log.emit,
                    cancellation_callback=self.isInterruptionRequested,
                    progress_callback=self._emit_progress,
                    preflight_callback=self.preflight.emit,
                ).run()
            elif self.task == "joint-apply":
                if self.joint_recommendation is None:
                    raise ValueError("No reviewed joint recommendation was supplied")
                result = JointResponseAnalyzer(
                    self.config,
                    log_callback=self.log.emit,
                    cancellation_callback=self.isInterruptionRequested,
                    progress_callback=self._emit_progress,
                    preflight_callback=self.preflight.emit,
                ).apply_recommendation(self.joint_recommendation)
            elif self.task == "joint-run":
                result = JointResponseAnalyzer(
                    self.config,
                    log_callback=self.log.emit,
                    cancellation_callback=self.isInterruptionRequested,
                    progress_callback=self._emit_progress,
                    preflight_callback=self.preflight.emit,
                    measurement_callback=self.correction_measurement.emit,
                ).run_automatic()
            elif self.task == "run":
                result = workflow.run()
            elif self.task == "apply":
                if self.recommendation is None:
                    raise ValueError("No reviewed recommendation was supplied")
                result = workflow.apply_recommendation(self.recommendation)
            elif self.task == "design-k1":
                if self.design_k1_request is None:
                    raise ValueError("No reviewed design K1 request was supplied")
                result = workflow.apply_design_targets(
                    self.design_k1_request.target_values,
                    reviewed_baseline=self.design_k1_request.baseline_values,
                    max_changes=self.design_k1_request.max_changes,
                )
            elif self.task == "restore-correction":
                if self.restore_request is None:
                    raise ValueError("No reviewed correction restore was supplied")
                result = workflow.restore_correction_state(
                    self.restore_request.target_values,
                    reviewed_baseline=self.restore_request.baseline_values,
                    max_changes=self.restore_request.max_changes,
                )
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


@dataclass(frozen=True)
class DispersionPlotDataset:
    bpm_names: tuple[str, ...]
    values_mm: np.ndarray
    sigma_mm: np.ndarray
    valid: np.ndarray
    label: str
    target_mask: np.ndarray
    plane: str = "x"


@dataclass(frozen=True)
class CorrectionSessionRun:
    label: str
    task: str
    result: CorrectionResult | JointCorrectionResult
    requested_generations: int | None = None


@dataclass(frozen=True)
class DesignK1Request:
    baseline_values: dict[str, float]
    target_values: dict[str, float]
    max_changes: dict[str, float]


@dataclass(frozen=True)
class CorrectionRestoreRequest:
    run_label: str
    baseline_values: dict[str, float]
    target_values: dict[str, float]
    max_changes: dict[str, float]


class OverviewControls(QWidget):
    """Arrange overview actions and data/model controls as a responsive toolbar."""

    COMPACT_WIDTH = 860

    def __init__(
        self,
        title: QLabel,
        measurement_label: QLabel,
        state_label: QLabel,
        plane_combo: QComboBox,
        design_checkbox: QCheckBox,
        snapshot_checkbox: QCheckBox,
        refresh_snapshot_button: QPushButton,
        details_button: QPushButton,
    ) -> None:
        super().__init__()
        self._title = title
        self._measurement_label = measurement_label
        self._state_label = state_label
        self._plane_combo = plane_combo
        self._design_checkbox = design_checkbox
        self._snapshot_checkbox = snapshot_checkbox
        self._refresh_snapshot_button = refresh_snapshot_button
        self._details_button = details_button
        self.setObjectName("overviewToolbar")

        self.measurement_group = QFrame(self)
        self.measurement_group.setObjectName("overviewControlGroup")
        measurement_layout = QHBoxLayout(self.measurement_group)
        measurement_layout.setContentsMargins(8, 5, 8, 5)
        measurement_layout.setSpacing(7)
        measurement_layout.addWidget(self._measurement_label)
        measurement_layout.addWidget(self._plane_combo)
        measurement_layout.addWidget(self._state_label, 1)

        self.overlays_group = QFrame(self)
        self.overlays_group.setObjectName("overviewControlGroup")
        overlays_layout = QHBoxLayout(self.overlays_group)
        overlays_layout.setContentsMargins(8, 5, 8, 5)
        overlays_layout.setSpacing(9)
        overlays_layout.addWidget(self._design_checkbox)
        overlays_layout.addWidget(self._snapshot_checkbox)
        overlays_layout.addStretch(1)

        self.compact: bool | None = None
        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setHorizontalSpacing(8)
        self._layout.setVerticalSpacing(7)
        self._relayout(False)

    def _relayout(self, compact: bool) -> None:
        if self.compact == compact:
            return
        self.compact = compact
        widgets = (
            self._title,
            self.measurement_group,
            self.overlays_group,
            self._refresh_snapshot_button,
            self._details_button,
        )
        for widget in widgets:
            self._layout.removeWidget(widget)
        for column in range(4):
            self._layout.setColumnStretch(column, 0)

        self._layout.addWidget(self._title, 0, 0)
        self._layout.setColumnStretch(1, 1)
        self._layout.addWidget(self._refresh_snapshot_button, 0, 2)
        self._layout.addWidget(self._details_button, 0, 3)

        if compact:
            self._layout.addWidget(self.measurement_group, 1, 0, 1, 4)
            self._layout.addWidget(self.overlays_group, 2, 0, 1, 4)
            return

        self._layout.addWidget(self.measurement_group, 1, 0, 1, 2)
        self._layout.addWidget(self.overlays_group, 1, 2, 1, 2)

    def resizeEvent(self, event) -> None:
        self._relayout(event.size().width() < self.COMPACT_WIDTH)
        super().resizeEvent(event)


class DispersionCurveWidget(QWidget):
    DEFAULT_TOOLTIP = (
        "Measured BPM dispersion is the primary result. Design and current-K1 "
        "model curves are optional references. Move over the lattice strip for "
        "element details when a model has been analyzed."
    )

    def __init__(
        self,
        model_entrance: str | None = None,
        plane: str = "x",
    ) -> None:
        super().__init__()
        self.model_entrance = model_entrance
        self.plane = "y" if str(plane).strip().lower() == "y" else "x"
        self.result: ModelResponseResult | None = None
        self.measurement: DispersionPlotDataset | None = None
        self.reference_measurement: DispersionPlotDataset | None = None
        self.measurement_overlays: tuple[DispersionPlotDataset, ...] = ()
        self.correction_bpms: tuple[str, ...] = ()
        self.show_design_model = False
        self.show_snapshot_model = False
        self.theme_name = "night_shift"
        self._lattice_geometry: tuple[QRectF, float, float] | None = None
        self.setMinimumHeight(300)
        self.setMouseTracking(True)
        self.setToolTip(self.DEFAULT_TOOLTIP)

    def set_model_entrance(self, name: str | None) -> None:
        self.model_entrance = name
        self.update()

    def set_plane(self, plane: str) -> None:
        normalized = str(plane).strip().lower()
        if normalized not in {"x", "y"}:
            raise ValueError("plane must be 'x' or 'y'")
        self.plane = normalized
        self.update()

    def set_correction_bpms(self, names: tuple[str, ...]) -> None:
        self.correction_bpms = tuple(dict.fromkeys(str(name) for name in names))
        self.update()

    def _model_dispersion(self, curve: ModelOpticsCurve) -> np.ndarray:
        return curve.dx_mm if self.plane == "x" else curve.dy_mm

    def set_result(self, result: ModelResponseResult | None) -> None:
        self.result = result
        if result is None:
            self._lattice_geometry = None
            self.setToolTip(self.DEFAULT_TOOLTIP)
        self.update()

    def set_measurement(
        self,
        measurement: DispersionPlotDataset | None,
        reference: DispersionPlotDataset | None = None,
        overlays: tuple[DispersionPlotDataset, ...] = (),
    ) -> None:
        self.measurement = measurement
        self.reference_measurement = reference
        self.measurement_overlays = tuple(overlays)
        self.update()

    def set_model_visibility(self, *, design: bool, snapshot: bool) -> None:
        self.show_design_model = bool(design)
        self.show_snapshot_model = bool(snapshot)
        self.update()

    def set_theme(self, name: str) -> None:
        self.theme_name = name
        self.update()

    def _measurement_s_by_name(self) -> dict[str, float]:
        if self.result is not None:
            curve = self.result.selected_curve
            positions = {
                name: float(curve.s_m[index])
                for index, name in enumerate(curve.element_names)
            }
            if self.model_entrance:
                positions.setdefault(self.model_entrance, float(curve.s_m[0]))
            return positions
        if self.measurement is None:
            return {}
        return {
            name: float(index)
            for index, name in enumerate(self.measurement.bpm_names)
        }

    def unmapped_measurement_bpms(self) -> tuple[str, ...]:
        if self.result is None or self.measurement is None:
            return ()
        positions = self._measurement_s_by_name()
        return tuple(
            bpm
            for bpm, value, valid in zip(
                self.measurement.bpm_names,
                self.measurement.values_mm,
                self.measurement.valid,
            )
            if bool(valid)
            and math.isfinite(float(value))
            and bpm not in positions
        )

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        tokens = theme_tokens(self.theme_name)
        painter.fillRect(self.rect(), QColor(tokens["plot_bg"]))
        plot = self.rect().adjusted(58, 42, -18, -112)
        painter.setPen(QColor(tokens["text_muted"]))
        painter.drawText(12, 18, f"Dispersion η{self.plane} (mm)")
        if plot.width() <= 0 or plot.height() <= 0:
            return
        if self.result is None and self.measurement is None:
            painter.drawText(
                plot,
                Qt.AlignCenter,
                "Measure dispersion to begin",
            )
            return

        displayed_curves: list[np.ndarray] = []
        if self.result is not None and self.show_design_model:
            design_curve = self.result.design_curve or self.result.selected_curve
            displayed_curves.append(self._model_dispersion(design_curve))
        if (
            self.result is not None
            and self.show_snapshot_model
            and self.result.model_source != "design"
        ):
            displayed_curves.append(
                self._model_dispersion(self.result.selected_curve)
            )
        limit = max(
            (
                abs(float(value))
                for curve in displayed_curves
                for value in curve
            ),
            default=0.0,
        )
        for dataset in (
            self.measurement,
            self.reference_measurement,
            *self.measurement_overlays,
        ):
            if dataset is None:
                continue
            for value, sigma, valid in zip(
                dataset.values_mm,
                dataset.sigma_mm,
                dataset.valid,
            ):
                if not bool(valid) or not math.isfinite(float(value)):
                    continue
                uncertainty = float(sigma) if math.isfinite(float(sigma)) else 0.0
                limit = max(limit, abs(float(value)) + uncertainty)
        limit = max(limit * 1.1, 1.0e-6)
        if self.result is not None:
            s_values = self.result.selected_curve.s_m
            s_min = float(s_values[0])
            s_max = float(s_values[-1])
            s_by_name = self._measurement_s_by_name()
        else:
            assert self.measurement is not None
            s_min = 0.0
            s_max = float(max(len(self.measurement.bpm_names) - 1, 1))
            s_by_name = self._measurement_s_by_name()
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

        model_color = QColor(
            "#e66b5b" if self.plane == "x" else "#4c9be8"
        )
        if self.result is not None and self.show_design_model:
            design_curve = self.result.design_curve or self.result.selected_curve
            design_color = QColor(model_color)
            design_color.setAlpha(150)
            painter.setPen(QPen(design_color, 2, Qt.DotLine))
            painter.drawPolyline(
                points(
                    design_curve.s_m,
                    self._model_dispersion(design_curve),
                )
            )
        if (
            self.result is not None
            and self.show_snapshot_model
            and self.result.model_source != "design"
        ):
            snapshot_color = QColor(model_color)
            snapshot_color.setAlpha(190)
            painter.setPen(QPen(snapshot_color, 2, Qt.DashLine))
            painter.drawPolyline(
                points(
                    self.result.selected_curve.s_m,
                    self._model_dispersion(self.result.selected_curve),
                )
            )

        def draw_measurement(
            dataset: DispersionPlotDataset,
            color: QColor,
            *,
            radius: float,
            line_width: int,
            draw_markers: bool = True,
        ) -> None:
            for bpm, value, sigma, valid, is_target in zip(
                dataset.bpm_names,
                dataset.values_mm,
                dataset.sigma_mm,
                dataset.valid,
                dataset.target_mask,
            ):
                if not bool(valid) or not math.isfinite(float(value)):
                    continue
                if bpm not in s_by_name:
                    continue
                x = plot.left() + (s_by_name[bpm] - s_min) / s_span * plot.width()
                y = plot.center().y() - float(value) / limit * plot.height() / 2.0
                point_color = QColor(color)
                if not bool(is_target):
                    point_color.setAlpha(150)
                painter.setPen(QPen(point_color, line_width))
                if draw_markers and math.isfinite(float(sigma)):
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
                if draw_markers:
                    painter.setBrush(
                        point_color if bool(is_target) else Qt.NoBrush
                    )
                    point_radius = radius if bool(is_target) else max(2.5, radius - 0.5)
                    painter.drawEllipse(
                        QPointF(x, y),
                        point_radius,
                        point_radius,
                    )

        for dataset in self.measurement_overlays:
            overlay_color = QColor(tokens["text_muted"])
            overlay_color.setAlpha(105)
            draw_measurement(
                dataset,
                overlay_color,
                radius=2.5,
                line_width=1,
                draw_markers=True,
            )
        if self.reference_measurement is not None:
            reference_color = QColor(tokens["text_muted"])
            reference_color.setAlpha(150)
            draw_measurement(
                self.reference_measurement,
                reference_color,
                radius=3.0,
                line_width=1,
            )
        if self.measurement is not None:
            draw_measurement(
                self.measurement,
                QColor("#f2c14e"),
                radius=4.5,
                line_width=2,
            )

        painter.setPen(QColor(tokens["text_muted"]))
        painter.drawText(4, plot.top() + 5, f"{limit:.3g}")
        painter.drawText(4, plot.bottom(), f"{-limit:.3g}")
        legend_y = plot.top() - 9
        model_curves_visible = bool(
            self.result is not None
            and (
                self.show_design_model
                or (
                    self.show_snapshot_model
                    and self.result.model_source != "design"
                )
            )
        )
        if model_curves_visible:
            painter.setPen(model_color)
            painter.drawText(
                plot.left() + 8,
                legend_y,
                f"η{self.plane}",
            )
        legend_x = float(
            plot.left() + (38 if model_curves_visible else 8)
        )
        text_color = QColor(tokens["text_muted"])
        font_metrics = painter.fontMetrics()

        def draw_series_key(label: str, color: QColor) -> None:
            nonlocal legend_x
            sample_pen = QPen(color, 4)
            sample_pen.setCapStyle(Qt.RoundCap)
            painter.setPen(sample_pen)
            painter.drawLine(
                QPointF(legend_x, legend_y - 4),
                QPointF(legend_x + 10, legend_y - 4),
            )
            painter.setPen(text_color)
            painter.setBrush(Qt.NoBrush)
            painter.drawText(round(legend_x + 16), legend_y, label)
            legend_x += 24 + font_metrics.horizontalAdvance(label)

        def draw_line_key(label: str, style: Qt.PenStyle) -> None:
            nonlocal legend_x
            painter.setPen(QPen(text_color, 2, style))
            painter.drawLine(
                QPointF(legend_x, legend_y - 4),
                QPointF(legend_x + 14, legend_y - 4),
            )
            painter.drawText(round(legend_x + 20), legend_y, label)
            legend_x += 28 + font_metrics.horizontalAdvance(label)

        def draw_role_keys() -> None:
            nonlocal legend_x
            label = "BPM:"
            painter.setPen(text_color)
            painter.drawText(round(legend_x), legend_y, label)
            legend_x += font_metrics.horizontalAdvance(label) + 8
            for role_label, filled in (
                ("Correction", True),
                ("Monitor", False),
            ):
                painter.setPen(QPen(text_color, 1))
                painter.setBrush(text_color if filled else Qt.NoBrush)
                painter.drawEllipse(
                    QPointF(legend_x + 3, legend_y - 4),
                    3.0,
                    3.0,
                )
                painter.setPen(text_color)
                painter.drawText(round(legend_x + 10), legend_y, role_label)
                legend_x += 18 + font_metrics.horizontalAdvance(role_label)

        if self.measurement is not None:
            draw_series_key(self.measurement.label, QColor("#f2c14e"))
        if self.reference_measurement is not None:
            reference_key_color = QColor(tokens["text_muted"])
            reference_key_color.setAlpha(150)
            draw_series_key(self.reference_measurement.label, reference_key_color)
        if self.measurement_overlays:
            overlay_key_color = QColor(tokens["text_muted"])
            overlay_key_color.setAlpha(105)
            draw_series_key("Accepted generations", overlay_key_color)
        role_datasets = (
            self.measurement,
            self.reference_measurement,
            *self.measurement_overlays,
        )
        if any(
            dataset is not None and np.any(~dataset.target_mask)
            for dataset in role_datasets
        ):
            draw_role_keys()
        if self.result is not None and self.show_design_model:
            draw_line_key("Design model", Qt.DotLine)
        if (
            self.result is not None
            and self.show_snapshot_model
            and self.result.model_source != "design"
        ):
            draw_line_key("Current K1 model", Qt.DashLine)
        if self.result is not None:
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
        else:
            self._lattice_geometry = None
            painter.setPen(QColor(tokens["text_muted"]))
            for name, position in s_by_name.items():
                x = plot.left() + (position - s_min) / s_span * plot.width()
                painter.drawText(
                    QRectF(x - 35.0, plot.bottom() + 16.0, 70.0, 18.0),
                    Qt.AlignCenter,
                    name,
                )

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
        curve = self.result.selected_curve
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
        correction_bpms = set(self.correction_bpms)
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

            if self._is_bend(element_type):
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

            if name in correction_bpms:
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
            elif self._is_bend(element_type):
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
        curve = self.result.selected_curve
        indices = self._visible_element_indices()
        bend_indices = [
            index
            for index in indices
            if self._is_bend(curve.element_types[index])
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
            label_width = float(painter.fontMetrics().horizontalAdvance(label))
            painter.drawText(
                QRectF(x + 12.0, y, label_width + 2.0, 14.0),
                label,
            )
            x += 12.0 + label_width + 12.0
        visible_names = {curve.element_names[index] for index in indices}
        if visible_names.intersection(self.correction_bpms):
            marker_color = QColor(tokens["focus"])
            marker_color.setAlpha(150)
            painter.setPen(QPen(marker_color, 2, Qt.DotLine))
            painter.drawLine(
                QPointF(x + 4.0, y + 1.0),
                QPointF(x + 4.0, y + 12.0),
            )
            painter.setPen(QColor(tokens["text_muted"]))
            painter.drawText(
                QRectF(x + 10.0, y, 100.0, 14.0),
                "Correction BPM",
            )

    def _visible_element_indices(self) -> list[int]:
        if self.result is None:
            return []
        curve = self.result.selected_curve
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
            cls._is_bend(element_type)
            or "QUAD" in element_type
            or cls._is_bpm(name, element_type)
            or cls._is_rf(element_type)
        )

    @staticmethod
    def _is_bend(element_type: str) -> bool:
        normalized = element_type.upper()
        return "BEND" in normalized or normalized in {"SBEN", "RBEN"}

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
        curve = self.result.selected_curve
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
        *,
        offline_demo: bool = False,
    ) -> None:
        super().__init__()
        install_qt_window_raise_handler(self)
        self.app_context = app_context
        self.offline_demo = bool(offline_demo)
        self._offline_demo_window: MainWindow | None = None
        self.setWindowTitle(self._window_title())
        self.setMinimumSize(1120, 760)
        self.resize(1700, 1020)
        self.theme_name = (
            "control_room" if resolve_initial_theme() == "light" else "night_shift"
        )
        self.worker: WorkflowWorker | None = None
        self.preflight_worker: LivePreflightWorker | None = None
        self.model_worker: ModelResponseWorker | None = None
        self.pending_model_source: str | None = None
        self.current_snapshot_time: datetime | None = None
        self._refresh_snapshot_after_task = False
        self.live_plot_measurement: DispersionPlotDataset | None = None
        self.reference_plot_measurement: DispersionPlotDataset | None = None
        self.live_plane_measurements: dict[str, DispersionPlotDataset] = {}
        self.latest_plane_measurements: dict[
            str,
            DispersionMeasurement,
        ] = {}
        self._last_unmapped_plot_bpms: tuple[str, ...] = ()
        self._automatic_initial_measurement: DispersionMeasurement | None = None
        self._active_task = ""
        self.correction_mode: str | None = None
        self.latest_measurement_time: datetime | None = None
        self.latest_measurement: DispersionMeasurement | None = None
        self.latest_response: ResponseMatrixResult | None = None
        self.latest_joint_response: JointResponseAnalysisResult | None = None
        self.correction_recommendation: CorrectionRecommendation | None = None
        self.correction_session_runs: list[CorrectionSessionRun] = []
        self.correction_restore_request: CorrectionRestoreRequest | None = None
        self.last_live_preflight = None
        self.operation_plan: dict | None = None
        self._loading_widgets = False
        self.config_path: Path | None = None
        self.config = config or default_offline_config()
        self.configured_energy_calibration = dict(self.config.energy_knob.calibration)
        self.session_energy_calibration_source: str | None = None
        self.selected_knobs = tuple(self.config.runtime_knobs)
        self.knob_hard_limits = tuple(
            knob.limit for knob in self.config.runtime_knobs
        )
        self.available_bpms = (
            selectable_profile_bpms(
                app_context,
                self.config.measurement.plane,
            )
            if app_context is not None
            else self.config.target_bpms
        )
        self.available_quadrupoles = (
            selectable_profile_quadrupoles(app_context)
            if app_context is not None
            else tuple(
                dict.fromkeys(
                    device
                    for knob in self.config.runtime_knobs
                    for device in knob.devices
                )
            )
        )
        self._close_when_finished = False

        self._build_ui()
        self._configure_profile_mode()
        self._load_config_to_widgets()
        self._set_running(False, "")
        self._apply_theme()
        self._refresh_status("Ready")

    def _window_title(self) -> str:
        if self.offline_demo:
            return "Dispersion Correction · Offline Demo"
        if self.app_context is None:
            return "Dispersion Correction"
        return (
            f"Dispersion Correction · {self.app_context.profile.machine.display_name} · "
            f"{self.app_context.control_backend.name.upper()}"
        )

    def _configure_profile_mode(self) -> None:
        model_available = self._model_analysis_available()
        self.model_response_button.setVisible(model_available)
        self.model_source_combo.setVisible(model_available)
        self.model_source_label.setVisible(model_available)
        self.model_boundary_label.setVisible(model_available)
        self.show_design_model_checkbox.setVisible(model_available)
        self.show_snapshot_model_checkbox.setVisible(model_available)
        self.measurement_action_button.setVisible(
            not self.config.section.model_only
        )
        self.measurement_status_label.setVisible(
            not self.config.section.model_only
        )
        if self.offline_demo:
            self.config_title_label.setText("Offline Demo")
            self.load_button.hide()
        if self.app_context is None:
            return
        self.load_button.hide()
        self.load_button.setToolTip("Runtime configuration is managed by the selected machine profile.")
        self.bpm_edit.setReadOnly(True)
        self.config_title_label.setText("Configuration")
        fixed_selection = (
            self.config.section.model_only
            or self.config.section.diagnostic_only
        )
        self.bpm_select_button.setVisible(not fixed_selection)
        self.knob_select_button.setVisible(not fixed_selection)

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
        self.app_title_label = QLabel("Dispersion Correction")
        self.app_title_label.setObjectName("titleLabel")
        header_layout.addWidget(self.app_title_label)
        header_layout.addStretch(1)

        if self.app_context is None:
            runtime_machine_id = "standalone"
            runtime_machine_name = "Standalone"
            runtime_backend = self.config.backend.type
        else:
            runtime_machine_id = self.app_context.profile.machine.id
            runtime_machine_name = self.app_context.profile.machine.display_name
            runtime_backend = self.app_context.control_backend.name
        self.runtime_context_widget = RuntimeContextWidget(
            machine_id=runtime_machine_id,
            machine_display_name=runtime_machine_name,
            control_backend=runtime_backend,
            parent=frame,
        )
        header_layout.addWidget(self.runtime_context_widget)

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
                ("ACCESS", "-"),
                ("ENERGY STEP", "-"),
                ("READINESS", "UNCHECKED"),
                ("LAST RESULT", "-"),
            ]
        )
        energy_status = self.status_strip.items["ENERGY STEP"]
        energy_status.setMinimumWidth(160)
        energy_status.value_label.setWordWrap(False)
        if self.offline_demo:
            access_status = self.status_strip.items["ACCESS"]
            access_status.setMinimumWidth(125)
            access_status.value_label.setWordWrap(False)
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
        self.preflight_button = QPushButton("Check PVs")
        self.preflight_button.setObjectName("preflightButton")
        self.preflight_button.clicked.connect(self._start_live_preflight)
        heading_layout.addWidget(self.preflight_button)
        self.load_button = QPushButton("Load Config")
        self.load_button.setObjectName("configLoadButton")
        self.load_button.clicked.connect(self._load_config_dialog)
        heading_layout.addWidget(self.load_button)
        layout.addLayout(heading_layout)

        self.machine_card = QFrame()
        self.machine_card.setObjectName("controlSectionCard")
        machine_card_layout = QVBoxLayout(self.machine_card)
        machine_card_layout.setContentsMargins(10, 8, 10, 10)
        machine_card_layout.setSpacing(6)
        machine_header = QHBoxLayout()
        machine_header.setContentsMargins(0, 0, 0, 0)
        self.machine_card_title = QLabel("Machine")
        self.machine_card_title.setObjectName("controlSectionTitle")
        machine_header.addWidget(self.machine_card_title)
        machine_header.addStretch(1)
        machine_card_layout.addLayout(machine_header)

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
        self.bpm_edit.setReadOnly(True)
        bpm_selector = QWidget()
        bpm_selector.setFixedHeight(34)
        bpm_selector_layout = QHBoxLayout(bpm_selector)
        bpm_selector_layout.setContentsMargins(0, 0, 0, 0)
        bpm_selector_layout.setSpacing(6)
        bpm_selector_layout.addWidget(self.bpm_edit, 1)
        self.bpm_select_button = QPushButton("Set")
        self.bpm_select_button.setObjectName("bpmSelectButton")
        self.bpm_select_button.setFixedHeight(34)
        self.bpm_select_button.setFixedWidth(78)
        self.bpm_select_button.clicked.connect(
            self._configure_correction_bpms
        )
        bpm_selector_layout.addWidget(self.bpm_select_button, 0, Qt.AlignVCenter)
        self._add_form_row(machine_form, "Correction BPMs", bpm_selector)

        self.monitor_bpm_edit = QLineEdit()
        self.monitor_bpm_edit.setFixedHeight(34)
        self.monitor_bpm_edit.setReadOnly(True)
        self.monitor_bpm_edit.setToolTip(
            "Measured and displayed for diagnostics, but excluded from correction "
            "RMS, response solving, and acceptance decisions."
        )
        self._add_form_row(
            machine_form,
            "Monitor BPMs",
            self.monitor_bpm_edit,
        )

        self.knob_edit = QLineEdit()
        self.knob_edit.setFixedHeight(34)
        self.knob_edit.setReadOnly(True)
        knob_selector = QWidget()
        knob_selector.setFixedHeight(34)
        knob_selector_layout = QHBoxLayout(knob_selector)
        knob_selector_layout.setContentsMargins(0, 0, 0, 0)
        knob_selector_layout.setSpacing(6)
        knob_selector_layout.addWidget(self.knob_edit, 1)
        self.knob_select_button = QPushButton("Set")
        self.knob_select_button.setObjectName("knobSelectButton")
        self.knob_select_button.setFixedHeight(34)
        self.knob_select_button.setFixedWidth(78)
        self.knob_select_button.setVisible(self.app_context is not None)
        self.knob_select_button.clicked.connect(self._select_knobs)
        knob_selector_layout.addWidget(self.knob_select_button, 0, Qt.AlignVCenter)
        self._add_form_row(machine_form, "Quad Knobs", knob_selector)

        self.delta_spin = QDoubleSpinBox()
        self.delta_spin.setDecimals(8)
        self.delta_spin.setRange(1.0e-7, 1.0e-2)
        self.delta_spin.setSingleStep(1.0e-4)
        self.delta_spin.setToolTip("Relative momentum perturbation used at +dp/p and -dp/p to measure D_eff.")
        self.delta_spin.valueChanged.connect(self._energy_step_changed)
        self.energy_step_field_label = self._add_form_row(
            machine_form,
            "Energy Step (Δp/p)",
            self.delta_spin,
        )
        machine_card_layout.addLayout(machine_form)

        self.energy_step_summary = QLabel()
        self.energy_step_summary.setObjectName("energyStepSummary")
        self.energy_step_summary.setWordWrap(True)
        machine_card_layout.addWidget(self.energy_step_summary)

        self.energy_calibration_controls = QWidget()
        calibration_layout = QVBoxLayout(self.energy_calibration_controls)
        calibration_layout.setContentsMargins(0, 0, 0, 0)
        calibration_layout.setSpacing(5)
        self.calibration_status_label = QLabel()
        self.calibration_status_label.setObjectName("energyCalibrationStatus")
        calibration_layout.addWidget(self.calibration_status_label)
        calibration_actions = QHBoxLayout()
        calibration_actions.setContentsMargins(0, 0, 0, 0)
        calibration_actions.setSpacing(6)
        self.restore_calibration_button = QPushButton("Restore Configured")
        self.restore_calibration_button.clicked.connect(
            self._restore_configured_calibration
        )
        calibration_actions.addWidget(self.restore_calibration_button)
        self.calibration_button = QPushButton("Edit Energy Knob Calibration…")
        self.calibration_button.clicked.connect(self._open_calibration_editor)
        calibration_actions.addWidget(self.calibration_button, 1)
        calibration_layout.addLayout(calibration_actions)
        machine_card_layout.addWidget(self.energy_calibration_controls)
        layout.addWidget(self.machine_card)

        self.measurement_card = QFrame()
        self.measurement_card.setObjectName("controlSectionCard")
        measurement_card_layout = QVBoxLayout(self.measurement_card)
        measurement_card_layout.setContentsMargins(10, 8, 10, 10)
        measurement_card_layout.setSpacing(6)
        self.measurement_card_title = QLabel("Measurement")
        self.measurement_card_title.setObjectName("controlSectionTitle")
        measurement_card_layout.addWidget(self.measurement_card_title)
        sampling_form = self._config_form()

        self.samples_per_step_spin = QSpinBox()
        self.samples_per_step_spin.setRange(1, 100)
        self.samples_per_step_spin.setToolTip(
            "BPM samples per energy setting for Measure Dispersion, Q response "
            "scans, and intermediate iteration measurements."
        )
        self.samples_per_step_spin.valueChanged.connect(self._workflow_input_changed)
        self._add_form_row(sampling_form, "Scan Samples", self.samples_per_step_spin)

        self.settle_time_spin = QDoubleSpinBox()
        self.settle_time_spin.setDecimals(2)
        self.settle_time_spin.setRange(0.0, 120.0)
        self.settle_time_spin.setSingleStep(0.5)
        self.settle_time_spin.setToolTip("Wait after each machine setting change before reading BPMs.")
        self.settle_time_spin.valueChanged.connect(self._workflow_input_changed)
        self._add_form_row(sampling_form, "Settle Time (s)", self.settle_time_spin)

        self.sample_interval_spin = QDoubleSpinBox()
        self.sample_interval_spin.setDecimals(3)
        self.sample_interval_spin.setRange(0.0, 60.0)
        self.sample_interval_spin.setSingleStep(0.05)
        self.sample_interval_spin.setToolTip("Wait between consecutive BPM samples; no wait follows the final sample.")
        self.sample_interval_spin.valueChanged.connect(self._workflow_input_changed)
        self._add_form_row(
            sampling_form,
            "Sample Interval (s)",
            self.sample_interval_spin,
        )

        self.final_samples_spin = QSpinBox()
        self.final_samples_spin.setRange(1, 200)
        self.final_samples_spin.setToolTip(
            "BPM samples per energy setting used to verify an applied correction "
            "and the final automatic-correction result."
        )
        self.final_samples_spin.valueChanged.connect(self._workflow_input_changed)
        self.verification_samples_field_label = self._add_form_row(
            sampling_form,
            "Verification Samples",
            self.final_samples_spin,
        )
        measurement_card_layout.addLayout(sampling_form)

        self.measurement_action_button = QPushButton("Measure Dispersion")
        self.measurement_action_button.setObjectName("measurementActionButton")
        self.measurement_action_button.setProperty("role", "control")
        self.measurement_action_button.clicked.connect(
            lambda: self._start_task("measure")
        )
        measurement_card_layout.addWidget(self.measurement_action_button)
        self.measurement_status_label = QLabel("No valid dispersion measurement")
        self.measurement_status_label.setObjectName("measurementStatus")
        self.measurement_status_label.setProperty("muted", "true")
        self.measurement_status_label.setWordWrap(True)
        measurement_card_layout.addWidget(self.measurement_status_label)
        layout.addWidget(self.measurement_card)

        self.correction_step_card = QFrame()
        self.correction_step_card.setObjectName("controlSectionCard")
        correction_card_layout = QVBoxLayout(self.correction_step_card)
        correction_card_layout.setContentsMargins(10, 8, 10, 10)
        correction_card_layout.setSpacing(6)
        self.correction_step_card_title = QLabel("Correction Step")
        self.correction_step_card_title.setObjectName("controlSectionTitle")
        correction_card_layout.addWidget(self.correction_step_card_title)
        correction_step_form = self._config_form()

        self.gain_spin = QDoubleSpinBox()
        self.gain_spin.setDecimals(3)
        self.gain_spin.setRange(0.001, 1.0)
        self.gain_spin.setSingleStep(0.05)
        self.gain_spin.valueChanged.connect(self._correction_setting_changed)
        self._add_form_row(correction_step_form, "Gain", self.gain_spin)

        self.max_step_pct_spin = QDoubleSpinBox()
        self.max_step_pct_spin.setDecimals(1)
        self.max_step_pct_spin.setRange(0.1, 100.0)
        self.max_step_pct_spin.setSingleStep(5.0)
        self.max_step_pct_spin.valueChanged.connect(lambda _value: self._update_knob_summary())
        self.max_step_pct_spin.valueChanged.connect(
            self._correction_setting_changed
        )
        self._add_form_row(
            correction_step_form,
            "Max Step (%)",
            self.max_step_pct_spin,
        )
        correction_card_layout.addLayout(correction_step_form)
        layout.addWidget(self.correction_step_card)

        # Automatic-only settings are edited in the confirmation dialog. Keep
        # these child widgets as session/config state without duplicating their
        # controls in the left panel.
        self.max_iter_spin = QSpinBox(frame)
        self.max_iter_spin.setRange(1, 20)
        self.max_iter_spin.setToolTip(
            "Maximum automatic correction generations; the loop may stop earlier."
        )
        self.max_iter_spin.valueChanged.connect(
            self._automatic_setting_changed
        )
        self.max_iter_spin.valueChanged.connect(
            self._update_automatic_correction_tooltip
        )
        self.max_iter_spin.hide()

        self.response_update_combo = QComboBox(frame)
        self.response_update_combo.addItems(["once", "every_iteration"])
        self.response_update_combo.currentTextChanged.connect(
            self._automatic_setting_changed
        )
        self.response_update_combo.currentTextChanged.connect(
            self._update_automatic_correction_tooltip
        )
        self.response_update_combo.hide()

        self.run_button = QPushButton("Automatic Correction…")
        self.run_button.setObjectName("automaticCorrectionButton")
        self.run_button.setProperty("role", "control")
        self.run_button.setToolTip(
            "Run several correction generations without confirmation between "
            "accepted steps."
        )
        self.run_button.clicked.connect(self._confirm_automatic_correction)

        self.operation_banner = QLabel()
        self.operation_banner.setObjectName("operationBanner")
        self.operation_banner.setWordWrap(True)
        self.operation_banner.setProperty("tone", "warning")
        layout.addWidget(self.operation_banner)

        self.abort_button = QPushButton("Abort")
        self.abort_button.setProperty("role", "danger")
        self.abort_button.setEnabled(False)
        self.abort_button.setVisible(False)
        self.abort_button.clicked.connect(self._request_abort)
        layout.addWidget(self.abort_button)

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
        frame.setObjectName("workspacePanel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.online_page = QFrame()
        self.online_page.setObjectName("workflowActionCard")
        self.online_page.setMinimumHeight(150)
        self.online_page.setMaximumHeight(290)
        self.online_content = self.online_page
        online_layout = QVBoxLayout(self.online_page)
        online_layout.setContentsMargins(14, 10, 14, 12)
        online_layout.setSpacing(6)

        workflow_header = QHBoxLayout()
        self.workflow_title_label = QLabel("Correction Workflow")
        self.workflow_title_label.setObjectName("cardTitle")
        workflow_header.addWidget(self.workflow_title_label)
        workflow_header.addStretch(1)
        self.offline_demo_button = QPushButton("Offline Demo…")
        self.offline_demo_button.setObjectName("workflowSecondaryButton")
        self.offline_demo_button.setToolTip(
            "Open an independent deterministic demonstration. It does not access "
            "or write any machine PV."
        )
        self.offline_demo_button.clicked.connect(self._open_offline_demo)
        self.offline_demo_button.setVisible(
            self.app_context is not None and not self.offline_demo
        )
        workflow_header.addWidget(self.offline_demo_button)
        self.restore_initial_state_button = QPushButton(
            "Restore Initial State…"
        )
        self.restore_initial_state_button.setObjectName(
            "restoreInitialStateButton"
        )
        self.restore_initial_state_button.setProperty("role", "control")
        self.restore_initial_state_button.clicked.connect(
            self._restore_initial_correction_state
        )
        self.restore_initial_state_button.hide()
        workflow_header.addWidget(self.restore_initial_state_button)
        self.history_button = QPushButton("History…")
        self.history_button.setObjectName("workflowSecondaryButton")
        self.history_button.clicked.connect(self._show_iteration_history)
        # Keep the former attribute as a compatibility alias for callers that
        # customized the window before the two history entries were merged.
        self.last_run_button = self.history_button
        workflow_header.addWidget(self.history_button)
        online_layout.addLayout(workflow_header)

        self.workflow_state_label = QLabel("Current state")
        self.workflow_state_label.setObjectName("workflowState")
        self.workflow_state_label.setWordWrap(True)
        online_layout.addWidget(self.workflow_state_label)
        self.workflow_hint_label = QLabel()
        self.workflow_hint_label.setObjectName("workflowHint")
        self.workflow_hint_label.setWordWrap(True)
        online_layout.addWidget(self.workflow_hint_label)
        self.workflow_summary_label = QLabel()
        self.workflow_summary_label.setObjectName("workflowSummary")
        self.workflow_summary_label.setWordWrap(True)
        online_layout.addWidget(self.workflow_summary_label)
        self.correction_mode_actions = QWidget()
        correction_mode_layout = QHBoxLayout(self.correction_mode_actions)
        correction_mode_layout.setContentsMargins(0, 0, 0, 0)
        correction_mode_layout.setSpacing(8)
        self.next_action_button = QPushButton("Manual Correction")
        self.manual_correction_button = self.next_action_button
        self.next_action_button.setObjectName("nextWorkflowAction")
        self.next_action_button.setProperty("role", "control")
        self.next_action_button.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Fixed,
        )
        self.run_button.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Fixed,
        )
        self.next_action_button.clicked.connect(self._run_next_workflow_action)
        correction_mode_layout.addWidget(self.next_action_button, 1)
        correction_mode_layout.addWidget(self.run_button, 1)
        online_layout.addWidget(self.correction_mode_actions)
        self._update_automatic_correction_tooltip()
        workflow_secondary_actions = QHBoxLayout()
        self.back_to_correction_methods_button = QPushButton(
            "Back to Correction Methods"
        )
        self.back_to_correction_methods_button.setObjectName(
            "workflowSecondaryButton"
        )
        self.back_to_correction_methods_button.clicked.connect(
            self._return_to_correction_methods
        )
        workflow_secondary_actions.addWidget(
            self.back_to_correction_methods_button
        )
        workflow_secondary_actions.addStretch(1)
        self.response_details_button = QPushButton("Response Details…")
        self.response_details_button.setObjectName("workflowSecondaryButton")
        self.response_details_button.clicked.connect(self._show_response_details)
        workflow_secondary_actions.addWidget(self.response_details_button)
        online_layout.addLayout(workflow_secondary_actions)

        # These operation-specific buttons remain as internal state holders for the
        # existing safety/tool-tip logic. Operators use only next_action_button.
        self.measure_button = QPushButton("Measure Dispersion", self.online_content)
        self.measure_button.clicked.connect(lambda: self._start_task("measure"))
        self.measure_button.hide()
        self.response_button = QPushButton("Measure Q Response", self.online_content)
        self.response_button.clicked.connect(lambda: self._start_task("response"))
        self.response_button.hide()
        self.review_button = QPushButton("Review Recommendation", self.online_content)
        self.review_button.clicked.connect(self._review_recommendation)
        self.review_button.hide()

        self.measure_table = self._table(
            ["BPM", "Role", "Measured mm", "Target mm", "Residual mm", "Valid"]
        )
        self.measure_page = QWidget()
        measure_layout = QVBoxLayout(self.measure_page)
        measure_layout.setContentsMargins(8, 4, 8, 8)
        self.measure_title = QLabel()
        self.measure_title.setObjectName("workspaceIntro")
        measure_layout.addWidget(self.measure_title)
        measure_layout.addWidget(self.measure_table, 1)
        self.measure_page.setParent(self.online_content)
        self.measure_page.hide()

        self.response_dialog = QDialog(self)
        self.response_dialog.setObjectName("workflowDetailsDialog")
        self.response_dialog.setWindowTitle("Q Response Diagnostics")
        self.response_dialog.resize(900, 620)
        response_dialog_layout = QVBoxLayout(self.response_dialog)
        self.response_table = self._table([])
        self.response_info = QPlainTextEdit()
        self.response_info.setReadOnly(True)
        self.response_page = QWidget(self.response_dialog)
        response_layout = QVBoxLayout(self.response_page)
        response_layout.setContentsMargins(8, 4, 8, 8)
        response_title = QLabel("Measured quadrupole response matrix")
        response_title.setObjectName("workspaceIntro")
        response_layout.addWidget(response_title)
        response_layout.addWidget(self.response_table, 3)
        response_layout.addWidget(self.response_info, 1)
        response_dialog_layout.addWidget(self.response_page, 1)
        response_dialog_actions = QHBoxLayout()
        response_dialog_actions.addStretch(1)
        self.apply_joint_recommendation_button = QPushButton(
            "Apply and Verify"
        )
        self.apply_joint_recommendation_button.setProperty("role", "control")
        self.apply_joint_recommendation_button.clicked.connect(
            self._apply_joint_recommendation
        )
        self.apply_joint_recommendation_button.hide()
        response_dialog_actions.addWidget(
            self.apply_joint_recommendation_button
        )
        self.close_response_details_button = QPushButton("Close")
        self.close_response_details_button.clicked.connect(
            self.response_dialog.close
        )
        response_dialog_actions.addWidget(self.close_response_details_button)
        response_dialog_layout.addLayout(response_dialog_actions)

        self.recommendation_dialog = QDialog(self)
        self.recommendation_dialog.setObjectName("workflowDetailsDialog")
        self.recommendation_dialog.setWindowTitle("Recommendation Review")
        self.recommendation_dialog.resize(1100, 760)
        recommendation_dialog_layout = QVBoxLayout(self.recommendation_dialog)
        self.correction_page = QWidget(self.recommendation_dialog)
        correction_layout = QVBoxLayout(self.correction_page)
        correction_layout.setContentsMargins(8, 4, 8, 8)
        correction_title = QLabel(
            "Review one bounded correction generation prepared from measured dispersion "
            "and Q response. No quadrupole target is written until you confirm below."
        )
        correction_title.setObjectName("workspaceIntro")
        correction_title.setWordWrap(True)
        correction_layout.addWidget(correction_title)
        self.correction_state_label = QLabel(
            "Prepare a correction to measure the Q response and calculate a "
            "recommendation."
        )
        self.correction_state_label.setWordWrap(True)
        correction_layout.addWidget(self.correction_state_label)
        self.recommendation_summary_label = QLabel(
            "No recommendation has been calculated."
        )
        self.recommendation_summary_label.setWordWrap(True)
        correction_layout.addWidget(self.recommendation_summary_label)
        prediction_title = QLabel("Predicted dispersion after the reviewed step")
        prediction_title.setObjectName("workspaceIntro")
        correction_layout.addWidget(prediction_title)
        self.recommendation_prediction_table = self._table(
            [
                "BPM",
                "Role",
                "Measured mm",
                "Target mm",
                "Predicted mm",
                "Predicted residual mm",
            ]
        )
        correction_layout.addWidget(self.recommendation_prediction_table, 1)
        device_title = QLabel("Reviewed quadrupole changes")
        device_title.setObjectName("workspaceIntro")
        correction_layout.addWidget(device_title)
        self.recommendation_table = self._table(
            ["Device", "Current", "Change", "Target", "Source knob", "Status"]
        )
        correction_layout.addWidget(self.recommendation_table, 1)

        correction_actions = QHBoxLayout()
        self.compute_recommendation_button = QPushButton(
            "Recalculate Recommendation",
            self.correction_page,
        )
        self.compute_recommendation_button.clicked.connect(self._compute_recommendation)
        self.compute_recommendation_button.hide()
        correction_actions.addStretch(1)
        self.apply_recommendation_button = QPushButton(
            "Apply and Verify",
            self.correction_page,
        )
        self.apply_recommendation_button.setProperty("role", "control")
        self.apply_recommendation_button.clicked.connect(
            self._apply_reviewed_recommendation
        )
        correction_actions.addWidget(self.apply_recommendation_button)
        correction_layout.addLayout(correction_actions)
        recommendation_dialog_layout.addWidget(self.correction_page, 1)
        recommendation_dialog_actions = QHBoxLayout()
        recommendation_dialog_actions.addStretch(1)
        self.close_recommendation_details_button = QPushButton("Close")
        self.close_recommendation_details_button.clicked.connect(
            self.recommendation_dialog.close
        )
        recommendation_dialog_actions.addWidget(
            self.close_recommendation_details_button
        )
        recommendation_dialog_layout.addLayout(recommendation_dialog_actions)

        self.correction_table = self._table(
            ["Iter", "Gain", "Accepted", "RMS Before", "RMS After", "Reason"]
        )

        self.model_dialog = QDialog(self)
        self.model_dialog.setObjectName("modelDetailsDialog")
        self.model_dialog.setWindowTitle("Model Details")
        self.model_dialog.resize(1100, 720)
        model_dialog_layout = QVBoxLayout(self.model_dialog)
        self.model_page = QWidget(self.model_dialog)
        model_layout = QVBoxLayout(self.model_page)
        model_layout.setContentsMargins(8, 4, 8, 8)
        model_intro = QLabel(
            "Measured BPM dispersion is the primary result. Add design or current-K1 "
            "model curves only when they help explain the measurement."
        )
        model_intro.setObjectName("workspaceIntro")
        model_intro.setWordWrap(True)
        model_layout.addWidget(model_intro)

        model_actions = QHBoxLayout()
        self.model_source_label = QLabel("Calculate")
        model_actions.addWidget(self.model_source_label)
        self.model_source_combo = QComboBox()
        self.model_source_combo.addItem("Design lattice", "design")
        if self.app_context is not None:
            backend_name = self.app_context.control_backend.name.lower()
            self.model_source_combo.addItem("Current K1 model", "live")
            snapshot_tooltip = (
                f"Reads quadrupole K1 PVs from the active {backend_name.upper()} backend "
                "without writing machine state."
            )
        else:
            snapshot_tooltip = "Current K1 model requires a machine-profile backend."
        self.model_source_combo.setToolTip(
            snapshot_tooltip
        )
        self.model_source_combo.currentIndexChanged.connect(self._model_source_changed)
        model_actions.addWidget(self.model_source_combo)
        self.model_response_button = QPushButton("Analyze Model")
        self.model_response_button.clicked.connect(
            lambda: self._start_model_response()
        )
        self.model_response_button.setVisible(self._model_analysis_available())
        model_actions.addWidget(self.model_response_button)
        self.model_boundary_label = QLabel()
        self.model_boundary_label.setObjectName("modelBoundaryLabel")
        model_actions.addWidget(self.model_boundary_label)
        model_actions.addStretch(1)
        model_layout.addLayout(model_actions)
        model_notice = QLabel(
            "Measurement display and model comparison never write machine PVs."
        )
        model_notice.setObjectName("modelSafetyNotice")
        model_layout.addWidget(model_notice)
        self.model_empty_label = QLabel(
            "Choose a model source and click Analyze Model. Model and measurement "
            "comparison details will appear here."
        )
        self.model_empty_label.setObjectName("workspaceIntro")
        self.model_empty_label.setAlignment(Qt.AlignCenter)
        self.model_empty_label.setWordWrap(True)
        model_layout.addWidget(self.model_empty_label, 1)
        self.model_table = self._table([])
        self.model_table.setVisible(False)
        model_layout.addWidget(self.model_table, 1)
        self.model_measure_table = self._table([])
        self.model_measure_table.setVisible(False)
        model_layout.addWidget(self.model_measure_table, 1)
        self.model_info = QPlainTextEdit()
        self.model_info.setReadOnly(True)
        self.model_info.setVisible(False)
        model_layout.addWidget(self.model_info, 1)
        model_dialog_layout.addWidget(self.model_page, 1)
        model_dialog_actions = QHBoxLayout()
        self.apply_design_k1_button = QPushButton("Apply Design K1…")
        self.apply_design_k1_button.setProperty("role", "control")
        self.apply_design_k1_button.setToolTip(
            "Review and write the lattice design K1 values for the active correction quadrupoles."
        )
        self.apply_design_k1_button.clicked.connect(self._apply_design_k1)
        model_dialog_actions.addWidget(self.apply_design_k1_button)
        self.design_k1_status_label = QLabel()
        self.design_k1_status_label.setObjectName("designK1StatusLabel")
        self.design_k1_status_label.setProperty("role", "field")
        self.design_k1_status_label.setWordWrap(True)
        self.design_k1_status_label.hide()
        model_dialog_actions.addWidget(self.design_k1_status_label, 1)
        self.close_model_details_button = QPushButton("Close")
        self.close_model_details_button.clicked.connect(self.model_dialog.close)
        model_dialog_actions.addWidget(self.close_model_details_button)
        model_dialog_layout.addLayout(model_dialog_actions)

        self.report_text = QPlainTextEdit()
        self.report_text.setReadOnly(True)

        self.last_run_dialog = QDialog(self)
        self.last_run_dialog.setObjectName("workflowDetailsDialog")
        self.last_run_dialog.setWindowTitle("Last Run")
        self.last_run_dialog.resize(1000, 700)
        last_run_dialog_layout = QVBoxLayout(self.last_run_dialog)
        self.history_page = QWidget(self.last_run_dialog)
        history_layout = QVBoxLayout(self.history_page)
        history_layout.setContentsMargins(8, 8, 8, 8)
        history_intro = QLabel(
            "Latest correction execution. Profile-managed operations are also archived "
            "under the application runtime directory."
        )
        history_intro.setObjectName("workspaceIntro")
        history_intro.setWordWrap(True)
        history_layout.addWidget(history_intro)
        history_layout.addWidget(QLabel("Iteration summary"))
        history_layout.addWidget(self.correction_table, 1)
        history_layout.addWidget(QLabel("Latest report"))
        history_layout.addWidget(self.report_text, 2)
        last_run_dialog_layout.addWidget(self.history_page, 1)
        last_run_dialog_actions = QHBoxLayout()
        last_run_dialog_actions.addStretch(1)
        self.close_last_run_button = QPushButton("Close")
        self.close_last_run_button.clicked.connect(self.last_run_dialog.close)
        last_run_dialog_actions.addWidget(self.close_last_run_button)
        last_run_dialog_layout.addLayout(last_run_dialog_actions)

        self.iteration_history_dialog = QDialog(self)
        self.iteration_history_dialog.setObjectName("workflowDetailsDialog")
        self.iteration_history_dialog.setWindowTitle("Iteration History")
        self.iteration_history_dialog.resize(1050, 760)
        iteration_history_layout = QVBoxLayout(self.iteration_history_dialog)
        iteration_history_intro = QLabel(
            "Review every attempted generation from this GUI session. The main "
            "dispersion plot continues to show only the initial and latest/final "
            "measurement."
        )
        iteration_history_intro.setObjectName("workspaceIntro")
        iteration_history_intro.setWordWrap(True)
        iteration_history_layout.addWidget(iteration_history_intro)
        iteration_history_controls = QHBoxLayout()
        iteration_history_controls.addWidget(QLabel("Correction run"))
        self.iteration_history_run_combo = QComboBox()
        self.iteration_history_run_combo.setMinimumWidth(210)
        self.iteration_history_run_combo.currentIndexChanged.connect(
            self._iteration_history_run_changed
        )
        iteration_history_controls.addWidget(self.iteration_history_run_combo)
        iteration_history_controls.addWidget(QLabel("Displayed state"))
        self.iteration_history_generation_combo = QComboBox()
        self.iteration_history_generation_combo.setMinimumWidth(250)
        self.iteration_history_generation_combo.currentIndexChanged.connect(
            self._refresh_iteration_history_view
        )
        iteration_history_controls.addWidget(
            self.iteration_history_generation_combo
        )
        self.iteration_history_plane_label = QLabel("Plane")
        self.iteration_history_plane_combo = QComboBox()
        self.iteration_history_plane_combo.addItem("ηx", "x")
        self.iteration_history_plane_combo.addItem("ηy", "y")
        self.iteration_history_plane_combo.currentIndexChanged.connect(
            self._refresh_iteration_history_view
        )
        self.iteration_history_plane_label.hide()
        self.iteration_history_plane_combo.hide()
        iteration_history_controls.addWidget(
            self.iteration_history_plane_label
        )
        iteration_history_controls.addWidget(
            self.iteration_history_plane_combo
        )
        self.iteration_history_overlay_checkbox = QCheckBox(
            "Show accepted generations"
        )
        self.iteration_history_overlay_checkbox.setToolTip(
            "Add accepted intermediate generations as thin muted curves."
        )
        self.iteration_history_overlay_checkbox.toggled.connect(
            self._refresh_iteration_history_view
        )
        iteration_history_controls.addWidget(
            self.iteration_history_overlay_checkbox
        )
        iteration_history_controls.addStretch(1)
        iteration_history_layout.addLayout(iteration_history_controls)
        self.iteration_history_status_label = QLabel()
        self.iteration_history_status_label.setObjectName("workflowSummary")
        self.iteration_history_status_label.setWordWrap(True)
        iteration_history_layout.addWidget(
            self.iteration_history_status_label
        )
        self.iteration_history_curve = DispersionCurveWidget(
            self.config.section.model_entrance,
            self.config.measurement.planes[0],
        )
        self.iteration_history_curve.setMinimumHeight(330)
        iteration_history_layout.addWidget(self.iteration_history_curve, 2)
        iteration_history_layout.addWidget(
            QLabel("Quadrupole / correction-knob state")
        )
        self.iteration_history_knob_table = self._table(
            ["Quadrupole / Knob", "Before", "Trial / Final", "Delta"]
        )
        iteration_history_layout.addWidget(
            self.iteration_history_knob_table,
            1,
        )
        iteration_history_actions = QHBoxLayout()
        iteration_history_actions.addStretch(1)
        self.close_iteration_history_button = QPushButton("Close")
        self.close_iteration_history_button.clicked.connect(
            self.iteration_history_dialog.close
        )
        iteration_history_actions.addWidget(
            self.close_iteration_history_button
        )
        iteration_history_layout.addLayout(iteration_history_actions)

        self.dispersion_overview = QFrame()
        self.dispersion_overview.setObjectName("dispersionOverviewCard")
        overview_layout = QVBoxLayout(self.dispersion_overview)
        overview_layout.setContentsMargins(12, 10, 12, 12)
        overview_layout.setSpacing(4)
        self.overview_title_label = QLabel("Dispersion Overview")
        self.overview_title_label.setObjectName("cardTitle")
        self.measurement_header_label = QLabel("Measurement")
        self.measurement_header_label.setObjectName("overviewGroupLabel")
        self.display_plane_combo = QComboBox()
        self.display_plane_combo.setObjectName("displayPlaneCombo")
        self.display_plane_combo.addItem("Horizontal ηx", "x")
        self.display_plane_combo.addItem("Vertical ηy", "y")
        self.display_plane_combo.setToolTip(
            "Choose which plane from the same two-plane energy scan is "
            "displayed in the overview."
        )
        self.display_plane_combo.currentIndexChanged.connect(
            self._display_plane_changed
        )
        self.display_plane_combo.hide()
        self.plot_state_label = QLabel("No measured data")
        self.plot_state_label.setObjectName("overviewStateLabel")
        self.plot_state_label.setSizePolicy(
            QSizePolicy.Ignored,
            QSizePolicy.Preferred,
        )
        self.plot_state_label.hide()
        self.show_design_model_checkbox = QCheckBox("Design model")
        self.show_design_model_checkbox.setProperty("role", "modelOverlayToggle")
        self.show_design_model_checkbox.setChecked(False)
        self.show_design_model_checkbox.setToolTip(
            "Calculate and show the read-only design-lattice model. "
            "No dispersion measurement is required."
        )
        self.show_design_model_checkbox.toggled.connect(
            self._model_visibility_changed
        )
        self.show_snapshot_model_checkbox = QCheckBox("Current K1 model")
        self.show_snapshot_model_checkbox.setProperty("role", "modelOverlayToggle")
        self.show_snapshot_model_checkbox.setChecked(False)
        self.show_snapshot_model_checkbox.setEnabled(False)
        self.show_snapshot_model_checkbox.setToolTip(
            "Read the configured quadrupole K1 snapshot and calculate its model curve. "
            "No dispersion measurement or machine write is required."
        )
        self.show_snapshot_model_checkbox.toggled.connect(
            self._model_visibility_changed
        )
        self.refresh_snapshot_button = QPushButton("Refresh")
        self.refresh_snapshot_button.setObjectName("refreshSnapshotButton")
        self.refresh_snapshot_button.setToolTip(
            "Read the current quadrupole K1 PVs again and recalculate the model curve."
        )
        self.refresh_snapshot_button.clicked.connect(
            self._refresh_current_snapshot
        )
        self.model_details_button = QPushButton("Model Details…")
        self.model_details_button.setObjectName("modelDetailsButton")
        self.model_details_button.clicked.connect(self._show_model_details)
        self.overview_controls = OverviewControls(
            self.overview_title_label,
            self.measurement_header_label,
            self.plot_state_label,
            self.display_plane_combo,
            self.show_design_model_checkbox,
            self.show_snapshot_model_checkbox,
            self.refresh_snapshot_button,
            self.model_details_button,
        )
        overview_layout.addWidget(self.overview_controls)
        self.dispersion_curve = DispersionCurveWidget(
            self.config.section.model_entrance,
            self.config.measurement.planes[0],
        )
        overview_layout.addWidget(self.dispersion_curve, 1)

        self.workspace_splitter = QSplitter(Qt.Vertical)
        self.workspace_splitter.setChildrenCollapsible(False)
        self.workspace_splitter.addWidget(self.dispersion_overview)
        self.workspace_splitter.addWidget(self.online_page)
        self.workspace_splitter.setStretchFactor(0, 1)
        self.workspace_splitter.setStretchFactor(1, 0)
        self.workspace_splitter.setSizes([560, 220])
        layout.addWidget(self.workspace_splitter, 1)
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

    def _show_workflow_detail(self, page: QWidget) -> None:
        if page is self.model_page:
            self._show_model_details()
        elif page is self.response_page:
            self._show_response_details()
        elif page is self.correction_page:
            self._show_recommendation_details()
        elif page is self.history_page:
            self._show_iteration_history()

    def _show_response_details(self) -> None:
        if self.latest_response is None and self.latest_joint_response is None:
            return
        self.response_dialog.setStyleSheet(build_stylesheet(self.theme_name))
        self.response_dialog.show()
        self.response_dialog.raise_()
        self.response_dialog.activateWindow()

    def _show_recommendation_details(self) -> None:
        self.recommendation_dialog.setStyleSheet(
            build_stylesheet(self.theme_name)
        )
        self.recommendation_dialog.show()
        self.recommendation_dialog.raise_()
        self.recommendation_dialog.activateWindow()

    def _show_last_run(self) -> None:
        if self.correction_table.rowCount() == 0 and not self.report_text.toPlainText():
            return
        self.last_run_dialog.setStyleSheet(build_stylesheet(self.theme_name))
        self.last_run_dialog.show()
        self.last_run_dialog.raise_()
        self.last_run_dialog.activateWindow()

    def _record_correction_run(
        self,
        task: str,
        result: CorrectionResult | JointCorrectionResult,
    ) -> None:
        joint = isinstance(result, JointCorrectionResult)
        run_kind = "Automatic" if task in {"run", "joint-run"} else "Manual"
        if joint:
            run_kind = f"Joint {run_kind}"
        sequence = (
            sum(entry.task == task for entry in self.correction_session_runs)
            + 1
        )
        self.correction_session_runs.append(
            CorrectionSessionRun(
                label=f"{run_kind} {sequence}",
                task=task,
                result=result,
                requested_generations=(
                    int(self.config.solver.max_iter)
                    if task in {"run", "joint-run"}
                    else None
                ),
            )
        )
        self._refresh_iteration_history_runs(
            selected=len(self.correction_session_runs) - 1
        )

    def _build_correction_restore_request(
        self,
        result: CorrectionResult | JointCorrectionResult,
        *,
        run_label: str,
    ) -> CorrectionRestoreRequest | None:
        accepted_steps = [
            step
            for step in result.steps
            if (
                step.accepted
                and step.device_values_before
                and step.device_values_trial
            )
        ]
        if not result.success or not accepted_steps:
            return None
        targets = {
            str(name): float(value)
            for name, value in accepted_steps[0].device_values_before.items()
        }
        baseline = {
            str(name): float(value)
            for name, value in accepted_steps[-1].device_values_trial.items()
        }
        if not targets or set(targets) != set(baseline):
            return None
        limits: dict[str, float] = {}
        for knob in self.config.runtime_knobs:
            for device, weight in knob.devices.items():
                limits[device] = (
                    limits.get(device, 0.0)
                    + abs(float(weight)) * float(knob.limit)
                )
        if set(targets) != set(limits):
            return None
        return CorrectionRestoreRequest(
            run_label=run_label,
            baseline_values=baseline,
            target_values=targets,
            max_changes={name: limits[name] for name in targets},
        )

    def _restore_initial_correction_state(self) -> None:
        request = self.correction_restore_request
        if request is None:
            return
        unit = self._knob_control_unit()
        lines = [
            f"Restore the quadrupole state saved before {request.run_label}?",
            "",
        ]
        for name in request.target_values:
            baseline = request.baseline_values[name]
            target = request.target_values[name]
            lines.append(
                f"{name}: expected current {baseline:.8g} -> "
                f"{target:.8g} {unit}"
            )
        lines.extend(
            [
                "",
                "Connections and the expected current values will be checked again "
                "before writing. If safety checks fail, the state present before "
                "this restore attempt is written back.",
                "",
                "Existing measurements and recommendations will be discarded.",
            ]
        )
        answer = QMessageBox.question(
            self,
            "Restore Initial Correction State",
            "\n".join(lines),
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return
        self._start_task(
            "restore-correction",
            restore_request=request,
        )

    def _refresh_iteration_history_runs(
        self,
        *,
        selected: int | None = None,
    ) -> None:
        current = (
            self.iteration_history_run_combo.currentData()
            if selected is None
            else selected
        )
        self.iteration_history_run_combo.blockSignals(True)
        self.iteration_history_run_combo.clear()
        for index, entry in enumerate(self.correction_session_runs):
            display = entry.label
            if entry.requested_generations is not None:
                executed = max(
                    (step.iteration for step in entry.result.steps),
                    default=0,
                )
                display += (
                    f" · {executed}/{entry.requested_generations} generations"
                )
            self.iteration_history_run_combo.addItem(display, index)
        index = self.iteration_history_run_combo.findData(current)
        if index < 0 and self.iteration_history_run_combo.count():
            index = self.iteration_history_run_combo.count() - 1
        self.iteration_history_run_combo.setCurrentIndex(index)
        self.iteration_history_run_combo.blockSignals(False)
        self._iteration_history_run_changed(index)

    def _selected_correction_run(self) -> CorrectionSessionRun | None:
        index = self.iteration_history_run_combo.currentData()
        if not isinstance(index, int):
            return None
        if not 0 <= index < len(self.correction_session_runs):
            return None
        return self.correction_session_runs[index]

    def _iteration_history_run_changed(
        self,
        _index: int | None = None,
    ) -> None:
        entry = self._selected_correction_run()
        joint = (
            entry is not None
            and isinstance(entry.result, JointCorrectionResult)
        )
        self.iteration_history_plane_label.setVisible(joint)
        self.iteration_history_plane_combo.setVisible(joint)
        self.iteration_history_generation_combo.blockSignals(True)
        self.iteration_history_generation_combo.clear()
        if entry is not None:
            self.iteration_history_generation_combo.addItem(
                "Initial measurement",
                "initial",
            )
            for index, step in enumerate(entry.result.steps):
                if step.accepted:
                    state = "accepted"
                elif step.restored:
                    state = "restored"
                else:
                    state = "stopped"
                self.iteration_history_generation_combo.addItem(
                    f"Generation {step.iteration} · {state}",
                    f"step:{index}",
                )
            executed = max(
                (step.iteration for step in entry.result.steps),
                default=0,
            )
            requested = entry.requested_generations
            if requested is not None and executed < requested:
                self.iteration_history_generation_combo.addItem(
                    (
                        f"Stopped early · {executed}/{requested} generations "
                        "executed"
                    ),
                    "early-stop",
                )
            self.iteration_history_generation_combo.addItem(
                "Final verification",
                "final",
            )
            self.iteration_history_generation_combo.setCurrentIndex(
                self.iteration_history_generation_combo.count() - 1
            )
        self.iteration_history_generation_combo.blockSignals(False)
        self._refresh_iteration_history_view()

    def _refresh_iteration_history_view(
        self,
        _value: object | None = None,
    ) -> None:
        entry = self._selected_correction_run()
        if entry is None:
            self.iteration_history_curve.set_measurement(None)
            self.iteration_history_status_label.setText(
                "No correction run is available."
            )
            self.iteration_history_knob_table.setRowCount(0)
            return

        result = entry.result
        joint = isinstance(result, JointCorrectionResult)
        plane = str(self.iteration_history_plane_combo.currentData() or "x")

        def displayed_measurement(
            measurement: DispersionMeasurement
            | MultiPlaneDispersionMeasurement,
        ) -> DispersionMeasurement:
            if isinstance(measurement, MultiPlaneDispersionMeasurement):
                return measurement.for_plane(plane)
            return measurement

        selection = str(
            self.iteration_history_generation_combo.currentData()
            or "final"
        )
        selected_step_index: int | None = None
        if selection.startswith("step:"):
            try:
                selected_step_index = int(selection.split(":", 1)[1])
            except ValueError:
                selected_step_index = None

        if selection == "initial":
            measurement = displayed_measurement(result.initial)
            reference = None
            label = "Initial measured"
            before_knobs = (
                result.steps[0].response.baseline_device_values
                if joint and result.steps
                else result.initial_knobs
                if isinstance(result, CorrectionResult)
                else {}
            )
            target_knobs = None
            status = (
                f"{entry.label} · initial normalized RMS "
                f"{result.normalized_rms_before:.6g}"
                if joint
                else (
                    f"{entry.label} · initial RMS "
                    f"{result.initial.rms_mm:.6g} mm"
                )
            )
        elif (
            selected_step_index is not None
            and 0 <= selected_step_index < len(result.steps)
        ):
            step = result.steps[selected_step_index]
            measurement = displayed_measurement(
                step.measured_after
                if joint
                else (
                    step.measurement_after
                    or step.measurement_before
                    or result.initial
                )
            )
            reference = (
                displayed_measurement(result.initial)
            )
            label = (
                f"Generation {step.iteration} measured"
                if joint or step.measurement_after is not None
                else f"Generation {step.iteration} baseline"
            )
            before_knobs = (
                step.device_values_before
                or (
                    {}
                    if joint
                    else step.knobs_before or result.initial_knobs
                )
            )
            target_knobs = (
                step.device_values_trial
                if joint
                else step.device_values_trial or step.knobs_trial
            )
            if step.accepted:
                state = "accepted"
            elif step.restored:
                state = "rejected and restored"
            else:
                state = "stopped before a valid trial"
            if joint:
                status = (
                    f"{entry.label} · generation {step.iteration} {state} · "
                    f"normalized RMS "
                    f"{step.response.normalized_rms_before:.6g} → "
                    f"{step.normalized_rms_after:.6g} · {step.reason}"
                )
            else:
                after = (
                    ""
                    if step.rms_after_mm is None
                    else f" → {step.rms_after_mm:.6g} mm"
                )
                status = (
                    f"{entry.label} · generation {step.iteration} {state} · "
                    f"RMS {step.rms_before_mm:.6g} mm{after} · {step.reason}"
                )
        elif selection == "early-stop":
            measurement = displayed_measurement(result.final)
            reference = (
                displayed_measurement(result.initial)
            )
            label = "Stopped early · final verified"
            before_knobs = (
                result.steps[0].device_values_before
                if joint and result.steps
                else result.initial_knobs
            )
            target_knobs = (
                result.steps[-1].device_values_trial
                if joint and result.steps
                else result.final_knobs
            )
            executed = max(
                (step.iteration for step in result.steps),
                default=0,
            )
            requested = entry.requested_generations or executed
            stop_reason = (
                result.steps[-1].reason
                if result.steps
                else result.reason
            )
            status = (
                f"{entry.label} · stopped after {executed}/{requested} "
                f"generations; later generations were not run · {stop_reason}"
            )
        else:
            measurement = displayed_measurement(result.final)
            reference = (
                displayed_measurement(result.initial)
            )
            label = "Final verified"
            before_knobs = (
                result.steps[0].device_values_before
                if joint and result.steps
                else result.initial_knobs
            )
            target_knobs = (
                result.steps[-1].device_values_trial
                if joint and result.steps
                else result.final_knobs
            )
            state = "accepted" if result.success else "not accepted"
            status = (
                (
                    f"{entry.label} · final result {state} · normalized RMS "
                    f"{result.normalized_rms_before:.6g} → "
                    f"{result.normalized_rms_after:.6g} · {result.reason}"
                )
                if joint
                else (
                    f"{entry.label} · final result {state} · RMS "
                    f"{result.initial.rms_mm:.6g} → "
                    f"{result.final.rms_mm:.6g} mm · {result.reason}"
                )
            )

        overlays: list[DispersionPlotDataset] = []
        if self.iteration_history_overlay_checkbox.isChecked():
            for index, step in enumerate(result.steps):
                if (
                    step.accepted
                    and (
                        joint
                        or step.measurement_after is not None
                    )
                    and index != selected_step_index
                ):
                    overlay_measurement = (
                        displayed_measurement(step.measured_after)
                        if joint
                        else step.measurement_after
                    )
                    overlays.append(
                        self._plot_dataset_from_measurement(
                            overlay_measurement,
                            f"Generation {step.iteration}",
                        )
                    )
        self.iteration_history_curve.set_measurement(
            self._plot_dataset_from_measurement(measurement, label),
            (
                None
                if reference is None
                else self._plot_dataset_from_measurement(
                    reference,
                    "Initial measured",
                )
            ),
            tuple(overlays),
        )
        self.iteration_history_status_label.setText(status)
        self._fill_iteration_history_knobs(
            before_knobs,
            target_knobs,
        )

    def _fill_iteration_history_knobs(
        self,
        before: dict[str, float],
        target: dict[str, float] | None,
    ) -> None:
        names = list(before)
        if target is not None:
            names.extend(name for name in target if name not in before)
        self.iteration_history_knob_table.setRowCount(len(names))
        for row, name in enumerate(names):
            before_value = before.get(name)
            target_value = None if target is None else target.get(name)
            self.iteration_history_knob_table.setItem(
                row,
                0,
                QTableWidgetItem(name),
            )
            self.iteration_history_knob_table.setItem(
                row,
                1,
                QTableWidgetItem(
                    "" if before_value is None else f"{before_value:.8g}"
                ),
            )
            self.iteration_history_knob_table.setItem(
                row,
                2,
                QTableWidgetItem(
                    "" if target_value is None else f"{target_value:.8g}"
                ),
            )
            delta = (
                ""
                if before_value is None or target_value is None
                else f"{target_value - before_value:+.8g}"
            )
            self.iteration_history_knob_table.setItem(
                row,
                3,
                QTableWidgetItem(delta),
            )
        self.iteration_history_knob_table.resizeColumnsToContents()

    def _show_iteration_history(self) -> None:
        if not self.correction_session_runs:
            return
        self._refresh_iteration_history_runs()
        self.iteration_history_dialog.setStyleSheet(
            build_stylesheet(self.theme_name)
        )
        self.iteration_history_dialog.show()
        self.iteration_history_dialog.raise_()
        self.iteration_history_dialog.activateWindow()

    def _open_offline_demo(self) -> None:
        existing = self._offline_demo_window
        if existing is not None:
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return
        demo = MainWindow(
            config=default_offline_config(),
            app_context=None,
            offline_demo=True,
        )
        demo.setAttribute(Qt.WA_DeleteOnClose, True)
        demo.destroyed.connect(self._offline_demo_closed)
        self._offline_demo_window = demo
        demo.show()
        demo.raise_()
        demo.activateWindow()

    def _offline_demo_closed(self, _object=None) -> None:
        self._offline_demo_window = None

    def _show_model_details(self) -> None:
        self.model_dialog.setStyleSheet(build_stylesheet(self.theme_name))
        self.model_dialog.show()
        self.model_dialog.raise_()
        self.model_dialog.activateWindow()

    def _add_form_row(self, form: QFormLayout, label_text: str, widget) -> QLabel:
        label = QLabel(label_text)
        label.setProperty("role", "field")
        label.setFixedWidth(124)
        label.setMinimumHeight(34)
        label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.addRow(label, widget)
        return label

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
        self.correction_restore_request = None
        if hasattr(self, "dispersion_curve"):
            self.dispersion_curve.set_model_entrance(
                self.config.section.model_entrance
            )
            self.dispersion_curve.set_plane(
                self.config.measurement.planes[0]
            )
            self.iteration_history_curve.set_model_entrance(
                self.config.section.model_entrance
            )
            self.iteration_history_curve.set_plane(
                self.config.measurement.planes[0]
            )
            correction_bpms = self._configured_correction_bpms()
            self.dispersion_curve.set_correction_bpms(correction_bpms)
            self.iteration_history_curve.set_correction_bpms(correction_bpms)
            self._last_unmapped_plot_bpms = ()
        self._invalidate_staged_results(
            "Configuration loaded. Measure the Q response before calculating a recommendation."
        )
        self.last_live_preflight = None
        self._loading_widgets = True
        try:
            self.selected_knobs = tuple(self.config.runtime_knobs)
            if self.app_context is not None:
                self.available_bpms = selectable_profile_bpms(
                    self.app_context,
                    self.config.measurement.plane,
                )
            plane_name = {
                "x": "horizontal",
                "y": "vertical",
                "xy": "two-plane",
            }[self.config.measurement.plane]
            self.measure_title.setText(
                f"Measured {plane_name} effective dispersion"
            )
            multi_plane = self.config.measurement.plane == "xy"
            self.display_plane_combo.setVisible(multi_plane)
            self.display_plane_combo.setCurrentIndex(0)
            section_index = self.section_combo.findData(self.config.section.id)
            if section_index >= 0:
                self.section_combo.setCurrentIndex(section_index)
            self._update_correction_bpm_summary()
            self.monitor_bpm_edit.setText(
                ", ".join(self._display_monitor_bpms())
            )
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
            self._update_energy_step_summary()
            model_only = self.config.section.model_only
            diagnostic_only = self.config.section.diagnostic_only
            bpm_configuration_available = not model_only and not diagnostic_only
            knob_selection_fixed = model_only or diagnostic_only
            measurement_only = (
                diagnostic_only
                or (
                    self.config.measurement.plane == "xy"
                    and not self._joint_correction_enabled()
                )
            )
            self.bpm_select_button.setVisible(bpm_configuration_available)
            self.knob_select_button.setVisible(not knob_selection_fixed)
            self.correction_step_card.setVisible(not measurement_only)
            self.verification_samples_field_label.setVisible(
                not measurement_only
            )
            self.final_samples_spin.setVisible(not measurement_only)
            self.workflow_title_label.setText(
                (
                    "Diagnostic Measurement"
                    if diagnostic_only
                    else "Two-Plane Measurement"
                )
                if measurement_only
                else "Correction Workflow"
            )
            self.energy_step_field_label.setVisible(not model_only)
            self.delta_spin.setVisible(not model_only)
        finally:
            self._loading_widgets = False
        self._refresh_operation_plan()
        self._update_calibration_controls()
        self._update_static_safety_status()
        self._show_workflow_detail(self.online_page)
        self._refresh_status("Config loaded")

    def _update_correction_bpm_summary(self) -> None:
        if self._joint_correction_enabled():
            targets = self.config.section.joint_response_analysis.targets
            self.bpm_edit.setText(
                "; ".join(
                    f"{bpm} · ηx/ηy"
                    for bpm in dict.fromkeys(
                        target.bpm for target in targets
                    )
                )
            )
            self.bpm_edit.setToolTip(
                "\n".join(
                    f"{target.name}: target {target.target_mm:g} mm, "
                    f"tolerance {target.tolerance_mm:g} mm"
                    for target in targets
                )
            )
        else:
            self.bpm_edit.setText(
                "; ".join(
                    f"{bpm}={target:g}"
                    for bpm, target in zip(
                        self.config.target_bpms,
                        self.config.section.target_dispersion_mm,
                    )
                )
            )
            plane = self.config.measurement.planes[0]
            self.bpm_edit.setToolTip(
                "\n".join(
                    f"{bpm} η{plane}: target {target:g} mm"
                    for bpm, target in zip(
                        self.config.target_bpms,
                        self.config.section.target_dispersion_mm,
                    )
                )
            )
        if not self.bpm_edit.text():
            self.bpm_edit.setText("None — diagnostics only")
        self.bpm_edit.setCursorPosition(0)

    def _configured_correction_bpms(self) -> tuple[str, ...]:
        if self._joint_correction_enabled():
            return tuple(
                dict.fromkeys(
                    target.bpm
                    for target in self.config.section.joint_response_analysis.targets
                )
            )
        return self.config.target_bpms

    def _display_monitor_bpms(self) -> tuple[str, ...]:
        if not self._joint_correction_enabled():
            return self.config.monitor_bpms
        correction_bpms = {
            target.bpm
            for target in self.config.section.joint_response_analysis.targets
        }
        return tuple(
            bpm
            for bpm in self.config.monitor_bpms
            if bpm not in correction_bpms
        )

    def _update_knob_summary(self) -> None:
        summaries = []
        if self.config.section.diagnostic_only:
            self.knob_edit.setText("None — measurement only")
            self.knob_edit.setCursorPosition(0)
            self.knob_edit.setToolTip(
                "Diagnostic sections do not scan or write quadrupoles."
            )
            return
        if self.config.section.model_only:
            tooltip_lines = [
                "Design-reference quadrupoles. Model comparison does not use scan, "
                "step, or backend execution limits."
            ]
            for knob in self.selected_knobs:
                devices = tuple(knob.devices)
                summaries.append("/".join(devices))
                tooltip_lines.append(
                    f"{knob.name}: restore device K1 values to lattice design"
                )
            self.knob_edit.setText("; ".join(summaries))
            self.knob_edit.setCursorPosition(0)
            self.knob_edit.setToolTip("\n".join(tooltip_lines))
            return
        tooltip_lines = ["Symmetric device weights: +1 / +1"]
        unit = self._knob_control_unit()
        unit_suffix = f" {unit}" if unit else ""
        step_fraction = float(self.max_step_pct_spin.value()) / 100.0
        display_knobs = self.selected_knobs
        for knob in display_knobs:
            devices = tuple(knob.devices)
            summaries.append("/".join(devices))
            step_limit = knob.limit * step_fraction
            tooltip_lines.append(
                f"{knob.name}: measure step ±{knob.scan_step:g}{unit_suffix}, "
                f"total Δ limit ±{knob.limit:g}{unit_suffix}, "
                f"correction step limit ±{step_limit:g}{unit_suffix}"
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
        self.configured_energy_calibration = dict(config.energy_knob.calibration)
        self.session_energy_calibration_source = None
        self.correction_restore_request = None
        self.pending_model_source = None
        self.current_snapshot_time = None
        for checkbox in (
            self.show_design_model_checkbox,
            self.show_snapshot_model_checkbox,
        ):
            checkbox.blockSignals(True)
            checkbox.setChecked(False)
            checkbox.blockSignals(False)
        self.selected_knobs = tuple(config.runtime_knobs)
        self.knob_hard_limits = tuple(
            knob.limit for knob in config.runtime_knobs
        )
        self._invalidate_staged_results(
            "Section changed. Previous measurements and recommendations were discarded."
        )
        self.dispersion_curve.set_result(None)
        self.live_plot_measurement = None
        self.reference_plot_measurement = None
        self.live_plane_measurements = {}
        self.latest_plane_measurements = {}
        self.dispersion_curve.set_measurement(None)
        self.model_info.clear()
        self.model_info.setVisible(False)
        self.model_empty_label.setVisible(True)
        self.model_table.setRowCount(0)
        self.model_table.setVisible(False)
        self.model_measure_table.setRowCount(0)
        self.model_measure_table.setVisible(False)
        self.response_info.clear()
        self.response_table.setRowCount(0)
        self.measure_table.setRowCount(0)
        self._configure_profile_mode()
        self._load_config_to_widgets()
        self._set_running(False, "")

    def _invalidate_staged_results(self, reason: str) -> None:
        self.correction_mode = None
        self.latest_measurement = None
        self.latest_measurement_time = None
        self.latest_plane_measurements = {}
        self.latest_response = None
        self.latest_joint_response = None
        self.correction_recommendation = None
        self.live_plot_measurement = None
        self.reference_plot_measurement = None
        self.live_plane_measurements = {}
        if hasattr(self, "dispersion_curve"):
            self._refresh_plot_measurement()
        if not hasattr(self, "correction_state_label"):
            return
        self.correction_state_label.setText(reason)
        self.recommendation_summary_label.setText(
            "No recommendation has been calculated."
        )
        self.recommendation_prediction_table.setRowCount(0)
        self.recommendation_table.setRowCount(0)
        self.measure_table.setRowCount(0)
        self.response_table.setRowCount(0)
        self.response_info.clear()

    def _workflow_input_changed(self, _value=None) -> None:
        if self._loading_widgets:
            return
        self._selection_changed()

    def _correction_setting_changed(self, _value=None) -> None:
        if self._loading_widgets:
            return
        self.correction_recommendation = None
        self.correction_state_label.setText(
            "Correction limits changed. The previous recommendation was discarded, "
            "but the dispersion measurement remains valid."
        )
        self.recommendation_summary_label.setText(
            "No recommendation has been calculated."
        )
        self.recommendation_prediction_table.setRowCount(0)
        self.recommendation_table.setRowCount(0)
        self._sync_nonmeasurement_settings()

    def _automatic_setting_changed(self, _value=None) -> None:
        if self._loading_widgets:
            return
        self._sync_nonmeasurement_settings()

    def _sync_nonmeasurement_settings(self) -> None:
        try:
            self.config = self._config_from_widgets()
            self.monitor_bpm_edit.setText(
                ", ".join(self._display_monitor_bpms())
            )
            self.operation_plan = build_operation_plan(self.config)
        except Exception as exc:
            self.operation_plan = None
            self._append_log(f"Operation plan validation failed: {exc}")
            self.status_strip.set_value("READINESS", "NOT READY", "danger")
            return
        self._refresh_operation_plan()
        self._update_static_safety_status()
        self._set_running(False, "")

    def _model_source_changed(self, _index: int | None = None) -> None:
        if not hasattr(self, "dispersion_curve"):
            return
        self.dispersion_curve.set_result(None)
        self.model_info.clear()
        self.model_info.setVisible(False)
        self.model_table.setRowCount(0)
        self.model_table.setVisible(False)
        self.model_measure_table.setRowCount(0)
        self.model_measure_table.setVisible(False)
        self.show_design_model_checkbox.setChecked(False)
        self.show_snapshot_model_checkbox.setChecked(False)
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

    def _configure_correction_bpms(self) -> None:
        dialog, table, buttons = self._build_correction_bpm_dialog()

        def accept_configuration() -> None:
            try:
                self._apply_correction_bpm_table(table)
            except Exception as exc:
                QMessageBox.warning(dialog, "Correction BPMs", str(exc))
                return
            dialog.accept()

        buttons.accepted.connect(accept_configuration)
        buttons.rejected.connect(dialog.reject)
        dialog.exec_()

    def _build_correction_bpm_dialog(
        self,
    ) -> tuple[QDialog, QTableWidget, QDialogButtonBox]:
        joint = self._joint_correction_enabled()
        dialog = QDialog(self)
        dialog.setObjectName("correctionBpmDialog")
        dialog.setStyleSheet(build_stylesheet(self.theme_name))
        dialog.setWindowTitle("Set Correction BPMs")
        dialog.resize(940 if joint else 650, 560)
        layout = QVBoxLayout(dialog)
        prompt = QLabel(
            (
                "Choose the BPMs used by the joint solver, then set ηx/ηy "
                "targets and normalization tolerances. Unselected BPMs remain "
                "measurement-only monitors."
            )
            if joint
            else (
                "Choose correction BPMs and set their target dispersion. "
                "Unselected BPMs already in this section remain "
                "measurement-only monitors."
            )
        )
        prompt.setObjectName("correctionBpmPrompt")
        prompt.setWordWrap(True)
        layout.addWidget(prompt)

        candidates = self._correction_bpm_candidates()
        if joint:
            target_map = {
                (target.bpm, target.plane): target
                for target in self.config.section.joint_response_analysis.targets
            }
            selected = {bpm for bpm, _plane in target_map}
            table = QTableWidget(len(candidates), 6)
            table.setHorizontalHeaderLabels(
                [
                    "Use",
                    "BPM",
                    "ηx Target",
                    "ηx Tol.",
                    "ηy Target",
                    "ηy Tol.",
                ]
            )
            for row, bpm in enumerate(candidates):
                table.setCellWidget(
                    row,
                    0,
                    self._bpm_use_checkbox(bpm in selected),
                )
                table.setItem(row, 1, QTableWidgetItem(bpm))
                x_target = target_map.get((bpm, "x"))
                y_target = target_map.get((bpm, "y"))
                table.setCellWidget(
                    row,
                    2,
                    self._dispersion_target_spin(
                        0.0 if x_target is None else x_target.target_mm
                    ),
                )
                table.setCellWidget(
                    row,
                    3,
                    self._dispersion_tolerance_spin(
                        1.0 if x_target is None else x_target.tolerance_mm
                    ),
                )
                table.setCellWidget(
                    row,
                    4,
                    self._dispersion_target_spin(
                        0.0 if y_target is None else y_target.target_mm
                    ),
                )
                table.setCellWidget(
                    row,
                    5,
                    self._dispersion_tolerance_spin(
                        1.0 if y_target is None else y_target.tolerance_mm
                    ),
                )
        else:
            plane = self.config.measurement.planes[0]
            target_by_bpm = dict(
                zip(
                    self.config.target_bpms,
                    self.config.section.target_dispersion_mm,
                )
            )
            table = QTableWidget(len(candidates), 4)
            table.setHorizontalHeaderLabels(
                ["Use", "BPM", "Plane", "Target (mm)"]
            )
            for row, bpm in enumerate(candidates):
                table.setCellWidget(
                    row,
                    0,
                    self._bpm_use_checkbox(bpm in target_by_bpm),
                )
                table.setItem(row, 1, QTableWidgetItem(bpm))
                table.setItem(row, 2, QTableWidgetItem(f"η{plane}"))
                table.setCellWidget(
                    row,
                    3,
                    self._dispersion_target_spin(
                        target_by_bpm.get(bpm, 0.0)
                    ),
                )
        table.setObjectName("correctionBpmTable")
        table.verticalHeader().setVisible(False)
        table.setSelectionMode(QAbstractItemView.NoSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        if joint:
            for column in range(2, 6):
                header.setSectionResizeMode(column, QHeaderView.Stretch)
        else:
            header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
            header.setSectionResizeMode(3, QHeaderView.Stretch)
        for row in range(table.rowCount()):
            table.setRowHeight(row, 42)
        layout.addWidget(table, 1)

        note = QLabel(
            "Changes affect only this GUI session. Switching section restores "
            "the machine-profile defaults."
        )
        note.setObjectName("workspaceIntro")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        save_button = buttons.button(QDialogButtonBox.Save)
        if save_button is not None:
            save_button.setText("Use for Current Session")
        layout.addWidget(buttons)
        return dialog, table, buttons

    def _correction_bpm_candidates(self) -> tuple[str, ...]:
        if self._joint_correction_enabled():
            return self.config.measurement_bpms
        return tuple(
            dict.fromkeys(
                (
                    *self.config.measurement_bpms,
                    *self.available_bpms,
                )
            )
        )

    def _apply_correction_bpm_table(
        self,
        table: QTableWidget,
    ) -> None:
        joint = self._joint_correction_enabled()
        candidates = self._correction_bpm_candidates()
        selected_bpms = tuple(
            bpm
            for row, bpm in enumerate(candidates)
            if self._table_checkbox_checked(table, row, 0)
        )
        if not selected_bpms:
            raise ValueError("Select at least one correction BPM.")

        previous_measurement_bpms = self.config.measurement_bpms
        target_map: dict[tuple[str, str], float] = {}
        if joint:
            previous = self.config.section.joint_response_analysis
            targets: list[JointDispersionTargetConfig] = []
            for row, bpm in enumerate(candidates):
                if bpm not in selected_bpms:
                    continue
                for plane, target_column, tolerance_column in (
                    ("x", 2, 3),
                    ("y", 4, 5),
                ):
                    target_mm = self._table_spin_value(
                        table,
                        row,
                        target_column,
                    )
                    tolerance_mm = self._table_spin_value(
                        table,
                        row,
                        tolerance_column,
                    )
                    targets.append(
                        JointDispersionTargetConfig(
                            bpm=bpm,
                            plane=plane,
                            target_mm=target_mm,
                            tolerance_mm=tolerance_mm,
                        )
                    )
                    target_map[(bpm, plane)] = target_mm
            section = replace(
                self.config.section,
                joint_response_analysis=replace(
                    previous,
                    targets=tuple(targets),
                ),
            )
            target_bpms: tuple[str, ...] = ()
            monitor_bpms = tuple(
                dict.fromkeys(
                    (*previous_measurement_bpms, *selected_bpms)
                )
            )
        else:
            plane = self.config.measurement.planes[0]
            value_by_bpm = {
                bpm: self._table_spin_value(table, row, 3)
                for row, bpm in enumerate(candidates)
                if bpm in selected_bpms
            }
            values = tuple(
                value_by_bpm[bpm] for bpm in selected_bpms
            )
            target_map.update(
                {
                    (bpm, plane): value
                    for bpm, value in value_by_bpm.items()
                }
            )
            section = replace(
                self.config.section,
                target_dispersion_mm=values,
            )
            target_bpms = selected_bpms
            monitor_bpms = tuple(
                bpm
                for bpm in dict.fromkeys(
                    (*previous_measurement_bpms, *selected_bpms)
                )
                if bpm not in selected_bpms
            )
        observables = tuple(
            replace(
                observable,
                target=target_map.get(
                    (
                        observable.element,
                        observable.component.lower().removeprefix("d"),
                    ),
                    observable.target,
                ),
            )
            for observable in section.model_observables
        )
        self.config = replace(
            self.config,
            target_bpms=target_bpms,
            monitor_bpms=monitor_bpms,
            section=replace(section, model_observables=observables),
        )
        measurement_bpms_changed = (
            self.config.measurement_bpms != previous_measurement_bpms
        )
        self.correction_restore_request = None
        self.correction_recommendation = None
        if measurement_bpms_changed:
            self.last_live_preflight = None
            self._invalidate_staged_results(
                "Correction BPMs changed. Remeasure dispersion before "
                "continuing."
            )
        elif joint:
            self.latest_joint_response = None
            self.response_table.setRowCount(0)
            self.response_info.clear()
        elif self.latest_measurement is not None:
            retargeted = self._retarget_measurement(
                self.latest_measurement,
                target_map,
            )
            self.latest_measurement = retargeted
            if self.latest_response is not None:
                self.latest_response = replace(
                    self.latest_response,
                    measurement=retargeted,
                )
            self._show_measurement(retargeted)
        self.recommendation_summary_label.setText(
            "Targets changed. Calculate a new recommendation."
        )
        self.recommendation_prediction_table.setRowCount(0)
        self.recommendation_table.setRowCount(0)
        self._update_correction_bpm_summary()
        correction_bpms = self._configured_correction_bpms()
        self.dispersion_curve.set_correction_bpms(correction_bpms)
        self.iteration_history_curve.set_correction_bpms(correction_bpms)
        self.monitor_bpm_edit.setText(
            ", ".join(self._display_monitor_bpms())
        )
        self._sync_nonmeasurement_settings()

    @staticmethod
    def _bpm_use_checkbox(checked: bool) -> QCheckBox:
        checkbox = QCheckBox()
        checkbox.setProperty("role", "bpmUseToggle")
        checkbox.setChecked(checked)
        checkbox.setToolTip(
            "Correction target" if checked else "Measurement-only monitor"
        )
        return checkbox

    @staticmethod
    def _table_checkbox_checked(
        table: QTableWidget,
        row: int,
        column: int,
    ) -> bool:
        widget = table.cellWidget(row, column)
        return bool(
            isinstance(widget, QCheckBox) and widget.isChecked()
        )

    @staticmethod
    def _retarget_measurement(
        measurement: DispersionMeasurement,
        targets: dict[tuple[str, str], float],
    ) -> DispersionMeasurement:
        target_values = np.asarray(
            [
                targets.get((bpm, measurement.plane), 0.0)
                for bpm in measurement.bpm_names
            ],
            dtype=float,
        )
        target_mask = np.asarray(
            [
                (bpm, measurement.plane) in targets
                for bpm in measurement.bpm_names
            ],
            dtype=bool,
        )
        return replace(
            measurement,
            target_values_mm=target_values,
            target_mask=target_mask,
        )

    @staticmethod
    def _dispersion_target_spin(value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setDecimals(6)
        spin.setRange(-1.0e6, 1.0e6)
        spin.setSingleStep(0.1)
        spin.setValue(float(value))
        return spin

    @staticmethod
    def _dispersion_tolerance_spin(value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setDecimals(6)
        spin.setRange(1.0e-6, 1.0e6)
        spin.setSingleStep(0.1)
        spin.setValue(float(value))
        return spin

    def _build_knob_selection_dialog(self):
        dialog = QDialog(self)
        dialog.setObjectName("knobSelectionDialog")
        dialog.setStyleSheet(build_stylesheet(self.theme_name))
        dialog.setWindowTitle("Set Quad Knobs")
        dialog.resize(720, 300)
        layout = QVBoxLayout(dialog)
        prompt = QLabel(
            "Choose two distinct quadrupoles for each symmetric knob. "
            "Session measure-step and total-Δ limits cannot exceed profile limits."
        )
        prompt.setObjectName("knobSelectionPrompt")
        prompt.setWordWrap(True)
        layout.addWidget(prompt)

        unit = self._knob_control_unit()
        suffix = f" ({unit})" if unit else ""
        table = QTableWidget(len(self.selected_knobs), 5)
        table.setObjectName("knobSelectionTable")
        table.setHorizontalHeaderLabels(
            [
                "Knob",
                "Q1",
                "Q2",
                f"Measure Step ±{suffix}",
                f"Total Δ Limit ±{suffix}",
            ]
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
                    f"Knob row {row + 1} requires Measure Step <= Total Δ Limit"
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
            raise ValueError(
                f"Table row {row + 1} has no numeric value in "
                f"column {column + 1}"
            )
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

    def _selection_changed(self) -> None:
        if self._loading_widgets:
            return
        self.correction_restore_request = None
        self._invalidate_staged_results(
            "Configuration changed. Previous measurements and recommendations were discarded."
        )
        self.last_live_preflight = None
        try:
            self.config = self._config_from_widgets()
            self.operation_plan = build_operation_plan(self.config)
            self._update_correction_bpm_summary()
        except Exception as exc:
            self.operation_plan = None
            self._append_log(f"Operation plan validation failed: {exc}")
            self.status_strip.set_value("READINESS", "NOT READY", "danger")
            self.measure_button.setEnabled(False)
            self.response_button.setEnabled(False)
            self.run_button.setEnabled(False)
            self.review_button.setEnabled(False)
            self.compute_recommendation_button.setEnabled(False)
            self.apply_recommendation_button.setEnabled(False)
            return
        self._update_energy_step_summary()
        self._update_static_safety_status()
        self._refresh_status("Selection updated")
        self._set_running(False, "")

    def _knob_control_unit(self) -> str:
        pv_map = self.config.backend.options.get("pv_map", {})
        quadrupoles = pv_map.get("quadrupoles", {}) if isinstance(pv_map, dict) else {}
        controls = {
            str(quadrupoles.get(device, {}).get("control", "k1")).lower()
            for knob in self.config.runtime_knobs
            for device in knob.devices
            if isinstance(quadrupoles.get(device), dict)
        }
        if controls == {"current"}:
            return "A"
        if controls == {"k1"}:
            return "K1 [1/m²]"
        return ""

    def _config_from_widgets(self) -> RunConfig:
        diagnostic_only = self.config.section.diagnostic_only
        joint = self._joint_correction_enabled()
        bpms = (
            ()
            if diagnostic_only or joint
            else self.config.target_bpms
        )
        if not bpms and not diagnostic_only and not joint:
            raise ValueError("At least one BPM is required")
        session_knobs = () if diagnostic_only else tuple(self.selected_knobs)
        knobs = () if joint else session_knobs
        target_by_bpm = dict(
            zip(
                self.config.target_bpms,
                self.config.section.target_dispersion_mm,
            )
        )
        monitor_bpms = tuple(
            dict.fromkeys(
                (
                    *self.config.monitor_bpms,
                    *(
                        name
                        for name in self.config.target_bpms
                        if name not in bpms
                    ),
                )
            )
        )
        monitor_bpms = tuple(
            name for name in monitor_bpms if name not in bpms
        )

        section = self.config.section
        if joint:
            section = replace(
                section,
                joint_response_analysis=replace(
                    section.joint_response_analysis,
                    knobs=session_knobs,
                ),
            )
        config = replace(
            self.config,
            energy_knob=replace(self.config.energy_knob, delta=float(self.delta_spin.value())),
            target_bpms=bpms,
            monitor_bpms=monitor_bpms,
            section=replace(
                section,
                target_dispersion_mm=tuple(
                    target_by_bpm.get(name, 0.0)
                    for name in bpms
                ),
            ),
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
        validate_config(config)
        return config

    def _start_task(
        self,
        task: str,
        recommendation: CorrectionRecommendation | None = None,
        joint_recommendation: JointResponseAnalysisResult | None = None,
        design_k1_request: DesignK1Request | None = None,
        restore_request: CorrectionRestoreRequest | None = None,
    ) -> bool:
        if self.worker is not None and self.worker.isRunning():
            return False
        try:
            config = self._config_from_widgets()
        except Exception as exc:
            QMessageBox.warning(self, "Configuration", str(exc))
            return False
        self.config = config
        self._update_static_safety_status()
        blocked_reason = self._operation_block_reason()
        if blocked_reason is not None:
            QMessageBox.warning(self, "Dispersion Correction", blocked_reason)
            self._set_running(False, "")
            return False
        if config.backend.type.lower() == "epics":
            preflight = run_preflight(config)
            if not preflight.ok:
                QMessageBox.warning(self, "EPICS Preflight", "\n".join(preflight.blockers))
                return False
        if task == "apply" and recommendation is None:
            QMessageBox.warning(
                self,
                "Apply Recommendation",
                "Calculate and review a recommendation before applying it.",
            )
            return False
        if task == "joint-apply" and joint_recommendation is None:
            QMessageBox.warning(
                self,
                "Apply Joint Recommendation",
                "Measure and review the joint Q response before applying it.",
            )
            return False
        if task == "restore-correction" and restore_request is None:
            QMessageBox.warning(
                self,
                "Restore Initial Correction State",
                "No successful correction is available to restore.",
            )
            return False
        if task in {"run", "joint-run"}:
            self._automatic_initial_measurement = None
        self.worker = WorkflowWorker(
            task=task,
            config=config,
            recommendation=recommendation,
            joint_recommendation=joint_recommendation,
            design_k1_request=design_k1_request,
            restore_request=restore_request,
        )
        self.worker.log.connect(self._append_log)
        self.worker.progress.connect(self._update_progress)
        self.worker.correction_measurement.connect(
            self._automatic_measurement_updated
        )
        self.worker.preflight.connect(self._workflow_preflight_completed)
        self.worker.failed.connect(self._task_failed)
        self.worker.completed.connect(self._task_completed)
        self.worker.finished.connect(self._task_finished)
        self._set_running(True, task)
        self._update_progress("Starting", 0, 1)
        self.worker.start()
        return True

    def _task_completed(self, task: str, result: object) -> None:
        if isinstance(result, MultiPlaneDispersionMeasurement):
            self.correction_mode = None
            self.latest_plane_measurements = {
                measurement.plane: measurement
                for measurement in result.measurements
            }
            self.latest_measurement = result.primary
            self.latest_measurement_time = datetime.now()
            self.latest_response = None
            self.latest_joint_response = None
            self.correction_recommendation = None
            self.correction_state_label.setText(
                (
                    "Two-plane dispersion measured. Choose manual or automatic "
                    "joint correction."
                    if self._joint_correction_enabled()
                    else "Two-plane dispersion measured from one energy scan."
                )
            )
            self.recommendation_summary_label.setText(
                "No joint recommendation has been calculated."
            )
            self.recommendation_prediction_table.setRowCount(0)
            self.recommendation_table.setRowCount(0)
            self._show_measurement(result)
            self._set_live_multiplane_measurement(
                result,
                label="Latest measured",
            )
            rms_text = " · ".join(
                f"η{measurement.plane} "
                f"{measurement.measured_rms_mm:.4g} mm"
                for measurement in result.measurements
            )
            self._refresh_status(rms_text)
        elif isinstance(result, DispersionMeasurement):
            self.correction_mode = None
            self.latest_plane_measurements = {result.plane: result}
            self.latest_measurement = result
            self.latest_measurement_time = datetime.now()
            self.latest_response = None
            self.latest_joint_response = None
            self.correction_recommendation = None
            self.correction_state_label.setText(
                "Dispersion measured. Prepare a correction when you are ready to scan "
                "the quadrupole response."
            )
            self.recommendation_summary_label.setText(
                "No recommendation has been calculated."
            )
            self.recommendation_prediction_table.setRowCount(0)
            self.recommendation_table.setRowCount(0)
            self._show_measurement(result)
            self._set_live_comparison_measurement(
                result,
                label="Latest measured",
            )
            self._refresh_status(f"RMS {result.rms_mm:.4g} mm")
        elif isinstance(result, ResponseMatrixResult):
            self.correction_mode = "manual"
            self.latest_response = result
            self.latest_measurement = result.measurement
            self.latest_measurement_time = datetime.now()
            self.correction_recommendation = None
            automatic_block = automatic_response_block_reason(
                result,
                self.config.solver.svd_cut,
            )
            rank_warning = rank_reduced_response_warning(
                result,
                self.config.solver.svd_cut,
            )
            self.correction_state_label.setText(
                (
                    automatic_block
                    + " A manually reviewed bounded recommendation is still available."
                )
                if automatic_block is not None
                else (
                    rank_warning
                    + " Calculating one bounded manual recommendation."
                    if rank_warning is not None
                    else "Quadrupole response measured. Calculating one bounded recommendation."
                )
            )
            self.recommendation_summary_label.setText(
                "No recommendation has been calculated."
            )
            self.recommendation_prediction_table.setRowCount(0)
            self.recommendation_table.setRowCount(0)
            self._show_response(result)
            self._show_measurement(result.measurement)
            self._set_live_comparison_measurement(
                result.measurement,
                label="Response baseline",
            )
            self._refresh_status(f"Cond {result.condition_number:.4g}")
            self._compute_recommendation()
        elif isinstance(result, JointResponseAnalysisResult):
            self.latest_joint_response = result
            self.latest_response = None
            self.latest_plane_measurements = {
                measurement.plane: measurement
                for measurement in result.baseline.measurements
            }
            self.latest_measurement = result.baseline.primary
            self.latest_measurement_time = datetime.now()
            self._show_joint_response(result)
            self._show_measurement(result.baseline)
            self._set_live_multiplane_measurement(
                result.baseline,
                label="Joint response baseline",
            )
            self.correction_state_label.setText(
                "Joint Q-response analysis complete. The suggested knob changes "
                "are a read-only preview and cannot be applied from this workflow."
            )
            self._refresh_status(
                f"Joint modes {result.retained_rank}/"
                f"{min(result.matrix.shape)}"
            )
        elif isinstance(result, JointCorrectionResult):
            self._record_correction_run(task, result)
            if result.success:
                self.correction_restore_request = (
                    self._build_correction_restore_request(
                        result,
                        run_label=self.correction_session_runs[-1].label,
                    )
                )
            self.latest_plane_measurements = {
                measurement.plane: measurement
                for measurement in result.final.measurements
            }
            self.latest_measurement = result.final.primary
            self.latest_measurement_time = datetime.now()
            self.latest_joint_response = None
            self.correction_mode = None
            self._show_measurement(result.final)
            self._set_live_multiplane_measurement(
                result.final,
                label=(
                    "Joint correction verified"
                    if result.success
                    else "Joint correction restored"
                ),
            )
            self.correction_state_label.setText(result.reason)
            self.response_table.setRowCount(0)
            self.response_info.clear()
            self._refresh_status(
                (
                    "Joint accepted"
                    if result.success
                    else "Joint not accepted"
                )
            )
            if result.success:
                self._refresh_snapshot_after_task = True
        elif isinstance(result, CorrectionResult):
            self._record_correction_run(task, result)
            if result.success:
                self.correction_restore_request = (
                    self._build_correction_restore_request(
                        result,
                        run_label=self.correction_session_runs[-1].label,
                    )
                )
            self._show_result(result)
            status = "Accepted" if result.success else "Aborted" if result.reason.startswith("Aborted") else "Not accepted"
            self._refresh_status(status)
            self.status_strip.set_value(
                "READINESS",
                result.safety.reason,
                "success" if result.safety.ok else "danger",
            )
            self.latest_measurement = result.final if result.success else None
            self.latest_measurement_time = (
                datetime.now() if result.success else None
            )
            self.latest_response = None
            self.correction_recommendation = None
            self.correction_mode = None
            self.correction_state_label.setText(
                (
                    "Execution completed and final dispersion verified. Choose manual "
                    "or automatic correction to continue."
                )
                if result.success
                else (
                    "The correction was not accepted or was aborted. Remeasure "
                    "dispersion before starting another correction."
                )
            )
            self.recommendation_summary_label.setText(
                "The reviewed recommendation is no longer current."
            )
            self.recommendation_prediction_table.setRowCount(0)
            self.recommendation_table.setRowCount(0)
            if result.success:
                self._refresh_snapshot_after_task = True
        elif (
            task == "design-k1"
            and isinstance(result, dict)
            and result.get("operation") == "design-k1"
        ):
            self.correction_restore_request = None
            self._invalidate_staged_results(
                "Design K1 targets were applied. Remeasure dispersion before correction."
            )
            self.last_live_preflight = None
            self.status_strip.set_value("READINESS", "UNCHECKED", "warning")
            self._refresh_status("Design K1 applied")
            self._append_log(
                "Design K1 targets applied and verified: "
                + ", ".join(
                    f"{name}={value:.8g}"
                    for name, value in result.get("final_values", {}).items()
                )
            )
            self._refresh_snapshot_after_task = True
        elif (
            task == "restore-correction"
            and isinstance(result, dict)
            and result.get("operation") == "restore-correction"
        ):
            self.correction_restore_request = None
            self._invalidate_staged_results(
                "The pre-correction quadrupole state was restored. Remeasure "
                "dispersion before starting another correction."
            )
            self.last_live_preflight = None
            self.status_strip.set_value("READINESS", "UNCHECKED", "warning")
            self._refresh_status("Initial state restored")
            self._append_log(
                "Pre-correction quadrupole state restored and verified: "
                + ", ".join(
                    f"{name}={value:.8g}"
                    for name, value in result.get("final_values", {}).items()
                )
            )
            self._refresh_snapshot_after_task = True
        if self.app_context is not None and isinstance(
            result,
            (
                DispersionMeasurement,
                MultiPlaneDispersionMeasurement,
                ResponseMatrixResult,
                JointResponseAnalysisResult,
                JointCorrectionResult,
                CorrectionResult,
            ),
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
        if task in {"run", "joint-run"}:
            self._automatic_initial_measurement = None

    def _automatic_measurement_updated(
        self,
        iteration: int,
        total: int,
        state: str,
        measurement: object,
    ) -> None:
        if self._active_task not in {"run", "joint-run"}:
            return
        if isinstance(measurement, MultiPlaneDispersionMeasurement):
            self._show_measurement(measurement)
            self._set_live_multiplane_measurement(
                measurement,
                label=f"Joint generation {iteration} · {state}",
            )
            rms_text = " · ".join(
                f"η{item.plane} {item.measured_rms_mm:.4g} mm"
                for item in measurement.measurements
            )
            summary = (
                f"Automatic joint correction · generation {iteration}/{total} "
                f"{state} · {rms_text}"
            )
            self.workflow_summary_label.setText(summary)
            self.plot_state_label.setText(summary)
            self.plot_state_label.show()
            return
        if not isinstance(measurement, DispersionMeasurement):
            return
        if state == "initial":
            self._automatic_initial_measurement = measurement
            label = "Automatic initial"
            reference = None
            summary = (
                f"Automatic correction · initial dispersion measured · "
                f"RMS {measurement.rms_mm:.6g} mm"
            )
        elif state == "final":
            label = "Final verification"
            reference = self._automatic_initial_measurement
            summary = (
                f"Automatic correction · final verification measured · "
                f"RMS {measurement.rms_mm:.6g} mm"
            )
        else:
            label = f"Generation {iteration} · {state}"
            reference = self._automatic_initial_measurement
            restored = (
                " · restoring previous state"
                if state == "rejected"
                else ""
            )
            summary = (
                f"Automatic correction · generation {iteration}/{total} "
                f"{state}{restored} · RMS {measurement.rms_mm:.6g} mm"
            )
        self._show_measurement(measurement)
        self._set_live_comparison_measurement(
            measurement,
            label=label,
            reference=reference,
        )
        self.workflow_summary_label.setText(summary)
        self.plot_state_label.setText(summary)
        self.plot_state_label.show()

    def _task_failed(self, message: str) -> None:
        failed_task = self._active_task
        self._automatic_initial_measurement = None
        if failed_task in {
            "response",
            "joint-response",
            "apply",
            "joint-apply",
            "run",
            "joint-run",
        }:
            self.correction_mode = None
        if message == "Operation aborted":
            self._append_log("Operation aborted; temporary state restored")
            self._refresh_status("Aborted")
            return
        self._append_log(f"ERROR: {message}")
        self.status_strip.set_value("READINESS", "NOT READY", "danger")
        self._refresh_status("Failed")
        QMessageBox.warning(self, "Workflow", message)

    def _task_finished(self) -> None:
        refresh_snapshot = self._refresh_snapshot_after_task
        self._refresh_snapshot_after_task = False
        self._set_running(False, "")
        if refresh_snapshot and self._model_analysis_available():
            self._refresh_current_snapshot()

    def _start_model_response(
        self,
        *,
        model_source: str | None = None,
        focus_comparison: bool = True,
    ) -> None:
        if self.app_context is None or self.app_context.model_backend is None:
            QMessageBox.warning(self, "Model Comparison", "No Elegant model backend is configured.")
            return
        if self.model_worker is not None and self.model_worker.isRunning():
            return
        try:
            self.config = self._config_from_widgets()
        except Exception as exc:
            QMessageBox.warning(self, "Configuration", str(exc))
            return
        selected_source = model_source or str(
            self.model_source_combo.currentData() or "design"
        )
        source_index = self.model_source_combo.findData(selected_source)
        if source_index >= 0 and source_index != self.model_source_combo.currentIndex():
            self.model_source_combo.blockSignals(True)
            self.model_source_combo.setCurrentIndex(source_index)
            self.model_source_combo.blockSignals(False)
        self.pending_model_source = selected_source
        self.model_worker = ModelResponseWorker(
            self.app_context,
            self.config,
            selected_source,
        )
        self.model_worker.progress.connect(self._update_progress)
        self.model_worker.failed.connect(self._model_response_failed)
        self.model_worker.completed.connect(self._model_response_completed)
        self.model_worker.finished.connect(self._task_finished)
        self._set_running(True, "model-response")
        if focus_comparison:
            self._show_workflow_detail(self.model_page)
        self.model_worker.start()

    def _model_response_completed(self, result: object) -> None:
        self.pending_model_source = None
        if not isinstance(result, ModelResponseResult):
            self._task_failed("Unexpected model comparison result")
            return
        if result.model_source != "design":
            self.current_snapshot_time = datetime.now()
            refreshed_at = self.current_snapshot_time.strftime("%H:%M:%S")
            self.show_snapshot_model_checkbox.setToolTip(
                "Read the configured quadrupole K1 snapshot and calculate its model "
                f"curve. Last refreshed at {refreshed_at}."
            )
        self._show_model_response(result)
        self._refresh_status("Model comparison ready")
        self._append_log(
            f"Model comparison completed from {result.model_source} without machine writes"
        )
        self._set_running(False, "")
        design_k1_reason = self._design_k1_block_reason()
        if design_k1_reason is None:
            self._append_log("Design K1 targets are ready for operator review")
        else:
            self._append_log(f"Design K1 remains blocked: {design_k1_reason}")

    def _model_response_failed(self, message: str) -> None:
        failed_source = self.pending_model_source
        self.pending_model_source = None
        response = self.dispersion_curve.result
        if failed_source == "design" and response is None:
            self.show_design_model_checkbox.blockSignals(True)
            self.show_design_model_checkbox.setChecked(False)
            self.show_design_model_checkbox.blockSignals(False)
        elif failed_source not in {None, "design"} and (
            response is None or response.model_source == "design"
        ):
            self.show_snapshot_model_checkbox.blockSignals(True)
            self.show_snapshot_model_checkbox.setChecked(False)
            self.show_snapshot_model_checkbox.blockSignals(False)
        self._model_visibility_changed()
        self._task_failed(message)

    def _show_measurement(
        self,
        result: DispersionMeasurement | MultiPlaneDispersionMeasurement,
    ) -> None:
        measurements = (
            result.measurements
            if isinstance(result, MultiPlaneDispersionMeasurement)
            else (result,)
        )
        multi_plane = len(measurements) > 1
        headers = [
            "BPM",
            *(["Plane"] if multi_plane else []),
            "Role",
            "Measured mm",
            "Target mm",
            "Residual mm",
            "Valid",
        ]
        self.measure_table.setHorizontalHeaderLabels(headers)
        self.measure_table.setColumnCount(len(headers))
        row_count = sum(
            len(measurement.bpm_names)
            for measurement in measurements
        )
        self.measure_table.setRowCount(row_count)
        row = 0
        for measurement in measurements:
            for index, name in enumerate(measurement.bpm_names):
                is_target = bool(measurement.target_mask[index])
                column = 0
                self.measure_table.setItem(row, column, QTableWidgetItem(name))
                column += 1
                if multi_plane:
                    self.measure_table.setItem(
                        row,
                        column,
                        QTableWidgetItem(f"η{measurement.plane}"),
                    )
                    column += 1
                self.measure_table.setItem(
                    row,
                    column,
                    QTableWidgetItem(
                        "Correction" if is_target else "Monitor"
                    ),
                )
                column += 1
                self.measure_table.setItem(
                    row,
                    column,
                    QTableWidgetItem(
                        f"{measurement.values_mm[index]:.6g}"
                    ),
                )
                column += 1
                self.measure_table.setItem(
                    row,
                    column,
                    QTableWidgetItem(
                        f"{measurement.target_values_mm[index]:.6g}"
                        if is_target
                        else "—"
                    ),
                )
                column += 1
                self.measure_table.setItem(
                    row,
                    column,
                    QTableWidgetItem(
                        f"{measurement.residual_values_mm[index]:.6g}"
                        if is_target
                        else "—"
                    ),
                )
                column += 1
                self.measure_table.setItem(
                    row,
                    column,
                    QTableWidgetItem(
                        "yes" if measurement.valid[index] else "no"
                    ),
                )
                row += 1
        self.measure_table.resizeColumnsToContents()

    @staticmethod
    def _plot_dataset_from_measurement(
        measurement: DispersionMeasurement,
        label: str,
    ) -> DispersionPlotDataset:
        return DispersionPlotDataset(
            bpm_names=measurement.bpm_names,
            values_mm=np.asarray(measurement.values_mm, dtype=float),
            sigma_mm=np.full(len(measurement.bpm_names), np.nan),
            valid=np.asarray(measurement.valid, dtype=bool),
            label=label,
            target_mask=np.asarray(measurement.target_mask, dtype=bool),
            plane=measurement.plane,
        )

    def _set_live_multiplane_measurement(
        self,
        result: MultiPlaneDispersionMeasurement,
        *,
        label: str,
    ) -> None:
        self.live_plane_measurements = {
            measurement.plane: self._plot_dataset_from_measurement(
                measurement,
                label,
            )
            for measurement in result.measurements
        }
        self.reference_plot_measurement = None
        self.display_plane_combo.show()
        self._display_plane_changed()

    def _set_live_comparison_measurement(
        self,
        measurement: DispersionMeasurement,
        *,
        label: str,
        reference: DispersionMeasurement | None = None,
    ) -> None:
        self.live_plane_measurements = {}
        self.live_plot_measurement = self._plot_dataset_from_measurement(
            measurement,
            label,
        )
        self.reference_plot_measurement = (
            None
            if reference is None
            else self._plot_dataset_from_measurement(reference, "Before correction")
        )
        self._refresh_plot_measurement()

    def _active_plot_measurement(self) -> DispersionPlotDataset | None:
        return self.live_plot_measurement

    def _display_plane_changed(self, _index: int | None = None) -> None:
        plane = str(
            self.display_plane_combo.currentData()
            or self.config.measurement.planes[0]
        )
        if plane not in self.config.measurement.planes:
            plane = self.config.measurement.planes[0]
        self.dispersion_curve.set_plane(plane)
        if self.live_plane_measurements:
            self.live_plot_measurement = self.live_plane_measurements.get(
                plane
            )
        self._refresh_plot_measurement()

    def _refresh_plot_measurement(self) -> None:
        measurement = self.live_plot_measurement
        self.dispersion_curve.set_measurement(
            measurement,
            self.reference_plot_measurement,
        )
        self._report_unmapped_plot_bpms()
        self._show_measurement_comparison(measurement)
        self._update_plot_state()

    def _report_unmapped_plot_bpms(self) -> None:
        unmapped = self.dispersion_curve.unmapped_measurement_bpms()
        if unmapped == self._last_unmapped_plot_bpms:
            return
        self._last_unmapped_plot_bpms = unmapped
        if unmapped:
            self._append_log(
                "Measured BPMs missing from the model plot: "
                + ", ".join(unmapped)
            )

    def _update_plot_state(self, *, running: bool = False, task: str = "") -> None:
        if not hasattr(self, "plot_state_label"):
            return
        if running:
            messages = {
                "measure": "Measuring dispersion · current plot remains unchanged",
                "response": "Measuring Q response · current plot remains unchanged",
                "apply": "Applying correction · current plot remains unchanged",
                "run": "Automatic correction running · current plot remains unchanged",
                "model-response": "Analyzing model · measurement remains unchanged",
                "preflight": "Checking connections · measurement remains unchanged",
                "design-k1": "Applying design K1 · current plot remains unchanged",
                "restore-correction": (
                    "Restoring initial state · current plot remains unchanged"
                ),
            }
            self.plot_state_label.setText(messages.get(task, "Operation in progress"))
            self.plot_state_label.show()
            return
        measurement = self._active_plot_measurement()
        if measurement is None:
            if self.dispersion_curve.result is None:
                self.plot_state_label.setText("No measured data")
                self.plot_state_label.hide()
            else:
                self.plot_state_label.setText("Model reference only · no measured data")
                self.plot_state_label.show()
            return
        target_valid = int(
            np.count_nonzero(measurement.valid & measurement.target_mask)
        )
        target_count = int(np.count_nonzero(measurement.target_mask))
        monitor_mask = ~measurement.target_mask
        monitor_count = int(np.count_nonzero(monitor_mask))
        validity = f"{target_valid}/{target_count} correction BPMs valid"
        if monitor_count:
            monitor_valid = int(
                np.count_nonzero(measurement.valid & monitor_mask)
            )
            validity += f" · {monitor_valid}/{monitor_count} monitors valid"
        self.plot_state_label.setText(f"{measurement.label} · {validity}")
        self.plot_state_label.show()

    def _model_visibility_changed(self, _checked: bool | None = None) -> None:
        self.dispersion_curve.set_model_visibility(
            design=self.show_design_model_checkbox.isChecked(),
            snapshot=self.show_snapshot_model_checkbox.isChecked(),
        )
        self._show_measurement_comparison(self._active_plot_measurement())
        if self.model_worker is not None and self.model_worker.isRunning():
            return
        response = self.dispersion_curve.result
        if self.show_snapshot_model_checkbox.isChecked() and (
            response is None or response.model_source == "design"
        ):
            self._start_model_response(
                model_source="live",
                focus_comparison=False,
            )
        elif self.show_design_model_checkbox.isChecked() and response is None:
            self._start_model_response(
                model_source="design",
                focus_comparison=False,
            )

    def _refresh_current_snapshot(self) -> None:
        if not self._model_analysis_available():
            return
        if self.model_worker is not None and self.model_worker.isRunning():
            return
        self.show_snapshot_model_checkbox.blockSignals(True)
        self.show_snapshot_model_checkbox.setChecked(True)
        self.show_snapshot_model_checkbox.blockSignals(False)
        self.dispersion_curve.set_model_visibility(
            design=self.show_design_model_checkbox.isChecked(),
            snapshot=True,
        )
        self._show_measurement_comparison(self._active_plot_measurement())
        self._start_model_response(
            model_source="live",
            focus_comparison=False,
        )

    def _show_measurement_comparison(
        self,
        measurement: DispersionPlotDataset | None,
    ) -> None:
        if measurement is None:
            self.model_measure_table.setRowCount(0)
            self.model_measure_table.setVisible(False)
            self.model_empty_label.setVisible(
                self.dispersion_curve.result is None
            )
            return
        self.model_empty_label.setVisible(False)
        response = self.dispersion_curve.result
        plane = measurement.plane
        eta_label = f"η{plane}"

        def curve_values(curve: ModelOpticsCurve) -> np.ndarray:
            return curve.dx_mm if plane == "x" else curve.dy_mm

        model_columns: list[tuple[str, dict[str, float]]] = []
        if response is not None and self.show_design_model_checkbox.isChecked():
            curve = response.design_curve or response.selected_curve
            model_columns.append(
                (
                    "Design model",
                    {
                        name: float(curve_values(curve)[index])
                        for index, name in enumerate(curve.element_names)
                    },
                )
            )
        if (
            response is not None
            and self.show_snapshot_model_checkbox.isChecked()
            and response.model_source != "design"
        ):
            curve = response.selected_curve
            model_columns.append(
                (
                    "Current K1 model",
                    {
                        name: float(curve_values(curve)[index])
                        for index, name in enumerate(curve.element_names)
                    },
                )
            )
        headers = [
            "BPM",
            f"Measurement {eta_label} (mm)",
            f"σ{eta_label} (mm)",
            "Valid",
        ]
        for label, _values in model_columns:
            headers.extend(
                (
                    f"{label} {eta_label} (mm)",
                    f"Measurement − {label} (mm)",
                )
            )
        self.model_measure_table.setColumnCount(len(headers))
        self.model_measure_table.setHorizontalHeaderLabels(headers)
        self.model_measure_table.setRowCount(len(measurement.bpm_names))
        self.model_measure_table.setVisible(True)
        for row, (bpm, measured, sigma, valid) in enumerate(
            zip(
                measurement.bpm_names,
                measurement.values_mm,
                measurement.sigma_mm,
                measurement.valid,
            )
        ):
            sigma_text = f"{sigma:.6g}" if math.isfinite(float(sigma)) else ""
            self.model_measure_table.setItem(row, 0, QTableWidgetItem(bpm))
            self.model_measure_table.setItem(row, 1, QTableWidgetItem(f"{measured:.6g}"))
            self.model_measure_table.setItem(row, 2, QTableWidgetItem(sigma_text))
            self.model_measure_table.setItem(
                row,
                3,
                QTableWidgetItem("yes" if bool(valid) else "no"),
            )
            column = 4
            for _label, values in model_columns:
                model_value = values.get(bpm)
                model_text = "" if model_value is None else f"{model_value:.6g}"
                residual_text = (
                    ""
                    if model_value is None
                    else f"{float(measured) - model_value:.6g}"
                )
                self.model_measure_table.setItem(
                    row,
                    column,
                    QTableWidgetItem(model_text),
                )
                self.model_measure_table.setItem(
                    row,
                    column + 1,
                    QTableWidgetItem(residual_text),
                )
                column += 2
        self.model_measure_table.resizeColumnsToContents()

    def _show_response(self, response: ResponseMatrixResult) -> None:
        self.response_dialog.setWindowTitle("Q Response Diagnostics")
        self.apply_joint_recommendation_button.hide()
        self.response_table.setRowCount(len(response.bpm_names))
        self.response_table.setColumnCount(len(response.knob_names) + 2)
        self.response_table.setHorizontalHeaderLabels(
            ["BPM", "Role", *response.knob_names]
        )
        for row, bpm in enumerate(response.bpm_names):
            self.response_table.setItem(row, 0, QTableWidgetItem(bpm))
            self.response_table.setItem(
                row,
                1,
                QTableWidgetItem(
                    "Correction"
                    if bool(response.measurement.target_mask[row])
                    else "Monitor"
                ),
            )
            for col, value in enumerate(response.matrix[row, :], start=2):
                self.response_table.setItem(row, col, QTableWidgetItem(f"{value:.6g}"))
        self.response_table.resizeColumnsToContents()
        retained_rank, required_rank, target_count, knob_count = (
            response_mode_counts(
                response,
                self.config.solver.svd_cut,
            )
        )
        rank_summary = (
            f"\nCorrection knobs: {knob_count}"
            f"\nTarget BPM modes: {target_count}"
            f"\nEffective modes: {retained_rank}/{required_rank} "
            f"at svd_cut={self.config.solver.svd_cut:g}"
        )
        if retained_rank < required_rank:
            rank_summary += (
                "\nRank-reduced response: manual correction uses only the retained "
                "independent mode; automatic correction uses the same controllable "
                "mode and stops if measured RMS does not improve."
            )
        self.response_info.setPlainText(
            "Singular values: "
            + ", ".join(f"{value:.6g}" for value in response.singular_values)
            + f"\nCondition number: {response.condition_number:.6g}"
            + rank_summary
        )

    def _show_joint_response(self, result: JointResponseAnalysisResult) -> None:
        self.response_dialog.setWindowTitle("Joint ηx / ηy Q Response Analysis")
        self.response_table.setRowCount(len(result.target_names))
        self.response_table.setColumnCount(len(result.knob_names) + 4)
        self.response_table.setHorizontalHeaderLabels(
            ["Observation", "Measured", "Target", "Predicted", *result.knob_names]
        )
        for row, name in enumerate(result.target_names):
            values = (
                name,
                f"{result.baseline_values_mm[row]:.6g}",
                f"{result.target_values_mm[row]:.6g}",
                f"{result.predicted_values_mm[row]:.6g}",
            )
            for column, value in enumerate(values):
                self.response_table.setItem(
                    row,
                    column,
                    QTableWidgetItem(value),
                )
            for column, value in enumerate(
                result.matrix[row, :],
                start=4,
            ):
                self.response_table.setItem(
                    row,
                    column,
                    QTableWidgetItem(f"{value:.6g}"),
                )
        self.response_table.resizeColumnsToContents()
        recommended = "\n".join(
            f"  {name}: {value:+.6g}"
            for name, value in result.delta_knobs.items()
        )
        required = min(result.matrix.shape)
        can_apply = not self.config.section.diagnostic_only
        self.apply_joint_recommendation_button.setVisible(can_apply)
        self.apply_joint_recommendation_button.setEnabled(can_apply)
        self.response_info.setPlainText(
            (
                "Review the joint recommendation before Apply and Verify.\n"
                if can_apply
                else "Read-only recommendation preview; no Apply action is available.\n"
            )
            + f"Effective modes: {result.retained_rank}/{required} "
            f"at svd_cut={self.config.solver.svd_cut:g}\n"
            f"Singular values: "
            + ", ".join(f"{value:.6g}" for value in result.singular_values)
            + f"\nCondition number: {result.condition_number:.6g}"
            + f"\nNormalized residual RMS: "
            f"{result.normalized_rms_before:.6g} → "
            f"{result.normalized_rms_after:.6g}"
            + f"\nUncontrollable residual RMS: {result.uncontrollable_rms:.6g}"
            + "\nSuggested analysis-knob changes:\n"
            + recommended
        )

    def _apply_joint_recommendation(self) -> None:
        recommendation = self.latest_joint_response
        if recommendation is None or self.config.section.diagnostic_only:
            return
        answer = QMessageBox.question(
            self,
            "Apply Joint Recommendation",
            "Apply the reviewed ηx/ηy quadrupole targets and verify both planes? "
            "The initial state is restored if the residual does not improve.",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return
        self.response_dialog.close()
        self._start_task(
            "joint-apply",
            joint_recommendation=recommendation,
        )

    def _prepare_correction(self) -> None:
        if self.latest_measurement is None:
            QMessageBox.warning(
                self,
                "Prepare Correction",
                "Measure the current dispersion before preparing a correction.",
            )
            return
        self.correction_mode = "manual"
        if self.latest_response is None:
            scan_count = 1 + 2 * len(self.config.knobs)
            knob_lines = "\n".join(
                f"  {knob.name}: ±{knob.scan_step:g}"
                for knob in self.config.knobs
            )
            answer = QMessageBox.question(
                self,
                "Measure Q Response",
                (
                    "This operation writes temporary quadrupole scan settings and "
                    "performs a full ±energy scan at each setting.\n\n"
                    f"Dispersion scans: {scan_count} "
                    f"(1 baseline + 2 × {len(self.config.knobs)} knobs)\n"
                    f"Quad measure steps:\n{knob_lines}\n\n"
                    "Every temporary setting is restored after its response column. "
                    "Continue?"
                ),
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if answer != QMessageBox.Yes:
                return
            self._start_task("response")
            return
        self._compute_recommendation()

    def _return_to_correction_methods(self) -> None:
        if self._active_task:
            return
        self.correction_mode = None
        self.correction_recommendation = None
        self.response_info.clear()
        self.recommendation_prediction_table.setRowCount(0)
        self.recommendation_table.setRowCount(0)
        self.recommendation_summary_label.setText(
            "No recommendation has been calculated."
        )
        self.response_dialog.close()
        self.recommendation_dialog.close()
        if self.latest_measurement is not None:
            self._set_live_comparison_measurement(
                self.latest_measurement,
                label="Latest measured",
            )
            self.correction_state_label.setText(
                "Manual preparation was discarded. The current dispersion "
                "measurement remains valid."
            )
        self._set_running(False, "")

    def _automatic_correction_settings_tooltip(self) -> str:
        policy = (
            "measure Q response every generation"
            if self.response_update_combo.currentText() == "every_iteration"
            else "reuse the first measured Q response"
        )
        text = (
            f"Maximum {self.max_iter_spin.value()} generations · {policy}. "
            "The loop may stop earlier."
        )
        rank_warning = rank_reduced_response_warning(
            self.latest_response,
            self.config.solver.svd_cut,
        )
        return f"{text} {rank_warning}" if rank_warning is not None else text

    def _update_automatic_correction_tooltip(
        self,
        _value: object | None = None,
    ) -> None:
        if self._active_task not in {"run", "joint-run"}:
            self.run_button.setToolTip(
                self._automatic_correction_settings_tooltip()
            )

    def _build_automatic_correction_dialog(
        self,
    ) -> tuple[QDialog, QSpinBox, QComboBox]:
        dialog = QDialog(self)
        dialog.setObjectName("automaticCorrectionDialog")
        dialog.setWindowTitle("Automatic Correction")
        dialog.setMinimumWidth(680)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        intro = QLabel(
            "Runs repeated measure → solve → apply → verify cycles without "
            "confirmation between accepted generations."
        )
        intro.setObjectName("automaticDialogIntro")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        settings_card = QFrame(dialog)
        settings_card.setObjectName("automaticSettingsCard")
        settings = QGridLayout(settings_card)
        settings.setContentsMargins(14, 10, 14, 12)
        settings.setHorizontalSpacing(10)
        settings.setVerticalSpacing(8)
        settings.setColumnStretch(1, 2)
        settings.setColumnStretch(3, 1)
        settings_title = QLabel("Run Settings")
        settings_title.setObjectName("automaticDialogSectionTitle")
        settings.addWidget(settings_title, 0, 0, 1, 4)

        generations_label = QLabel("Maximum generations (upper limit)")
        generations_label.setProperty("role", "field")
        settings.addWidget(generations_label, 1, 0)
        generations = QSpinBox(dialog)
        generations.setObjectName("automaticGenerationsSpin")
        generations.setRange(1, 20)
        generations.setValue(self.max_iter_spin.value())
        generations.setToolTip(
            "Maximum correction generations; the loop may stop earlier."
        )
        settings.addWidget(generations, 1, 1)

        gain_label = QLabel("Solver gain")
        gain_label.setProperty("role", "field")
        settings.addWidget(gain_label, 1, 2)
        gain_value = QLabel(f"{self.gain_spin.value():.3g}")
        gain_value.setObjectName("automaticReadOnlyValue")
        settings.addWidget(gain_value, 1, 3)

        response_label = QLabel("Q response strategy")
        response_label.setProperty("role", "field")
        settings.addWidget(response_label, 2, 0)
        response_policy = QComboBox(dialog)
        response_policy.setObjectName("automaticResponsePolicy")
        response_policy.addItem(
            "Every generation (recommended)",
            "every_iteration",
        )
        response_policy.addItem(
            "Once (reuse first response)",
            "once",
        )
        policy_index = response_policy.findData(
            self.response_update_combo.currentText()
        )
        response_policy.setCurrentIndex(max(0, policy_index))
        if self._joint_correction_enabled():
            response_policy.setCurrentIndex(0)
            response_policy.setEnabled(False)
            response_policy.setToolTip(
                "Joint ηx/ηy correction remeasures the response every generation."
            )
        settings.addWidget(response_policy, 2, 1)

        max_step_label = QLabel("Maximum step")
        max_step_label.setProperty("role", "field")
        settings.addWidget(max_step_label, 2, 2)
        max_step_value = QLabel(
            f"{self.max_step_pct_spin.value():.3g}% of range"
        )
        max_step_value.setObjectName("automaticReadOnlyValue")
        settings.addWidget(max_step_value, 2, 3)
        layout.addWidget(settings_card)

        safety_text = (
            "Stops early if dispersion does not improve or a safety check fails; "
            "rejected trial settings are restored automatically.\n"
            "For a new machine configuration, validate one manual correction first."
        )
        rank_warning = rank_reduced_response_warning(
            self.latest_response,
            self.config.solver.svd_cut,
        )
        if rank_warning is not None:
            safety_text += "\n\n" + rank_warning
        safety = QLabel(safety_text)
        safety.setObjectName("automaticSafetyNote")
        safety.setWordWrap(True)
        layout.addWidget(safety)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel_button = QPushButton("Cancel")
        cancel_button.setObjectName("automaticCancelButton")
        cancel_button.clicked.connect(dialog.reject)
        buttons.addWidget(cancel_button)
        start_button = QPushButton("Start Automatic Correction")
        start_button.setObjectName("automaticStartButton")
        start_button.setProperty("role", "control")
        start_button.clicked.connect(dialog.accept)
        buttons.addWidget(start_button)
        layout.addLayout(buttons)
        return dialog, generations, response_policy

    def _confirm_automatic_correction(self) -> None:
        block_reason = self._operation_block_reason()
        if block_reason is not None:
            QMessageBox.warning(self, "Automatic Correction", block_reason)
            return
        if (
            self.config.section.model_only
            or self.config.section.diagnostic_only
            or (
                self.config.measurement.plane == "xy"
                and not self._joint_correction_enabled()
            )
        ):
            return
        if self.latest_measurement is None:
            QMessageBox.warning(
                self,
                "Automatic Correction",
                "Measure dispersion before starting automatic correction.",
            )
            return
        response_block = (
            None
            if self._joint_correction_enabled()
            else automatic_response_block_reason(
                self.latest_response,
                self.config.solver.svd_cut,
            )
        )
        if response_block is not None:
            QMessageBox.warning(self, "Automatic Correction", response_block)
            return
        if self.config.backend.type.lower() == "epics" and (
            self.last_live_preflight is None or not self.last_live_preflight.ok
        ):
            QMessageBox.warning(
                self,
                "Automatic Correction",
                "Click Check PVs before starting automatic correction.",
            )
            return

        dialog, generations, response_policy = (
            self._build_automatic_correction_dialog()
        )
        if dialog.exec_() != QDialog.Accepted:
            return

        previous_loading = self._loading_widgets
        self._loading_widgets = True
        try:
            self.max_iter_spin.setValue(generations.value())
            self.response_update_combo.setCurrentText(
                "every_iteration"
                if self._joint_correction_enabled()
                else str(response_policy.currentData())
            )
        finally:
            self._loading_widgets = previous_loading
        self.correction_mode = "automatic"
        self.latest_response = None
        self.latest_joint_response = None
        self.correction_recommendation = None
        self.correction_state_label.setText(
            "Automatic correction is starting. Any previous single-generation "
            "recommendation was discarded."
        )
        self.recommendation_summary_label.setText(
            "No manually reviewed recommendation is active."
        )
        self.recommendation_prediction_table.setRowCount(0)
        self.recommendation_table.setRowCount(0)
        self.response_table.setRowCount(0)
        self.response_info.clear()
        if self.latest_measurement is not None:
            self._set_live_comparison_measurement(
                self.latest_measurement,
                label="Latest measured",
            )
        started = self._start_task(
            "joint-run" if self._joint_correction_enabled() else "run"
        )
        if started is False:
            self.correction_mode = None
            self._set_running(False, "")

    def _review_recommendation(self) -> None:
        self._show_workflow_detail(self.correction_page)
        if self.correction_recommendation is not None:
            return
        if self.latest_measurement is None or self.latest_response is None:
            self.correction_state_label.setText(
                "Prepare the correction first. That operation measures the Q response "
                "and records the dispersion baseline used by the recommendation."
            )
            return
        self._compute_recommendation()

    def _compute_recommendation(self) -> None:
        if self.latest_measurement is None or self.latest_response is None:
            QMessageBox.warning(
                self,
                "Correction Recommendation",
                "Prepare the correction before calculating a recommendation.",
            )
            return
        try:
            self.config = self._config_from_widgets()
            baseline = self._recommendation_device_baseline()
            recommendation = build_correction_recommendation(
                self.config,
                self.latest_measurement,
                self.latest_response,
                baseline_device_values=baseline,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Correction Recommendation", str(exc))
            return
        self.correction_recommendation = recommendation
        self._show_recommendation(recommendation)
        self._show_workflow_detail(self.correction_page)
        self._append_log(
            "Correction prepared from the measured Q response; no quadrupole target "
            "was written"
        )
        self._refresh_status("Recommendation ready")

    def _recommendation_device_baseline(self) -> dict[str, float]:
        if self.config.backend.type.lower() == "offline":
            return {}
        if self.last_live_preflight is None or not self.last_live_preflight.ok:
            raise RuntimeError(
                "A current successful connection check is required to attach physical "
                "quadrupole targets to the recommendation."
            )
        raw = self.last_live_preflight.readings.get("quadrupole_readbacks", {})
        baseline = {str(name): float(value) for name, value in raw.items()}
        required = {
            device for knob in self.config.knobs for device in knob.devices
        }
        missing = sorted(required - set(baseline))
        if missing:
            raise RuntimeError(
                "Live preflight did not return quadrupole readbacks for: "
                + ", ".join(missing)
            )
        return baseline

    def _show_recommendation(
        self,
        recommendation: CorrectionRecommendation,
    ) -> None:
        improvement = recommendation.measurement.rms_mm - recommendation.predicted_rms_mm
        retained_rank, required_rank, _target_count, knob_count = (
            response_mode_counts(
                recommendation.response,
                self.config.solver.svd_cut,
            )
        )
        self.correction_state_label.setText(
            "Prediction only — no backend read or write occurred. Review every target "
            "before choosing Apply and Verify."
        )
        knob_lines = [
            f"{name}: {value:+.6g}"
            for name, value in recommendation.delta_knobs.items()
        ]
        self.recommendation_summary_label.setText(
            f"Measured residual RMS: {recommendation.measurement.rms_mm:.6g} mm  →  "
            f"predicted: {recommendation.predicted_rms_mm:.6g} mm "
            f"(improvement {improvement:+.6g} mm)\n"
            f"Response condition number: {recommendation.condition_number:.6g} · "
            f"{knob_count} knobs · effective modes {retained_rank}/{required_rank} · "
            f"gain {self.config.solver.gain:.3g} · "
            f"max step {100.0 * self.config.solver.max_step_fraction:.1f}%\n"
            + "Knob changes: "
            + "; ".join(knob_lines)
        )
        measurement = recommendation.measurement
        self.recommendation_prediction_table.setRowCount(
            len(measurement.bpm_names)
        )
        for row, bpm in enumerate(measurement.bpm_names):
            is_target = bool(measurement.target_mask[row])
            self.recommendation_prediction_table.setItem(
                row, 0, QTableWidgetItem(bpm)
            )
            self.recommendation_prediction_table.setItem(
                row,
                1,
                QTableWidgetItem("Correction" if is_target else "Monitor"),
            )
            self.recommendation_prediction_table.setItem(
                row,
                2,
                QTableWidgetItem(f"{measurement.values_mm[row]:.6g}"),
            )
            self.recommendation_prediction_table.setItem(
                row,
                3,
                QTableWidgetItem(
                    f"{measurement.target_values_mm[row]:.6g}"
                    if is_target
                    else "—"
                ),
            )
            self.recommendation_prediction_table.setItem(
                row,
                4,
                QTableWidgetItem(f"{recommendation.predicted_values_mm[row]:.6g}"),
            )
            self.recommendation_prediction_table.setItem(
                row,
                5,
                QTableWidgetItem(
                    f"{recommendation.predicted_residual_values_mm[row]:.6g}"
                    if is_target
                    else "—"
                ),
            )
        self.recommendation_prediction_table.resizeColumnsToContents()
        source_knobs = {
            device: knob.name
            for knob in self.config.knobs
            for device in knob.devices
        }
        devices = tuple(recommendation.device_deltas)
        self.recommendation_table.setRowCount(len(devices))
        for row, device in enumerate(devices):
            current = recommendation.baseline_device_values.get(device)
            target = recommendation.target_device_values.get(device)
            current_text = "" if current is None else f"{current:.8g}"
            target_text = "" if target is None else f"{target:.8g}"
            status = (
                "Ready for reviewed write"
                if target is not None
                else "Offline logical prediction"
            )
            self.recommendation_table.setItem(row, 0, QTableWidgetItem(device))
            self.recommendation_table.setItem(row, 1, QTableWidgetItem(current_text))
            self.recommendation_table.setItem(
                row,
                2,
                QTableWidgetItem(f"{recommendation.device_deltas[device]:+.8g}"),
            )
            self.recommendation_table.setItem(row, 3, QTableWidgetItem(target_text))
            self.recommendation_table.setItem(
                row,
                4,
                QTableWidgetItem(source_knobs.get(device, "")),
            )
            self.recommendation_table.setItem(row, 5, QTableWidgetItem(status))
        self.recommendation_table.resizeColumnsToContents()

    def _apply_reviewed_recommendation(self) -> None:
        recommendation = self.correction_recommendation
        if recommendation is None or not recommendation.ready:
            QMessageBox.warning(
                self,
                "Apply Recommendation",
                "Calculate and review a non-zero recommendation first.",
            )
            return
        blocked_reason = self._recommendation_apply_block_reason()
        if blocked_reason is not None:
            QMessageBox.warning(self, "Apply Recommendation", blocked_reason)
            return
        answer = QMessageBox.question(
            self,
            "Confirm Reviewed Quadrupole Targets",
            self._format_recommendation_confirmation(recommendation),
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return
        self._start_task("apply", recommendation)

    def _recommendation_apply_block_reason(self) -> str | None:
        recommendation = self.correction_recommendation
        if recommendation is None or not recommendation.ready:
            return "No reviewed recommendation is ready."
        operation_reason = self._operation_block_reason()
        if operation_reason is not None:
            return operation_reason
        if self.config.backend.type.lower() == "offline":
            return None
        if self.last_live_preflight is None or not self.last_live_preflight.ok:
            return (
                "Click Check PVs after the most recent configuration change "
                "before applying the recommendation."
            )
        required = set(recommendation.device_deltas)
        if set(recommendation.target_device_values) != required:
            return "The recommendation does not contain physical targets for every quadrupole."
        return None

    def _format_recommendation_confirmation(
        self,
        recommendation: CorrectionRecommendation,
    ) -> str:
        lines = [
            "The following reviewed targets will be applied for one generation:",
            "",
        ]
        if self.config.backend.type.lower() == "offline":
            lines.extend(
                f"  {name}: {value:+.8g}"
                for name, value in recommendation.delta_knobs.items()
            )
            lines.extend(["", "Backend: OFFLINE demonstration"])
        else:
            pv_map = self.config.backend.options.get("pv_map", {})
            quadrupoles = (
                pv_map.get("quadrupoles", {}) if isinstance(pv_map, dict) else {}
            )
            unit = self._knob_control_unit() or "configured unit"
            for device, change in recommendation.device_deltas.items():
                current = recommendation.baseline_device_values[device]
                target = recommendation.target_device_values[device]
                entry = quadrupoles.get(device, {})
                control = (
                    str(entry.get("control", "k1")).lower()
                    if isinstance(entry, dict)
                    else "k1"
                )
                pv = ""
                if isinstance(entry, dict):
                    pv = str(
                        entry.get("current_set")
                        if control == "current"
                        else entry.get("K1_set") or entry.get("K1") or ""
                    )
                lines.append(
                    f"  {device}: {current:.8g} → {target:.8g} {unit} "
                    f"(Δ {change:+.8g})"
                )
                if pv:
                    lines.append(f"    PV: {pv}")
        lines.extend(
            [
                "",
                "The workflow will recheck connections and verify that the current "
                "quadrupole readbacks still match this review.",
                "It will then remeasure dispersion to verify the result. If safety "
                "checks fail or the RMS "
                "does not improve enough, the pre-apply snapshot is restored.",
                "",
                "Proceed?",
            ]
        )
        return "\n".join(lines)

    def _design_k1_request(self) -> DesignK1Request:
        response = self.dispersion_curve.result
        if response is None or response.model_source == "design":
            raise RuntimeError("Refresh Current K1 model before reviewing design K1 targets.")
        baseline = {
            name: float(response.selected_k1[name])
            for name in response.device_names
        }
        targets = {
            name: float(response.design_k1[name])
            for name in response.device_names
        }
        limits: dict[str, float] = {}
        for knob in self.config.runtime_knobs:
            for device, weight in knob.devices.items():
                weighted_limit = abs(float(weight)) * float(knob.limit)
                if weighted_limit <= 0:
                    continue
                limits[device] = min(
                    limits.get(device, weighted_limit),
                    weighted_limit,
                )
        missing = sorted(set(targets) - set(limits))
        if missing:
            raise RuntimeError(
                "No configured K1 change limit for: " + ", ".join(missing)
            )
        return DesignK1Request(
            baseline_values=baseline,
            target_values=targets,
            max_changes={name: limits[name] for name in targets},
        )

    def _design_k1_block_reason(self) -> str | None:
        if (
            self.config.measurement.plane == "xy"
            and not self._joint_correction_enabled()
        ):
            return (
                "Two-plane sections are measurement-only until joint response "
                "analysis is enabled."
            )
        if self.config.section.diagnostic_only:
            return (
                "Diagnostic sections measure and display dispersion but do not "
                "write quadrupoles."
            )
        if self.config.section.model_only:
            return "The active backend is model-only and cannot write design K1 targets."
        if self.config.backend.type.lower() != "epics":
            return "Apply Design K1 is available only for an online EPICS backend."
        if self.config.backend.mode != "write_enabled":
            return "The active backend is read-only."
        if self._knob_control_unit() != "K1 [1/m²]":
            return "Apply Design K1 requires K1-controlled quadrupole channels."
        operation_reason = self._operation_block_reason()
        if operation_reason is not None:
            return operation_reason
        if self.last_live_preflight is None or not self.last_live_preflight.ok:
            return "Click Check PVs before applying design K1 targets."
        try:
            request = self._design_k1_request()
        except Exception as exc:
            return str(exc)
        changes = {
            name: request.target_values[name] - request.baseline_values[name]
            for name in request.target_values
        }
        if all(abs(change) <= 1.0e-15 for change in changes.values()):
            return "The selected quadrupoles are already at their design K1 values."
        for name, change in changes.items():
            if abs(change) > request.max_changes[name] + 1.0e-15:
                return (
                    f"{name} requires ΔK1={change:+.8g}, exceeding the configured "
                    f"limit ±{request.max_changes[name]:.8g}."
                )
        return None

    def _apply_design_k1(self) -> None:
        block_reason = self._design_k1_block_reason()
        if block_reason is not None:
            QMessageBox.warning(self, "Apply Design K1", block_reason)
            return
        request = self._design_k1_request()
        pv_map = self.config.backend.options.get("pv_map", {})
        quadrupoles = (
            pv_map.get("quadrupoles", {}) if isinstance(pv_map, dict) else {}
        )
        lines = [
            "Apply lattice design K1 to the active dispersion-correction quadrupoles?",
            "",
        ]
        for name, target in request.target_values.items():
            current = request.baseline_values[name]
            change = target - current
            lines.append(
                f"  {name}: {current:.8g} → {target:.8g} 1/m² "
                f"(ΔK1 {change:+.8g}, limit ±{request.max_changes[name]:.8g})"
            )
            mapping = quadrupoles.get(name, {})
            if isinstance(mapping, dict):
                pv = mapping.get("K1_set") or mapping.get("K1")
                if pv:
                    lines.append(f"    PV: {pv}")
        lines.extend(
            [
                "",
                "Connections and reviewed baselines will be checked again before writing.",
                "Any PV write/verification, cancellation, or orbit-safety failure restores "
                "the complete pre-write snapshot.",
                "After success, Current K1 model is recalculated automatically.",
                "",
                "Proceed?",
            ]
        )
        answer = QMessageBox.question(
            self,
            "Confirm Design K1 Targets",
            "\n".join(lines),
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return
        self._start_task(
            "design-k1",
            design_k1_request=request,
        )

    def _show_model_response(self, response: ModelResponseResult) -> None:
        self.model_empty_label.setVisible(False)
        self.model_table.setVisible(True)
        self.model_info.setVisible(True)
        self.model_table.setRowCount(len(response.device_names))
        self.model_table.setColumnCount(4)
        self.model_table.setHorizontalHeaderLabels(
            ["Quadrupole", "Selected K1", "Design K1", "Design-reference ΔK1"]
        )
        for row, device in enumerate(response.device_names):
            self.model_table.setItem(row, 0, QTableWidgetItem(device))
            self.model_table.setItem(
                row, 1, QTableWidgetItem(f"{response.selected_k1[device]:.8g}")
            )
            self.model_table.setItem(
                row, 2, QTableWidgetItem(f"{response.design_k1[device]:.8g}")
            )
            self.model_table.setItem(
                row,
                3,
                QTableWidgetItem(f"{response.design_reference_deltas[device]:+.8g}"),
            )
        self.model_table.resizeColumnsToContents()
        self.dispersion_curve.set_result(response)
        self.model_info.setPlainText(format_model_response(response))
        has_snapshot = response.model_source != "design"
        if has_snapshot:
            self.show_snapshot_model_checkbox.setChecked(True)
        else:
            self.show_design_model_checkbox.setChecked(True)
            self.show_snapshot_model_checkbox.setChecked(False)
        self._model_visibility_changed()
        self._report_unmapped_plot_bpms()

    def _show_result(self, result: CorrectionResult) -> None:
        self._show_measurement(result.final)
        self._set_live_comparison_measurement(
            result.final,
            label="Final measured",
            reference=result.initial,
        )
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
        if self.config.section.model_only:
            access = "MODEL ONLY"
        elif self.config.backend.type.lower() == "offline":
            access = "OFFLINE DEMO" if self.offline_demo else "OFFLINE"
        elif self.config.backend.mode == "write_enabled":
            access = "WRITE ENABLED"
        else:
            access = "READ ONLY"
        access_tone = "danger" if access == "WRITE ENABLED" else "warning"
        if access in {"OFFLINE", "OFFLINE DEMO"}:
            access_tone = "subtle"
        readiness = self.status_strip.items["READINESS"].value_label.text()
        readiness_tone = "success" if readiness in {"READY", "OK"} else "warning"
        if readiness == "NOT READY":
            readiness_tone = "danger"
        result_tone = "success" if last_result in {"Accepted", "Plan ready", "Ready", "Config loaded"} else "subtle"
        if "Fail" in last_result or "Not accepted" in last_result:
            result_tone = "danger"
        self.status_strip.set_value("ACCESS", access, access_tone)
        self.status_strip.set_value("ENERGY STEP", self._energy_step_compact(), "subtle")
        self.status_strip.set_value("READINESS", readiness, readiness_tone)
        self.status_strip.set_value("LAST RESULT", last_result, result_tone)
        self._update_operation_banner()

    def _next_workflow_action(self) -> tuple[str | None, str, str, str]:
        if self.config.section.model_only:
            if self.dispersion_curve.result is None:
                return (
                    "model-design",
                    "Calculate Design Model",
                    "Model-only section",
                    "Calculates and displays the design curve without a measurement or "
                    "machine write.",
                )
            return (
                "model-details",
                "Show Model Details",
                "Model curve available",
                "The model curve is already displayed in the dispersion overview.",
            )
        if self.config.section.diagnostic_only:
            joint = self.config.section.joint_response_analysis
            if joint.enabled and self.latest_plane_measurements:
                return (
                    "joint-response",
                    "Analyze Joint Q Response…",
                    "Two-plane baseline available",
                    "Temporarily scans the configured analysis Q knobs, restores "
                    "them after every scan, and prepares a read-only ηx/ηy preview.",
                )
            return (
                None,
                "Measurement Only",
                "Diagnostic section",
                (
                    "Measure ηx and ηy first. Joint response analysis becomes "
                    "available after a valid baseline measurement."
                    if joint.enabled
                    else "Use Measure Dispersion in the left panel. This section "
                    "has no correction BPMs or quadrupole knobs."
                ),
            )
        if self.config.measurement.plane == "xy":
            if self._joint_correction_enabled():
                block_reason = self._operation_block_reason()
                if block_reason is not None:
                    return (
                        None,
                        "Manual Joint Correction",
                        "Joint correction is unavailable",
                        block_reason.replace("\n", " "),
                    )
                if self.latest_measurement is None:
                    return (
                        None,
                        "Manual Joint Correction",
                        "Joint correction workflow locked",
                        "Measure ηx and ηy before choosing a correction method.",
                    )
                if self.correction_mode is None:
                    return (
                        "select-joint-manual",
                        "Manual Joint Correction",
                        "Choose a correction method",
                        "Manual mode measures the joint Q response and lets you "
                        "review one bounded ηx/ηy recommendation.",
                    )
                if self.latest_joint_response is None:
                    return (
                        "joint-response",
                        "Measure Joint Q Response…",
                        "Manual joint correction selected",
                        "Temporarily scans the configured Q knobs and restores "
                        "the baseline before showing a recommendation.",
                    )
                return (
                    "review-joint",
                    "Review Joint Recommendation…",
                    "Joint recommendation ready",
                    "Review every predicted ηx/ηy target and quadrupole change "
                    "before Apply and Verify.",
                )
            return (
                None,
                "Measurement Only",
                "Two-plane measurement",
                "Use Measure Dispersion in the left panel. Joint Q-response "
                "analysis and correction are not enabled yet.",
            )

        block_reason = self._operation_block_reason()
        if block_reason is not None:
            return (
                None,
                "Manual Correction",
                "Online correction is unavailable",
                block_reason.replace("\n", " "),
            )
        backend_type = self.config.backend.type.lower()
        if backend_type == "epics" and (
            self.last_live_preflight is None or not self.last_live_preflight.ok
        ):
            return (
                None,
                "Manual Correction",
                "Connection check required",
                "Click Check PVs in the Configuration header before "
                "starting an online measurement.",
            )
        if self.latest_measurement is None:
            return (
                None,
                "Manual Correction",
                "Correction workflow locked",
                "Measure dispersion from the left panel before choosing a correction "
                "method.",
            )
        if self.correction_mode is None:
            return (
                "select-manual",
                "Manual Correction",
                "Choose a correction method",
                "Manual correction measures the selected quadrupole response, "
                "then lets you review one recommendation before any target is written.",
            )
        if self.correction_recommendation is None:
            if self.latest_response is None:
                hint = (
                    "Review the configured quadrupole scan range, then explicitly "
                    "start Q-response measurement. Temporary scan settings are restored."
                )
                state = "Manual correction selected"
            else:
                hint = (
                    "Uses the measured response to finish calculating the recommendation. "
                    "No backend write occurs."
                )
                state = "Response measured; recommendation not ready"
            return (
                "prepare",
                (
                    "Measure Q Response…"
                    if self.latest_response is None
                    else "Calculate Recommendation"
                ),
                state,
                hint,
            )
        apply_reason = self._recommendation_apply_block_reason()
        return (
            "review",
            "Review Recommendation…",
            (
                "Recommendation ready"
                if apply_reason is None
                else "Recommendation ready; application is blocked"
            ),
            (
                "Review the predicted dispersion and every quadrupole target, then "
                "choose Apply and Verify in the review window."
                if apply_reason is None
                else apply_reason
            ),
        )

    def _run_next_workflow_action(self) -> None:
        action = str(self.next_action_button.property("workflowAction") or "")
        if action == "select-manual":
            self.correction_mode = "manual"
            self._set_running(False, "")
        elif action == "select-joint-manual":
            self.correction_mode = "manual"
            self._set_running(False, "")
        elif action == "prepare":
            self._prepare_correction()
        elif action == "review":
            self._review_recommendation()
        elif action == "model-design":
            if self.dispersion_curve.result is None:
                if self.show_design_model_checkbox.isChecked():
                    self._start_model_response(
                        model_source="design",
                        focus_comparison=False,
                    )
                else:
                    self.show_design_model_checkbox.setChecked(True)
            self._show_workflow_detail(self.model_page)
        elif action == "model-details":
            self._show_workflow_detail(self.model_page)
        elif action == "joint-response":
            answer = QMessageBox.question(
                self,
                "Joint Q Response Analysis",
                "This analysis temporarily scans the configured quadrupole knobs "
                "in both directions and restores the initial state. It only "
                "previews a recommendation and cannot apply it.\n\nStart now?",
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if answer == QMessageBox.Yes:
                self._start_task("joint-response")
        elif action == "review-joint":
            self._show_response_details()

    def _workflow_summary_text(self) -> str:
        if self.config.section.model_only:
            if self.dispersion_curve.result is None:
                return "Read-only model workflow · no energy scan or machine write"
            return "Model reference available · no energy scan or machine write"
        if self.config.section.diagnostic_only:
            measurement = self.latest_measurement
            if measurement is None:
                return (
                    f"Measurement-only diagnostics · "
                    f"{len(self.config.monitor_bpms)} monitor BPMs"
                )
            measurements = tuple(
                self.latest_plane_measurements.values()
            ) or (measurement,)
            rms = " · ".join(
                f"η{item.plane} RMS {item.measured_rms_mm:.4g} mm"
                for item in measurements
            )
            valid = int(np.count_nonzero(measurement.valid))
            return (
                f"{rms} · "
                f"{valid}/{len(measurement.bpm_names)} monitor BPMs valid"
            )
        if self._joint_correction_enabled():
            if self.latest_joint_response is not None:
                result = self.latest_joint_response
                return (
                    f"Joint normalized RMS "
                    f"{result.normalized_rms_before:.4g} → "
                    f"{result.normalized_rms_after:.4g} · "
                    f"{result.retained_rank}/{min(result.matrix.shape)} "
                    "effective modes"
                )
            if self.latest_plane_measurements:
                return " · ".join(
                    f"η{item.plane} RMS {item.measured_rms_mm:.4g} mm"
                    for item in self.latest_plane_measurements.values()
                )
            return "Measure ηx and ηy before joint correction"
        recommendation = self.correction_recommendation
        if recommendation is not None:
            target_count = len(recommendation.device_deltas)
            retained, required, _targets, knobs = response_mode_counts(
                recommendation.response,
                self.config.solver.svd_cut,
            )
            return (
                f"Predicted residual RMS "
                f"{recommendation.measurement.rms_mm:.4g} → "
                f"{recommendation.predicted_rms_mm:.4g} mm · "
                f"{target_count} quadrupole target(s) · "
                f"{knobs} knobs · {retained}/{required} effective modes"
            )
        response = self.latest_response
        if response is not None:
            retained, required, _targets, knobs = response_mode_counts(
                response,
                self.config.solver.svd_cut,
            )
            return (
                f"Baseline residual RMS {response.measurement.rms_mm:.4g} mm · "
                f"{knobs} knobs · {retained}/{required} effective modes · "
                f"condition {response.condition_number:.4g}"
            )
        measurement = self.latest_measurement
        if measurement is not None:
            target_valid = int(
                np.count_nonzero(
                    measurement.valid & measurement.target_mask
                )
            )
            target_count = int(np.count_nonzero(measurement.target_mask))
            monitor_count = int(np.count_nonzero(~measurement.target_mask))
            monitor_summary = (
                f" · {monitor_count} monitor BPM(s)"
                if monitor_count
                else ""
            )
            return (
                f"Measured residual RMS {measurement.rms_mm:.4g} mm · "
                f"{target_valid}/{target_count} correction BPMs valid"
                f"{monitor_summary}"
            )
        return (
            f"Energy step {self._energy_step_compact()} · "
            f"{self.samples_per_step_spin.value()} scan samples/setting"
        )

    def _joint_correction_enabled(self) -> bool:
        return bool(
            not self.config.section.diagnostic_only
            and self.config.measurement.plane == "xy"
            and self.config.section.joint_response_analysis.enabled
        )

    def _update_workflow_auxiliary_actions(self, running: bool) -> None:
        diagnostic_only = self.config.section.diagnostic_only
        measurement_only = (
            diagnostic_only
            or (
                self.config.measurement.plane == "xy"
                and not self._joint_correction_enabled()
            )
        )
        manual_mode = self.correction_mode == "manual"
        has_response = (
            manual_mode and self.latest_response is not None
        ) or self.latest_joint_response is not None
        has_history = bool(self.correction_session_runs)
        self.back_to_correction_methods_button.setVisible(
            manual_mode
            and not self.config.section.model_only
            and not measurement_only
        )
        self.back_to_correction_methods_button.setEnabled(
            not running and manual_mode
        )
        self.response_details_button.setVisible(has_response)
        self.response_details_button.setEnabled(not running and has_response)
        self.history_button.setEnabled(not running and has_history)
        self.history_button.setVisible(not measurement_only)
        self.history_button.setToolTip(
            "Review manual and automatic correction runs by generation."
            if has_history
            else "No correction history is available yet."
        )
        has_restore = (
            self.correction_restore_request is not None
            and self.config.backend.type.lower() == "epics"
            and self.config.backend.mode == "write_enabled"
            and not self.config.section.model_only
        )
        self.restore_initial_state_button.setVisible(has_restore)
        self.restore_initial_state_button.setEnabled(
            not running and has_restore
        )
        self.restore_initial_state_button.setToolTip(
            "Restore the quadrupole values saved immediately before the latest "
            "successful correction."
            if has_restore
            else "No successful online correction is available to restore."
        )

    def _update_next_workflow_action(self, running: bool, task: str) -> None:
        self.workflow_summary_label.setText(self._workflow_summary_text())
        self._update_workflow_auxiliary_actions(running)
        if running:
            if task in {"run", "joint-run"}:
                self.next_action_button.hide()
                self.run_button.show()
            elif task in {
                "response",
                "joint-response",
                "apply",
                "joint-apply",
            }:
                self.next_action_button.show()
                self.run_button.hide()
            else:
                self.next_action_button.hide()
                self.run_button.hide()
            labels = {
                "preflight": ("Manual Correction", "Checking connections"),
                "measure": ("Manual Correction", "Dispersion measurement running"),
                "response": (
                    "Preparing Manual Correction…",
                    "Measuring Q response and preparing recommendation",
                ),
                "joint-response": (
                    "Analyzing Joint Q Response…",
                    "Scanning ηx/ηy quadrupole response",
                ),
                "joint-apply": (
                    "Applying Joint Correction…",
                    "Applying and verifying ηx/ηy targets",
                ),
                "joint-run": (
                    "Automatic Joint Correction",
                    "Automatic ηx/ηy correction running",
                ),
                "apply": ("Applying and Verifying…", "Reviewed correction running"),
                "run": ("Manual Correction", "Automatic correction running"),
                "model-response": ("Calculating Model…", "Model analysis running"),
                "restore-correction": (
                    "Manual Correction",
                    "Restoring pre-correction quadrupole state",
                ),
            }
            button_text, state_text = labels.get(
                task,
                ("Operation Running…", "Operation in progress"),
            )
            self.next_action_button.setProperty("workflowAction", "")
            self.next_action_button.setText(button_text)
            self.next_action_button.setEnabled(False)
            self.next_action_button.setVisible(
                task in {
                    "response",
                    "joint-response",
                    "apply",
                    "joint-apply",
                }
            )
            self.workflow_state_label.setText(state_text)
            self.workflow_hint_label.setText(
                "Wait for the current operation to finish or use Abort when available."
            )
            self.next_action_button.setToolTip(
                "Wait for the current operation to finish or use Abort."
            )
            return
        action, button_text, state_text, hint = self._next_workflow_action()
        self.next_action_button.setProperty("workflowAction", action or "")
        self.next_action_button.setText(button_text)
        self.next_action_button.setEnabled(action is not None)
        model_only = self.config.section.model_only
        diagnostic_only = self.config.section.diagnostic_only
        measurement_only = (
            diagnostic_only
            or (
                self.config.measurement.plane == "xy"
                and not self._joint_correction_enabled()
            )
        )
        manual_mode = self.correction_mode == "manual"
        automatic_mode = self.correction_mode == "automatic"
        self.next_action_button.setVisible(
            (
                action == "joint-response"
                or (
                    not measurement_only
                    and (model_only or not automatic_mode)
                )
            )
        )
        self.run_button.setVisible(
            not model_only
            and not measurement_only
            and not manual_mode
        )
        self.next_action_button.setToolTip(hint)
        self.workflow_state_label.setText(state_text)
        self.workflow_hint_label.setText(hint)

    def _update_measurement_action(self, running: bool, task: str) -> None:
        model_only = self.config.section.model_only
        diagnostic_only = self.config.section.diagnostic_only
        self.measurement_action_button.setVisible(not model_only)
        self.measurement_status_label.setVisible(not model_only)
        if model_only:
            return
        connection_ready = (
            self.config.backend.type.lower() != "epics"
            or (
                self.last_live_preflight is not None
                and self.last_live_preflight.ok
            )
        )
        block_reason = self._operation_block_reason()
        if running and task == "measure":
            self.measurement_action_button.setText("Measuring Dispersion…")
            self.measurement_status_label.setText(
                "Energy scan in progress; the previous valid curve remains visible."
            )
        else:
            self.measurement_action_button.setText(
                "Remeasure Dispersion"
                if self.latest_measurement is not None
                else "Measure Dispersion"
            )
            if self.latest_measurement is None:
                self.measurement_status_label.setText(
                    "No valid dispersion measurement"
                )
            else:
                measured_at = (
                    self.latest_measurement_time.strftime("%H:%M:%S")
                    if self.latest_measurement_time is not None
                    else "current session"
                )
                if (
                    diagnostic_only
                    and len(self.latest_plane_measurements) > 1
                ):
                    rms_text = " · ".join(
                        f"η{item.plane} "
                        f"{item.measured_rms_mm:.4g} mm"
                        for item in self.latest_plane_measurements.values()
                    )
                else:
                    displayed_rms = (
                        self.latest_measurement.measured_rms_mm
                        if diagnostic_only
                        else self.latest_measurement.rms_mm
                    )
                    rms_text = f"RMS {displayed_rms:.4g} mm"
                self.measurement_status_label.setText(
                    f"{rms_text} · {measured_at}"
                )
        if running:
            tooltip = "Another operation is running."
        elif block_reason is not None:
            tooltip = block_reason
        elif not connection_ready:
            tooltip = "Click Check PVs before measuring dispersion."
        else:
            tooltip = (
                "Run the configured ±energy scan and update the persistent "
                "dispersion plot."
            )
        self.measurement_action_button.setEnabled(
            not running
            and block_reason is None
            and connection_ready
        )
        self.measurement_action_button.setToolTip(tooltip)

    def _set_running(self, running: bool, task: str) -> None:
        self._active_task = task if running else ""
        profile_managed = self.app_context is not None
        block_reason = self._operation_block_reason()
        operation_allowed = block_reason is None
        self.load_button.setEnabled(not running and not profile_managed)
        for widget in (
            self.section_combo,
            self.bpm_select_button,
            self.knob_select_button,
            self.delta_spin,
            self.samples_per_step_spin,
            self.settle_time_spin,
            self.sample_interval_spin,
            self.final_samples_spin,
            self.max_iter_spin,
            self.gain_spin,
            self.max_step_pct_spin,
            self.response_update_combo,
        ):
            widget.setEnabled(not running)
        self.calibration_button.setEnabled(
            not running and not self.config.section.model_only
        )
        self.energy_calibration_controls.setVisible(
            not self.config.section.model_only
        )
        self.restore_calibration_button.setVisible(
            not self.config.section.model_only
            and self.session_energy_calibration_source is not None
        )
        self.restore_calibration_button.setEnabled(
            not running
            and not self.config.section.model_only
            and self.session_energy_calibration_source is not None
        )
        connection_available = (
            self.config.backend.type.lower() == "epics"
            and not self.config.section.model_only
        )
        self.preflight_button.setVisible(connection_available)
        self.preflight_button.setEnabled(not running and connection_available)
        self.model_response_button.setEnabled(
            not running and self._model_analysis_available()
        )
        self.refresh_snapshot_button.setVisible(
            self._model_analysis_available() and self.app_context is not None
        )
        self.refresh_snapshot_button.setEnabled(
            not running
            and self._model_analysis_available()
            and self.app_context is not None
        )
        self.model_source_combo.setEnabled(not running)
        self.show_design_model_checkbox.setEnabled(
            not running and self._model_analysis_available()
        )
        self.show_snapshot_model_checkbox.setEnabled(
            not running and self._model_analysis_available()
        )
        design_k1_reason = (
            "Another operation is running."
            if running
            else self._design_k1_block_reason()
        )
        self.apply_design_k1_button.setEnabled(
            not running and design_k1_reason is None
        )
        self.apply_design_k1_button.setToolTip(
            design_k1_reason
            or (
                "Review and write lattice design K1 values for the active "
                "correction quadrupoles."
            )
        )
        if design_k1_reason is None:
            self.design_k1_status_label.clear()
            self.design_k1_status_label.hide()
        else:
            self.design_k1_status_label.setText(design_k1_reason)
            self.design_k1_status_label.show()
        self._update_plot_state(running=running, task=task)
        correction_enabled = not (
            self.config.section.model_only
            or self.config.section.diagnostic_only
            or (
                self.config.measurement.plane == "xy"
                and not self._joint_correction_enabled()
            )
        )
        self.measure_button.setEnabled(not running and operation_allowed)
        self.response_button.setEnabled(
            not running and operation_allowed and correction_enabled
        )
        automatic_visible = correction_enabled
        automatic_connection_ready = (
            self.config.backend.type.lower() != "epics"
            or (
                self.last_live_preflight is not None
                and self.last_live_preflight.ok
            )
        )
        automatic_response_reason = automatic_response_block_reason(
            self.latest_response,
            self.config.solver.svd_cut,
        )
        self.run_button.setVisible(automatic_visible)
        if running and task in {"run", "joint-run"}:
            self.run_button.setText("Automatic Correction · 0%")
        elif not running:
            self.run_button.setText("Automatic Correction…")
        self.run_button.setEnabled(
            not running
            and operation_allowed
            and automatic_connection_ready
            and automatic_visible
            and self.latest_measurement is not None
            and automatic_response_reason is None
        )
        recommendation_inputs_ready = (
            correction_enabled
            and self.latest_measurement is not None
            and self.latest_response is not None
        )
        self.review_button.setEnabled(not running and recommendation_inputs_ready)
        self.compute_recommendation_button.setEnabled(
            not running and recommendation_inputs_ready
        )
        apply_reason = self._recommendation_apply_block_reason()
        self.apply_recommendation_button.setEnabled(
            not running and apply_reason is None
        )
        action_tooltip = (
            "Another operation is running."
            if running
            else block_reason
            or "This operation changes machine settings and performs live safety checks."
        )
        for button in (self.measure_button, self.response_button):
            button.setToolTip(action_tooltip)
        if running:
            automatic_tooltip = "Another operation is running."
        elif block_reason is not None:
            automatic_tooltip = block_reason
        elif not automatic_connection_ready:
            automatic_tooltip = (
                "Click Check PVs before starting automatic correction."
            )
        elif self.latest_measurement is None:
            automatic_tooltip = (
                "Measure dispersion before starting automatic correction."
            )
        elif automatic_response_reason is not None:
            automatic_tooltip = automatic_response_reason
        else:
            automatic_tooltip = self._automatic_correction_settings_tooltip()
        self.run_button.setToolTip(automatic_tooltip)
        recommendation_tooltip = (
            "Another operation is running."
            if running
            else (
                "Uses the measured dispersion and Q response. Calculation does not "
                "access or write the backend."
                if recommendation_inputs_ready
                else "Measure the Q response first."
            )
        )
        self.review_button.setToolTip(recommendation_tooltip)
        self.compute_recommendation_button.setToolTip(recommendation_tooltip)
        self.apply_recommendation_button.setToolTip(
            "Another operation is running."
            if running
            else apply_reason
            or (
                "Applies exactly the reviewed targets, remeasures dispersion, and "
                "restores the pre-apply snapshot if the step is rejected."
            )
        )
        self.preflight_button.setToolTip(
            "Another operation is running."
            if running and connection_available
            else (
                "Read-only check: reads all configured PVs without changing any "
                "setpoint."
                if connection_available
                else "Connection checks are only used by online EPICS workflows."
            )
        )
        abortable = running and task != "preflight"
        self.abort_button.setEnabled(abortable)
        self.abort_button.setVisible(abortable)
        self.progress_widget.setVisible(running)
        self._update_measurement_action(running, task)
        self._update_next_workflow_action(running, task)
        self._update_operation_banner()
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
        if self._active_task in {"run", "joint-run"}:
            iteration_match = re.search(
                r"(?:Iteration|Generation)\s+(\d+)/(\d+)",
                stage,
            )
            if iteration_match is not None:
                generation = (
                    f"Gen {iteration_match.group(1)}/"
                    f"{iteration_match.group(2)}"
                )
            elif "Final" in stage:
                generation = "Final"
            elif "Initial" in stage:
                generation = "Initial"
            else:
                generation = ""
            parts = ["Automatic"]
            if generation:
                parts.append(generation)
            if "·" in stage:
                phase = stage.split("·", 1)[1].strip()
                if phase:
                    parts.append(phase)
            parts.append(f"{percent}%")
            progress_text = " · ".join(parts)
            self.run_button.setText(progress_text)
            self.run_button.setToolTip(f"{stage} · {percent}%")

    def _energy_step_changed(self, _value: float) -> None:
        self._update_energy_step_summary()
        if self._loading_widgets:
            return
        self.last_live_preflight = None
        self._selection_changed()

    def _energy_step_plan(self) -> dict[str, object]:
        delta = (
            float(self.delta_spin.value())
            if hasattr(self, "delta_spin")
            else self.config.energy_knob.delta
        )
        if is_direct_delta_actuator(self.config.energy_knob.actuator):
            return {
                "calibrated": True,
                "actuator_step": delta,
                "plus_offset": delta,
                "minus_offset": -delta,
            }
        return actuator_step_for_delta(delta, self.config.energy_knob.calibration)

    def _energy_step_compact(self) -> str:
        delta = (
            float(self.delta_spin.value())
            if hasattr(self, "delta_spin")
            else self.config.energy_knob.delta
        )
        if self.config.section.model_only:
            return "NOT USED"
        if self.config.backend.type.lower() == "offline":
            return f"SIM ±{delta:g} Δp/p"
        plan = self._energy_step_plan()
        if not plan.get("calibrated"):
            return f"±{delta:g} Δp/p"
        step = abs(float(plan["actuator_step"]))
        return f"±{delta:g} / ±{step:g} {self.config.energy_knob.actuator_unit}"

    def _update_energy_step_summary(self) -> None:
        if not hasattr(self, "energy_step_summary"):
            return
        delta = float(self.delta_spin.value())
        if self.config.section.model_only:
            self.energy_step_summary.setText(
                "Model analysis calculates dispersion directly.\n"
                "No energy scan or energy-knob write is used."
            )
            return
        if self.config.backend.type.lower() == "offline":
            self.energy_step_summary.setText(
                f"Simulated energy step: ±{delta:g} Δp/p\n"
                "Software calculation only; no backend or PV write."
            )
            return
        plan = self._energy_step_plan()
        lines = [
            f"{self.config.energy_knob.name} · {self.config.energy_knob.actuator}",
        ]
        if plan.get("calibrated"):
            plus = float(plan["plus_offset"])
            minus = float(plan["minus_offset"])
            unit = self.config.energy_knob.actuator_unit
            lines.append(
                f"+{delta:g} Δp/p → {plus:+g} {unit}; "
                f"-{delta:g} Δp/p → {minus:+g} {unit}"
            )
        else:
            lines.append(
                f"±{delta:g} Δp/p · actuator calibration is incomplete"
            )
        self.energy_step_summary.setText("\n".join(lines))

    def _update_operation_banner(self) -> None:
        if not hasattr(self, "operation_banner"):
            return
        visible = True
        if self.progress_widget.isVisible():
            text = "Operation in progress. Abort restores the operation snapshot."
            tone = "warning"
        elif self.config.section.model_only:
            text = (
                "Model-only section: use “Model / Import”. Online energy modulation "
                "and correction are unavailable."
            )
            tone = "warning"
            visible = False
        elif self.config.backend.type.lower() == "offline":
            text = "Offline demonstration: no live machine PVs are connected."
            tone = "subtle"
            visible = False
        else:
            static = run_preflight(self.config)
            if not static.ok:
                text = "Configuration is not ready: " + static.blockers[0]
                tone = "danger"
            elif (
                self.last_live_preflight is not None
                and not self.last_live_preflight.ok
            ):
                text = "Live checks failed. Machine operations remain blocked."
                tone = "danger"
            elif self.config.backend.mode != "write_enabled":
                text = (
                    "Online readback is available. This profile is READ ONLY, so "
                    "dispersion measurement and correction cannot change the energy actuator."
                )
                tone = "warning"
                visible = False
            elif self.last_live_preflight is None:
                text = "Write access is enabled. Check connections before any machine operation."
                tone = "warning"
                visible = False
            else:
                text = "Live checks passed. Review the energy step before starting an operation."
                tone = "success"
                visible = False
        self.operation_banner.setText(text)
        self.operation_banner.setVisible(visible)
        self.operation_banner.setProperty("tone", tone)
        self.operation_banner.style().unpolish(self.operation_banner)
        self.operation_banner.style().polish(self.operation_banner)
        self.operation_banner.update()

    def _format_live_preflight(self, result) -> str:
        lines = [
            "Connection and Readback Check",
            "",
            f"Result: {'READY' if result.ok else 'NOT READY'}",
            "Write activity: none — no setpoint was changed",
        ]
        readings = result.readings
        attempt = int(readings.get("live_preflight_attempt", 1))
        attempts_allowed = int(
            readings.get("live_preflight_attempts_allowed", attempt)
        )
        if attempts_allowed > 1:
            lines.append(
                f"Read attempts: {attempt}/{attempts_allowed}"
            )
        energy = readings.get("energy_value")
        if energy is not None:
            lines.extend(["", f"Energy actuator: {energy:.8g} Δp/p"])

        readbacks = readings.get("quadrupole_readbacks", {})
        setpoints = readings.get("quadrupole_setpoints", {})
        if readbacks:
            lines.extend(["", "Quadrupoles:"])
            for name, readback in readbacks.items():
                setpoint = setpoints.get(name)
                setpoint_text = "-" if setpoint is None else f"{float(setpoint):.8g}"
                lines.append(
                    f"  {name}: set={setpoint_text}, readback={float(readback):.8g}"
                )

        bpms = readings.get("bpms", {})
        if bpms:
            lines.extend(["", "BPMs:"])
            for name, values in bpms.items():
                lines.append(
                    f"  {name}: x={values.get('x_mm')}, y={values.get('y_mm')}, "
                    f"valid={values.get('valid')}"
                )

        blockers = [*result.static.blockers, *result.blockers]
        warnings = [*result.static.warnings, *result.warnings]
        if blockers:
            lines.extend(["", "Blockers:", *(f"  - {item}" for item in blockers)])
        if warnings:
            lines.extend(["", "Warnings:", *(f"  - {item}" for item in warnings)])
        return "\n".join(lines) + "\n"

    def _refresh_operation_plan(self) -> None:
        try:
            self.config = self._config_from_widgets() if hasattr(self, "bpm_edit") else self.config
            self.operation_plan = build_operation_plan(self.config)
        except Exception as exc:
            self.operation_plan = None
            if hasattr(self, "log_view"):
                self._append_log(f"Operation plan validation failed: {exc}")

    def _update_calibration_controls(self) -> None:
        calibration = self.config.energy_knob.calibration
        if self.session_energy_calibration_source is not None:
            source = "Session override"
            tone = "warning"
        elif is_direct_delta_actuator(self.config.energy_knob.actuator):
            source = "Not required"
            tone = "subtle"
        elif not calibration:
            source = "Missing"
            tone = "danger"
        elif self.app_context is not None:
            source = "Machine profile"
            tone = "success"
        else:
            source = "Loaded configuration"
            tone = "success"
        self.calibration_status_label.setText(f"Calibration: {source}")
        self.calibration_status_label.setVisible(
            source in {"Session override", "Missing"}
        )
        tooltip_lines = [
            f"Actuator: {self.config.energy_knob.actuator}",
            f"Unit: {self.config.energy_knob.actuator_unit}",
            f"Target momentum perturbation: {self.config.energy_knob.delta:g} dp/p",
        ]
        if self.session_energy_calibration_source is not None:
            tooltip_lines.extend(
                (
                    "Source: current session override",
                    "The machine profile was not modified.",
                    f"Draft: {self.session_energy_calibration_source}",
                )
            )
        elif self.app_context is not None:
            tooltip_lines.append("Source: configured machine profile")
        else:
            tooltip_lines.append("Source: loaded configuration")
        if calibration:
            for key, value in calibration.items():
                tooltip_lines.append(f"{key}: {value}")
        else:
            tooltip_lines.append("Calibration data: missing")
        self.calibration_status_label.setToolTip("\n".join(tooltip_lines))
        self.calibration_status_label.setProperty("tone", tone)
        self.calibration_status_label.style().unpolish(
            self.calibration_status_label
        )
        self.calibration_status_label.style().polish(
            self.calibration_status_label
        )
        self.restore_calibration_button.setText(
            "Restore Profile" if self.app_context is not None else "Restore Configured"
        )

    def _open_calibration_editor(self) -> None:
        if self.config.section.model_only:
            return
        machine_id = (
            self.app_context.profile.machine.id
            if self.app_context is not None
            else "standalone"
        )
        backend = (
            self.app_context.control_backend.name
            if self.app_context is not None
            else self.config.backend.type
        )
        dialog = CalibrationEditorDialog(
            actuator=self.config.energy_knob.actuator,
            actuator_unit=self.config.energy_knob.actuator_unit,
            target_delta=self.config.energy_knob.delta,
            draft_directory=energy_calibration_draft_directory(self.app_context),
            machine_id=machine_id,
            backend=backend,
            parent=self,
        )
        if dialog.exec_() != QDialog.Accepted:
            return
        if (
            dialog.activated_calibration is None
            or dialog.activated_source is None
        ):
            return
        self._activate_session_calibration(
            dialog.activated_calibration,
            dialog.activated_source,
        )

    def _activate_session_calibration(
        self,
        calibration: dict,
        source: str,
    ) -> None:
        base_config = self._config_from_widgets()
        self.config = replace(
            base_config,
            energy_knob=replace(
                base_config.energy_knob,
                calibration=dict(calibration),
            ),
        )
        self.session_energy_calibration_source = str(source)
        self.correction_restore_request = None
        self._invalidate_staged_results(
            "Session energy calibration activated. Previous measurements and "
            "recommendations were discarded."
        )
        self._refresh_operation_plan()
        self._update_calibration_controls()
        self._update_energy_step_summary()
        self.last_live_preflight = None
        self._update_static_safety_status()
        self._set_running(False, "")
        self._refresh_status("Calibration loaded")

    def _restore_configured_calibration(self) -> None:
        if self.session_energy_calibration_source is None:
            return
        answer = QMessageBox.question(
            self,
            "Restore Configured Calibration",
            "Restore the calibration from the machine profile/configuration?\n\n"
            "This does not write a PV. Existing dispersion measurements, response "
            "matrices, and recommendations will be discarded.",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return
        self._apply_configured_calibration()

    def _apply_configured_calibration(self) -> None:
        base_config = self._config_from_widgets()
        self.config = replace(
            base_config,
            energy_knob=replace(
                base_config.energy_knob,
                calibration=dict(self.configured_energy_calibration),
            ),
        )
        self.session_energy_calibration_source = None
        self.correction_restore_request = None
        self._invalidate_staged_results(
            "Configured energy calibration restored. Previous measurements and "
            "recommendations were discarded."
        )
        self._refresh_operation_plan()
        self._update_calibration_controls()
        self._update_energy_step_summary()
        self.last_live_preflight = None
        self._update_static_safety_status()
        self._set_running(False, "")
        self._refresh_status("Configured calibration restored")

    def _toggle_theme(self) -> None:
        self.theme_name = "control_room" if self.theme_name == "night_shift" else "night_shift"
        self._apply_theme()

    def _apply_theme(self) -> None:
        self.setStyleSheet(build_stylesheet(self.theme_name))
        self.model_dialog.setStyleSheet(build_stylesheet(self.theme_name))
        self.response_dialog.setStyleSheet(build_stylesheet(self.theme_name))
        self.recommendation_dialog.setStyleSheet(
            build_stylesheet(self.theme_name)
        )
        self.last_run_dialog.setStyleSheet(build_stylesheet(self.theme_name))
        self.iteration_history_dialog.setStyleSheet(
            build_stylesheet(self.theme_name)
        )
        self.dispersion_curve.set_theme(self.theme_name)
        self.iteration_history_curve.set_theme(self.theme_name)
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
            self.progress_stage_label.setText("Stopping at a safe point…")
            self.workflow_state_label.setText(
                "Abort requested · waiting for a safe restore boundary"
            )
            self._refresh_status("Aborting")
            restore_target = {
                "measure": "the initial energy setting",
                "response": "the Q-response scan snapshot",
                "joint-response": "the joint Q-response scan snapshot",
                "apply": "the pre-apply machine snapshot",
                "joint-apply": "the pre-apply joint machine snapshot",
                "run": "the automatic-correction start snapshot",
                "joint-run": "the automatic joint-correction snapshot",
                "design-k1": "the pre-write quadrupole snapshot",
                "restore-correction": "the pre-restore quadrupole snapshot",
            }.get(self._active_task, "the operation snapshot")
            self._append_log(
                f"Abort requested; stopping at a safe point and restoring "
                f"{restore_target}"
            )

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
            self.status_strip.set_value("READINESS", "MODEL ONLY", "warning")
        elif self.config.backend.type.lower() == "offline":
            self.status_strip.set_value("READINESS", "READY", "success")
        elif not result.ok:
            self.status_strip.set_value("READINESS", "NOT READY", "danger")
        else:
            self.status_strip.set_value("READINESS", "UNCHECKED", "warning")
        self._update_operation_banner()

    def _start_live_preflight(self) -> None:
        if self.preflight_worker is not None and self.preflight_worker.isRunning():
            return
        try:
            self.config = self._config_from_widgets()
        except Exception as exc:
            QMessageBox.warning(self, "Configuration", str(exc))
            return
        self.last_live_preflight = None
        self.status_strip.set_value("READINESS", "CHECKING", "warning")
        self._append_log(
            "Checking configured energy actuator, quadrupoles, and BPM readbacks; "
            "no setpoint will be changed"
        )
        self.preflight_worker = LivePreflightWorker(self.config)
        self.preflight_worker.completed.connect(self._live_preflight_completed)
        self.preflight_worker.failed.connect(self._live_preflight_failed)
        self.preflight_worker.finished.connect(lambda: self._set_running(False, ""))
        self._set_running(True, "preflight")
        self.preflight_worker.start()

    def _live_preflight_completed(self, result) -> None:
        self._handle_live_preflight_result(result, interactive=True)
        self._set_running(False, "")

    def _workflow_preflight_completed(self, result) -> None:
        self._handle_live_preflight_result(result, interactive=False)

    def _handle_live_preflight_result(self, result, *, interactive: bool) -> None:
        self.last_live_preflight = result
        ready = result.ok
        self.status_strip.set_value(
            "READINESS",
            "READY" if ready else "NOT READY",
            "success" if ready else "danger",
        )
        messages = [*result.static.blockers, *result.blockers]
        warnings = [*result.static.warnings, *result.warnings]
        if not interactive:
            return
        self._append_log(
            "Live preflight diagnostics:\n"
            + self._format_live_preflight(result).rstrip()
        )
        if messages:
            self._append_log("Live preflight blockers: " + "; ".join(messages))
        if warnings:
            self._append_log("Live preflight warnings: " + "; ".join(warnings))
        if ready:
            self._append_log("Live read-only preflight passed; no setpoint was changed")
        if messages:
            details = "\n".join(f"• {item}" for item in messages)
            if warnings:
                details += "\n\nWarnings:\n" + "\n".join(
                    f"• {item}" for item in warnings
                )
            QMessageBox.warning(
                self,
                "Connection Check Failed",
                "The connection check did not pass:\n\n"
                f"{details}\n\nFull diagnostics were written to Log.",
            )
        elif warnings:
            QMessageBox.warning(
                self,
                "Connection Check Warnings",
                "The connection check passed with warnings:\n\n"
                + "\n".join(f"• {item}" for item in warnings)
                + "\n\nFull diagnostics were written to Log.",
            )

    def _live_preflight_failed(self, message: str) -> None:
        self.last_live_preflight = None
        self.status_strip.set_value("READINESS", "NOT READY", "danger")
        self._append_log(f"Live preflight failed: {message}")
        self._set_running(False, "")
        QMessageBox.warning(
            self,
            "Connection Check Failed",
            f"{message}\n\nNo setpoint was changed. See Log for details.",
        )

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
            self.configured_energy_calibration = dict(
                self.config.energy_knob.calibration
            )
            self.session_energy_calibration_source = None
            self.correction_restore_request = None
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
