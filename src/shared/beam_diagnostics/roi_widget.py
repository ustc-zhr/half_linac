from __future__ import annotations

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QCheckBox, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSpinBox, QVBoxLayout, QWidget
from matplotlib.patches import Rectangle
from matplotlib.widgets import RectangleSelector

from .roi import ImageROI, clamp_roi, resolve_roi, save_roi


class ROIControl(QWidget):
    roiChanged = pyqtSignal(object, bool)
    warningRaised = pyqtSignal(str)

    def __init__(self, *, image_shape, runtime_path, configured=None, parent=None):
        super().__init__(parent)
        self.image_shape = tuple(image_shape)
        self.runtime_path = runtime_path
        self.configured = configured
        self._selector = None
        self._rectangle = None
        self.use_roi = QCheckBox("Use ROI", self)
        self.spins = {name: QSpinBox(self) for name in ("x", "y", "width", "height")}
        height, width = self.image_shape
        self.spins["x"].setRange(0, max(width - 1, 0)); self.spins["y"].setRange(0, max(height - 1, 0))
        self.spins["width"].setRange(1, width); self.spins["height"].setRange(1, height)
        form = QGridLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(6)
        form.setVerticalSpacing(3)
        for index, name in enumerate(("x", "y", "width", "height")):
            row, column = divmod(index, 2)
            form.addWidget(QLabel(name.title(), self), row, column * 2)
            form.addWidget(self.spins[name], row, column * 2 + 1)
        self.save_button = QPushButton("Save ROI", self); self.reset_button = QPushButton("Reset to Default", self)
        for button in (self.save_button, self.reset_button):
            button.setProperty("compact", True)
            button.setMinimumHeight(26)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        actions = QHBoxLayout(); actions.setSpacing(7); actions.addWidget(self.save_button, 1); actions.addWidget(self.reset_button, 1)
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(4); layout.addWidget(self.use_roi); layout.addLayout(form); layout.addLayout(actions)
        self.use_roi.toggled.connect(self._emit); self.save_button.clicked.connect(self.save); self.reset_button.clicked.connect(self.reset)
        for spin in self.spins.values(): spin.valueChanged.connect(self._spin_changed)
        self.reload()

    def roi(self) -> ImageROI:
        return ImageROI(*(self.spins[name].value() for name in ("x", "y", "width", "height")))

    def active_roi(self) -> ImageROI | None:
        return self.roi() if self.use_roi.isChecked() else None

    def set_state(self, roi: ImageROI, enabled: bool):
        bounded, warnings = clamp_roi(roi, self.image_shape)
        self._set_roi(bounded)
        blocked = self.use_roi.blockSignals(True)
        self.use_roi.setChecked(bool(enabled))
        self.use_roi.blockSignals(blocked)
        for warning in warnings:
            self.warningRaised.emit(warning)
        self._emit()

    def reconfigure(self, *, image_shape, runtime_path, configured=None):
        self.image_shape = tuple(image_shape)
        self.runtime_path = runtime_path
        self.configured = configured
        height, width = self.image_shape
        self.spins["x"].setRange(0, max(width - 1, 0)); self.spins["y"].setRange(0, max(height - 1, 0))
        self.spins["width"].setRange(1, width); self.spins["height"].setRange(1, height)
        self.use_roi.setChecked(False)
        self.reload()

    def reload(self):
        roi, source, warnings = resolve_roi(
            runtime_path=self.runtime_path,
            configured=self.configured,
            shape=self.image_shape,
        )
        self._set_roi(roi)
        blocked = self.use_roi.blockSignals(True)
        self.use_roi.setChecked(source in {"runtime", "configured"})
        self.use_roi.blockSignals(blocked)
        for warning in warnings: self.warningRaised.emit(warning)
        self._emit()

    def save(self):
        save_roi(self.runtime_path, self.roi()); self._emit()

    def reset(self):
        try:
            path = self.runtime_path
            if path and str(path) != "/__missing_roi__":
                from pathlib import Path
                Path(path).unlink(missing_ok=True)
        except OSError as exc:
            self.warningRaised.emit(f"Could not remove saved ROI: {exc}")
        roi, source, warnings = resolve_roi(
            runtime_path="/__missing_roi__",
            configured=self.configured,
            shape=self.image_shape,
        )
        self._set_roi(roi)
        blocked = self.use_roi.blockSignals(True)
        self.use_roi.setChecked(source in {"runtime", "configured"})
        self.use_roi.blockSignals(blocked)
        for warning in warnings: self.warningRaised.emit(warning)
        self._emit()

    def attach_axes(self, axes, *, extent):
        if self._selector is not None:
            self._selector.set_active(False)
        self._extent = tuple(float(value) for value in extent)
        self._selector = RectangleSelector(axes, self._dragged, useblit=False, button=[1], minspanx=2, minspany=2, spancoords="pixels", interactive=True)
        self._selector.set_active(self.use_roi.isChecked())
        return self.draw_overlay(axes, extent=extent)

    def draw_overlay(self, axes, *, extent):
        if not self.use_roi.isChecked(): return None
        xmin, xmax, ymin, ymax = (float(value) for value in extent)
        height, width = self.image_shape; roi = self.roi()
        x = xmin + roi.x / width * (xmax - xmin); y = ymin + roi.y / height * (ymax - ymin)
        patch = Rectangle((x, y), roi.width / width * (xmax - xmin), roi.height / height * (ymax - ymin), fill=False, edgecolor="#ffcc66", linewidth=1.5)
        axes.add_patch(patch); self._rectangle = patch; return patch

    def _dragged(self, click, release):
        xmin, xmax, ymin, ymax = self._extent; height, width = self.image_shape
        x0, x1 = sorted((click.xdata, release.xdata)); y0, y1 = sorted((click.ydata, release.ydata))
        if None in (x0, x1, y0, y1): return
        roi = ImageROI(max(0, round((x0 - xmin) / (xmax - xmin) * width)), max(0, round((y0 - ymin) / (ymax - ymin) * height)), max(1, round((x1 - x0) / (xmax - xmin) * width)), max(1, round((y1 - y0) / (ymax - ymin) * height)))
        bounded, warnings = clamp_roi(roi, self.image_shape); self._set_roi(bounded)
        for warning in warnings: self.warningRaised.emit(warning)
        self._emit()

    def _set_roi(self, roi):
        for name, value in roi.as_dict().items(): self.spins[name].blockSignals(True); self.spins[name].setValue(value); self.spins[name].blockSignals(False)

    def _spin_changed(self, _value):
        bounded, warnings = clamp_roi(self.roi(), self.image_shape); self._set_roi(bounded)
        for warning in warnings: self.warningRaised.emit(warning)
        self._emit()

    def _emit(self, *_args):
        if self._selector is not None: self._selector.set_active(self.use_roi.isChecked())
        self.roiChanged.emit(self.roi(), self.use_roi.isChecked())
