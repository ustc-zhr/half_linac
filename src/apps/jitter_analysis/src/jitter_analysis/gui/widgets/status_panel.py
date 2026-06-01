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
            self.setStyleSheet(
                """
                QWidget#statusStrip {
                    background: #fffaf3;
                    border: 1px solid #ddd4c7;
                    border-radius: 12px;
                }
                QFrame#statusItem {
                    background: transparent;
                    border: none;
                    border-left: 4px solid #d8d0c3;
                }
                QFrame#statusItem[tone="info"] {
                    border-left-color: #60a5fa;
                }
                QFrame#statusItem[tone="success"] {
                    border-left-color: #86efac;
                }
                QFrame#statusItem[tone="warning"] {
                    border-left-color: #f59e0b;
                }
                QFrame#statusItem[tone="danger"] {
                    border-left-color: #f87171;
                }
                QFrame#statusSeparator {
                    background: #e7dfd3;
                    min-width: 1px;
                    max-width: 1px;
                    border: none;
                }
                QLabel[role="title"] {
                    color: #7c6f60;
                    font-size: 10px;
                    font-weight: 700;
                    letter-spacing: 0.8px;
                }
                QLabel[role="value"][tone="subtle"] {
                    color: #475569;
                    font-size: 15px;
                    font-weight: 700;
                }
                QLabel[role="value"][tone="info"] {
                    color: #1d4ed8;
                    font-size: 15px;
                    font-weight: 700;
                }
                QLabel[role="value"][tone="success"] {
                    color: #166534;
                    font-size: 15px;
                    font-weight: 700;
                }
                QLabel[role="value"][tone="warning"] {
                    color: #b45309;
                    font-size: 15px;
                    font-weight: 700;
                }
                QLabel[role="value"][tone="danger"] {
                    color: #b91c1c;
                    font-size: 15px;
                    font-weight: 700;
                }
                """
            )
            layout = QtWidgets.QHBoxLayout(self)
            layout.setContentsMargins(12, 10, 12, 10)
            layout.setSpacing(0)

            self.connection_value = self._add_item(layout, "connection", "CONNECTION", "Not checked")
            self.mode_value = self._add_item(layout, "mode", "MODE", "Idle")
            self.sample_value = self._add_item(layout, "sample", "SAMPLE", "-")
            self.step_value = self._add_item(layout, "step", "STEP", "-")
            self.current_pv_value = self._add_item(layout, "current", "CURRENT", "--")
            self.timestamp_value = self._add_item(layout, "time", "TIME", "--")
            layout.addStretch(1)

        def _add_item(self, layout, key: str, title: str, value: str):
            if self._items:
                separator = QtWidgets.QFrame(self)
                separator.setObjectName("statusSeparator")
                separator.setFrameShape(QtWidgets.QFrame.VLine)
                separator.setFrameShadow(QtWidgets.QFrame.Plain)
                layout.addWidget(separator)
            container = QtWidgets.QFrame(self)
            container.setObjectName("statusItem")
            container.setProperty("tone", "subtle")
            container.setMinimumWidth(118)
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
            layout.addWidget(container)
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

        def set_connection(self, text: str, tone: str = "subtle") -> None:
            self.set_item("connection", text, tone=tone)

        def set_mode(self, text: str, tone: str = "subtle") -> None:
            self.set_item("mode", text, tone=tone)

        def set_sample(self, text: str, tone: str = "subtle") -> None:
            self.set_item("sample", text, tone=tone)

        def set_step(self, text: str, tone: str = "subtle") -> None:
            self.set_item("step", text, tone=tone)

        def set_current(self, text: str, tone: str = "subtle") -> None:
            self.set_item("current", text, tone=tone)

        def set_time(self, text: str, tone: str = "subtle") -> None:
            self.set_item("time", text, tone=tone)

else:

    class StatusPanel:  # pragma: no cover - fallback for non-GUI tests
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("PyQt5 is required to create StatusPanel")
