from datetime import datetime, timedelta
import math
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


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
from jitter_analysis.domain.types import WaveformIndexEntry, WaveformRecord
from jitter_analysis.gui import waveform_analysis as waveform_analysis_module


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


@pytest.mark.skipif(
    waveform_analysis_module.QtCore is None,
    reason="PyQt5 is required for WaveformAnalysisWorker tests",
)
def test_waveform_worker_streams_variable_lengths_and_pairs_delay_by_batch():
    origin = datetime(2026, 1, 1)

    def waveform(
        pv_id,
        batch_index,
        values,
        *,
        interval=0.0,
        connected=True,
    ):
        return WaveformRecord(
            pv_id=pv_id,
            values=list(values),
            timestamp=origin + timedelta(seconds=batch_index),
            waveform_sample_interval_sec=interval,
            connected=connected,
            batch_index=batch_index,
        )

    worker = waveform_analysis_module.WaveformAnalysisWorker(
        waveform_ids=["scope_a", "scope_b"],
        labels_by_pv={"scope_a": "Scope A", "scope_b": "Scope B"},
        request_signature=("worker-test",),
        baseline_start_index=0,
        baseline_stop_index=2,
        roi_start_index=0,
        roi_stop_index=100,
        polarity="auto",
        primary_pv_id="scope_a",
        secondary_pv_id="scope_b",
        in_memory_records_by_pv={
            "scope_a": [
                waveform("scope_a", 0, [0, 0, 1, 2, 1, 0]),
                waveform("scope_a", 1, [0, 0, 2, 0], interval=1.0e-9),
                waveform("scope_a", 2, [math.nan] * 4, interval=1.0e-9),
                waveform("scope_a", 3, [0, 1, 0], connected=False),
            ],
            "scope_b": [
                waveform("scope_b", 0, [0, 0, 0, 1, 2, 1]),
                waveform(
                    "scope_b",
                    1,
                    [0, 0, 0, 2],
                    interval=2.0e-9,
                ),
                waveform("scope_b", 5, [0, 0, 1, 2, 1, 0]),
            ],
        },
    )

    payload = worker._analyze()
    row_a = next(row for row in payload["feature_rows"] if row["pv_id"] == "scope_a")

    assert payload["record_counts"] == {"scope_a": 4, "scope_b": 3}
    assert row_a["valid_count"] == 2
    assert row_a["quality"]["no_finite"] == 1
    assert row_a["quality"]["disconnected"] == 1
    assert row_a["quality"]["skipped"] == 2
    assert row_a["quality"]["sample_interval_variation"] == 1
    assert row_a["aggregate"]["valid_counts"] == [2, 2, 2, 2, 1, 1]
    assert payload["delay_sample_indices"] == [0]
    assert payload["delay_unit"] == "samples"
    assert payload["delay_series"][0] > 0.0
    assert len(payload["delay_correlations"]) == 1
    assert payload["delay_matched_count"] == 1
    assert payload["delay_skipped_count"] == 4
    assert "Skipped 4" in payload["delay_summary"]


@pytest.mark.skipif(
    waveform_analysis_module.QtCore is None,
    reason="PyQt5 is required for WaveformAnalysisWorker tests",
)
def test_saved_run_worker_consumes_one_waveform_iterator():
    origin = datetime(2026, 1, 1)
    entries = [
        WaveformIndexEntry(
            pv_id="scope_a",
            timestamp=origin + timedelta(seconds=batch_index),
            waveform_sample_interval_sec=1.0e-9,
            offset=batch_index * 4,
            length=4,
            batch_index=batch_index,
        )
        for batch_index in range(3)
    ]
    iterator_calls = []

    def waveform_iterator(requested_entries):
        requested_entries = list(requested_entries)
        iterator_calls.append(requested_entries)
        for entry in requested_entries:
            yield WaveformRecord(
                pv_id=entry.pv_id,
                values=[0.0, 1.0 + entry.batch_index, 0.0, 0.0],
                timestamp=entry.timestamp,
                waveform_sample_interval_sec=entry.waveform_sample_interval_sec,
                batch_index=entry.batch_index,
            )

    worker = waveform_analysis_module.WaveformAnalysisWorker(
        waveform_ids=["scope_a"],
        labels_by_pv={"scope_a": "Scope A"},
        request_signature=("saved-run",),
        baseline_start_index=0,
        baseline_stop_index=1,
        roi_start_index=0,
        roi_stop_index=4,
        primary_pv_id="scope_a",
        secondary_pv_id="",
        waveform_entries_by_pv={"scope_a": entries},
        waveform_iterator=waveform_iterator,
    )

    payload = worker._analyze()

    assert len(iterator_calls) == 1
    assert iterator_calls[0] == entries
    assert payload["record_counts"] == {"scope_a": 3}
    assert payload["feature_rows"][0]["sample_indices"] == [0, 1, 2]
