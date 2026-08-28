import pytest
import numpy as np

pytest.importorskip("PyQt5")

from gotacc.gui.workers.engine_worker import EngineWorker


def _offline_task(tmp_path):
    return {
        "task_name": "failing_worker_task",
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


class _FailingOptimizer:
    def optimize(self):
        raise RuntimeError("optimizer failed")


class _EvaluatingOptimizer:
    def __init__(self, objective_callable):
        self.objective_callable = objective_callable

    def optimize(self):
        return self.objective_callable(np.asarray([0.0]))


class _FakeBackend:
    def __init__(self, *, fail_restore=False):
        self.fail_restore = fail_restore
        self.restore_called = False
        self.close_called = False

    def init_knob_value(self):
        return [0.0]

    def evaluate(self, x):
        return 0.0

    def restore_initial(self):
        self.restore_called = True
        if self.fail_restore:
            raise RuntimeError("restore failed")

    def close(self):
        self.close_called = True


def test_engine_worker_routes_policy_events_to_run_log_signal(tmp_path):
    class EventPolicy:
        def set_event_sink(self, sink):
            self.sink = sink

    policy = EventPolicy()
    backend = type(
        "Backend",
        (),
        {"objective_policy": policy, "constraint_policy": None},
    )()
    worker = EngineWorker(_offline_task(tmp_path))
    messages = []
    worker.sig_log.connect(messages.append)

    worker._attach_policy_event_sink(backend)
    policy.sink("Policy triggered for beam_current [replace]: 5 → 0")

    assert messages == ["Policy triggered for beam_current [replace]: 5 → 0"]


def test_engine_worker_preserves_numeric_constraints_for_live_plots(tmp_path):
    worker = EngineWorker(_offline_task(tmp_path))

    normalized = worker._normalize_output(([1.25], [-0.2, 0.4]))

    np.testing.assert_allclose(normalized["constraint_values"], [-0.2, 0.4])
    assert normalized["constraint_summary"] == "c0=-0.200000, c1=0.400000"


def test_population_live_hypervolume_emits_only_new_generations(tmp_path):
    worker = EngineWorker(_offline_task(tmp_path))
    optimizer = type("PopulationOptimizer", (), {"hypervolume_history": [0.1]})()
    worker._single_objective = False
    worker._population_multi_objective = True
    worker._optimizer = optimizer

    assert worker._live_hypervolume_updates() == [0.1]
    assert worker._live_hypervolume_updates() == []

    optimizer.hypervolume_history.append(0.25)
    assert worker._live_hypervolume_updates() == [0.25]
    assert worker._live_hypervolume_updates() == []


@pytest.fixture
def worker_patches(monkeypatch):
    created_backends = []

    def patch_backend(*, fail_restore=False):
        def fake_build_backend(_task_cfg):
            backend = _FakeBackend(fail_restore=fail_restore)
            created_backends.append(backend)
            return backend

        monkeypatch.setattr("gotacc.interfaces.factory.build_backend", fake_build_backend)

    monkeypatch.setattr(
        "gotacc.runners.task_runner.build_optimizer",
        lambda **_kwargs: _FailingOptimizer(),
    )
    return patch_backend, created_backends


def _run_worker_and_capture(worker):
    errors = []
    warnings = []
    worker.sig_error.connect(errors.append)
    worker.sig_warning.connect(warnings.append)

    worker.run()

    return errors, warnings


def test_engine_worker_restores_initial_after_unhandled_run_error(tmp_path, worker_patches):
    patch_backend, created_backends = worker_patches
    patch_backend(fail_restore=False)
    worker = EngineWorker(_offline_task(tmp_path))

    errors, warnings = _run_worker_and_capture(worker)

    assert errors == ["optimizer failed"]
    assert warnings == ["Run failed; initial machine state was restored."]
    assert created_backends[0].restore_called
    assert created_backends[0].close_called


def test_engine_worker_reports_restore_failure_after_run_error(tmp_path, worker_patches):
    patch_backend, created_backends = worker_patches
    patch_backend(fail_restore=True)
    worker = EngineWorker(_offline_task(tmp_path))

    errors, warnings = _run_worker_and_capture(worker)

    assert "optimizer failed" in errors[0]
    assert "Restore initial failed after run error: restore failed" in errors[0]
    assert warnings == ["Restore initial failed after run error: restore failed"]
    assert created_backends[0].restore_called
    assert created_backends[0].close_called


def test_engine_worker_stop_does_not_restore(tmp_path, worker_patches, monkeypatch):
    patch_backend, created_backends = worker_patches
    patch_backend(fail_restore=False)
    monkeypatch.setattr(
        "gotacc.runners.task_runner.build_optimizer",
        lambda **kwargs: _EvaluatingOptimizer(kwargs["objective_callable"]),
    )
    worker = EngineWorker(_offline_task(tmp_path))
    finished = []
    worker.sig_finished.connect(finished.append)
    worker.request_stop()

    worker.run()

    assert finished[0]["state"] == "Stopped"
    assert finished[0]["restore_state"] == "not_requested"
    assert not created_backends[0].restore_called


@pytest.mark.parametrize(
    ("fail_restore", "expected_state", "expected_restore_state"),
    [
        (False, "Aborted", "restored"),
        (True, "Restore Failed", "failed"),
    ],
)
def test_engine_worker_reports_abort_restore_outcome(
    tmp_path,
    worker_patches,
    monkeypatch,
    fail_restore,
    expected_state,
    expected_restore_state,
):
    patch_backend, created_backends = worker_patches
    patch_backend(fail_restore=fail_restore)
    monkeypatch.setattr(
        "gotacc.runners.task_runner.build_optimizer",
        lambda **kwargs: _EvaluatingOptimizer(kwargs["objective_callable"]),
    )
    worker = EngineWorker(_offline_task(tmp_path))
    statuses = []
    finished = []
    warnings = []
    worker.sig_status.connect(statuses.append)
    worker.sig_finished.connect(finished.append)
    worker.sig_warning.connect(warnings.append)
    worker.request_abort_restore()

    worker.run()

    assert finished[0]["state"] == expected_state
    assert finished[0]["restore_state"] == expected_restore_state
    assert created_backends[0].restore_called
    assert created_backends[0].close_called
    assert any(payload.get("state") == "Restoring" for payload in statuses)
    if fail_restore:
        assert warnings == ["Restore initial failed after abort: restore failed"]
