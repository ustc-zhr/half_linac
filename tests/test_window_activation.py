import sys
from pathlib import Path
from types import ModuleType
import unittest
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT.parent))

from half_linac.src.shared.window_activation import raise_qt_window


class _Qt:
    WindowMinimized = 1
    WindowActive = 2
    WindowStaysOnTopHint = 4


class _Timer:
    callbacks = []

    @classmethod
    def singleShot(cls, _delay_ms, callback):
        cls.callbacks.append(callback)


class _Window:
    def __init__(self):
        self.flags = 0
        self.state = _Qt.WindowMinimized
        self.calls = []

    def windowFlags(self):
        return self.flags

    def setWindowFlag(self, flag, enabled):
        self.calls.append(("flag", flag, enabled))
        if enabled:
            self.flags |= flag
        else:
            self.flags &= ~flag

    def windowState(self):
        return self.state

    def setWindowState(self, state):
        self.state = state
        self.calls.append(("state", state))

    def show(self):
        self.calls.append(("show",))

    def hide(self):
        self.calls.append(("hide",))

    def raise_(self):
        self.calls.append(("raise",))

    def activateWindow(self):
        self.calls.append(("activate",))


class WindowActivationTests(unittest.TestCase):
    def test_wsl_raise_remaps_window_without_changing_always_on_top(self):
        qt_core = ModuleType("PyQt5.QtCore")
        qt_core.Qt = _Qt
        qt_core.QTimer = _Timer
        window = _Window()
        _Timer.callbacks = []

        with patch.dict(sys.modules, {"PyQt5.QtCore": qt_core}), patch.dict(
            "os.environ", {"WSL_DISTRO_NAME": "Ubuntu"}, clear=False
        ):
            raise_qt_window(window)

        self.assertEqual(window.state, _Qt.WindowActive)
        self.assertFalse(window.flags & _Qt.WindowStaysOnTopHint)
        self.assertEqual(window.calls[-1], ("hide",))
        self.assertEqual(len(_Timer.callbacks), 1)

        _Timer.callbacks[0]()

        self.assertFalse(window.flags & _Qt.WindowStaysOnTopHint)
        self.assertEqual(
            window.calls[-3:],
            [("show",), ("raise",), ("activate",)],
        )

    def test_non_wsl_raise_does_not_change_always_on_top_flag(self):
        qt_core = ModuleType("PyQt5.QtCore")
        qt_core.Qt = _Qt
        qt_core.QTimer = _Timer
        window = _Window()
        _Timer.callbacks = []

        with patch.dict(sys.modules, {"PyQt5.QtCore": qt_core}), patch.dict(
            "os.environ", {}, clear=True
        ):
            raise_qt_window(window)

        self.assertFalse(window.flags & _Qt.WindowStaysOnTopHint)
        self.assertFalse(_Timer.callbacks)
        self.assertIn(("raise",), window.calls)
        self.assertIn(("activate",), window.calls)
