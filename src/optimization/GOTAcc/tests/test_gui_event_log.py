from __future__ import annotations

import sys
from datetime import datetime

import pytest

from gotacc.gui.log_events import GuiLogEvent


def test_gui_log_event_formats_structured_fields():
    event = GuiLogEvent(
        message="  restore failed  ",
        category="run",
        level="warning",
        timestamp=datetime(2026, 10, 19, 8, 7, 6),
    )

    assert event.category == "RUN"
    assert event.level == "WARNING"
    assert event.is_warning
    assert event.display_text() == "[08:07:06] [RUN] [WARNING] restore failed"


def test_run_events_use_bottom_log_without_live_duplicate(monkeypatch):
    pytest.importorskip("PyQt5")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from PyQt5.QtWidgets import QApplication

    import gotacc.gui.main  # noqa: F401 - configures Qt runtime paths
    from gotacc.gui.views.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    try:
        window.ui.plainTextEdit_consoleLog.clear()
        window.ui.plainTextEdit_warningError.clear()
        window.ui.plainTextEdit_pvLog.clear()
        window._log_events.clear()

        assert window.run_ui.splitter_main.widget(1) is window.run_ui.groupBox_table
        assert not hasattr(window.run_ui, "groupBox_events")
        assert not hasattr(window.run_ui, "pushButton_openRunLog")

        window._log_event("Run started.")
        assert "[RUN] Run started." in window.ui.plainTextEdit_consoleLog.toPlainText()

        window._log_worker_output("optimizer iteration 1")
        assert "[CONSOLE] optimizer iteration 1" in window.ui.plainTextEdit_consoleLog.toPlainText()

        window._log_worker_output("Policy triggered for transmission [replace]: 0 → 1")
        assert "[POLICY] Policy triggered" in window.ui.plainTextEdit_consoleLog.toPlainText()

        window._log_pv("Initial values restored.")
        assert "[MACHINE] Initial values restored." in window.ui.plainTextEdit_consoleLog.toPlainText()
        assert "[MACHINE] Initial values restored." in window.ui.plainTextEdit_pvLog.toPlainText()

        window.run_controller.on_session_warning("restore warning")
        combined = window.ui.plainTextEdit_consoleLog.toPlainText()
        assert combined.count("restore warning") == 1
        assert "[RUN] [WARNING] restore warning" in combined
        assert "restore warning" in window.ui.plainTextEdit_warningError.toPlainText()
        assert not window.ui.tabWidget_bottomOutput.isHidden()
    finally:
        window.close()
        app.processEvents()
