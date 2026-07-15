from dataclasses import replace
from copy import deepcopy

import numpy as np
import pytest

from half_linac.src.apps.dispersion_correction.config import load_config
from half_linac.src.apps.dispersion_correction.machine.epics import EpicsMachine
from half_linac.src.apps.dispersion_correction.preflight import run_live_preflight, run_preflight
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


def write_config():
    config = load_config("tests/dispersion_correction/fixtures/irfel_achromat.json")
    return replace(
        config,
        backend=replace(config.backend, mode="write_enabled"),
        energy_knob=replace(
            config.energy_knob,
            calibration={"kind": "linear", "phase_per_delta": 2500.0},
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
