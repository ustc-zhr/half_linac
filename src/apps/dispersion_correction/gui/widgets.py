from __future__ import annotations

from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout


class StatusItem(QFrame):
    def __init__(self, title: str, value: str = "-") -> None:
        super().__init__()
        self.setObjectName("statusItem")
        self.setProperty("tone", "subtle")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(2)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("statusTitle")
        self.title_label.setProperty("role", "title")
        self.value_label = QLabel(value)
        self.value_label.setObjectName("statusValue")
        self.value_label.setProperty("role", "value")
        self.value_label.setProperty("tone", "subtle")
        self.value_label.setWordWrap(True)
        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)

    def set_value(self, value: str, tone: str = "subtle") -> None:
        self.value_label.setText(value)
        self.setProperty("tone", tone)
        self.value_label.setProperty("tone", tone)
        self._refresh_style()

    def _refresh_style(self) -> None:
        for widget in (self, self.value_label):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()


class StatusStrip(QFrame):
    def __init__(self, items: list[tuple[str, str]]) -> None:
        super().__init__()
        self.setObjectName("statusStrip")
        self.items: dict[str, StatusItem] = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(0)
        for title, value in items:
            if self.items:
                separator = QFrame()
                separator.setObjectName("statusSeparator")
                layout.addWidget(separator)
            item = StatusItem(title, value)
            item.setMinimumWidth(94)
            self.items[title] = item
            layout.addWidget(item)
        layout.addStretch(1)

    def set_value(self, title: str, value: str, tone: str = "subtle") -> None:
        if title in self.items:
            self.items[title].set_value(value, tone)
