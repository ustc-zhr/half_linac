from __future__ import annotations

import os
from pathlib import Path
import time

import joblib
import pandas as pd
import pytest
from sklearn.dummy import DummyRegressor


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from jitter_analysis.gui import training_page as training_page_module
from jitter_analysis.gui.training_page import TrainingPage
from jitter_analysis.training import (
    RunDescriptor,
    RunRole,
    TrainingCancelled,
    TrainingExperimentResult,
)


pytestmark = pytest.mark.skipif(
    training_page_module.QtWidgets is None,
    reason="PyQt5 is required for Training page tests",
)


def _descriptor(path: Path, run_id: str, created_at: str, *, compatible: bool = True):
    return RunDescriptor(
        path=str(path.resolve()),
        run_id=run_id,
        created_at=created_at,
        mode="timed_acquisition",
        status="completed",
        machine="IRFEL",
        notes="",
        sample_interval_seconds=0.2,
        logical_sample_count=1000,
        object_ids=("bpm02_x", "bpm03_x", "bpm04_x", "bpm04_s"),
        units={"bpm04_x": "mm"},
        compatible=compatible,
        issues=() if compatible else ("raw.h5 is missing",),
    )


class FakeService:
    def __init__(self, descriptors):
        self.descriptors = descriptors
        self.last_spec = None

    def discover_runs(self, _root, _task_id):
        return list(self.descriptors)

    def validate_spec(self, spec):
        self.last_spec = spec
        if not spec.train_paths:
            raise ValueError("Select at least one Train run")
        if not spec.test_paths:
            raise ValueError("Select at least one Test run")
        return [
            descriptor
            for descriptor in self.descriptors
            if descriptor.path in spec.train_paths + spec.test_paths
        ]


def _wait_until(app, predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    app.processEvents()
    assert predicate()


def _result_files(root: Path) -> TrainingExperimentResult:
    experiment = root / "experiment"
    candidates = experiment / "candidates"
    candidates.mkdir(parents=True)
    metrics = pd.DataFrame(
        [
            {
                "scope": "pooled",
                "run_id": "ALL",
                "candidate_id": "target_history__ridge",
                "feature_group": "target_history",
                "model": "ridge",
                "exportable": True,
                "samples": 4,
                "rmse_mm": 0.09,
                "mae_mm": 0.07,
                "persistence_rmse_mm": 0.10,
                "skill_vs_persistence": 0.19,
                "delta_correlation": 0.4,
                "direction_accuracy_large_half": 0.75,
            },
            {
                "scope": "run",
                "run_id": "newest",
                "candidate_id": "target_history__ridge",
                "feature_group": "target_history",
                "model": "ridge",
                "exportable": True,
                "samples": 4,
                "rmse_mm": 0.09,
                "mae_mm": 0.07,
                "persistence_rmse_mm": 0.10,
                "skill_vs_persistence": 0.19,
                "delta_correlation": 0.4,
                "direction_accuracy_large_half": 0.75,
            },
            {
                "scope": "pooled",
                "run_id": "ALL",
                "candidate_id": "rf_hv__mlp",
                "feature_group": "rf_hv",
                "model": "mlp",
                "exportable": True,
                "samples": 4,
                "rmse_mm": 0.12,
                "mae_mm": 0.10,
                "persistence_rmse_mm": 0.10,
                "skill_vs_persistence": -0.44,
                "delta_correlation": -0.1,
                "direction_accuracy_large_half": 0.25,
            },
        ]
    )
    predictions = []
    for candidate, scale in (("target_history__ridge", 0.8), ("rf_hv__mlp", -0.2)):
        for index, actual in enumerate((0.1, -0.2, 0.05, 0.12), start=30):
            predicted = actual * scale
            predictions.append(
                {
                    "run_id": "newest",
                    "candidate_id": candidate,
                    "feature_group": candidate.split("__", 1)[0],
                    "model": candidate.split("__", 1)[1],
                    "window_index": index,
                    "current_q_mm": 1.0,
                    "actual_delta_mm": actual,
                    "predicted_delta_mm": predicted,
                    "actual_next_q_mm": 1.0 + actual,
                    "predicted_next_q_mm": 1.0 + predicted,
                }
            )
    metrics_path = experiment / "metrics.csv"
    predictions_path = experiment / "predictions.csv"
    metrics.to_csv(metrics_path, index=False)
    pd.DataFrame(predictions).to_csv(predictions_path, index=False)
    model_path = candidates / "target_history__ridge.joblib"
    joblib.dump(DummyRegressor(), model_path)
    return TrainingExperimentResult(
        experiment_id="experiment",
        experiment_dir=str(experiment),
        task_id="fir_bpm04_forecast",
        recommended_candidate_id="target_history__ridge",
        plot_candidate_id="target_history__ridge",
        candidate_model_paths={"target_history__ridge": str(model_path)},
        metrics_path=str(metrics_path),
        predictions_path=str(predictions_path),
    )


def test_run_roles_parameters_and_acquisition_interlock(session_qt_application, tmp_path):
    descriptors = [
        _descriptor(tmp_path / "newest", "newest", "2026-11-01T10:00:00"),
        _descriptor(tmp_path / "older", "older", "2026-10-24T10:00:00"),
        _descriptor(
            tmp_path / "incompatible",
            "incompatible",
            "2026-10-23T10:00:00",
            compatible=False,
        ),
    ]
    service = FakeService(descriptors)
    page = TrainingPage(default_run_root=str(tmp_path), service=service)
    page.refresh_runs()

    assert page.run_table.rowCount() == 3
    assert page.run_table.cellWidget(0, 0).currentData() == RunRole.TEST.value
    assert page.run_table.cellWidget(1, 0).currentData() == RunRole.TRAIN.value
    assert page.run_table.cellWidget(2, 0).currentData() == RunRole.IGNORE.value
    assert not page.run_table.cellWidget(2, 0).isEnabled()
    assert page.start_button.isEnabled()

    page.run_table.cellWidget(0, 0).setCurrentIndex(
        page.run_table.cellWidget(0, 0).findData(RunRole.TRAIN.value)
    )
    page.run_table.cellWidget(1, 0).setCurrentIndex(
        page.run_table.cellWidget(1, 0).findData(RunRole.TEST.value)
    )
    assert page.validate_selection()
    assert service.last_spec.train_paths == [descriptors[0].path]
    assert service.last_spec.test_paths == [descriptors[1].path]

    page.subtract_orbit_check.setChecked(True)
    assert "a = b = 0" in page.target_note_label.text()
    assert "not an energy residual" in page.target_note_label.text()
    page.coefficient_a_spin.setValue(1.25)
    page.coefficient_b_spin.setValue(-0.4)
    assert "q = BPM04.x" in page.target_note_label.text()
    assert page._build_spec().coefficient_a == pytest.approx(1.25)

    page.set_acquisition_active(True)
    assert not page.start_button.isEnabled()
    assert "Stop acquisition" in page.start_button.toolTip()
    page.set_acquisition_active(False)
    assert page.start_button.isEnabled()
    page.close()


class SuccessfulService(FakeService):
    def __init__(self, descriptors, result):
        super().__init__(descriptors)
        self.result = result

    def run(self, _spec, *, progress=None, cancelled=None):
        progress(20, "Loading runs")
        assert not cancelled()
        progress(75, "Training candidate")
        return self.result


def test_threaded_training_progress_results_and_warned_export(
    session_qt_application, tmp_path, monkeypatch
):
    app = session_qt_application
    descriptors = [
        _descriptor(tmp_path / "newest", "newest", "2026-11-01T10:00:00"),
        _descriptor(tmp_path / "older", "older", "2026-10-24T10:00:00"),
    ]
    result = _result_files(tmp_path)
    page = TrainingPage(
        default_run_root=str(tmp_path),
        service=SuccessfulService(descriptors, result),
    )
    page.refresh_runs()
    states = []
    page.trainingStateChanged.connect(states.append)

    page.start_training()
    _wait_until(app, lambda: not page.training_active and page._result is not None)

    assert states[0] is True
    assert states[-1] is False
    assert page.progress_bar.value() == 100
    assert "Recommended: target_history__ridge" == page.recommendation_label.text()
    assert page.export_button.isEnabled()
    assert (Path(result.experiment_dir) / "plots" / "prediction.png").exists()
    assert (Path(result.experiment_dir) / "plots" / "delta_residual.png").exists()

    for row in range(page.metrics_table.rowCount()):
        if page.metrics_table.item(row, 3).text() == "rf_hv__mlp":
            page.metrics_table.selectRow(row)
            break
    warnings = []
    exports = []
    monkeypatch.setattr(
        training_page_module.QtWidgets.QMessageBox,
        "warning",
        lambda *args: warnings.append(args),
    )
    destination = tmp_path / "manual_export.zip"
    monkeypatch.setattr(
        training_page_module.QtWidgets.QFileDialog,
        "getSaveFileName",
        lambda *args, **kwargs: (str(destination), "ZIP Packages (*.zip)"),
    )

    def fake_export(experiment_dir, candidate_id, path):
        exports.append((experiment_dir, candidate_id, path))
        Path(path).write_bytes(b"zip")
        return Path(path)

    monkeypatch.setattr(training_page_module, "export_model_package", fake_export)
    page.export_selected_model()

    assert warnings
    assert exports[0][1] == "rf_hv__mlp"
    assert destination.exists()
    page.close()


class CancellingService(FakeService):
    def run(self, _spec, *, progress=None, cancelled=None):
        progress(30, "Fitting current candidate")
        while not cancelled():
            time.sleep(0.005)
        raise TrainingCancelled("cancelled")


def test_threaded_training_can_cancel_between_candidates(
    session_qt_application, tmp_path
):
    app = session_qt_application
    descriptors = [
        _descriptor(tmp_path / "newest", "newest", "2026-11-01T10:00:00"),
        _descriptor(tmp_path / "older", "older", "2026-10-24T10:00:00"),
    ]
    page = TrainingPage(
        default_run_root=str(tmp_path),
        service=CancellingService(descriptors),
    )
    page.refresh_runs()

    page.start_training()
    _wait_until(app, lambda: page.training_active and page.progress_bar.value() == 30)
    assert page.cancel_button.isEnabled()
    page.cancel_training()
    _wait_until(app, lambda: not page.training_active)

    assert "cancelled" in page.progress_label.text().lower()
    assert page.start_button.isEnabled()
    page.close()
