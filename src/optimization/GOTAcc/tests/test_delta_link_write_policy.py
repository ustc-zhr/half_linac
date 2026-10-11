from __future__ import annotations

import numpy as np
import pytest

from gotacc.interfaces import epics
from gotacc.interfaces.epics import DeltaLinkWritePolicy, EpicsObjective


def _backend(monkeypatch, policy, *, readback_check=False):
    reads = {
        "K0:RB": 10.0,
        "K1:RB": 20.0,
        "T0:RB": 100.0,
        "T1:SET": -5.0,
    }
    writes = []

    def caget(name, timeout=None):
        return reads.get(name)

    def caget_many(names):
        return [reads.get(name) for name in names]

    def caput(name, value):
        writes.append(([name], [value]))

    def caput_many(names, values):
        writes.append((list(names), list(values)))

    monkeypatch.setattr(epics, "_load_epics", lambda: (caget, caput, caput_many, caget_many))
    backend = EpicsObjective(
        knobs_pvnames=["K0:SET", "K1:SET"],
        knob_readback_pvnames=["K0:RB", "K1:RB"],
        obj_pvnames=["OBJ"],
        obj_weights=[1.0],
        obj_samples=1,
        obj_math=["mean"],
        readback_check=readback_check,
        readback_tol=[0.1, 0.2],
        write_policy=policy,
    )
    return backend, reads, writes


def _policy():
    return DeltaLinkWritePolicy(
        [
            {
                "source_index": 0,
                "target_pv": "T0:SET",
                "target_readback_pv": "T0:RB",
                "scale": 1.0,
                "readback_tol": 0.05,
            },
            {
                "source_index": 1,
                "target_pv": "T1:SET",
                "scale": -2.0,
            },
        ]
    )


def test_delta_link_uses_fixed_baseline_and_restores(monkeypatch):
    backend, _reads, writes = _backend(monkeypatch, _policy())
    np.testing.assert_allclose(backend.init_knob_value(), [10.0, 20.0])

    backend._apply_setpoints(np.asarray([13.0, 18.0]))
    backend._apply_setpoints(np.asarray([13.0, 18.0]))
    backend.restore_initial()

    expected_pvs = ["K0:SET", "K1:SET", "T0:SET", "T1:SET"]
    assert writes[0][0] == expected_pvs
    np.testing.assert_allclose(writes[0][1], [13.0, 18.0, 103.0, -1.0])
    np.testing.assert_allclose(writes[1][1], writes[0][1])
    np.testing.assert_allclose(writes[2][1], [10.0, 20.0, 100.0, -5.0])


def test_delta_link_state_round_trip_and_rule_fingerprint(monkeypatch):
    backend, _reads, _writes = _backend(monkeypatch, _policy())
    backend.init_knob_value()
    state = backend.export_write_policy_state()
    assert state and state["version"] == 1

    restored, _reads2, writes = _backend(monkeypatch, _policy())
    restored.load_write_policy_state(state)
    preview = restored.preview_linked_setpoints([12.0, 19.0])
    assert [item["value"] for item in preview] == [102.0, -3.0]
    restored._apply_setpoints(np.asarray([12.0, 19.0]))
    np.testing.assert_allclose(writes[-1][1], [12.0, 19.0, 102.0, -3.0])

    mismatch = DeltaLinkWritePolicy(
        [{"source_index": 0, "target_pv": "OTHER", "scale": 1.0}]
    )
    with pytest.raises(ValueError, match="does not match"):
        mismatch.load_state(state)


def test_delta_link_requires_baseline_before_any_write(monkeypatch):
    backend, _reads, writes = _backend(monkeypatch, _policy())
    with pytest.raises(RuntimeError, match="baseline is unavailable"):
        backend._apply_setpoints(np.asarray([11.0, 21.0]))
    assert writes == []


def test_delta_link_rejects_conflicts_and_invalid_rules_before_write(monkeypatch):
    conflict = DeltaLinkWritePolicy(
        [{"source_index": 0, "target_pv": "K1:SET"}]
    )
    backend, _reads, writes = _backend(monkeypatch, conflict)
    with pytest.raises(ValueError, match="also a knob"):
        backend.init_knob_value()
    assert writes == []

    with pytest.raises(ValueError, match="unique"):
        DeltaLinkWritePolicy(
            [
                {"source_index": 0, "target_pv": "T"},
                {"source_index": 1, "target_pv": "T"},
            ]
        )
    with pytest.raises(ValueError, match="finite"):
        DeltaLinkWritePolicy(
            [{"source_index": 0, "target_pv": "T", "scale": float("nan")}]
        )


def test_delta_link_readback_checks_linked_targets(monkeypatch):
    backend, reads, _writes = _backend(monkeypatch, _policy(), readback_check=True)
    backend.init_knob_value()
    reads.update({"K0:RB": 12.0, "K1:RB": 19.0, "T0:RB": 102.04, "T1:SET": -2.85})
    backend._check_readback(np.asarray([12.0, 19.0]))

    reads["T0:RB"] = 102.2
    with pytest.raises(RuntimeError, match="Linked PV readback"):
        backend._check_readback(np.asarray([12.0, 19.0]))
