import os

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from jitter_analysis.gui.widgets import scan_panel as scan_panel_module


pytestmark = pytest.mark.skipif(
    scan_panel_module.QtWidgets is None,
    reason="PyQt5 is required for ScanPanel tests",
)


@pytest.fixture(scope="module")
def qt_app():
    app = scan_panel_module.QtWidgets.QApplication.instance()
    return app or scan_panel_module.QtWidgets.QApplication([])


def _select_stop_mode(panel, mode: str) -> None:
    panel.stop_condition_combo.setCurrentIndex(panel.stop_condition_combo.findData(mode))


def test_monitor_stop_condition_shows_only_relevant_controls(qt_app):
    panel = scan_panel_module.ScanPanel()

    _select_stop_mode(panel, "samples")
    assert not panel.count_spin.isHidden()
    assert panel.duration_spin.isHidden()
    assert not panel.monitor_estimate_label.isHidden()

    _select_stop_mode(panel, "duration")
    assert panel.count_spin.isHidden()
    assert not panel.duration_spin.isHidden()
    assert not panel.monitor_estimate_label.isHidden()

    _select_stop_mode(panel, "continuous")
    assert panel.count_spin.isHidden()
    assert panel.duration_spin.isHidden()
    assert panel.monitor_estimate_label.isHidden()
