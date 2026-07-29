from dataclasses import replace

import numpy as np
import pytest

from half_linac.src.apps.dispersion_correction.config import load_config
from half_linac.src.apps.dispersion_correction.machine.offline import OfflineMachine
from half_linac.src.apps.dispersion_correction.recommendation import (
    build_correction_recommendation,
)
from half_linac.src.apps.dispersion_correction.solver import (
    automatic_response_block_reason,
    rank_reduced_response_warning,
    response_mode_counts,
)
from half_linac.src.apps.dispersion_correction.workflow import AchromatWorkflow


def staged_fixture():
    config = load_config(
        "tests/dispersion_correction/fixtures/irfel_achromat.mock.json"
    )
    config = replace(
        config,
        measurement=replace(
            config.measurement,
            samples_per_step=1,
            final_samples=1,
            settle_time_s=0.0,
            sample_interval_s=0.0,
        ),
    )
    machine = OfflineMachine(config)
    workflow = AchromatWorkflow(config, machine=machine)
    response = workflow.build_response_matrix()
    return config, machine, workflow, response


def test_recommendation_is_pure_bounded_prediction() -> None:
    config, machine, _workflow, response = staged_fixture()
    before_knobs = machine.get_knobs(response.knob_names)

    recommendation = build_correction_recommendation(
        config,
        response.measurement,
        response,
    )

    assert recommendation.ready
    assert recommendation.predicted_rms_mm < response.measurement.rms_mm
    assert machine.get_knobs(response.knob_names) == before_knobs
    for knob in config.knobs:
        assert abs(recommendation.delta_knobs[knob.name]) <= (
            knob.limit * config.solver.max_step_fraction + 1.0e-15
        )


def test_rank_reduced_response_uses_retained_svd_mode() -> None:
    config, _machine, _workflow, _response = staged_fixture()
    logs = []
    machine = OfflineMachine(
        config,
        initial_dispersion_mm=[86.0, 112.0],
        response_matrix=[
            [-9500.0, -19000.0],
            [-11800.0, -23600.0],
        ],
    )
    workflow = AchromatWorkflow(config, machine=machine, log_callback=logs.append)

    response = workflow.build_response_matrix()
    recommendation = build_correction_recommendation(
        config,
        response.measurement,
        response,
    )

    assert recommendation.ready
    assert recommendation.predicted_rms_mm < response.measurement.rms_mm
    assert np.count_nonzero(
        response.singular_values / np.max(response.singular_values)
        > config.solver.svd_cut
    ) == 1
    assert response_mode_counts(response, config.solver.svd_cut) == (1, 2, 2, 2)
    assert automatic_response_block_reason(response, config.solver.svd_cut) is None
    assert "act only on the controllable dispersion component" in (
        rank_reduced_response_warning(response, config.solver.svd_cut) or ""
    )
    assert any("rank-reduced: retained 1/2" in message for message in logs)
    assert any("Measured response matrix:" in message for message in logs)


def test_automatic_correction_uses_rank_reduced_response_with_verification() -> None:
    config, _machine, _workflow, _response = staged_fixture()
    machine = OfflineMachine(
        config,
        initial_dispersion_mm=[86.0, 112.0],
        response_matrix=[
            [-9500.0, -19000.0],
            [-11800.0, -23600.0],
        ],
    )
    initial_knobs = machine.get_knobs(("Q13_Q16_sym", "Q14_Q15_sym"))

    result = AchromatWorkflow(config, machine=machine).run()

    assert result.success
    assert result.response is not None
    assert any(step.accepted for step in result.steps)
    assert machine.get_knobs(tuple(initial_knobs)) != initial_knobs


def test_zero_response_still_fails_quality_check() -> None:
    config, _machine, _workflow, _response = staged_fixture()
    machine = OfflineMachine(
        config,
        response_matrix=np.zeros((len(config.measurement_bpms), len(config.knobs))),
    )

    with pytest.raises(RuntimeError, match="no SVD modes were retained"):
        AchromatWorkflow(config, machine=machine).build_response_matrix()


def test_reviewed_recommendation_applies_once_and_remeasures() -> None:
    config, _machine, workflow, response = staged_fixture()
    recommendation = build_correction_recommendation(
        config,
        response.measurement,
        response,
    )

    result = workflow.apply_recommendation(recommendation)

    assert result.success
    assert result.steps[0].accepted
    assert result.steps[0].measurement_before is recommendation.measurement
    assert result.steps[0].measurement_after is result.final
    assert result.steps[0].knobs_before == result.initial_knobs
    assert result.steps[0].knobs_trial == result.final_knobs
    assert result.steps[0].device_values_before
    assert result.steps[0].device_values_trial
    assert not result.steps[0].restored
    assert result.final.rms_mm < result.initial.rms_mm
    assert result.response is response


def test_recommendation_rejects_stale_response_knobs() -> None:
    config, _machine, _workflow, response = staged_fixture()
    stale = replace(response, knob_names=tuple(reversed(response.knob_names)))

    with pytest.raises(ValueError, match="Response knobs"):
        build_correction_recommendation(
            config,
            response.measurement,
            stale,
        )


def test_monitor_bpms_are_retained_but_excluded_from_response_solve() -> None:
    config, _machine, _workflow, _response = staged_fixture()
    config = replace(
        config,
        monitor_bpms=("BPM_MON",),
        backend=replace(
            config.backend,
            options={
                **config.backend.options,
                "model": {},
            },
        ),
    )
    machine = OfflineMachine(
        config,
        initial_dispersion_mm=[500.0, 90.0, 110.0],
        response_matrix=[
            [1.0e7, -1.0e7],
            [-9000.0, -5000.0],
            [-11000.0, 6000.0],
        ],
    )
    workflow = AchromatWorkflow(config, machine=machine)
    response = workflow.build_response_matrix()
    recommendation = build_correction_recommendation(
        config,
        response.measurement,
        response,
    )

    assert response.bpm_names == ("BPM_MON", *config.target_bpms)
    assert response.measurement.target_mask.tolist() == [False, True, True]
    assert recommendation.valid.tolist() == [False, True, True]
    assert recommendation.predicted_values_mm.shape == (3,)
    assert recommendation.predicted_rms_mm < response.measurement.rms_mm
