from __future__ import annotations

try:
    from PyQt5 import QtWidgets
except ImportError:  # pragma: no cover - optional runtime dependency
    QtWidgets = None


if QtWidgets is not None:

    class StatusPanel(QtWidgets.QWidget):
        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            self._items = {}
            self.setObjectName("statusStrip")
            self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            layout = QtWidgets.QHBoxLayout(self)
            self._layout = layout
            layout.setContentsMargins(12, 10, 12, 10)
            layout.setSpacing(0)

            self.run_id_value = self._add_item(layout, "run_id", "RUN ID", "--", 260)
            self.sample_value = self._add_item(layout, "sample", "SAMPLES", "-", 120)
            self.step_value = self._add_item(layout, "step", "STEP", "-", 110)
            self.elapsed_value = self._add_item(layout, "elapsed", "ELAPSED", "--", 140)

        def add_trailing_widget(self, widget) -> None:
            self._layout.addWidget(widget, 0)

        def _add_item(self, layout, key: str, title: str, value: str, min_width: int):
            if self._items:
                separator = QtWidgets.QFrame(self)
                separator.setObjectName("statusSeparator")
                separator.setFrameShape(QtWidgets.QFrame.VLine)
                separator.setFrameShadow(QtWidgets.QFrame.Plain)
                layout.addWidget(separator)
            container = QtWidgets.QFrame(self)
            container.setObjectName("statusItem")
            container.setProperty("tone", "subtle")
            container.setMinimumWidth(min_width)
            inner = QtWidgets.QVBoxLayout(container)
            inner.setContentsMargins(12, 2, 10, 2)
            inner.setSpacing(3)
            title_label = QtWidgets.QLabel(title)
            title_label.setProperty("role", "title")
            value_label = QtWidgets.QLabel(value)
            value_label.setProperty("role", "value")
            value_label.setProperty("tone", "subtle")
            value_label.setWordWrap(True)
            inner.addWidget(title_label)
            inner.addWidget(value_label)
            layout.addWidget(container, 2 if key == "run_id" else 1)
            self._items[key] = (container, value_label)
            return value_label

        def _apply_tone(self, container, value_label, tone: str) -> None:
            container.setProperty("tone", tone)
            value_label.setProperty("tone", tone)
            container.style().unpolish(container)
            container.style().polish(container)
            value_label.style().unpolish(value_label)
            value_label.style().polish(value_label)
            container.update()
            value_label.update()

        def set_item(self, key: str, text: str, tone: str = "subtle") -> None:
            item = self._items.get(key)
            if item is None:
                return
            container, value_label = item
            value_label.setText(text)
            self._apply_tone(container, value_label, tone)

        def set_run_id(self, text: str, tone: str = "subtle") -> None:
            self.set_item("run_id", text, tone=tone)

        def set_sample(self, text: str, tone: str = "subtle") -> None:
            self.set_item("sample", text, tone=tone)

        def set_step(self, text: str, tone: str = "subtle") -> None:
            self.set_item("step", text, tone=tone)

        def set_elapsed(self, text: str, tone: str = "subtle") -> None:
            self.set_item("elapsed", text, tone=tone)

else:

    class StatusPanel:  # pragma: no cover - fallback for non-GUI tests
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("PyQt5 is required to create StatusPanel")
