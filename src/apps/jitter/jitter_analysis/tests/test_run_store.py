from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime
import sys

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jitter_analysis.storage.run_store import RunStore
from jitter_analysis.domain.types import RunMetadata, RunMode, RunResult, RunStatus, SampleRecord, WaveformRecord


class _FakeHandle(dict):
    def __init__(self, *args, attrs=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.attrs = dict(attrs or {})


def _write_run_fixture(
    run_dir: Path,
    *,
    run_id: str,
    created_at: str,
    mode: str,
    status: str,
    has_raw: bool,
) -> None:
    metadata = {
        "run_id": run_id,
        "mode": mode,
        "created_at": created_at,
        "operator": "tester",
        "machine": "IRFEL",
        "config_path": "/tmp/example.json",
        "notes": "",
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (run_dir / "result.json").write_text(
        json.dumps(
            {
                "metadata": metadata,
                "status": status,
                "counts": {"samples": 12, "steps": 3},
            }
        ),
        encoding="utf-8",
    )
    if has_raw:
        (run_dir / "raw.h5").write_text("", encoding="utf-8")


def test_list_runs_recurses_into_nested_directories(tmp_path: Path):
    root = tmp_path / "runs"
    nested_run = root / "2026" / "05" / "run_nested"
    top_run = root / "run_top"
    _write_run_fixture(
        nested_run,
        run_id="run_nested",
        created_at="2026-05-12T11:00:00",
        mode="knob_scan",
        status="completed",
        has_raw=True,
    )
    _write_run_fixture(
        top_run,
        run_id="run_top",
        created_at="2026-05-12T10:00:00",
        mode="timed_acquisition",
        status="failed",
        has_raw=False,
    )

    rows = RunStore(root).list_runs(root)

    assert [row["run_id"] for row in rows] == ["run_nested", "run_top"]
    assert rows[0]["path"] == str(nested_run.resolve())
    assert rows[0]["has_raw"] is True
    assert rows[1]["has_raw"] is False
    assert rows[1]["status"] == "failed"


def test_list_runs_accepts_single_run_directory_as_root(tmp_path: Path):
    run_dir = tmp_path / "single_run"
    _write_run_fixture(
        run_dir,
        run_id="single_run",
        created_at="2026-05-12T09:30:00",
        mode="multi_knob_random",
        status="completed",
        has_raw=True,
    )

    rows = RunStore(tmp_path).list_runs(run_dir)

    assert len(rows) == 1
    assert rows[0]["run_id"] == "single_run"
    assert rows[0]["path"] == str(run_dir.resolve())


def test_save_config_snapshot_writes_json_into_run_directory(tmp_path: Path):
    store = RunStore(tmp_path)

    path = store.save_config_snapshot(
        "run_with_snapshot",
        json.dumps({"schema_version": "2.0", "machine": {"name": "IRFEL"}}),
    )

    assert path == tmp_path / "run_with_snapshot" / "config_snapshot.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schema_version": "2.0",
        "machine": {"name": "IRFEL"},
    }


def test_preferred_config_path_for_prefers_snapshot_over_recorded_absolute_path(tmp_path: Path):
    run_dir = tmp_path / "saved_run"
    _write_run_fixture(
        run_dir,
        run_id="saved_run",
        created_at="2026-05-19T09:30:00",
        mode="timed_acquisition",
        status="completed",
        has_raw=True,
    )
    snapshot_path = run_dir / "config_snapshot.json"
    snapshot_path.write_text(json.dumps({"schema_version": "2.0"}), encoding="utf-8")

    preferred = RunStore.preferred_config_path_for(
        run_dir,
        metadata_config_path="C:\\Users\\someone\\Desktop\\jitter_analysis\\configs\\irfel_pvlist.json",
    )

    assert preferred == str(snapshot_path.resolve())


def test_preferred_config_path_for_falls_back_to_recorded_path_when_snapshot_is_missing(tmp_path: Path):
    run_dir = tmp_path / "saved_run_without_snapshot"
    _write_run_fixture(
        run_dir,
        run_id="saved_run_without_snapshot",
        created_at="2026-05-19T09:35:00",
        mode="timed_acquisition",
        status="completed",
        has_raw=True,
    )

    preferred = RunStore.preferred_config_path_for(
        run_dir,
        metadata_config_path="/tmp/example.json",
    )

    assert preferred == "/tmp/example.json"


def test_load_samples_reads_batch_index_when_present():
    sample_dtype = np.dtype(
        [
            ("pv_id", "U32"),
            ("value", np.float64),
            ("timestamp", "U32"),
            ("connected", np.bool_),
            ("severity", np.int32),
            ("status", np.int32),
            ("step_index", np.int64),
            ("batch_index", np.int64),
        ]
    )
    handle = {
        "samples": np.array(
            [("bpm01_x", 1.5, "2026-05-13T09:30:00", True, 0, 0, 2, 7)],
            dtype=sample_dtype,
        )
    }

    samples = RunStore._load_samples(handle)

    assert len(samples) == 1
    assert samples[0].step_index == 2
    assert samples[0].batch_index == 7


def test_load_samples_reads_hdf5_rows_in_chunks(monkeypatch):
    sample_dtype = np.dtype(
        [
            ("pv_id", "U32"),
            ("value", np.float64),
            ("timestamp", "U32"),
            ("connected", np.bool_),
            ("severity", np.int32),
            ("status", np.int32),
            ("step_index", np.int64),
            ("batch_index", np.int64),
        ]
    )

    class _ChunkedDataset:
        def __init__(self):
            self.rows = np.array(
                [
                    ("a", 1.0, "2026-05-13T09:30:00", True, 0, 0, -1, 0),
                    ("b", 2.0, "2026-05-13T09:30:01", True, 0, 0, -1, 1),
                    ("c", 3.0, "2026-05-13T09:30:02", True, 0, 0, -1, 2),
                ],
                dtype=sample_dtype,
            )
            self.dtype = self.rows.dtype
            self.shape = self.rows.shape
            self.requests = []

        def __getitem__(self, item):
            self.requests.append(item)
            return self.rows[item]

    dataset = _ChunkedDataset()
    monkeypatch.setattr(RunStore, "SAMPLE_LOAD_CHUNK_SIZE", 2)

    samples = RunStore._load_samples({"samples": dataset})

    assert [sample.pv_id for sample in samples] == ["a", "b", "c"]
    assert dataset.requests == [slice(0, 2, None), slice(2, 3, None)]


def test_load_samples_keeps_legacy_runs_without_batch_index_compatible():
    sample_dtype = np.dtype(
        [
            ("pv_id", "U32"),
            ("value", np.float64),
            ("timestamp", "U32"),
            ("connected", np.bool_),
            ("severity", np.int32),
            ("status", np.int32),
            ("step_index", np.int64),
        ]
    )
    handle = {
        "samples": np.array(
            [("bpm01_y", 2.5, "2026-05-13T09:31:00", True, 0, 0, -1)],
            dtype=sample_dtype,
        )
    }

    samples = RunStore._load_samples(handle)

    assert len(samples) == 1
    assert samples[0].step_index is None
    assert samples[0].batch_index is None


def test_fast_timed_snapshot_groups_samples_without_building_sample_records():
    sample_dtype = np.dtype(
        [
            ("pv_id", "U32"),
            ("value", np.float64),
            ("timestamp", "U32"),
            ("connected", np.bool_),
            ("severity", np.int32),
            ("status", np.int32),
            ("step_index", np.int64),
            ("batch_index", np.int64),
        ]
    )
    handle = _FakeHandle(
        {
            "samples": np.array(
                [
                    ("obj_a", 1.0, "2026-05-20T10:00:00", True, 0, 0, -1, 0),
                    ("obj_b", 10.0, "2026-05-20T10:00:00", True, 0, 0, -1, 0),
                    ("obj_a", 2.0, "2026-05-20T10:00:01", True, 0, 0, -1, 1),
                    ("obj_b", 20.0, "2026-05-20T10:00:01", True, 0, 0, -1, 1),
                    ("obj_a", 3.0, "2026-05-20T10:00:02", True, 0, 0, -1, 2),
                    ("obj_b", 30.0, "2026-05-20T10:00:02", True, 0, 0, -1, 2),
                ],
                dtype=sample_dtype,
            )
        },
        attrs={},
    )
    metadata_payload = {
        "metadata": {
            "run_id": "fast_run",
            "mode": "timed_acquisition",
            "created_at": "2026-05-20T10:00:00",
            "operator": "tester",
            "machine": "IRFEL",
            "config_path": "/tmp/example.json",
            "notes": "",
        }
    }
    summary_payload = {
        "status": "completed",
        "details": {"target_object_ids": ["obj_a", "obj_b"]},
    }

    snapshot = RunStore._load_timed_acquisition_series_fast_from_handle(
        handle,
        metadata_payload,
        summary_payload,
        minimum_record_count=4,
    )

    assert snapshot is not None
    assert snapshot.record_count == 6
    assert snapshot.logical_sample_count == 3
    assert snapshot.ordered_object_ids == ["obj_a", "obj_b"]
    assert snapshot.series_values["obj_a"] == [1.0, 2.0, 3.0]
    assert snapshot.series_values["obj_b"] == [10.0, 20.0, 30.0]
    assert snapshot.series_sample_indices["obj_a"] == [0, 1, 2]
    assert len(snapshot.sample_timestamps) == 3
    assert snapshot.used_legacy_batch_reconstruction is False


def test_fast_timed_snapshot_infers_target_order_from_batch_index_when_details_are_missing():
    sample_dtype = np.dtype(
        [
            ("pv_id", "U32"),
            ("value", np.float64),
            ("timestamp", "U32"),
            ("connected", np.bool_),
            ("severity", np.int32),
            ("status", np.int32),
            ("step_index", np.int64),
            ("batch_index", np.int64),
        ]
    )
    handle = _FakeHandle(
        {
            "samples": np.array(
                [
                    ("obj_a", 1.0, "2026-05-20T10:00:00", True, 0, 0, -1, 0),
                    ("obj_b", 10.0, "2026-05-20T10:00:00", True, 0, 0, -1, 0),
                    ("obj_a", 2.0, "2026-05-20T10:00:01", True, 0, 0, -1, 1),
                    ("obj_b", 20.0, "2026-05-20T10:00:01", True, 0, 0, -1, 1),
                ],
                dtype=sample_dtype,
            )
        },
        attrs={},
    )
    metadata_payload = {
        "metadata": {
            "run_id": "inferred_order_run",
            "mode": "timed_acquisition",
            "created_at": "2026-05-20T10:00:00",
        }
    }

    snapshot = RunStore._load_timed_acquisition_series_fast_from_handle(
        handle,
        metadata_payload,
        {"status": "completed", "details": {}},
        minimum_record_count=1,
    )

    assert snapshot is not None
    assert snapshot.ordered_object_ids == ["obj_a", "obj_b"]
    assert snapshot.series_values["obj_a"] == [1.0, 2.0]
    assert snapshot.series_values["obj_b"] == [10.0, 20.0]


def test_fast_timed_snapshot_can_load_requested_object_subset():
    sample_dtype = np.dtype(
        [
            ("pv_id", "U32"),
            ("value", np.float64),
            ("timestamp", "U32"),
            ("connected", np.bool_),
            ("severity", np.int32),
            ("status", np.int32),
            ("step_index", np.int64),
            ("batch_index", np.int64),
        ]
    )
    handle = _FakeHandle(
        {
            "samples": np.array(
                [
                    ("obj_a", 1.0, "2026-05-20T10:00:00", True, 0, 0, -1, 0),
                    ("obj_b", 10.0, "2026-05-20T10:00:00", True, 0, 0, -1, 0),
                    ("obj_c", 100.0, "2026-05-20T10:00:00", True, 0, 0, -1, 0),
                    ("obj_a", 2.0, "2026-05-20T10:00:01", True, 0, 0, -1, 1),
                    ("obj_b", 20.0, "2026-05-20T10:00:01", True, 0, 0, -1, 1),
                    ("obj_c", 200.0, "2026-05-20T10:00:01", True, 0, 0, -1, 1),
                ],
                dtype=sample_dtype,
            )
        },
        attrs={},
    )
    metadata_payload = {
        "metadata": {
            "run_id": "subset_run",
            "mode": "timed_acquisition",
            "created_at": "2026-05-20T10:00:00",
        }
    }
    summary_payload = {
        "status": "completed",
        "details": {"target_object_ids": ["obj_a", "obj_b", "obj_c"]},
    }

    snapshot = RunStore._load_timed_acquisition_series_fast_from_handle(
        handle,
        metadata_payload,
        summary_payload,
        minimum_record_count=1,
        requested_object_ids=["obj_c"],
    )

    assert snapshot is not None
    assert snapshot.ordered_object_ids == ["obj_a", "obj_b", "obj_c"]
    assert snapshot.series_values == {"obj_c": [100.0, 200.0]}
    assert snapshot.series_sample_indices == {"obj_c": [0, 1]}


def test_fast_timed_snapshot_prefers_scalar_object_ids_over_mixed_targets():
    sample_dtype = np.dtype(
        [
            ("pv_id", "U32"),
            ("value", np.float64),
            ("timestamp", "U32"),
            ("connected", np.bool_),
            ("severity", np.int32),
            ("status", np.int32),
            ("step_index", np.int64),
            ("batch_index", np.int64),
        ]
    )
    handle = _FakeHandle(
        {
            "samples": np.array(
                [
                    ("scalar_a", 1.0, "2026-05-20T10:00:00", True, 0, 0, -1, 0),
                    ("scalar_b", 10.0, "2026-05-20T10:00:00", True, 0, 0, -1, 0),
                    ("scalar_a", 2.0, "2026-05-20T10:00:01", True, 0, 0, -1, 1),
                    ("scalar_b", 20.0, "2026-05-20T10:00:01", True, 0, 0, -1, 1),
                ],
                dtype=sample_dtype,
            )
        },
        attrs={},
    )
    metadata_payload = {
        "metadata": {
            "run_id": "mixed_target_run",
            "mode": "timed_acquisition",
            "created_at": "2026-05-20T10:00:00",
        }
    }
    summary_payload = {
        "status": "completed",
        "details": {
            "target_object_ids": ["scalar_a", "waveform_a", "scalar_b"],
            "scalar_object_ids": ["scalar_a", "scalar_b"],
            "waveform_object_ids": ["waveform_a"],
        },
    }

    snapshot = RunStore._load_timed_acquisition_series_fast_from_handle(
        handle,
        metadata_payload,
        summary_payload,
        minimum_record_count=1,
    )

    assert snapshot is not None
    assert snapshot.ordered_object_ids == ["scalar_a", "scalar_b"]
    assert snapshot.series_values["scalar_a"] == [1.0, 2.0]
    assert snapshot.series_values["scalar_b"] == [10.0, 20.0]


def test_fast_timed_snapshot_returns_none_when_target_order_does_not_match_samples():
    sample_dtype = np.dtype(
        [
            ("pv_id", "U32"),
            ("value", np.float64),
            ("timestamp", "U32"),
            ("connected", np.bool_),
            ("severity", np.int32),
            ("status", np.int32),
            ("step_index", np.int64),
            ("batch_index", np.int64),
        ]
    )
    handle = _FakeHandle(
        {
            "samples": np.array(
                [
                    ("obj_b", 10.0, "2026-05-20T10:00:00", True, 0, 0, -1, 0),
                    ("obj_a", 1.0, "2026-05-20T10:00:00", True, 0, 0, -1, 0),
                    ("obj_b", 20.0, "2026-05-20T10:00:01", True, 0, 0, -1, 1),
                    ("obj_a", 2.0, "2026-05-20T10:00:01", True, 0, 0, -1, 1),
                ],
                dtype=sample_dtype,
            )
        },
        attrs={},
    )
    metadata_payload = {
        "metadata": {
            "run_id": "mismatch_run",
            "mode": "timed_acquisition",
            "created_at": "2026-05-20T10:00:00",
        }
    }
    summary_payload = {
        "status": "completed",
        "details": {"target_object_ids": ["obj_a", "obj_b"]},
    }

    snapshot = RunStore._load_timed_acquisition_series_fast_from_handle(
        handle,
        metadata_payload,
        summary_payload,
        minimum_record_count=1,
    )

    assert snapshot is None


def test_fast_timed_snapshot_marks_legacy_runs_without_batch_index():
    sample_dtype = np.dtype(
        [
            ("pv_id", "U32"),
            ("value", np.float64),
            ("timestamp", "U32"),
            ("connected", np.bool_),
            ("severity", np.int32),
            ("status", np.int32),
            ("step_index", np.int64),
        ]
    )
    handle = _FakeHandle(
        {
            "samples": np.array(
                [
                    ("obj_a", 1.0, "2026-05-20T10:00:00", True, 0, 0, -1),
                    ("obj_b", 10.0, "2026-05-20T10:00:00", True, 0, 0, -1),
                    ("obj_a", 2.0, "2026-05-20T10:00:01", True, 0, 0, -1),
                    ("obj_b", 20.0, "2026-05-20T10:00:01", True, 0, 0, -1),
                ],
                dtype=sample_dtype,
            )
        },
        attrs={},
    )
    metadata_payload = {
        "metadata": {
            "run_id": "legacy_run",
            "mode": "timed_acquisition",
            "created_at": "2026-05-20T10:00:00",
        }
    }
    summary_payload = {
        "status": "completed",
        "details": {"target_object_ids": ["obj_a", "obj_b"]},
    }

    snapshot = RunStore._load_timed_acquisition_series_fast_from_handle(
        handle,
        metadata_payload,
        summary_payload,
        minimum_record_count=1,
    )

    assert snapshot is not None
    assert snapshot.used_legacy_batch_reconstruction is True


def test_load_steps_returns_empty_for_legacy_files_without_steps_dataset():
    handle = {}

    assert RunStore._load_steps(handle, []) == []


def test_hdf5_writer_creates_compressed_datasets(tmp_path: Path):
    import h5py

    store = RunStore(tmp_path)
    metadata = RunMetadata(
        run_id="compressed_run",
        mode=RunMode.TIMED_ACQUISITION,
        created_at=datetime.fromisoformat("2026-05-21T09:00:00"),
    )
    raw_path = store.start_run(metadata)
    store.close_active_stream()

    with h5py.File(raw_path, "r") as handle:
        assert handle["samples"].compression == "gzip"
        assert handle["steps"].compression == "gzip"
        assert handle["waveforms/index"].compression == "gzip"
        assert handle["waveforms/data"].compression == "gzip"


def test_run_store_persists_and_loads_waveform_index_and_values(tmp_path: Path):
    store = RunStore(tmp_path)
    metadata = RunMetadata(
        run_id="wave_run",
        mode=RunMode.TIMED_ACQUISITION,
        created_at=datetime.fromisoformat("2026-05-21T10:00:00"),
    )
    store.start_run(metadata)
    store.append_samples(
        [
            SampleRecord(
                pv_id="bpm_x",
                value=1.0,
                timestamp=datetime.fromisoformat("2026-05-21T10:00:00"),
                batch_index=0,
            )
        ]
    )
    store.append_waveforms(
        [
            WaveformRecord(
                pv_id="scope_a",
                values=[0.0, 1.0, 0.5],
                timestamp=datetime.fromisoformat("2026-05-21T10:00:00"),
                waveform_sample_interval_sec=1.0e-9,
                batch_index=0,
            ),
            WaveformRecord(
                pv_id="scope_b",
                values=[0.0, -1.0, -0.5, 0.25],
                timestamp=datetime.fromisoformat("2026-05-21T10:00:00"),
                waveform_sample_interval_sec=1.0e-9,
                batch_index=0,
            ),
        ]
    )
    store.save_result(
        RunResult(
            metadata=metadata,
            status=RunStatus.COMPLETED,
            samples=[],
            steps=[],
            details={"target_object_ids": ["bpm_x", "scope_a", "scope_b"]},
        )
    )

    index_entries = store.load_waveform_index(tmp_path / "wave_run")

    assert len(index_entries) == 2
    assert [entry.pv_id for entry in index_entries] == ["scope_a", "scope_b"]
    assert index_entries[0].length == 3
    assert index_entries[1].length == 4

    loaded_waveform = store.load_waveform(tmp_path / "wave_run", index_entries[1])

    assert loaded_waveform.pv_id == "scope_b"
    assert loaded_waveform.values == [0.0, -1.0, -0.5, 0.25]
    assert loaded_waveform.batch_index == 0


def test_load_result_exposes_waveform_metadata_without_loading_all_waveforms(tmp_path: Path):
    store = RunStore(tmp_path)
    metadata = RunMetadata(
        run_id="wave_meta_run",
        mode=RunMode.TIMED_ACQUISITION,
        created_at=datetime.fromisoformat("2026-05-21T11:00:00"),
    )
    store.start_run(metadata)
    store.append_waveforms(
        [
            WaveformRecord(
                pv_id="scope_a",
                values=[0.0, 1.0],
                timestamp=datetime.fromisoformat("2026-05-21T11:00:00"),
                waveform_sample_interval_sec=1.0e-9,
                batch_index=0,
            )
        ]
    )
    store.save_result(
        RunResult(
            metadata=metadata,
            status=RunStatus.COMPLETED,
            samples=[],
            steps=[],
            details={"target_object_ids": ["scope_a"], "waveform_object_ids": ["scope_a"]},
        )
    )

    result = store.load_result(tmp_path / "wave_meta_run")

    assert result.details["waveform_count"] == 1
    assert result.details["waveform_object_ids"] == ["scope_a"]


def test_load_details_normalizes_legacy_sample_interval_field_name():
    handle = _FakeHandle(attrs={"details_json": json.dumps({"sample_interval_sec": 0.25, "sample_count": 3})})

    details = RunStore._load_details(handle, {})

    assert details["shot_interval_sec"] == 0.25
    assert "sample_interval_sec" not in details
    assert details["sample_count"] == 3
