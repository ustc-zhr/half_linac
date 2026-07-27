from __future__ import annotations

from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout


class StatusItem(QFrame):
    def __init__(self, title: str, value: str, parent=None):
        super().__init__(parent)
        self.setObjectName("statusItem")
        self.setProperty("tone", "subtle")
        self.title_label = QLabel(title, self)
        self.title_label.setProperty("role", "statusTitle")
        self.value_label = QLabel(value, self)
        self.value_label.setProperty("role", "statusValue")
        self.value_label.setProperty("tone", "subtle")
        self.value_label.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(2)
        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)

    def set_value(self, value: str, tone: str = "subtle") -> None:
        self.value_label.setText(value)
        self.value_label.setProperty("tone", tone)
        self.setProperty("tone", tone)
        for widget in (self, self.value_label):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()


class StatusStrip(QFrame):
    def __init__(self, items: tuple[tuple[str, str], ...], parent=None):
        super().__init__(parent)
        self.setObjectName("statusStrip")
        self.items: dict[str, StatusItem] = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(0)
        for title, value in items:
            if self.items:
                separator = QFrame(self)
                separator.setObjectName("statusSeparator")
                layout.addWidget(separator)
            item = StatusItem(title, value, self)
            item.setMinimumWidth(94)
            self.items[title] = item
            layout.addWidget(item)
        layout.addStretch(1)

    def set_value(self, title: str, value: str, tone: str = "subtle") -> None:
        self.items[title].set_value(value, tone)
