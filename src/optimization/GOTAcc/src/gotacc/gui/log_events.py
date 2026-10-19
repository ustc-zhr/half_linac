from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class GuiLogEvent:
    """One structured entry in the Studio-wide activity log."""

    message: str
    category: str
    level: str = "INFO"
    timestamp: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        message = str(self.message).strip()
        category = str(self.category).strip().upper() or "APP"
        level = str(self.level).strip().upper() or "INFO"
        if not message:
            raise ValueError("GUI log events require a non-empty message")
        object.__setattr__(self, "message", message)
        object.__setattr__(self, "category", category)
        object.__setattr__(self, "level", level)

    @property
    def is_warning(self) -> bool:
        return self.level in {"WARNING", "ERROR"}

    def display_text(self) -> str:
        tags = [self.category]
        if self.level != "INFO" and self.level != self.category:
            tags.append(self.level)
        tag_text = " ".join(f"[{tag}]" for tag in tags)
        return f"[{self.timestamp.strftime('%H:%M:%S')}] {tag_text} {self.message}"
