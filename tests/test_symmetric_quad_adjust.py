from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("epics")

from half_linac.src.apps.symmetric_quad_adjust.epics_client import K1Monitor, K1WriteWorker
from half_linac.src.apps.symmetric_quad_adjust.model import (
    CoalescingK1Queue,
    QuadPair,
    shifted_pair_targets,
    single_target,
)
from half_linac.src.apps.symmetric_quad_adjust.profile_runtime import (
    load_symmetric_quad_runtime,
)


def test_half_runtime_loads_mirror_pairs_and_k1_pvs() -> None:
    with patch.dict(
        os.environ,
        {
            "HALF_LINAC_MACHINE_ID": "half",
            "HALF_LINAC_CONTROL_BACKEND": "vm",
        },
    ):
        runtime = load_symmetric_quad_runtime()

    assert [pair.pair.elements for pair in runtime.pairs] == [
        ("QL01", "QL06"),
        ("QL02", "QL05"),
        ("QL03", "QL04"),
    ]
    assert runtime.logical_channel == "K1"
    assert runtime.default_step == 0.1
    assert runtime.custom_step_minimum == 0.000001
    assert runtime.custom_step_maximum == 1.0
    assert runtime.button_repeat_delay_ms == 300
    assert runtime.button_repeat_interval_ms == 150
    assert [target.pv_name for target in runtime.targets] == [
        "HALF:IN:AP:QUAD:QL01:K1:ao",
        "HALF:IN:AP:QUAD:QL06:K1:ao",
        "HALF:IN:AP:QUAD:QL02:K1:ao",
        "HALF:IN:AP:QUAD:QL05:K1:ao",
        "HALF:IN:AP:QUAD:QL03:K1:ao",
        "HALF:IN:AP:QUAD:QL04:K1:ao",
    ]
    assert [target.readback_pv for target in runtime.targets] == [
        target.pv_name for target in runtime.targets
    ]


def test_real_runtime_resolves_k1_not_current_channels() -> None:
    with patch.dict(
        os.environ,
        {
            "HALF_LINAC_MACHINE_ID": "half",
            "HALF_LINAC_CONTROL_BACKEND": "real",
        },
    ):
        runtime = load_symmetric_quad_runtime()

    assert runtime.pairs[0].left.pv_name == "IN:MG:L002:QUAD:QL01:K1"
    assert (
        runtime.pairs[0].left.readback_pv
        == "IN:MG:L002:QUAD:QL01:K1:TOTAL"
    )
    assert ":current:" not in runtime.pairs[0].left.pv_name


def test_pair_shift_uses_same_delta_and_single_set_is_independent() -> None:
    pair = QuadPair("QL01", "QL06")
    shifted = shifted_pair_targets(pair, {"QL01": 6.3, "QL06": 6.4}, 0.1)
    assert shifted == pytest.approx({"QL01": 6.4, "QL06": 6.5})
    assert single_target("QL01", -2.5) == {"QL01": -2.5}
    with pytest.raises(ValueError, match="QL06"):
        shifted_pair_targets(pair, {"QL01": 6.3}, 0.1)


def test_batch_worker_writes_pair_in_one_caput_many_call() -> None:
    worker = K1WriteWorker(
        {
            "QL01": ("PV:QL01:K1", 6.4),
            "QL06": ("PV:QL06:K1", 6.4),
        }
    )
    emissions = []
    worker.completed.connect(lambda *args: emissions.append(args))
    with patch(
        "half_linac.src.apps.symmetric_quad_adjust.epics_client.epics.caput_many",
        return_value=[1, 1],
    ) as caput_many:
        worker.run()

    caput_many.assert_called_once_with(
        ["PV:QL01:K1", "PV:QL06:K1"],
        [6.4, 6.4],
        wait="all",
        connection_timeout=2.0,
        put_timeout=5.0,
    )
    assert emissions[0][1] == {"QL01": True, "QL06": True}
    assert emissions[0][2] == ""


def test_monitor_binds_k1_setpoint_and_k1_total_readback() -> None:
    with patch.dict(
        os.environ,
        {
            "HALF_LINAC_MACHINE_ID": "half",
            "HALF_LINAC_CONTROL_BACKEND": "real",
        },
    ):
        target = load_symmetric_quad_runtime().pairs[0].left
    monitor = K1Monitor()
    with patch(
        "half_linac.src.apps.symmetric_quad_adjust.epics_client.epics.PV"
    ) as pv:
        monitor.bind((target,))

    assert [call.args[0] for call in pv.call_args_list] == [
        "IN:MG:L002:QUAD:QL01:K1",
        "IN:MG:L002:QUAD:QL01:K1:TOTAL",
    ]


def test_write_queue_accumulates_rapid_pair_steps_from_latest_target() -> None:
    queue = CoalescingK1Queue()
    observed = {"QL01": 6.3, "QL06": 6.3}
    first = shifted_pair_targets(
        QuadPair("QL01", "QL06"), queue.desired_values(observed), 0.1
    )
    queue.enqueue(first)
    assert queue.begin_next() == pytest.approx({"QL01": 6.4, "QL06": 6.4})

    second = shifted_pair_targets(
        QuadPair("QL01", "QL06"), queue.desired_values(observed), 0.1
    )
    queue.enqueue(second)
    third = shifted_pair_targets(
        QuadPair("QL01", "QL06"), queue.desired_values(observed), 0.1
    )
    queue.enqueue(third)

    assert queue.finish() == pytest.approx({"QL01": 6.4, "QL06": 6.4})
    assert queue.begin_next() == pytest.approx({"QL01": 6.6, "QL06": 6.6})


def test_launcher_contains_symmetric_quad_entry() -> None:
    source = (Path(__file__).parents[1] / "src/apps/launcher/main.py").read_text(
        encoding="utf-8"
    )
    assert '"symmetric_quad_adjust"' in source
    assert 'ROOT / "src/apps/symmetric_quad_adjust"' in source
