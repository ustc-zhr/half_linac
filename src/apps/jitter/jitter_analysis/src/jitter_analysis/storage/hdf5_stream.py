from __future__ import annotations

import json
import math
from pathlib import Path
import time

import numpy as np

from ..domain.types import (
    MultiKnobStepRecord,
    RunMetadata,
    RunResult,
    SampleRecord,
    ScanStepRecord,
    WaveformRecord,
)
from .serializers import to_jsonable


class HDF5RunWriter:
    _dataset_compression = {
        "compression": "gzip",
        "compression_opts": 4,
        "shuffle": True,
    }

    def __init__(self, path: str | Path, metadata: RunMetadata) -> None:
        try:
            import h5py
        except ImportError as exc:  # pragma: no cover - runtime dependency
            raise RuntimeError("h5py is required to stream raw acquisition data to HDF5.") from exc

        self._h5py = h5py
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = metadata.run_id
        self._string_dtype = h5py.string_dtype(encoding="utf-8")
        self._sample_dtype = np.dtype(
            [
                ("pv_id", self._string_dtype),
                ("value", np.float64),
                ("timestamp", self._string_dtype),
                ("connected", np.bool_),
                ("severity", np.int32),
                ("status", np.int32),
                ("step_index", np.int64),
                ("batch_index", np.int64),
            ]
        )
        self._waveform_index_dtype = np.dtype(
            [
                ("pv_id", self._string_dtype),
                ("timestamp", self._string_dtype),
                ("connected", np.bool_),
                ("severity", np.int32),
                ("status", np.int32),
                ("step_index", np.int64),
                ("batch_index", np.int64),
                ("offset", np.int64),
                ("length", np.int64),
                ("waveform_sample_interval_sec", np.float64),
            ]
        )
        self._step_dtype = np.dtype(
            [
                ("mode", self._string_dtype),
                ("step_index", np.int64),
                ("target_value", np.float64),
                ("readback_value", np.float64),
                ("target_values_json", self._string_dtype),
                ("readback_values_json", self._string_dtype),
                ("started_at", self._string_dtype),
                ("settled_at", self._string_dtype),
                ("sample_count", np.int64),
            ]
        )

        self._file = h5py.File(self.path, "w")
        self._file.attrs["schema_version"] = "3"
        self._file.attrs["run_id"] = metadata.run_id
        self._file.attrs["mode"] = metadata.mode.value
        self._file.attrs["created_at"] = metadata.created_at.isoformat()
        self._file.attrs["operator"] = metadata.operator
        self._file.attrs["machine"] = metadata.machine
        self._file.attrs["config_path"] = metadata.config_path
        self._file.attrs["notes"] = metadata.notes

        self._samples = self._file.create_dataset(
            "samples",
            shape=(0,),
            maxshape=(None,),
            chunks=True,
            dtype=self._sample_dtype,
            **self._dataset_compression,
        )
        self._steps = self._file.create_dataset(
            "steps",
            shape=(0,),
            maxshape=(None,),
            chunks=True,
            dtype=self._step_dtype,
            **self._dataset_compression,
        )
        self._waveforms_index = self._file.create_dataset(
            "waveforms/index",
            shape=(0,),
            maxshape=(None,),
            chunks=True,
            dtype=self._waveform_index_dtype,
            **self._dataset_compression,
        )
        self._waveforms_data = self._file.create_dataset(
            "waveforms/data",
            shape=(0,),
            maxshape=(None,),
            chunks=True,
            dtype=np.float64,
            **self._dataset_compression,
        )
        self.waveform_record_count = 0
        self._pending_row_count = 0
        self._flush_row_threshold = 256
        self._flush_interval_sec = 1.0
        self._last_flush_monotonic = time.monotonic()

    def _append_rows(self, dataset, rows: np.ndarray, *, flush_hint: bool = False) -> None:
        if len(rows) <= 0:
            return
        start = int(dataset.shape[0])
        dataset.resize((start + len(rows),))
        dataset[start:] = rows
        self._pending_row_count += int(len(rows))
        if flush_hint:
            self._flush()
            return
        elapsed = time.monotonic() - self._last_flush_monotonic
        if self._pending_row_count >= self._flush_row_threshold or elapsed >= self._flush_interval_sec:
            self._flush()

    def _flush(self) -> None:
        if getattr(self, "_file", None) is None:
            return
        self._file.flush()
        self._pending_row_count = 0
        self._last_flush_monotonic = time.monotonic()

    def append_samples(self, samples: list[SampleRecord]) -> None:
        if not samples:
            return
        rows = np.empty(len(samples), dtype=self._sample_dtype)
        for index, sample in enumerate(samples):
            rows[index] = (
                sample.pv_id,
                float(sample.value),
                sample.timestamp.isoformat(),
                bool(sample.connected),
                int(sample.severity),
                int(sample.status),
                -1 if sample.step_index is None else int(sample.step_index),
                -1 if sample.batch_index is None else int(sample.batch_index),
            )
        self._append_rows(self._samples, rows)

    def append_waveforms(self, waveforms: list[WaveformRecord]) -> None:
        if not waveforms:
            return
        rows = np.empty(len(waveforms), dtype=self._waveform_index_dtype)
        for index, waveform in enumerate(waveforms):
            values = np.asarray(list(waveform.values), dtype=float).reshape(-1)
            offset = int(self._waveforms_data.shape[0])
            if values.size > 0:
                self._append_rows(self._waveforms_data, values)
            rows[index] = (
                waveform.pv_id,
                waveform.timestamp.isoformat(),
                bool(waveform.connected),
                int(waveform.severity),
                int(waveform.status),
                -1 if waveform.step_index is None else int(waveform.step_index),
                -1 if waveform.batch_index is None else int(waveform.batch_index),
                offset,
                int(values.size),
                float(waveform.waveform_sample_interval_sec),
            )
        self.waveform_record_count += len(waveforms)
        self._append_rows(self._waveforms_index, rows)

    def append_step(self, step: ScanStepRecord | MultiKnobStepRecord) -> None:
        rows = np.empty(1, dtype=self._step_dtype)
        if isinstance(step, ScanStepRecord):
            rows[0] = (
                "single_knob_scan",
                int(step.step_index),
                float(step.target_value),
                math.nan if step.readback_value is None else float(step.readback_value),
                "",
                "",
                step.started_at.isoformat(),
                "" if step.settled_at is None else step.settled_at.isoformat(),
                len(step.samples),
            )
        else:
            rows[0] = (
                "multi_knob_random",
                int(step.step_index),
                math.nan,
                math.nan,
                json.dumps(to_jsonable(step.target_values), sort_keys=True),
                json.dumps(to_jsonable(step.readback_values), sort_keys=True),
                step.started_at.isoformat(),
                "" if step.settled_at is None else step.settled_at.isoformat(),
                len(step.samples),
            )
        self._append_rows(self._steps, rows, flush_hint=True)

    def finalize(self, result: RunResult) -> None:
        self._file.attrs["final_status"] = result.status.value
        self._file.attrs["warning_count"] = len(result.warnings)
        self._file.attrs["sample_count"] = len(result.samples)
        self._file.attrs["step_count"] = len(result.steps)
        self._file.attrs["waveform_count"] = int(self.waveform_record_count)
        self._file.attrs["warnings_json"] = json.dumps(to_jsonable(result.warnings))
        self._file.attrs["details_json"] = json.dumps(to_jsonable(result.details), sort_keys=True)
        self._flush()
        self.close()

    def close(self) -> None:
        if getattr(self, "_file", None) is not None:
            if self._pending_row_count > 0:
                self._flush()
            self._file.close()
            self._file = None
