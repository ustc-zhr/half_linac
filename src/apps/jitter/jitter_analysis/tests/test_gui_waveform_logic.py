from pathlib import Path
from types import SimpleNamespace
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jitter_analysis.gui.waveform_logic import (
    group_waveform_index_entries,
    has_waveform_data,
    waveform_counts_signature,
    waveform_ids_in_current_run,
    waveform_max_length_hint,
    waveform_record_counts,
)


def _record(values, batch_index=None):
    return SimpleNamespace(values=values, batch_index=batch_index)


def _entry(pv_id, length=0, batch_index=None):
    return SimpleNamespace(pv_id=pv_id, length=length, batch_index=batch_index)


def test_has_waveform_data_detects_records_or_index_entries():
    assert has_waveform_data({}, {}) is False
    assert has_waveform_data({"scope_a": []}, {}) is False
    assert has_waveform_data({"scope_a": [_record([1.0])]}, {}) is True
    assert has_waveform_data({}, {"scope_a": [_entry("scope_a")]}) is True


def test_waveform_ids_prefers_explicit_then_inferred_then_selected_ids():
    assert waveform_ids_in_current_run(
        {"waveform_object_ids": [" scope_a ", "", "scope_b"]},
        {"ignored_record": []},
        {"ignored_index": []},
        ["selected"],
    ) == ["scope_a", "scope_b"]
    assert waveform_ids_in_current_run(
        {},
        {"scope_a": []},
        {"scope_b": [], "scope_a": []},
        ["selected"],
    ) == ["scope_a", "scope_b"]
    assert waveform_ids_in_current_run({}, {}, {}, ["selected"]) == ["selected"]


def test_waveform_record_counts_prefers_record_counts_over_index_counts():
    counts = waveform_record_counts(
        ["scope_a", "scope_b", "scope_c"],
        {"scope_a": [_record([1.0]), _record([2.0])], "scope_b": []},
        {"scope_a": [_entry("scope_a")], "scope_b": [_entry("scope_b")], "scope_c": []},
    )

    assert counts == {"scope_a": 2, "scope_b": 0, "scope_c": 0}


def test_waveform_max_length_hint_uses_records_index_and_analysis_result():
    assert waveform_max_length_hint(
        {"scope_a": [_record([1.0, 2.0])]},
        {"scope_b": [_entry("scope_b", length=4)]},
        {"max_waveform_length": 6},
    ) == 6


def test_waveform_counts_signature_uses_tail_record_or_index_batch():
    signature = waveform_counts_signature(
        ["scope_a", "scope_b", "scope_c"],
        {"scope_a": [_record([1.0], batch_index=3)]},
        {"scope_b": [_entry("scope_b", batch_index=None), _entry("scope_b", batch_index=7)]},
    )

    assert signature == [
        ("scope_a", 1, 3),
        ("scope_b", 2, 7),
        ("scope_c", 0, -1),
    ]


def test_group_waveform_index_entries_preserves_entry_order():
    first = _entry("scope_a", length=1)
    second = _entry("scope_b", length=2)
    third = _entry("scope_a", length=3)

    assert group_waveform_index_entries([first, second, third]) == {
        "scope_a": [first, third],
        "scope_b": [second],
    }
