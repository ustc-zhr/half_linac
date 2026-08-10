from dataclasses import replace

import pytest

from half_linac.src.apps.dispersion_correction.config import load_config
from half_linac.src.apps.dispersion_correction.machine.epics import EpicsMachine


class FakeEpics:
    def __init__(self, values):
        self.values = dict(values)

    def caget(self, pv, *args, **kwargs):
        return self.values.get(pv)


def test_irfel_epics_readonly_pv_mapping() -> None:
    config = load_config("tests/dispersion_correction/fixtures/irfel_achromat.json")
    config = replace(
        config,
        energy_knob=replace(
            config.energy_knob,
            calibration={
                "kind": "linear_relative",
                "actuator_per_delta": 2500.0,
            },
        ),
    )
    epics = FakeEpics(
        {
            "IRFEL-BI:BPM09:BPM_PX2": 1.25,
            "IRFEL-BI:BPM10:BPM_PX2": -0.5,
            "IRFEL:IN-MW:KLY1:SET_PHASE": 12.3,
            "IRFEL:PS:QM13:K1:ao": 0.13,
            "IRFEL:PS:QM14:K1:ao": 0.14,
            "IRFEL:PS:QM15:K1:ao": 0.15,
            "IRFEL:PS:QM16:K1:ao": 0.16,
            "IRFEL:PS:QM13:current:ai": 1.3,
            "IRFEL:PS:QM14:current:ai": 1.4,
            "IRFEL:PS:QM15:current:ai": 1.5,
            "IRFEL:PS:QM16:current:ai": 1.6,
        }
    )
    machine = EpicsMachine(config, epics_client=epics)

    bpm = machine.read_bpm(config.target_bpms)
    snapshot = machine.snapshot()

    assert bpm.names == ("BPM9", "BPM10")
    assert bpm.x_mm.tolist() == [1.25, -0.5]
    assert bpm.valid.tolist() == [True, True]
    assert snapshot.energy_delta == pytest.approx(12.3 / 2500.0)
    assert snapshot.device_values == {"QM13": 1.3, "QM14": 1.4, "QM15": 1.5, "QM16": 1.6}


def test_epics_readonly_rejects_writes() -> None:
    config = load_config("tests/dispersion_correction/fixtures/irfel_achromat.json")
    machine = EpicsMachine(config, epics_client=FakeEpics({}))

    with pytest.raises(PermissionError):
        machine.set_energy_delta(1.0)


def test_epics_wait_stable_uses_measurement_settle_time(monkeypatch) -> None:
    config = load_config("tests/dispersion_correction/fixtures/irfel_achromat.json")
    machine = EpicsMachine(config, epics_client=FakeEpics({}))
    waits = []
    monkeypatch.setattr("half_linac.src.apps.dispersion_correction.machine.epics.time.sleep", waits.append)

    machine.wait_stable()

    assert waits == [1.0]
