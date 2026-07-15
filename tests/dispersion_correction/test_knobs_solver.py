import numpy as np

from half_linac.src.apps.dispersion_correction.knobs import SymmetricKnobSet
from half_linac.src.apps.dispersion_correction.models import KnobConfig
from half_linac.src.apps.dispersion_correction.solver import solve_bounded_correction


def test_symmetric_knob_device_mapping_and_limits() -> None:
    knobs = SymmetricKnobSet(
        [
            KnobConfig("Q1_sym", {"Q1L": 1.0, "Q1R": 1.0}, 0.002, 0.03),
            KnobConfig("Q2_sym", {"Q2L": 1.0, "Q2R": 1.0}, 0.002, 0.03),
        ],
        {"Q1_sym": 0.0, "Q2_sym": 0.0},
    )

    values = {"Q1_sym": 0.004, "Q2_sym": -0.003}

    assert knobs.device_deltas(values) == {"Q1L": 0.004, "Q1R": 0.004, "Q2L": -0.003, "Q2R": -0.003}
    assert knobs.within_total_limits(values)
    assert not knobs.within_total_limits({"Q1_sym": 0.031, "Q2_sym": 0.0})
    np.testing.assert_allclose(knobs.limits(), [0.03, 0.03])
    np.testing.assert_allclose(knobs.step_limits(0.25), [0.0075, 0.0075])


def test_svd_solver_full_rank_solution() -> None:
    response = np.asarray([[1.0, 0.0], [0.0, 2.0]])
    dispersion = np.asarray([1.0, 4.0])

    delta, singular_values, condition = solve_bounded_correction(
        response,
        dispersion,
        svd_cut=1.0e-6,
        gain=1.0,
        limits=np.asarray([10.0, 10.0]),
        max_step_fraction=1.0,
        current_values=np.zeros(2),
        initial_values=np.zeros(2),
        regularization=0.0,
    )

    np.testing.assert_allclose(delta, [-1.0, -2.0])
    np.testing.assert_allclose(singular_values, [20.0, 10.0])
    assert condition == 2.0


def test_svd_solver_handles_rank_deficient_matrix() -> None:
    response = np.asarray([[1.0, 1.0], [2.0, 2.0]])
    dispersion = np.asarray([3.0, 6.0])

    delta, singular_values, condition = solve_bounded_correction(
        response,
        dispersion,
        svd_cut=1.0e-3,
        gain=1.0,
        limits=np.asarray([10.0, 10.0]),
        max_step_fraction=1.0,
        current_values=np.zeros(2),
        initial_values=np.zeros(2),
        regularization=1.0e-3,
    )

    assert np.all(np.isfinite(delta))
    assert singular_values[1] < 1.0e-12
    assert condition > 1.0e12


def test_bounded_solver_handles_three_knobs_and_remaining_limits() -> None:
    response = np.asarray([[1.0, 0.0, 1.0], [0.0, 2.0, 1.0]])
    dispersion = np.asarray([-4.0, -4.0])
    limits = np.asarray([1.0, 0.5, 2.0])
    current = np.asarray([0.9, 0.0, 0.0])

    delta, singular_values, _ = solve_bounded_correction(
        response,
        dispersion,
        svd_cut=1.0e-6,
        gain=0.5,
        limits=limits,
        max_step_fraction=0.5,
        current_values=current,
        initial_values=np.zeros(3),
        regularization=1.0e-3,
    )

    step_limits = limits * 0.5
    assert singular_values.size == 2
    assert np.all(np.abs(delta) <= step_limits + 1.0e-12)
    assert delta[0] <= 0.1 + 1.0e-12
    assert np.linalg.norm(response @ delta + 0.5 * dispersion) < np.linalg.norm(0.5 * dispersion)
