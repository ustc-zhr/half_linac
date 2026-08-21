from half_linac.src.apps.rf_phase_scan.phase_energy_scan import (
    EnergyMatchResult,
    PhaseEnergyScanner,
    PhaseScanSettings,
)
from half_linac.src.apps.rf_phase_scan.image_acquisition import RFImageAcquisition
from half_linac.src.apps.rf_phase_scan.energy_match_tuner import (
    RFPhaseEnergyMatcher,
    center_out_values,
)
from half_linac.src.apps.rf_phase_scan.spectrum_profile import project_image_profiles

import numpy as np


def _settings(*, mode="relative", start=-10.0, stop=10.0, points=3):
    return PhaseScanSettings(
        low_offset_deg=start,
        high_offset_deg=stop,
        points=points,
        phase_settle_time_s=0.0,
        tracking_half_window_mev=25.0,
        fallback_half_window_mev=100.0,
        max_consecutive_failures=3,
        energy_low_mev=0.0,
        energy_high_mev=2450.0,
        phase_mode=mode,
    )


def _scanner(settings, *, initial_phase, match_energy, commands):
    return PhaseEnergyScanner(
        settings=settings,
        read_phase=lambda: initial_phase,
        set_phase=lambda value: commands.append(value),
        read_energy=lambda: 2200.0,
        set_energy=lambda _value: None,
        match_energy=match_energy,
    )


def test_relative_phase_scan_uses_initial_phase_and_wraps_commands():
    commands = []
    scanner = _scanner(
        _settings(start=-10.0, stop=10.0),
        initial_phase=175.0,
        commands=commands,
        match_energy=lambda _center, _low, _high, _attempt: EnergyMatchResult(
            True, "DONE", energy_mev=2200.0
        ),
    )

    result = scanner.run()

    assert [point.requested_phase_unwrapped_deg for point in result.points] == [
        175.0, 165.0, 185.0,
    ]
    assert [point.command_phase_deg for point in result.points] == [175.0, 165.0, -175.0]
    assert commands[-1] == 175.0


def test_absolute_phase_scan_preserves_continuous_targets():
    commands = []
    scanner = _scanner(
        _settings(mode="absolute", start=170.0, stop=190.0),
        initial_phase=20.0,
        commands=commands,
        match_energy=lambda _center, _low, _high, _attempt: EnergyMatchResult(
            True, "DONE", energy_mev=2200.0
        ),
    )

    result = scanner.run()

    assert [point.requested_phase_unwrapped_deg for point in result.points] == [
        170.0, 180.0, 190.0,
    ]
    assert [point.command_phase_deg for point in result.points] == [170.0, -180.0, -170.0]
    assert [point.offset_deg for point in result.points] == [150.0, 160.0, 170.0]
    assert commands[-1] == 20.0


def test_point_measurement_failure_does_not_retry_fallback_window():
    commands = []
    attempts = []

    def fail_measurement(center, low, high, attempt):
        attempts.append((center, low, high, attempt))
        return EnergyMatchResult(False, "MEASUREMENT_FAILED", energy_mev=2200.0)

    scanner = _scanner(
        _settings(),
        initial_phase=0.0,
        commands=commands,
        match_energy=fail_measurement,
    )

    result = scanner.run()

    assert len(attempts) == 3
    assert all(attempt == 1 for _center, _low, _high, attempt in attempts)
    assert all(point.attempts == 1 for point in result.points)


class _ImagePV:
    def __init__(self, image):
        self.image = np.asarray(image)

    def get(self):
        return self.image.ravel()


def test_rf_image_acquisition_keeps_raw_image_and_subtracts_background_for_analysis():
    raw = np.full((5, 6), 10.0)
    acquisition = RFImageAcquisition(
        _ImagePV(raw),
        (6, 5),
        0.1,
        background=np.full((5, 6), 3.0),
    )

    np.testing.assert_array_equal(acquisition.read_raw(), raw)
    np.testing.assert_array_equal(acquisition.read_analysis(), np.full((5, 6), 7.0))


def test_rf_projection_preserves_established_edge_crop():
    image = np.arange(30, dtype=float).reshape(5, 6)
    projection = project_image_profiles(image, 0.1)

    np.testing.assert_array_equal(projection.image, image[1:-1, 1:-1])


def test_reacquire_grid_is_ordered_from_center_outward():
    values = center_out_values(0.0, 100.0, 50.0, 5)

    assert values == (50.0, 25.0, 75.0, 0.0, 100.0)


def test_dispersion_prediction_moves_coordinated_energy_toward_beam_energy():
    matcher = object.__new__(RFPhaseEnergyMatcher)
    matcher.design_eta_m = 0.75
    matcher.max_correction_step_mev = 25.0

    predicted = matcher._predicted_energy(
        {"energy": 2200.0, "offset_mm": 3.0}
    )

    assert predicted == 2208.8


def test_dispersion_prediction_limits_single_correction_step():
    matcher = object.__new__(RFPhaseEnergyMatcher)
    matcher.design_eta_m = 0.75
    matcher.max_correction_step_mev = 5.0

    assert matcher._predicted_energy({"energy": 2200.0, "offset_mm": 30.0}) == 2205.0
