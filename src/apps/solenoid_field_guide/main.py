from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "repo_bootstrap.py").is_file())
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from repo_bootstrap import ensure_repo_import_path
ensure_repo_import_path(__file__)

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton, QToolButton, QVBoxLayout, QWidget, QFrame
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from half_linac.src.apps.solenoid_field_guide.calibration import CalibrationError, CombinedRecommendation, MagnetCalibration, load_calibrations, recommend_combined, recommend_single
from half_linac.src.shared.app_theme import resolve_initial_theme
from half_linac.src.shared.machine_profile import CONTROL_BACKEND_ENV, RuntimeContextWidget, load_profile, normalize_mode


THEMES = {
    "dark": {"window": "#0f1519", "panel": "#172027", "input": "#10171c", "border": "#2a3943", "text": "#e6edf2", "muted": "#91a2ad", "accent": "#45d0bc", "warning": "#e4b86f", "plot": "#11191f"},
    "light": {"window": "#f2ede5", "panel": "#fffdf9", "input": "#fffdf8", "border": "#d7cec1", "text": "#2c3942", "muted": "#746c62", "accent": "#2d7f6d", "warning": "#a97118", "plot": "#fffdf9"},
}


def build_stylesheet(theme: str) -> str:
    p = THEMES[theme]
    return f"""
    QMainWindow, QWidget {{ background: {p['window']}; color: {p['text']}; font-family: \"IBM Plex Sans\", \"Source Han Sans SC\", \"Segoe UI\", sans-serif; font-size: 12px; }}
    QGroupBox {{ background: {p['panel']}; border: 1px solid {p['border']}; border-radius: 7px; margin-top: 10px; padding: 14px 10px 10px; font-weight: 700; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; color: {p['muted']}; }}
    QLabel#title {{ font-size: 20px; font-weight: 700; }}
    QLabel#meta {{ color: {p['muted']}; }}
    QLabel#status {{ background: {p['input']}; border: 1px solid {p['border']}; border-radius: 5px; padding: 7px; }}
    QComboBox, QDoubleSpinBox, QPlainTextEdit {{ background: {p['input']}; color: {p['text']}; border: 1px solid {p['border']}; border-radius: 5px; padding: 5px; }}
    QComboBox:focus, QDoubleSpinBox:focus {{ border-color: {p['accent']}; }}
    QPlainTextEdit {{ font-family: \"IBM Plex Mono\", monospace; }}
    QPushButton {{ background: {p['panel']}; color: {p['text']}; border: 1px solid {p['border']}; border-radius: 5px; padding: 6px 12px; min-height: 26px; font-weight: 600; }}
    QPushButton:hover {{ border-color: {p['accent']}; }}
    QPushButton#designButton {{ background: {p['accent']}; color: {p['window']}; border-color: {p['accent']}; }}
    QToolButton#themeButton {{ background: {p['panel']}; color: {p['text']}; border: 1px solid {p['border']}; border-radius: 5px; min-width: 32px; max-width: 32px; min-height: 32px; max-height: 32px; font-size: 16px; }}
    QLabel#primaryValue {{ color: {p['accent']}; font-size: 24px; font-weight: 700; }}
    QLabel#valueLabel {{ font-size: 14px; font-weight: 700; }}
    QLabel#fieldLabel {{ color: {p['muted']}; }}
    QFrame#resultRow {{ border-bottom: 1px solid {p['border']}; }}
    """


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Solenoid Field Guide")
        self.resize(1040, 720)
        self.current_theme = resolve_initial_theme()
        self.profile = load_profile("half")
        self.control_backend = normalize_mode(os.environ.get(CONTROL_BACKEND_ENV, "real"), "control_backend")
        self.catalog = load_calibrations()
        self.calibrations = self.catalog.calibrations
        self._build_ui()
        self._apply_theme()
        self._refresh()

    def _build_ui(self):
        root = QWidget(self)
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        header = QHBoxLayout()
        title = QLabel("Solenoid Field Guide")
        title.setObjectName("title")
        header.addWidget(title)
        header.addStretch(1)
        self.runtime_context = RuntimeContextWidget(
            machine_id=self.profile.machine.id,
            machine_display_name=self.profile.machine.display_name,
            control_backend=self.control_backend,
        )
        header.addWidget(self.runtime_context)
        self.theme_button = QToolButton()
        self.theme_button.setObjectName("themeButton")
        self.theme_button.clicked.connect(self._toggle_theme)
        header.addWidget(self.theme_button)
        layout.addLayout(header)
        workspace = QHBoxLayout()
        workspace.setSpacing(10)
        target_box = QGroupBox("Target")
        target_layout = QVBoxLayout(target_box)
        form = QFormLayout()
        self.device_combo = QComboBox()
        self.device_combo.addItem("SS01", "SS01")
        self.device_combo.addItem("SS02", "SS02")
        self.device_combo.addItem("SM01", "SM01")
        for composite in self.catalog.composites.values():
            self.device_combo.addItem(composite.display_name, composite.id)
        self.device_combo.addItem("SL01-1 (single section)", "SL01-1")
        self.device_combo.addItem("SL01-2 (single section)", "SL01-2")
        self.quantity_combo = QComboBox()
        self.quantity_combo.addItem("Peak field |Bpeak|", "peak")
        self.quantity_combo.addItem("Integral field |∫Bz dz|", "integral")
        self.quantity_combo.setCurrentIndex(0)
        self.target_spin = QDoubleSpinBox()
        self.target_spin.setDecimals(8)
        self.target_spin.setRange(0.0, 100.0)
        self.target_spin.setSingleStep(0.001)
        self.target_unit = QLabel("T·m")
        form.addRow("Element / assembly", self.device_combo)
        form.addRow("Target quantity", self.quantity_combo)
        target_row = QHBoxLayout()
        target_row.addWidget(self.target_spin)
        target_row.addWidget(self.target_unit)
        form.addRow("Target", target_row)
        target_layout.addLayout(form)
        self.design_button = QPushButton("Use Design Value")
        self.design_button.setObjectName("designButton")
        self.design_button.setToolTip("Fill the target from the configured design peak field reference.")
        self.design_button.clicked.connect(self._use_design_value)
        target_layout.addWidget(self.design_button, alignment=Qt.AlignRight)
        target_layout.addStretch(1)
        workspace.addWidget(target_box, 1)

        result_box = QGroupBox("Recommendation")
        result_layout = QVBoxLayout(result_box)
        self.status = QLabel()
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        result_layout.addWidget(self.status)
        self.result = QWidget()
        self.result_layout = QVBoxLayout(self.result)
        self.result_layout.setContentsMargins(0, 4, 0, 4)
        self.result_layout.setSpacing(3)
        result_layout.addWidget(self.result)
        workspace.addWidget(result_box, 2)
        layout.addLayout(workspace)

        plot_box = QGroupBox("Measured Calibration")
        plot_layout = QVBoxLayout(plot_box)
        self.figure = Figure(figsize=(7, 2.8), tight_layout=True)
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setMinimumHeight(260)
        plot_layout.addWidget(self.canvas)
        layout.addWidget(plot_box, 1)
        self.meta = QLabel()
        self.meta.setObjectName("meta")
        self.meta.setWordWrap(True)
        layout.addWidget(self.meta)
        actions = QHBoxLayout()
        self.copy_button = QPushButton("Copy Result")
        self.copy_button.clicked.connect(self._copy_result)
        actions.addStretch(1)
        actions.addWidget(self.copy_button)
        layout.addLayout(actions)
        self.device_combo.currentIndexChanged.connect(self._device_changed)
        self.quantity_combo.currentIndexChanged.connect(self._quantity_changed)
        self.target_spin.valueChanged.connect(self._refresh)

    def _apply_theme(self):
        self.setStyleSheet(build_stylesheet(self.current_theme))
        self.theme_button.setText("☼" if self.current_theme == "dark" else "☾")
        self.theme_button.setToolTip("Switch to light theme." if self.current_theme == "dark" else "Switch to dark theme.")

    def _toggle_theme(self):
        self.current_theme = "light" if self.current_theme == "dark" else "dark"
        self._apply_theme()
        self._refresh()

    def _device_changed(self):
        is_combined = self.device_combo.currentData() in self.catalog.composites
        self.quantity_combo.setEnabled(not is_combined)
        if is_combined:
            self.quantity_combo.setCurrentIndex(0)
        self._refresh()

    def _quantity_changed(self):
        is_peak = self.quantity_combo.currentData() == "peak"
        self.target_unit.setText("T" if is_peak else "T·m")
        self._refresh()

    def _use_design_value(self):
        try:
            device = self.device_combo.currentData()
            if device in self.catalog.composites:
                target = sum(
                    self.calibrations[element_id].design_integral_field
                    for element_id in self.catalog.composites[device].members
                )
            else:
                calibration = self.calibrations[device]
                if calibration.design_peak_field is None:
                    raise CalibrationError(f"{device}: no design peak field is configured.")
                target = (
                    calibration.design_peak_field
                    if self.quantity_combo.currentData() == "peak"
                    else calibration.design_integral_field
                )
            self.target_spin.setValue(target)
        except CalibrationError as exc:
            self.status.setText(f"Cannot load design value: {exc}")

    def _refresh(self):
        try:
            device = self.device_combo.currentData()
            target = self.target_spin.value()
            if device in self.catalog.composites:
                recommendation = recommend_combined(self.catalog, device, target)
                text = self._combined_text(recommendation)
                self.status.setText("Ready: common field scale applied to both long-solenoid sections.")
                self._plot(self.calibrations[recommendation.magnets[0].element_id], recommendation.magnets[0].current, recommendation.magnets[0].peak_field)
            else:
                quantity = self.quantity_combo.currentData()
                recommendation = recommend_single(self.calibrations[device], target, quantity)
                text = self._single_text(recommendation, self.calibrations[device], quantity, target)
                self.status.setText("Ready: target is inside the measured calibration range.")
                self._plot(self.calibrations[device], recommendation.current, recommendation.peak_field)
            self.copy_text = text
            if device in self.catalog.composites:
                self._show_combined_result(recommendation)
            else:
                self._show_single_result(recommendation, self.calibrations[device], quantity, target)
            if device in self.catalog.composites:
                self.meta.setText("Composite calibration: " + self.catalog.composites[device].rule)
            else:
                calibration = self.calibrations[device]
                self.meta.setText(f"Calibration: {calibration.test_date} · {calibration.serial} · {calibration.source}")
            self.copy_button.setEnabled(True)
        except CalibrationError as exc:
            self.status.setText(f"Cannot calculate: {exc}")
            self.copy_text = ""
            self._clear_result()
            self.meta.clear()
            self.copy_button.setEnabled(False)
            self.figure.clear()
            self.canvas.draw_idle()

    def _plot(self, calibration: MagnetCalibration, current: float, peak: float):
        palette = THEMES[self.current_theme]
        axis = self.figure.clear() or self.figure.add_subplot(111)
        self.figure.patch.set_facecolor(palette["plot"])
        axis.set_facecolor(palette["plot"])
        axis.plot(calibration.currents, calibration.peak_fields, "o-", color="#2d7f6d", label="measured")
        axis.plot([current], [peak], "o", color="#d26a3a", markersize=8, label="target")
        axis.set_xlabel("Current (A)")
        axis.set_ylabel("|Bpeak| (T)")
        axis.tick_params(colors=palette["text"])
        axis.xaxis.label.set_color(palette["muted"])
        axis.yaxis.label.set_color(palette["muted"])
        for spine in axis.spines.values():
            spine.set_color(palette["border"])
        axis.grid(True, alpha=0.25)
        axis.legend(loc="best")
        self.canvas.draw_idle()

    def _clear_result(self):
        while self.result_layout.count():
            item = self.result_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    @staticmethod
    def _result_row(label: str, value: str, *, primary: bool = False):
        row = QFrame()
        row.setObjectName("resultRow")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(4, 3, 4, 3)
        label_widget = QLabel(label)
        label_widget.setObjectName("fieldLabel")
        value_widget = QLabel(value)
        value_widget.setObjectName("primaryValue" if primary else "valueLabel")
        value_widget.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row_layout.addWidget(label_widget)
        row_layout.addStretch(1)
        row_layout.addWidget(value_widget)
        return row

    def _show_single_result(self, rec, calibration, quantity, target):
        self._clear_result()
        self.result_layout.addWidget(self._result_row("Recommended current", f"{rec.current:.5g} A", primary=True))
        self.result_layout.addWidget(self._result_row("Peak field", f"{rec.peak_field:.6g} T"))
        self.result_layout.addWidget(self._result_row("Integral field", f"{rec.integral_field:.6g} T·m"))
        self.result_layout.addWidget(self._result_row("Design peak reference", f"{calibration.design_peak_field:.6g} T"))
        self.result_layout.addWidget(self._result_row("Measured current range", f"{calibration.current_range[0]:g}–{calibration.current_range[1]:g} A"))
        self.result_layout.addStretch(1)

    def _show_combined_result(self, rec):
        self._clear_result()
        self.result_layout.addWidget(self._result_row("Common field scale", f"{rec.scale:.6g}", primary=True))
        for item in rec.magnets:
            section = QLabel(item.element_id)
            section.setObjectName("fieldLabel")
            self.result_layout.addWidget(section)
            self.result_layout.addWidget(self._result_row("Recommended current", f"{item.current:.5g} A"))
            self.result_layout.addWidget(self._result_row("Peak field", f"{item.peak_field:.6g} T"))
        self.result_layout.addStretch(1)

    @staticmethod
    def _single_text(rec, calibration, quantity, target):
        return (f"{rec.element_id}\n\nTarget {quantity}: {target:.8g} {'T' if quantity == 'peak' else 'T·m'}\n"
                f"Recommended current: {rec.current:.6g} A\nPeak field: {rec.peak_field:.8g} T\n"
                f"Integral field (fixed-shape estimate): {rec.integral_field:.8g} T·m\n\n"
                f"Design peak field reference: {calibration.design_peak_field:.8g} T\n"
                f"Calibration: {calibration.test_date} · {calibration.serial}\nSource: {calibration.source}\n"
                f"Measured current range: {calibration.current_range[0]:g}–{calibration.current_range[1]:g} A")

    @staticmethod
    def _combined_text(rec: CombinedRecommendation):
        lines = [f"SL (SL01-1 + SL01-2)", "", f"Target combined integral field: {rec.target_integral_field:.8g} T·m", f"Common field scale: {rec.scale:.8g}", ""]
        for item in rec.magnets:
            lines.extend([item.element_id, f"  Recommended current: {item.current:.6g} A", f"  Peak field: {item.peak_field:.8g} T", f"  Integral field: {item.integral_field:.8g} T·m", ""])
        lines.append("Integral fields are estimated from the fixed-shape calibration assumption.")
        return "\n".join(lines)

    def _copy_result(self):
        QApplication.clipboard().setText(getattr(self, "copy_text", ""))
        self.status.setText("Result copied to clipboard.")


def main() -> int:
    if os.environ.get("HALF_LINAC_MACHINE_ID", "half") != "half":
        app = QApplication(sys.argv)
        QMessageBox.critical(None, "Solenoid Field Guide", "This tool is available only for the HALF machine profile.")
        return 2
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
