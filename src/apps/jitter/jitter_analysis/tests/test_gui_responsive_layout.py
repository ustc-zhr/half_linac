from __future__ import annotations

import os

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from jitter_analysis.gui import main_window as main_window_module
from jitter_analysis.gui.dialogs.epics_connection_dialog import (
    EpicsConnectionResultsDialog,
)
from jitter_analysis.gui.dialogs.pv_library_editor_dialog import (
    PVLibraryEditorDialog,
)
from jitter_analysis.gui.dialogs.pv_selector_dialog import PVSelectorDialog
from jitter_analysis.gui.dialogs.random_knob_config_dialog import (
    RandomKnobConfigDialog,
)
from jitter_analysis.gui.dialogs.run_browser_dialog import RunBrowserDialog
from jitter_analysis.gui.dialogs.setup_browser_dialog import SetupBrowserDialog
from jitter_analysis.gui.state import AppState
from jitter_analysis.gui.theme import apply_app_theme
from jitter_analysis.services.run_service import RunService
from jitter_analysis.services.task_service import TaskService


pytestmark = pytest.mark.skipif(
    main_window_module.QtWidgets is None,
    reason="PyQt5 is required for responsive GUI tests",
)


@pytest.mark.parametrize("theme_id", ["control_room", "night_shift"])
@pytest.mark.parametrize("window_size", [(1280, 720), (1920, 1080)])
def test_primary_pages_fit_supported_window_sizes(
    session_qt_application,
    monkeypatch,
    theme_id,
    window_size,
):
    app = session_qt_application
    apply_app_theme(app, theme_id)
    monkeypatch.setattr(
        main_window_module.MainWindow,
        "_try_load_default_config",
        lambda self: None,
    )
    window = main_window_module.MainWindow(
        AppState(),
        RunService(),
        TaskService(),
    )
    window.show()
    app.processEvents()
    window.resize(*window_size)
    app.processEvents()

    assert (window.width(), window.height()) == window_size
    assert window.minimumSizeHint().width() <= 1280
    assert window.findChild(
        main_window_module.QtWidgets.QFrame, "globalStatusBar"
    ).height() == 52

    window.main_tabs.setCurrentIndex(window.config_tab_index)
    app.processEvents()
    sidebar = window.findChild(
        main_window_module.QtWidgets.QScrollArea, "configSidebar"
    )
    task_content = window.findChild(
        main_window_module.QtWidgets.QWidget, "taskSetupContent"
    )
    assert 280 <= sidebar.width() <= 340
    assert task_content.width() <= 760

    window.main_tabs.setCurrentIndex(window.run_tab_index)
    app.processEvents()
    plot_surface = window.findChild(
        main_window_module.QtWidgets.QFrame, "runPlotSurface"
    )
    assert plot_surface.isVisibleTo(window)
    assert plot_surface.width() <= window.main_tabs.currentWidget().width()
    assert not window.status_panel.findChildren(
        main_window_module.QtWidgets.QFrame, "statusSeparator"
    )

    window.main_tabs.setCurrentIndex(window.analysis_page_index)
    app.processEvents()
    tab_bar = window.analysis_tabs.tabBar()
    assert not tab_bar.expanding()
    assert window.analysis_tabs.usesScrollButtons()
    assert window.analysis_tabs.width() <= window.main_tabs.currentWidget().width()
    assert window.findChild(
        main_window_module.QtWidgets.QFrame, "analysisSection"
    ) is None

    window.main_tabs.setCurrentIndex(window.training_page_index)
    app.processEvents()
    assert window.main_tabs.tabText(window.training_page_index) == "Training"
    assert window.training_page.width() <= window.main_tabs.width()
    assert window.training_page.height() <= window.main_tabs.height()

    window.close()


def test_supported_dialogs_share_compact_layout(
    session_qt_application,
):
    dialogs = [
        EpicsConnectionResultsDialog(connected=0, total=0, failures=[]),
        PVSelectorDialog(knobs=[], objects=[]),
        PVLibraryEditorDialog(
            objects=[],
            groups=[],
            save_callback=lambda *_args: None,
            save_as_callback=lambda *_args: None,
        ),
        RandomKnobConfigDialog(knobs=[]),
        RunBrowserDialog(),
        SetupBrowserDialog(),
    ]

    try:
        for dialog in dialogs:
            margins = dialog.layout().contentsMargins()
            assert (
                margins.left(),
                margins.top(),
                margins.right(),
                margins.bottom(),
            ) == (20, 20, 20, 16)
            assert dialog.layout().spacing() == 12
            assert dialog.findChildren(
                main_window_module.QtWidgets.QDialogButtonBox
            )
    finally:
        for dialog in dialogs:
            dialog.close()
