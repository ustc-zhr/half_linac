from dataclasses import replace

import numpy as np
import pytest

from half_linac.src.apps.dispersion_correction.config import load_config, validate_config
from half_linac.src.apps.dispersion_correction.machine.offline import OfflineMachine
from half_linac.src.apps.dispersion_correction.models import KnobConfig
from half_linac.src.apps.dispersion_correction.reports import result_to_csv, result_to_dict, result_to_json, result_to_markdown
from half_linac.src.apps.dispersion_correction.workflow import AchromatWorkflow


CONFIG_PATH = "tests/dispersion_correction/fixtures/achromat_mvp.example.json"
IRFEL_MOCK_CONFIG_PATH = "tests/dispersion_correction/fixtures/irfel_achromat.mock.json"


class CountingWorkflow(AchromatWorkflow):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.response_build_count = 0

    def build_response_matrix(self, knob_set=None):
        self.response_build_count += 1
        return super().build_response_matrix(knob_set)


class RecordingUnsafeMachine(OfflineMachine):
    def __init__(self, config) -> None:
        super().__init__(config)
        self.events = []
        self.unsafe = True

    def set_energy_delta(self, value: float) -> None:
        self.events.append(("energy", float(value)))
        super().set_energy_delta(value)

    def restore(self, snapshot) -> None:
        self.events.append(("restore", snapshot.energy_delta))
        super().restore(snapshot)


class SplitEnergyBaselineMachine(OfflineMachine):
    def __init__(self, config) -> None:
        super().__init__(config)
        self._energy_delta = 0.01
        self.energy_targets = []

    def get_energy_delta(self) -> float:
        return 0.0099

    def set_energy_delta(self, value: float) -> None:
        self.energy_targets.append(float(value))
        super().set_energy_delta(value)


def test_dispersion_scan_centers_and_restores_on_energy_setpoint() -> None:
    config = load_config(CONFIG_PATH)
    machine = SplitEnergyBaselineMachine(config)

    AchromatWorkflow(config, machine=machine).measure_dispersion(samples=1)

    delta = config.energy_knob.delta
    assert machine.energy_targets == pytest.approx(
        [0.01 + delta, 0.01 - delta, 0.01]
    )


def test_offline_workflow_keeps_accepted_improvements() -> None:
    config = load_config(CONFIG_PATH)
    result = AchromatWorkflow(config).run()

    assert result.success
    assert result.improvement > 1.0
    assert result.final.rms_mm < result.initial.rms_mm
    assert any(step.accepted for step in result.steps)
    assert all(step.measurement_before is not None for step in result.steps)
    accepted = [step for step in result.steps if step.accepted]
    assert all(step.measurement_after is not None for step in accepted)
    assert all(step.knobs_before is not None for step in result.steps)
    assert all(step.knobs_trial is not None for step in accepted)


def test_automatic_workflow_does_not_require_an_overall_improvement_target() -> None:
    config = load_config(CONFIG_PATH)
    config = replace(
        config,
        solver=replace(config.solver, gain=0.1, max_iter=1),
    )

    result = AchromatWorkflow(config).run()

    assert result.success
    assert any(step.accepted for step in result.steps)
    assert 1.0 < result.improvement < 2.0
    assert result.final_knobs != result.initial_knobs


def test_automatic_workflow_streams_completed_generation_measurements() -> None:
    config = load_config(CONFIG_PATH)
    updates = []
    result = AchromatWorkflow(
        config,
        correction_measurement_callback=(
            lambda iteration, total, state, measurement: updates.append(
                (iteration, total, state, measurement)
            )
        ),
    ).run()

    assert updates[0][2] == "initial"
    assert updates[0][3] is result.initial
    assert updates[-1][2] == "final"
    generation_updates = [
        update for update in updates if update[2] in {"accepted", "rejected"}
    ]
    assert len(generation_updates) == sum(
        step.measurement_after is not None for step in result.steps
    )
    assert [update[0] for update in generation_updates] == [
        step.iteration
        for step in result.steps
        if step.measurement_after is not None
    ]


def test_workflow_reports_structured_progress() -> None:
    config = load_config(CONFIG_PATH)
    updates = []
    workflow = AchromatWorkflow(
        config,
        progress_callback=lambda stage, current, total: updates.append((stage, current, total)),
    )

    workflow.measure_dispersion()

    assert updates[0] == ("Setting +Δp/p", 0, 5)
    assert updates[-1] == ("Measurement complete", 5, 5)


def test_response_progress_does_not_reset_for_nested_measurements() -> None:
    config = load_config(CONFIG_PATH)
    updates = []
    workflow = AchromatWorkflow(
        config,
        progress_callback=lambda stage, current, total: updates.append((stage, current, total)),
    )

    workflow.build_response_matrix()

    assert updates[0][0] == "Measuring baseline"
    assert updates[-1] == ("Response complete", 5, 5)
    assert all("Sampling" not in stage for stage, _, _ in updates)
    assert [current for _, current, _ in updates] == sorted(current for _, current, _ in updates)


def test_bpm_sample_interval_is_applied_only_between_samples(monkeypatch) -> None:
    config = load_config(CONFIG_PATH)
    config = replace(
        config,
        measurement=replace(config.measurement, sample_interval_s=0.25),
    )
    sleeps = []
    monkeypatch.setattr("half_linac.src.apps.dispersion_correction.workflow.time.sleep", sleeps.append)

    AchromatWorkflow(config)._average_bpm(3)

    assert sleeps == [0.25, 0.25]


def test_json_config_runs_workflow() -> None:
    json_config = load_config(CONFIG_PATH)

    result = AchromatWorkflow(json_config).run()

    assert result.success
    assert result.improvement > 1.0


def test_response_update_once_measures_matrix_only_once() -> None:
    config = load_config(CONFIG_PATH)
    workflow = CountingWorkflow(config)

    result = workflow.run()

    assert result.success
    assert max(step.iteration for step in result.steps) > 1
    assert workflow.response_build_count == 1


def test_response_update_every_iteration_remeasures_matrix() -> None:
    config = load_config(CONFIG_PATH)
    config = replace(config, solver=replace(config.solver, response_update="every_iteration"))
    workflow = CountingWorkflow(config)

    result = workflow.run()

    assert result.success
    assert workflow.response_build_count > 1
    assert workflow.response_build_count == max(step.iteration for step in result.steps)


def test_response_update_rejects_unknown_policy() -> None:
    config = load_config(CONFIG_PATH)
    invalid = replace(config, solver=replace(config.solver, response_update="adaptive"))

    with pytest.raises(ValueError, match="response_update"):
        validate_config(invalid)


def test_workflow_restores_state_when_machine_becomes_unsafe() -> None:
    config = load_config(CONFIG_PATH)
    machine = RecordingUnsafeMachine(config)

    result = AchromatWorkflow(config, machine=machine).run()

    assert not result.success
    assert "unsafe" in result.reason.lower()
    assert machine.get_energy_delta() == 0.0
    assert machine.get_knobs(("Q1_sym", "Q2_sym")) == {"Q1_sym": 0.0, "Q2_sym": 0.0}
    last_restore = max(index for index, event in enumerate(machine.events) if event[0] == "restore")
    assert all(event[0] != "energy" for event in machine.events[last_restore + 1 :])


def test_workflow_stops_without_accepting_zero_response() -> None:
    config = load_config(CONFIG_PATH)
    machine = OfflineMachine(
        config,
        response_matrix=np.zeros((len(config.target_bpms), len(config.knobs))),
    )

    result = AchromatWorkflow(config, machine=machine).run()

    assert not result.success
    assert result.final.rms_mm == result.initial.rms_mm
    assert machine.get_knobs(("Q1_sym", "Q2_sym")) == {"Q1_sym": 0.0, "Q2_sym": 0.0}


def test_workflow_abort_restores_initial_machine_state() -> None:
    config = load_config(CONFIG_PATH)
    machine = OfflineMachine(config)

    def cancel_after_first_knob_change() -> bool:
        return any(abs(value) > 0 for value in machine.get_knobs(("Q1_sym", "Q2_sym")).values())

    result = AchromatWorkflow(
        config,
        machine=machine,
        cancellation_callback=cancel_after_first_knob_change,
    ).run()

    assert not result.success
    assert result.reason == "Aborted; initial state restored"
    assert result.safety.ok
    assert machine.get_energy_delta() == 0.0
    assert machine.get_knobs(("Q1_sym", "Q2_sym")) == {"Q1_sym": 0.0, "Q2_sym": 0.0}


def test_reports_include_bpm_knob_and_safety_sections() -> None:
    result = AchromatWorkflow(load_config(CONFIG_PATH)).run()

    data = result_to_dict(result)
    json_text = result_to_json(result)
    csv_text = result_to_csv(result)
    markdown = result_to_markdown(result)

    assert data["bpm_table"]
    assert "Q1_sym" in data["knob_delta"]
    assert "safety" in data
    assert data["steps"][0]["measurement_before"]["bpm_names"]
    assert data["steps"][0]["measurement_after"]["values_mm"]
    assert data["steps"][0]["knobs_before"]
    assert data["steps"][0]["device_values_before"]
    assert data["steps"][0]["device_values_trial"]
    assert "restored" in data["steps"][0]
    assert "initial_rms_mm" in json_text
    assert "BPM01" in csv_text
    assert "## Knobs" in markdown


def test_irfel_mock_workflow_uses_irfel_names_and_improves() -> None:
    config = load_config(IRFEL_MOCK_CONFIG_PATH)
    machine = OfflineMachine(config)

    assert machine.read_quadrupole_readbacks() == {
        "QM13": 0.820,
        "QM14": -0.615,
        "QM15": -0.608,
        "QM16": 0.817,
    }

    result = AchromatWorkflow(config, machine=machine).run()

    assert result.success
    assert result.initial.bpm_names == ("BPM9", "BPM10")
    assert result.improvement > 1.0
    assert set(result.final_knobs) == {"Q13_Q16_sym", "Q14_Q15_sym"}


def test_offline_workflow_supports_three_knobs() -> None:
    config = load_config(CONFIG_PATH)
    config = replace(
        config,
        knobs=(
            *config.knobs,
            KnobConfig("Q3_sym", {"Q3L": 1.0, "Q3R": 1.0}, 0.002, 0.03),
        ),
    )
    response = np.asarray(
        [
            [-8000.0, -3000.0, 2000.0],
            [-9000.0, -1000.0, 1000.0],
            [-10000.0, 2000.0, -1000.0],
            [-11000.0, 4000.0, -2000.0],
        ]
    )
    machine = OfflineMachine(config, response_matrix=response)

    result = AchromatWorkflow(config, machine=machine).run()

    assert result.success
    assert set(result.final_knobs) == {"Q1_sym", "Q2_sym", "Q3_sym"}
    assert result.improvement > 1.0
