import pytest

from half_linac.src.apps.dispersion_correction.calibration import (
    actuator_step_for_delta,
    load_energy_knob_calibration_csv,
    load_phase_calibration_csv,
)


def test_phase_calibration_csv_fit() -> None:
    fit = load_phase_calibration_csv("tests/dispersion_correction/fixtures/irfel_phase_calibration.example.csv")

    assert fit.n_samples == 5
    assert abs(fit.slope_delta_per_phase - 0.0004) < 1.0e-12
    assert abs(fit.phase_per_delta - 2500.0) < 1.0e-6
    assert fit.r_squared > 0.999999


def test_actuator_step_for_delta_uses_phase_per_delta() -> None:
    plan = actuator_step_for_delta(1.0e-4, {"kind": "linear", "phase_per_delta": 2500.0})

    assert plan["calibrated"]
    assert plan["actuator_step"] == 0.25
    assert plan["plus_offset"] == 0.25
    assert plan["minus_offset"] == -0.25


def test_generic_energy_knob_calibration_csv_fit(tmp_path) -> None:
    path = tmp_path / "voltage_calibration.csv"
    path.write_text(
        "actuator_value,delta_p_over_p\n"
        "19.5,-0.0001\n"
        "20.0,0.0\n"
        "20.5,0.0001\n",
        encoding="utf-8",
    )

    fit = load_energy_knob_calibration_csv(path)
    plan = actuator_step_for_delta(
        1.0e-4,
        {
            "kind": "linear_relative",
            "actuator_per_delta": fit.actuator_per_delta,
        },
    )

    assert fit.slope_delta_per_actuator == pytest.approx(0.0002)
    assert fit.actuator_per_delta == pytest.approx(5000.0)
    assert plan["actuator_step"] == pytest.approx(0.5)
