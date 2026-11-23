from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import json
import os
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
from matplotlib.figure import Figure

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QMessageBox

from half_linac.src.shared.machine_profile import (
    MachineProfileError,
    get_workflow,
    load_profile,
)
from half_linac.src.shared.machine_profile.loader import (
    _validate_linked_timing_energy_scan,
)
from half_linac.src.apps.power_source_timing.main import TimingWindow
from half_linac.src.apps.power_source_timing import scan_executor
from half_linac.src.apps.power_source_timing.model import DEVICES
from half_linac.src.apps.power_source_timing.profile_runtime import (
    TimingGroup,
    load_timing_runtime,
)
from half_linac.src.apps.power_source_timing.scan import (
    TimingEnergyMatchResult,
    TimingEnergyScanPoint,
    TimingEnergyScanResult,
    TimingEnergyScanner,
    TimingEnergyScanSettings,
    delay_targets,
    select_best_point,
)
from half_linac.src.apps.power_source_timing.scan_dialog import (
    LinkedTimingEnergyScanDialog,
)
from half_linac.src.apps.power_source_timing.scan_executor import (
    TimingEnergyScanExecutor,
    TimingEnergyScanRequest,
)
from half_linac.src.apps.power_source_timing.scan_log import TimingEnergyScanLog


def _settings(**updates):
    values = {
        "low_offset_us": -0.2,
        "high_offset_us": 0.2,
        "step_us": 0.1,
        "hard_limit_us": 1.0,
        "timing_settle_time_s": 1.0,
        "energy_settle_time_s": 3.0,
        "tracking_half_window_mev": 50.0,
        "energy_low_mev": 0.0,
        "energy_high_mev": 2450.0,
        "minimum_delay_us": 0.0,
        "min_relative_brightness": 0.05,
    }
    values.update(updates)
    return TimingEnergyScanSettings(**values)


def _match(energy, *, setpoint=None, brightness=100.0, status="DONE"):
    return TimingEnergyMatchResult(
        True,
        status,
        energy_mev=float(energy),
        energy_setpoint_mev=float(setpoint if setpoint is not None else energy),
        energy_standard_error_mev=0.1,
        brightness=brightness,
        valid_frames=5,
        total_frames=5,
    )


def test_offset_order_and_grid_validation():
    assert _settings().offsets() == (0.0, 0.1, 0.2, -0.1, -0.2)
    with pytest.raises(ValueError, match="step grid"):
        _settings(high_offset_us=0.25)


def test_common_offset_preserves_relative_delays_and_minimum():
    initial = {"hv": 10.0, "llrf": 11.0, "ssa": 12.0, "kly": 13.0}
    targets = delay_targets(initial, -0.5, minimum_us=0.0)
    assert targets == {"hv": 9.5, "llrf": 10.5, "ssa": 11.5, "kly": 12.5}
    assert targets["kly"] - targets["hv"] == initial["kly"] - initial["hv"]
    with pytest.raises(ValueError, match="below"):
        delay_targets(initial, -10.1, minimum_us=0.0)


def test_scanner_returns_through_center_waits_twice_energy_settle_and_restores():
    initial = {"hv": 10.0, "llrf": 11.0, "ssa": 12.0, "kly": 13.0}
    delay_writes = []
    center_energy_writes = []
    restored_energy = []
    waits = []
    events = []
    search_centers = []
    measured = {
        0.0: (2200.0, 2201.0),
        0.1: (2202.0, 2203.0),
        0.2: (2205.0, 2206.0),
        -0.1: (2204.0, 2205.0),
        -0.2: (2201.0, 2202.0),
    }

    def set_delays(targets):
        delay_writes.append(dict(targets))
        return dict(targets)

    def match_energy(offset, center, *_args):
        search_centers.append((offset, center))
        return _match(
            measured[offset][0],
            setpoint=measured[offset][1],
        )

    scanner = TimingEnergyScanner(
        _settings(),
        read_delays=lambda: initial,
        set_delays=set_delays,
        read_energy=lambda: 2190.0,
        set_energy=lambda value: restored_energy.append(value),
        write_center_energy=lambda value: center_energy_writes.append(value),
        match_energy=match_energy,
        progress_callback=events.append,
        wait_callback=lambda duration, cancellable: waits.append(
            (duration, cancellable)
        ),
    )
    result = scanner.run()

    assert result.status == "DONE"
    assert [point.offset_us for point in result.points] == [
        0.0,
        0.1,
        0.2,
        -0.1,
        -0.2,
    ]
    # Point writes, one explicit branch-center write, and the final restore.
    assert [write["hv"] - initial["hv"] for write in delay_writes] == pytest.approx(
        [0.0, 0.1, 0.2, 0.0, -0.1, -0.2, 0.0]
    )
    assert center_energy_writes == [2201.0]
    assert (6.0, True) in waits
    assert search_centers == [
        (0.0, 2190.0),
        (0.1, 2201.0),
        (0.2, 2203.0),
        (-0.1, 2201.0),
        (-0.2, 2205.0),
    ]
    assert restored_energy == [2190.0]
    assert result.restore_readbacks_us == initial
    assert result.timing_restored
    assert result.energy_restored
    assert result.best_point.offset_us == pytest.approx(0.2)
    assert result.best_at_boundary
    assert any(event["event"] == "center_return" for event in events)
    assert any(event["event"] == "center_return_verified" for event in events)


def test_scanner_aborts_on_timing_failure_and_still_restores():
    initial = {device: 10.0 + index for index, device in enumerate(DEVICES)}
    calls = 0

    def set_delays(targets):
        nonlocal calls
        calls += 1
        offset = float(targets["hv"] - initial["hv"])
        if offset > 0.05 and calls < 4:
            raise RuntimeError("readback timeout")
        return dict(targets)

    restored_energy = []
    scanner = TimingEnergyScanner(
        _settings(),
        read_delays=lambda: initial,
        set_delays=set_delays,
        read_energy=lambda: 2200.0,
        set_energy=lambda value: restored_energy.append(value),
        write_center_energy=lambda _value: None,
        match_energy=lambda *_args: _match(2200.0),
        wait_callback=lambda *_args: None,
    )
    result = scanner.run()

    assert result.status == "FAILED"
    assert "readback timeout" in result.message
    assert result.timing_restored
    assert result.energy_restored
    assert restored_energy == [2200.0]


def test_best_point_uses_maximum_and_exact_tie_prefers_center():
    def point(offset, energy, index):
        return TimingEnergyScanPoint(
            index=index,
            acquisition_index=index,
            offset_us=offset,
            status="ok",
            targets_us={device: 1.0 for device in DEVICES},
            readbacks_us={device: 1.0 for device in DEVICES},
            search_low_mev=0.0,
            search_high_mev=2450.0,
            matched_energy_mev=energy,
        )

    points = [point(-0.2, 2200.0, 0), point(0.1, 2201.0, 1), point(0.2, 2201.0, 2)]
    assert select_best_point(points).offset_us == pytest.approx(0.1)


def _fake_request(write_many, read_pv, *, readback_tolerance_us=0.001):
    context = SimpleNamespace(
        machine=SimpleNamespace(id="test-machine"),
        control_backend=SimpleNamespace(name="real"),
    )
    channels = {
        device: {
            "delay_set": f"{device}:set",
            "delay_readback": f"{device}:rb",
            "enable": f"{device}:enable",
            "width_set": f"{device}:width:set",
            "width_readback": f"{device}:width:rb",
        }
        for device in DEVICES
    }
    group = TimingGroup("KLY01", "KLY01", channels, {})
    image = SimpleNamespace(get=lambda: [0.0] * 16)
    request = TimingEnergyScanRequest(
        context=context,
        group=group,
        settings=_settings(
            timing_settle_time_s=0.0, energy_settle_time_s=0.0
        ),
        scan={"design_eta_m": -0.7},
        image_pv=image,
        image_shape=(4, 4),
        pixel_width_mm=0.1,
        energy_pv="energy:set",
        point_measurement={"samples": 2, "min_valid_samples": 1, "interval_s": 0},
        readback_tolerance_us=readback_tolerance_us,
        readback_timeout_s=0.0,
    )
    return TimingEnergyScanExecutor(
        request,
        manage_locks=False,
        write_many=write_many,
        read_pv=read_pv,
    )


def test_executor_rejects_partial_timing_write():
    executor = _fake_request(
        lambda *_args, **_kwargs: [1, 1, 0, 1],
        lambda _pv: 10.0,
    )
    with pytest.raises(RuntimeError, match="SSA"):
        executor.set_delays({device: 10.0 for device in DEVICES})


def test_executor_verifies_all_four_readbacks():
    values = {
        f"{device}:rb": 10.0 for device in DEVICES
    }
    executor = _fake_request(
        lambda *_args, **_kwargs: [1, 1, 1, 1],
        lambda pv: values[pv],
    )
    actual = executor.set_delays({device: 10.0 for device in DEVICES})
    assert actual == {device: 10.0 for device in DEVICES}


def test_executor_reads_live_values_instead_of_monitor_cache(monkeypatch):
    def fake_caget(_pv, *, use_monitor=True):
        return 9.0 if use_monitor else 10.0

    monkeypatch.setattr(scan_executor, "caget", fake_caget)
    executor = _fake_request(lambda *_args, **_kwargs: [1] * len(DEVICES), None)

    actual = executor.set_delays({device: 10.0 for device in DEVICES})

    assert actual == {device: 10.0 for device in DEVICES}


def test_executor_identifies_each_readback_mismatch_on_timeout():
    readbacks = {"hv:rb": 10.0, "llrf:rb": 10.015, "ssa:rb": None, "kly:rb": 10.0}
    executor = _fake_request(lambda *_args, **_kwargs: [1] * len(DEVICES), readbacks.get)

    with pytest.raises(RuntimeError, match="Timing readback verification failed") as exc:
        executor.set_delays({device: 10.0 for device in DEVICES})

    message = str(exc.value)
    assert "LLRF target=10, readback=10.015, delta=+0.015" in message
    assert "SSA target=10, readback unavailable" in message
    assert "HV target=" not in message
    assert "Tolerance=0.001 us" in message


@pytest.mark.parametrize("direction", [-1, 1])
def test_executor_accepts_two_ns_and_rejects_larger_deviations(direction):
    target = 228.26326530612244
    readback = target + direction * 0.002
    executor = _fake_request(
        lambda *_args, **_kwargs: [1] * len(DEVICES),
        lambda _pv: readback,
        readback_tolerance_us=0.002,
    )
    targets = dict.fromkeys(DEVICES, target)

    assert executor.set_delays(targets) == dict.fromkeys(DEVICES, readback)
    readback = target + direction * 0.002001
    with pytest.raises(RuntimeError, match="Tolerance=0.002 us"):
        executor.set_delays(targets)


def test_executor_verifies_energy_setpoint_after_write():
    values = {"energy:set": 2200.0}
    executor = _fake_request(
        lambda *_args, **_kwargs: [1, 1, 1, 1],
        lambda pv: values[pv],
    )
    writes = []

    def write(pv, value, **_kwargs):
        writes.append((pv, value))
        values[pv] = value
        return True

    executor.write_pv = write
    executor.write_energy(2205.0)
    assert writes == [("energy:set", 2205.0)]

    values["energy:set"] = 2200.0
    executor.write_pv = lambda *_args, **_kwargs: True
    with pytest.raises(RuntimeError, match="verification failed"):
        executor.write_energy(2205.0)


def test_scan_log_serializes_numpy_diagnostics(tmp_path):
    log = TimingEnergyScanLog.create(
        tmp_path,
        {"machine_id": "test", "backend": "real", "group": "KLY01"},
    )
    try:
        log.finish(
            {
                "status": "DONE",
                "timing_restored": True,
                "energy_restored": True,
                "points": [
                    {"diagnostics": {"frame_energies_mev": np.array([1.0, 2.0])}}
                ],
            }
        )
    finally:
        log.close()
    payload = json.loads(log.json_path.read_text(encoding="utf-8"))
    assert payload["points"][0]["diagnostics"]["frame_energies_mev"] == [
        1.0,
        2.0,
    ]


def test_apply_best_rolls_back_both_resources_on_energy_failure():
    executor = _fake_request(
        lambda *_args, **_kwargs: [1, 1, 1, 1],
        lambda _pv: 10.0,
    )
    writes = []
    energies = []
    executor.set_delays = lambda targets: writes.append(dict(targets)) or dict(targets)

    def set_energy(value):
        energies.append(value)
        if len(energies) == 1:
            raise RuntimeError("energy write failed")

    executor.set_energy = set_energy
    point = TimingEnergyScanPoint(
        index=0,
        acquisition_index=0,
        offset_us=0.1,
        status="ok",
        targets_us={device: 10.1 for device in DEVICES},
        readbacks_us={device: 10.1 for device in DEVICES},
        search_low_mev=2100,
        search_high_mev=2300,
        matched_energy_mev=2205,
        energy_setpoint_mev=2204,
    )
    result = TimingEnergyScanResult(
        status="DONE",
        initial_delays_us={device: 10.0 for device in DEVICES},
        initial_energy_mev=2200.0,
        points=(point,),
        best_point=point,
        timing_restored=True,
        energy_restored=True,
        restore_errors={},
    )
    with patch(
        "half_linac.src.apps.power_source_timing.scan_executor."
        "require_workflow_write_allowed"
    ):
        payload = executor.apply_best(result)
    assert payload["status"] == "APPLY_FAILED"
    assert writes[-1] == result.initial_delays_us
    assert energies == [2204.0, 2200.0]


def test_energy_plot_connects_by_offset_and_leaves_gaps_at_invalid_points():
    points = [
        {"offset_us": offset, "matched_energy_mev": energy, "status": status}
        for offset, energy, status in (
            (0.0, 2219.0, "ok"),
            (0.1, 2210.0, "ok"),
            (-0.1, 2214.0, "ok"),
            (-0.2, 2195.0, "low_signal"),
            (-0.3, 2180.0, "ok"),
            (-0.4, None, "failed"),
            (-0.5, 2170.0, "ok"),
        )
    ]
    figure = Figure()
    axes = figure.subplots()
    dialog = SimpleNamespace(
        points_data=points,
        energy_plot=SimpleNamespace(axes=axes, canvas=figure.canvas),
    )

    LinkedTimingEnergyScanDialog._redraw_energy_plot(dialog, best=points[0])

    line = axes.containers[0].lines[0]
    np.testing.assert_array_equal(
        line.get_xdata(orig=False), [-0.5, -0.4, -0.3, -0.2, -0.1, 0.0, 0.1]
    )
    np.testing.assert_array_equal(
        line.get_ydata(orig=False),
        [2170.0, np.nan, 2180.0, np.nan, 2214.0, 2219.0, 2210.0],
    )
    markers = {artist.get_label(): artist for artist in axes.collections}
    np.testing.assert_array_equal(markers["Excluded"].get_offsets(), [[-0.2, 2195.0]])
    np.testing.assert_array_equal(markers["Current"].get_offsets(), [[-0.5, 2170.0]])
    np.testing.assert_array_equal(markers["Best"].get_offsets(), [[0.0, 2219.0]])


def test_runtime_and_dialog_expose_linked_scan_defaults():
    with patch.dict(
        os.environ,
        {
            "HALF_LINAC_MACHINE_ID": "half",
            "HALF_LINAC_CONTROL_BACKEND": "real",
        },
    ):
        runtime = load_timing_runtime()
    config = runtime.linked_energy_scan
    assert config is not None
    assert config.offset_low_us == -1.0
    assert config.offset_high_us == 1.0
    assert config.offset_step_us == 0.1
    assert config.timing_settle_time_s == 1.0

    app = QApplication.instance() or QApplication([])
    group = next(item for item in runtime.groups if item.element_id == "KLY01")
    dialog = LinkedTimingEnergyScanDialog(runtime, group)
    try:
        request = dialog._build_request()
        assert len(request.settings.offsets()) == 21
        assert request.settings.offsets()[1] == 0.1
        assert request.readback_tolerance_us == config.readback_tolerance_us
        assert request.settings.energy_settle_time_s == 3.0
    finally:
        dialog.close()


def test_main_window_disables_scan_for_prebuncher_and_enables_complete_group(
    monkeypatch,
):
    with patch.dict(
        os.environ,
        {
            "HALF_LINAC_MACHINE_ID": "half",
            "HALF_LINAC_CONTROL_BACKEND": "real",
        },
    ):
        runtime = load_timing_runtime()
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        "half_linac.src.apps.power_source_timing.main.GroupMonitor.bind",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        "half_linac.src.apps.power_source_timing.waveform_view.WaveformMonitor.bind",
        lambda *_args: None,
    )
    window = TimingWindow(runtime)
    opened = []
    monkeypatch.setattr(
        LinkedTimingEnergyScanDialog, "exec_", lambda dialog: opened.append(dialog)
    )
    try:
        window._select_group("PREBUNCHER")
        assert not window.scan_button.isEnabled()
        assert "complete" in window.scan_button.toolTip().lower()
        assert window.batch_scan_button.isEnabled()
        window.batch_scan_button.click()
        assert opened[-1].batch_groups
        assert all(group.devices == DEVICES for group in opened[-1].batch_groups)

        window._select_group("KLY01")
        for device in DEVICES:
            for field in ("delay_set", "delay_readback", "enable"):
                window._on_connection(device, field, True)
            window._on_pv_value(device, "delay_set", 10.0)
            window._on_pv_value(device, "delay_readback", 10.0)
            window._on_pv_value(device, "enable", 1)
        assert window.scan_button.isEnabled()
        window.scan_button.click()
        assert not opened[-1].batch_groups
        assert opened[-1].group.element_id == "KLY01"
    finally:
        window.close()


@pytest.mark.parametrize("outcome", ["DONE", "RESTORE_FAILED", "CANCELLED"])
def test_batch_scan_runs_in_order_and_stops_after_failed_restore_or_cancel(
    outcome, monkeypatch, tmp_path,
):
    monkeypatch.setenv("HALF_LINAC_MACHINE_ID", "half")
    monkeypatch.setenv("HALF_LINAC_CONTROL_BACKEND", "real")
    runtime = load_timing_runtime()
    config = runtime.linked_energy_scan
    assert config is not None
    groups = tuple(group for group in runtime.groups if group.devices == DEVICES)
    active_locks = []
    started = Event()
    finish_scan = Event()
    scanned = []
    offsets = []

    @contextmanager
    def lock(name):
        active_locks.append(name)
        try:
            yield
        finally:
            assert active_locks.pop() == name

    def executor(request, *, cancel_requested, callbacks, runs_dir, manage_locks):
        def run():
            assert active_locks == ["timing", "energy"]
            assert runs_dir == tmp_path
            assert not manage_locks
            assert request.readback_tolerance_us == config.readback_tolerance_us
            scanned.append(request.group.element_id)
            offsets.append(request.settings.offsets())
            started.set()
            if outcome == "CANCELLED":
                assert finish_scan.wait(5)
                assert cancel_requested()
            best = None
            if outcome == "DONE" and request.group == groups[0]:
                best = TimingEnergyScanPoint(
                    index=0,
                    acquisition_index=0,
                    offset_us=0.0,
                    status="ok",
                    targets_us={device: 10.0 for device in DEVICES},
                    readbacks_us={device: 10.0 for device in DEVICES},
                    search_low_mev=2150.0,
                    search_high_mev=2250.0,
                    matched_energy_mev=2201.0,
                )
                callbacks.point(best.to_mapping())
            return TimingEnergyScanResult(
                status="CANCELLED" if outcome == "CANCELLED" else "DONE",
                initial_delays_us={device: 10.0 for device in DEVICES},
                initial_energy_mev=2200.0,
                points=(best,) if best else (),
                best_point=best,
                timing_restored=outcome != "RESTORE_FAILED",
                energy_restored=True,
                restore_errors={},
                json_path=str(tmp_path / "scan.json"),
            )

        return SimpleNamespace(run=run)

    monkeypatch.setattr(
        scan_executor.TimingControlLock, "for_machine", lambda *_: lock("timing")
    )
    monkeypatch.setattr(
        scan_executor.EnergyControlLock, "for_machine", lambda *_: lock("energy")
    )
    monkeypatch.setattr(scan_executor.epics.ca, "use_initial_context", lambda: None)
    monkeypatch.setattr(
        scan_executor, "new_app_run_dir", lambda *_args, **_kwargs: tmp_path
    )
    monkeypatch.setattr(scan_executor, "TimingEnergyScanExecutor", executor)
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.Yes)

    app = QApplication.instance() or QApplication([])
    dialog = LinkedTimingEnergyScanDialog(runtime, None, batch_groups=groups)
    dialog.show()
    try:
        assert not dialog.start.isEnabled()
        assert not dialog.apply_best.isVisible()
        dialog.batch_table.item(2, 0).setCheckState(Qt.Checked)
        dialog.batch_table.item(0, 0).setCheckState(Qt.Checked)
        dialog.high.setValue(0.2)
        dialog.start.click()
        worker = dialog.worker
        assert worker is not None
        if outcome == "CANCELLED":
            assert started.wait(5)
            dialog.reject()
            assert dialog.isVisible()
            finish_scan.set()
        assert worker.wait(5000)
        app.processEvents()

        assert not active_locks
        assert dialog.worker is None
        assert dialog.batch_table.item(1, 1).text() == "—"
        if outcome == "DONE":
            assert scanned == [groups[0].element_id, groups[2].element_id]
            assert offsets[0] == offsets[1]
            assert offsets[0][2] == pytest.approx(0.2)
            assert dialog.batch_table.item(0, 2).text() == "+0.000"
            assert dialog.batch_table.item(0, 3).text() == "2201.000"
            assert dialog.batch_table.item(2, 1).text() == "No valid point"
            assert dialog.table.rowCount() == 0
        else:
            assert scanned == [groups[0].element_id]
            assert dialog.batch_table.item(2, 1).text() == "Not run"
            if outcome == "RESTORE_FAILED":
                assert dialog.batch_table.item(0, 1).text() == "Restore failed"
                assert "RESTORE FAILED" in dialog.machine_state.text()
            else:
                assert dialog.batch_table.item(0, 1).text() == "Cancelled"
                assert not dialog.isVisible()
    finally:
        finish_scan.set()
        if dialog.worker is not None:
            dialog.worker.wait(5000)
            app.processEvents()
        dialog.close()


def test_machine_profile_rejects_invalid_linked_scan_grid_and_hard_limit():
    profile = load_profile("half")
    workflow = deepcopy(get_workflow(profile, "power_source_timing"))
    workflow["linked_energy_scan"]["offset"]["high"] = 0.95
    with pytest.raises(MachineProfileError, match="step grid"):
        _validate_linked_timing_energy_scan(
            profile, workflow, backends=("real",)
        )

    workflow = deepcopy(get_workflow(profile, "power_source_timing"))
    workflow["linked_energy_scan"]["offset"]["hard_limit"] = 1.1
    with pytest.raises(MachineProfileError, match="hard_limit"):
        _validate_linked_timing_energy_scan(
            profile, workflow, backends=("real",)
        )


def test_machine_profile_reuses_energy_match_validation_for_timing_scan():
    profile = load_profile("half")
    workflow = deepcopy(get_workflow(profile, "power_source_timing"))
    defaults = workflow["linked_energy_scan"]["energy_match_defaults"]
    defaults["measurement"]["min_fit_r_squared"] = 1.1
    with pytest.raises(MachineProfileError, match="min_fit_r_squared"):
        _validate_linked_timing_energy_scan(
            profile, workflow, backends=("real",)
        )
