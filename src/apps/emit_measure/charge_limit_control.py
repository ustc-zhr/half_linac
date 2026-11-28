"""Charge limit input shared by the two emittance optimizers."""

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QComboBox, QDoubleSpinBox, QHBoxLayout, QLineEdit, QStackedWidget, QWidget

from .optimization import ChargeConstraint


class ChargeLimitControl(QWidget):
    changed = pyqtSignal()

    def __init__(self, element_id="ICT01", parent=None):
        super().__init__(parent)
        self.element_id = element_id
        self.mode = QComboBox(self)
        self.mode.setAccessibleName(f"{element_id} charge limit mode")
        self.mode.addItem("Minimum (nC)", "minimum_nc")
        self.mode.addItem("Retention (%)", "retention")
        self.minimum = QLineEdit(self)
        self.minimum.setAccessibleName(f"{element_id} minimum charge (nC)")
        self.minimum.setPlaceholderText("Enter nC")
        self.retention = QDoubleSpinBox(self)
        self.retention.setAccessibleName(f"{element_id} minimum retained charge (%)")
        self.retention.setRange(0.1, 100.0)
        self.retention.setDecimals(1)
        self.retention.setSingleStep(1.0)
        self.retention.setValue(95.0)
        self.retention.setSuffix(" %")
        inputs = QStackedWidget(self)
        for widget in (self.minimum, self.retention):
            widget.setFixedHeight(32)
            inputs.addWidget(widget)
        self.mode.setFixedHeight(32)
        self.mode.currentIndexChanged.connect(inputs.setCurrentIndex)
        self.mode.currentIndexChanged.connect(self.changed)
        self.minimum.textChanged.connect(self.changed)
        self.retention.valueChanged.connect(self.changed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.mode)
        layout.addWidget(inputs, 1)

    def constraint(self):
        if self.mode.currentData() == "retention":
            return ChargeConstraint(element_id=self.element_id, retention=self.retention.value() / 100)
        try:
            constraint = ChargeConstraint(element_id=self.element_id, minimum_nc=float(self.minimum.text()))
            constraint.validate()
        except ValueError:
            self.minimum.setFocus()
            self.minimum.selectAll()
            raise ValueError(f"Enter a finite positive minimum charge (nC) for {self.element_id}.") from None
        return constraint

    def load_settings(self, settings):
        constraint = ChargeConstraint.from_settings(settings)
        if constraint.minimum_nc is not None:
            self.minimum.setText(str(constraint.minimum_nc))
            self.mode.setCurrentIndex(self.mode.findData("minimum_nc"))
        else:
            self.retention.setValue(100 * constraint.retention)
            self.mode.setCurrentIndex(self.mode.findData("retention"))


def format_charge_limit(label, baseline_nc, minimum_nc):
    return f"{label} baseline {baseline_nc:.4g} nC · minimum {minimum_nc:.6g} nC ({minimum_nc / baseline_nc:.1%})"
