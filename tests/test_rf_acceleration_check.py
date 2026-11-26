from contextlib import nullcontext
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from half_linac.src.apps.rf_phase_scan.acceleration_check import (
    AccelerationCheck,
    discover_acceleration_checks,
    resolve_acceleration_capture,
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
        "conditions": recovery["stages"][0]["acceleration_check"]["conditions"],
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
            config.conditions[0].pv: -2.7,
        }
        self.lower_response = lower_response
        self.nominal_response = 1.0
        self.sum = 10.0
        self.stale = False
        self.power_stale = False
        self.power = lambda amplitude: 7.0 if amplitude == 50.0 else 6.5
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
        if pv == config.kly_forward_power_pv:
            return AmpReading(self.power(amplitude), 1.0 if self.power_stale else self.now)
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


def make_check(check_setup, tmp_path, lower_response=2.0, *, k1_window=None):
    context, _, stages, settings, spec, config = check_setup
    if k1_window is not None:
        conditions = [{**spec["conditions"][0], "window": k1_window}]
        config = resolve_acceleration_check(
            context, stages["LLRF02"], settings, {**spec, "conditions": conditions}
        )
    beam = SimulatedBeam(config, lower_response)
    runner = AccelerationCheck(
        context=context, config=config, applied_phase=12.0,
        recovery_path=tmp_path / "run.json", path=tmp_path / "acceleration_check.json",
        on_state=lambda _: None, client_factory=lambda: beam,
        lock_factory=lambda *_: nullcontext(), monotonic=lambda: beam.now, sleep=beam.wait,
    )
    return runner, beam


def make_capture(check_setup, tmp_path, *, amplitude_step=1.0):
    context, recovery, stages, settings, _, _ = check_setup
    config = resolve_acceleration_capture(
        context, stages["LLRF02"], settings, recovery["stages"][0]["acceleration_check"],
        amplitude_step=amplitude_step, current_step=0.1,
    )
    beam = SimulatedBeam(config)
    runner = AccelerationCheck(
        context=context, config=config, applied_phase=None,
        recovery_path=None, path=tmp_path / "run.json",
        on_state=lambda _: None, client_factory=lambda: beam,
        lock_factory=lambda *_: nullcontext(), monotonic=lambda: beam.now, sleep=beam.wait,
    )
    return runner, beam


def test_real_capture_collects_independent_samples_without_a_recovery_run(check_setup, tmp_path):
    runner, beam = make_capture(check_setup, tmp_path)
    beam.skew = 0.02
    beam.sum = 0.0
    result = runner.run()
    assert result["status"] == "undetermined"
    assert result["capture_completed"]
    assert result["restore_status"] == "verified"
    assert result["measured_phase_deg"] == 12.0
    assert result["recovery_path"] is None
    stored = json.loads(runner.path.read_text())
    assert stored["mode"] == "capture"
    assert len(stored["groups"]) == 3
    assert stored["configuration"]["kly_forward_power_pv"] == "IN:MW:LLRF02:GET_CH2_POWER"
    assert stored["kly_forward_power_check"] == "lower_at_low_amp"
    assert [group["kly_forward_power"]["median"] for group in stored["groups"]] == [7.0, 6.5, 7.0]
    for group in stored["groups"]:
        power = group["kly_forward_power"]
        assert len(power["samples"]) == runner.config.samples_per_state
        assert all(sample["timestamp"] > power["after_timestamp"] for sample in power["samples"])
    samples = stored["groups"][0]["points"][0]["samples"]
    assert len(samples["x"]) == len(samples["sum"]) == runner.config.samples_per_state
    assert samples["sum"][0]["timestamp"] - samples["x"][0]["timestamp"] == pytest.approx(0.02)
    assert samples["sum"][0]["value"] == 0.0
    assert beam.values[runner.config.amplitude.setpoint] == 50.0
    assert beam.values[runner.config.corrector.setpoint] == 0.2


@pytest.mark.parametrize(
    "power_stale, low_power, expected",
    [(True, 6.5, "insufficient_samples"), (False, 7.0, "overlap"), (False, 7.5, "higher_at_low_amp")],
)
def test_power_comparison_reports_direction_without_changing_capture_result(
    check_setup, tmp_path, power_stale, low_power, expected
):
    runner, beam = make_capture(check_setup, tmp_path)
    beam.power_stale = power_stale
    beam.power = lambda amplitude: 7.0 if amplitude == 50.0 else low_power
    result = runner.run()
    assert result["capture_completed"]
    assert result["status"] == "undetermined"
    assert result["kly_forward_power_check"] == expected


def test_capture_rejects_a_step_outside_machine_limits_before_writing(check_setup, tmp_path):
    runner, beam = make_capture(check_setup, tmp_path, amplitude_step=60)
    result = runner.run()
    assert not result["capture_completed"]
    assert "perturbation exceeds" in result["reason"]
    assert beam.writes == []


def test_cancelled_capture_restores_original_amplitude(check_setup, tmp_path):
    runner, beam = make_capture(check_setup, tmp_path)
    config = runner.config

    def stop_after_amplitude_write(pv, value):
        if pv == config.amplitude.setpoint and value == 49.0:
            runner.stop()

    beam.on_write = stop_after_amplitude_write
    result = runner.run()
    assert not result["capture_completed"]
    assert result["restore_status"] == "verified"
    assert beam.values[config.amplitude.setpoint] == 50.0
    assert beam.values[config.corrector.setpoint] == 0.2


def test_half_has_no_enabled_checks_and_bad_paths_keep_sum_recovery(check_setup):
    context, recovery, stages, settings, spec, config = check_setup
    configs, unavailable = discover_acceleration_checks(context, recovery, stages, settings)
    assert configs == {}
    assert "not been enabled" in unavailable["LLRF02"]
    assert config.conditions[0].pv == "IN:MG:L002:QUAD:QL16:K1:TOTAL"
    assert config.conditions[0].limits is None
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
    assert stored["condition_readbacks"]
    assert all(
        (reading["element"], reading["channel"], reading["value"]) == ("QL16", "K1_total", -2.7)
        for reading in stored["condition_readbacks"]
    )


def test_k1_changes_are_recorded_without_a_window(check_setup, tmp_path):
    runner, beam = make_check(check_setup, tmp_path)
    config = runner.config

    def after_write(pv, value):
        if pv == config.amplitude.setpoint:
            beam.values[config.conditions[0].pv] = -2.6 if value == 49.0 else -2.7

    beam.on_write = after_write
    result = runner.run()
    assert result["status"] == "accelerating"
    assert {reading["value"] for reading in result["condition_readbacks"]} == {-2.7, -2.6}


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
    window = (-3, -2) if fault == "optics" else None
    runner, beam = make_check(check_setup, tmp_path, k1_window=window)
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
    if fault == "optics":
        assert "outside the calibrated range" in result["reason"]
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
    window.resize(1280, 720)
    window.show()
    app.processEvents()
    page = window.recovery_page
    assert page.check_button.isVisible()
    assert page.check_status.isVisible()
    parameters = page.sample_interval.parentWidget()
    assert page.check_button.parentWidget() is parameters
    fields = parameters.layout()
    sample_row, sample_column, _, _ = fields.getItemPosition(fields.indexOf(page.sample_interval))
    joint_row, joint_column, _, _ = fields.getItemPosition(fields.indexOf(page.use_acceleration))
    check_row, check_column, _, _ = fields.getItemPosition(fields.indexOf(page.check_button))
    assert (joint_row, joint_column) == (sample_row, sample_column + 1)
    assert (check_row, check_column) == (sample_row, sample_column + 2)
    assert page.check_status.mapTo(window, page.check_status.rect().bottomRight()).y() < 720
    assert not page.check_button.isEnabled()
    assert page.check_status.text() == "No calibrated path"
    page.table.selectRow(0)
    assert page.check_status.text() == "Path unavailable"
    assert "LLRF02" in page.check_status.toolTip()
    assert "calibrated" in page.check_status.toolTip()

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
    assert page.check_status.text() == "Ready"
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


def test_offscreen_real_capture_entry_without_sum_scan(check_setup, tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("HALF_LINAC_MACHINE_ID", "half")
    monkeypatch.setenv("HALF_LINAC_CONTROL_BACKEND", "vm")
    from PyQt5.QtCore import QTimer
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QApplication, QDialogButtonBox, QDoubleSpinBox
    from half_linac.src.apps.rf_phase_scan.main import RFPhaseScanWindow
    from half_linac.src.apps.rf_phase_scan import transmission_recovery_ui

    app = QApplication.instance() or QApplication([])
    window = RFPhaseScanWindow()
    window.resize(1280, 900)
    window.show()
    app.processEvents()
    page = window.recovery_page
    runner, beam = make_capture(check_setup, tmp_path)
    assert page.capture_button.isVisible()
    assert page.capture_button.mapTo(window, page.capture_button.rect().bottomRight()).x() < 1280
    page.table.selectRow(0)
    assert not page.capture_button.isEnabled()

    page.context = runner.context
    page.write_enabled = True
    page.runtime_paths["runs_dir"] = tmp_path
    page.table.selectRow(1)
    assert not page.capture_button.isEnabled()
    page.table.selectRow(0)
    assert page.last_result is None
    assert page.capture_button.isEnabled()
    assert not page.check_button.isEnabled()

    def build_check(**options):
        options.update(
            client_factory=lambda: beam, lock_factory=lambda *_: nullcontext(),
            monotonic=lambda: beam.now, sleep=beam.wait,
        )
        return AccelerationCheck(**options)

    dialog_state = []

    def enter_steps():
        dialog = app.activeModalWidget()
        fields = dialog.findChildren(QDoubleSpinBox)
        start = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok)
        dialog_state.append(start.isEnabled())
        fields[0].setValue(1.0)
        fields[1].setValue(0.1)
        dialog_state.append(start.isEnabled())
        start.click()

    monkeypatch.setattr(transmission_recovery_ui, "AccelerationCheck", build_check)
    QTimer.singleShot(0, enter_steps)
    page.capture_button.click()
    assert dialog_state == [False, True]
    for _ in range(100):
        if page.check_worker is None:
            break
        QTest.qWait(5)
    assert page.check_worker is None
    assert "Test data saved" in page.summary.text()
    assert "KLY forward power fell at lower AMP" in page.summary.text()
    assert page.table.item(0, page.ACCELERATION_COLUMN).text() == "Undetermined"
    records = list(tmp_path.glob("acceleration_capture_*/run.json"))
    assert len(records) == 1
    assert json.loads(records[0].read_text())["capture_completed"]
    window.close()
