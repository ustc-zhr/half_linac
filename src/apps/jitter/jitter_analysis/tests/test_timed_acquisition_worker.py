import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jitter_analysis.acquisition import workers


pytestmark = pytest.mark.skipif(
    workers.QtCore is None,
    reason="PyQt5 is required for acquisition worker tests",
)


class _Sampler:
    def __init__(self):
        self.worker = None
        self.calls = 0

    def sample_batch(self, objects, batch_index):
        self.calls += 1
        if self.worker is not None and self.worker.stop_mode == "continuous" and self.calls >= 3:
            self.worker.stop()
        return []

    class _Client:
        def snapshot_connections(self, pv_names):
            return [True for _ in pv_names]

    client = _Client()


def _run_worker(*, stop_mode, sample_count=0, duration_sec=0.0):
    sampler = _Sampler()
    worker = workers.TimedAcquisitionWorker(
        sampler,
        [],
        0.002,
        sample_count,
        stop_mode,
        duration_sec,
    )
    sampler.worker = worker
    finished = []
    progress = []
    time_progress = []
    worker.signals.finished.connect(finished.append)
    worker.signals.progress.connect(lambda completed, total: progress.append((completed, total)))
    worker.signals.time_progress.connect(lambda elapsed, total: time_progress.append((elapsed, total)))
    worker.run()
    return sampler, finished, progress, time_progress


def test_timed_worker_stops_after_total_samples():
    sampler, finished, progress, time_progress = _run_worker(stop_mode="samples", sample_count=3)

    assert sampler.calls == 3
    assert finished == ["completed"]
    assert progress[-1] == (3, 3)
    assert time_progress == []


def test_timed_worker_stops_after_total_duration():
    sampler, finished, progress, time_progress = _run_worker(
        stop_mode="duration", duration_sec=0.008
    )

    assert sampler.calls >= 1
    assert finished == ["completed"]
    assert progress[-1][1] == 0
    assert time_progress[-1] == pytest.approx((0.008, 0.008), abs=1e-9)


def test_timed_worker_continuous_mode_stops_only_when_requested():
    sampler, finished, progress, time_progress = _run_worker(stop_mode="continuous")

    assert sampler.calls == 3
    assert finished == ["stopped"]
    assert progress[-1] == (3, 0)
    assert time_progress[-1][1] == 0.0
