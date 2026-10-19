import json
from pathlib import Path

import numpy as np
import pytest

from gotacc.gui.services.task_service import SUPPORTED_GUI_OPTIMIZERS, TaskService
from gotacc.runners.task_runner import build_optimizer


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


def _offline_multi_task(tmp_path: Path, test_function: str, *, n_objectives: int = 2) -> dict:
    task = _offline_task(tmp_path)
    task.update(
        {
            "objective_type": "Multi Objective",
            "algorithm": "NSGA-II",
            "test_function": test_function,
            "max_evaluations": 12,
        }
    )
    n_variables = max(3, n_objectives)
    task["variables"] = [
        {
            "Enable": "Yes",
            "Name": f"x{index}",
            "Lower": "0",
            "Upper": "1",
            "Initial": "0.5",
        }
        for index in range(n_variables)
    ]
    task["objectives"] = [
        {
            "Enable": "Yes",
            "Name": f"f{index + 1}",
            "Direction": "maximize",
            "Weight": "1",
        }
        for index in range(n_objectives)
    ]
    return task


def test_gui_task_config_build_has_no_filesystem_side_effect(tmp_path):
    task = _offline_task(tmp_path)

    cfg = TaskService.build_task_config(task)

    assert cfg.backend.type == "offline"
    assert cfg.backend.kwargs["combine_mode"] == "weighted_sum"
    assert cfg.runtime.history_path == str(tmp_path / "save" / "preview_task_history.dat")
    assert not (tmp_path / "save").exists()


def test_run_archive_is_unique_and_preserves_task_identity(tmp_path):
    task = _offline_task(tmp_path)

    first = TaskService.create_run_archive(task)
    second = TaskService.create_run_archive(task)

    first_dir = Path(first["run_archive_dir"])
    second_dir = Path(second["run_archive_dir"])
    assert first_dir != second_dir
    assert first_dir.parent == tmp_path / "preview_task"
    assert (first_dir / "task_config.yaml").is_file()
    assert TaskService.build_task_config(first).runtime.history_path == str(first_dir / "history.dat")
    assert TaskService.normalized_task_identity(first) == TaskService.normalized_task_identity(task)


def test_gui_preview_has_no_filesystem_side_effect(tmp_path):
    task = _offline_task(tmp_path)

    preview = TaskService.to_preview_text(task)

    assert "preview_task" in preview
    assert not (tmp_path / "save").exists()


@pytest.mark.parametrize(
    ("test_function", "n_objectives"),
    [("tradeoff", 2), ("zdt1", 2), ("zdt2", 2), ("dtlz2", 3)],
)
def test_offline_multi_objective_benchmarks_build_vector_functions(
    tmp_path,
    test_function,
    n_objectives,
):
    task = _offline_multi_task(
        tmp_path,
        test_function,
        n_objectives=n_objectives,
    )

    valid, errors = TaskService.validate_task_data(task)
    cfg = TaskService.build_task_config(task)
    X = np.full((4, len(task["variables"])), 0.5, dtype=float)
    values = np.asarray(cfg.backend.kwargs["func"](X), dtype=float)

    assert valid, errors
    assert cfg.backend.kwargs["combine_mode"] == "vector"
    assert values.shape == (4, n_objectives)
    assert np.all(np.isfinite(values))


def test_offline_multi_objective_benchmark_names_are_separate_from_single_objective():
    assert TaskService.offline_test_function_names("Single Objective") == (
        "sphere",
        "rosenbrock",
        "ackley",
    )
    assert TaskService.offline_test_function_names("Multi Objective") == (
        "tradeoff",
        "zdt1",
        "zdt2",
        "dtlz2",
    )


def test_offline_benchmark_templates_include_complete_task_rows():
    for name in (
        *TaskService.offline_test_function_names("Single Objective"),
        *TaskService.offline_test_function_names("Multi Objective"),
    ):
        template = TaskService.offline_benchmark_template(name)
        assert template["variables"]
        assert template["objectives"]
        assert all(row["Enable"] == "Y" for row in template["variables"])
        assert all(row["Direction"] == "maximize" for row in template["objectives"])

    assert len(TaskService.offline_benchmark_template("zdt1")["variables"]) == 3
    assert len(TaskService.offline_benchmark_template("zdt1")["objectives"]) == 2
    assert len(TaskService.offline_benchmark_template("dtlz2")["variables"]) == 3


@pytest.mark.parametrize(
    "direction",
    [None, "", "min", "MAXIMIZE", " maximize ", "minimise"],
)
def test_objective_direction_validation_rejects_noncanonical_values(
    tmp_path,
    direction,
):
    task = _offline_task(tmp_path)
    task["objectives"][0]["Direction"] = direction

    valid, errors = TaskService.validate_task_data(task)

    assert not valid
    assert any("invalid Direction" in error for error in errors)
    with pytest.raises(ValueError, match="invalid Direction"):
        TaskService.build_task_config(task)


def test_objective_direction_validation_requires_explicit_value(tmp_path):
    task = _offline_task(tmp_path)
    task["objectives"][0].pop("Direction")

    valid, errors = TaskService.validate_task_data(task)

    assert not valid
    assert any("invalid Direction" in error for error in errors)
    with pytest.raises(ValueError, match="invalid Direction"):
        TaskService.build_task_config(task)


def test_minimize_direction_is_converted_to_maximize_convention(tmp_path):
    task = _offline_task(tmp_path)
    task["objectives"][0]["Direction"] = "minimize"

    valid, errors = TaskService.validate_task_data(task)
    cfg = TaskService.build_task_config(task)
    value = float(np.asarray(cfg.backend.kwargs["func"](np.asarray([1.0]))).reshape(-1)[0])

    assert valid, errors
    assert value == pytest.approx(1.0)


def test_offline_benchmark_selection_autofills_tables_without_overwriting_loaded_project(
    monkeypatch,
    tmp_path,
):
    pytest.importorskip("PyQt5")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from PyQt5.QtWidgets import QApplication

    import gotacc.gui.main  # noqa: F401 - configures Qt runtime paths
    from gotacc.gui.views.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.task_builder_controller.create_new_offline_task()
    window.task_ui.comboBox_objectiveType.setCurrentText("Multi Objective")
    window.task_ui.comboBox_testFunction.setCurrentText("zdt1")
    app.processEvents()

    task = window._current_task()
    assert task["test_function"] == "zdt1"
    assert [row["Name"] for row in task["variables"]] == ["x0", "x1", "x2"]
    assert all(row["Lower"] == "0" and row["Upper"] == "1" for row in task["variables"])
    assert [row["Name"] for row in task["objectives"]] == ["f1", "f2"]
    assert all(row["Direction"] == "maximize" for row in task["objectives"])

    loaded = _offline_multi_task(tmp_path, "zdt2")
    loaded["variables"][0]["Name"] = "custom_x"
    loaded["objectives"][0]["Name"] = "custom_f"
    window.task_builder_controller.apply_task_payload(loaded, goto_builder=False)
    app.processEvents()
    restored = window._current_task()
    assert restored["test_function"] == "zdt2"
    assert restored["variables"][0]["Name"] == "custom_x"
    assert restored["objectives"][0]["Name"] == "custom_f"
    window.close()


def test_objective_direction_uses_restricted_combo_and_round_trips(monkeypatch, tmp_path):
    pytest.importorskip("PyQt5")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from PyQt5.QtWidgets import QApplication, QComboBox

    import gotacc.gui.main  # noqa: F401 - configures Qt runtime paths
    from gotacc.gui.views.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    task = _offline_task(tmp_path)
    task["objectives"][0].update(
        {"Direction": "minimize", "Samples": "1", "Math": "mean"}
    )

    window.task_builder_controller.apply_task_payload(task, goto_builder=False)
    app.processEvents()

    table = window.task_ui.tableWidget_objectives
    headers = [
        table.horizontalHeaderItem(column).text()
        for column in range(table.columnCount())
    ]
    direction_column = headers.index("Direction")
    combo = table.cellWidget(0, direction_column)
    assert isinstance(combo, QComboBox)
    assert [combo.itemText(index) for index in range(combo.count())] == [
        "maximize",
        "minimize",
    ]
    assert combo.currentText() == "minimize"

    combo.setCurrentText("maximize")
    app.processEvents()
    assert window._current_task()["objectives"][0]["Direction"] == "maximize"

    task["objectives"][0]["Direction"] = "unsupported"
    with pytest.raises(ValueError, match="invalid Direction"):
        window.task_builder_controller.apply_task_payload(task, goto_builder=False)
    app.processEvents()
    assert table.cellWidget(0, direction_column).currentText() == "maximize"
    assert window._current_task()["objectives"][0]["Direction"] == "maximize"

    window.task_builder_controller.add_task_table_row("objectives")
    app.processEvents()
    assert table.cellWidget(1, direction_column).currentText() == "maximize"
    window.close()


def test_offline_zdt_and_dtlz_dimension_validation(tmp_path):
    zdt_task = _offline_multi_task(tmp_path, "zdt1", n_objectives=3)
    valid, errors = TaskService.validate_task_data(zdt_task)
    assert not valid
    assert any("ZDT1 requires exactly two" in error for error in errors)

    dtlz_task = _offline_multi_task(tmp_path, "dtlz2", n_objectives=3)
    dtlz_task["variables"] = dtlz_task["variables"][:2]
    valid, errors = TaskService.validate_task_data(dtlz_task)
    assert not valid
    assert any("DTLZ2 requires at least as many" in error for error in errors)

    bounds_task = _offline_multi_task(tmp_path, "zdt2", n_objectives=2)
    bounds_task["variables"][0]["Lower"] = "-1"
    valid, errors = TaskService.validate_task_data(bounds_task)
    assert not valid
    assert any("ZDT2 requires every variable to use bounds [0, 1]" in error for error in errors)


def test_gui_export_creates_runtime_directory(tmp_path):
    task = _offline_task(tmp_path)

    TaskService.export_task_config(task, tmp_path / "exports" / "task.yaml")

    assert (tmp_path / "exports" / "task.yaml").is_file()
    assert (tmp_path / "save").is_dir()


def test_gui_algorithm_registry_includes_all_builder_options():
    pytest.importorskip("PyQt5")
    from gotacc.gui.views.controllers.task_builder_controller import (
        MULTI_OBJECTIVE_ALGORITHMS,
        SINGLE_OBJECTIVE_ALGORITHMS,
    )

    normalized = {
        TaskService._optimizer_name_from_gui(name)
        for name in (*SINGLE_OBJECTIVE_ALGORITHMS, *MULTI_OBJECTIVE_ALGORITHMS)
    }

    assert normalized <= SUPPORTED_GUI_OPTIMIZERS
    assert "rcds" in normalized


def test_gui_rcds_task_config_builds_runner_optimizer(tmp_path):
    task = _offline_task(tmp_path)
    task["algorithm"] = "RCDS"
    task["algorithm_params"] = [
        {"Parameter": "step", "Value": "0.1", "Type": "float", "Note": ""},
        {"Parameter": "maxIt", "Value": "2", "Type": "int", "Note": ""},
    ]

    cfg = TaskService.build_task_config(task)
    optimizer = build_optimizer(
        task_cfg=cfg,
        objective_callable=cfg.backend.kwargs["func"],
        bounds=np.asarray(cfg.backend.bounds, dtype=float),
    )

    assert cfg.optimizer.name == "rcds"
    assert cfg.optimizer.kwargs["maxEval"] == task["max_evaluations"]
    assert optimizer.vrange.shape == (1, 2)


def test_algorithm_detail_round_trip_preserves_all_optimizer_kwargs(monkeypatch):
    pytest.importorskip("PyQt5")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from PyQt5.QtWidgets import QApplication

    from gotacc.gui.views.algorithm_ui_specs import parameter_ui_spec
    from gotacc.gui.views.controllers.task_builder_controller import (
        MULTI_OBJECTIVE_ALGORITHMS,
        SINGLE_OBJECTIVE_ALGORITHMS,
        TaskBuilderController,
    )
    from gotacc.gui.views.tool_dialogs import AlgorithmDetailDialog

    app = QApplication.instance() or QApplication([])
    controller = object.__new__(TaskBuilderController)
    controller._algorithm_param_specs_cache = {}

    for algorithm in (*SINGLE_OBJECTIVE_ALGORITHMS, *MULTI_OBJECTIVE_ALGORITHMS):
        _algorithm_key, specs = controller._recommended_param_specs(algorithm, [])
        assert specs, f"{algorithm} GUI parameter metadata is empty"
        records = [[name, default, dtype, note] for name, default, dtype, note in specs]
        before_dyn = TaskService._dynamic_params_to_dict(
            [
                {"Parameter": name, "Value": value, "Type": dtype, "Note": note}
                for name, value, dtype, note in records
            ]
        )
        task = {
            "algorithm": algorithm,
            "max_evaluations": 100,
            "seed": 7,
        }
        before_kwargs = TaskService._build_optimizer_kwargs(task, before_dyn, 2)

        dialog = AlgorithmDetailDialog(
            algorithm=algorithm,
            specs=specs,
            records=records,
            evaluation_budget=100,
        )
        after_records = dialog.parameter_records()
        after_dyn = TaskService._dynamic_params_to_dict(
            [
                {"Parameter": name, "Value": value, "Type": dtype, "Note": note}
                for name, value, dtype, note in after_records
            ]
        )
        after_kwargs = TaskService._build_optimizer_kwargs(task, after_dyn, 2)

        assert after_dyn == before_dyn
        assert set(after_kwargs) == set(before_kwargs)
        for key in before_kwargs:
            before_value = before_kwargs[key]
            after_value = after_kwargs[key]
            if isinstance(before_value, np.ndarray):
                np.testing.assert_array_equal(after_value, before_value)
            else:
                assert after_value == before_value
        assert "maximize" not in dialog.visible_parameter_names
        assert all(
            name in dialog.visible_parameter_names
            for name, *_rest in specs
            if not parameter_ui_spec(algorithm, name).hidden
        )
        dialog.deleteLater()

    app.processEvents()


def test_algorithm_detail_uses_choices_and_q_batch_dependency(monkeypatch):
    pytest.importorskip("PyQt5")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from PyQt5.QtWidgets import QApplication, QCheckBox, QComboBox

    from gotacc.gui.views.controllers.task_builder_controller import TaskBuilderController
    from gotacc.gui.views.tool_dialogs import AlgorithmDetailDialog

    app = QApplication.instance() or QApplication([])
    controller = object.__new__(TaskBuilderController)
    controller._algorithm_param_specs_cache = {}
    _algorithm_key, specs = controller._recommended_param_specs("MOBO", [])
    records = [[name, default, dtype, note] for name, default, dtype, note in specs]
    dialog = AlgorithmDetailDialog(algorithm="MOBO", specs=specs, records=records)

    acq_editor = dialog._editors["acq"][0]
    q_batch_editor = dialog._editors["q_batch_size"][0]
    assert isinstance(acq_editor, QComboBox)
    assert [acq_editor.itemText(index) for index in range(acq_editor.count())] == [
        "ehvi",
        "qehvi",
        "qnehvi",
    ]
    assert not q_batch_editor.isEnabled()

    acq_editor.setCurrentText("qehvi")
    app.processEvents()
    assert q_batch_editor.isEnabled()
    dialog.deleteLater()

    _algorithm_key, specs = controller._recommended_param_specs("MGGPO", [])
    records = [[name, default, dtype, note] for name, default, dtype, note in specs]
    dialog = AlgorithmDetailDialog(algorithm="MGGPO", specs=specs, records=records)
    all_history_editor = dialog._editors["use_all_history_for_gp"][0]
    history_limit_editor = dialog._editors["gp_history_max"][0]
    assert isinstance(all_history_editor, QCheckBox)
    assert history_limit_editor.isEnabled()

    all_history_editor.setChecked(True)
    app.processEvents()
    assert not history_limit_editor.isEnabled()
    all_history_editor.setChecked(False)
    app.processEvents()
    assert history_limit_editor.isEnabled()
    dialog.deleteLater()

    _algorithm_key, specs = controller._recommended_param_specs("BO", [])
    records = [[name, default, dtype, note] for name, default, dtype, note in specs]
    dialog = AlgorithmDetailDialog(algorithm="BO", specs=specs, records=records)
    optimizer_editor = dialog._editors["acq_optimizer"][0]
    options_editor = dialog._editors["acq_opt_kwargs"][0]
    assert not options_editor.spinBox_numRestarts.isHidden()
    assert not options_editor.spinBox_rawSamples.isHidden()
    assert options_editor.spinBox_candidates.isHidden()

    optimizer_editor.setCurrentText("random")
    app.processEvents()
    assert options_editor.spinBox_numRestarts.isHidden()
    assert options_editor.spinBox_rawSamples.isHidden()
    assert not options_editor.spinBox_candidates.isHidden()
    switched_records = dialog.parameter_records()
    switched_dyn = TaskService._dynamic_params_to_dict(
        [
            {"Parameter": name, "Value": value, "Type": dtype, "Note": note}
            for name, value, dtype, note in switched_records
        ]
    )
    assert switched_dyn["acq_opt_kwargs"] == {"n_candidates": 8192}
    dialog.deleteLater()


def test_canonical_policy_bindings_compile_targets_from_mapping_order():
    task = {
        "machine": {
            "mapping": [
                {"Role": "objective", "Name": "charge", "PV Name": "TEST:CHARGE"},
                {"Role": "objective", "Name": "fel_energy", "PV Name": "TEST:FEL"},
                {"Role": "constraint", "Name": "orbit_x", "PV Name": "TEST:BPM:X"},
            ],
            "policy_bindings": [
                {
                    "kind": "objective",
                    "target": "fel_energy",
                    "enabled": True,
                    "preset": "fel_energy_guard",
                    "policy": {
                        "name": "sample_guard",
                        "kwargs": {
                            "target": "stale_name",
                            "target_col": 99,
                            "conditions": [
                                {"metric": "mean_abs", "operator": "gt", "value": 1e6}
                            ],
                            "match": "all",
                            "action": {"type": "replace", "value": 0.0},
                        },
                    },
                },
                {
                    "kind": "constraint",
                    "target": "orbit_x",
                    "enabled": True,
                    "preset": "bpm_guard",
                    "policy": {
                        "name": "sample_guard",
                        "kwargs": {
                            "conditions": [
                                {"metric": "max_abs", "operator": "le", "value": 1e-9}
                            ],
                            "match": "all",
                            "action": {
                                "type": "violate_bound",
                                "delta_ratio": 0.1,
                                "delta_min": 1e-6,
                                "scale_floor": 1.0,
                            },
                        },
                    },
                },
            ],
        }
    }

    objective = TaskService._build_objective_policy_specs(task)
    constraint = TaskService._build_constraint_policy_specs(task)

    assert objective[0]["kwargs"]["target"] == "fel_energy"
    assert objective[0]["kwargs"]["target_col"] == 1
    assert constraint[0]["kwargs"]["target"] == "orbit_x"
    assert constraint[0]["kwargs"]["target_col"] == 0


def test_empty_canonical_bindings_override_legacy_policy_rows():
    task = {
        "machine": {
            "policy_bindings": [],
            "objective_policies": [
                {
                    "Enabled": "True",
                    "Policy Name": "fel_energy_guard",
                    "Kwargs JSON": "{}",
                }
            ],
        }
    }

    assert TaskService._build_objective_policy_specs(task) == []


def test_policy_binding_issues_are_targeted_and_check_violate_bound_setup():
    task = {
        "constraints": [
            {
                "Enable": "Y",
                "Name": "orbit_x",
                "Lower": "",
                "Upper": "",
            }
        ],
        "machine": {
            "mapping": [
                {
                    "Role": "constraint",
                    "Name": "orbit_x",
                    "PV Name": "TEST:BPM:X",
                }
            ],
            "policy_bindings": [
                {
                    "kind": "constraint",
                    "target": "orbit_x",
                    "enabled": True,
                    "preset": "bpm_guard",
                    "policy": {
                        "name": "sample_guard",
                        "kwargs": {
                            "target": "orbit_x",
                            "conditions": [
                                {
                                    "metric": "max_abs",
                                    "operator": "le",
                                    "value": 1e-9,
                                }
                            ],
                            "match": "all",
                            "action": {"type": "violate_bound"},
                        },
                    },
                }
            ],
        },
    }

    issues = TaskService.policy_binding_issues(task)

    assert issues == [
        {
            "binding_index": 0,
            "kind": "constraint",
            "target": "orbit_x",
            "message": (
                "Constraint policy for 'orbit_x': Mark infeasible requires "
                "Lower or Upper in Task Builder."
            ),
        }
    ]

    task["constraints"][0]["Upper"] = "1.0"
    assert TaskService.policy_binding_issues(task) == []

    task["machine"]["policy_bindings"][0]["target"] = "missing_bpm"
    issues = TaskService.policy_binding_issues(task)
    assert issues[0]["binding_index"] == 0
    assert issues[0]["target"] == "missing_bpm"
    assert "matching constraint PV Mapping row" in issues[0]["message"]


def test_gui_main_window_offscreen_smoke(monkeypatch, tmp_path):
    pytest.importorskip("PyQt5")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    import sys

    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import (
        QApplication,
        QDialog,
        QFrame,
        QGridLayout,
        QHeaderView,
        QPushButton,
        QSizePolicy,
    )

    import gotacc.gui.main  # noqa: F401 - configures Qt runtime paths
    import gotacc.gui.views.main_window as main_window_module
    import gotacc.gui.views.controllers.task_builder_controller as task_builder_controller_module
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
        assert window.label_workspace_run.text() == "Idle"
        assert window.label_workspace_machine.text() == "Not checked"
        assert not hasattr(window, "label_workspace_best")
        assert window.ui.groupBox_dashboardSummary.title() == "Run Readiness"
        assert window.ui.label_cardCurrentTaskTitle.text() == "Task Readiness"
        assert window.ui.label_cardModeTitle.text() == "Run Plan"
        assert window.ui.label_cardAlgorithmTitle.text() == "Backend Readiness"
        assert window.ui.label_cardStatusTitle.text() == "Last Outcome"
        assert window.ui.label_cardCurrentTaskValue.text() == "Not validated"
        assert window.ui.label_cardCurrentTaskValue.property("tone") == "warning"
        assert window.ui.label_cardModeValue.property("tone") == "info"
        assert window.ui.label_cardAlgorithmValue.property("tone") == "warning"
        assert "Vars 0" in window.ui.label_cardModeValue.text()
        assert window.ui.label_cardStatusValue.text() == "No run yet"
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
        for add_button in (
            window.task_ui.pushButton_addVariableRow,
            window.task_ui.pushButton_addObjectiveRow,
            window.task_ui.pushButton_addConstraintRow,
        ):
            assert add_button.isHidden()
            assert add_button.property("inlineAction") is True
            assert add_button.size().width() == 82
            assert add_button.size().height() == 24
        for remove_button in (
            window.task_ui.pushButton_removeVariableRows,
            window.task_ui.pushButton_removeObjectiveRows,
            window.task_ui.pushButton_removeConstraintRows,
        ):
            assert remove_button.property("inlineAction") is True
            assert remove_button.size().width() == 124
            assert remove_button.size().height() == 24
        preview_button = window.ui.pushButton_preview
        assert preview_button.text() == "Preview Task"
        assert window.ui.label_validationStatus.text() == "Not validated"
        assert preview_button.parent() is window.task_ui.frame_builderHero
        assert window.ui.label_validationStatus.parent() is window.ui.groupBox_runActions
        assert not preview_button.isHidden()
        assert preview_button.property("inlineAction") is True
        assert preview_button.property("runControl") is False
        assert preview_button.size().width() == 100
        assert preview_button.size().height() == 24
        assert (
            window.task_ui.horizontalLayout_builderSummaryActions.indexOf(
                window.task_ui.label_builderSummary
            )
            == 0
        )
        assert (
            window.task_ui.horizontalLayout_builderSummaryActions.indexOf(preview_button)
            == 1
        )

        preview_dialogs = []
        export_requests = []
        with monkeypatch.context() as preview_patch:
            preview_patch.setattr(
                task_builder_controller_module.QDialog,
                "exec_",
                lambda dialog: preview_dialogs.append(dialog) or QDialog.Rejected,
            )
            preview_patch.setattr(
                window.task_builder_controller,
                "export_config",
                lambda: export_requests.append(True),
            )
            window.task_builder_controller.show_task_preview()
        preview_actions = {
            button.text(): button for button in preview_dialogs[0].findChildren(QPushButton)
        }
        assert set(preview_actions) == {"Export TaskConfig", "Close"}
        preview_actions["Export TaskConfig"].click()
        assert export_requests == [True]
        assert window.task_ui.spinBox_seed.maximumWidth() == 160
        assert window.task_ui.spinBox_maxEval.maximumWidth() == 160
        assert window.task_ui.comboBox_mode.minimumWidth() == 180
        assert window.task_ui.comboBox_algorithm.minimumWidth() == 180
        assert window.task_ui.tabWidget_tables.documentMode()
        variables_header = window.task_ui.tableWidget_variables.horizontalHeader()
        assert variables_header.sectionResizeMode(0) == variables_header.Fixed
        assert variables_header.sectionSize(0) == 70
        assert variables_header.sectionResizeMode(1) == variables_header.Stretch
        assert variables_header.sectionResizeMode(5) == variables_header.Stretch
        window.task_builder_controller._set_validation_status(
            "Validated", "success", "Task validation passed."
        )
        assert window.ui.label_validationStatus.text() == "Validated"
        assert window.ui.label_validationStatus.property("tone") == "success"
        assert window.ui.label_cardCurrentTaskValue.text() == "Validated"
        assert window.ui.label_cardCurrentTaskValue.property("tone") == "success"
        window.task_builder_controller.refresh_task_preview()
        assert window.ui.label_validationStatus.text() == "Not validated"
        assert window.ui.label_cardCurrentTaskValue.text() == "Not validated"
        assert "Online EPICS" not in window.task_ui.label_builderSummary.text()
        assert "· BO ·" not in window.task_ui.label_builderSummary.text()
        window.task_ui.comboBox_mode.setCurrentText("Offline")
        window.task_ui.comboBox_algorithm.setCurrentText("BO")
        app.processEvents()
        assert window.label_workspace_mode.text() == "Offline"
        assert window.label_workspace_algorithm.text() == "BO"
        assert window.label_workspace_machine.text() == "Offline"
        assert window.ui.label_cardAlgorithmValue.text() == "Offline benchmark"
        assert window.ui.label_cardAlgorithmValue.property("tone") == "success"
        run_action_buttons = [
            window.ui.pushButton_validateTask,
            window.ui.pushButton_startRun,
            window.ui.pushButton_stopRun,
        ]
        assert len({button.height() for button in run_action_buttons}) == 1
        assert window.ui.pushButton_startRun.width() == window.ui.pushButton_stopRun.width()
        assert window.ui.pushButton_validateTask.width() > window.ui.pushButton_startRun.width()
        assert all(button.property("compact") is True for button in run_action_buttons)
        assert all(button.property("runControl") is True for button in run_action_buttons)
        assert all(
            button.fontMetrics().horizontalAdvance(button.text()) <= button.width() - 6
            for button in run_action_buttons
        )
        assert not hasattr(window.ui, "pushButton_pauseRun")
        assert not hasattr(window.ui, "actionPause")
        assert not hasattr(window.run_ui, "pushButton_pause")
        assert not hasattr(window.run_ui, "pushButton_resume")
        status_index = window.ui.gridLayout_runActions.indexOf(window.ui.label_validationStatus)
        assert window.ui.gridLayout_runActions.getItemPosition(status_index) == (0, 0, 1, 2)
        preview_index = window.ui.gridLayout_runActions.indexOf(window.ui.pushButton_preview)
        assert preview_index == -1
        validate_index = window.ui.gridLayout_runActions.indexOf(window.ui.pushButton_validateTask)
        assert window.ui.gridLayout_runActions.getItemPosition(validate_index) == (1, 0, 1, 2)
        stop_index = window.ui.gridLayout_runActions.indexOf(window.ui.pushButton_stopRun)
        assert window.ui.gridLayout_runActions.getItemPosition(stop_index) == (2, 1, 1, 1)
        assert window.machine_ui.groupBox_connection.isHidden()
        assert window.machine_ui.splitter_top.isHidden()
        assert window.machine_ui.label_machineTitle.parent() is window.machine_ui.frame_machineHero
        assert window.machine_ui.label_status.isHidden()
        assert window.machine_ui.label_statusValue.parent() is window.machine_ui.frame_machineHero
        assert window.machine_ui.label_statusValue.text() == "Not checked"
        assert window.machine_ui.pushButton_test.isHidden()
        assert window.machine_ui.horizontalLayout_machineHeader.indexOf(
            window.machine_ui.pushButton_test
        ) == -1
        assert not window.machine_ui.label_caAddress.isVisible()
        assert not window.machine_ui.lineEdit_caAddress.isVisible()
        assert not window.machine_ui.checkBox_autoConnect.isVisible()
        assert not window.machine_ui.pushButton_connect.isVisible()
        assert not window.machine_ui.pushButton_disconnect.isVisible()
        assert window.machine_ui.label_timeout.text() == "PV Read Timeout [s]"
        assert window.machine_ui.label_timeout.parent() is window.machine_ui.groupBox_guard
        assert window.machine_ui.doubleSpinBox_timeout.parent() is window.machine_ui.groupBox_guard
        assert not hasattr(window.machine_ui, "checkBox_confirm")
        assert "confirm_before_write" not in window._current_task()["machine"]
        assert "profile" not in window._current_task()["machine"]
        assert window.machine_ui.label_statusValue.property("role") == "statusPill"
        assert window.ui.label_readinessTestReadTitle.text() == "EPICS Preflight"
        assert window.machine_ui.frame_pvPresetLibrary.maximumHeight() == 40
        assert window.machine_ui.pushButton_selectPvs.text() == "Select PVs"
        assert window.machine_ui.pushButton_applySelectedPvLibrary.text() == "Sync To Task"
        assert window.machine_ui.pushButton_undoMappingSync.text() == "Undo Sync"
        assert not window.machine_ui.label_pvLibrarySummary.isHidden()
        assert not window.machine_ui.label_pvLibrarySummary.wordWrap()
        assert (
            window.machine_ui.horizontalLayout_pvLibraryControls.indexOf(
                window.machine_ui.label_pvLibrarySummary
            )
            >= 0
        )
        assert (
            window.machine_ui.verticalLayout_pvPresetLibrary.indexOf(
                window.machine_ui.label_pvLibrarySummary
            )
            == -1
        )
        assert window.machine_ui.pushButton_selectPvs.property("inlineAction") is True
        assert window.machine_ui.pushButton_applySelectedPvLibrary.property("inlineAction") is True
        assert (
            window.machine_ui.horizontalLayout_readbackCheck.itemAt(0).widget()
            is window.machine_ui.checkBox_readbackCheck
        )
        assert (
            window.machine_ui.horizontalLayout_readbackCheck.itemAt(2).widget()
            is window.machine_ui.label_readbackTol
        )
        assert not window.machine_ui.label_readbackTol.isEnabled()
        assert not window.machine_ui.doubleSpinBox_readbackTol.isEnabled()
        window.task_ui.comboBox_mode.setCurrentText("Online EPICS")
        window.machine_ui.checkBox_readbackCheck.setChecked(True)
        assert window.machine_ui.label_readbackTol.isEnabled()
        assert window.machine_ui.doubleSpinBox_readbackTol.isEnabled()
        assert window.machine_ui.tabWidget_machineAdvanced.documentMode()
        assert [
            window.machine_ui.tabWidget_machine.tabText(index)
            for index in range(window.machine_ui.tabWidget_machine.count())
        ] == ["PV Mapping", "Run Safeguards", "Policies"]
        assert [
            window.machine_ui.tabWidget_machineAdvanced.tabText(index)
            for index in range(window.machine_ui.tabWidget_machineAdvanced.count())
        ] == ["Write Policy", "Templates"]
        assert window.machine_ui.tableWidget_policyPresets.rowCount() == 3
        assert {
            window.machine_ui.tableWidget_policyPresets.item(row, 1).text()
            for row in range(window.machine_ui.tableWidget_policyPresets.rowCount())
        } == {"FEL Energy Guard", "Zero Objective Guard", "BPM Zero Guard"}
        assert window.machine_ui.splitter_pvMapping.count() == 2
        assert window.machine_ui.tableWidget_mapping.rowCount() == 0
        assert [
            window.machine_ui.tableWidget_mapping.isColumnHidden(column)
            for column in range(window.machine_ui.tableWidget_mapping.columnCount())
        ] == [False, False, False, True, True, True, False, True]
        assert window.machine_ui.policy_bindings == []
        assert window.machine_ui.label_mappingDetailTitle.text() == "Select a machine signal"
        assert not window.machine_ui.pushButton_manageMappingPolicies.isEnabled()
        assert "No Signals Selected" in window.machine_ui.label_pvLibrarySummary.text()
        assert not window.machine_ui.pushButton_applySelectedPvLibrary.isEnabled()
        window.task_builder_controller.fill_table_from_records(
            window.machine_ui.tableWidget_mapping,
            [
                {"Role": "knob", "Name": "x0", "Group": "main"},
                {"Role": "objective", "Name": "obj0", "Group": "metric"},
            ],
        )
        window._refresh_mapping_policy_widgets()
        assert window.machine_ui.tableWidget_mapping.item(1, 6).text() == "No policies"
        assert window.machine_ui.tableWidget_mapping.cellWidget(1, 7) is None
        window.machine_ui.tableWidget_mapping.setCurrentCell(1, 1)
        app.processEvents()
        assert window.machine_ui.label_mappingDetailTitle.text() == "Objective · obj0"
        assert window.machine_ui.label_mappingPolicySummary.text() == "No policies assigned."
        assert window.machine_ui.pushButton_manageMappingPolicies.text() == "Add Policy"
        assert not window.machine_ui.comboBox_mappingDetailRole.isEnabled()
        assert window.machine_ui.lineEdit_mappingDetailName.isReadOnly()
        assert window.machine_ui.lineEdit_mappingDetailPv.isReadOnly()
        assert window.machine_ui.lineEdit_mappingDetailReadback.isReadOnly()
        assert window.machine_ui.lineEdit_mappingDetailGroup.isReadOnly()
        assert window.machine_ui.lineEdit_mappingDetailNote.isReadOnly()
        advanced_editor_calls = []
        manager_calls = []
        def accept_first_policy_template(dialog):
            dialog.tableWidget_templates.setCurrentCell(0, 0)
            return QDialog.Accepted

        with monkeypatch.context() as policy_patch:
            policy_patch.setattr(
                main_window_module.PolicyTemplatePickerDialog,
                "exec_",
                accept_first_policy_template,
            )
            policy_patch.setattr(
                main_window_module.SampleGuardRuleEditorDialog,
                "exec_",
                lambda _dialog: advanced_editor_calls.append(True) or QDialog.Rejected,
            )
            policy_patch.setattr(
                main_window_module.MappingPolicyManagerDialog,
                "exec_",
                lambda _dialog: manager_calls.append(True) or QDialog.Rejected,
            )
            window._manage_mapping_policies(1)
        assert advanced_editor_calls == []
        assert manager_calls == []
        assert len(window.machine_ui.policy_bindings) == 1
        binding = window.machine_ui.policy_bindings[0]
        assert binding["kind"] == "objective"
        assert binding["target"] == "obj0"
        assert binding["preset"] == "fel_energy_guard"
        assert binding["policy"]["name"] == "sample_guard"
        stored_rule = binding["policy"]["kwargs"]
        assert stored_rule["target"] == "obj0"
        assert stored_rule["conditions"][0]["metric"] == "mean_abs"
        mapping_headers = [
            window.machine_ui.tableWidget_mapping.horizontalHeaderItem(column).text()
            for column in range(window.machine_ui.tableWidget_mapping.columnCount())
        ]
        assert mapping_headers[-2:] == ["Policies", "Policy Action"]
        assert window.machine_ui.tableWidget_mapping.item(1, 6).text().startswith("FEL Energy Guard")
        assert window.machine_ui.tableWidget_mapping.item(1, 6).text().endswith("· Ready")
        assert window.machine_ui.tableWidget_mapping.cellWidget(1, 7) is None
        assert "FEL Energy Guard · Ready" in window.machine_ui.label_mappingPolicySummary.text()
        assert window.machine_ui.pushButton_manageMappingPolicies.text() == "Manage 1 Policy"
        assert not window.machine_ui.comboBox_mappingDetailRole.isEnabled()
        assert not window.machine_ui.pushButton_reviewMappingIssues.isHidden()
        assert window.machine_ui.pushButton_reviewMappingIssues.text() == "Review 2 Issues"
        assert not hasattr(window.machine_ui, "pushButton_addObjectivePolicy")
        assert not hasattr(window.machine_ui, "tableWidget_constraintPolicies")
        serialized_mapping = window._current_task()["machine"]["mapping"]
        assert len(serialized_mapping) == 2
        assert all("Policies" not in row and "Policy Action" not in row for row in serialized_mapping)
        serialized_machine = window._current_task()["machine"]
        assert serialized_machine["policy_bindings"][0]["target"] == "obj0"
        assert "objective_policies" not in serialized_machine
        assert "constraint_policies" not in serialized_machine
        assert (
            window.machine_ui.tabWidget_machine.indexOf(window.machine_ui.tab_runSafeguards)
            == 1
        )
        assert window.machine_ui.groupBox_guard.title() == ""
        assert (
            window.machine_ui.groupBox_guard.sizePolicy().verticalPolicy()
            == QSizePolicy.Fixed
        )
        assert window.machine_ui.doubleSpinBox_readbackTol.maximumWidth() == 160
        assert window.machine_ui.doubleSpinBox_setInterval.maximumWidth() == 160
        assert window.machine_ui.doubleSpinBox_sampleInterval.maximumWidth() == 160
        assert not hasattr(window.machine_ui, "checkBox_restore")
        assert "last edited value" in window.machine_ui.doubleSpinBox_setInterval.toolTip()
        assert "last edited value" in window.machine_ui.doubleSpinBox_sampleInterval.toolTip()
        assert (
            window.machine_ui.horizontalLayout_readbackCheck.itemAt(1)
            .spacerItem()
            .sizePolicy()
            .horizontalPolicy()
            == QSizePolicy.Fixed
        )
        assert (
            window.machine_ui.checkBox_readbackCheck.sizePolicy().horizontalPolicy()
            == QSizePolicy.Maximum
        )
        assert (
            window.machine_ui.horizontalLayout_readbackCheck.itemAt(4)
            .spacerItem()
            .sizePolicy()
            .horizontalPolicy()
            == QSizePolicy.Expanding
        )
        assert (
            window.machine_ui.horizontalLayout_pvLibraryControls.itemAt(0).widget()
            is window.machine_ui.pushButton_selectPvs
        )
        assert (
            window.machine_ui.horizontalLayout_pvLibraryControls.itemAt(1).widget()
            is window.machine_ui.pushButton_applySelectedPvLibrary
        )
        assert (
            window.machine_ui.horizontalLayout_pvLibraryControls.itemAt(2).widget()
            is window.machine_ui.pushButton_undoMappingSync
        )
        assert not window.offline_ui.frame_offlineHero.isVisible()
        assert not window.offline_ui.frame_offlinePlaceholder.isVisible()
        assert window.offline_ui.groupBox_benchmark.title() == "Benchmark"
        assert window.run_ui.groupBox_runtime.title() == ""
        assert window.run_ui.groupBox_runtime.maximumHeight() == 56
        assert window.run_ui.gridLayout_runtime.contentsMargins().top() == 6
        assert window.run_ui.groupBox_actions.isHidden()
        assert window.run_ui.pushButton_stop.parent() is window.run_ui.groupBox_runtime
        assert window.run_ui.pushButton_abortRestore.parent() is window.run_ui.groupBox_runtime
        assert window.run_ui.pushButton_restoreInitial.parent() is window.frame_results_source
        assert window.run_ui.pushButton_setBest.parent() is window.frame_results_source
        assert window.run_ui.frame_eval.maximumWidth() == 118
        assert window.run_ui.frame_best.maximumWidth() == 176
        assert window.run_ui.frame_eval.objectName() == "statusItem"
        assert window.run_ui.label_evalTitle.property("role") == "title"
        assert window.run_ui.label_evalValue.property("role") == "value"
        assert window.run_ui.label_evalValue.text() == "0/100"
        window.state.latest_task_snapshot = {"max_evaluations": 50}
        window.state.run.eval_count = 12
        window.runtime_status_controller.update_evaluation_label()
        assert window.run_ui.label_evalValue.text() == "12/50"
        window.task_ui.spinBox_maxEval.setValue(75)
        assert window.run_ui.label_evalValue.text() == "12/50"
        window.state.latest_task_snapshot.clear()
        window.state.run.eval_count = 0
        window.runtime_status_controller.update_evaluation_label()
        assert window.run_ui.label_evalValue.text() == "0/75"
        window.task_ui.spinBox_maxEval.setValue(100)
        assert window.run_ui.label_evalValue.text() == "0/100"
        assert window.run_ui.frame_phase.isHidden()
        assert window.run_ui.splitter_main.orientation() == Qt.Vertical
        assert window.run_ui.splitter_runRight.orientation() == Qt.Horizontal
        assert not window.run_ui.splitter_main.childrenCollapsible()
        assert not window.run_ui.splitter_main.isCollapsible(0)
        assert window.run_ui.splitter_main.isCollapsible(1)
        window.run_ui.splitter_main.setSizes([1, 0])
        app.processEvents()
        assert window.run_ui.splitter_main.sizes()[1] == 0
        window.run_ui.splitter_main.setSizes([430, 240])
        assert not window.run_ui.splitter_runRight.childrenCollapsible()
        assert window.run_ui.tabWidget_plots.documentMode()
        run_mode_combo = window.run_ui.comboBox_variableTrajectoryMode
        results_mode_combo = window.ui.comboBox_variableTrajectoryMode
        for overlay_layout in (
            window.run_ui.verticalLayout_variables,
            window.ui.verticalLayout_variablePlot,
        ):
            assert isinstance(overlay_layout, QGridLayout)
            assert [
                overlay_layout.getItemPosition(index)
                for index in range(overlay_layout.count())
            ] == [(0, 0, 1, 1), (0, 0, 1, 1)]
        assert [run_mode_combo.itemData(i) for i in range(run_mode_combo.count())] == [
            "raw",
            "bounds",
            "delta_initial",
        ]
        assert run_mode_combo.currentData() == "raw"
        run_mode_combo.setCurrentIndex(run_mode_combo.findData("bounds"))
        assert window.results_controller.variable_trajectory_mode == "bounds"
        assert results_mode_combo.currentData() == "bounds"
        results_mode_combo.setCurrentIndex(results_mode_combo.findData("delta_initial"))
        assert window.results_controller.variable_trajectory_mode == "delta_initial"
        assert run_mode_combo.currentData() == "delta_initial"

        window.state.latest_task_snapshot = _offline_task(tmp_path)
        window.state.eval_x_history[:] = [[-1.0], [0.0], [1.5]]
        window.results_controller.set_variable_trajectory_mode("bounds")
        variable_axis = window.run_var_canvas.figure.axes[0]
        np.testing.assert_allclose(variable_axis.lines[0].get_ydata(), [0.0, 50.0, 125.0])
        assert variable_axis.get_ylabel() == "Position in bounds (%)"
        assert variable_axis.get_title() == "Variable Trajectories — Bounds %"
        window.state.eval_x_history.clear()
        window.state.latest_task_snapshot.clear()
        window.results_controller.set_variable_trajectory_mode("raw")
        assert window.run_ui.frame_obj.property("plotHost") is True
        assert window.run_ui.frame_obj.frameShape() == window.run_ui.frame_obj.NoFrame
        plot_canvases = (
            window.obj_canvas,
            window.pareto_canvas,
            window.run_var_canvas,
            window.run_constraints_canvas,
            window.results_conv_canvas,
            window.results_pareto_canvas,
            window.var_canvas,
        )
        for canvas in plot_canvases:
            toolbar = canvas.navigation_toolbar
            actions = [action for action in toolbar.actions() if not action.isSeparator()]
            assert toolbar.objectName() == "plotNavigationToolbar"
            assert toolbar.property("compact") is True
            assert toolbar.minimumHeight() == toolbar.maximumHeight() == 28
            assert toolbar.toolButtonStyle() == Qt.ToolButtonIconOnly
            assert [action.text() for action in actions] == ["Reset", "Pan", "Zoom"]
            assert all(not action.icon().isNull() for action in actions)
        toolbar = window.obj_canvas.navigation_toolbar
        initial_mode = toolbar.mode
        toolbar.actions()[1].trigger()
        assert toolbar.mode != initial_mode
        toolbar.actions()[1].trigger()
        assert toolbar.mode == initial_mode
        assert window.run_ui.verticalLayout_main.indexOf(window.run_ui.groupBox_runtime) == 1
        assert window.run_ui.verticalLayout_main.indexOf(window.run_ui.groupBox_actions) == -1
        assert not window.ui.splitter_resultsMain.childrenCollapsible()
        assert not window.ui.splitter_resultsRight.childrenCollapsible()
        assert window.ui.splitter_convergencePlots.orientation() == Qt.Horizontal
        assert window.ui.tabWidget_resultsViews.documentMode()
        assert window.ui.groupBox_runList.maximumWidth() == 300
        for button in (
            window.ui.pushButton_refreshRunArchives,
            window.ui.pushButton_loadArchivedRun,
            window.ui.pushButton_openRunArchive,
        ):
            assert button.property("compact") is True
            assert button.height() <= 30
        refresh_archives = window.ui.pushButton_refreshRunArchives
        assert refresh_archives.text() == ""
        assert not refresh_archives.icon().isNull()
        assert refresh_archives.property("iconOnly") is True
        assert refresh_archives.width() == 28
        assert refresh_archives.accessibleName() == "Refresh archived runs"
        assert "Refresh archived runs" in refresh_archives.toolTip()
        assert window.ui.groupBox_convergencePlot.title() == ""
        assert window.ui.groupBox_convergencePlot.property("plotPanel") is True
        assert window.ui.frame_plotConvergence.property("plotHost") is True
        assert window.run_ui.groupBox_table.title() == "Evaluation History · Optimizer Scores"
        assert window.run_ui.pushButton_setSelectedEvaluation.text() == "Set Selected to Machine"
        assert not window.run_ui.pushButton_setSelectedEvaluation.isEnabled()
        assert window.ui.widget_resultsTables.isHidden()
        window.results_controller.append_recent_eval(
            {
                "eval_id": 1,
                "timestamp": "12:00:00",
                "status": "ok",
                "x_values": {"x0": 0.5},
                "objective_value": 1.0,
                "constraint_summary": "--",
            }
        )
        assert window.run_ui.tableWidget_recent.rowCount() == 1
        assert window.ui.tableWidget_recentEvaluations.rowCount() == 0
        window.view_adapter.clear_recent_evaluations()
        assert window.run_ui.tableWidget_recent.rowCount() == 0
        assert window.ui.groupBox_evalHistory.isHidden()
        assert window.ui.pushButton_writeSelectedPareto.text() == "Write Selected to Machine"
        assert window.ui.pushButton_writeSelectedPareto.property("machineWrite") is True
        assert not window.ui.tableWidget_paretoSolutions.wordWrap()
        pareto_header = window.ui.tableWidget_paretoSolutions.horizontalHeader()
        assert all(
            pareto_header.sectionResizeMode(column) == QHeaderView.Interactive
            for column in range(window.ui.tableWidget_paretoSolutions.columnCount())
        )
        assert pareto_header.stretchLastSection()
        assert not hasattr(window.ui, "tableWidget_paretoSelectionDetail")
        assert window.ui.label_paretoSolutionsHint.isHidden()
        assert window.label_results_source_task.text() == "No run"
        assert window.label_results_source_outcome.text() == "--"
        result_status_items = window.frame_results_source.findChildren(
            QFrame, "statusItem"
        )
        assert len(result_status_items) == 3
        assert window.frame_results_source.findChildren(QFrame, "statusSeparator") == []
        assert window.ui.treeWidget_runList.topLevelItem(0).text(0) == "Archived Runs"
        window.state.latest_task_snapshot = {"task_name": "result_task"}
        window.state.latest_result_output_dir = "/tmp/gotacc/result_task"
        window.state.run.phase = "Finished"
        window.results_controller.refresh_result_source()
        assert window.label_results_source_task.text() == "result_task"
        assert window.label_results_source_outcome.text() == "Finished"
        assert window.label_results_source_output.text() == "result_task"
        window.state.reset_results_snapshot()
        window.state.run.phase = "Idle"
        window.state.eval_history = [({"x0": 0.25}, 1.5, {"c0": 0.0})]
        window.results_controller.on_history_row_clicked(0)
        assert window.ui.tableWidget_solutionInspector.rowCount() == 5
        assert window.ui.tableWidget_solutionInspector.item(3, 0).text() == "Constraints"
        window.state.eval_history.clear()
        window.results_controller.update_results_summary_table()

        multi_task = _offline_multi_task(tmp_path, "zdt1")
        window.state.latest_task_snapshot = multi_task
        window.state.objective_dim = 2
        window.state.pareto_points = [(-0.2, -1.4), (-0.5, -0.8)]
        window.state.hypervolume_history = [0.0, 0.0]
        window.runtime_status_controller.sync_run_workspace(multi_task)
        window.runtime_status_controller.update_runtime_labels()
        assert window.run_ui.label_bestTitle.text() == "Hypervolume"
        assert window.run_ui.label_bestValue.text() == "0"
        objective_tab = window.run_ui.tabWidget_plots.indexOf(window.run_ui.tab_obj)
        assert window.run_ui.tabWidget_plots.tabText(objective_tab) == "Hypervolume"
        window.results_controller.update_results_after_finish(
            {
                "state": "Finished",
                "pareto_x": [[0.2, 0.5, 0.5], [0.5, 0.2, 0.2]],
                "pareto_y": [[-0.2, -1.4], [-0.5, -0.8]],
                "pareto_feasible": [True, False],
                "pareto_constraints": [[], [0.1]],
                "hypervolume_history": [0.0, 0.0],
            }
        )
        assert window.ui.tabWidget_resultsViews.currentWidget() is window.ui.tab_pareto
        pareto_splitter = window.ui.splitter_paretoResults
        assert pareto_splitter.orientation() == Qt.Vertical
        assert not pareto_splitter.childrenCollapsible()
        assert not pareto_splitter.isCollapsible(0)
        assert pareto_splitter.isCollapsible(1)
        assert pareto_splitter.handleWidth() == 6
        pareto_splitter.setSizes([420, 0])
        app.processEvents()
        assert pareto_splitter.sizes()[1] == 0
        pareto_splitter.setSizes([420, 240])
        assert window.ui.tableWidget_paretoSolutions.rowCount() == 2
        assert (
            window.ui.tableWidget_paretoSolutions.item(0, 2).text()
            == "f1 ↑ · w=1 = -0.2, f2 ↑ · w=1 = -1.4"
        )
        window.ui.tableWidget_paretoSolutions.selectRow(1)
        assert window.ui.tableWidget_solutionInspector.item(0, 1).text() == "1"
        assert window.ui.tableWidget_solutionInspector.item(1, 1).text() == "no"
        assert (
            window.ui.tableWidget_solutionInspector.item(2, 1).text()
            == "f1 ↑ · w=1 = -0.5, f2 ↑ · w=1 = -0.8"
        )
        assert (
            window.ui.tableWidget_paretoSolutions.item(1, 2).toolTip()
            == "f1 ↑ · w=1 = -0.5, f2 ↑ · w=1 = -0.8"
        )

        archived_task = TaskService.create_run_archive(multi_task)
        archive_dir = Path(archived_task["run_archive_dir"])
        evaluation_records = [
            {
                "eval_id": 1,
                "timestamp": "2026-08-28 12:00:00",
                "status": "ok",
                "x_values": {"x0": 0.2, "x1": 0.5, "x2": 0.5},
                "objective_values": [-0.2, -1.4],
                "constraint_values": [],
                "feasible": True,
                "hypervolume_updates": [0.1],
            },
            {
                "eval_id": 2,
                "timestamp": "2026-08-28 12:00:01",
                "status": "ok",
                "x_values": {"x0": 0.5, "x1": 0.2, "x2": 0.2},
                "objective_values": [-0.5, -0.8],
                "constraint_values": [],
                "feasible": True,
                "hypervolume_updates": [0.25],
            },
        ]
        (archive_dir / "evaluations.jsonl").write_text(
            "".join(json.dumps(record) + "\n" for record in evaluation_records),
            encoding="utf-8",
        )
        (archive_dir / "run_summary.json").write_text(
            json.dumps(
                {
                    "format_version": 1,
                    "run_id": archived_task["run_id"],
                    "task": archived_task,
                    "run_state": "Finished",
                    "elapsed_seconds": 3,
                    "eval_count": 2,
                    "objective_dim": 2,
                    "best_value": None,
                    "best_x": {},
                    "initial_x": {"x0": 0.1, "x1": 0.2, "x2": 0.3},
                    "write_policy_state": {"policy": "delta_link", "version": 1},
                    "pareto_solutions": [
                        {
                            "index": 0,
                            "x": evaluation_records[0]["x_values"],
                            "x_values": [0.2, 0.5, 0.5],
                            "y": [-0.2, -1.4],
                            "constraints": [],
                            "feasible": True,
                        }
                    ],
                    "hypervolume_history": [0.1, 0.25],
                    "history_path": str(archive_dir / "history.dat"),
                    "plot_path": "",
                    "result_plot_paths": {},
                    "output_directory": str(archive_dir),
                }
            ),
            encoding="utf-8",
        )
        window.results_controller.load_run_archive(archive_dir)
        assert window.state.viewing_archived_run
        assert window.state.run.eval_count == 2
        assert window.state.hypervolume_history == [0.1, 0.25]
        assert window.state.latest_initial_x == {"x0": 0.1, "x1": 0.2, "x2": 0.3}
        assert window.state.latest_write_policy_state == {"policy": "delta_link", "version": 1}
        assert len(window.state.eval_history) == 2
        assert window.run_ui.tableWidget_recent.rowCount() == 2
        window.run_ui.tableWidget_recent.selectRow(1)
        selected = window.results_controller.selected_evaluation_record()
        assert selected["eval_id"] == 2
        assert selected["x_values"] == evaluation_records[1]["x_values"]
        assert "x2=0.2" in window.run_ui.plainTextEdit_selectedEvaluation.toPlainText()
        assert not window.run_ui.pushButton_setSelectedEvaluation.isEnabled()
        assert window.label_results_source_outcome.text() == "Archived · Finished"

        window.state.latest_task_snapshot = _offline_task(tmp_path)
        window.state.viewing_archived_run = False
        window.state.objective_dim = 1
        window.runtime_status_controller.sync_run_workspace(window.state.latest_task_snapshot)
        window.runtime_status_controller.update_runtime_labels()
        assert (
            window.run_ui.label_bestTitle.text()
            == "Best Feasible Optimizer Score · sphere ↑ · w=1"
        )
        tooltip = window.run_ui.label_bestTitle.toolTip()
        assert "sphere: maximize; configured weight=1" in tooltip
        assert "weight is not applied by the offline backend" in tooltip
        assert window.run_ui.tabWidget_plots.tabText(objective_tab) == "Optimizer Score"
        window.state.run.phase = "Running"
        window.runtime_status_controller.set_run_phase("Running")
        assert window.label_workspace_run.text() == "Running"
        assert window.label_workspace_run.property("tone") == "success"
        window.state.run.phase = "Abort Requested"
        window.runtime_status_controller.set_run_phase("Abort Requested")
        assert window.label_workspace_run.property("tone") == "danger"
        window.state.run.phase = "Idle"
        window.runtime_status_controller.set_run_phase("Idle")
        assert window.label_workspace_run.property("tone") == "subtle"
        window.state.run.phase = "Running"
        window.runtime_status_controller.sync_run_workspace()
        assert window.run_ui.pushButton_abortRestore.isHidden()
        assert window.run_ui.groupBox_actions.isHidden()
        online_visibility_task = dict(window.state.latest_task_snapshot, mode="Online EPICS")
        window.runtime_status_controller.sync_run_workspace(online_visibility_task)
        assert not window.run_ui.pushButton_abortRestore.isHidden()
        assert window.run_ui.groupBox_actions.isHidden()
        window.state.run.phase = "Idle"
        window.state.latest_initial_x = {"x0": 0.0}
        window.state.latest_best_x = {"x0": 0.5}
        window.runtime_status_controller.sync_run_workspace(online_visibility_task)
        assert not window.run_ui.pushButton_restoreInitial.isHidden()
        assert not window.run_ui.pushButton_setBest.isHidden()
        online_multi_task = dict(online_visibility_task, objective_type="Multi Objective")
        window.runtime_status_controller.sync_run_workspace(online_multi_task)
        assert not window.run_ui.pushButton_restoreInitial.isHidden()
        assert window.run_ui.pushButton_setBest.isHidden()
        window.state.viewing_archived_run = True
        window.runtime_status_controller.sync_run_workspace(online_visibility_task)
        assert window.run_ui.pushButton_restoreInitial.isHidden()
        assert window.run_ui.pushButton_setBest.isHidden()
        window.state.viewing_archived_run = False
        window.runtime_status_controller.sync_run_workspace()
        assert window.run_ui.groupBox_actions.isHidden()
        assert [action.text() for action in window.new_task_menu.actions()] == [
            "Offline Task",
            "Online EPICS Task",
        ]
        window.new_online_task_action.trigger()
        app.processEvents()
        assert window.task_ui.comboBox_mode.currentText() == "Online EPICS"
        assert window.task_ui.lineEdit_taskName.text() == "online_task"
        legacy_task = window._current_task()
        legacy_task["machine"]["confirm_before_write"] = False
        window._apply_task_payload(legacy_task, goto_builder=False)
        app.processEvents()
        assert "confirm_before_write" not in window._current_task()["machine"]
        window.new_offline_task_action.trigger()
        app.processEvents()
        assert window.task_ui.comboBox_mode.currentText() == "Offline"
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


def test_variable_trajectory_display_transforms():
    pytest.importorskip("PyQt5")
    from gotacc.gui.views.controllers.results_controller import ResultsController

    values = np.array([[2.0, 120.0, 1.0], [12.0, 150.0, 1.0]])
    metadata = [
        {"name": "small", "lower": 0.0, "upper": 10.0, "initial": 2.0},
        {"name": "large", "lower": 100.0, "upper": 200.0, "initial": None},
        {"name": "fixed", "lower": 1.0, "upper": 1.0, "initial": 1.0},
    ]

    raw, raw_indices, raw_info = ResultsController.transform_variable_trajectories(
        values, metadata, "raw"
    )
    np.testing.assert_allclose(raw, values)
    assert raw_indices == [0, 1, 2]
    assert raw_info["ylabel"] == "Value"

    bounded, bounded_indices, bounded_info = (
        ResultsController.transform_variable_trajectories(values, metadata, "bounds")
    )
    np.testing.assert_allclose(bounded, [[20.0, 20.0], [120.0, 50.0]])
    assert bounded_indices == [0, 1]
    assert bounded_info["skipped"] == ["fixed"]
    assert bounded_info["reference_lines"] == [0.0, 100.0]

    delta, delta_indices, delta_info = ResultsController.transform_variable_trajectories(
        values, metadata, "delta_initial"
    )
    np.testing.assert_allclose(delta, [[0.0, 0.0], [100.0, 30.0]])
    assert delta_indices == [0, 1]
    assert delta_info["fallback_initials"] == ["large"]
    assert delta_info["skipped"] == ["fixed"]


def test_variable_trajectory_metadata_prefers_frozen_task_snapshot():
    pytest.importorskip("PyQt5")
    from types import SimpleNamespace

    from gotacc.gui.views.controllers.results_controller import ResultsController

    frozen_task = {
        "variables": [
            {
                "Enable": "Yes",
                "Name": "frozen_x",
                "Lower": "-5",
                "Upper": "5",
                "Initial": "1",
            }
        ]
    }
    current_task = {
        "variables": [
            {
                "Enable": "Yes",
                "Name": "edited_x",
                "Lower": "0",
                "Upper": "100",
                "Initial": "50",
            }
        ]
    }
    window = SimpleNamespace(
        state=SimpleNamespace(latest_task_snapshot=frozen_task),
        view_adapter=SimpleNamespace(current_task=lambda: current_task),
        task_ui=SimpleNamespace(tableWidget_variables=None),
    )

    metadata = ResultsController(window, canvas_class=None)._variable_trajectory_metadata(1)

    assert metadata == [
        {"name": "frozen_x", "lower": -5.0, "upper": 5.0, "initial": 1.0}
    ]
