from half_linac.src.apps.dispersion_correction.config import load_config
from dataclasses import replace
from half_linac.src.apps.dispersion_correction.preflight import format_preflight, run_preflight


def test_irfel_mock_preflight_is_offline_ready() -> None:
    result = run_preflight(load_config("tests/dispersion_correction/fixtures/irfel_achromat.mock.json"))

    assert result.ok
    assert result.level == "offline-ready"
    assert result.checks["target_bpm_x_pvs_configured"]
    assert result.checks["quadrupole_pvs_configured"]
    assert result.checks["energy_calibration_available"]


def test_irfel_real_preflight_blocks_missing_phase_calibration() -> None:
    result = run_preflight(load_config("tests/dispersion_correction/fixtures/irfel_achromat.json"))
    text = format_preflight(result)

    assert not result.ok
    assert result.level == "blocked"
    assert any("calibration.phase_per_delta" in item for item in result.blockers)
    assert not any("Charge PV" in item for item in result.warnings)
    assert not any("Loss PV" in item for item in result.warnings)
    assert "FAIL  energy_calibration_available" in text


def test_irfel_real_preflight_allows_missing_optional_signals() -> None:
    config = load_config("tests/dispersion_correction/fixtures/irfel_achromat.json")
    config = replace(
        config,
        energy_knob=replace(
            config.energy_knob,
            calibration={"kind": "linear", "phase_per_delta": 2500.0},
        ),
    )

    result = run_preflight(config)

    assert result.ok
    assert result.level == "read-only-ready"
    assert result.checks["energy_calibration_available"]
