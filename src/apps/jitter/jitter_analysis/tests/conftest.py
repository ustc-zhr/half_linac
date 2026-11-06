from __future__ import annotations

import os

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt5 import QtWidgets
except ImportError:  # pragma: no cover - optional GUI dependency
    QtWidgets = None


_SESSION_QT_APP = None


@pytest.fixture(scope="session", autouse=True)
def session_qt_application():
    """Keep one QApplication alive across GUI test modules."""
    global _SESSION_QT_APP
    if QtWidgets is None:
        yield None
        return

    _SESSION_QT_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield _SESSION_QT_APP
