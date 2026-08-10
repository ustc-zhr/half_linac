import math
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jitter_analysis.gui.series_logic import (
    filtered_series_payload,
    series_sample_indices,
    series_step_indices,
)


def test_series_sample_indices_supports_dict_metadata_and_defaults():
    assert series_sample_indices({"sample_indices": [4, 5]}, [1.0, 2.0]) == [4, 5]
    assert series_sample_indices({"sample_indices": ("6", "7")}, [1.0, 2.0]) == [6, 7]
    assert series_sample_indices({}, [1.0, 2.0]) == [0, 1]
    assert series_sample_indices({}, [1.0, 2.0], expected_length=3) == [0, 1, 2]


def test_series_sample_indices_supports_legacy_row_metadata():
    metadata = [{"sample_index": "10"}, {}, {"sample_index": 12}]

    assert series_sample_indices(metadata, [1.0, 2.0, 3.0]) == [10, 1, 12]


def test_series_step_indices_supports_dict_and_legacy_metadata():
    assert series_step_indices({"step_indices": [None, 2]}, [1.0, 2.0]) == [None, 2]
    assert series_step_indices({"step_indices": ("1", "2")}, [1.0, 2.0]) == ["1", "2"]
    assert series_step_indices({}, [1.0, 2.0]) == [None, None]
    assert series_step_indices([{"step_index": 0}, {"step_index": 1}], [1.0, 2.0]) == [0, 1]


def test_filtered_series_payload_keeps_alignment_with_nan_values_when_filter_disabled():
    payload = filtered_series_payload(
        [1.0, float("nan"), 3.0],
        [10, 11, 12],
        [0, 0, 1],
        outlier_filter_enabled=False,
        outlier_filter_threshold=4.0,
    )

    assert payload["raw_values"] == [1.0, 3.0]
    assert payload["filtered_values"] == [1.0, 3.0]
    assert payload["filtered_sample_indices"] == [10, 12]
    assert payload["filtered_step_indices"] == [0, 1]
    assert payload["filtered_raw_indices"] == [0, 2]
    assert math.isnan(payload["aligned_values"][1])
    assert payload["raw_count"] == 2
    assert payload["removed_count"] == 0
    assert payload["filter_mode"] == "off"


def test_filtered_series_payload_removes_outliers_from_aligned_values():
    payload = filtered_series_payload(
        [1.0, 1.1, 0.9, 1.05, 25.0],
        [0, 1, 2, 3, 4],
        [None, None, None, None, None],
        outlier_filter_enabled=True,
        outlier_filter_threshold=4.0,
    )

    assert payload["filtered_values"] == [1.0, 1.1, 0.9, 1.05]
    assert payload["filtered_raw_indices"] == [0, 1, 2, 3]
    assert math.isnan(payload["aligned_values"][4])
    assert payload["removed_count"] == 1
    assert payload["filter_mode"] == "robust_z_score"


def test_filtered_series_payload_handles_all_nan_values():
    payload = filtered_series_payload(
        [float("nan")],
        [3],
        [None],
        outlier_filter_enabled=True,
        outlier_filter_threshold=4.0,
    )

    assert payload["raw_values"] == []
    assert payload["filtered_values"] == []
    assert math.isnan(payload["aligned_values"][0])
    assert payload["raw_count"] == 0
    assert payload["filter_mode"] == "robust_z_score_noop"
