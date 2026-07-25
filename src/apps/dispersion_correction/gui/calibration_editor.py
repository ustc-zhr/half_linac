from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from half_linac.src.apps.dispersion_correction.calibration_draft import (
    EnergyCalibrationAnalysis,
    EnergyCalibrationDraft,
    EnergyCalibrationPoint,
    analyze_energy_calibration_draft,
    calibration_fragment,
    load_energy_calibration_draft,
    save_energy_calibration_draft,
)
from half_linac.src.apps.dispersion_correction.gui.theme import (
    build_stylesheet,
    theme_tokens,
)


class CalibrationPlotWidget(QWidget):
    def __init__(self, theme_name: str = "night_shift") -> None:
        super().__init__()
        self.theme_name = theme_name
        self.analysis: EnergyCalibrationAnalysis | None = None
        self.setMinimumHeight(150)

    def set_analysis(self, analysis: EnergyCalibrationAnalysis | None) -> None:
        self.analysis = analysis
        self.update()

    def paintEvent(self, _event) -> None:
        tokens = theme_tokens(self.theme_name)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(tokens["plot_bg"]))
        rect = QRectF(self.rect()).adjusted(52, 16, -18, -32)
        painter.setPen(QPen(QColor(tokens["status_item_bar"]), 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        painter.drawLine(rect.bottomLeft(), rect.topLeft())
        painter.setPen(QColor(tokens["text_muted"]))
        painter.drawText(8, 18, "Δp/p")
        painter.drawText(
            int(rect.right() - 88),
            int(self.height() - 8),
            "actuator",
        )

        analysis = self.analysis
        if (
            analysis is None
            or analysis.actuator_values.size == 0
            or analysis.delta_values.size == 0
        ):
            painter.drawText(rect, Qt.AlignCenter, "Enter calibration points")
            return
        x = analysis.actuator_values
        y = analysis.delta_values
        x_min = float(x.min())
        x_max = float(x.max())
        y_min = float(y.min())
        y_max = float(y.max())
        x_pad = max((x_max - x_min) * 0.08, 1.0e-12)
        y_pad = max((y_max - y_min) * 0.12, 1.0e-12)
        x_min -= x_pad
        x_max += x_pad
        y_min -= y_pad
        y_max += y_pad

        def point(x_value: float, y_value: float) -> QPointF:
            px = rect.left() + (x_value - x_min) / (x_max - x_min) * rect.width()
            py = rect.bottom() - (y_value - y_min) / (y_max - y_min) * rect.height()
            return QPointF(px, py)

        if analysis.fit is not None:
            fit = analysis.fit
            painter.setPen(QPen(QColor(tokens["status_warning"]), 2))
            painter.drawLine(
                point(
                    x_min,
                    fit.slope_delta_per_actuator * x_min + fit.intercept_delta,
                ),
                point(
                    x_max,
                    fit.slope_delta_per_actuator * x_max + fit.intercept_delta,
                ),
            )
        painter.setPen(QPen(QColor(tokens["focus"]), 2))
        painter.setBrush(QColor(tokens["focus"]))
        for x_value, y_value in zip(x, y):
            center = point(float(x_value), float(y_value))
            painter.drawEllipse(center, 3.5, 3.5)


class CalibrationEditorDialog(QDialog):
    def __init__(
        self,
        *,
        actuator: str,
        actuator_unit: str,
        target_delta: float,
        draft_directory: str | Path,
        machine_id: str,
        backend: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.actuator = actuator
        self.actuator_unit = actuator_unit
        self.target_delta = float(target_delta)
        self.draft_directory = Path(draft_directory)
        self.machine_id = machine_id
        self.backend = backend
        self.analysis: EnergyCalibrationAnalysis | None = None
        self.activated_calibration: dict | None = None
        self.activated_source: str | None = None
        self._updating = False

        self.theme_name = str(getattr(parent, "theme_name", "night_shift"))
        self.setObjectName("energyCalibrationDialog")
        self.setStyleSheet(build_stylesheet(self.theme_name))
        self.setWindowTitle("Energy Knob Calibration Editor")
        self.resize(1080, 860)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 12)
        layout.setSpacing(10)

        self.settings_card = QFrame()
        self.settings_card.setObjectName("calibrationSettingsCard")
        settings = QGridLayout(self.settings_card)
        settings.setContentsMargins(14, 10, 14, 12)
        settings.setHorizontalSpacing(12)
        settings.setVerticalSpacing(7)
        settings.setColumnStretch(1, 1)
        settings.setColumnStretch(3, 1)
        settings_title = QLabel("Calibration Setup")
        settings_title.setObjectName("calibrationSectionTitle")
        settings.addWidget(settings_title, 0, 0, 1, 4)

        actuator_label = QLabel("Actuator")
        actuator_label.setProperty("role", "field")
        settings.addWidget(actuator_label, 1, 0)
        self.actuator_value_label = QLabel(f"{actuator} ({actuator_unit})")
        self.actuator_value_label.setObjectName("calibrationActuatorValue")
        settings.addWidget(self.actuator_value_label, 1, 1)

        input_mode_label = QLabel("Input mode")
        input_mode_label.setProperty("role", "field")
        settings.addWidget(input_mode_label, 1, 2)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Measured energy", "measured_energy")
        self.mode_combo.addItem("Direct Δp/p", "direct_delta")
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        settings.addWidget(self.mode_combo, 1, 3)

        baseline_label = QLabel(f"Baseline actuator ({actuator_unit})")
        baseline_label.setProperty("role", "field")
        settings.addWidget(baseline_label, 2, 0)
        self.baseline_actuator_spin = self._wide_spin()
        self.baseline_actuator_spin.valueChanged.connect(self._refresh_analysis)
        settings.addWidget(self.baseline_actuator_spin, 2, 1)

        reference_label = QLabel("Reference energy E0")
        reference_label.setProperty("role", "field")
        settings.addWidget(reference_label, 2, 2)
        self.reference_energy_row = QWidget(self.settings_card)
        reference_layout = QHBoxLayout(self.reference_energy_row)
        reference_layout.setContentsMargins(0, 0, 0, 0)
        reference_layout.setSpacing(6)
        self.reference_energy_spin = self._wide_spin()
        self.reference_energy_spin.setRange(1.0e-9, 1.0e9)
        self.reference_energy_spin.setValue(1.0)
        self.reference_energy_spin.valueChanged.connect(self._refresh_analysis)
        reference_layout.addWidget(self.reference_energy_spin, 1)
        self.energy_unit_combo = QComboBox()
        self.energy_unit_combo.setEditable(True)
        self.energy_unit_combo.addItems(["MeV", "GeV"])
        self.energy_unit_combo.setMinimumWidth(88)
        self.energy_unit_combo.setMaximumWidth(110)
        self.energy_unit_combo.currentTextChanged.connect(self._energy_unit_changed)
        reference_layout.addWidget(self.energy_unit_combo)
        settings.addWidget(self.reference_energy_row, 2, 3)

        note_label = QLabel("Draft note / source")
        note_label.setProperty("role", "field")
        settings.addWidget(note_label, 3, 0)
        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText(
            "Measurement source, operating point, operator, or commissioning note"
        )
        settings.addWidget(self.note_edit, 3, 1, 1, 3)
        layout.addWidget(self.settings_card)

        points_header = QHBoxLayout()
        points_title = QLabel("Calibration Points")
        points_title.setObjectName("calibrationSectionTitle")
        points_header.addWidget(points_title)
        self.table_hint_label = QLabel(
            "Paste columns: actuator, measurement, uncertainty, note"
        )
        self.table_hint_label.setObjectName("calibrationTableHint")
        points_header.addWidget(self.table_hint_label)
        points_header.addStretch(1)
        self.load_button = QPushButton("Load Draft")
        self.load_button.clicked.connect(self._load_latest)
        points_header.addWidget(self.load_button)
        self.save_button = QPushButton("Save Draft")
        self.save_button.clicked.connect(self._save_draft)
        points_header.addWidget(self.save_button)
        layout.addLayout(points_header)

        self.table = QTableWidget(0, 6)
        self.table.setObjectName("calibrationPointsTable")
        self.table.setHorizontalHeaderLabels(
            [
                "Use",
                f"Actuator ({actuator_unit})",
                "Measured energy (E0 unit)",
                "Δp/p",
                "Uncertainty",
                "Note",
            ]
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.verticalHeader().setMinimumSectionSize(36)
        self.table.setMinimumHeight(
            self.table.horizontalHeader().sizeHint().height() + 5 * 36 + 4
        )
        self.table.cellChanged.connect(self._refresh_analysis)
        layout.addWidget(self.table, 3)

        table_actions = QHBoxLayout()
        self.add_button = QPushButton("Add Row")
        self.add_button.clicked.connect(self._add_empty_row)
        table_actions.addWidget(self.add_button)
        self.delete_button = QPushButton("Delete Row")
        self.delete_button.clicked.connect(self._delete_rows)
        table_actions.addWidget(self.delete_button)
        self.paste_button = QPushButton("Paste Data")
        self.paste_button.clicked.connect(self._paste_clipboard)
        table_actions.addWidget(self.paste_button)
        table_actions.addStretch(1)
        layout.addLayout(table_actions)

        self.analysis_card = QFrame()
        self.analysis_card.setObjectName("calibrationAnalysisCard")
        analysis_layout = QHBoxLayout(self.analysis_card)
        analysis_layout.setContentsMargins(12, 10, 12, 12)
        analysis_layout.setSpacing(12)
        plot_panel = QWidget(self.analysis_card)
        plot_layout = QVBoxLayout(plot_panel)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        plot_layout.setSpacing(6)
        plot_title = QLabel("Fit Preview")
        plot_title.setObjectName("calibrationSectionTitle")
        plot_layout.addWidget(plot_title)
        self.plot = CalibrationPlotWidget(self.theme_name)
        self.plot.setMinimumHeight(170)
        plot_layout.addWidget(self.plot, 1)
        analysis_layout.addWidget(plot_panel, 2)

        quality_panel = QWidget(self.analysis_card)
        quality_layout = QVBoxLayout(quality_panel)
        quality_layout.setContentsMargins(0, 0, 0, 0)
        quality_layout.setSpacing(6)
        quality_title = QLabel("Quality")
        quality_title.setObjectName("calibrationSectionTitle")
        quality_layout.addWidget(quality_title)
        self.preview = QPlainTextEdit()
        self.preview.setObjectName("calibrationQualityPreview")
        self.preview.setReadOnly(True)
        self.preview.setMinimumWidth(310)
        self.preview.setMaximumWidth(380)
        quality_layout.addWidget(self.preview, 1)
        analysis_layout.addWidget(quality_panel, 1)
        layout.addWidget(self.analysis_card, 1)

        bottom = QHBoxLayout()
        session_hint = QLabel(
            "Activation applies only to this GUI session and does not write PVs."
        )
        session_hint.setObjectName("calibrationSessionHint")
        bottom.addWidget(session_hint)
        bottom.addStretch(1)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.reject)
        bottom.addWidget(self.close_button)
        self.activate_button = QPushButton("Activate for Current Session")
        self.activate_button.setProperty("role", "control")
        self.activate_button.clicked.connect(self._activate)
        bottom.addWidget(self.activate_button)
        layout.addLayout(bottom)

        for _ in range(5):
            self._add_empty_row()
        self._energy_unit_changed(self.energy_unit_combo.currentText())
        self._mode_changed()

    @staticmethod
    def _wide_spin() -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setDecimals(9)
        spin.setRange(-1.0e9, 1.0e9)
        spin.setSingleStep(0.1)
        return spin

    def set_draft(self, draft: EnergyCalibrationDraft) -> None:
        self._updating = True
        try:
            mode_index = self.mode_combo.findData(draft.input_mode)
            if mode_index >= 0:
                self.mode_combo.setCurrentIndex(mode_index)
            self.baseline_actuator_spin.setValue(draft.baseline_actuator)
            if draft.reference_energy is not None:
                self.reference_energy_spin.setValue(draft.reference_energy)
            self.energy_unit_combo.setCurrentText(draft.energy_unit)
            self.note_edit.setText(draft.note)
            self.table.setRowCount(0)
            for point in draft.points:
                self._append_point(point)
        finally:
            self._updating = False
        self._mode_changed()

    def current_draft(self) -> EnergyCalibrationDraft:
        mode = str(self.mode_combo.currentData())
        points = []
        for row in range(self.table.rowCount()):
            enabled_item = self.table.item(row, 0)
            points.append(
                EnergyCalibrationPoint(
                    actuator_value=self._optional_cell_float(row, 1),
                    measured_energy=self._optional_cell_float(row, 2),
                    delta_p_over_p=self._optional_cell_float(row, 3),
                    uncertainty=self._optional_cell_float(row, 4),
                    note=self._cell_text(row, 5),
                    enabled=(
                        enabled_item is not None
                        and enabled_item.checkState() == Qt.Checked
                    ),
                )
            )
        return EnergyCalibrationDraft(
            actuator=self.actuator,
            actuator_unit=self.actuator_unit,
            input_mode=mode,
            baseline_actuator=float(self.baseline_actuator_spin.value()),
            reference_energy=(
                float(self.reference_energy_spin.value())
                if mode == "measured_energy"
                else None
            ),
            points=tuple(points),
            energy_unit=self.energy_unit_combo.currentText().strip() or "MeV",
            machine_id=self.machine_id,
            backend=self.backend,
            note=self.note_edit.text().strip(),
        )

    def _add_empty_row(self) -> None:
        self._append_point(EnergyCalibrationPoint())
        self._refresh_analysis()

    def _append_point(self, point: EnergyCalibrationPoint) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        enabled = QTableWidgetItem()
        enabled.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
        enabled.setCheckState(Qt.Checked if point.enabled else Qt.Unchecked)
        self.table.setItem(row, 0, enabled)
        self._set_cell(row, 1, point.actuator_value)
        self._set_cell(row, 2, point.measured_energy)
        self._set_cell(row, 3, point.delta_p_over_p)
        self._set_cell(row, 4, point.uncertainty)
        self.table.setItem(row, 5, QTableWidgetItem(point.note))

    def _delete_rows(self) -> None:
        rows = sorted(
            {index.row() for index in self.table.selectedIndexes()},
            reverse=True,
        )
        if not rows and self.table.rowCount():
            rows = [self.table.rowCount() - 1]
        for row in rows:
            self.table.removeRow(row)
        self._refresh_analysis()

    def _paste_clipboard(self) -> None:
        text = QApplication.clipboard().text().strip()
        if not text:
            return
        mode = str(self.mode_combo.currentData())
        parsed = []
        for line in text.splitlines():
            fields = [field.strip() for field in line.split("\t")]
            if len(fields) == 1:
                fields = [field.strip() for field in line.split(",")]
            try:
                actuator = float(fields[0])
                measured = float(fields[1])
            except (IndexError, ValueError):
                continue
            uncertainty = None
            if len(fields) > 2 and fields[2]:
                try:
                    uncertainty = float(fields[2])
                except ValueError:
                    uncertainty = None
            note = fields[3] if len(fields) > 3 else ""
            parsed.append(
                EnergyCalibrationPoint(
                    actuator_value=actuator,
                    measured_energy=measured if mode == "measured_energy" else None,
                    delta_p_over_p=measured if mode == "direct_delta" else None,
                    uncertainty=uncertainty,
                    note=note,
                )
            )
        if not parsed:
            QMessageBox.warning(
                self,
                "Paste Calibration Data",
                "No numeric actuator/measurement rows were found.",
            )
            return
        self._updating = True
        try:
            self.table.setRowCount(0)
            for point in parsed:
                self._append_point(point)
        finally:
            self._updating = False
        self._mode_changed()

    def _mode_changed(self, _index: int | None = None) -> None:
        mode = str(self.mode_combo.currentData())
        measured_mode = mode == "measured_energy"
        self.reference_energy_spin.setEnabled(measured_mode)
        self.energy_unit_combo.setEnabled(measured_mode)
        self._updating = True
        try:
            for row in range(self.table.rowCount()):
                measured_item = self.table.item(row, 2)
                delta_item = self.table.item(row, 3)
                if measured_item is not None:
                    measured_item.setFlags(
                        Qt.ItemIsEnabled | Qt.ItemIsEditable
                        if measured_mode
                        else Qt.NoItemFlags
                    )
                if delta_item is not None:
                    delta_item.setFlags(
                        Qt.NoItemFlags
                        if measured_mode
                        else Qt.ItemIsEnabled | Qt.ItemIsEditable
                    )
        finally:
            self._updating = False
        self._refresh_analysis()

    def _energy_unit_changed(self, _text: str) -> None:
        unit = self.energy_unit_combo.currentText().strip() or "E0 unit"
        self.table.setHorizontalHeaderItem(
            2,
            QTableWidgetItem(f"Measured energy ({unit})"),
        )

    def _refresh_analysis(self, _row=None, _column=None) -> None:
        if self._updating:
            return
        draft = self.current_draft()
        analysis = analyze_energy_calibration_draft(
            draft,
            target_delta=self.target_delta,
        )
        self.analysis = analysis
        if draft.input_mode == "measured_energy":
            self._updating = True
            try:
                reference = draft.reference_energy
                for row, point in enumerate(draft.points):
                    delta = (
                        None
                        if reference is None or point.measured_energy is None
                        else (point.measured_energy - reference) / reference
                    )
                    self._set_cell(row, 3, delta, editable=False)
            finally:
                self._updating = False
        self.preview.setPlainText(self._format_analysis(analysis))
        self.plot.set_analysis(analysis)
        self.activate_button.setEnabled(analysis.valid)

    def _format_analysis(self, analysis: EnergyCalibrationAnalysis) -> str:
        lines = [
            f"Quality: {'PASS' if analysis.valid else 'NOT READY'}",
            f"Target energy step: ±{analysis.target_delta:g} Δp/p",
        ]
        if analysis.fit is not None:
            fit = analysis.fit
            lines.extend(
                [
                    f"Points: {fit.n_samples}",
                    f"actuator_per_delta: {fit.actuator_per_delta:.12g}",
                    f"R²: {fit.r_squared:.8g}",
                    f"Maximum fit residual: {analysis.max_abs_residual:.6g}",
                    (
                        "Predicted actuator step: "
                        f"±{analysis.target_actuator_step:.8g} {self.actuator_unit}"
                    ),
                ]
            )
        if analysis.blockers:
            lines.extend(["", "Blockers:", *(f"- {item}" for item in analysis.blockers)])
        if analysis.warnings:
            lines.extend(["", "Warnings:", *(f"- {item}" for item in analysis.warnings)])
        return "\n".join(lines)

    def _save_draft(self, _checked=False, *, show_message: bool = True):
        draft = self.current_draft()
        analysis = analyze_energy_calibration_draft(
            draft,
            target_delta=self.target_delta,
        )
        paths = save_energy_calibration_draft(
            self.draft_directory,
            draft,
            analysis,
        )
        if show_message:
            QMessageBox.information(
                self,
                "Calibration Draft Saved",
                f"Draft saved to:\n{paths['archive']}",
            )
        return paths

    def _load_latest(self) -> None:
        path = self.draft_directory / "latest.json"
        if not path.exists():
            QMessageBox.warning(
                self,
                "Load Calibration Draft",
                f"No saved draft exists at:\n{path}",
            )
            return
        try:
            draft = load_energy_calibration_draft(path)
        except Exception as exc:
            QMessageBox.warning(self, "Load Calibration Draft", str(exc))
            return
        if (
            draft.actuator != self.actuator
            or draft.actuator_unit != self.actuator_unit
        ):
            QMessageBox.warning(
                self,
                "Load Calibration Draft",
                "The saved draft belongs to a different actuator or unit.",
            )
            return
        self.set_draft(draft)

    def _activate(self) -> None:
        draft = self.current_draft()
        analysis = analyze_energy_calibration_draft(
            draft,
            target_delta=self.target_delta,
        )
        if not analysis.valid:
            QMessageBox.warning(
                self,
                "Activate Calibration",
                "The calibration draft has not passed all quality checks.",
            )
            return
        paths = save_energy_calibration_draft(
            self.draft_directory,
            draft,
            analysis,
        )
        fit = analysis.fit
        assert fit is not None
        answer = QMessageBox.question(
            self,
            "Activate Session Calibration",
            "Activate this calibration for the current GUI session?\n\n"
            f"actuator_per_delta: {fit.actuator_per_delta:.12g}\n"
            f"R²: {fit.r_squared:.8g}\n"
            f"Target actuator step: ±{analysis.target_actuator_step:.8g} "
            f"{self.actuator_unit}\n\n"
            "This does not modify the machine profile or write a PV. Existing "
            "dispersion measurements, response matrices, and recommendations "
            "will be discarded.",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return
        self.activated_source = str(paths["archive"])
        self.activated_calibration = calibration_fragment(
            draft,
            analysis,
            source_path=self.activated_source,
        )
        self.accept()

    def _optional_cell_float(self, row: int, column: int) -> float | None:
        text = self._cell_text(row, column)
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    def _cell_text(self, row: int, column: int) -> str:
        item = self.table.item(row, column)
        return item.text().strip() if item is not None else ""

    def _set_cell(
        self,
        row: int,
        column: int,
        value: float | None,
        *,
        editable: bool = True,
    ) -> None:
        text = "" if value is None else f"{float(value):.12g}"
        item = self.table.item(row, column)
        if item is None:
            item = QTableWidgetItem()
            self.table.setItem(row, column, item)
        item.setText(text)
        item.setFlags(
            Qt.ItemIsEnabled | Qt.ItemIsEditable if editable else Qt.NoItemFlags
        )
