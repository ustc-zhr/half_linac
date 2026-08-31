from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jitter_analysis.domain.types import RunMetadata, RunMode, RunResult, RunStatus
from jitter_analysis.gui.run_logic import (
    current_record_count,
    has_run_data,
    loaded_run_object_count_hint,
    loaded_run_parameter_updates,
    resolve_loaded_run_selection,
    run_browser_scope_kind,
    validate_loaded_run_config,
)


def _config(object_ids=(), knob_ids=()):
    return SimpleNamespace(
        objects=[SimpleNamespace(id=item) for item in object_ids],
        knobs=[SimpleNamespace(id=item) for item in knob_ids],
    )


def _result(mode: RunMode, details: dict[str, object]) -> RunResult:
    return RunResult(
        metadata=RunMetadata(
            run_id="run",
            mode=mode,
            created_at=datetime(2026, 7, 1, 12, 0, 0),
        ),
        status=RunStatus.COMPLETED,
        details=details,
    )


def test_run_browser_scope_kind_detects_root_and_single_run_dirs(tmp_path):
    assert run_browser_scope_kind(tmp_path / "missing") == "root"
    assert run_browser_scope_kind(tmp_path) == "root"

    (tmp_path / "metadata.json").write_text("{}", encoding="utf-8")

    assert run_browser_scope_kind(tmp_path) == "single_run"


def test_current_record_count_prefers_materialized_records_then_stored_count():
    assert current_record_count([object(), object()], 10) == 2
    assert current_record_count([], 10) == 10


def test_has_run_data_uses_record_count_or_series_values():
    assert has_run_data(1, {}) is True
    assert has_run_data(0, {"pv1": [], "pv2": [1.0]}) is True
    assert has_run_data(0, {"pv1": []}) is False


def test_validate_loaded_run_config_rejects_missing_config():
    result = _result(RunMode.TIMED_ACQUISITION, {})

    assert validate_loaded_run_config(result, None) == (False, "No PV library is loaded.")


def test_validate_loaded_run_config_accepts_matching_timed_run():
    result = _result(
        RunMode.TIMED_ACQUISITION,
        {"target_object_ids": [" bpm_x ", "scope_a", ""]},
    )

    assert validate_loaded_run_config(result, _config(object_ids=["bpm_x", "scope_a"])) == (True, "")


def test_validate_loaded_run_config_reports_missing_read_and_single_knob_ids():
    result = _result(
        RunMode.KNOB_SCAN,
        {"target_object_ids": ["bpm_x", "missing_read"], "knob_id": "missing_knob"},
    )

    ok, message = validate_loaded_run_config(result, _config(object_ids=["bpm_x"], knob_ids=["k1"]))

    assert ok is False
    assert "read PV IDs: missing_read" in message
    assert "control PV IDs: missing_knob" in message


def test_validate_loaded_run_config_reports_missing_multi_knob_ids():
    result = _result(
        RunMode.MULTI_KNOB_RANDOM,
        {
            "target_object_ids": ["bpm_x"],
            "knob_ranges": [
                {"knob_id": "k1"},
                {"knob_id": "missing_knob"},
                {"knob_id": ""},
                "bad",
            ],
        },
    )

    ok, message = validate_loaded_run_config(result, _config(object_ids=["bpm_x"], knob_ids=["k1"]))

    assert ok is False
    assert "control PV IDs: missing_knob" in message


def test_resolve_loaded_run_selection_prefers_record_then_series_object_ids():
    selection = resolve_loaded_run_selection(
        {},
        RunMode.TIMED_ACQUISITION,
        available_object_ids={"bpm_x", "bpm_y", "scope_a"},
        record_pv_ids=["ignored", "bpm_x", "bpm_y", "bpm_x"],
        series_pv_ids=["scope_a"],
    )

    assert selection == {
        "selected_object_ids": ["bpm_x", "bpm_y"],
        "selected_knob_ids": [],
        "active_knob_id": None,
    }

    fallback_selection = resolve_loaded_run_selection(
        {},
        RunMode.TIMED_ACQUISITION,
        available_object_ids={"scope_a"},
        record_pv_ids=["ignored"],
        series_pv_ids=["scope_a"],
    )

    assert fallback_selection["selected_object_ids"] == ["scope_a"]


def test_resolve_loaded_run_selection_uses_recorded_objects_and_knob_mode():
    selection = resolve_loaded_run_selection(
        {"target_object_ids": ["bpm_x"], "knob_id": " k1 "},
        RunMode.KNOB_SCAN,
        available_object_ids={"bpm_x"},
    )

    assert selection == {
        "selected_object_ids": ["bpm_x"],
        "selected_knob_ids": ["k1"],
        "active_knob_id": "k1",
    }


def test_resolve_loaded_run_selection_uses_random_knob_ranges():
    selection = resolve_loaded_run_selection(
        {
            "target_object_ids": ["bpm_x"],
            "knob_ranges": [
                {"knob_id": "k1"},
                {"knob_id": ""},
                "bad",
                {"knob_id": "k2"},
            ],
        },
        RunMode.MULTI_KNOB_RANDOM,
        available_object_ids={"bpm_x"},
    )

    assert selection["selected_knob_ids"] == ["k1", "k2"]
    assert selection["active_knob_id"] == "k1"


def test_loaded_run_object_count_hint_uses_targets_selection_then_records():
    assert loaded_run_object_count_hint({"target_object_ids": [" bpm_x ", "", "scope_a"]}) == 2
    assert loaded_run_object_count_hint({}, selected_object_ids=["bpm_x", "scope_a"]) == 2
    assert loaded_run_object_count_hint({}, record_pv_ids=["bpm_x", "bpm_x", "scope_a"]) == 2


def test_loaded_run_parameter_updates_for_timed_acquisition():
    updates = loaded_run_parameter_updates(
        {"shot_interval_sec": "0.25", "sample_count": "12"},
        RunMode.TIMED_ACQUISITION,
    )

    assert updates == {"shot_interval_sec": 0.25, "sample_count": 12}


def test_loaded_run_parameter_updates_supports_monitor_stop_modes():
    updates = loaded_run_parameter_updates(
        {"shot_interval_sec": 0.25, "stop_mode": "duration", "duration_sec": 30.0},
        RunMode.TIMED_ACQUISITION,
    )

    assert updates == {
        "shot_interval_sec": 0.25,
        "stop_mode": "duration",
        "duration_sec": 30.0,
    }


def test_loaded_run_parameter_updates_for_single_knob_scan():
    updates = loaded_run_parameter_updates(
        {
            "settle_delay_sec": "0.5",
            "shot_interval_sec": "0.1",
            "sample_count_per_step": "3",
            "restore_initial_value": True,
            "scan_values": ["-0.1", 0, 0.25],
        },
        RunMode.KNOB_SCAN,
    )

    assert updates == {
        "settle_delay_sec": 0.5,
        "shot_interval_sec": 0.1,
        "sample_count_per_step": 3,
        "restore_initial_value": True,
        "manual_scan_values_text": "-0.1, 0, 0.25",
    }


def test_loaded_run_parameter_updates_for_random_multi_knob():
    updates = loaded_run_parameter_updates(
        {
            "settle_delay_sec": "0.5",
            "shot_interval_sec": "0.1",
            "sample_count_per_point": "4",
            "num_points": "9",
            "seed": 123,
            "restore_initial_values": False,
            "sampling_method": " grid ",
            "levels_per_knob": "3",
            "knob_ranges": [
                {"knob_id": " k1 ", "low": "-1", "high": "1"},
                {"knob_id": "", "low": 0, "high": 1},
                "bad",
                {"knob_id": "k2"},
            ],
        },
        RunMode.MULTI_KNOB_RANDOM,
    )

    assert updates == {
        "settle_delay_sec": 0.5,
        "shot_interval_sec": 0.1,
        "sample_count_per_point": 4,
        "num_points": 9,
        "restore_initial_values": False,
        "sampling_method": "grid",
        "levels_per_knob": 3,
        "knob_state": {
            "k1": {"enabled": True, "low": -1.0, "high": 1.0},
            "k2": {"enabled": True, "low": 0.0, "high": 0.0},
        },
    }
