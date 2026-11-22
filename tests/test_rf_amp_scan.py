from contextlib import nullcontext
from dataclasses import replace
import json
from threading import Barrier, local
from time import monotonic
from unittest.mock import patch

import pytest

from half_linac.src.apps.rf_phase_scan.amp_scan import (
    AmpReading, AmpScan, AmpSettings, discover_amp_stations,
)
from half_linac.src.shared.machine_profile import MachineControlLock, get_workflow, load_app_context


class SimulatedRf:
    def __init__(self, stations, curve=None):
        self.stations = {station.element_id: station for station in stations}
        self.amps = dict.fromkeys(self.stations, 50.0)
        self.writes = []
        self.output_reads = dict.fromkeys(self.stations, 0)
        self.curve = curve or (lambda amp, _index: 100 - 8 * (amp - 50.5) ** 2)
        self.stale = False
        self.on_write = lambda _element, _value: None
        self.clock = local()
        self.readback = lambda element: self.amps[element]

    @property
    def now(self):
        return getattr(self.clock, "now", 0.0)

    def wait(self, seconds):
        self.clock.now = self.now + seconds

    def read(self, name, timeout=0.5):
        for key, station in self.stations.items():
            if name == station.set_pv:
                return AmpReading(self.amps[key], None)
            if name == station.readback_pv:
                return AmpReading(self.readback(key), None)
            if name == station.output_pv:
                self.output_reads[key] += 1
                number = self.output_reads[key]
                return AmpReading(self.curve(self.amps[key], number), 1 if self.stale else number)
        raise AssertionError(f"Unexpected PV: {name}")

    def write(self, name, value):
        element_id = next(key for key, station in self.stations.items() if station.set_pv == name)
        self.amps[element_id] = value
        self.writes.append((element_id, value))
        self.on_write(element_id, value)

    def close(self):
        pass


@pytest.fixture
def amp_setup():
    context = load_app_context("rf_phase_scan", machine_id="half", control_backend="real")
    config = get_workflow(context.profile, "rf_phase_scan")["amp_scan"]
    settings = replace(AmpSettings.from_config(config), sample_timeout_s=0.5, execution_mode="serial")
    stations = discover_amp_stations(context, config, settings)
    return context, config, settings, stations


def make_runner(amp_setup, path, curve=None):
    context, _config, settings, stations = amp_setup
    approved = tuple(replace(station, window=(48, 52)) for station in stations[1:3])
    simulator = SimulatedRf(approved, curve)
    scan = AmpScan(
        context=context, stations=approved, settings=settings, path=path,
        client_factory=lambda: simulator, lock_factory=lambda *_: nullcontext(),
        monotonic=lambda: simulator.now, wait=simulator.wait,
    )
    return scan, simulator


def test_half_discovers_real_stations_and_uses_validated_profile_windows(amp_setup):
    context, config, settings, stations = amp_setup
    assert [station.element_id for station in stations] == [f"LLRF{index:02d}" for index in range(1, 21)]
    assert set(config["approved_stations"]) == {station.element_id for station in stations}
    assert stations[1].set_pv == "IN:MW:LLRF02:SET_AMP"
    assert stations[1].readback_pv == "IN:MW:LLRF02:GET_AMP"
    assert stations[1].output_pv == "IN:MW:LLRF02:GET_CH2_POWER"
    assert settings.power_unit == "MW"
    assert all(station.window == (0, 100) for station in stations)

    approved = {**config, "approved_stations": {"LLRF02": {"low": -10, "high": 50}}}
    narrowed = discover_amp_stations(context, approved, settings)
    assert narrowed[0].window is None
    assert narrowed[1].window == (0, 50)


def test_amp_settings_accept_legacy_step_and_validate_point_spacing(amp_setup):
    _context, config, settings, _stations = amp_setup
    legacy = {key: value for key, value in config.items() if key not in {
        "half_range", "points", "samples_per_point", "sample_interval_s",
        "execution_mode",
    }}
    legacy["step"] = 0.5
    parsed = AmpSettings.from_config(legacy)
    assert (parsed.half_range, parsed.points, parsed.step) == (1.0, 5, 0.5)
    assert (parsed.samples_per_point, parsed.sample_interval_s) == (5, 0.05)
    assert parsed.execution_mode == "serial"
    with pytest.raises(ValueError, match="odd integer"):
        replace(settings, points=6).validate()
    with pytest.raises(ValueError, match="readback tolerance"):
        replace(settings, half_range=0.1).validate()


def test_configured_scan_points_and_sample_interval(amp_setup, tmp_path):
    _context, _config, settings, _stations = amp_setup
    custom = replace(settings, half_range=1.5, points=7, settle_time_s=0.25,
                     samples_per_point=3, sample_interval_s=0.75)
    context, config, _settings, stations = amp_setup
    scan, simulator = make_runner((context, config, custom, stations), tmp_path / "run.json")
    reads = []
    read = simulator.read

    def track_reads(name, timeout=0.5):
        if name == scan.stations["LLRF02"].output_pv:
            reads.append(simulator.now)
        return read(name, timeout)

    simulator.read = track_reads
    result = scan.run()
    first = result["stations"][0]
    assert first["status"] == "candidate"
    assert [point["amp"] for point in first["points"][:7]] == [50, 49.5, 50.5, 49, 51, 48.5, 51.5]
    assert all(len(point["samples"]) == 3 for point in first["points"])
    assert reads[:4] == pytest.approx([0.25, 0.25, 1.0, 1.75])
    assert result["settings"]["sample_interval_s"] == 0.75


def test_batch_repeats_candidate_restores_and_applies_only_selected(amp_setup, tmp_path):
    path = tmp_path / "run.json"
    scan, simulator = make_runner(amp_setup, path)
    result = scan.run()
    assert result["status"] == "completed"
    expected_order = [49.5, 50.5, 49, 51, 50, 50.5, 50]
    assert simulator.writes == [
        (station, value) for station in ("LLRF02", "LLRF03") for value in expected_order
    ]
    assert simulator.amps == {"LLRF02": 50, "LLRF03": 50}
    for record in result["stations"]:
        assert record["status"] == "candidate"
        assert record["restored"]
        assert record["suggestion"] == 50.5
        assert len(record["points"]) == 7
        assert all(len(point["samples"]) == 5 for point in record["points"])
    applied = scan.apply(["LLRF03"])
    assert applied["status"] == "completed"
    assert simulator.amps == {"LLRF02": 50, "LLRF03": 50.5}
    assert json.loads(path.read_text())["stations"][1]["applied"] == 50.5


def test_parallel_scans_overlap_and_record_each_station(amp_setup, tmp_path):
    path = tmp_path / "run.json"
    scan, simulator = make_runner(amp_setup, path)
    scan.settings = replace(scan.settings, execution_mode="parallel")
    first_step = Barrier(2)
    simulator.on_write = lambda _station, value: first_step.wait(3) if value == 49.5 else None
    result = scan.run()
    assert result["status"] == "completed"
    assert result["settings"]["execution_mode"] == "parallel"
    expected_order = [49.5, 50.5, 49, 51, 50, 50.5, 50]
    for record in result["stations"]:
        assert record["status"] == "candidate" and record["restored"]
        assert len(record["points"]) == 7
        assert [value for station, value in simulator.writes if station == record["station"]] == expected_order
    assert simulator.amps == {"LLRF02": 50, "LLRF03": 50}
    assert [record["station"] for record in json.loads(path.read_text())["stations"]] == ["LLRF02", "LLRF03"]


def test_parallel_stop_waits_for_each_station_restore(amp_setup, tmp_path):
    scan, simulator = make_runner(amp_setup, tmp_path / "run.json")
    scan.settings = replace(scan.settings, execution_mode="parallel")
    first_step = Barrier(2, action=scan.stop)
    simulator.on_write = lambda _station, value: first_step.wait(3) if value == 49.5 else None
    result = scan.run()
    assert result["status"] == "stopped"
    assert all(record["status"] == "stopped" and record["restored"] for record in result["stations"])
    assert sorted(simulator.writes) == [("LLRF02", 49.5), ("LLRF02", 50), ("LLRF03", 49.5), ("LLRF03", 50)]


def test_parallel_write_fault_stops_and_restores_the_other_station(amp_setup, tmp_path):
    scan, simulator = make_runner(amp_setup, tmp_path / "run.json")
    scan.settings = replace(scan.settings, execution_mode="parallel")
    first_step = Barrier(2)

    def fail_at_first_step(station, value):
        if value == 49.5:
            first_step.wait(3)
            if station == "LLRF02":
                raise RuntimeError("Unconfirmed AMP write.")
            assert scan.stopping.wait(3)

    simulator.on_write = fail_at_first_step
    result = scan.run()
    assert result["status"] == "failed"
    assert "LLRF02" in result["error"]
    assert [record["status"] for record in result["stations"]] == ["failed", "stopped"]
    assert all(record["restored"] for record in result["stations"])
    assert simulator.amps == {"LLRF02": 50, "LLRF03": 50}


def test_parallel_scan_rejects_shared_setpoint_pv(amp_setup, tmp_path):
    scan, simulator = make_runner(amp_setup, tmp_path / "run.json")
    scan.settings = replace(scan.settings, execution_mode="parallel")
    scan.stations["LLRF03"] = replace(
        scan.stations["LLRF03"], set_pv=scan.stations["LLRF02"].set_pv
    )
    result = scan.run()
    assert result["status"] == "failed"
    assert "distinct setpoint PV" in result["error"]
    assert simulator.writes == []


@pytest.mark.parametrize(
    "curve, status, reason",
    [
        (lambda _amp, _index: 100, "keep", ""),
        (lambda amp, _index: amp, "undetermined", "boundary"),
        (lambda amp, _index: 100 if amp >= 50.5 else 90, "undetermined", "plateau"),
        (lambda _amp, index: 100 if index < 31 else 110, "undetermined", "baseline drift"),
        (
            lambda amp, index: 100 - 8 * (amp - 50.5) ** 2 - (2 if index > 36 else 0),
            "undetermined", "did not reproduce",
        ),
    ],
)
def test_peak_qualification(amp_setup, tmp_path, curve, status, reason):
    scan, simulator = make_runner(amp_setup, tmp_path / "run.json", curve)
    result = scan.run()
    assert result["status"] == "completed"
    assert set(simulator.amps.values()) == {50}
    assert all(record["status"] == status for record in result["stations"])
    assert all(reason in record.get("error", "") for record in result["stations"])


def test_duplicate_timestamps_and_out_of_range_do_not_write(amp_setup, tmp_path):
    scan, simulator = make_runner(amp_setup, tmp_path / "run.json")
    simulator.stale = True
    simulator.amps["LLRF03"] = 51.5
    first, second = scan.run()["stations"]
    assert "Insufficient new KLY power" in first["error"]
    assert "approved AMP window" in second["error"]
    assert simulator.writes == []


def test_operator_stop_restores_current_station_and_ends_queue(amp_setup, tmp_path):
    scan, simulator = make_runner(amp_setup, tmp_path / "run.json")
    simulator.on_write = lambda _, value: scan.stop() if value == 49.5 else None
    result = scan.run()
    assert result["status"] == "stopped"
    assert simulator.writes == [("LLRF02", 49.5), ("LLRF02", 50)]
    assert result["stations"][0]["restored"]
    assert result["stations"][1]["status"] == "pending"


@pytest.mark.parametrize("interrupt", ["external change", "power read failure", "failed readback"])
def test_write_fault_stops_the_queue_and_reports_restore(amp_setup, tmp_path, interrupt):
    scan, simulator = make_runner(amp_setup, tmp_path / "run.json")
    read = simulator.read

    def read_during_fault(name, timeout=0.5):
        if name == scan.stations["LLRF02"].output_pv:
            raise RuntimeError("KLY power read failed.")
        return read(name, timeout)

    def disrupt(element, value):
        if value != 49.5:
            return
        if interrupt == "external change":
            simulator.amps[element] = 51.5
        elif interrupt == "power read failure":
            simulator.read = read_during_fault
        else:
            simulator.readback = lambda key: 50

    simulator.on_write = disrupt
    result = scan.run()
    first, second = result["stations"]
    assert result["status"] == "failed"
    assert second["status"] == "pending"
    if interrupt != "external change":
        assert first["restored"]
        assert simulator.writes == [("LLRF02", 49.5), ("LLRF02", 50)]
    else:
        assert first["status"] == "restore_failed"
        assert simulator.writes == [("LLRF02", 49.5)]


def test_workflow_write_policy_blocks_scan_before_connecting(amp_setup, tmp_path):
    scan, _simulator = make_runner(amp_setup, tmp_path / "run.json")
    profile = scan.context.profile
    workflow = get_workflow(profile, "rf_phase_scan")
    scan.context = replace(scan.context, profile=replace(
        profile, workflows={"rf_phase_scan": {**workflow, "write_control": {"real": "blocked"}}},
    ))
    with patch.object(scan, "client_factory") as connect:
        result = scan.run()
    connect.assert_not_called()
    assert result["status"] == "failed"
    assert "write_control" in result["error"]


def test_partial_apply_leaves_external_change_as_found(amp_setup, tmp_path):
    scan, simulator = make_runner(amp_setup, tmp_path / "run.json")
    scan.run()
    simulator.amps["LLRF03"] = 51.5
    count = len(simulator.writes)
    result = scan.apply(["LLRF02", "LLRF03"])
    assert result["status"] == "failed"
    assert [row["status"] for row in result["stations"]] == ["applied", "apply_failed"]
    assert simulator.writes[count:] == [("LLRF02", 50.5)]
    assert simulator.amps["LLRF03"] == 51.5


def test_external_change_to_candidate_during_apply_is_not_rolled_back(amp_setup, tmp_path):
    scan, simulator = make_runner(amp_setup, tmp_path / "run.json")
    scan.run()

    def change_amp(record):
        if record["status"] == "applying":
            simulator.amps["LLRF02"] = record["suggestion"]

    scan.on_update = change_amp
    count = len(simulator.writes)
    result = scan.apply(["LLRF02"])
    assert result["stations"][0]["status"] == "apply_failed"
    assert simulator.amps["LLRF02"] == 50.5
    assert len(simulator.writes) == count


def test_shared_lock_blocks_scan_and_manual_amp_write(amp_setup, tmp_path):
    from half_linac.src.apps.llrf_control.epics_client import WriteWorker

    scan, simulator = make_runner(amp_setup, tmp_path / "run.json")
    lock_path = tmp_path / "lock"
    scan.lock_factory = lambda *_: MachineControlLock(lock_path, {})
    worker = WriteWorker("amplitude", "TEST:SET_AMP", 50, machine_id="half")
    completed = []
    worker.completed.connect(lambda *args: completed.append(args))
    with (
        MachineControlLock(lock_path, {"operation": "phase scan"}),
        patch("half_linac.src.apps.llrf_control.epics_client.EnergyControlLock.for_machine",
              side_effect=scan.lock_factory),
        patch("half_linac.src.apps.llrf_control.epics_client.epics.caput") as caput,
    ):
        assert "busy" in scan.run()["error"]
        worker.run()
        caput.assert_not_called()
    assert simulator.writes == []
    assert completed[0][2] is False
    assert "busy" in completed[0][3]


def test_offscreen_amp_scan_review_apply_and_phase_handoff(amp_setup, tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("HALF_LINAC_MACHINE_ID", "half")
    monkeypatch.setenv("HALF_LINAC_CONTROL_BACKEND", "real")
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QApplication, QMessageBox, QToolBar
    from half_linac.src.apps.rf_phase_scan import amp_scan_ui
    from half_linac.src.apps.rf_phase_scan.main import RFPhaseScanWindow

    mock_scan, simulator = make_runner(amp_setup, tmp_path / "unused.json")
    monkeypatch.setattr(amp_scan_ui, "discover_amp_stations", lambda *_: tuple(mock_scan.stations.values()))
    monkeypatch.setattr(amp_scan_ui, "AmpScan", lambda **kwargs: AmpScan(
        **kwargs, client_factory=lambda: simulator, lock_factory=lambda *_: nullcontext(),
        monotonic=lambda: simulator.now, wait=simulator.wait,
    ))
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.Yes)
    monkeypatch.setattr(RFPhaseScanWindow, "_load_latest_background", lambda *_args, **_kwargs: None)
    app = QApplication.instance() or QApplication([])
    window = RFPhaseScanWindow()
    page = window.amp_page
    page.runs_dir = tmp_path

    def finish():
        deadline = monotonic() + 5
        while page.is_running() and monotonic() < deadline:
            app.processEvents()
        assert not page.is_running()

    try:
        window.tuning_tabs.setCurrentWidget(page)
        assert page.execution_mode.currentText() == "Parallel"
        assert all(selector.isChecked() for selector in page.selectors)
        assert page.start_button.isEnabled()
        assert simulator.writes == []
        page.clear_button.click()
        assert not page.start_button.isEnabled()
        page.execution_mode.setCurrentText("Serial")
        assert page.status.parentWidget().objectName() == "summaryPanel"
        assert page.table.parentWidget().objectName() == "plotCard"
        assert page.plot.parentWidget().objectName() == "plotCard"
        assert page.plot.findChild(QToolBar).isHidden()
        assert not page.plot.axes.axison
        assert page.table.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
        page.half_range.setValue(1.5)
        page.points.setValue(7)
        page.settle.setValue(0.25)
        page.samples.setValue(3)
        page.sample_interval.setValue(0.75)
        page.all_button.click()
        assert page.start_button.isEnabled()
        page.start_button.click()
        assert not window.tuning_tabs.isTabEnabled(0)
        assert not window.tuning_tabs.isTabEnabled(1)
        assert all(not widget.isEnabled() for widget in page.parameter_inputs)
        finish()
        assert page.records["LLRF02"]["status"] == "candidate"
        assert page.plot.axes.get_ylabel() == "KLY forward power (MW)"
        assert page.progress.value() == page.progress.maximum() == 2
        assert page.runner.settings == page._scan_settings()
        assert page.runner.settings.execution_mode == "serial"
        assert all(widget.isEnabled() for widget in page.parameter_inputs)
        assert len(page.records["LLRF02"]["points"][0]["samples"]) == 3
        assert not page.selectors[0].isChecked()
        assert not page.apply_button.isEnabled()
        assert simulator.amps == {"LLRF02": 50, "LLRF03": 50}
        page.selectors[0].setChecked(True)
        page.apply_button.click()
        finish()
        assert page.progress.value() == page.progress.maximum() == 1
        assert simulator.amps == {"LLRF02": 50.5, "LLRF03": 50}
        window.tuning_tabs.setCurrentWidget(window.crest_page)
        assert window.combo.currentText() == "LLRF02"
        assert [
            stage.rf_element for stage, box in zip(
                window.recovery_page.candidates, window.recovery_page.stage_checkboxes
            ) if box.isChecked()
        ] == ["LLRF02"]
        window._show_batch_dialog()
        assert [
            window.batch_dialog.devices[row].element_id for row in window.batch_dialog._rows()
        ] == ["LLRF02"]
        assert not window._phase_busy()
    finally:
        window.close()
        app.processEvents()


@pytest.mark.parametrize("select_all_by_default", [True, False])
def test_amp_only_profile_opens_without_beam_diagnostics(
    amp_setup, tmp_path, monkeypatch, select_all_by_default,
):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication
    from half_linac.src.apps.rf_phase_scan import main
    from half_linac.src.shared.machine_profile.loader import _validate_basic_app_support

    context, config, _settings, _stations = amp_setup
    scan_config = dict(config)
    if not select_all_by_default:
        scan_config.pop("default_selection", None)
    only_amp = {"amp_scan": scan_config, "control_backends": ["real"]}
    profile = replace(context.profile, workflows={"rf_phase_scan": only_amp})
    _validate_basic_app_support(profile, "rf_phase_scan", "real")
    monkeypatch.setattr(main, "load_app_context", lambda _name: replace(context, profile=profile))
    monkeypatch.setattr(main, "resolve_app_runtime_paths", lambda *_: {"runs_dir": tmp_path})
    app = QApplication.instance() or QApplication([])
    window = main.RFPhaseScanWindow()
    try:
        assert window.tuning_tabs.count() == 1
        assert window.tuning_tabs.tabText(0) == "Power Scan"
        assert window.amp_page.table.rowCount() == 20
        assert window.amp_page.table.item(0, 5).text() == "Ready"
        assert all(selector.isEnabled() for selector in window.amp_page.selectors)
        assert all(selector.isChecked() == select_all_by_default for selector in window.amp_page.selectors)
        assert window.amp_page.start_button.isEnabled() == select_all_by_default
        window.amp_page.all_button.click()
        assert window.amp_page.start_button.isEnabled()
    finally:
        window.close()
        app.processEvents()
