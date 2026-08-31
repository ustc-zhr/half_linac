from datetime import datetime
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jitter_analysis.analysis.sensitivity import compute_single_knob_sensitivity
from jitter_analysis.domain.types import SampleRecord, ScanStepRecord


def _step(index: int, target: float, readback: float | None, value: float) -> ScanStepRecord:
    return ScanStepRecord(
        step_index=index,
        target_value=target,
        readback_value=readback,
        started_at=datetime.fromisoformat("2026-05-20T10:00:00"),
        samples=[
            SampleRecord(
                pv_id="obj",
                value=value,
                timestamp=datetime.fromisoformat("2026-05-20T10:00:00"),
                step_index=index,
            )
        ],
    )


def test_single_knob_sensitivity_groups_repeated_knob_positions_before_fit():
    rows = compute_single_knob_sensitivity(
        [
            _step(0, 0.0, 0.01, 1.0),
            _step(1, 1.0, 1.02, 3.0),
            _step(2, 0.0, -0.01, 1.2),
            _step(3, 2.0, 2.01, 5.0),
            _step(4, 1.0, 0.98, 3.4),
        ],
        axis_source="readback",
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.point_count == 3
    assert row.raw_point_count == 5
    assert row.knob_values.tolist() == pytest.approx([0.0, 1.0, 2.01])
    assert row.response_values.tolist() == pytest.approx([1.1, 3.2, 5.0])
    assert row.repeat_counts.tolist() == [2, 2, 1]
    assert row.response_std_values.tolist() == pytest.approx(
        [0.02 ** 0.5, 0.08 ** 0.5, 0.0]
    )


def test_single_knob_sensitivity_uses_target_values_for_target_axis_groups():
    rows = compute_single_knob_sensitivity(
        [
            _step(0, 1.0, 0.9, 2.0),
            _step(1, 1.0, 1.1, 4.0),
            _step(2, 2.0, 2.2, 6.0),
        ],
        axis_source="target",
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.knob_values.tolist() == pytest.approx([1.0, 2.0])
    assert row.response_values.tolist() == pytest.approx([3.0, 6.0])
    assert row.raw_point_count == 3
