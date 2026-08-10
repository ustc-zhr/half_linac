from dataclasses import dataclass, field
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jitter_analysis.gui.scan_logic import (
    collect_random_knob_ranges,
    generate_random_targets,
    generate_values_by_points,
    generate_values_by_step,
    parse_manual_scan_values,
    random_preview_payload,
    resolve_random_seed,
    single_knob_preview_payload,
)


@dataclass
class _Limits:
    low: float = -1.0
    high: float = 1.0


@dataclass
class _Knob:
    id: str
    name: str = "K1"
    limits: _Limits = field(default_factory=_Limits)


def test_resolve_random_seed_uses_default_for_empty_text():
    assert resolve_random_seed("", 123) == (123, True)
    assert resolve_random_seed("  ", 123) == (123, True)


def test_resolve_random_seed_parses_explicit_integer():
    assert resolve_random_seed("42", 123) == (42, False)


def test_resolve_random_seed_rejects_non_integer_text():
    with pytest.raises(ValueError, match="Seed must be an integer"):
        resolve_random_seed("4.2", 123)


def test_collect_random_knob_ranges_returns_enabled_selected_rows_as_floats():
    knob = _Knob("k1", "K1", _Limits(-2.0, 2.0))
    rows = {
        "k1": {"enabled": True, "low_text": "-0.5", "high_text": "0.75"},
        "k2": {"enabled": True, "low_text": "-1.0", "high_text": "1.0"},
        "k3": {"enabled": False, "low_text": "", "high_text": ""},
    }

    assert collect_random_knob_ranges([knob], rows) == [
        {"knob": knob, "low": -0.5, "high": 0.75}
    ]


def test_collect_random_knob_ranges_rejects_invalid_rows():
    knob = _Knob("k1", "K1", _Limits(-1.0, 1.0))

    with pytest.raises(ValueError, match="Low/High must be set for K1"):
        collect_random_knob_ranges([knob], {"k1": {"enabled": True, "low_text": "", "high_text": "1"}})
    with pytest.raises(ValueError, match="Low/High must be numeric for K1"):
        collect_random_knob_ranges([knob], {"k1": {"enabled": True, "low_text": "bad", "high_text": "1"}})
    with pytest.raises(ValueError, match="Low must be <= High for K1"):
        collect_random_knob_ranges([knob], {"k1": {"enabled": True, "low_text": "1", "high_text": "0"}})
    with pytest.raises(ValueError, match="must stay within"):
        collect_random_knob_ranges([knob], {"k1": {"enabled": True, "low_text": "-2", "high_text": "0"}})
    with pytest.raises(ValueError, match="Enable at least one knob row"):
        collect_random_knob_ranges([knob], {"k1": {"enabled": False, "low_text": "-1", "high_text": "1"}})


def test_parse_manual_scan_values_accepts_commas_semicolons_and_newlines():
    assert parse_manual_scan_values("-0.1, 0; 0.1\n0.2") == [-0.1, 0.0, 0.1, 0.2]


def test_parse_manual_scan_values_rejects_empty_and_non_numeric_values():
    with pytest.raises(ValueError, match="Enter one or more"):
        parse_manual_scan_values(" , ; ")
    with pytest.raises(ValueError, match="must be numeric"):
        parse_manual_scan_values("0.1, bad")


def test_generate_values_by_step_includes_stop_for_ascending_and_descending_ranges():
    assert generate_values_by_step(0.0, 0.5, 0.2) == [0.0, 0.2, 0.4, 0.5]
    assert generate_values_by_step(0.5, 0.0, 0.2) == [0.5, 0.3, 0.09999999999999998, 0.0]


def test_generate_values_by_points_handles_single_and_multiple_points():
    assert generate_values_by_points(1.0, 2.0, 1) == [1.0]
    assert generate_values_by_points(1.0, 2.0, 3) == [1.0, 1.5, 2.0]


def test_generate_random_targets_is_seeded_and_clips_normal_values():
    knob_ranges = [
        {"knob": _Knob("k1"), "low": -1.0, "high": 1.0},
        {"knob": _Knob("k2"), "low": 2.0, "high": 2.0},
    ]

    first = generate_random_targets(knob_ranges, "normal_clipped", 3, 123)
    second = generate_random_targets(knob_ranges, "normal_clipped", 3, 123)

    assert first == second
    assert len(first) == 3
    assert all(-1.0 <= row["k1"] <= 1.0 for row in first)
    assert all(row["k2"] == 2.0 for row in first)


def test_generate_random_targets_rejects_invalid_inputs():
    knob_ranges = [{"knob": _Knob("k1"), "low": 0.0, "high": 1.0}]
    with pytest.raises(ValueError, match="Num points"):
        generate_random_targets(knob_ranges, "uniform", 0, 1)
    with pytest.raises(ValueError, match="Unsupported random distribution"):
        generate_random_targets(knob_ranges, "bad", 1, 1)


def test_random_preview_payload_formats_preview_lines_summary_and_detail():
    knob_ranges = [
        {"knob": _Knob("k1", "K1"), "low": -1.0, "high": 1.0},
        {"knob": _Knob("k2", "K2"), "low": 2.0, "high": 3.0},
    ]
    target_steps = [
        {"k1": 0.1234567, "k2": 2.0},
        {"k1": -0.5, "k2": 2.75},
        {"k1": 1.0, "k2": 3.0},
    ]

    payload = random_preview_payload(
        knob_ranges,
        target_steps,
        distribution="uniform",
        seed=42,
        preview_limit=2,
    )

    assert payload["lines"] == [
        "001: K1=0.123457, K2=2",
        "002: K1=-0.5, K2=2.75",
        "... 1 more point(s)",
    ]
    assert payload["summary"] == "3 random point(s) across 2 knob(s)  |  distribution=uniform  |  seed=42"
    assert payload["detail"] == "K1[-1, 1], K2[2, 3]"


def test_single_knob_preview_payload_formats_summary_and_optional_center_detail():
    payload = single_knob_preview_payload(
        [-0.2, 0.0, 0.2],
        "K1",
        "mm",
        "manual",
    )

    assert payload == {
        "summary": "3 point(s) for K1: -0.2 to 0.2 mm  |  first step 0.2",
        "detail": "",
    }

    symmetric_payload = single_knob_preview_payload(
        [-0.1, 0.0, 0.1],
        "K1",
        "mm",
        "symmetric_points",
        center=1.234567,
    )

    assert symmetric_payload == {
        "summary": "3 point(s) for K1: -0.1 to 0.1 mm  |  first step 0.1",
        "detail": "Preview center from K1: 1.23457 mm",
    }

    missing_center_payload = single_knob_preview_payload(
        [0.0],
        "K1",
        "mm",
        "symmetric_points",
        center=None,
    )

    assert missing_center_payload == {
        "summary": "1 point(s) for K1: 0 to 0 mm",
        "detail": "",
    }
