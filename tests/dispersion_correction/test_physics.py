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
