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
from half_linac.src.apps.dispersion_correction.config import load_config
from half_linac.src.apps.dispersion_correction.dryrun import build_operation_plan
from half_linac.src.apps.dispersion_correction.gui.calibration_editor import (
    CalibrationEditorDialog,
)
from half_linac.src.apps.dispersion_correction.gui.theme import build_stylesheet, theme_tokens
from half_linac.src.apps.dispersion_correction.gui.widgets import StatusStrip
from half_linac.src.apps.dispersion_correction.models import (
    CorrectionRecommendation,
    CorrectionResult,
    DispersionMeasurement,
    ImportedDispersionDataset,
    KnobConfig,
    ModelResponseResult,
    ResponseMatrixResult,
    RunConfig,
)
from half_linac.src.apps.dispersion_correction.recommendation import (
    build_correction_recommendation,
)
from half_linac.src.apps.dispersion_correction.measurement_import import load_dispersion_csv
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

    def __init__(
        self,
        task: str,
        config: RunConfig,
        recommendation: CorrectionRecommendation | None = None,
    ) -> None:
        super().__init__()
        self.task = task
        self.config = config
        self.recommendation = recommendation

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
            elif self.task == "apply":
                if self.recommendation is None:
                    raise ValueError("No reviewed recommendation was supplied")
                result = workflow.apply_recommendation(self.recommendation)
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


class DispersionCurveWidget(QWidget):
    DEFAULT_TOOLTIP = (
        "Measured BPM dispersion is the primary result. Design and current-snapshot "
        "model curves are optional references. Move over the lattice strip for "
        "element details when a model has been analyzed."
    )

    def __init__(self) -> None:
        super().__init__()
        self.result: ModelResponseResult | None = None
        self.measurement: DispersionPlotDataset | None = None
        self.reference_measurement: DispersionPlotDataset | None = None
        self.show_design_model = False
        self.show_snapshot_model = False
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

    def set_measurement(
        self,
        measurement: DispersionPlotDataset | None,
        reference: DispersionPlotDataset | None = None,
    ) -> None:
        self.measurement = measurement
        self.reference_measurement = reference
        self.update()

    def set_model_visibility(self, *, design: bool, snapshot: bool) -> None:
        self.show_design_model = bool(design)
        self.show_snapshot_model = bool(snapshot)
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
        if plot.width() <= 0 or plot.height() <= 0:
            return
        if self.result is None and self.measurement is None:
            painter.drawText(
                plot,
                Qt.AlignCenter,
                "Measure dispersion or import external BPM points to begin",
            )
            return

        displayed_curves: list[np.ndarray] = []
        if self.result is not None and self.show_design_model:
            design_curve = self.result.design_curve or self.result.selected_curve
            displayed_curves.extend((design_curve.dx_mm, design_curve.dy_mm))
        if (
            self.result is not None
            and self.show_snapshot_model
            and self.result.model_source != "design"
        ):
            displayed_curves.extend(
                (self.result.selected_curve.dx_mm, self.result.selected_curve.dy_mm)
            )
        limit = max(
            (
                abs(float(value))
                for curve in displayed_curves
                for value in curve
            ),
            default=0.0,
        )
        for dataset in (self.measurement, self.reference_measurement):
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
            s_by_name = {
                name: float(self.result.selected_curve.s_m[index])
                for index, name in enumerate(self.result.selected_curve.element_names)
            }
        else:
            assert self.measurement is not None
            s_min = 0.0
            s_max = float(max(len(self.measurement.bpm_names) - 1, 1))
            s_by_name = {
                name: float(index)
                for index, name in enumerate(self.measurement.bpm_names)
            }
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
        if self.result is not None and self.show_design_model:
            design_curve = self.result.design_curve or self.result.selected_curve
            for color, curve in (
                (horizontal, design_curve.dx_mm),
                (vertical, design_curve.dy_mm),
            ):
                design_color = QColor(color)
                design_color.setAlpha(150)
                painter.setPen(QPen(design_color, 2, Qt.DotLine))
                painter.drawPolyline(points(design_curve.s_m, curve))
        if (
            self.result is not None
            and self.show_snapshot_model
            and self.result.model_source != "design"
        ):
            for color, curve in (
                (horizontal, self.result.selected_curve.dx_mm),
                (vertical, self.result.selected_curve.dy_mm),
            ):
                snapshot_color = QColor(color)
                snapshot_color.setAlpha(190)
                painter.setPen(QPen(snapshot_color, 2, Qt.DashLine))
                painter.drawPolyline(points(self.result.selected_curve.s_m, curve))

        def draw_measurement(
            dataset: DispersionPlotDataset,
            color: QColor,
            *,
            radius: float,
            line_width: int,
        ) -> None:
            measurement_points = []
            painter.setPen(QPen(color, line_width))
            painter.setBrush(color)
            for bpm, value, sigma, valid in zip(
                dataset.bpm_names,
                dataset.values_mm,
                dataset.sigma_mm,
                dataset.valid,
            ):
                if not bool(valid) or not math.isfinite(float(value)):
                    continue
                if bpm not in s_by_name:
                    continue
                x = plot.left() + (s_by_name[bpm] - s_min) / s_span * plot.width()
                y = plot.center().y() - float(value) / limit * plot.height() / 2.0
                measurement_points.append(QPointF(x, y))
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
                painter.drawEllipse(QPointF(x, y), radius, radius)
            if len(measurement_points) > 1:
                line_color = QColor(color)
                line_color.setAlpha(150)
                painter.setPen(QPen(line_color, line_width))
                painter.drawPolyline(QPolygonF(measurement_points))

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
        painter.setPen(horizontal)
        painter.drawText(plot.left() + 8, plot.top() + 16, "ηx")
        painter.setPen(vertical)
        painter.drawText(plot.left() + 38, plot.top() + 16, "ηy")
        painter.setPen(QColor(tokens["text_muted"]))
        legend_items = []
        if self.measurement is not None:
            legend_items.append(f"{self.measurement.label} ●")
        if self.reference_measurement is not None:
            legend_items.append(f"{self.reference_measurement.label} ○")
        if self.result is not None and self.show_design_model:
            legend_items.append("Design model ···")
        if (
            self.result is not None
            and self.show_snapshot_model
            and self.result.model_source != "design"
        ):
            legend_items.append("Current snapshot --")
        painter.drawText(
            plot.left() + 75,
            plot.top() + 16,
            "  ".join(legend_items),
        )
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
    ) -> None:
        super().__init__()
        install_qt_window_raise_handler(self)
        self.app_context = app_context
        self.setWindowTitle(self._window_title())
        self.setMinimumSize(1120, 760)
        self.resize(1600, 1000)
        self.theme_name = (
            "control_room" if resolve_initial_theme() == "light" else "night_shift"
        )
        self.worker: WorkflowWorker | None = None
        self.preflight_worker: LivePreflightWorker | None = None
        self.model_worker: ModelResponseWorker | None = None
        self.pending_model_source: str | None = None
        self.imported_dispersion: ImportedDispersionDataset | None = None
        self.live_plot_measurement: DispersionPlotDataset | None = None
        self.reference_plot_measurement: DispersionPlotDataset | None = None
        self.latest_measurement: DispersionMeasurement | None = None
        self.latest_response: ResponseMatrixResult | None = None
        self.correction_recommendation: CorrectionRecommendation | None = None
        self.last_live_preflight = None
        self.operation_plan: dict | None = None
        self._loading_widgets = False
        self.config_path: Path | None = None
        self.config = config or default_offline_config()
        self.configured_energy_calibration = dict(self.config.energy_knob.calibration)
        self.session_energy_calibration_source: str | None = None
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
        model_available = self._model_analysis_available()
        self.model_response_button.setVisible(model_available)
        self.model_source_combo.setVisible(model_available)
        self.model_source_label.setVisible(model_available)
        self.model_boundary_label.setVisible(model_available)
        self.show_design_model_checkbox.setVisible(model_available)
        self.show_snapshot_model_checkbox.setVisible(model_available)
        if self.app_context is None:
            return
        self.load_button.hide()
        self.load_button.setToolTip("Runtime configuration is managed by the selected machine profile.")
        self.bpm_edit.setReadOnly(True)
        self.config_title_label.setText("Machine Profile")
        fixed_selection = self.config.section.model_only
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
                ("MACHINE", "-"),
                ("BACKEND", "-"),
                ("ACCESS", "-"),
                ("ENERGY STEP", "-"),
                ("READINESS", "UNCHECKED"),
                ("LAST RESULT", "-"),
            ]
        )
        energy_status = self.status_strip.items["ENERGY STEP"]
        energy_status.setMinimumWidth(160)
        energy_status.value_label.setWordWrap(False)
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
        self.bpm_edit.editingFinished.connect(self._selection_changed)
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
        self.delta_spin.valueChanged.connect(self._energy_step_changed)
        self.energy_step_field_label = self._add_form_row(
            machine_form,
            "Energy Step (Δp/p)",
            self.delta_spin,
        )
        layout.addLayout(machine_form)

        self.energy_step_summary = QLabel()
        self.energy_step_summary.setObjectName("energyStepSummary")
        self.energy_step_summary.setWordWrap(True)
        layout.addWidget(self.energy_step_summary)

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
        layout.addWidget(self.energy_calibration_controls)

        layout.addWidget(self._config_section_label("MEASUREMENT"))
        sampling_form = self._config_form()

        self.samples_per_step_spin = QSpinBox()
        self.samples_per_step_spin.setRange(1, 100)
        self.samples_per_step_spin.setToolTip("BPM samples collected at each measurement step.")
        self.samples_per_step_spin.valueChanged.connect(self._workflow_input_changed)
        self._add_form_row(sampling_form, "Samples/step", self.samples_per_step_spin)

        self.settle_time_spin = QDoubleSpinBox()
        self.settle_time_spin.setDecimals(2)
        self.settle_time_spin.setRange(0.0, 120.0)
        self.settle_time_spin.setSingleStep(0.5)
        self.settle_time_spin.setToolTip("Wait after each machine setting change before reading BPMs.")
        self.settle_time_spin.valueChanged.connect(self._workflow_input_changed)
        self._add_form_row(sampling_form, "Settle Time (s)", self.settle_time_spin)
        layout.addLayout(sampling_form)

        self.advanced_button = QToolButton()
        self.advanced_button.setObjectName("advancedSettingsButton")
        self.advanced_button.setText("Advanced settings")
        self.advanced_button.setCheckable(True)
        self.advanced_button.setChecked(False)
        self.advanced_button.setArrowType(Qt.RightArrow)
        self.advanced_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.advanced_button.toggled.connect(self._toggle_advanced_settings)
        layout.addWidget(self.advanced_button)

        self.advanced_settings = QWidget()
        advanced_layout = QVBoxLayout(self.advanced_settings)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.setSpacing(7)

        advanced_layout.addWidget(self._config_section_label("SAMPLING DETAILS"))
        sampling_details_form = self._config_form()

        self.sample_interval_spin = QDoubleSpinBox()
        self.sample_interval_spin.setDecimals(3)
        self.sample_interval_spin.setRange(0.0, 60.0)
        self.sample_interval_spin.setSingleStep(0.05)
        self.sample_interval_spin.setToolTip("Wait between consecutive BPM samples; no wait follows the final sample.")
        self.sample_interval_spin.valueChanged.connect(self._workflow_input_changed)
        self._add_form_row(sampling_details_form, "Sample Interval (s)", self.sample_interval_spin)

        self.final_samples_spin = QSpinBox()
        self.final_samples_spin.setRange(1, 200)
        self.final_samples_spin.setToolTip("BPM samples used for the final acceptance measurement.")
        self.final_samples_spin.valueChanged.connect(self._workflow_input_changed)
        self._add_form_row(sampling_details_form, "Final Samples", self.final_samples_spin)
        advanced_layout.addLayout(sampling_details_form)

        advanced_layout.addWidget(self._config_section_label("SOLVER"))
        solver_form = self._config_form()

        self.max_iter_spin = QSpinBox()
        self.max_iter_spin.setRange(1, 20)
        self.max_iter_spin.valueChanged.connect(self._workflow_input_changed)
        self._add_form_row(solver_form, "Max Iter", self.max_iter_spin)

        self.gain_spin = QDoubleSpinBox()
        self.gain_spin.setDecimals(3)
        self.gain_spin.setRange(0.001, 1.0)
        self.gain_spin.setSingleStep(0.05)
        self.gain_spin.valueChanged.connect(self._workflow_input_changed)
        self._add_form_row(solver_form, "Gain", self.gain_spin)

        self.max_step_pct_spin = QDoubleSpinBox()
        self.max_step_pct_spin.setDecimals(1)
        self.max_step_pct_spin.setRange(0.1, 100.0)
        self.max_step_pct_spin.setSingleStep(5.0)
        self.max_step_pct_spin.valueChanged.connect(lambda _value: self._update_knob_summary())
        self.max_step_pct_spin.valueChanged.connect(self._workflow_input_changed)
        self._add_form_row(solver_form, "Max Step (%)", self.max_step_pct_spin)

        self.response_update_combo = QComboBox()
        self.response_update_combo.addItems(["once", "every_iteration"])
        self.response_update_combo.currentTextChanged.connect(self._workflow_input_changed)
        self._add_form_row(solver_form, "Response", self.response_update_combo)
        advanced_layout.addLayout(solver_form)
        self.advanced_settings.setVisible(False)
        layout.addWidget(self.advanced_settings)

        self.connection_controls = QWidget()
        connection_layout = QVBoxLayout(self.connection_controls)
        connection_layout.setContentsMargins(0, 0, 0, 0)
        connection_layout.setSpacing(7)
        connection_layout.addWidget(self._config_section_label("CONNECTION"))
        self.preflight_button = QPushButton("Check Connections")
        self.preflight_button.setObjectName("preflightButton")
        self.preflight_button.clicked.connect(self._start_live_preflight)
        connection_layout.addWidget(self.preflight_button)
        layout.addWidget(self.connection_controls)

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
        self.online_page.setMinimumHeight(190)
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
        self.last_run_button = QPushButton("Last Run…")
        self.last_run_button.setObjectName("workflowSecondaryButton")
        self.last_run_button.clicked.connect(self._show_last_run)
        workflow_header.addWidget(self.last_run_button)
        online_layout.addLayout(workflow_header)

        self.workflow_state_label = QLabel("Current state")
        self.workflow_state_label.setObjectName("workflowState")
        self.workflow_state_label.setWordWrap(True)
        online_layout.addWidget(self.workflow_state_label)
        self.workflow_summary_label = QLabel()
        self.workflow_summary_label.setObjectName("workflowSummary")
        self.workflow_summary_label.setWordWrap(True)
        online_layout.addWidget(self.workflow_summary_label)
        self.next_action_button = QPushButton("Check Connections")
        self.next_action_button.setObjectName("nextWorkflowAction")
        self.next_action_button.setProperty("role", "control")
        self.next_action_button.clicked.connect(self._run_next_workflow_action)
        online_layout.addWidget(self.next_action_button)
        self.workflow_hint_label = QLabel()
        self.workflow_hint_label.setObjectName("workflowHint")
        self.workflow_hint_label.setWordWrap(True)
        online_layout.addWidget(self.workflow_hint_label)
        workflow_secondary_actions = QHBoxLayout()
        workflow_secondary_actions.addStretch(1)
        self.response_details_button = QPushButton("Q Response…")
        self.response_details_button.setObjectName("workflowSecondaryButton")
        self.response_details_button.clicked.connect(self._show_response_details)
        workflow_secondary_actions.addWidget(self.response_details_button)
        self.recommendation_details_button = QPushButton("Review Details…")
        self.recommendation_details_button.setObjectName(
            "workflowSecondaryButton"
        )
        self.recommendation_details_button.clicked.connect(
            self._show_recommendation_details
        )
        workflow_secondary_actions.addWidget(self.recommendation_details_button)
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
            ["BPM", "Measured mm", "Target mm", "Residual mm", "Valid"]
        )
        self.measure_page = QWidget()
        measure_layout = QVBoxLayout(self.measure_page)
        measure_layout.setContentsMargins(8, 4, 8, 8)
        measure_title = QLabel("Measured horizontal effective dispersion")
        measure_title.setObjectName("workspaceIntro")
        measure_layout.addWidget(measure_title)
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
            "Review one bounded correction step calculated from the measured dispersion "
            "and measured Q response. Calculation does not access or write the backend."
        )
        correction_title.setObjectName("workspaceIntro")
        correction_title.setWordWrap(True)
        correction_layout.addWidget(correction_title)
        self.correction_state_label = QLabel(
            "Measure the Q response to prepare a correction recommendation."
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
            ["BPM", "Measured mm", "Target mm", "Predicted mm", "Predicted residual mm"]
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
        self.compute_recommendation_button = QPushButton("Compute Recommendation")
        self.compute_recommendation_button.clicked.connect(self._compute_recommendation)
        correction_actions.addWidget(self.compute_recommendation_button)
        correction_actions.addStretch(1)
        self.run_button = QPushButton("Advanced: Automatic Loop")
        self.run_button.clicked.connect(lambda: self._start_task("run"))
        correction_actions.addWidget(self.run_button)
        self.apply_recommendation_button = QPushButton(
            "Apply & Remeasure",
            self.correction_page,
        )
        self.apply_recommendation_button.setProperty("role", "control")
        self.apply_recommendation_button.clicked.connect(
            self._apply_reviewed_recommendation
        )
        self.apply_recommendation_button.hide()
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
            "Measured BPM dispersion is the primary result. Add design or current-"
            "snapshot model curves only when they help explain the measurement."
        )
        model_intro.setObjectName("workspaceIntro")
        model_intro.setWordWrap(True)
        model_layout.addWidget(model_intro)
        measurement_actions = QHBoxLayout()
        measurement_actions.addWidget(QLabel("External measurement"))
        measurement_actions.addStretch(1)
        self.import_measurement_button = QPushButton("Import ηx CSV")
        self.import_measurement_button.clicked.connect(self._import_measurement_csv)
        self.import_measurement_button.setToolTip(
            "Import bpm, etax_mm, and optional etax_sigma_mm columns for comparison only."
        )
        measurement_actions.addWidget(self.import_measurement_button)
        self.clear_measurement_button = QPushButton("Clear External")
        self.clear_measurement_button.clicked.connect(self._clear_imported_measurement)
        measurement_actions.addWidget(self.clear_measurement_button)
        model_layout.addLayout(measurement_actions)

        model_actions = QHBoxLayout()
        self.model_source_label = QLabel("Calculate")
        model_actions.addWidget(self.model_source_label)
        self.model_source_combo = QComboBox()
        self.model_source_combo.addItem("Design lattice", "design")
        if self.app_context is not None:
            backend_name = self.app_context.control_backend.name.lower()
            self.model_source_combo.addItem("Current snapshot", "live")
            snapshot_tooltip = (
                f"Reads quadrupole K1 PVs from the active {backend_name.upper()} backend "
                "without writing machine state."
            )
        else:
            snapshot_tooltip = "Current snapshot requires a machine-profile backend."
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
        model_dialog_actions.addStretch(1)
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

        self.dispersion_overview = QFrame()
        self.dispersion_overview.setObjectName("dispersionOverviewCard")
        overview_layout = QVBoxLayout(self.dispersion_overview)
        overview_layout.setContentsMargins(12, 10, 12, 12)
        overview_layout.setSpacing(4)
        overview_display_row = QHBoxLayout()
        overview_display_row.setSpacing(8)
        self.overview_title_label = QLabel("Dispersion Overview")
        self.overview_title_label.setObjectName("cardTitle")
        overview_display_row.addWidget(self.overview_title_label)
        overview_display_row.addWidget(QLabel("Displayed"))
        self.measurement_source_combo = QComboBox()
        self.measurement_source_combo.setMinimumWidth(180)
        self.measurement_source_combo.addItem("No measurement available", "none")
        self.measurement_source_combo.currentIndexChanged.connect(
            self._comparison_measurement_changed
        )
        overview_display_row.addWidget(self.measurement_source_combo)
        overview_display_row.addStretch(1)
        overview_layout.addLayout(overview_display_row)

        overview_options_row = QHBoxLayout()
        overview_options_row.setSpacing(8)
        self.plot_state_label = QLabel("No measured data")
        self.plot_state_label.setObjectName("modelBoundaryLabel")
        overview_options_row.addWidget(self.plot_state_label)
        overview_options_row.addStretch(1)
        self.show_design_model_checkbox = QCheckBox("Design model")
        self.show_design_model_checkbox.setChecked(False)
        self.show_design_model_checkbox.setToolTip(
            "Calculate and show the read-only design-lattice model. "
            "No dispersion measurement is required."
        )
        self.show_design_model_checkbox.toggled.connect(
            self._model_visibility_changed
        )
        overview_options_row.addWidget(self.show_design_model_checkbox)
        self.show_snapshot_model_checkbox = QCheckBox("Current snapshot")
        self.show_snapshot_model_checkbox.setChecked(False)
        self.show_snapshot_model_checkbox.setEnabled(False)
        self.show_snapshot_model_checkbox.setToolTip(
            "Read the configured quadrupole K1 snapshot and calculate its model curve. "
            "No dispersion measurement or machine write is required."
        )
        self.show_snapshot_model_checkbox.toggled.connect(
            self._model_visibility_changed
        )
        overview_options_row.addWidget(self.show_snapshot_model_checkbox)
        self.model_details_button = QPushButton("Model Details…")
        self.model_details_button.setObjectName("modelDetailsButton")
        self.model_details_button.clicked.connect(self._show_model_details)
        overview_options_row.addWidget(self.model_details_button)
        overview_layout.addLayout(overview_options_row)
        self.dispersion_curve = DispersionCurveWidget()
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
            self._show_last_run()

    def _show_response_details(self) -> None:
        if self.latest_response is None:
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
        self._invalidate_staged_results(
            "Configuration loaded. Measure the Q response before calculating a recommendation."
        )
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
            self._update_energy_step_summary()
            model_only = self.config.section.model_only
            self.energy_step_field_label.setVisible(not model_only)
            self.delta_spin.setVisible(not model_only)
        finally:
            self._loading_widgets = False
        self._refresh_operation_plan()
        self._update_calibration_controls()
        self._update_static_safety_status()
        self._show_workflow_detail(self.online_page)
        self._refresh_status("Config loaded")

    def _update_knob_summary(self) -> None:
        summaries = []
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
        self.configured_energy_calibration = dict(config.energy_knob.calibration)
        self.session_energy_calibration_source = None
        self.selected_knobs = tuple(config.knobs)
        self.knob_hard_limits = tuple(knob.limit for knob in config.knobs)
        self._invalidate_staged_results(
            "Section changed. Previous measurements and recommendations were discarded."
        )
        self.dispersion_curve.set_result(None)
        self.imported_dispersion = None
        self.live_plot_measurement = None
        self.reference_plot_measurement = None
        self.dispersion_curve.set_measurement(None)
        self._refresh_measurement_source_combo()
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
        self.latest_measurement = None
        self.latest_response = None
        self.correction_recommendation = None
        self.live_plot_measurement = None
        self.reference_plot_measurement = None
        if hasattr(self, "measurement_source_combo"):
            self._refresh_measurement_source_combo(preferred="imported")
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

    def _import_measurement_csv(self) -> None:
        response = self.dispersion_curve.result
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import external ηx measurement",
            str(Path.cwd()),
            "CSV files (*.csv)",
            options=QFileDialog.Options() | QFileDialog.DontUseNativeDialog,
        )
        if not path:
            return
        allowed_bpms = (
            tuple(
                name
                for name, element_type in zip(
                    response.selected_curve.element_names,
                    response.selected_curve.element_types,
                )
                if DispersionCurveWidget._is_bpm(name, element_type.upper())
            )
            if response is not None
            else tuple(self.available_bpms or self.config.target_bpms)
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
        self._refresh_measurement_source_combo(preferred="imported")
        self._append_log(
            f"Imported {len(imported.bpm_names)} eta_x point(s) from {imported.source_path}"
        )
        self._refresh_status(f"Imported {len(imported.bpm_names)} ηx points")
        self._set_running(False, "")

    def _clear_imported_measurement(self) -> None:
        self.imported_dispersion = None
        self._refresh_measurement_source_combo(preferred="live")
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
        self._invalidate_staged_results(
            "Configuration changed. Previous measurements and recommendations were discarded."
        )
        self.last_live_preflight = None
        try:
            self.config = self._config_from_widgets()
            self.operation_plan = build_operation_plan(self.config)
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

    def _start_task(
        self,
        task: str,
        recommendation: CorrectionRecommendation | None = None,
    ) -> None:
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
        if task == "apply" and recommendation is None:
            QMessageBox.warning(
                self,
                "Apply Recommendation",
                "Calculate and review a recommendation before applying it.",
            )
            return
        self.worker = WorkflowWorker(task, config, recommendation)
        self.worker.log.connect(self._append_log)
        self.worker.progress.connect(self._update_progress)
        self.worker.preflight.connect(self._live_preflight_completed)
        self.worker.failed.connect(self._task_failed)
        self.worker.completed.connect(self._task_completed)
        self.worker.finished.connect(self._task_finished)
        self._set_running(True, task)
        self._update_progress("Starting", 0, 1)
        self.worker.start()

    def _task_completed(self, task: str, result: object) -> None:
        if isinstance(result, DispersionMeasurement):
            self.latest_measurement = result
            self.latest_response = None
            self.correction_recommendation = None
            self.correction_state_label.setText(
                "Dispersion measured. Measure the Q response to calculate a recommendation."
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
            self.latest_response = result
            self.latest_measurement = result.measurement
            self.correction_recommendation = None
            self.correction_state_label.setText(
                "Measured dispersion and Q response are ready. Review the recommendation."
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
        elif isinstance(result, CorrectionResult):
            self._show_result(result)
            status = "Accepted" if result.success else "Aborted" if result.reason.startswith("Aborted") else "Not accepted"
            self._refresh_status(status)
            self.status_strip.set_value(
                "READINESS",
                result.safety.reason,
                "success" if result.safety.ok else "danger",
            )
            self.latest_measurement = result.final
            self.latest_response = None
            self.correction_recommendation = None
            self.correction_state_label.setText(
                "Execution completed. Measure the Q response again before the next recommendation."
            )
            self.recommendation_summary_label.setText(
                "The reviewed recommendation is no longer current."
            )
            self.recommendation_prediction_table.setRowCount(0)
            self.recommendation_table.setRowCount(0)
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
        self.status_strip.set_value("READINESS", "NOT READY", "danger")
        self._refresh_status("Failed")
        QMessageBox.warning(self, "Workflow", message)

    def _task_finished(self) -> None:
        self._set_running(False, "")

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
        self._show_model_response(result)
        self._refresh_status("Model comparison ready")
        self._append_log(
            f"Model comparison completed from {result.model_source} without machine writes"
        )

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
        )

    @staticmethod
    def _plot_dataset_from_import(
        imported: ImportedDispersionDataset,
    ) -> DispersionPlotDataset:
        return DispersionPlotDataset(
            bpm_names=imported.bpm_names,
            values_mm=np.asarray(imported.etax_mm, dtype=float),
            sigma_mm=np.asarray(imported.etax_sigma_mm, dtype=float),
            valid=np.ones(len(imported.bpm_names), dtype=bool),
            label="External measurement",
        )

    def _set_live_comparison_measurement(
        self,
        measurement: DispersionMeasurement,
        *,
        label: str,
        reference: DispersionMeasurement | None = None,
    ) -> None:
        self.live_plot_measurement = self._plot_dataset_from_measurement(
            measurement,
            label,
        )
        self.reference_plot_measurement = (
            None
            if reference is None
            else self._plot_dataset_from_measurement(reference, "Before correction")
        )
        self._refresh_measurement_source_combo(preferred="live")

    def _refresh_measurement_source_combo(self, preferred: str | None = None) -> None:
        if not hasattr(self, "measurement_source_combo"):
            return
        current = preferred or str(
            self.measurement_source_combo.currentData() or "none"
        )
        self.measurement_source_combo.blockSignals(True)
        self.measurement_source_combo.clear()
        if self.live_plot_measurement is not None:
            self.measurement_source_combo.addItem(
                self.live_plot_measurement.label,
                "live",
            )
        if self.imported_dispersion is not None:
            self.measurement_source_combo.addItem("External measurement", "imported")
        if self.measurement_source_combo.count() == 0:
            self.measurement_source_combo.addItem("No measurement available", "none")
        index = self.measurement_source_combo.findData(current)
        if index < 0:
            index = 0
        self.measurement_source_combo.setCurrentIndex(index)
        self.measurement_source_combo.blockSignals(False)
        self._comparison_measurement_changed()

    def _active_plot_measurement(self) -> DispersionPlotDataset | None:
        source = str(self.measurement_source_combo.currentData() or "none")
        if source == "live":
            return self.live_plot_measurement
        if source == "imported" and self.imported_dispersion is not None:
            return self._plot_dataset_from_import(self.imported_dispersion)
        return None

    def _comparison_measurement_changed(self, _index: int | None = None) -> None:
        measurement = self._active_plot_measurement()
        source = str(self.measurement_source_combo.currentData() or "none")
        reference = (
            self.reference_plot_measurement
            if source == "live"
            else None
        )
        self.dispersion_curve.set_measurement(measurement, reference)
        self._show_measurement_comparison(measurement)
        self._update_plot_state()

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
            }
            self.plot_state_label.setText(messages.get(task, "Operation in progress"))
            return
        measurement = self._active_plot_measurement()
        if measurement is None:
            if self.dispersion_curve.result is None:
                self.plot_state_label.setText("No measured data")
            else:
                self.plot_state_label.setText("Model reference only · no measured data")
            return
        valid_count = int(np.count_nonzero(measurement.valid))
        self.plot_state_label.setText(
            f"{measurement.label} · {valid_count}/{len(measurement.bpm_names)} valid BPMs"
        )

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
        model_columns: list[tuple[str, dict[str, float]]] = []
        if response is not None and self.show_design_model_checkbox.isChecked():
            curve = response.design_curve or response.selected_curve
            model_columns.append(
                (
                    "Design model",
                    {
                        name: float(curve.dx_mm[index])
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
                    "Current snapshot",
                    {
                        name: float(curve.dx_mm[index])
                        for index, name in enumerate(curve.element_names)
                    },
                )
            )
        headers = ["BPM", "Measurement ηx (mm)", "σηx (mm)", "Valid"]
        for label, _values in model_columns:
            headers.extend((f"{label} ηx (mm)", f"Measurement − {label} (mm)"))
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

    def _show_imported_comparison(
        self,
        response: ModelResponseResult,
        imported: ImportedDispersionDataset,
    ) -> None:
        """Compatibility wrapper used by older callers and focused GUI tests."""

        self.imported_dispersion = imported
        self.dispersion_curve.set_result(response)
        self._refresh_measurement_source_combo(preferred="imported")

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

    def _review_recommendation(self) -> None:
        self._show_workflow_detail(self.correction_page)
        if self.correction_recommendation is not None:
            return
        if self.latest_measurement is None or self.latest_response is None:
            self.correction_state_label.setText(
                "Measure the Q response first. That operation also records the current "
                "dispersion used by the recommendation."
            )
            return
        self._compute_recommendation()

    def _compute_recommendation(self) -> None:
        if self.latest_measurement is None or self.latest_response is None:
            QMessageBox.warning(
                self,
                "Correction Recommendation",
                "Measure the Q response before calculating a recommendation.",
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
            "Correction recommendation calculated from measured data; no backend was accessed"
        )
        self._refresh_status("Recommendation ready")
        self._set_running(False, "")

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
        self.correction_state_label.setText(
            "Prediction only — no backend read or write occurred. Review every target "
            "before choosing Apply & Remeasure."
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
            self.recommendation_prediction_table.setItem(
                row, 0, QTableWidgetItem(bpm)
            )
            self.recommendation_prediction_table.setItem(
                row, 1, QTableWidgetItem(f"{measurement.values_mm[row]:.6g}")
            )
            self.recommendation_prediction_table.setItem(
                row,
                2,
                QTableWidgetItem(f"{measurement.target_values_mm[row]:.6g}"),
            )
            self.recommendation_prediction_table.setItem(
                row,
                3,
                QTableWidgetItem(f"{recommendation.predicted_values_mm[row]:.6g}"),
            )
            self.recommendation_prediction_table.setItem(
                row,
                4,
                QTableWidgetItem(
                    f"{recommendation.predicted_residual_values_mm[row]:.6g}"
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
                "Run Check Connections after the most recent configuration change "
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
            "The following reviewed targets will be applied once:",
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
                "It will then remeasure dispersion. If safety checks fail or the RMS "
                "does not improve enough, the pre-apply snapshot is restored.",
                "",
                "Proceed?",
            ]
        )
        return "\n".join(lines)

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
        machine = (
            self.app_context.profile.machine.id.upper()
            if self.app_context is not None
            else "STANDALONE"
        )
        backend = (
            self.app_context.control_backend.name.upper()
            if self.app_context is not None
            else self.config.backend.type.upper()
        )
        backend_tone = "warning" if backend == "VM" else "success"
        if self.config.section.model_only:
            access = "MODEL ONLY"
        elif self.config.backend.type.lower() == "offline":
            access = "OFFLINE"
        elif self.config.backend.mode == "write_enabled":
            access = "WRITE ENABLED"
        else:
            access = "READ ONLY"
        access_tone = "danger" if access == "WRITE ENABLED" else "warning"
        if access == "OFFLINE":
            access_tone = "subtle"
        readiness = self.status_strip.items["READINESS"].value_label.text()
        readiness_tone = "success" if readiness in {"READY", "OK"} else "warning"
        if readiness == "NOT READY":
            readiness_tone = "danger"
        result_tone = "success" if last_result in {"Accepted", "Plan ready", "Ready", "Config loaded"} else "subtle"
        if "Fail" in last_result or "Not accepted" in last_result:
            result_tone = "danger"
        self.status_strip.set_value("MACHINE", machine, "subtle")
        self.status_strip.set_value("BACKEND", backend, backend_tone)
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

        backend_type = self.config.backend.type.lower()
        if backend_type == "epics" and (
            self.last_live_preflight is None or not self.last_live_preflight.ok
        ):
            return (
                None,
                "Measure Dispersion",
                "Connection check required",
                "Run Check Connections in the left configuration panel before "
                "starting an online measurement.",
            )

        block_reason = self._operation_block_reason()
        if block_reason is not None:
            return (
                None,
                "Online Measurement Unavailable",
                "Online correction is unavailable",
                block_reason.replace("\n", " "),
            )
        if self.latest_measurement is None:
            return (
                "measure",
                "Measure Dispersion",
                "Ready to measure dispersion",
                "Runs the configured energy scan and updates the persistent dispersion "
                "plot.",
            )
        if self.latest_response is None:
            return (
                "response",
                "Measure Q Response",
                "Dispersion measured",
                "Measures the selected quadrupole response around the latest dispersion "
                "baseline.",
            )
        if self.correction_recommendation is None:
            return (
                "review",
                "Review Recommendation",
                "Dispersion and Q response ready",
                "Calculates one bounded recommendation from measured data without "
                "accessing the backend.",
            )
        apply_reason = self._recommendation_apply_block_reason()
        return (
            "apply" if apply_reason is None else None,
            "Apply & Remeasure",
            "Recommendation ready for review",
            apply_reason
            or "Applies the reviewed targets once, remeasures dispersion, and restores "
            "the snapshot if the step is rejected.",
        )

    def _run_next_workflow_action(self) -> None:
        action = str(self.next_action_button.property("workflowAction") or "")
        if action == "measure":
            self._start_task("measure")
        elif action == "response":
            self._start_task("response")
        elif action == "review":
            self._review_recommendation()
        elif action == "apply":
            self._apply_reviewed_recommendation()
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

    def _workflow_summary_text(self) -> str:
        if self.config.section.model_only:
            if self.dispersion_curve.result is None:
                return "Read-only model workflow · no energy scan or machine write"
            return "Model reference available · no energy scan or machine write"
        recommendation = self.correction_recommendation
        if recommendation is not None:
            target_count = len(recommendation.device_deltas)
            return (
                f"Predicted residual RMS "
                f"{recommendation.measurement.rms_mm:.4g} → "
                f"{recommendation.predicted_rms_mm:.4g} mm · "
                f"{target_count} quadrupole target(s)"
            )
        response = self.latest_response
        if response is not None:
            return (
                f"Baseline residual RMS {response.measurement.rms_mm:.4g} mm · "
                f"response condition number {response.condition_number:.4g}"
            )
        measurement = self.latest_measurement
        if measurement is not None:
            valid_count = int(np.count_nonzero(measurement.valid))
            return (
                f"Measured residual RMS {measurement.rms_mm:.4g} mm · "
                f"{valid_count}/{len(measurement.bpm_names)} valid BPMs"
            )
        return (
            f"Energy step {self._energy_step_compact()} · "
            f"{self.samples_per_step_spin.value()} samples/step"
        )

    def _update_workflow_auxiliary_actions(self, running: bool) -> None:
        has_response = self.latest_response is not None
        has_recommendation = self.correction_recommendation is not None
        has_last_run = (
            self.correction_table.rowCount() > 0
            or bool(self.report_text.toPlainText())
        )
        self.response_details_button.setVisible(has_response)
        self.response_details_button.setEnabled(not running and has_response)
        self.recommendation_details_button.setVisible(has_recommendation)
        self.recommendation_details_button.setEnabled(
            not running and has_recommendation
        )
        self.last_run_button.setEnabled(not running and has_last_run)
        self.last_run_button.setToolTip(
            "Open the latest correction execution and report."
            if has_last_run
            else "No correction execution is available yet."
        )

    def _update_next_workflow_action(self, running: bool, task: str) -> None:
        self.workflow_summary_label.setText(self._workflow_summary_text())
        self._update_workflow_auxiliary_actions(running)
        if running:
            labels = {
                "preflight": ("Measure Dispersion", "Checking connections"),
                "measure": ("Measuring Dispersion…", "Dispersion measurement running"),
                "response": ("Measuring Q Response…", "Q response measurement running"),
                "apply": ("Applying & Remeasuring…", "Reviewed correction running"),
                "run": ("Automatic Loop Running…", "Automatic correction running"),
                "model-response": ("Calculating Model…", "Model analysis running"),
            }
            button_text, state_text = labels.get(
                task,
                ("Operation Running…", "Operation in progress"),
            )
            self.next_action_button.setProperty("workflowAction", "")
            self.next_action_button.setText(button_text)
            self.next_action_button.setEnabled(False)
            self.workflow_state_label.setText(state_text)
            self.workflow_hint_label.setText(
                "Wait for the current operation to finish or use Abort when available."
            )
            return
        action, button_text, state_text, hint = self._next_workflow_action()
        self.next_action_button.setProperty("workflowAction", action or "")
        self.next_action_button.setText(button_text)
        self.next_action_button.setEnabled(action is not None)
        self.workflow_state_label.setText(state_text)
        self.workflow_hint_label.setText(hint)

    def _set_running(self, running: bool, task: str) -> None:
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
        self.connection_controls.setVisible(connection_available)
        self.preflight_button.setVisible(connection_available)
        self.preflight_button.setEnabled(not running and connection_available)
        self.model_response_button.setEnabled(
            not running and self._model_analysis_available()
        )
        self.model_source_combo.setEnabled(not running)
        self.import_measurement_button.setEnabled(not running)
        self.measurement_source_combo.setEnabled(not running)
        self.show_design_model_checkbox.setEnabled(
            not running and self._model_analysis_available()
        )
        self.show_snapshot_model_checkbox.setEnabled(
            not running and self._model_analysis_available()
        )
        self.clear_measurement_button.setEnabled(
            not running and self.imported_dispersion is not None
        )
        self._update_plot_state(running=running, task=task)
        self.measure_button.setEnabled(not running and operation_allowed)
        self.response_button.setEnabled(not running and operation_allowed)
        self.run_button.setEnabled(not running and operation_allowed)
        recommendation_inputs_ready = (
            self.latest_measurement is not None and self.latest_response is not None
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
        for button in (self.measure_button, self.response_button, self.run_button):
            button.setToolTip(action_tooltip)
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
        self.advanced_button.setEnabled(not running)
        abortable = running and task != "preflight"
        self.abort_button.setEnabled(abortable)
        self.abort_button.setVisible(abortable)
        self.progress_widget.setVisible(running)
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

    def _toggle_advanced_settings(self, checked: bool) -> None:
        self.advanced_settings.setVisible(checked)
        self.advanced_button.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)

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
        if self.progress_widget.isVisible():
            text = "Operation in progress. Abort restores the operation snapshot."
            tone = "warning"
        elif self.config.section.model_only:
            text = (
                "Model-only section: use “Model / Import”. Online energy modulation "
                "and correction are unavailable."
            )
            tone = "warning"
        elif self.config.backend.type.lower() == "offline":
            text = "Offline demonstration: no live machine PVs are connected."
            tone = "subtle"
        else:
            static = run_preflight(self.config)
            if not static.ok:
                text = "Configuration is not ready: " + static.blockers[0]
                tone = "danger"
            elif self.config.backend.mode != "write_enabled":
                text = (
                    "Online readback is available. This profile is READ ONLY, so "
                    "dispersion measurement and correction cannot change the energy actuator."
                )
                tone = "warning"
            elif self.last_live_preflight is None:
                text = "Write access is enabled. Check connections before any machine operation."
                tone = "warning"
            elif not self.last_live_preflight.ok:
                text = "Live checks failed. Machine operations remain blocked."
                tone = "danger"
            else:
                text = "Live checks passed. Review the energy step before starting an operation."
                tone = "success"
        self.operation_banner.setText(text)
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
        self.last_live_preflight = result
        ready = result.ok
        self.status_strip.set_value(
            "READINESS",
            "READY" if ready else "NOT READY",
            "success" if ready else "danger",
        )
        messages = [*result.static.blockers, *result.blockers]
        warnings = [*result.static.warnings, *result.warnings]
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
