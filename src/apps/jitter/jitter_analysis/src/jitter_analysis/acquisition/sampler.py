from __future__ import annotations

from datetime import datetime

import numpy as np

from ..config.models import ObjectSpec
from ..domain.types import AcquisitionBatch, SampleRecord, WaveformRecord
from ..epics.client import PyEpicsClient


class AcquisitionSampler:
    def __init__(self, client: PyEpicsClient) -> None:
        self.client = client

    @staticmethod
    def _normalize_value_reducer(value_reducer: str) -> str:
        return str(value_reducer or "none").strip().lower().replace("-", "_")

    @staticmethod
    def _normalize_capture_mode(capture_mode: str) -> str:
        return str(capture_mode or "scalar").strip().lower().replace("-", "_")

    def _is_waveform_object(self, obj: ObjectSpec) -> bool:
        return self._normalize_capture_mode(getattr(obj, "capture_mode", "scalar")) == "waveform"

    def _coerce_scalar_value(self, obj: ObjectSpec, raw_value: object) -> float:
        try:
            return float(raw_value)
        except (TypeError, ValueError):
            try:
                data = np.asarray(raw_value, dtype=float)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Object {obj.id} ({obj.read_pv}) returned a non-numeric value: {raw_value!r}"
                ) from exc
            if data.size != 1:
                raise ValueError(
                    f"Object {obj.id} ({obj.read_pv}) expects a scalar value but received {data.size} elements. "
                    "Set value_reducer to 'mean' if this PV should be averaged."
                )
            return float(data.reshape(-1)[0])

    def _reduce_mean_value(self, obj: ObjectSpec, raw_value: object) -> float:
        try:
            data = np.asarray(raw_value, dtype=float).reshape(-1)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Object {obj.id} ({obj.read_pv}) could not be converted to a numeric array for mean reduction."
            ) from exc
        if data.size == 0:
            return float("nan")
        finite_data = data[np.isfinite(data)]
        if finite_data.size == 0:
            return float("nan")
        return float(np.mean(finite_data))

    def _coerce_waveform_values(self, obj: ObjectSpec, raw_value: object) -> list[float]:
        if raw_value is None:
            return []
        try:
            data = np.asarray(raw_value, dtype=float).reshape(-1)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Waveform object {obj.id} ({obj.read_pv}) could not be converted to a numeric array."
            ) from exc
        if data.size == 0:
            return []
        return [float(value) for value in data]

    def _coerce_sample_value(self, obj: ObjectSpec, raw_value: object) -> float:
        if raw_value is None:
            return float("nan")

        reducer = self._normalize_value_reducer(obj.value_reducer)
        if reducer == "none":
            return self._coerce_scalar_value(obj, raw_value)
        if reducer == "mean":
            return self._reduce_mean_value(obj, raw_value)
        raise ValueError(f"Unsupported value reducer for object {obj.id}: {obj.value_reducer}")

    def sample_object(
        self,
        obj: ObjectSpec,
        step_index: int | None = None,
        batch_index: int | None = None,
    ) -> SampleRecord:
        return self.sample_objects([obj], step_index=step_index, batch_index=batch_index)[0]

    def sample_objects(
        self,
        objects: list[ObjectSpec],
        step_index: int | None = None,
        batch_index: int | None = None,
    ) -> list[SampleRecord]:
        if not objects:
            return []
        waveform_objects = [obj for obj in objects if self._is_waveform_object(obj)]
        if waveform_objects:
            raise ValueError(
                "sample_objects only supports scalar objects. Use sample_batch for waveform capture."
            )
        if batch_index is None and step_index is not None:
            batch_index = int(step_index)

        results = self.client.read_many([obj.read_pv for obj in objects])
        if len(results) != len(objects):
            raise RuntimeError("EPICS client returned a mismatched bulk read result.")

        # One batch timestamp makes each acquisition round behave like a coherent snapshot.
        batch_timestamp = datetime.now()
        samples = []
        for obj, result in zip(objects, results):
            value = self._coerce_sample_value(obj, result.value)
            samples.append(
                SampleRecord(
                    pv_id=obj.id,
                    value=value,
                    timestamp=batch_timestamp,
                    connected=result.connected,
                    step_index=step_index,
                    batch_index=batch_index,
                )
            )
        return samples

    def sample_batch(
        self,
        objects: list[ObjectSpec],
        step_index: int | None = None,
        batch_index: int | None = None,
    ) -> AcquisitionBatch:
        if not objects:
            return AcquisitionBatch()
        if batch_index is None and step_index is not None:
            batch_index = int(step_index)

        results = self.client.read_many([obj.read_pv for obj in objects])
        if len(results) != len(objects):
            raise RuntimeError("EPICS client returned a mismatched bulk read result.")

        batch_timestamp = datetime.now()
        scalar_samples: list[SampleRecord] = []
        waveform_samples: list[WaveformRecord] = []
        for obj, result in zip(objects, results):
            if self._is_waveform_object(obj):
                waveform_samples.append(
                    WaveformRecord(
                        pv_id=obj.id,
                        values=self._coerce_waveform_values(obj, result.value),
                        timestamp=batch_timestamp,
                        waveform_sample_interval_sec=float(obj.waveform_sample_interval_sec or 0.0),
                        connected=result.connected,
                        step_index=step_index,
                        batch_index=batch_index,
                    )
                )
                continue

            scalar_samples.append(
                SampleRecord(
                    pv_id=obj.id,
                    value=self._coerce_sample_value(obj, result.value),
                    timestamp=batch_timestamp,
                    connected=result.connected,
                    step_index=step_index,
                    batch_index=batch_index,
                )
            )
        return AcquisitionBatch(
            scalar_samples=scalar_samples,
            waveform_samples=waveform_samples,
        )
