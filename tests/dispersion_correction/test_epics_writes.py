from dataclasses import replace
from copy import deepcopy

import numpy as np
import pytest

from half_linac.src.apps.dispersion_correction.config import load_config
from half_linac.src.apps.dispersion_correction.machine.epics import EpicsMachine
from half_linac.src.apps.dispersion_correction.preflight import run_live_preflight, run_preflight
from half_linac.src.apps.dispersion_correction.recommendation import (
    build_correction_recommendation,
)
from half_linac.src.apps.dispersion_correction.workflow import AchromatWorkflow


ENERGY_PV = "IRFEL:IN-MW:KLY1:SET_PHASE"
QUAD_PVS = {
    "QM13": "IRFEL:PS:QM13:current:ao",
    "QM14": "IRFEL:PS:QM14:current:ao",
    "QM15": "IRFEL:PS:QM15:current:ao",
    "QM16": "IRFEL:PS:QM16:current:ao",
}
QUAD_READBACK_PVS = {
    "QM13": "IRFEL:PS:QM13:current:ai",
    "QM14": "IRFEL:PS:QM14:current:ai",
    "QM15": "IRFEL:PS:QM15:current:ai",
    "QM16": "IRFEL:PS:QM16:current:ai",
}
QUAD_K1_PVS = {name: f"IRFEL:PS:{name}:K1:ao" for name in QUAD_PVS}
READBACK_BY_SET = {QUAD_PVS[name]: QUAD_READBACK_PVS[name] for name in QUAD_PVS}


class FakeEpics:
    def __init__(self, values, fail_pvs=()):
        self.values = dict(values)
        self.fail_pvs = set(fail_pvs)
        self.caput_calls = []

    def caget(self, pv, *args, **kwargs):
        return self.values.get(pv)

    def caput(self, pv, value, *args, **kwargs):
        self.caput_calls.append((pv, float(value)))
        if pv in self.fail_pvs:
            return 0
        self.values[pv] = float(value)
        if pv in READBACK_BY_SET:
            self.values[READBACK_BY_SET[pv]] = float(value)
        return 1


class DynamicFakeEpics(FakeEpics):
    bpm_pvs = {
        "IRFEL-BI:BPM09:BPM_PX2": 0,
        "IRFEL-BI:BPM10:BPM_PX2": 1,
    }
    initial_dispersion = np.asarray([86.0, 112.0])
    response = np.asarray([[-9500.0, -3600.0], [-11800.0, 4200.0]])
    reference_orbit = np.asarray([0.15, -0.1])

    def __init__(self, values):
        super().__init__(values)
        self.initial_phase = float(values[ENERGY_PV])
        self.initial_quads = {name: float(values[pv]) for name, pv in QUAD_READBACK_PVS.items()}

    def caget(self, pv, *args, **kwargs):
        if pv not in self.bpm_pvs:
            return super().caget(pv, *args, **kwargs)
        q1 = 0.5 * (
            self.values[QUAD_READBACK_PVS["QM13"]] - self.initial_quads["QM13"]
            + self.values[QUAD_READBACK_PVS["QM16"]] - self.initial_quads["QM16"]
        )
        q2 = 0.5 * (
            self.values[QUAD_READBACK_PVS["QM14"]] - self.initial_quads["QM14"]
            + self.values[QUAD_READBACK_PVS["QM15"]] - self.initial_quads["QM15"]
        )
        dispersion = self.initial_dispersion + self.response @ np.asarray([q1, q2])
        delta = (self.values[ENERGY_PV] - self.initial_phase) / 2500.0
        index = self.bpm_pvs[pv]
        return float(self.reference_orbit[index] + dispersion[index] * delta)


class EnergyReadbackFakeEpics(FakeEpics):
    def __init__(self, values, set_pv: str, readback_pv: str):
        super().__init__(values)
        self.set_pv = set_pv
        self.readback_pv = readback_pv

    def caput(self, pv, value, *args, **kwargs):
        result = super().caput(pv, value, *args, **kwargs)
        if result and pv == self.set_pv:
            self.values[self.readback_pv] = float(value)
        return result


def write_config():
    config = load_config("tests/dispersion_correction/fixtures/irfel_achromat.json")
    return replace(
        config,
        backend=replace(config.backend, mode="write_enabled"),
        energy_knob=replace(
            config.energy_knob,
            calibration={"kind": "linear_relative", "actuator_per_delta": 2500.0},
        ),
    )


def initial_values():
    values = {
        ENERGY_PV: 12.3,
        QUAD_PVS["QM13"]: 1.3,
        QUAD_PVS["QM14"]: 1.4,
        QUAD_PVS["QM15"]: 1.5,
        QUAD_PVS["QM16"]: 1.6,
    }
    values.update({QUAD_READBACK_PVS[name]: values[QUAD_PVS[name]] for name in QUAD_PVS})
    values.update(
        {
            QUAD_K1_PVS["QM13"]: 0.13,
            QUAD_K1_PVS["QM14"]: 0.14,
            QUAD_K1_PVS["QM15"]: 0.15,
            QUAD_K1_PVS["QM16"]: 0.16,
        }
    )
    return values


def test_phase_energy_delta_is_calibrated_and_verified() -> None:
    epics = FakeEpics(initial_values())
    machine = EpicsMachine(write_config(), epics_client=epics)

    baseline_delta = machine.get_energy_delta()
    machine.set_energy_delta(baseline_delta + 1.0e-4)

    assert baseline_delta == pytest.approx(12.3 / 2500.0)
    assert epics.values[ENERGY_PV] == pytest.approx(12.55)


def test_modulator_voltage_uses_same_normalized_energy_delta_interface() -> None:
    config = write_config()
    options = deepcopy(config.backend.options)
    options["pv_map"]["energy_knob"] = {
        "set": "TEST:MODULATOR:HV",
        "readback": "TEST:MODULATOR:HV",
    }
    config = replace(
        config,
        backend=replace(config.backend, options=options),
        energy_knob=replace(
            config.energy_knob,
            name="MODULATOR_HV",
            actuator="modulator_voltage",
            actuator_unit="kV",
            calibration={
                "kind": "linear_relative",
                "actuator_per_delta": 5000.0,
            },
        ),
    )
    epics = FakeEpics({"TEST:MODULATOR:HV": 20.0})
    machine = EpicsMachine(config, epics_client=epics)

    baseline_delta = machine.get_energy_delta()
    machine.set_energy_delta(baseline_delta + 1.0e-4)

    assert baseline_delta == pytest.approx(20.0 / 5000.0)
    assert epics.values["TEST:MODULATOR:HV"] == pytest.approx(20.5)


def test_snapshot_and_restore_preserve_energy_setpoint_not_initial_readback() -> None:
    set_pv = "TEST:MODULATOR:HV:SET"
    readback_pv = "TEST:MODULATOR:HV:READBACK"
    config = write_config()
    options = deepcopy(config.backend.options)
    options["pv_map"]["energy_knob"] = {
        "set": set_pv,
        "readback": readback_pv,
    }
    config = replace(
        config,
        backend=replace(config.backend, options=options),
        energy_knob=replace(
            config.energy_knob,
            actuator="modulator_voltage",
            actuator_unit="kV",
            calibration={
                "kind": "linear_relative",
                "actuator_per_delta": 5000.0,
            },
        ),
    )
    values = initial_values()
    values[set_pv] = 20.0
    values[readback_pv] = 19.96
    epics = EnergyReadbackFakeEpics(values, set_pv, readback_pv)
    machine = EpicsMachine(config, epics_client=epics)

    snapshot = machine.snapshot()
    machine.set_energy_delta(snapshot.energy_delta + 1.0e-4)
    machine.restore(snapshot)

    assert snapshot.energy_delta == pytest.approx(20.0 / 5000.0)
    assert snapshot.metadata["energy_readback_delta"] == pytest.approx(
        19.96 / 5000.0
    )
    energy_writes = [
        value
        for pv, value in epics.caput_calls
        if pv == set_pv
    ]
    assert energy_writes == pytest.approx([20.5, 20.0])
    assert epics.values[set_pv] == pytest.approx(20.0)


def test_legacy_phase_calibration_and_pv_aliases_remain_supported() -> None:
    config = write_config()
    config = replace(
        config,
        energy_knob=replace(
            config.energy_knob,
            calibration={"kind": "linear", "phase_per_delta": 2500.0},
        ),
    )

    machine = EpicsMachine(config, epics_client=FakeEpics(initial_values()))

    assert machine.get_energy_delta() == pytest.approx(12.3 / 2500.0)


def test_current_deltas_are_relative_to_snapshot_and_restore_exactly() -> None:
    epics = FakeEpics(initial_values())
    machine = EpicsMachine(write_config(), epics_client=epics)
    snapshot = machine.snapshot()

    machine.set_knobs({"Q13_Q16_sym": 0.001, "Q14_Q15_sym": 0.0})
    machine.apply_device_deltas({"QM13": 0.001, "QM16": 0.001})

    assert epics.values[QUAD_PVS["QM13"]] == pytest.approx(1.301)
    assert epics.values[QUAD_PVS["QM16"]] == pytest.approx(1.601)
    machine.restore(snapshot)
    assert epics.values[QUAD_PVS["QM13"]] == pytest.approx(1.3)
    assert epics.values[QUAD_PVS["QM16"]] == pytest.approx(1.6)
    assert machine.get_knobs(("Q13_Q16_sym", "Q14_Q15_sym")) == {
        "Q13_Q16_sym": 0.0,
        "Q14_Q15_sym": 0.0,
    }


def test_control_can_switch_to_k1_with_k1_tolerance() -> None:
    config = write_config()
    options = deepcopy(config.backend.options)
    for item in options["pv_map"]["quadrupoles"].values():
        item["control"] = "k1"
    options["quadrupole_readback_tolerance"] = 1.0e-5
    config = replace(config, backend=replace(config.backend, options=options))
    epics = FakeEpics(initial_values())
    machine = EpicsMachine(config, epics_client=epics)
    machine.snapshot()

    machine.set_knobs({"Q13_Q16_sym": 0.001})
    machine.apply_device_deltas({"QM13": 0.001, "QM16": 0.001})

    assert epics.values[QUAD_K1_PVS["QM13"]] == pytest.approx(0.131)
    assert epics.values[QUAD_K1_PVS["QM16"]] == pytest.approx(0.161)


def test_partial_quadrupole_failure_rolls_back_written_devices() -> None:
    epics = FakeEpics(initial_values(), fail_pvs={QUAD_PVS["QM16"]})
    machine = EpicsMachine(write_config(), epics_client=epics)
    machine.snapshot()
    machine.set_knobs({"Q13_Q16_sym": 0.001})

    with pytest.raises(RuntimeError, match="Quadrupole write failed"):
        machine.apply_device_deltas({"QM13": 0.001, "QM16": 0.001})

    assert epics.values[QUAD_PVS["QM13"]] == pytest.approx(1.3)


def test_write_enabled_preflight_accepts_calibrated_irfel_mapping() -> None:
    result = run_preflight(write_config())

    assert result.ok
    assert result.level == "write-ready"
    assert result.checks["energy_write_pv_configured"]
    assert result.checks["quadrupole_write_pvs_configured"]
    assert result.checks["quadrupole_independent_readbacks"]
    assert len(result.warnings) == 1


def test_apply_design_targets_writes_reviewed_k1_values() -> None:
    config = write_config()
    options = deepcopy(config.backend.options)
    for item in options["pv_map"]["quadrupoles"].values():
        item["control"] = "k1"
    config = replace(
        config,
        backend=replace(config.backend, options=options),
        measurement=replace(
            config.measurement,
            samples_per_step=1,
            sample_interval_s=1.0e-6,
            settle_time_s=1.0e-6,
        ),
    )
    epics = DynamicFakeEpics(initial_values())
    machine = EpicsMachine(config, epics_client=epics)
    workflow = AchromatWorkflow(config, machine=machine)
    baseline = machine.read_quadrupole_readbacks()
    targets = {
        name: value + 0.001
        for name, value in baseline.items()
    }

    result = workflow.apply_design_targets(
        targets,
        reviewed_baseline=baseline,
        max_changes={name: 0.01 for name in targets},
    )

    assert result["operation"] == "design-k1"
    assert result["final_values"] == pytest.approx(targets)
    assert {
        name: epics.values[pv]
        for name, pv in QUAD_K1_PVS.items()
    } == pytest.approx(targets)


def test_apply_design_targets_rejects_change_over_configured_limit() -> None:
    config = write_config()
    options = deepcopy(config.backend.options)
    for item in options["pv_map"]["quadrupoles"].values():
        item["control"] = "k1"
    config = replace(config, backend=replace(config.backend, options=options))
    epics = DynamicFakeEpics(initial_values())
    machine = EpicsMachine(config, epics_client=epics)
    workflow = AchromatWorkflow(config, machine=machine)
    baseline = machine.read_quadrupole_readbacks()
    targets = dict(baseline)
    targets["QM13"] += 0.02

    with pytest.raises(ValueError, match="exceeds configured limit"):
        workflow.apply_design_targets(
            targets,
            reviewed_baseline=baseline,
            max_changes={name: 0.01 for name in targets},
        )

    assert epics.caput_calls == []


def test_apply_design_targets_restores_snapshot_when_safety_fails() -> None:
    config = write_config()
    options = deepcopy(config.backend.options)
    for item in options["pv_map"]["quadrupoles"].values():
        item["control"] = "k1"
    config = replace(
        config,
        backend=replace(config.backend, options=options),
        measurement=replace(
            config.measurement,
            samples_per_step=1,
            sample_interval_s=1.0e-6,
            settle_time_s=1.0e-6,
        ),
    )
    epics = DynamicFakeEpics(initial_values())

    class UnsafeAfterWriteMachine(EpicsMachine):
        def is_safe(self) -> bool:
            return False

    machine = UnsafeAfterWriteMachine(config, epics_client=epics)
    workflow = AchromatWorkflow(config, machine=machine)
    baseline = machine.read_quadrupole_readbacks()
    targets = {
        name: value + 0.001
        for name, value in baseline.items()
    }

    with pytest.raises(
        RuntimeError,
        match="pre-write quadrupole setpoints restored",
    ):
        workflow.apply_design_targets(
            targets,
            reviewed_baseline=baseline,
            max_changes={name: 0.01 for name in targets},
        )

    assert {
        name: epics.values[pv]
        for name, pv in QUAD_K1_PVS.items()
    } == pytest.approx(baseline)


def test_restore_correction_state_writes_reviewed_initial_values() -> None:
    config = write_config()
    options = deepcopy(config.backend.options)
    for item in options["pv_map"]["quadrupoles"].values():
        item["control"] = "k1"
    config = replace(
        config,
        backend=replace(config.backend, options=options),
        measurement=replace(
            config.measurement,
            samples_per_step=1,
            sample_interval_s=1.0e-6,
            settle_time_s=1.0e-6,
        ),
    )
    epics = DynamicFakeEpics(initial_values())
    machine = EpicsMachine(config, epics_client=epics)
    targets = machine.read_quadrupole_readbacks()
    corrected = {name: value + 0.001 for name, value in targets.items()}
    for name, value in corrected.items():
        epics.values[QUAD_K1_PVS[name]] = value
    workflow = AchromatWorkflow(config, machine=machine)

    result = workflow.restore_correction_state(
        targets,
        reviewed_baseline=corrected,
        max_changes={name: 0.01 for name in targets},
    )

    assert result["operation"] == "restore-correction"
    assert result["final_values"] == pytest.approx(targets)
    assert {
        name: epics.values[pv]
        for name, pv in QUAD_K1_PVS.items()
    } == pytest.approx(targets)


def test_restore_correction_state_rejects_stale_current_values() -> None:
    config = write_config()
    options = deepcopy(config.backend.options)
    for item in options["pv_map"]["quadrupoles"].values():
        item["control"] = "k1"
    config = replace(config, backend=replace(config.backend, options=options))
    epics = DynamicFakeEpics(initial_values())
    machine = EpicsMachine(config, epics_client=epics)
    targets = machine.read_quadrupole_readbacks()
    reviewed = {name: value + 0.001 for name, value in targets.items()}
    for name, value in reviewed.items():
        epics.values[QUAD_K1_PVS[name]] = value
    epics.values[QUAD_K1_PVS["QM13"]] += 0.2
    workflow = AchromatWorkflow(config, machine=machine)

    with pytest.raises(RuntimeError, match="changed after review"):
        workflow.restore_correction_state(
            targets,
            reviewed_baseline=reviewed,
            max_changes={name: 0.01 for name in targets},
        )

    assert epics.caput_calls == []


def test_restore_correction_state_rolls_back_when_safety_fails() -> None:
    config = write_config()
    options = deepcopy(config.backend.options)
    for item in options["pv_map"]["quadrupoles"].values():
        item["control"] = "k1"
    config = replace(
        config,
        backend=replace(config.backend, options=options),
        measurement=replace(
            config.measurement,
            samples_per_step=1,
            sample_interval_s=1.0e-6,
            settle_time_s=1.0e-6,
        ),
    )
    epics = DynamicFakeEpics(initial_values())

    class UnsafeAfterWriteMachine(EpicsMachine):
        def is_safe(self) -> bool:
            return False

    machine = UnsafeAfterWriteMachine(config, epics_client=epics)
    targets = machine.read_quadrupole_readbacks()
    corrected = {name: value + 0.001 for name, value in targets.items()}
    for name, value in corrected.items():
        epics.values[QUAD_K1_PVS[name]] = value
    workflow = AchromatWorkflow(config, machine=machine)

    with pytest.raises(
        RuntimeError,
        match="pre-restore quadrupole setpoints restored",
    ):
        workflow.restore_correction_state(
            targets,
            reviewed_baseline=corrected,
            max_changes={name: 0.01 for name in targets},
        )

    assert {
        name: epics.values[pv]
        for name, pv in QUAD_K1_PVS.items()
    } == pytest.approx(corrected)


def test_live_preflight_reads_all_required_pvs_without_writing() -> None:
    epics = DynamicFakeEpics(initial_values())
    machine = EpicsMachine(write_config(), epics_client=epics)

    result = run_live_preflight(machine.config, machine)

    assert result.ok
    assert result.checks["all_target_bpms_valid"]
    assert result.checks["quadrupole_setpoint_readback_match"]
    assert epics.caput_calls == []


def test_live_preflight_blocks_quadrupole_mismatch_without_writing() -> None:
    values = initial_values()
    values[QUAD_READBACK_PVS["QM13"]] += 0.2
    epics = DynamicFakeEpics(values)
    machine = EpicsMachine(write_config(), epics_client=epics)

    result = run_live_preflight(machine.config, machine)

    assert not result.ok
    assert not result.checks["quadrupole_setpoint_readback_match"]
    assert epics.caput_calls == []


def test_live_preflight_retries_transient_read_failure_without_writing() -> None:
    config = write_config()
    config = replace(
        config,
        backend=replace(
            config.backend,
            options={
                **config.backend.options,
                "live_preflight_attempts": 2,
                "live_preflight_retry_interval_s": 0.0,
            },
        ),
    )
    epics = DynamicFakeEpics(initial_values())

    class TransientEnergyReadMachine(EpicsMachine):
        def __init__(self):
            super().__init__(config, epics_client=epics)
            self.energy_reads = 0

        def get_energy_delta(self) -> float:
            self.energy_reads += 1
            if self.energy_reads == 1:
                raise RuntimeError("channel is still connecting")
            return super().get_energy_delta()

    machine = TransientEnergyReadMachine()
    result = run_live_preflight(config, machine)

    assert result.ok
    assert result.readings["live_preflight_attempt"] == 2
    assert result.readings["live_preflight_attempts_allowed"] == 2
    assert epics.caput_calls == []


def test_full_epics_workflow_corrects_dynamic_model_and_restores_phase() -> None:
    config = write_config()
    config = replace(
        config,
        measurement=replace(
            config.measurement,
            samples_per_step=2,
            sample_interval_s=1.0e-6,
            final_samples=2,
            settle_time_s=1.0e-6,
        ),
        solver=replace(config.solver, max_iter=5),
    )
    epics = DynamicFakeEpics(initial_values())
    machine = EpicsMachine(config, epics_client=epics)

    result = AchromatWorkflow(config, machine=machine).run()

    assert result.success
    assert result.improvement >= config.solver.success_min_improvement
    assert epics.values[ENERGY_PV] == pytest.approx(12.3)
    assert any(
        epics.values[pv] != pytest.approx(initial_values()[pv])
        for pv in QUAD_PVS.values()
    )


def test_reviewed_recommendation_blocks_changed_quadrupole_before_writing() -> None:
    config = write_config()
    config = replace(
        config,
        measurement=replace(
            config.measurement,
            samples_per_step=1,
            sample_interval_s=1.0e-6,
            final_samples=1,
            settle_time_s=1.0e-6,
        ),
    )
    epics = DynamicFakeEpics(initial_values())
    machine = EpicsMachine(config, epics_client=epics)
    workflow = AchromatWorkflow(config, machine=machine)
    response = workflow.build_response_matrix()
    baseline = machine.read_quadrupole_readbacks()
    recommendation = build_correction_recommendation(
        config,
        response.measurement,
        response,
        baseline_device_values=baseline,
    )

    changed_value = baseline["QM13"] + 0.2
    epics.values[QUAD_PVS["QM13"]] = changed_value
    epics.values[QUAD_READBACK_PVS["QM13"]] = changed_value
    writes_before_apply = list(epics.caput_calls)

    with pytest.raises(RuntimeError, match="QM13 changed after review"):
        workflow.apply_recommendation(recommendation)

    assert epics.caput_calls == writes_before_apply


def test_reviewed_recommendation_writes_the_displayed_physical_targets() -> None:
    config = write_config()
    config = replace(
        config,
        measurement=replace(
            config.measurement,
            samples_per_step=1,
            sample_interval_s=1.0e-6,
            final_samples=1,
            settle_time_s=1.0e-6,
        ),
    )
    epics = DynamicFakeEpics(initial_values())
    machine = EpicsMachine(config, epics_client=epics)
    workflow = AchromatWorkflow(config, machine=machine)
    response = workflow.build_response_matrix()
    recommendation = build_correction_recommendation(
        config,
        response.measurement,
        response,
        baseline_device_values=machine.read_quadrupole_readbacks(),
    )
    call_count = len(epics.caput_calls)

    result = workflow.apply_recommendation(recommendation)

    assert result.success
    apply_calls = epics.caput_calls[call_count:]
    for device, target in recommendation.target_device_values.items():
        assert any(
            pv == QUAD_PVS[device] and value == pytest.approx(target)
            for pv, value in apply_calls
        )
