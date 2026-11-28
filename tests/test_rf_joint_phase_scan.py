import json
import math
from dataclasses import replace
from threading import Lock
from types import SimpleNamespace

import pytest

from half_linac.src.apps.rf_phase_scan.amp_scan import AmpReading
from half_linac.src.apps.rf_phase_scan.joint_phase_scan import (
    PhaseResponseMeasurement,
    resolve_joint_phase_scan,
)
from half_linac.src.apps.rf_phase_scan.transmission_recovery import (
    TransmissionRecoveryRunner,
    discover_recovery_stages,
    settings_from_config,
)
from half_linac.src.shared.machine_profile import get_workflow, load_app_context


@pytest.fixture
def joint_setup():
    context = load_app_context("rf_phase_scan", machine_id="half", control_backend="real")
    workflow = get_workflow(context.profile, "rf_phase_scan")
    recovery = workflow["transmission_recovery"]
    stages = discover_recovery_stages(context, recovery)
    settings = replace(
        settings_from_config(recovery),
        half_range_deg=180,
        settle_time_s=0,
        samples_per_point=2,
        sample_interval_s=0,
    )
    route = resolve_joint_phase_scan(
        context, stages[0], settings, recovery["stages"][0]["acceleration_check"],
    )
    return context, workflow, stages, settings, route


class PhaseScanBeam:
    def __init__(self, stage, route):
        self.stage, self.route = stage, route
        self.phase = 170.0
        self.current = 0.14
        self.pulse = 0
        self.phase_commands = []
        self.current_commands = []
        self.on_current_write = lambda: None
        self.signal = lambda phase: 70000 if phase == 20 else 50000
        self.orbit_sum = lambda _phase, _current: 33000
        self.slope = lambda phase: 2800 / (1 + 0.35 * math.cos(math.radians(phase + 160)))
        self.forward_power = 65.5
        self.closed = False

    def read_scan(self, pv):
        if pv == self.stage.signal_pv:
            return self.signal(self.phase)
        assert pv in (self.stage.phase_pv, self.stage.phase_readback_pv)
        return self.phase

    def write_scan(self, pv, value, **_options):
        assert pv == self.stage.phase_pv
        self.phase = value
        self.phase_commands.append(value)
        return True

    def read(self, pv, timeout=0.5):
        config = self.route.response
        if pv == config.x_pv:
            self.pulse += 1
            x = self.phase * 40 + self.current * self.slope(self.phase)
            return AmpReading(x + 0.1 * (-1) ** self.pulse, float(self.pulse))
        if pv == config.sum_pv:
            return AmpReading(self.orbit_sum(self.phase, self.current), self.pulse + 0.0001)
        values = {
            config.phase_pv: self.phase,
            config.phase_readback_pv: self.phase,
            config.amplitude.setpoint: 87.5,
            config.amplitude.readback: 87.497,
            config.corrector.setpoint: self.current,
            config.corrector.readback: self.current,
            config.conditions[0].pv: -2.7391,
            self.route.optics_current_pvs[0]: -30.0,
            self.route.forward_power_pv: self.forward_power,
        }
        return AmpReading(values[pv], None)

    def write(self, pv, value):
        assert pv == self.route.response.corrector.setpoint
        self.current_commands.append(value)
        self.current = value
        self.on_current_write()

    def close(self):
        self.closed = True


def make_runner(joint_setup, tmp_path, *, enabled=True):
    context, _, stages, settings, route = joint_setup
    route = route.with_corrector_step(route.response.corrector.step, settle_time_s=0)
    beam = PhaseScanBeam(stages[0], route)
    runner = TransmissionRecoveryRunner(
        context=context, candidates=stages, stages=stages[:1], settings=settings,
        directory=tmp_path, read=beam.read_scan, write=beam.write_scan,
        response_routes={"LLRF02": route} if enabled else None,
        response_client_factory=lambda: beam, lock_factory=lambda *_: Lock(),
        sleeper=lambda _: None,
    )
    return runner, beam


@pytest.mark.parametrize("readback_delay", [3.0, 6.0])
def test_corrector_readback_timeout_and_settle_before_sampling(joint_setup, readback_delay):
    context, _, stages, _, route = joint_setup
    route = route.with_corrector_step(route.response.corrector.step, settle_time_s=1.5)
    beam = PhaseScanBeam(stages[0], route)
    initial_current = beam.current
    read = beam.read
    now = 0.0
    write_times = []
    sample_delays = []

    def sleep(delay):
        nonlocal now
        now += delay

    def on_write():
        write_times.append(now)

    def read_delayed(pv, timeout=0.5):
        if beam.current != initial_current:
            elapsed = now - write_times[-1]
            if pv == route.response.corrector.readback and elapsed < readback_delay:
                return AmpReading(beam.current + 0.02, None)
            if pv == route.response.x_pv:
                sample_delays.append(elapsed)
        return read(pv, timeout)

    beam.read = read_delayed
    beam.on_current_write = on_write
    measurement = PhaseResponseMeasurement(
        context=context, route=route, on_state=lambda _: None, check_cancelled=lambda: None,
        client_factory=lambda: beam, monotonic=lambda: now, sleep=sleep,
    )
    record = {}
    with measurement.open():
        if readback_delay > 5.0:
            with pytest.raises(RuntimeError, match="XC10: readback did not reach"):
                measurement.measure(beam.phase, record)
            assert write_times[1] - write_times[0] == pytest.approx(5.0)
            assert not sample_delays
        else:
            measurement.measure(beam.phase, record)
            assert record["status"] == "valid"
            assert min(sample_delays) == pytest.approx(4.5, abs=0.06)
    assert beam.current == initial_current
    assert record["restore_status"] == "verified"


@pytest.mark.parametrize("enabled, applied", [(True, -160.0), (False, 20.0)])
def test_scan_with_and_without_acceleration(joint_setup, tmp_path, enabled, applied):
    runner, beam = make_runner(joint_setup, tmp_path, enabled=enabled)
    result = runner.run()
    assert result["status"] == "completed", result["error"]
    record = result["items"][0]
    assert record["peak_phase_deg"] == 20.0
    assert record["applied_phase_deg"] == beam.phase == applied
    assert beam.current == 0.14
    if enabled:
        by_phase = {point["command_phase_deg"]: point for point in record["points"]}
        assert by_phase[20.0]["acceleration"] == "decelerating"
        assert by_phase[-160.0]["acceleration"] == "accelerating"
        assert by_phase[-70.0]["acceleration"] == "undetermined"
        assert record["selected_sum"] == 50000
        assert record["response_reference_repeat"]["status"] == "valid"
        assert beam.current_commands == pytest.approx([-0.06, 0.34, 0.14] * 14)
        assert beam.closed
        logged = json.loads((tmp_path / "run.json").read_text())
        assert logged["configuration"]["selection_mode"] == "acceleration_and_sum"
        assert logged["items"][0]["selected_phase_deg"] == applied
    else:
        assert not beam.current_commands
        assert not beam.closed
        assert "acceleration_analysis" not in record


def test_joint_scan_uses_high_sum_within_the_accelerating_region(joint_setup, tmp_path):
    runner, beam = make_runner(joint_setup, tmp_path)
    beam.signal = lambda phase: {-160.0: 45000, 170.0: 50000, -130.0: 49000}.get(phase, 40000)
    record = runner.run()["items"][0]
    assert record["acceleration_analysis"]["selection_minimum_sum"] == 47500
    assert record["applied_phase_deg"] == 170.0
    assert record["selected_sum"] == 50000


@pytest.mark.parametrize("unusable", ["beam_loss", "flat_response", "low_accelerating_sum"])
def test_joint_scan_restores_initial_phase_without_a_candidate(joint_setup, tmp_path, unusable):
    runner, beam = make_runner(joint_setup, tmp_path)
    if unusable == "beam_loss":
        beam.orbit_sum = lambda _phase, current: 33000 if current == 0.14 else 19000
    elif unusable == "flat_response":
        beam.slope = lambda _phase: 2800.0
    else:
        beam.phase = 20.0
        beam.signal = lambda phase: 50000 if abs(phase - 20) <= 60 else 1000
    initial = beam.phase
    result = runner.run()
    assert result["status"] == "undetermined", result["error"]
    record = result["items"][0]
    assert record["acceleration_analysis"]["reason"]
    assert "applied_phase_deg" not in record
    assert beam.phase == initial
    assert beam.current == 0.14
    assert beam.closed
    if unusable == "beam_loss":
        assert all(point["response"]["status"] == "invalid" for point in record["points"])
        assert all("stability" in point["response"]["reason"] for point in record["points"])
    elif unusable == "low_accelerating_sum":
        assert record["initial_phase_acceleration"] == "decelerating"


@pytest.mark.parametrize("interrupt", ["stop", "power", "external_phase"])
def test_joint_scan_restores_corrector_on_interruption(joint_setup, tmp_path, interrupt):
    runner, beam = make_runner(joint_setup, tmp_path)

    def on_current_write():
        if beam.current != 0.14:
            if interrupt == "stop":
                runner.stop()
            elif interrupt == "power":
                beam.forward_power = 30.0
            else:
                beam.phase = 11.0

    beam.on_current_write = on_current_write
    result = runner.run()
    assert result["status"] == ("stopped" if interrupt == "stop" else "failed")
    assert beam.current == 0.14
    assert beam.closed
    if interrupt == "external_phase":
        assert "phase setpoint changed outside" in result["error"]
        assert beam.phase == 11.0
    else:
        assert beam.phase == 170.0


def test_gui_checkbox_switches_supported_stations(joint_setup, tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication
    from half_linac.src.apps.rf_phase_scan.transmission_recovery_ui import TransmissionRecoveryPage

    app = QApplication.instance() or QApplication([])
    context, workflow, stages, _, _ = joint_setup
    page = TransmissionRecoveryPage(
        window=SimpleNamespace(), context=context, workflow=workflow,
        write_enabled=True, runtime_paths={"runs_dir": tmp_path},
    )
    assert page.use_acceleration.isEnabled()
    assert not page.use_acceleration.isChecked()
    assert page._selected_rows() == list(range(len(stages)))
    page.use_acceleration.setChecked(True)
    assert page._selected_rows() == [0]
    assert page.stage_checkboxes[0].isEnabled()
    assert all(not selector.isEnabled() for selector in page.stage_checkboxes[1:])
    page.use_acceleration.setChecked(False)
    assert page._selected_rows() == list(range(len(stages)))

    runner, _ = make_runner(joint_setup, tmp_path)
    record = runner.run()["items"][0]
    page.selected_rows = [0]
    page._item(0, {**record, "status": "scanning", "acceleration_analysis": None})
    page._item(0, record)
    assert page.table.item(0, page.ACCELERATION_COLUMN).text() == "Accelerating"
    assert page.table.item(0, 4).text() == "20.00°"
    assert page.table.item(0, 6).text() == "-160.00°"
    labels = [label.get_text() for label in page.plot.axes.get_legend().get_texts()]
    assert labels == ["Accelerating", "Decelerating", "Undetermined", "Applied phase"]
    page.close()
    app.processEvents()


def test_gui_displays_corrector_response_for_joint_scan_results(joint_setup, tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication
    from half_linac.src.apps.rf_phase_scan import transmission_recovery_ui as ui

    app = QApplication.instance() or QApplication([])
    context, workflow, _, _, _ = joint_setup
    window = SimpleNamespace(
        _amp_busy=lambda: False, _set_recovery_running=lambda _running: None,
        thread=None, batch_dialog=None,
    )
    page = ui.TransmissionRecoveryPage(
        window=window, context=context, workflow=workflow,
        write_enabled=True, runtime_paths={"runs_dir": tmp_path},
    )
    signal = SimpleNamespace(connect=lambda _callback: None)
    monkeypatch.setattr(
        ui, "TransmissionRecoveryThread",
        lambda _factory: SimpleNamespace(
            item=signal, point=signal, state=signal, done=signal,
            start=lambda: None, deleteLater=lambda: None,
        ),
    )
    monkeypatch.setattr(page, "_confirm_joint_scan", lambda *_: ({"LLRF02": 0.2}, 1.0))
    monkeypatch.setattr(ui.QMessageBox, "question", lambda *_: ui.QMessageBox.Yes)

    page.use_acceleration.setChecked(True)
    assert len(page.plot.fig.axes) == 1
    page.start_button.click()
    sum_axes, response_axes = page.plot.fig.axes
    assert sum_axes.get_shared_x_axes().joined(sum_axes, response_axes)

    record = {"rf_element": "LLRF02", "target_bpm": "BPM08", "status": "scanning"}
    page._item(0, record)
    valid = {
        "acquisition_index": 0, "offset_deg": 0, "median_sum": 50_000,
        "response": {"status": "valid", "magnitude": 7_500, "magnitude_interval": [7_200, 7_800]},
    }
    invalid = {
        "acquisition_index": 1, "offset_deg": 30, "median_sum": 45_000,
        "response": {"status": "invalid", "reason": "BPM10 SUM below minimum"},
    }
    page._point(0, valid)
    page._point(0, invalid)
    intervals, dots = response_axes.collections
    assert response_axes.get_title(loc="left") == "XC10 → BPM10"
    assert response_axes.get_xlabel() == "Phase offset (deg)"
    assert list(sum_axes.lines[0].get_xdata()) == [0, 30]
    assert dots.get_offsets().tolist() == [[0, 7_500]]
    assert intervals.get_segments()[0].tolist() == [[0, 7_200], [0, 7_800]]

    final_response = dict(valid["response"], comparison_interval=[6_900, 8_100])
    final = dict(
        record, status="undetermined", points=[dict(valid, response=final_response), invalid],
        acceleration_analysis={"status": "undetermined", "reason": "Overlapping response intervals"},
    )
    page._item(0, final)
    page._done({"status": "undetermined", "items": [final]})
    assert response_axes.collections[0].get_segments()[0].tolist() == [[0, 6_900], [0, 8_100]]
    page.use_acceleration.setChecked(False)
    assert page.plot.fig.axes == [sum_axes, response_axes]
    page.start_button.click()
    assert page.plot.fig.axes == [page.plot.axes]
    page.close()
    app.processEvents()


@pytest.mark.parametrize(
    ("current_step", "expected_tolerance", "settle_time_s"),
    [(0.08, 0.005, 1.0), (0.016, 0.004, 2.5)],
)
def test_gui_corrector_step_reaches_runner_and_capture(
    joint_setup, tmp_path, monkeypatch, current_step, expected_tolerance, settle_time_s,
):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication, QDialogButtonBox, QDoubleSpinBox
    from half_linac.src.apps.rf_phase_scan import transmission_recovery_ui as ui

    app = QApplication.instance() or QApplication([])
    context, workflow, stages, _, _ = joint_setup
    window = SimpleNamespace(
        _amp_busy=lambda: False, _set_recovery_running=lambda _running: None,
        thread=None, batch_dialog=None,
    )
    page = ui.TransmissionRecoveryPage(
        window=window, context=context, workflow=workflow,
        write_enabled=True, runtime_paths={"runs_dir": tmp_path},
    )
    page.use_acceleration.setChecked(True)
    page.settle.setValue(0.25)
    signal = SimpleNamespace(connect=lambda _callback: None)
    launched = []

    def worker_factory(factory):
        launched.append(factory(None))
        return SimpleNamespace(item=signal, point=signal, state=signal, done=signal, start=lambda: None)

    monkeypatch.setattr(ui, "TransmissionRecoveryThread", worker_factory)

    def choose_step():
        dialog = app.activeModalWidget()
        step, settle = dialog.findChildren(QDoubleSpinBox)
        assert step.value() == pytest.approx(0.2)
        assert settle.value() == pytest.approx(1.0)
        step.setValue(current_step)
        settle.setValue(settle_time_s)
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()

    QTimer.singleShot(0, choose_step)
    page.start_button.click()
    route = launched[0].response_routes["LLRF02"]
    assert route.response.corrector.step == pytest.approx(current_step)
    assert route.response.corrector.tolerance == pytest.approx(expected_tolerance)
    assert route.response.amplitude.step == 0
    config = launched[0]._payload()["configuration"]
    response = config["phase_response_routes"]["LLRF02"]["response"]
    assert response["corrector"]["step"] == pytest.approx(current_step)
    assert response["settle_time_s"] == pytest.approx(settle_time_s)
    assert response["corrector_readback_timeout_s"] == pytest.approx(5.0)
    assert config["settings"]["settle_time_s"] == pytest.approx(0.25)
    assert config["settings"]["readback_timeout_s"] == pytest.approx(2.0)

    def choose_capture_steps():
        dialog = app.activeModalWidget()
        amp, current = dialog.findChildren(QDoubleSpinBox)
        assert amp.value() == 0
        assert current.value() == pytest.approx(current_step)
        amp.setValue(1.5)
        current.setValue(0.3)
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()

    page.worker = None
    page._set_running(False)
    QTimer.singleShot(0, choose_capture_steps)
    assert page._capture_steps(stages[0], page.capture_specs["LLRF02"]) == pytest.approx((1.5, 0.3))
    page.close()
    app.processEvents()
