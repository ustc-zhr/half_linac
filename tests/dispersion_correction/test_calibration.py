import pytest

from half_linac.src.apps.dispersion_correction.calibration import (
    actuator_step_for_delta,
    load_energy_knob_calibration_csv,
    load_phase_calibration_csv,
)
from half_linac.src.apps.dispersion_correction.calibration_draft import (
    EnergyCalibrationDraft,
    EnergyCalibrationPoint,
    analyze_energy_calibration_draft,
    calibration_fragment,
    load_energy_calibration_draft,
    save_energy_calibration_draft,
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


def direct_delta_draft() -> EnergyCalibrationDraft:
    return EnergyCalibrationDraft(
        actuator="rf_phase",
        actuator_unit="deg",
        input_mode="direct_delta",
        baseline_actuator=0.0,
        reference_energy=None,
        points=tuple(
            EnergyCalibrationPoint(
                actuator_value=actuator,
                delta_p_over_p=delta,
                note="reference" if actuator == 0.0 else "",
            )
            for actuator, delta in (
                (-0.50, -0.00020),
                (-0.25, -0.00010),
                (0.00, 0.00000),
                (0.25, 0.00010),
                (0.50, 0.00020),
            )
        ),
        machine_id="irfel",
        backend="real",
    )


def test_calibration_draft_quality_and_fragment() -> None:
    draft = direct_delta_draft()

    analysis = analyze_energy_calibration_draft(
        draft,
        target_delta=1.0e-4,
    )
    fragment = calibration_fragment(
        draft,
        analysis,
        source_path="runtime/calibrations/latest.json",
    )

    assert analysis.valid
    assert analysis.fit is not None
    assert analysis.fit.actuator_per_delta == pytest.approx(2500.0)
    assert analysis.target_actuator_step == pytest.approx(0.25)
    assert fragment["session_override"]
    assert fragment["actuator_per_delta"] == pytest.approx(2500.0)


def test_measured_energy_draft_computes_relative_momentum() -> None:
    draft = EnergyCalibrationDraft(
        actuator="modulator_voltage",
        actuator_unit="kV",
        input_mode="measured_energy",
        baseline_actuator=20.0,
        reference_energy=40.0,
        points=tuple(
            EnergyCalibrationPoint(
                actuator_value=actuator,
                measured_energy=energy,
            )
            for actuator, energy in (
                (19.0, 39.992),
                (19.5, 39.996),
                (20.0, 40.000),
                (20.5, 40.004),
                (21.0, 40.008),
            )
        ),
    )

    analysis = analyze_energy_calibration_draft(
        draft,
        target_delta=1.0e-4,
    )

    assert analysis.valid
    assert analysis.delta_values.tolist() == pytest.approx(
        [-0.0002, -0.0001, 0.0, 0.0001, 0.0002]
    )


def test_calibration_draft_rejects_insufficient_or_one_sided_data() -> None:
    draft = EnergyCalibrationDraft(
        actuator="rf_phase",
        actuator_unit="deg",
        input_mode="direct_delta",
        baseline_actuator=0.0,
        reference_energy=None,
        points=(
            EnergyCalibrationPoint(actuator_value=0.0, delta_p_over_p=0.0),
            EnergyCalibrationPoint(actuator_value=0.1, delta_p_over_p=1.0e-4),
        ),
    )

    analysis = analyze_energy_calibration_draft(
        draft,
        target_delta=1.0e-4,
    )

    assert not analysis.valid
    assert any("At least 5" in item for item in analysis.blockers)
    assert any("negative and positive" in item for item in analysis.blockers)


def test_calibration_draft_round_trip(tmp_path) -> None:
    draft = direct_delta_draft()
    analysis = analyze_energy_calibration_draft(
        draft,
        target_delta=1.0e-4,
    )

    paths = save_energy_calibration_draft(tmp_path, draft, analysis)
    restored = load_energy_calibration_draft(paths["latest"])

    assert paths["archive"].exists()
    assert restored == draft
