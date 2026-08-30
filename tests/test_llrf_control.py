from __future__ import annotations

import os
from unittest.mock import patch

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("epics")

from half_linac.src.apps.llrf_control.epics_client import WriteWorker
from half_linac.src.apps.llrf_control.main import LlrfControlWindow
from half_linac.src.apps.llrf_control.model import CoalescingWriteQueue
from half_linac.src.apps.llrf_control.profile_runtime import load_llrf_runtime


def test_half_runtime_discovers_all_llrfs_with_limits_and_steps() -> None:
    with patch.dict(
        os.environ,
        {
            "HALF_LINAC_MACHINE_ID": "half",
            "HALF_LINAC_CONTROL_BACKEND": "real",
        },
    ):
        runtime = load_llrf_runtime()

    assert [group.element_id for group in runtime.groups] == [
        "LLRFPB",
        *(f"LLRF{index:02d}" for index in range(1, 21)),
    ]
    assert runtime.default_element == "LLRF01"
    assert runtime.groups[0].display_name == "Prebuncher LLRF"
    assert runtime.groups[1].display_name == "LLRF01"
    for group in runtime.groups:
        phase = group.quantities["phase"]
        amplitude = group.quantities["amplitude"]
        assert (phase.low, phase.high, phase.unit, phase.default_step) == (-180, 180, "deg", 5)
        assert (amplitude.low, amplitude.high, amplitude.unit, amplitude.default_step) == (0, 100, "%", 2)
        assert (phase.readback_tolerance, phase.settle_s) == (0.1, 2)
        assert (amplitude.readback_tolerance, amplitude.settle_s) == (0.1, 2)
        assert phase.setpoint_pv == f"IN:MW:{group.element_id}:SET_PHASE"
        assert phase.readback_pv == f"IN:MW:{group.element_id}:GET_PHASE"
        assert amplitude.setpoint_pv == f"IN:MW:{group.element_id}:SET_AMP"
        assert amplitude.readback_pv == f"IN:MW:{group.element_id}:GET_AMP"


def test_phase_wrap_preserves_range_endpoints_and_wraps_overflow() -> None:
    assert LlrfControlWindow._wrap_phase(180, -180, 180) == 180
    assert LlrfControlWindow._wrap_phase(185, -180, 180) == -175
    assert LlrfControlWindow._wrap_phase(-185, -180, 180) == 175


def test_write_worker_reports_caput_result() -> None:
    worker = WriteWorker("phase", "TEST:SET_PHASE", 12.5)
    emissions = []
    worker.completed.connect(lambda *args: emissions.append(args))

    with patch("half_linac.src.apps.llrf_control.epics_client.epics.caput", return_value=1) as caput:
        worker.run()

    caput.assert_called_once_with("TEST:SET_PHASE", 12.5, wait=True, timeout=5)
    assert emissions == [("phase", 12.5, True, "")]


def test_write_queue_coalesces_repeated_requests_to_latest_value() -> None:
    queue = CoalescingWriteQueue()
    queue.enqueue("phase", 5)
    assert queue.begin_next() == ("phase", 5)

    queue.enqueue("phase", 10)
    queue.enqueue("phase", 15)
    assert queue.requested["phase"] == 15
    assert queue.finish() == ("phase", 5)
    assert queue.begin_next() == ("phase", 15)
    queue.finish()
    queue.acknowledge("phase", 15)

    assert not queue.busy
    assert "phase" not in queue.requested


def test_write_queue_keeps_other_quantity_and_can_cancel_external_conflict() -> None:
    queue = CoalescingWriteQueue()
    queue.enqueue("phase", 5)
    queue.enqueue("amplitude", 80)
    assert queue.begin_next() == ("phase", 5)

    queue.cancel_pending("amplitude")
    assert "amplitude" not in queue.pending
    assert "amplitude" not in queue.requested
    assert queue.expected_values("phase") == (5,)
