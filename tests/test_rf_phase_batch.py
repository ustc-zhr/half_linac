from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from half_linac.src.apps.rf_phase_scan.batch import (
    CrestQualitySettings,
    RFPhaseBatchDevice,
    RFPhaseBatchRunner,
    RFPhaseBatchTask,
    discover_batch_devices,
    evaluate_crest_quality,
)
from half_linac.src.apps.rf_phase_scan.executor import RFPhaseScanRequest
from half_linac.src.apps.rf_phase_scan.history import load_batch_history
from half_linac.src.apps.rf_phase_scan.phase_energy_scan import PhaseScanSettings
from half_linac.src.shared.machine_profile import (
    MachineProfileError,
    get_workflow,
    load_app_context,
    load_profile,
)
from half_linac.src.shared.machine_profile.loader import _validate_basic_app_support


def _scan_result(
    *, crest=0.0, baseline=1000.0, amplitude=10.0, r_squared=0.98,
    points_used=7, rmse=0.2, status="DONE", restored=True,
):
    phases = (-30.0, -20.0, -10.0, 0.0, 10.0, 20.0, 30.0)
    return {
        "status": status,
        "message": None,
        "initial_phase_deg": 0.0,
        "initial_energy_mev": baseline,
        "phase_restored": restored,
        "energy_restored": restored,
        "points": [
            {"index": index, "requested_phase_unwrapped_deg": phase}
            for index, phase in enumerate(phases)
        ],
        "fit": {
            "baseline_energy_mev": baseline,
            "amplitude_mev": amplitude,
            "crest_phase_unwrapped_deg": crest,
            "crest_phase_command_deg": crest,
            "rmse_mev": rmse,
            "r_squared": r_squared,
            "points_used": points_used,
        },
        "csv_path": "scan.csv",
        "json_path": "scan.json",
    }


def test_crest_quality_accepts_valid_fit_and_one_step_boundary():
    assessment = evaluate_crest_quality(
        _scan_result(crest=-20.0),
        CrestQualitySettings(),
        energy_low_mev=0.0,
        energy_high_mev=2450.0,
    )

    assert assessment.accepted
    assert assessment.crest_phase_deg == pytest.approx(-20.0)
    assert assessment.crest_energy_mev == pytest.approx(1010.0)


def test_batch_history_loads_fitted_and_applied_crests(tmp_path):
    batch_path = tmp_path / "batch.json"
    batch_path.write_text(
        json.dumps(
            {
                "schema_version": "rf_crest_batch_v1",
                "machine_id": "half",
                "backend": "real",
                "status": "completed_with_skips",
                "updated_at": "2026-10-29T12:30:00+08:00",
                "error": None,
                "items": [
                    {
                        "element_id": "LLRF01",
                        "display_name": "LLRF 01",
                        "status": "applied",
                        "applied_phase_deg": 5.0,
                        "json_path": "001_LLRF01/phase_energy_scan.json",
                        "quality": {
                            "accepted": True,
                            "reasons": [],
                            "crest_phase_deg": 5.0,
                            "energy_gain_mev": 15.0,
                            "r_squared": 0.99,
                        },
                    },
                    {
                        "element_id": "LLRF02",
                        "display_name": "LLRF 02",
                        "status": "skipped",
                        "quality": {
                            "accepted": False,
                            "reasons": ["Curve R2 is below threshold."],
                            "crest_phase_deg": -8.0,
                            "energy_gain_mev": 9.0,
                            "r_squared": 0.75,
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    history = load_batch_history(batch_path)

    assert history.status == "completed_with_skips"
    assert len(history.items) == 2
    assert history.items[0].crest_phase_deg == pytest.approx(5.0)
    assert history.items[0].applied_phase_deg == pytest.approx(5.0)
    assert history.items[0].json_path == (
        tmp_path / "001_LLRF01/phase_energy_scan.json"
    )
    assert history.items[1].crest_phase_deg == pytest.approx(-8.0)
    assert history.items[1].applied_phase_deg is None
    assert history.items[1].accepted is False
    assert history.items[1].reasons == ("Curve R2 is below threshold.",)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"r_squared": 0.89}, "R2"),
        ({"points_used": 4}, "points"),
        ({"rmse": float("nan")}, "not finite"),
        ({"crest": -21.0}, "interior"),
        ({"baseline": 2445.0, "amplitude": 10.0}, "energy limits"),
    ],
)
def test_crest_quality_rejects_invalid_fit(changes, reason):
    assessment = evaluate_crest_quality(
        _scan_result(**changes),
        CrestQualitySettings(),
        energy_low_mev=0.0,
        energy_high_mev=2450.0,
    )

    assert not assessment.accepted
    assert reason.lower() in " ".join(assessment.reasons).lower()


def test_half_batch_candidates_are_profile_ordered_and_exclude_prebuncher():
    context = load_app_context(
        "rf_phase_scan", machine_id="half", control_backend="real"
    )
    workflow = get_workflow(context.profile, "rf_phase_scan")
    devices = discover_batch_devices(
        context,
        workflow,
        workflow["scan"]["phase"]["readback_verification"]["channel"],
    )

    assert [device.element_id for device in devices] == [
        f"LLRF{index:02d}" for index in range(1, 21)
    ]
    assert all(
        left.order < right.order for left, right in zip(devices, devices[1:])
    )


def test_rf_batch_profile_is_optional_and_rejects_invalid_quality():
    profile = load_profile("half")
    workflow = deepcopy(get_workflow(profile, "rf_phase_scan"))
    workflow.pop("batch")
    profile.workflows["rf_phase_scan"] = workflow
    _validate_basic_app_support(profile, "rf_phase_scan", "real")

    workflow = deepcopy(get_workflow(load_profile("half"), "rf_phase_scan"))
    workflow["batch"]["quality"]["min_points_used"] = 14
    profile.workflows["rf_phase_scan"] = workflow
    with pytest.raises(MachineProfileError, match="min_points_used"):
        _validate_basic_app_support(profile, "rf_phase_scan", "real")


def test_rf_batch_profile_rejects_unknown_exclusion_tag():
    profile = load_profile("half")
    workflow = deepcopy(get_workflow(profile, "rf_phase_scan"))
    workflow["batch"]["exclude_tags"] = ["not-a-physical-tag"]
    profile.workflows["rf_phase_scan"] = workflow

    with pytest.raises(MachineProfileError, match="unknown RF tag"):
        _validate_basic_app_support(profile, "rf_phase_scan", "real")


def test_batch_dialog_defaults_to_all_and_disables_controls_while_running(
    monkeypatch,
):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("HALF_LINAC_MACHINE_ID", "half")
    monkeypatch.setenv("HALF_LINAC_CONTROL_BACKEND", "real")
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QApplication
    from half_linac.src.apps.rf_phase_scan.batch import RFPhaseBatchDialog
    from half_linac.src.apps.rf_phase_scan.main import RFPhaseScanWindow

    app = QApplication.instance() or QApplication([])
    window = RFPhaseScanWindow()
    dialog = RFPhaseBatchDialog(window)

    assert dialog.table.rowCount() == 20
    assert all(
        dialog.table.item(row, 0).checkState() == Qt.Checked
        for row in range(dialog.table.rowCount())
    )
    dialog._select(False)
    assert not dialog.start_button.isEnabled()
    dialog._select(True)
    assert dialog.start_button.isEnabled()

    dialog._set_running(True)
    assert not dialog.table.isEnabled()
    assert not dialog.start_button.isEnabled()
    assert not dialog.history_button.isEnabled()
    assert dialog.stop_button.isEnabled()
    assert not window.start.isEnabled()
    dialog._set_running(False)
    assert dialog.table.isEnabled()
    assert dialog.history_button.isEnabled()

    dialog.close()
    window.close()
    app.processEvents()


def test_single_scan_thread_keeps_executor_owned_lock_behavior(monkeypatch):
    import half_linac.src.apps.rf_phase_scan.main as main_module

    calls = []

    class FakeExecutor:
        def __init__(self, request, **options):
            calls.append((request, options))

        def run(self):
            return {
                "status": "DONE",
                "points": [],
                "fit": None,
                "phase_restored": True,
                "energy_restored": True,
            }

    monkeypatch.setattr(main_module, "RFPhaseScanExecutor", FakeExecutor)
    context = SimpleNamespace(
        machine=SimpleNamespace(id="half"),
        control_backend=SimpleNamespace(name="real"),
    )
    target = SimpleNamespace(element_id="LLRF01", pv_name="PHASE")
    thread = main_module.ScanThread(
        context=context,
        target=target,
        settings=PhaseScanSettings(
            low_offset_deg=-30.0,
            high_offset_deg=30.0,
            points=7,
            phase_settle_time_s=0.0,
            tracking_half_window_mev=50.0,
            energy_low_mev=0.0,
            energy_high_mev=2450.0,
        ),
        scan={"design_eta_m": -0.75},
        image_pv=object(),
        image_shape=(10, 10),
        pixel_width_mm=0.1,
        phase_pv="PHASE",
        phase_readback_pv="PHASE_RB",
        phase_verification={
            "tolerance_deg": 0.1,
            "timeout_s": 1.0,
            "poll_interval_s": 0.01,
        },
        energy_pv="ENERGY",
        remove_bg=False,
        bg_image=None,
        background_metadata={},
        point_measurement={
            "samples": 1,
            "interval_s": 0.0,
            "min_valid_samples": 1,
        },
    )
    emitted = []
    thread.done.connect(emitted.append)

    thread.run()

    assert calls[0][1]["manage_lock"] is True
    assert emitted[0]["status"] == "DONE"


class _FakeLock:
    def __init__(self):
        self.acquires = 0
        self.releases = 0

    def acquire(self):
        self.acquires += 1

    def release(self):
        self.releases += 1


def _batch_fixture(tmp_path: Path, count=2):
    context = SimpleNamespace(
        machine=SimpleNamespace(id="half"),
        control_backend=SimpleNamespace(name="real"),
    )
    state = {"ENERGY": 1000.0}
    devices = []
    tasks = []
    settings = PhaseScanSettings(
        low_offset_deg=-30.0,
        high_offset_deg=30.0,
        points=7,
        phase_settle_time_s=0.0,
        tracking_half_window_mev=50.0,
        energy_low_mev=0.0,
        energy_high_mev=2450.0,
    )
    for index in range(1, count + 1):
        element_id = f"LLRF{index:02d}"
        target = SimpleNamespace(element_id=element_id, pv_name=f"P{index}")
        device = RFPhaseBatchDevice(
            element_id=element_id,
            display_name=element_id,
            order=index,
            target=target,
            phase_pv=f"P{index}",
            phase_readback_pv=f"R{index}",
        )
        state[f"P{index}"] = float(index)
        state[f"R{index}"] = float(index)
        request = RFPhaseScanRequest(
            context=context,
            target=target,
            settings=settings,
            scan={"design_eta_m": -0.75},
            image_pv=object(),
            image_shape=(10, 10),
            pixel_width_mm=0.1,
            phase_pv=f"P{index}",
            phase_readback_pv=f"R{index}",
            phase_verification={
                "tolerance_deg": 0.1,
                "timeout_s": 1.0,
                "poll_interval_s": 0.01,
            },
            energy_pv="ENERGY",
            remove_bg=False,
            bg_image=None,
            background_metadata={},
            point_measurement={
                "samples": 1,
                "interval_s": 0.0,
                "min_valid_samples": 1,
            },
        )
        devices.append(device)
        tasks.append(RFPhaseBatchTask(device, request))
    return context, state, tuple(devices), tuple(tasks), tmp_path / "batch"


def _runner(
    monkeypatch, tmp_path, results, *, count=2, on_apply=None, on_run=None,
    fail_energy_apply=False,
):
    import half_linac.src.apps.rf_phase_scan.batch as batch_module

    monkeypatch.setattr(
        batch_module, "require_workflow_write_allowed", lambda *_args: None
    )
    context, state, devices, tasks, directory = _batch_fixture(tmp_path, count)
    lock = _FakeLock()
    observations = {"manage_lock": [], "initial_energies": []}
    result_map = dict(results)

    class FakeExecutor:
        def __init__(
            self, request, *, cancel_requested, callbacks, runs_dir, manage_lock,
        ):
            self.request = request
            self.cancel_requested = cancel_requested
            self.callbacks = callbacks
            self.runs_dir = Path(runs_dir)
            self.initial_energy = state["ENERGY"]
            self.energy_failure_used = False
            observations["manage_lock"].append(manage_lock)

        def run(self):
            assert (directory / "initial_snapshot.json").is_file()
            observations["initial_energies"].append(state["ENERGY"])
            if on_run is not None:
                on_run(self)
            result = deepcopy(result_map[self.request.target.element_id])
            result["initial_phase_deg"] = state[self.request.phase_pv]
            result["initial_energy_mev"] = self.initial_energy
            return result

        def set_phase(self, value):
            state[self.request.phase_pv] = float(value)
            state[self.request.phase_readback_pv] = float(value)

        def set_energy(self, value):
            if (
                fail_energy_apply
                and not self.energy_failure_used
                and float(value) != self.initial_energy
            ):
                self.energy_failure_used = True
                raise RuntimeError("energy apply failed")
            state["ENERGY"] = float(value)
            if on_apply is not None and float(value) != self.initial_energy:
                on_apply(self)

    runner = RFPhaseBatchRunner(
        context=context,
        devices=devices,
        tasks=tasks,
        quality=CrestQualitySettings(),
        directory=directory,
        executor_factory=FakeExecutor,
        read=state.get,
        lock_factory=lambda *_args: lock,
    )
    return runner, state, lock, observations, directory


def test_batch_applies_each_crest_and_next_scan_uses_followed_energy(
    monkeypatch, tmp_path
):
    runner, state, lock, observations, directory = _runner(
        monkeypatch,
        tmp_path,
        {
            "LLRF01": _scan_result(crest=5.0, baseline=1000.0, amplitude=10.0),
            "LLRF02": _scan_result(crest=15.0, baseline=1010.0, amplitude=20.0),
        },
    )

    result = runner.run()

    assert result.status == "completed"
    assert [record["status"] for record in result.records] == ["applied", "applied"]
    assert state["P1"] == pytest.approx(5.0)
    assert state["P2"] == pytest.approx(15.0)
    assert state["ENERGY"] == pytest.approx(1030.0)
    assert observations["initial_energies"] == pytest.approx([1000.0, 1010.0])
    assert observations["manage_lock"] == [False, False]
    assert (lock.acquires, lock.releases) == (1, 1)
    saved = json.loads((directory / "batch.json").read_text(encoding="utf-8"))
    assert saved["status"] == "completed"
    assert saved["configuration"]["selected_elements"] == ["LLRF01", "LLRF02"]
    assert saved["initial_snapshot"]["energy_setpoint_mev"] == pytest.approx(1000.0)


def test_batch_skips_rejected_fit_and_continues(monkeypatch, tmp_path):
    runner, state, _lock, observations, _directory = _runner(
        monkeypatch,
        tmp_path,
        {
            "LLRF01": _scan_result(r_squared=0.5),
            "LLRF02": _scan_result(crest=12.0, baseline=1000.0, amplitude=15.0),
        },
    )

    result = runner.run()

    assert result.status == "completed_with_skips"
    assert [record["status"] for record in result.records] == ["skipped", "applied"]
    assert state["P1"] == pytest.approx(1.0)
    assert state["P2"] == pytest.approx(12.0)
    assert observations["initial_energies"] == pytest.approx([1000.0, 1000.0])


def test_batch_stop_waits_for_current_restore_and_leaves_remaining_not_run(
    monkeypatch, tmp_path
):
    holder = {}

    def stop_during_scan(_executor):
        holder["runner"].stop()

    runner, state, lock, _observations, _directory = _runner(
        monkeypatch,
        tmp_path,
        {
            "LLRF01": _scan_result(status="CANCELLED", restored=True),
            "LLRF02": _scan_result(),
        },
        on_run=stop_during_scan,
    )
    holder["runner"] = runner

    result = runner.run()

    assert result.status == "stopped"
    assert [record["status"] for record in result.records] == ["stopped", "not_run"]
    assert state["P1"] == pytest.approx(1.0)
    assert state["ENERGY"] == pytest.approx(1000.0)
    assert (lock.acquires, lock.releases) == (1, 1)


def test_batch_external_phase_drift_stops_remaining_items(monkeypatch, tmp_path):
    def drift_after_first_apply(executor):
        if executor.request.target.element_id == "LLRF01":
            state["P2"] = 99.0
            state["R2"] = 99.0

    runner, state, _lock, _observations, _directory = _runner(
        monkeypatch,
        tmp_path,
        {
            "LLRF01": _scan_result(crest=5.0),
            "LLRF02": _scan_result(crest=15.0, baseline=1010.0),
            "LLRF03": _scan_result(crest=25.0, baseline=1020.0),
        },
        count=3,
        on_apply=drift_after_first_apply,
    )

    result = runner.run()

    assert result.status == "failed"
    assert "changed outside this batch" in result.error
    assert [record["status"] for record in result.records] == [
        "applied", "failed", "not_run",
    ]


def test_batch_energy_apply_failure_rolls_back_current_item(monkeypatch, tmp_path):
    runner, state, lock, _observations, _directory = _runner(
        monkeypatch,
        tmp_path,
        {"LLRF01": _scan_result(crest=5.0)},
        count=1,
        fail_energy_apply=True,
    )

    result = runner.run()

    assert result.status == "failed"
    assert result.records[0]["status"] == "failed"
    assert state["P1"] == pytest.approx(1.0)
    assert state["ENERGY"] == pytest.approx(1000.0)
    assert (lock.acquires, lock.releases) == (1, 1)
