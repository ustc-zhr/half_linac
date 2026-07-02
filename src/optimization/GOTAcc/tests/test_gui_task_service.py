from pathlib import Path

import pytest

from gotacc.gui.services.task_service import TaskService


def _offline_task(tmp_path: Path) -> dict:
    return {
        "task_name": "preview_task",
        "description": "",
        "mode": "Offline",
        "objective_type": "Single Objective",
        "algorithm": "BO",
        "max_evaluations": 3,
        "seed": 1,
        "workdir": str(tmp_path),
        "test_function": "sphere",
        "variables": [
            {
                "Enable": "Yes",
                "Name": "x0",
                "Lower": "-1",
                "Upper": "1",
                "Initial": "0",
            }
        ],
        "objectives": [
            {
                "Enable": "Yes",
                "Name": "sphere",
                "Direction": "maximize",
                "Weight": "1",
            }
        ],
        "constraints": [],
        "algorithm_params": [],
    }


def test_gui_task_config_build_has_no_filesystem_side_effect(tmp_path):
    task = _offline_task(tmp_path)

    cfg = TaskService.build_task_config(task)

    assert cfg.backend.type == "offline"
    assert cfg.backend.kwargs["combine_mode"] == "weighted_sum"
    assert cfg.runtime.history_path == str(tmp_path / "save" / "preview_task_history.dat")
    assert not (tmp_path / "save").exists()


def test_gui_preview_has_no_filesystem_side_effect(tmp_path):
    task = _offline_task(tmp_path)

    preview = TaskService.to_preview_text(task)

    assert "preview_task" in preview
    assert not (tmp_path / "save").exists()


def test_gui_export_creates_runtime_directory(tmp_path):
    task = _offline_task(tmp_path)

    TaskService.export_task_config(task, tmp_path / "exports" / "task.yaml")

    assert (tmp_path / "exports" / "task.yaml").is_file()
    assert (tmp_path / "save").is_dir()


def test_gui_main_window_offscreen_smoke(monkeypatch):
    pytest.importorskip("PyQt5")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    import sys

    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QApplication

    import gotacc.gui.main  # noqa: F401 - configures Qt runtime paths
    import gotacc.gui.views.main_window as main_window_module
    from gotacc.gui.theme import DARK_THEME_KEY, apply_theme, current_theme_key
    from gotacc.gui.views.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme(app, "warm_studio")
    monkeypatch.setattr(main_window_module, "save_theme_key", lambda key: key)
    window = MainWindow()
    window.resize(1280, 820)
    window.show()
    app.processEvents()

    try:
        assert window.frame_workspace_header.objectName() == "summaryPanel"
        assert current_theme_key(app) == DARK_THEME_KEY
        assert window.log_toggle_button is not None
        assert window.theme_toggle_button is not None
        assert window.ui.pushButton_newOfflineTask.text() == "New Task"
        assert not window.ui.pushButton_newOnlineTask.isVisible()
        assert window.ui.pushButton_openConfig.text() == "Open Project"
        assert window.ui.pushButton_saveProject.text() == "Save Project"
        assert window.ui.tabWidget_configure.tabText(0) == "Task Builder"
        assert window.ui.tabWidget_configure.tabBar().elideMode() == Qt.ElideNone
        task_table_tabs = [
            window.task_ui.tabWidget_tables.tabText(index)
            for index in range(window.task_ui.tabWidget_tables.count())
        ]
        assert task_table_tabs == ["Variables", "Objectives", "Constraints"]
        assert window.task_ui.tabWidget_tables.tabBar().elideMode() == Qt.ElideNone
        assert window.task_ui.pushButton_openBoundsTools.text() == "Bounds"
        assert (
            window.task_ui.horizontalLayout_variablesToolbarActions.itemAt(0).widget()
            is window.task_ui.pushButton_openBoundsTools
        )
        footer_buttons = [
            window.task_ui.pushButton_preview,
            window.task_ui.pushButton_validate,
            window.task_ui.pushButton_export,
        ]
        assert [button.text() for button in footer_buttons] == ["Preview", "Validate", "Export Task"]
        assert all(button.height() == 24 for button in footer_buttons)
        assert len({button.width() for button in footer_buttons}) == 1
        assert [
            window.task_ui.horizontalLayout_actionBar.itemAt(index).widget()
            for index in range(3)
        ] == footer_buttons
        run_action_buttons = [
            window.ui.pushButton_validateTask,
            window.ui.pushButton_startRun,
            window.ui.pushButton_pauseRun,
            window.ui.pushButton_stopRun,
        ]
        assert len({button.height() for button in run_action_buttons}) == 1
        assert all(button.property("compact") is True for button in run_action_buttons)
        assert window.machine_ui.groupBox_connection.title() == "EPICS"
        assert not window.machine_ui.label_caAddress.isVisible()
        assert not window.machine_ui.lineEdit_caAddress.isVisible()
        assert not window.machine_ui.checkBox_autoConnect.isVisible()
        assert not window.machine_ui.pushButton_connect.isVisible()
        assert not window.machine_ui.pushButton_disconnect.isVisible()
        assert window.machine_ui.pushButton_test.text() == "Check"
        assert window.machine_ui.groupBox_connection.maximumHeight() == 82
        assert window.machine_ui.pushButton_test.property("inlineAction") is True
        assert window.machine_ui.label_statusValue.property("role") == "statusPill"
        assert window.machine_ui.frame_pvPresetLibrary.maximumHeight() == 34
        assert window.machine_ui.pushButton_selectPvs.text() == "Select PVs"
        assert window.machine_ui.pushButton_applySelectedPvLibrary.text() == "Sync To Task"
        assert window.machine_ui.pushButton_selectPvs.property("inlineAction") is True
        assert window.machine_ui.pushButton_applySelectedPvLibrary.property("inlineAction") is True
        assert (
            window.machine_ui.horizontalLayout_pvLibraryControls.itemAt(0).widget()
            is window.machine_ui.pushButton_selectPvs
        )
        assert (
            window.machine_ui.horizontalLayout_pvLibraryControls.itemAt(1).widget()
            is window.machine_ui.pushButton_applySelectedPvLibrary
        )
        assert not window.offline_ui.frame_offlineHero.isVisible()
        assert not window.offline_ui.frame_offlinePlaceholder.isVisible()
        assert window.offline_ui.groupBox_benchmark.title() == "Benchmark"
        assert window.run_ui.groupBox_runtime.maximumHeight() == 94
        assert window.run_ui.frame_eval.objectName() == "statusItem"
        assert window.run_ui.label_evalTitle.property("role") == "title"
        assert window.run_ui.label_evalValue.property("role") == "value"
        window.ui.pushButton_newOfflineTask.click()
        app.processEvents()
        assert window.task_ui.comboBox_mode.currentText() == "Online EPICS"
        assert window.task_ui.lineEdit_taskName.text() == "online_task"
        window.task_ui.comboBox_mode.setCurrentText("Online EPICS")
        app.processEvents()
        assert window.ui.tabWidget_configure.isTabVisible(window.CONFIGURE_TAB_MACHINE)
        assert not window.ui.tabWidget_configure.isTabVisible(window.CONFIGURE_TAB_OFFLINE)
        window.ui.tabWidget_configure.setCurrentIndex(window.CONFIGURE_TAB_MACHINE)
        window.task_ui.comboBox_mode.setCurrentText("Offline")
        app.processEvents()
        assert not window.ui.tabWidget_configure.isTabVisible(window.CONFIGURE_TAB_MACHINE)
        assert window.ui.tabWidget_configure.isTabVisible(window.CONFIGURE_TAB_OFFLINE)
        assert window.ui.tabWidget_configure.currentIndex() == window.CONFIGURE_TAB_OFFLINE
        assert not window.ui.tabWidget_bottomOutput.isVisible()
        assert window.workspace_shell_layout.indexOf(window.ui.tabWidget_bottomOutput) >= 0
        assert window.ui.splitter_centerVertical.indexOf(window.ui.tabWidget_bottomOutput) == -1
        bottom_tabs = [
            window.ui.tabWidget_bottomOutput.tabText(index)
            for index in range(window.ui.tabWidget_bottomOutput.count())
        ]
        assert bottom_tabs == ["Log"]
        window.log_toggle_button.click()
        app.processEvents()
        assert window.ui.tabWidget_bottomOutput.isVisible()
        assert not window.ui.groupBox_environmentStatus.isVisible()
    finally:
        window.close()
