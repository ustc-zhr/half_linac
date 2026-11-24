from contextlib import nullcontext
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from half_linac.src.apps.rf_phase_scan.acceleration_check import (
    AccelerationCheck,
    discover_acceleration_checks,
    resolve_acceleration_check,
)
from half_linac.src.apps.rf_phase_scan.amp_scan import AmpReading
from half_linac.src.apps.rf_phase_scan.transmission_recovery import (
    discover_recovery_stages,
    settings_from_config,
)
from half_linac.src.shared.machine_profile import get_workflow, load_app_context


@pytest.fixture
def check_setup():
    context = load_app_context("rf_phase_scan", machine_id="half", control_backend="real")
    recovery = get_workflow(context.profile, "rf_phase_scan")["transmission_recovery"]
    stages = {stage.rf_element: stage for stage in discover_recovery_stages(context, recovery)}
    settings = replace(settings_from_config(recovery), readback_timeout_s=0.2)
    spec = {
        "enabled": True,
        "bpm": "BPM10",
        "corrector": "XC10",
        "amplitude": {"window": [48, 52], "step": 1.0, "tolerance": 0.02},
        "current": {"window": [-1, 1], "step": 0.1, "tolerance": 0.01},
        "conditions": [{"element": "QL16", "channel": "current_readback", "window": [3, 4]}],
        "samples_per_state": 3,
        "minimum_sum": 1.0,
        "maximum_relative_sum_change": 0.2,
        "sample_timeout_s": 0.25,
        "pair_tolerance_s": 0.001,
        "settle_time_s": 0.01,
        "minimum_response_difference": 0.15,
        "maximum_baseline_drift": 0.15,
    }
    config = resolve_acceleration_check(context, stages["LLRF02"], settings, spec)
    return context, recovery, stages, settings, spec, config


class SimulatedBeam:
    def __init__(self, config, lower_response=2.0):
        self.config = config
        self.now = 0.0
        self.values = {
            config.phase_pv: 12.0,
            config.phase_readback_pv: 12.0,
            config.amplitude.setpoint: 50.0,
            config.corrector.setpoint: 0.2,
            config.conditions[0].pv: 3.5,
        }
        self.lower_response = lower_response
        self.nominal_response = 1.0
        self.sum = 10.0
        self.stale = False
        self.skew = 0.0
        self.writes = []
        self.on_write = lambda _pv, _value: None
        self.current_readback = lambda: self.values[config.corrector.setpoint]

    def wait(self, seconds):
        self.now += seconds

    def read(self, pv, timeout=0.5):
        config = self.config
        amplitude = self.values[config.amplitude.setpoint]
        if pv == config.x_pv:
            slope = self.nominal_response if amplitude == 50.0 else self.lower_response
            # Offset changes with RF amplitude and drops out of each paired response.
            x = amplitude + self.current_readback() * slope
            return AmpReading(x, 1.0 if self.stale else self.now)
        if pv == config.sum_pv:
            return AmpReading(self.sum, 1.0 if self.stale else self.now + self.skew)
        if pv == config.corrector.readback:
            return AmpReading(self.current_readback(), None)
        if pv == config.amplitude.readback:
            return AmpReading(amplitude, None)
        return AmpReading(self.values[pv], None)

    def write(self, pv, value):
        assert pv in (self.config.amplitude.setpoint, self.config.corrector.setpoint)
        self.values[pv] = value
        self.writes.append((pv, value))
        self.on_write(pv, value)

    def close(self):
        pass


def make_check(check_setup, tmp_path, lower_response=2.0):
    context, _, _, _, _, config = check_setup
    beam = SimulatedBeam(config, lower_response)
    runner = AccelerationCheck(
        context=context, config=config, applied_phase=12.0,
        recovery_path=tmp_path / "run.json", path=tmp_path / "acceleration_check.json",
        on_state=lambda _: None, client_factory=lambda: beam,
        lock_factory=lambda *_: nullcontext(), monotonic=lambda: beam.now, sleep=beam.wait,
    )
    return runner, beam


def test_half_has_no_enabled_checks_and_bad_paths_keep_sum_recovery(check_setup):
    context, recovery, stages, settings, spec, _ = check_setup
    configs, unavailable = discover_acceleration_checks(context, recovery, stages, settings)
    assert configs == {}
    assert "not been enabled" in unavailable["LLRF02"]
    invalid = {"stages": [{"rf_element": "LLRF02", "acceleration_check": {**spec, "corrector": "YC10"}}]}
    configs, unavailable = discover_acceleration_checks(context, invalid, stages, settings)
    assert configs == {}
    assert "horizontal corrector" in unavailable["LLRF02"]
    assert stages["LLRF02"].target_bpm == "BPM08"


@pytest.mark.parametrize(
    "lower_response, status", [(2.0, "accelerating"), (0.5, "decelerating"), (1.1, "undetermined")]
)
def test_measurement_classifies_and_logs_three_groups(check_setup, tmp_path, lower_response, status):
    runner, beam = make_check(check_setup, tmp_path, lower_response)
    result = runner.run()
    assert result["status"] == status
    assert result["restore_status"] == "verified"
    assert result["measured_phase_deg"] == 12.0
    assert [group["amplitude"] for group in result["groups"]] == [50.0, 49.0, 50.0]
    assert [group["slope"] for group in result["groups"]] == pytest.approx([1, lower_response, 1])
    assert len(result["groups"][0]["points"][0]["samples"]) == 3
    assert beam.values[runner.config.amplitude.setpoint] == 50.0
    assert beam.values[runner.config.corrector.setpoint] == 0.2
    stored = json.loads(runner.path.read_text())
    assert stored["status"] == status
    assert stored["recovery_path"] == str(tmp_path / "run.json")


@pytest.mark.parametrize(
    "field, value, reason",
    [("sum", 0, "beam SUM"), ("stale", True, "fresh, paired"), ("skew", 0.02, "fresh, paired")],
)
def test_preflight_rejects_unusable_bpm_without_writes(check_setup, tmp_path, field, value, reason):
    runner, beam = make_check(check_setup, tmp_path)
    setattr(beam, field, value)
    result = runner.run()
    assert result["status"] == "undetermined"
    assert reason in result["reason"]
    assert beam.writes == []


@pytest.mark.parametrize(
    "fault", ["stop", "drop", "sum_change", "phase", "optics", "drift", "readback", "external", "put"]
)
def test_measurement_stops_or_invalidates_and_restores_its_settings(check_setup, tmp_path, fault):
    runner, beam = make_check(check_setup, tmp_path)
    config = runner.config

    def after_write(pv, value):
        if pv != config.amplitude.setpoint:
            return
        if value == 49.0:
            if fault == "stop":
                runner.stop()
            elif fault == "drop":
                beam.sum = 0
            elif fault == "sum_change":
                beam.sum = 5
            elif fault == "phase":
                beam.values[config.phase_readback_pv] += 1
            elif fault == "optics":
                beam.values[config.conditions[0].pv] += 2
            elif fault == "readback":
                beam.current_readback = lambda: 0.2
            elif fault == "external":
                beam.values[config.amplitude.setpoint] = 51.0
            elif fault == "put":
                raise RuntimeError("Write acknowledgement lost.")
        elif fault == "drift":
            beam.nominal_response += 0.25

    beam.on_write = after_write
    result = runner.run()
    assert result["status"] == "undetermined"
    assert result["restore_status"] == ("failed" if fault == "external" else "verified")
    assert bool(result["restore_errors"]) == (fault == "external")
    assert beam.values[config.amplitude.setpoint] == (51.0 if fault == "external" else 50.0)
    assert beam.values[config.corrector.setpoint] == 0.2
    if fault == "external":
        assert (config.amplitude.setpoint, 50.0) not in beam.writes


def test_restore_failure_is_logged_even_if_the_other_actuator_can_restore(check_setup, tmp_path):
    runner, beam = make_check(check_setup, tmp_path)
    config = runner.config

    def after_write(pv, value):
        if pv == config.amplitude.setpoint and value == 49.0:
            runner.stop()
        if pv == config.amplitude.setpoint and value == 50.0:
            beam.values[pv] = 49.0
            raise RuntimeError("Amplitude restore write failed.")

    beam.on_write = after_write
    result = runner.run()
    assert result["status"] == "undetermined"
    assert result["restore_status"] == "failed"
    assert result["restore_errors"] == {"LLRF02": "Amplitude restore write failed."}
    assert beam.values[config.corrector.setpoint] == 0.2


def test_offscreen_check_uses_highlighted_row_and_invalidates_on_phase_change(
    check_setup, tmp_path, monkeypatch
):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("HALF_LINAC_MACHINE_ID", "half")
    monkeypatch.setenv("HALF_LINAC_CONTROL_BACKEND", "vm")
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QApplication, QMessageBox
    from half_linac.src.apps.rf_phase_scan.main import RFPhaseScanWindow
    from half_linac.src.apps.rf_phase_scan import transmission_recovery_ui

    app = QApplication.instance() or QApplication([])
    window = RFPhaseScanWindow()
    page = window.recovery_page
    runner, beam = make_check(check_setup, tmp_path)
    page.write_enabled = True
    page.check_configs = {"LLRF02": runner.config}
    page.check_unavailable.pop("LLRF02")
    page.last_result = {
        "json_path": str(tmp_path / "run.json"),
        "initial_snapshot": {},
        "items": [{"rf_element": "LLRF02", "status": "applied", "applied_phase_deg": 12.0}],
    }
    page.select_stations(["LLRF03"])

    def build_check(**options):
        options.update(
            context=runner.context, client_factory=lambda: beam,
            lock_factory=lambda *_: nullcontext(), monotonic=lambda: beam.now, sleep=beam.wait,
        )
        return AccelerationCheck(**options)

    monitors = []

    def watch(pv, **options):
        monitor = SimpleNamespace(callback=options["callback"], disconnect=lambda: None)
        monitors.append(monitor)
        return monitor

    monkeypatch.setattr(transmission_recovery_ui, "AccelerationCheck", build_check)
    monkeypatch.setattr("epics.PV", watch)
    page.table.selectRow(1)
    assert not page.check_button.isEnabled()
    page.table.selectRow(0)
    assert page.check_button.isEnabled()
    page.check_button.click()
    assert not window.tuning_tabs.isTabEnabled(1)
    assert not window.tuning_tabs.isTabEnabled(2)
    for _ in range(100):
        if page.check_worker is None:
            break
        QTest.qWait(5)
    assert page.check_worker is None
    assert window.tuning_tabs.isTabEnabled(1)
    item = page.table.item(0, page.ACCELERATION_COLUMN)
    assert item.text() == "Accelerating"
    monitors[0].callback(value=14.0, severity=0)
    app.processEvents()
    assert item.text() == "Undetermined"
    assert "phase changed" in item.toolTip()
    assert len(list(tmp_path.glob("acceleration_check_*.json"))) == 1

    page.check_button.click()
    for _ in range(100):
        if page.check_worker is None:
            break
        QTest.qWait(5)
    assert item.text() == "Accelerating"
    page.last_stages = (page.candidates[0],)
    page.last_settings = page.default_settings
    page.selected_rows = [0]
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.Yes)
    monkeypatch.setattr(
        transmission_recovery_ui, "restore_recovery_snapshot", lambda **_: {"LLRF02": 10.0}
    )
    page._restore_snapshot()
    assert item.text() == "Undetermined"
    for _ in range(100):
        if page.restore_worker is None:
            break
        QTest.qWait(5)
    assert page.restore_worker is None
    assert not page.check_button.isEnabled()
    assert len(list(tmp_path.glob("acceleration_check_*.json"))) == 2
    window.close()
