"""Application entry point."""

from __future__ import annotations

from typing import Sequence

from .bootstrap import create_application, create_main_window


def main(argv: Sequence[str] | None = None) -> int:
    app = create_application(argv)
    window = create_main_window()
    window.show()
    exec_fn = getattr(app, "exec", None) or getattr(app, "exec_", None)
    if exec_fn is None:
        raise RuntimeError("Unsupported Qt application object")
    return exec_fn()
