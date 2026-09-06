from half_linac.src.apps.rf_phase_scan.phase_energy_scan import (
    EnergyMatchResult,
    PhaseEnergyScanner,
    PhaseScanSettings,
    wait_for_phase_readback,
)
from half_linac.src.apps.rf_phase_scan.image_acquisition import RFImageAcquisition
from half_linac.src.apps.rf_phase_scan.energy_match_tuner import (
    RFPhaseEnergyMatcher,
    center_out_values,
)
from half_linac.src.apps.rf_phase_scan.spectrum_profile import (
    ProfileFit,
    project_image_profiles,
)

import numpy as np
import pytest


def _settings(
    *, mode="relative", start=-10.0, stop=10.0, points=3,
    retry_first_point_on_failure=False,
):
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
        retry_first_point_on_failure=retry_first_point_on_failure,
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


def test_first_point_can_retry_after_direct_match_failure():
    commands = []
    attempts = []

    def match(_center, low, high, attempt):
        attempts.append((low, high, attempt))
        if len(attempts) == 1:
            return EnergyMatchResult(False, "FAILED", message="Direct center lock failed.")
        return EnergyMatchResult(True, "DONE", energy_mev=2200.0)

    scanner = _scanner(
        _settings(retry_first_point_on_failure=True),
        initial_phase=0.0,
        commands=commands,
        match_energy=match,
    )

    result = scanner.run()

    assert result.status == "DONE"
    assert result.points[0].attempts == 2
    assert attempts[:2] == [
        (2175.0, 2225.0, 1),
        (2100.0, 2300.0, 2),
    ]


def test_phase_readback_verification_polls_until_wrapped_target_arrives(monkeypatch):
    readings = iter((None, 179.0, -179.95))
    monkeypatch.setattr(
        "half_linac.src.apps.rf_phase_scan.phase_energy_scan.time.sleep",
        lambda _duration: None,
    )

    actual = wait_for_phase_readback(
        lambda: next(readings),
        180.0,
        tolerance_deg=0.1,
        timeout_s=2.0,
        poll_interval_s=0.05,
    )

    assert actual == -179.95


def test_phase_readback_verification_reports_target_and_actual():
    with pytest.raises(RuntimeError, match=r"target=12 deg, readback=10 deg"):
        wait_for_phase_readback(
            lambda: 10.0,
            12.0,
            tolerance_deg=0.1,
            timeout_s=0.0,
            poll_interval_s=0.05,
        )


def test_energy_match_progress_includes_the_measured_image():
    image = np.arange(12, dtype=float).reshape(3, 4)

    class Acquisition:
        def sample_profile(self, **_kwargs):
            return {
                "raw_image": image,
                "center_mm": 0.25,
                "brightness": 42.0,
                "fit_method": "Gauss fit",
                "fit_r_squared": 0.95,
                "valid_frames": 3,
            }

    updates = []
    matcher = object.__new__(RFPhaseEnergyMatcher)
    matcher.acquisition = Acquisition()
    matcher.frame_samples = 3
    matcher.min_valid_frames = 2
    matcher.verification_frame_samples = 5
    matcher.verification_min_valid_frames = 3
    matcher.frame_interval_s = 0.0
    matcher.profile_fit_method = "Gauss fit"
    matcher.min_fit_r_squared = 0.7
    matcher.x_reference_mm = 0.0
    matcher.cancel_requested = None
    matcher.progress_callback = updates.append

    result = matcher._measure(2360.0, "reacquire")

    np.testing.assert_array_equal(result["raw_image"], image)
    np.testing.assert_array_equal(updates[0]["raw_image"], image)
    assert updates[0]["energy_mev"] == 2360.0
    assert updates[0]["stage"] == "reacquire"


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


def test_rf_image_acquisition_applies_configured_vertical_orientation():
    raw = np.arange(30, dtype=float).reshape(5, 6)
    acquisition = RFImageAcquisition(
        _ImagePV(raw),
        (6, 5),
        0.1,
        flip_y=True,
    )

    np.testing.assert_array_equal(acquisition.read_raw(), np.flipud(raw))


def test_rf_image_acquisition_rejects_low_quality_gaussian_frames(monkeypatch):
    raw = np.arange(30, dtype=float).reshape(5, 6)
    acquisition = RFImageAcquisition(_ImagePV(raw), (6, 5), 0.1)

    def low_quality_fit(x_mm, density_x, method, **_kwargs):
        normalized = np.asarray(density_x, dtype=float)
        normalized /= np.max(normalized)
        return ProfileFit(
            center_mm=float(x_mm[len(x_mm) // 2]),
            sigma_mm=0.1,
            normalized_density=normalized,
            fitted_density=normalized,
            method="Gauss fit",
            r_squared=0.4,
        )

    monkeypatch.setattr(
        "half_linac.src.apps.rf_phase_scan.image_acquisition.fit_projection_profile",
        low_quality_fit,
    )

    assert acquisition.sample_profile(
        samples=3,
        min_valid=2,
        interval_s=0,
        fit_method="Gauss fit",
        min_fit_r_squared=0.7,
    ) is None


def test_rf_projection_preserves_established_edge_crop():
    image = np.arange(30, dtype=float).reshape(5, 6)
    projection = project_image_profiles(image, 0.1)

    np.testing.assert_array_equal(projection.image, image[1:-1, 1:-1])


def test_reacquire_grid_is_ordered_from_center_outward():
    values = center_out_values(0.0, 100.0, 50.0, 5)

    assert values == (50.0, 25.0, 75.0, 0.0, 100.0)


def test_brightness_peak_stage_uses_configured_center_outward_point_count():
    matcher = object.__new__(RFPhaseEnergyMatcher)
    matcher._pipeline_start_energy = 50.0
    matcher._pipeline_seed = None
    matcher.pipeline = ("brightness_peak",)
    matcher.center_tolerance_mm = 0.2
    matcher.last_message = None
    visited = []
    matcher._reacquire = lambda low, high, center, steps: (
        visited.append((low, high, center, steps))
        or {"energy": 55.0, "brightness": 12.0, "offset_mm": 1.0}
    )

    result = matcher._run_brightness_peak_stage(
        0.0,
        100.0,
        {"strategy": "center_outward", "points": 5},
    )

    assert result.ok
    assert result.actuator_value == 55.0
    assert visited == [(0.0, 100.0, 50.0, 5)]
    assert result.diagnostics["exploration_strategy"] == "center_outward"
    assert result.diagnostics["exploration_points"] == 5


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
