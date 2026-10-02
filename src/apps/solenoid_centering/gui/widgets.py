from __future__ import annotations

from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout


class StatusItem(QFrame):
    def __init__(self, title: str, value: str, parent=None):
        super().__init__(parent)
        self.setObjectName("statusItem")
        self.setProperty("tone", "subtle")
        self.title_label = QLabel("Readback" if title == "READBACK VERIFIED" else title.capitalize(), self)
        self.title_label.setProperty("role", "statusTitle")
        self.value_label = QLabel(value, self)
        self.value_label.setProperty("role", "statusValue")
        self.value_label.setProperty("tone", "subtle")
        self.value_label.setWordWrap(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(2)
        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        self._fit_content()

    def _fit_content(self) -> None:
        self.setMinimumWidth(max(94, self.title_label.sizeHint().width() + 16,
                                 self.value_label.sizeHint().width() + 16))

    def set_value(self, value: str, tone: str = "subtle") -> None:
        self.value_label.setText(value)
        self._fit_content()
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
        self._scope = "single"
        self._initial = {title: (value, "subtle") for title, value in items}
        self._scopes = {"single": dict(self._initial)}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(0)
        for title, value in items:
            if self.items:
                separator = QFrame(self)
                separator.setObjectName("statusSeparator")
                layout.addWidget(separator)
            item = StatusItem(title, self._display_value(title, value), self)
            self.items[title] = item
            layout.addWidget(item)
        layout.addStretch(1)

    @staticmethod
    def _display_value(title: str, value: str) -> str:
        if title not in ("PRESET", "LAST RESULT") and value.isupper():
            return value.capitalize()
        return value

    def set_scope(self, scope: str) -> None:
        self._scope = scope
        self.items["PRESET"].title_label.setText("Group" if scope == "joint" else "Preset")
        self.items["PRESET"]._fit_content()
        values = self._scopes.setdefault(scope, dict(self._initial))
        for title, item in self.items.items():
            value, tone = values[title]
            item.set_value(self._display_value(title, value), tone)

    def set_value(self, title: str, value: str, tone: str = "subtle",
                  *, scope: str | None = None) -> None:
        target = scope or self._scope
        self._scopes.setdefault(target, dict(self._initial))[title] = (value, tone)
        if target == self._scope:
            self.items[title].set_value(self._display_value(title, value), tone)
