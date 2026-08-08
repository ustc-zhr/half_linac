import json
from pathlib import Path

import pytest

from jitter_analysis.config.editor import prepare_edited_config, save_config_file


def _source_text(objects=None, groups=None):
    payload = {
        "schema_version": "2.0",
        "machine": {"name": "Test", "facility": "Lab", "description": "Editor test"},
        "defaults": {
            "acquisition": {"shot_interval_sec": 0.2, "sample_count": 10, "timeout_sec": 1.0, "mode": "poll"},
            "scan": {"settle_mode": "fixed_delay", "settle_delay_sec": 0.5, "sample_count_per_step": 3,
                     "restore_initial_value": True, "max_wait_sec": 3.0},
            "storage": {"format": "hdf5", "save_raw_data": True, "save_analysis_summary": True},
            "safety": {"confirm_before_write": True, "abort_on_disconnection": True},
        },
        "groups": groups or [], "knobs": [], "objects": objects or [], "presets": [],
    }
    return json.dumps(payload)


def _object(object_id="pv1", group="diag", read_pv="PV:1"):
    return {
        "id": object_id, "name": object_id, "group": group, "read_pv": read_pv,
        "unit": "", "precision": 6, "kind": "scalar", "access": "ro",
        "analysis": {"jitter": True, "correlation": True, "spectrum": True},
        "value_reducer": "none", "capture_mode": "scalar",
    }


def test_prepare_edited_config_preserves_top_level_data_and_adds_missing_group():
    original = _source_text(groups=[])
    data = prepare_edited_config(original, [_object(group="user")])

    assert data["machine"]["name"] == "Test"
    assert data["objects"][0]["read_pv"] == "PV:1"
    assert data["groups"][0]["id"] == "user"


def test_prepare_edited_config_preserves_explicit_group_metadata():
    group = {"id": "diag", "label": "Diagnostics", "kind": "object", "color": "#123456", "order": 20}
    data = prepare_edited_config(_source_text(), [_object(group="diag")], [group])

    assert data["groups"] == [group]


def test_prepare_edited_config_rejects_invalid_waveform():
    item = _object()
    item["kind"] = "waveform"
    item["capture_mode"] = "waveform"
    item["waveform_sample_interval_sec"] = 0

    with pytest.raises(ValueError, match="waveform_sample_interval_sec"):
        prepare_edited_config(_source_text(), [item])


def test_prepare_edited_config_rejects_scalar_raw_waveform_capture():
    item = _object()
    item["capture_mode"] = "waveform"
    item["waveform_sample_interval_sec"] = 0.1

    with pytest.raises(ValueError, match="requires kind 'waveform'"):
        prepare_edited_config(_source_text(), [item])


def test_prepare_edited_config_allows_waveform_reduced_to_scalar():
    item = _object()
    item["kind"] = "waveform"
    item["capture_mode"] = "scalar"
    item["value_reducer"] = "mean"

    data = prepare_edited_config(_source_text(), [item])

    assert data["objects"][0]["value_reducer"] == "mean"


def test_prepare_edited_config_rejects_waveform_scalar_without_reducer():
    item = _object()
    item["kind"] = "waveform"

    with pytest.raises(ValueError, match="must use value_reducer 'mean'"):
        prepare_edited_config(_source_text(), [item])


def test_save_config_file_creates_backup_and_preserves_original_on_validation_failure(tmp_path: Path):
    target = tmp_path / "pvlist.json"
    target.write_text(_source_text(), encoding="utf-8")
    data = prepare_edited_config(_source_text(), [_object()])
    save_config_file(target, data)

    assert target.with_name("pvlist.json.bak").exists()
    assert json.loads(target.read_text(encoding="utf-8"))["objects"][0]["id"] == "pv1"
