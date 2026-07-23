from dataclasses import replace

import pytest

from half_linac.src.apps.dispersion_correction.config import load_config
from half_linac.src.apps.dispersion_correction.machine.offline import OfflineMachine
from half_linac.src.apps.dispersion_correction.recommendation import (
    build_correction_recommendation,
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
