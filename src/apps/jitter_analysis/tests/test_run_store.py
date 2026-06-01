from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jitter_analysis.storage.run_store import RunStore


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
