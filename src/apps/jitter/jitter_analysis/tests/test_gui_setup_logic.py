from datetime import datetime
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jitter_analysis.gui.setup_logic import list_saved_setups, parse_optional_datetime


def _mode_display_name(mode: str) -> str:
    labels = {
        "timed_acquisition": "Monitor",
        "single_knob_scan": "Single Knob",
    }
    return labels.get(mode, mode)


def test_parse_optional_datetime_accepts_iso_values_and_rejects_invalid_values():
    assert parse_optional_datetime("2026-07-01T12:34:56") == datetime(2026, 7, 1, 12, 34, 56)
    assert parse_optional_datetime("bad") is None
    assert parse_optional_datetime(None) is None


def test_list_saved_setups_filters_and_sorts_entries(tmp_path):
    older = tmp_path / "older.json"
    older.write_text(
        json.dumps(
            {
                "saved_at": "2026-07-01T09:00:00",
                "task_mode": "timed_acquisition",
                "operator": "alice",
                "selected_object_ids": ["pv1", "pv2"],
                "selected_knob_ids": ["knob1"],
                "save_dir": "runs/a",
                "config_path": "config.json",
                "notes": "older",
            }
        ),
        encoding="utf-8",
    )
    newer = tmp_path / "nested" / "newer.json"
    newer.parent.mkdir()
    newer.write_text(
        json.dumps(
            {
                "saved_at": "2026-07-01T10:00:00",
                "task_mode": "single_knob_scan",
                "selected_object_ids": [],
                "selected_knob_ids": ["knob1", "knob2"],
            }
        ),
        encoding="utf-8",
    )
    invalid_time = tmp_path / "manual_name.json"
    invalid_time.write_text(
        json.dumps({"saved_at": "bad", "task_mode": "unknown_mode"}),
        encoding="utf-8",
    )
    (tmp_path / "invalid.json").write_text("{", encoding="utf-8")
    (tmp_path / "not_setup.json").write_text(json.dumps({"saved_at": "2026-07-01T11:00:00"}), encoding="utf-8")

    entries = list_saved_setups(tmp_path, _mode_display_name)

    assert [entry["path"] for entry in entries] == [
        str(newer.resolve()),
        str(older.resolve()),
        str(invalid_time.resolve()),
    ]
    assert entries[0]["saved_at_text"] == "2026-07-01 10:00:00"
    assert entries[0]["task_mode"] == "Single Knob"
    assert entries[0]["knob_count"] == 2
    assert entries[1]["task_mode"] == "Monitor"
    assert entries[1]["object_count"] == 2
    assert entries[2]["saved_at_text"] == "manual_name"


def test_list_saved_setups_returns_empty_list_for_missing_root(tmp_path):
    assert list_saved_setups(tmp_path / "missing", _mode_display_name) == []
