import numpy as np

from half_linac.src.apps.dispersion_correction.models import BPMReading
from half_linac.src.apps.dispersion_correction.physics import compute_effective_dispersion, momentum_delta, robust_average


def test_effective_dispersion_uses_two_sided_delta() -> None:
    names = ("BPM01", "BPM02")
    delta = 1.0e-4
    expected = np.asarray([12.0, -8.0])
    plus = BPMReading(names, expected * delta, np.zeros(2), np.ones(2, dtype=bool))
    minus = BPMReading(names, -expected * delta, np.zeros(2), np.ones(2, dtype=bool))

    measurement = compute_effective_dispersion(names, plus, minus, delta)

    np.testing.assert_allclose(measurement.values_mm, expected)
    assert measurement.valid.tolist() == [True, True]


def test_effective_vertical_dispersion_uses_y_readings() -> None:
    names = ("BPM01", "BPM02")
    delta = 2.0e-4
    expected = np.asarray([15.0, -6.0])
    plus = BPMReading(
        names,
        np.asarray([100.0, 200.0]),
        expected * delta,
        np.ones(2, dtype=bool),
    )
    minus = BPMReading(
        names,
        np.asarray([-100.0, -200.0]),
        -expected * delta,
        np.ones(2, dtype=bool),
    )

    measurement = compute_effective_dispersion(
        names,
        plus,
        minus,
        delta,
        plane="y",
    )

    assert measurement.plane == "y"
    np.testing.assert_allclose(measurement.values_mm, expected)


def test_effective_dispersion_reports_residual_to_nonzero_target() -> None:
    names = ("BPM01", "BPM02")
    delta = 1.0e-4
    measured = np.asarray([12.0, -8.0])
    target = np.asarray([10.0, -5.0])
    plus = BPMReading(names, measured * delta, np.zeros(2), np.ones(2, dtype=bool))
    minus = BPMReading(names, -measured * delta, np.zeros(2), np.ones(2, dtype=bool))

    measurement = compute_effective_dispersion(
        names,
        plus,
        minus,
        delta,
        target_values_mm=target,
    )

    np.testing.assert_allclose(measurement.target_values_mm, target)
    np.testing.assert_allclose(measurement.residual_values_mm, [2.0, -3.0])
    assert measurement.measured_rms_mm == np.sqrt(104.0)
    assert measurement.rms_mm == np.sqrt(6.5)


def test_monitor_bpms_are_measured_but_excluded_from_residual_rms() -> None:
    names = ("BPM_MON", "BPM_TARGET")
    delta = 1.0e-4
    measured = np.asarray([1000.0, 4.0])
    plus = BPMReading(
        names,
        measured * delta,
        np.zeros(2),
        np.ones(2, dtype=bool),
    )
    minus = BPMReading(
        names,
        -measured * delta,
        np.zeros(2),
        np.ones(2, dtype=bool),
    )

    measurement = compute_effective_dispersion(
        names,
        plus,
        minus,
        delta,
        target_values_mm=[0.0, 1.0],
        target_mask=[False, True],
    )

    np.testing.assert_allclose(measurement.values_mm, measured)
    assert measurement.target_mask.tolist() == [False, True]
    assert measurement.correction_valid.tolist() == [False, True]
    assert measurement.rms_mm == 3.0


def test_momentum_delta_uses_configured_delta_directly() -> None:
    assert momentum_delta(1.0e-4) == 1.0e-4


def test_robust_average_filters_invalid_bpm_samples() -> None:
    names = ("BPM01", "BPM02")
    readings = [
        BPMReading(names, [1.0, 100.0], [0.0, 0.0], [True, False], charge=1.0, loss=2.0),
        BPMReading(names, [3.0, 4.0], [0.0, 0.0], [True, True], charge=1.2, loss=2.2),
    ]

    averaged = robust_average(readings)

    np.testing.assert_allclose(averaged.x_mm, [2.0, 4.0])
    assert averaged.valid.tolist() == [True, True]
    assert averaged.charge == 1.1
