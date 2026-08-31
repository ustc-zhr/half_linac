from datetime import datetime

import pytest

from jitter_analysis.analysis.influence import compute_random_multi_knob_influence
from jitter_analysis.domain.types import MultiKnobStepRecord, SampleRecord


def _step(index: int, k1: float, k2: float, response: float) -> MultiKnobStepRecord:
    timestamp = datetime(2026, 1, 1)
    return MultiKnobStepRecord(
        step_index=index,
        target_values={"k1": k1, "k2": k2},
        readback_values={"k1": k1, "k2": k2},
        started_at=timestamp,
        samples=[
            SampleRecord("pv", response - 0.1, timestamp, step_index=index),
            SampleRecord("pv", response + 0.1, timestamp, step_index=index),
        ],
    )


def test_random_influence_recovers_multivariable_linear_response():
    steps = []
    points = [
        (-2.0, -1.0),
        (-2.0, 1.0),
        (-1.0, -2.0),
        (-1.0, 2.0),
        (0.0, -1.0),
        (0.0, 1.0),
        (1.0, -2.0),
        (1.0, 2.0),
        (2.0, -1.0),
        (2.0, 1.0),
    ]
    for index, (k1, k2) in enumerate(points):
        steps.append(_step(index, k1, k2, 4.0 + 3.0 * k1 - 2.0 * k2))

    rows = compute_random_multi_knob_influence(steps, knob_ids=["k1", "k2"])

    assert len(rows) == 1
    row = rows[0]
    coefficients = {item.knob_id: item for item in row.coefficients}
    assert row.point_count == len(points)
    assert row.intercept == pytest.approx(4.0)
    assert row.r_squared == pytest.approx(1.0)
    assert coefficients["k1"].coefficient == pytest.approx(3.0)
    assert coefficients["k2"].coefficient == pytest.approx(-2.0)
    assert coefficients["k1"].standardized_coefficient > 0.0
    assert coefficients["k2"].standardized_coefficient < 0.0


def test_random_influence_uses_target_when_readback_is_missing():
    steps = [_step(index, float(index), (-1.0) ** index, 2.0 * index) for index in range(8)]
    for step in steps:
        step.readback_values["k1"] = None

    rows = compute_random_multi_knob_influence(steps, knob_ids=["k1", "k2"])

    assert rows
    assert rows[0].point_count == 8
