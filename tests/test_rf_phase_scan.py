from half_linac.src.apps.rf_phase_scan.phase_energy_scan import (
    EnergyMatchResult,
    PhaseEnergyScanner,
    PhaseScanPoint,
    PhaseScanSettings,
    aggregate_frame_energies,
    fit_phase_energy_curve,
    wait_for_phase_readback,
)
from half_linac.src.apps.rf_phase_scan.image_acquisition import RFImageAcquisition
from half_linac.src.apps.rf_phase_scan.energy_match_tuner import (
    RFPhaseEnergyMatcher,
    center_out_values,
)
from half_linac.src.apps.rf_phase_scan.executor import RFPhaseScanExecutor
from half_linac.src.apps.rf_phase_scan.spectrum_profile import (
    ProfileFit,
    project_image_profiles,
)

import numpy as np
import pytest


def _settings(*, mode="relative", start=-10.0, stop=10.0, points=3):
    return PhaseScanSettings(
        low_offset_deg=start,
        high_offset_deg=stop,
        points=points,
        phase_settle_time_s=0.0,
        tracking_half_window_mev=25.0,
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


def test_failed_point_does_not_start_a_wide_fallback_scan():
    commands = []
    attempts = []

    def match(_center, low, high, attempt):
        attempts.append((low, high, attempt))
        if len(attempts) == 1:
            return EnergyMatchResult(False, "FAILED", message="Center lock failed.")
        return EnergyMatchResult(
            True, "DONE", energy_mev=2200.0, brightness=1000.0
        )

    scanner = _scanner(
        _settings(),
        initial_phase=0.0,
        commands=commands,
        match_energy=match,
    )

    result = scanner.run()

    assert result.status == "DONE"
    assert result.points[0].status == "failed"
    assert all(point.attempts == 1 for point in result.points)
    assert attempts == [
        (2175.0, 2225.0, 1),
        (2175.0, 2225.0, 1),
        (2175.0, 2225.0, 1),
    ]


def test_low_brightness_point_is_displayed_but_excluded_from_curve_fit():
    commands = []

    def match(_center, _low, _high, _attempt):
        phase = commands[-1]
        energy = 2100.0 + 100.0 * np.cos(np.deg2rad(phase))
        brightness = 1000.0
        if phase == pytest.approx(50.0):
            energy = 2300.0
            brightness = 1.0
        return EnergyMatchResult(
            True, "DONE", energy_mev=energy, brightness=brightness
        )

    scanner = _scanner(
        _settings(start=-50.0, stop=50.0, points=11),
        initial_phase=0.0,
        commands=commands,
        match_energy=match,
    )

    result = scanner.run()

    low_signal = [point for point in result.points if point.status == "low_signal"]
    assert [point.offset_deg for point in low_signal] == [50.0]
    assert low_signal[0].matched_energy_mev == 2300.0
    assert "excluded from curve fit" in low_signal[0].message
    assert result.fit is not None
    assert result.fit.points_used == 10
    assert result.fit.amplitude_mev == pytest.approx(100.0)
    assert result.fit.r_squared == pytest.approx(1.0)


def test_selected_frame_energies_use_arithmetic_mean_and_sample_spread():
    statistics = aggregate_frame_energies(
        [2190.0, 2200.0, 2300.0, None, np.nan],
        [True, True, False, True, True],
    )

    assert statistics == pytest.approx({
        "mean_mev": 2195.0,
        "standard_deviation_mev": np.sqrt(50.0),
        "standard_error_mev": 5.0,
        "frames_used": 2,
    })
    assert aggregate_frame_energies([2190.0, 2200.0], [True, False]) is None
    with pytest.raises(ValueError, match="selection length"):
        aggregate_frame_energies([2190.0], [True, False])


def test_phase_curve_fit_respects_manual_point_selection():
    points = []
    for index, phase in enumerate(np.linspace(-50.0, 50.0, 11)):
        energy = 2100.0 + 100.0 * np.cos(np.deg2rad(phase))
        use_in_fit = True
        if phase == pytest.approx(50.0):
            energy = 2300.0
            use_in_fit = False
        points.append(PhaseScanPoint(
            index=index,
            offset_deg=float(phase),
            requested_phase_unwrapped_deg=float(phase),
            command_phase_deg=float(phase),
            status="ok",
            attempts=1,
            search_low_mev=2000.0,
            search_high_mev=2400.0,
            matched_energy_mev=float(energy),
            use_in_fit=use_in_fit,
        ))

    fit = fit_phase_energy_curve(points)

    assert fit is not None
    assert fit.points_used == 10
    assert fit.baseline_energy_mev == pytest.approx(2100.0)
    assert fit.amplitude_mev == pytest.approx(100.0)
    assert fit.r_squared == pytest.approx(1.0)


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


@pytest.mark.parametrize(
    ("scan", "expected_settle_time_s"),
    [({"settle_time_s": 1.25}, 1.25), ({}, 3.0)],
)
def test_energy_command_uses_configured_or_default_settle_time_without_echo(
    monkeypatch, scan, expected_settle_time_s,
):
    import half_linac.src.apps.rf_phase_scan.executor as executor_module

    writes = []
    sleeps = []
    executor = object.__new__(RFPhaseScanExecutor)
    executor.request = type("Request", (), {"energy_pv": "ENERGY"})()
    executor.scan = scan
    monkeypatch.setattr(
        executor_module,
        "caput",
        lambda pv, value, **options: writes.append((pv, value, options)) or True,
    )
    monkeypatch.setattr(
        executor_module,
        "caget",
        lambda _pv: pytest.fail("energy command PV must not be used as a readback"),
    )
    monkeypatch.setattr(executor_module.time, "sleep", sleeps.append)

    executor.set_energy(2212.9)

    assert writes == [
        ("ENERGY", 2212.9, {"wait": True, "timeout": 10}),
    ]
    assert sleeps == [expected_settle_time_s]


def test_energy_command_failure_does_not_wait(monkeypatch):
    import half_linac.src.apps.rf_phase_scan.executor as executor_module

    executor = object.__new__(RFPhaseScanExecutor)
    executor.request = type("Request", (), {"energy_pv": "ENERGY"})()
    executor.scan = {"settle_time_s": 1.25}
    monkeypatch.setattr(executor_module, "caput", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        executor_module.time,
        "sleep",
        lambda _duration: pytest.fail("failed commands must not enter settle wait"),
    )

    with pytest.raises(RuntimeError, match="setpoint write failed"):
        executor.set_energy(2212.9)


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


class _ImageSequencePV:
    def __init__(self, images):
        self.images = iter(images)

    def get(self):
        image = next(self.images)
        return None if image is None else np.asarray(image).ravel()


class _BeamPresence:
    has_beam = True

    def __init__(self, brightness):
        self.brightness = float(brightness)

    def diagnostics(self):
        return {"beam_area_px": int(self.brightness)}


def _patch_profile_samples(monkeypatch, centers):
    centers = iter(centers)

    monkeypatch.setattr(
        "half_linac.src.apps.rf_phase_scan.image_acquisition.detect_beam_presence",
        lambda image, **_kwargs: _BeamPresence(np.mean(image)),
    )

    def fitted_profile(x_mm, density_x, method, **_kwargs):
        normalized = np.asarray(density_x, dtype=float)
        normalized /= np.max(normalized)
        center_mm = float(next(centers))
        return ProfileFit(
            center_mm=center_mm,
            sigma_mm=0.1,
            normalized_density=normalized,
            fitted_density=normalized,
            method="Gauss fit",
            r_squared=0.99,
        )

    monkeypatch.setattr(
        "half_linac.src.apps.rf_phase_scan.image_acquisition.fit_projection_profile",
        fitted_profile,
    )


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

    monkeypatch.setattr(
        "half_linac.src.apps.rf_phase_scan.image_acquisition.detect_beam_presence",
        lambda image, **_kwargs: _BeamPresence(np.mean(image)),
    )

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


def test_rf_image_acquisition_accepts_centroid_fallback_and_reports_methods(monkeypatch):
    frames = [np.full((5, 6), value) for value in (1.0, 2.0, 3.0)]
    acquisition = RFImageAcquisition(_ImageSequencePV(frames), (6, 5), 0.1)
    fits = [
        ProfileFit(
            center_mm=-0.2, sigma_mm=0.1,
            normalized_density=np.ones(4), fitted_density=np.ones(4),
            method="Projection RMS", r_squared=None,
            fallback_error="Gaussian fit R2 0.10 is below 0.700.",
        ),
        ProfileFit(
            center_mm=0.0, sigma_mm=0.1,
            normalized_density=np.ones(4), fitted_density=np.ones(4),
            method="Gauss fit", r_squared=0.95,
        ),
        ProfileFit(
            center_mm=0.3, sigma_mm=0.1,
            normalized_density=np.ones(4), fitted_density=np.ones(4),
            method="Projection RMS", r_squared=None,
            fallback_error="Gaussian fit R2 0.10 is below 0.700.",
        ),
    ]
    fallback_flags = []

    monkeypatch.setattr(
        "half_linac.src.apps.rf_phase_scan.image_acquisition.detect_beam_presence",
        lambda image, **_kwargs: _BeamPresence(np.mean(image)),
    )

    def mixed_fit(_x_mm, _density_x, _method, **kwargs):
        fallback_flags.append(kwargs["allow_direct_fallback"])
        return fits.pop(0)

    monkeypatch.setattr(
        "half_linac.src.apps.rf_phase_scan.image_acquisition.fit_projection_profile",
        mixed_fit,
    )

    result = acquisition.sample_profile(
        samples=3,
        min_valid=2,
        interval_s=0,
        fit_method="Gauss fit",
        allow_direct_fallback=True,
        min_fit_r_squared=0.7,
    )

    assert result is not None
    assert result["center_mm"] == pytest.approx(0.0)
    np.testing.assert_array_equal(result["raw_image"], frames[1])
    assert fallback_flags == [True, True, True]
    assert result["fit_method"] == "Projection RMS / Gauss fit"
    assert result["frame_fit_methods"] == [
        "Projection RMS", "Gauss fit", "Projection RMS",
    ]
    assert result["frame_fit_r_squared"] == [None, 0.95, None]
    assert result["frame_brightness"] == [1.0, 2.0, 3.0]
    assert result["gaussian_frames"] == 1
    assert result["centroid_frames"] == 2
    assert result["fallback_reasons"] == {
        "Gaussian fit R2 0.10 is below 0.700.": 2
    }


def test_rf_image_acquisition_uses_configured_gaussian_r_squared(monkeypatch):
    raw = np.arange(30, dtype=float).reshape(5, 6)
    acquisition = RFImageAcquisition(_ImagePV(raw), (6, 5), 0.1)
    received_thresholds = []

    monkeypatch.setattr(
        "half_linac.src.apps.rf_phase_scan.image_acquisition.detect_beam_presence",
        lambda image, **_kwargs: _BeamPresence(np.mean(image)),
    )

    def configured_fit(x_mm, density_x, method, **kwargs):
        received_thresholds.append(kwargs["min_r_squared"])
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
        configured_fit,
    )

    result = acquisition.sample_profile(
        samples=3,
        min_valid=2,
        interval_s=0,
        fit_method="Gauss fit",
        min_fit_r_squared=0.3,
    )

    assert result is not None
    assert received_thresholds == [0.3, 0.3, 0.3]


def test_rf_image_acquisition_tolerates_one_empty_frame(monkeypatch):
    first = np.full((5, 6), 1.0)
    second = np.full((5, 6), 2.0)
    acquisition = RFImageAcquisition(
        _ImageSequencePV([None, first, second]),
        (6, 5),
        0.1,
    )
    _patch_profile_samples(monkeypatch, [0.0, 0.1])

    result = acquisition.sample_profile(
        samples=3,
        min_valid=2,
        interval_s=0,
        fit_method="Gauss fit",
        min_fit_r_squared=0.7,
    )

    assert result is not None
    assert result["valid_frames"] == 2


def test_rf_image_acquisition_reports_frame_rejection_reasons():
    acquisition = RFImageAcquisition(
        _ImageSequencePV([None, None, None]),
        (6, 5),
        0.1,
    )

    assert acquisition.sample_profile(
        samples=3,
        min_valid=2,
        interval_s=0,
        fit_method="Gauss fit",
        min_fit_r_squared=0.3,
    ) is None
    assert acquisition.last_diagnostic["valid_frames"] == 0
    assert acquisition.last_diagnostic["invalid_frames"] == 3
    assert acquisition.last_diagnostic["frame_centers_mm"] == []
    assert acquisition.last_diagnostic["frame_rejection_reasons"] == {
        "Flag image PV returned no data.": 3
    }
    assert "Retained 0/3 valid frames" in acquisition.last_diagnostic["message"]


def test_rf_image_acquisition_uses_representative_frame(monkeypatch):
    frames = [np.full((5, 6), value) for value in (1.0, 2.0, 3.0)]
    acquisition = RFImageAcquisition(_ImageSequencePV(frames), (6, 5), 0.1)
    _patch_profile_samples(monkeypatch, [-0.1, 0.0, 0.1])

    result = acquisition.sample_profile(
        samples=3,
        min_valid=2,
        interval_s=0,
        fit_method="Gauss fit",
        min_fit_r_squared=0.7,
    )

    assert result is not None
    assert result["center_mm"] == pytest.approx(0.0)
    np.testing.assert_array_equal(result["raw_image"], frames[1])
    assert result["beam_area_px"] == 2


def test_rf_image_acquisition_reports_center_jitter_without_rejecting(monkeypatch):
    frames = [np.full((5, 6), value) for value in (1.0, 2.0, 3.0)]
    acquisition = RFImageAcquisition(_ImageSequencePV(frames), (6, 5), 0.1)
    _patch_profile_samples(monkeypatch, [-5.0, -5.0, 10.0])

    result = acquisition.sample_profile(
        samples=3,
        min_valid=2,
        interval_s=0,
        fit_method="Gauss fit",
        min_fit_r_squared=0.7,
    )

    assert result is not None
    assert result["center_mm"] == pytest.approx(-5.0)
    assert result["center_spread_mm"] == pytest.approx(15.0)
    assert result["frame_centers_mm"] == [-5.0, -5.0, 10.0]


def test_rf_projection_preserves_established_edge_crop():
    image = np.arange(30, dtype=float).reshape(5, 6)
    projection = project_image_profiles(image, 0.1)

    np.testing.assert_array_equal(projection.image, image[1:-1, 1:-1])


def test_frame_energy_samples_are_converted_and_grouped_by_phase():
    from half_linac.src.apps.rf_phase_scan.main import (
        _excluded_frame_sample_plot_data, _frame_energies_mev,
        _frame_sample_plot_data,
    )

    energies = _frame_energies_mev(
        2200.0, [-1.0, 0.0, 1.0], 0.0, -0.5
    )
    assert energies == pytest.approx([2204.4, 2200.0, 2195.6])

    points = [
        {
            "offset_deg": -10.0,
            "requested_phase_unwrapped_deg": 20.0,
            "frame_energies_mev": energies[:2],
            "frame_use": [True, False],
        },
        {
            "offset_deg": 10.0,
            "requested_phase_unwrapped_deg": 40.0,
            "frame_energies_mev": energies[2:],
            "frame_use": [False],
        },
    ]
    relative_x, relative_energy = _frame_sample_plot_data(points, "relative")
    absolute_x, absolute_energy = _frame_sample_plot_data(points, "absolute")
    excluded_x, excluded_energy = _excluded_frame_sample_plot_data(
        points, "relative"
    )

    assert relative_x == [-10.0, -10.0, 10.0]
    assert absolute_x == [20.0, 20.0, 40.0]
    assert relative_energy == pytest.approx(energies)
    assert absolute_energy == pytest.approx(energies)
    assert excluded_x == [-10.0, 10.0]
    assert excluded_energy == pytest.approx([energies[1], energies[2]])


def test_rf_phase_scan_window_starts_and_reviews_frame_selection(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("HALF_LINAC_MACHINE_ID", "half")
    monkeypatch.setenv("HALF_LINAC_CONTROL_BACKEND", "vm")
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QApplication
    from half_linac.src.apps.rf_phase_scan.main import RFPhaseScanWindow

    app = QApplication.instance() or QApplication([])
    window = RFPhaseScanWindow()
    window.points_data = [
        dict(vars(PhaseScanPoint(
            index=index,
            offset_deg=float(index),
            requested_phase_unwrapped_deg=float(index),
            command_phase_deg=float(index),
            status="ok",
            attempts=1,
            search_low_mev=2100.0,
            search_high_mev=2300.0,
            matched_energy_mev=2200.0 + index,
            frame_energies_mev=[
                2199.0 + index, 2200.0 + index, 2201.0 + index,
            ],
            frame_use=[True, True, True],
            use_in_fit=True,
        )))
        for index in range(5)
    ]

    window._populate_review_tables()
    window.frame_review_table.selectRow(0)
    app.processEvents()
    window._exclude_review_selected()

    assert window.phase_review_table.rowCount() == 5
    assert window.frame_review_table.rowCount() == 3
    assert window.points_data[0]["frame_use"] == [False, True, True]
    assert window.frame_review_table.item(0, 0).checkState() == Qt.Unchecked

    window._recalculate_review()

    assert window.points_data[0]["matched_energy_mev"] == pytest.approx(2200.5)
    assert window.last_fit is not None
    window.close()


def test_rf_status_strip_prefers_single_line_horizontal_expansion(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication
    from half_linac.src.apps.rf_phase_scan.main import RFPhaseStatusStrip

    app = QApplication.instance() or QApplication([])
    strip = RFPhaseStatusStrip()
    strip.add_item("scan", "Scan", "Ready")
    _container, value_label = strip._items["scan"]
    short_width = value_label.minimumWidth()

    status = "Energy Match: Approximate Lock"
    strip.set_item("scan", status)
    app.processEvents()

    assert not value_label.wordWrap()
    assert value_label.minimumWidth() > short_width
    assert value_label.minimumWidth() >= value_label.fontMetrics().horizontalAdvance(status)

    strip.set_item("scan", "Done")
    app.processEvents()
    assert value_label.minimumWidth() < short_width


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


def test_center_lock_reuses_last_measurement_without_verification_sample():
    matcher = object.__new__(RFPhaseEnergyMatcher)
    matcher.design_eta_m = 0.75
    matcher.center_tolerance_mm = 0.5
    matcher.max_iterations = 1
    matcher.progress_callback = None
    matcher.center_lock_result = None
    matcher.result_measurement = None
    matcher.last_message = None
    matcher._predicted_energy = lambda _measurement: 2210.0
    candidate = {
        "energy": 2210.0,
        "offset_mm": 0.1,
        "brightness": 40.0,
        "valid_frames": 5,
        "fit_method": "Projection RMS",
        "fit_r_squared": None,
    }
    calls = []
    matcher._at = lambda energy, stage: (
        calls.append((energy, stage)) or candidate
    )
    matcher._set_energy = lambda *_args, **_kwargs: pytest.fail(
        "Converged measurement must not be repeated."
    )
    seed = {
        "energy": 2200.0,
        "offset_mm": 20.0,
        "brightness": 42.0,
        "valid_frames": 5,
        "fit_method": "Gauss fit",
        "fit_r_squared": 0.95,
    }

    result = matcher._track_center(seed, 2150.0, 2250.0)

    assert result == 2210.0
    assert calls == [(2210.0, "correct")]
    assert matcher.result_measurement is candidate
    assert matcher.center_lock_result["lock_converged"] is True
    assert matcher.center_lock_result["measurement_mode"] == "center_lock"


def test_incomplete_center_lock_keeps_best_approximate_measurement():
    matcher = object.__new__(RFPhaseEnergyMatcher)
    matcher.design_eta_m = 0.75
    matcher.center_tolerance_mm = 0.5
    matcher.max_iterations = 1
    matcher.progress_callback = None
    matcher.center_lock_result = None
    matcher.result_measurement = None
    matcher.last_message = None
    matcher._predicted_energy = lambda _measurement: 2210.0
    candidate = {
        "energy": 2210.0,
        "offset_mm": 2.0,
        "brightness": 40.0,
        "valid_frames": 5,
        "fit_method": "Projection RMS",
        "fit_r_squared": None,
    }
    matcher._at = lambda _energy, _stage: candidate
    matcher._set_energy = lambda *_args, **_kwargs: pytest.fail(
        "Best measurement is already at the current setpoint."
    )
    seed = {
        "energy": 2200.0,
        "offset_mm": 20.0,
        "brightness": 42.0,
        "valid_frames": 5,
        "fit_method": "Gauss fit",
        "fit_r_squared": 0.95,
    }

    result = matcher._track_center(seed, 2150.0, 2250.0)

    assert result == 2210.0
    assert matcher.result_measurement is candidate
    assert matcher.center_lock_result["lock_converged"] is False
    assert matcher.center_lock_result["approximate"] is True
    assert matcher.center_lock_result["measurement_mode"] == "approximate_lock"
    assert "Using approximate energy" in matcher.last_message


def test_incomplete_center_lock_restores_best_energy_after_lost_profile():
    matcher = object.__new__(RFPhaseEnergyMatcher)
    matcher.design_eta_m = 0.75
    matcher.center_tolerance_mm = 0.5
    matcher.max_iterations = 1
    matcher.progress_callback = None
    matcher.center_lock_result = None
    matcher.result_measurement = None
    matcher.last_message = None
    matcher._predicted_energy = lambda _measurement: 2210.0
    writes = []
    matcher._at = lambda energy, _stage: writes.append(energy)
    matcher._set_energy = lambda energy: writes.append(energy)
    seed = {
        "energy": 2200.0,
        "offset_mm": 20.0,
        "brightness": 42.0,
        "valid_frames": 5,
        "fit_method": "Gauss fit",
        "fit_r_squared": 0.95,
    }

    result = matcher._track_center(seed, 2150.0, 2250.0)

    assert result == 2200.0
    assert writes == [2210.0, 2200.0]
    assert matcher.result_measurement is seed
    assert matcher.center_lock_result["approximate"] is True


def test_center_lock_trigger_uses_position_inference_inside_threshold():
    matcher = object.__new__(RFPhaseEnergyMatcher)
    matcher.center_lock_trigger_mm = 10.0
    matcher.design_eta_m = 0.75
    matcher.progress_callback = None
    matcher.center_lock_result = None
    matcher._track_center = lambda _seed, _low, _high: pytest.fail(
        "Center lock should not run inside the trigger threshold."
    )
    seed = {
        "energy": 2200.0,
        "offset_mm": 3.0,
        "brightness": 42.0,
        "valid_frames": 3,
        "fit_method": "Gauss fit",
        "fit_r_squared": 0.98,
    }

    setpoint = matcher._match_or_infer(seed, 2150.0, 2250.0)

    assert setpoint == 2200.0
    assert matcher.center_lock_result["center_lock_applied"] is False
    assert matcher.center_lock_result["measurement_mode"] == "position_inference"
    assert matcher.center_lock_result["inferred_energy_mev"] == 2208.8
    assert matcher.center_lock_result["residual_energy_mev"] == pytest.approx(8.8)


def test_center_lock_trigger_runs_lock_outside_threshold():
    matcher = object.__new__(RFPhaseEnergyMatcher)
    matcher.center_lock_trigger_mm = 10.0
    calls = []
    matcher._track_center = lambda seed, low, high: (
        calls.append((seed, low, high)) or 2195.0
    )
    seed = {"energy": 2200.0, "offset_mm": 10.1}

    matched = matcher._match_or_infer(seed, 2150.0, 2250.0)

    assert matched == 2195.0
    assert calls == [(seed, 2150.0, 2250.0)]


def test_center_lock_stage_measures_current_energy_before_trigger():
    matcher = object.__new__(RFPhaseEnergyMatcher)
    matcher.initial_energy = 2200.0
    matcher._pipeline_seed = None
    matcher.center_lock_trigger_mm = 15.0
    matcher.design_eta_m = 0.75
    matcher.progress_callback = None
    matcher.center_lock_result = None
    matcher.last_message = None
    measurements = []
    seed = {
        "energy": 2200.0,
        "offset_mm": 3.0,
        "brightness": 42.0,
        "valid_frames": 3,
        "fit_method": "Gauss fit",
        "fit_r_squared": 0.98,
    }
    matcher._measure = lambda energy, stage: (
        measurements.append((energy, stage)) or seed
    )
    matcher._at = lambda _energy, _stage: pytest.fail(
        "Energy must not be written before evaluating the center-lock trigger."
    )
    matcher._track_center = lambda _seed, _low, _high: pytest.fail(
        "Center lock should not run inside the trigger threshold."
    )

    result = matcher._run_center_lock_stage(
        2210.0,
        2150.0,
        2250.0,
        {"strategy": "secant_dispersion"},
    )

    assert result.ok
    assert result.actuator_value == 2200.0
    assert measurements == [(2200.0, "track")]
    assert result.diagnostics["center_lock_applied"] is False
    assert result.diagnostics["measurement_mode"] == "position_inference"


def test_center_lock_stage_enters_lock_after_current_offset_exceeds_trigger():
    matcher = object.__new__(RFPhaseEnergyMatcher)
    matcher.initial_energy = 2200.0
    matcher._pipeline_seed = None
    matcher.center_lock_trigger_mm = 15.0
    matcher.last_message = None
    seed = {
        "energy": 2200.0,
        "offset_mm": 15.1,
        "brightness": 42.0,
        "valid_frames": 3,
        "fit_method": "Gauss fit",
        "fit_r_squared": 0.98,
    }
    matcher._measure = lambda energy, stage: seed
    calls = []
    matcher._track_center = lambda measured, low, high: (
        calls.append((measured, low, high)) or 2205.0
    )
    matcher.center_lock_result = {
        "center_lock_applied": True,
        "measurement_mode": "center_lock",
    }

    result = matcher._run_center_lock_stage(
        2210.0,
        2150.0,
        2250.0,
        {"strategy": "secant_dispersion"},
    )

    assert result.ok
    assert result.actuator_value == 2205.0
    assert calls == [(seed, 2150.0, 2250.0)]


def test_center_lock_stage_repositions_after_brightness_search():
    matcher = object.__new__(RFPhaseEnergyMatcher)
    matcher.initial_energy = 2200.0
    matcher.center_lock_trigger_mm = 15.0
    matcher.design_eta_m = 0.75
    matcher.progress_callback = None
    matcher.center_lock_result = None
    matcher.last_message = None
    seed = {
        "energy": 2190.0,
        "offset_mm": 3.0,
        "brightness": 42.0,
        "valid_frames": 3,
        "fit_method": "Gauss fit",
        "fit_r_squared": 0.98,
    }
    matcher._pipeline_seed = seed
    writes = []
    matcher._at = lambda energy, stage: writes.append((energy, stage)) or seed

    result = matcher._run_center_lock_stage(
        2190.0,
        2100.0,
        2300.0,
        {"strategy": "secant_dispersion"},
    )

    assert result.ok
    assert writes == [(2190.0, "track")]


def test_invalid_current_measurement_does_not_write_energy_or_retry_restore(monkeypatch):
    matcher = RFPhaseEnergyMatcher(
        flag_pv_obj=_ImagePV(np.ones((5, 6))),
        flag_pixel=(6, 5),
        bend_pv="TEST:ENERGY",
        design_eta_m=0.75,
        pixel_width_mm=0.1,
        pipeline=["center_lock"],
    )
    measurements = []

    monkeypatch.setattr(
        "half_linac.src.apps.rf_phase_scan.energy_match_tuner.caget",
        lambda _pv: 2200.0,
    )
    matcher._measure = lambda energy, stage: (
        measurements.append((energy, stage)) or None
    )
    matcher._set_energy = lambda *_args, **_kwargs: pytest.fail(
        "An invalid current measurement must not write the energy PV."
    )

    result = matcher.run(2150.0, 2250.0, start_energy=2210.0)

    assert result is None
    assert matcher.get_last_status() == "MEASUREMENT_FAILED"
    assert measurements == [
        (2200.0, "track"),
        (2200.0, "track_retry"),
    ]
