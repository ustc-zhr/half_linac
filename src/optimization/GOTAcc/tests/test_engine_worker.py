import pytest

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
