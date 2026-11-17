"""Operator request form; the shared ShadowRequest remains the wire format."""
import math

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QComboBox, QFormLayout, QGridLayout, QGroupBox, QLabel, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QVBoxLayout, QWidget,
)

from half_linac.src.shared.digital_shadow.models import Diagnostic, ShadowRequest
from half_linac.src.shared.elegant_backend import ElegantParser
from half_linac.src.shared.machine_profile.model_backend import build_model_backend


def twiss_to_moments(beta, alpha, geometric_emittance_mm_mrad):
    beta, alpha, emittance = map(float, (beta, alpha, geometric_emittance_mm_mrad))
    if not all(math.isfinite(v) for v in (beta, alpha, emittance)) or beta <= 0 or emittance <= 0:
        raise ValueError("Beta and geometric emittance must be positive; all beam parameters must be finite")
    emittance *= 1e-6
    return (emittance * beta, -emittance * alpha, emittance * (1 + alpha * alpha) / beta)


def moments_to_twiss(moments):
    a, b, c = moments
    emittance = math.sqrt(a * c - b * b)
    return a / emittance, -b / emittance, emittance * 1e6


class RequestForm(QWidget):
    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.context = context
        self._loaded = None
        self._beam_text = None
        self._state = None
        layout = QVBoxLayout(self)
        model = QGroupBox("Model and diagnostics", self)
        form = QFormLayout(model)
        self.line = QComboBox(model)
        self.line.setPlaceholderText("Select a model line")
        backend = build_model_backend(context)
        self.line.addItems([entry.name for entry in backend.get_measurement_model_lines()])
        self.line.setCurrentIndex(-1)
        self.reference = QComboBox(model)
        self.edge = QComboBox(model)
        self.edge.addItems(["entrance", "exit"])
        self.energy = QLineEdit(model)
        self.energy.setPlaceholderText("Kinetic energy, MeV")
        form.addRow("Model line", self.line)
        form.addRow("Reference", self.reference)
        form.addRow("Reference edge", self.edge)
        form.addRow("Kinetic energy [MeV]", self.energy)
        self.diagnostics = QTableWidget(0, 3, model)
        self.diagnostics.setHorizontalHeaderLabels(["Use", "Diagnostic · RMS", "Unit"])
        self.diagnostics.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.diagnostics.setColumnWidth(0, 42)
        self.diagnostics.setColumnWidth(2, 70)
        self.diagnostics.verticalHeader().hide()
        self.diagnostics.setMinimumHeight(140)
        self.diagnostics.setMaximumHeight(190)
        form.addRow(self.diagnostics)
        self.catalog_status = QLabel(model)
        self.catalog_status.setWordWrap(True)
        form.addRow(self.catalog_status)
        layout.addWidget(model)

        beam = QGroupBox("Entrance beam · geometric emittance", self)
        grid = QGridLayout(beam)
        for column, title in enumerate(("Plane", "Beta [m]", "Alpha", "Emittance [mm·mrad]")):
            grid.addWidget(QLabel(title), 0, column)
        self.beam_fields = {}
        for row, plane in enumerate(("x", "y"), 1):
            grid.addWidget(QLabel(plane.upper()), row, 0)
            self.beam_fields[plane] = tuple(QLineEdit(beam) for _ in range(3))
            for column, edit in enumerate(self.beam_fields[plane], 1):
                grid.addWidget(edit, row, column)
        self.basis = QComboBox(beam)
        self.basis.addItem("Assumed", "assumed")
        self.basis.addItem("Independent measurement", "independent_measurement")
        self.provenance = QLineEdit(beam)
        self.provenance.setPlaceholderText("Required: source, reference and applicability")
        grid.addWidget(QLabel("Basis"), 3, 0)
        grid.addWidget(self.basis, 3, 1, 1, 3)
        grid.addWidget(QLabel("Source"), 4, 0)
        grid.addWidget(self.provenance, 4, 1, 1, 3)
        layout.addWidget(beam)

        advanced = QGroupBox("Timing and fitted diagnostics", self)
        advanced.setCheckable(True)
        advanced.setChecked(False)
        advanced_layout = QVBoxLayout(advanced)
        advanced_body = QWidget(advanced)
        advanced_form = QFormLayout(advanced_body)
        self.capture_window = QLineEdit("5", advanced)
        self.diagnostic_age = QLineEdit("5", advanced)
        self.model_timeout = QLineEdit("30", advanced)
        self.fitted = QLineEdit(advanced)
        self.fitted.setPlaceholderText("Comma-separated keys, e.g. PRF07/sigx, PRF07/sigy")
        advanced_form.addRow("Capture window [s]", self.capture_window)
        advanced_form.addRow("Diagnostic age [s]", self.diagnostic_age)
        advanced_form.addRow("Model timeout [s]", self.model_timeout)
        advanced_form.addRow("Used for beam fitting", self.fitted)
        advanced_layout.addWidget(advanced_body)
        advanced_body.hide()
        advanced.toggled.connect(advanced_body.setVisible)
        layout.addWidget(advanced)
        layout.addStretch()
        self.line.currentTextChanged.connect(self._line_changed)
        self.reference.currentTextChanged.connect(self._refresh_diagnostics)

    def _line_changed(self, line):
        self._state = None
        self.reference.blockSignals(True)
        self.reference.clear()
        try:
            if line:
                config = self.context.model_backend.config
                self._state = ElegantParser(config["source_lattice"], config["optics_ini_ele"], line).build_runtime_state()
                names = self._state["usedline"]
                self.reference.addItems([name for name in names if names.count(name) == 1])
            self.catalog_status.clear()
        except (ValueError, KeyError, OSError) as error:
            self.catalog_status.setText(f"Model unavailable: {error}")
        finally:
            self.reference.blockSignals(False)
        self._refresh_diagnostics()

    def _selected_diagnostics(self):
        values = []
        for row in range(self.diagnostics.rowCount()):
            if self.diagnostics.item(row, 0).checkState() == Qt.Checked:
                element, channel = self.diagnostics.item(row, 1).data(Qt.UserRole)
                values.append(Diagnostic(element, channel, self.diagnostics.cellWidget(row, 2).currentText(), "rms"))
        return values

    def _add_diagnostic(self, diagnostic, checked):
        row = self.diagnostics.rowCount()
        self.diagnostics.insertRow(row)
        check = QTableWidgetItem()
        check.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
        check.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        name = QTableWidgetItem(diagnostic.key)
        name.setData(Qt.UserRole, (diagnostic.element, diagnostic.channel))
        name.setFlags(Qt.ItemIsEnabled)
        units = QComboBox(self.diagnostics)
        units.addItems(["mm", "m"])
        units.setCurrentText(diagnostic.unit)
        self.diagnostics.setItem(row, 0, check)
        self.diagnostics.setItem(row, 1, name)
        self.diagnostics.setCellWidget(row, 2, units)

    def _refresh_diagnostics(self, *args):
        selected = {item.key: item for item in self._selected_diagnostics()}
        self.diagnostics.setRowCount(0)
        if not self._state or not self.reference.currentText():
            return
        names = self._state["usedline"]
        reference = self.reference.currentText()
        if reference not in names:
            return
        for name in names[names.index(reference):]:
            if names.count(name) != 1 or self._state["lattice"][name]["TYPE"] != "WATCH":
                continue
            try:
                element = self.context.profile.get_element(name)
            except ValueError:
                continue
            if element.kind != "flag":
                continue
            for channel in ("sigx", "sigy"):
                if "vm" not in element.channels.get(channel, {}):
                    continue
                key = f"{name}/{channel}"
                self._add_diagnostic(selected.get(key, Diagnostic(name, channel, "mm", "rms")), key in selected)

    def load_request(self, request):
        self._loaded = request
        if self.line.findText(request.model_line) < 0:
            self.line.addItem(request.model_line)
        self.line.setCurrentText(request.model_line)
        if self.reference.findText(request.reference_element) < 0:
            self.reference.addItem(request.reference_element)
        self.reference.setCurrentText(request.reference_element)
        self.edge.setCurrentText(request.reference_edge)
        self.energy.setText(format(request.energy_mev, ".17g"))
        for plane, moments in (("x", request.x_moments), ("y", request.y_moments)):
            for edit, value in zip(self.beam_fields[plane], moments_to_twiss(moments)):
                edit.setText(format(value, ".12g"))
                edit.setToolTip(format(value, ".17g"))
        self._beam_text = self._beam_values()
        self.basis.setCurrentIndex(self.basis.findData(request.beam_basis))
        self.provenance.setText(request.beam_provenance)
        self.capture_window.setText(format(request.max_capture_duration_s, ".17g"))
        self.diagnostic_age.setText(format(request.diagnostic_max_age_s, ".17g"))
        self.model_timeout.setText(format(request.model_timeout_s, ".17g"))
        self.fitted.setText(", ".join(request.fitted_diagnostics))
        requested = {item.key: item for item in request.diagnostics}
        existing = set()
        for row in range(self.diagnostics.rowCount()):
            key = self.diagnostics.item(row, 1).text()
            existing.add(key)
            self.diagnostics.item(row, 0).setCheckState(Qt.Checked if key in requested else Qt.Unchecked)
            if key in requested:
                self.diagnostics.cellWidget(row, 2).setCurrentText(requested[key].unit)
        for key, diagnostic in requested.items():
            if key not in existing:
                self._add_diagnostic(diagnostic, True)  # Preserve imported inputs; submission validates capabilities.

    def _beam_values(self):
        return tuple(edit.text() for plane in ("x", "y") for edit in self.beam_fields[plane])

    def request(self):
        def number(edit, name):
            try:
                return float(edit.text())
            except ValueError as error:
                raise ValueError(f"Enter a number for {name}") from error
        moments = []
        for index, plane in enumerate(("x", "y")):
            if self._loaded is not None and self._beam_values()[index * 3:index * 3 + 3] == self._beam_text[index * 3:index * 3 + 3]:
                moments.append(getattr(self._loaded, plane + "_moments"))
            else:
                moments.append(twiss_to_moments(*[number(edit, plane.upper() + " beam") for edit in self.beam_fields[plane]]))
        x_moments, y_moments = moments
        return ShadowRequest(
            model_line=self.line.currentText(), reference_element=self.reference.currentText(),
            reference_edge=self.edge.currentText(), energy_mev=number(self.energy, "kinetic energy"),
            x_moments=x_moments, y_moments=y_moments, beam_basis=self.basis.currentData(),
            beam_provenance=self.provenance.text(), diagnostics=tuple(self._selected_diagnostics()),
            model_backend=self._loaded.model_backend if self._loaded else self.context.model_backend.name,
            max_capture_duration_s=number(self.capture_window, "capture window"),
            diagnostic_max_age_s=number(self.diagnostic_age, "diagnostic age"),
            model_timeout_s=number(self.model_timeout, "model timeout"),
            fitted_diagnostics=tuple(key.strip() for key in self.fitted.text().split(",") if key.strip()),
        )
