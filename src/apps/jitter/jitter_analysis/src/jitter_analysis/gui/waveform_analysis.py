from __future__ import annotations

import math

import numpy as np

try:
    from PyQt5 import QtCore
except ImportError:  # pragma: no cover - optional runtime dependency
    QtCore = None

from ..analysis.waveform import (
    compute_waveform_features,
    estimate_waveform_delay,
    estimate_waveform_delay_samples,
)
from ..domain.types import WaveformIndexEntry, WaveformRecord


_FEATURE_KEYS = (
    "baseline_mean",
    "peak_value",
    "peak_time_sec",
    "integral",
    "rms",
    "peak_to_peak",
    "baseline_noise_rms",
    "peak_amplitude",
    "pulse_peak_index",
    "pulse_peak_time_sec",
    "area_samples",
    "area",
    "fwhm_samples",
    "fwhm_sec",
    "snr",
)


class _PointwiseAccumulator:
    def __init__(self) -> None:
        self.total = np.zeros(0, dtype=float)
        self.total_square = np.zeros(0, dtype=float)
        self.count = np.zeros(0, dtype=np.int64)

    def append(self, values) -> None:
        data = np.asarray(values, dtype=float).reshape(-1)
        if data.size > self.total.size:
            extension = int(data.size - self.total.size)
            self.total = np.pad(self.total, (0, extension))
            self.total_square = np.pad(self.total_square, (0, extension))
            self.count = np.pad(self.count, (0, extension))
        finite = np.isfinite(data)
        indices = np.flatnonzero(finite)
        if indices.size <= 0:
            return
        finite_values = data[indices]
        self.total[indices] += finite_values
        self.total_square[indices] += np.square(finite_values)
        self.count[indices] += 1

    def result(self) -> dict[str, list[float]]:
        mean = np.full(self.total.size, np.nan, dtype=float)
        std = np.full(self.total.size, np.nan, dtype=float)
        valid = self.count > 0
        mean[valid] = self.total[valid] / self.count[valid]
        variance = np.zeros(self.total.size, dtype=float)
        variance[valid] = self.total_square[valid] / self.count[valid] - np.square(mean[valid])
        std[valid] = np.sqrt(np.maximum(variance[valid], 0.0))
        return {
            "mean_values": mean.tolist(),
            "std_values": std.tolist(),
            "valid_counts": self.count.astype(int).tolist(),
        }


if QtCore is not None:

    class WaveformAnalysisSignals(QtCore.QObject):
        finished = QtCore.pyqtSignal(object)
        failed = QtCore.pyqtSignal(str)


    class WaveformAnalysisWorker(QtCore.QObject):
        def __init__(
            self,
            *,
            waveform_ids,
            labels_by_pv,
            request_signature,
            roi_start_index: int,
            roi_stop_index: int,
            primary_pv_id: str,
            secondary_pv_id: str,
            baseline_start_index: int = 0,
            baseline_stop_index: int = 1,
            polarity: str = "auto",
            in_memory_records_by_pv=None,
            waveform_entries_by_pv=None,
            waveform_loader=None,
            waveform_iterator=None,
        ) -> None:
            super().__init__()
            self.waveform_ids = [str(item) for item in waveform_ids]
            self.labels_by_pv = {str(key): str(value) for key, value in dict(labels_by_pv).items()}
            self.request_signature = tuple(request_signature)
            self.roi_start_index = int(roi_start_index)
            self.roi_stop_index = int(roi_stop_index)
            self.baseline_start_index = int(baseline_start_index)
            self.baseline_stop_index = int(baseline_stop_index)
            self.polarity = str(polarity or "auto")
            self.primary_pv_id = str(primary_pv_id or "").strip()
            self.secondary_pv_id = str(secondary_pv_id or "").strip()
            self.in_memory_records_by_pv = {
                str(key): list(value)
                for key, value in dict(in_memory_records_by_pv or {}).items()
            }
            self.waveform_entries_by_pv = {
                str(key): list(value)
                for key, value in dict(waveform_entries_by_pv or {}).items()
            }
            self.waveform_loader = waveform_loader
            self.waveform_iterator = waveform_iterator
            self.signals = WaveformAnalysisSignals()

        @QtCore.pyqtSlot()
        def run(self) -> None:
            try:
                payload = self._analyze()
                self.signals.finished.emit(payload)
            except Exception as exc:  # pragma: no cover - thread runtime integration
                self.signals.failed.emit(str(exc))

        def _analyze(self) -> dict[str, object]:
            states = {
                pv_id: {
                    "sample_indices": [],
                    "timestamps": [],
                    "features": {key: [] for key in _FEATURE_KEYS},
                    "polarities": [],
                    "valid_count": 0,
                    "record_count": 0,
                    "intervals": [],
                    "aggregate": _PointwiseAccumulator(),
                    "quality": {
                        "disconnected": 0,
                        "empty": 0,
                        "no_finite": 0,
                        "invalid": 0,
                        "too_short": 0,
                        "fwhm_unavailable": 0,
                        "sample_interval_variation": 0,
                        "skipped": 0,
                    },
                }
                for pv_id in self.waveform_ids
            }
            max_waveform_length = 0
            pending_pair_key: int | None = None
            pending_pair: dict[str, WaveformRecord] = {}
            fallback_ordinals = {pv_id: 0 for pv_id in self.waveform_ids}
            delay_rows = []
            delay_skipped = 0

            for waveform in self._iter_records():
                pv_id = str(waveform.pv_id)
                if pv_id not in states:
                    continue
                state = states[pv_id]
                ordinal = fallback_ordinals[pv_id]
                fallback_ordinals[pv_id] += 1
                sample_index = (
                    int(waveform.batch_index)
                    if waveform.batch_index is not None
                    else ordinal
                )
                state["record_count"] += 1
                state["sample_indices"].append(sample_index)
                state["timestamps"].append(waveform.timestamp)
                state["intervals"].append(float(waveform.waveform_sample_interval_sec or 0.0))
                max_waveform_length = max(max_waveform_length, len(waveform.values))

                if not waveform.connected:
                    state["quality"]["disconnected"] += 1
                    state["quality"]["skipped"] += 1
                    self._append_invalid_features(state)
                elif not waveform.values:
                    state["quality"]["empty"] += 1
                    state["quality"]["skipped"] += 1
                    self._append_invalid_features(state)
                else:
                    roi_start = min(max(self.roi_start_index, 0), len(waveform.values) - 1)
                    roi_stop = min(
                        max(self.roi_stop_index, roi_start + 1),
                        len(waveform.values),
                    )
                    if roi_stop - roi_start < 3:
                        state["quality"]["too_short"] += 1
                    try:
                        features = compute_waveform_features(
                            waveform.values,
                            waveform.waveform_sample_interval_sec,
                            roi_start_index=self.roi_start_index,
                            roi_stop_index=self.roi_stop_index,
                            baseline_start_index=self.baseline_start_index,
                            baseline_stop_index=self.baseline_stop_index,
                            polarity=self.polarity,
                        )
                    except Exception as exc:
                        if "no finite" in str(exc).lower():
                            state["quality"]["no_finite"] += 1
                        else:
                            state["quality"]["invalid"] += 1
                        state["quality"]["skipped"] += 1
                        self._append_invalid_features(state)
                    else:
                        state["valid_count"] += 1
                        state["aggregate"].append(waveform.values)
                        self._append_features(state, features)
                        if not math.isfinite(float(features.fwhm_samples)):
                            state["quality"]["fwhm_unavailable"] += 1

                if (
                    self.primary_pv_id
                    and self.secondary_pv_id
                    and pv_id in {self.primary_pv_id, self.secondary_pv_id}
                ):
                    if pending_pair_key is not None and sample_index != pending_pair_key:
                        if pending_pair:
                            delay_skipped += 1
                        pending_pair = {}
                    pending_pair_key = sample_index
                    pending_pair[pv_id] = waveform
                    if (
                        self.primary_pv_id in pending_pair
                        and self.secondary_pv_id in pending_pair
                    ):
                        delay_row = self._delay_for_pair(
                            sample_index,
                            pending_pair[self.primary_pv_id],
                            pending_pair[self.secondary_pv_id],
                        )
                        if delay_row is None:
                            delay_skipped += 1
                        else:
                            delay_rows.append(delay_row)
                        pending_pair = {}
                        pending_pair_key = None

            if pending_pair:
                delay_skipped += 1
            feature_rows = [
                self._finalize_feature_row(pv_id, states[pv_id])
                for pv_id in self.waveform_ids
                if states[pv_id]["record_count"] > 0
            ]
            delay_sample_indices = [row["sample_index"] for row in delay_rows]
            delay_samples = [row["delay_samples"] for row in delay_rows]
            delay_seconds = [row["delay_sec"] for row in delay_rows]
            delay_correlations = [row["peak_correlation"] for row in delay_rows]
            uses_seconds = bool(delay_rows) and all(
                math.isfinite(float(value)) for value in delay_seconds
            )
            delay_series = delay_seconds if uses_seconds else delay_samples
            delay_unit = "s" if uses_seconds else "samples"

            if not self.primary_pv_id or not self.secondary_pv_id:
                delay_summary = "Select two waveform objects to compute delay."
            elif not delay_rows:
                delay_summary = "No matched usable waveform pairs are available for delay analysis."
            else:
                finite_correlations = [
                    float(value)
                    for value in delay_correlations
                    if math.isfinite(float(value))
                ]
                delay_summary = (
                    f"Delay is positive when "
                    f"{self.labels_by_pv.get(self.secondary_pv_id, self.secondary_pv_id)} "
                    f"lags {self.labels_by_pv.get(self.primary_pv_id, self.primary_pv_id)}. "
                    f"Matched {len(delay_rows)} pair(s)."
                )
                if finite_correlations:
                    delay_summary += (
                        f" Correlation peak mean {np.mean(finite_correlations):.4g}; "
                        f"minimum {min(finite_correlations):.4g}."
                    )
            if delay_skipped:
                delay_summary += f" Skipped {delay_skipped} unusable or unmatched pair(s)."

            return {
                "request_signature": self.request_signature,
                "feature_rows": feature_rows,
                "delay_sample_indices": delay_sample_indices,
                "delay_series": delay_series,
                "delay_samples": delay_samples,
                "delay_seconds": delay_seconds,
                "delay_correlations": delay_correlations,
                "delay_matched_count": len(delay_rows),
                "delay_skipped_count": delay_skipped,
                "delay_unit": delay_unit,
                "delay_summary": delay_summary,
                "record_counts": {
                    pv_id: int(states[pv_id]["record_count"])
                    for pv_id in self.waveform_ids
                },
                "max_waveform_length": max_waveform_length,
            }

        @staticmethod
        def _append_invalid_features(state) -> None:
            for key in _FEATURE_KEYS:
                state["features"][key].append(float("nan"))
            state["polarities"].append("")

        @staticmethod
        def _append_features(state, features) -> None:
            values = {
                "baseline_mean": features.baseline_mean,
                "peak_value": features.peak_value,
                "peak_time_sec": features.peak_time_sec,
                "integral": features.integral,
                "rms": features.rms,
                "peak_to_peak": features.peak_to_peak,
                "baseline_noise_rms": features.baseline_noise_rms,
                "peak_amplitude": features.peak_amplitude,
                "pulse_peak_index": features.pulse_peak_index,
                "pulse_peak_time_sec": features.pulse_peak_time_sec,
                "area_samples": features.area_samples,
                "area": features.area,
                "fwhm_samples": features.fwhm_samples,
                "fwhm_sec": features.fwhm_sec,
                "snr": features.snr,
            }
            for key in _FEATURE_KEYS:
                state["features"][key].append(float(values[key]))
            state["polarities"].append(str(features.polarity))

        def _finalize_feature_row(self, pv_id: str, state) -> dict[str, object]:
            intervals = [
                float(value)
                for value in state["intervals"]
                if float(value) > 0.0
            ]
            has_complete_time_axis = (
                len(intervals) == state["record_count"]
                and bool(intervals)
                and all(
                    math.isclose(value, intervals[0], rel_tol=1.0e-9, abs_tol=1.0e-15)
                    for value in intervals
                )
            )
            features = dict(state["features"])
            if has_complete_time_axis:
                features["peak_position"] = list(features["pulse_peak_time_sec"])
                features["pulse_area"] = list(features["area"])
                features["fwhm"] = list(features["fwhm_sec"])
                feature_units = {
                    "peak_position": "s",
                    "pulse_area": "amplitude·s",
                    "fwhm": "s",
                }
            else:
                features["peak_position"] = list(features["pulse_peak_index"])
                features["pulse_area"] = list(features["area_samples"])
                features["fwhm"] = list(features["fwhm_samples"])
                feature_units = {
                    "peak_position": "sample",
                    "pulse_area": "amplitude·sample",
                    "fwhm": "sample",
                }

            aggregate = state["aggregate"].result()
            aggregate["sample_interval_sec"] = intervals[0] if has_complete_time_axis else 0.0
            aggregate["uses_time_axis"] = has_complete_time_axis
            raw_intervals = [float(value) for value in state["intervals"]]
            interval_variation = bool(raw_intervals) and any(
                not math.isclose(
                    value,
                    raw_intervals[0],
                    rel_tol=1.0e-9,
                    abs_tol=1.0e-15,
                )
                for value in raw_intervals[1:]
            )
            aggregate["interval_mismatch"] = interval_variation
            quality = dict(state["quality"])
            quality["sample_interval_variation"] = int(interval_variation)

            return {
                "pv_id": pv_id,
                "label": self.labels_by_pv.get(pv_id, pv_id),
                "sample_indices": list(state["sample_indices"]),
                "timestamps": list(state["timestamps"]),
                "features": features,
                "polarities": list(state["polarities"]),
                "feature_units": feature_units,
                "valid_count": int(state["valid_count"]),
                "record_count": int(state["record_count"]),
                "quality": quality,
                "aggregate": aggregate,
            }

        def _delay_for_pair(
            self,
            sample_index: int,
            left_record: WaveformRecord,
            right_record: WaveformRecord,
        ) -> dict[str, float] | None:
            if not left_record.connected or not right_record.connected:
                return None
            left_interval = float(left_record.waveform_sample_interval_sec or 0.0)
            right_interval = float(right_record.waveform_sample_interval_sec or 0.0)
            try:
                if left_interval > 0.0 and right_interval > 0.0:
                    if not math.isclose(
                        left_interval,
                        right_interval,
                        rel_tol=1.0e-9,
                        abs_tol=1.0e-15,
                    ):
                        return None
                    estimate = estimate_waveform_delay(
                        left_record.values,
                        right_record.values,
                        left_interval,
                        roi_start_index=self.roi_start_index,
                        roi_stop_index=self.roi_stop_index,
                        baseline_start_index=self.baseline_start_index,
                        baseline_stop_index=self.baseline_stop_index,
                    )
                elif left_interval <= 0.0 and right_interval <= 0.0:
                    estimate = estimate_waveform_delay_samples(
                        left_record.values,
                        right_record.values,
                        roi_start_index=self.roi_start_index,
                        roi_stop_index=self.roi_stop_index,
                        baseline_start_index=self.baseline_start_index,
                        baseline_stop_index=self.baseline_stop_index,
                    )
                else:
                    return None
            except Exception:
                return None
            return {
                "sample_index": int(sample_index),
                "delay_samples": float(estimate.delay_samples),
                "delay_sec": float(estimate.delay_sec),
                "peak_correlation": float(estimate.peak_correlation),
            }

        def _iter_records(self):
            if self.in_memory_records_by_pv:
                records = [
                    record
                    for pv_id in self.waveform_ids
                    for record in self.in_memory_records_by_pv.get(pv_id, [])
                ]
                records.sort(key=self._record_sort_key)
                yield from records
                return

            entries = [
                entry
                for pv_id in self.waveform_ids
                for entry in self.waveform_entries_by_pv.get(pv_id, [])
            ]
            entries.sort(key=self._record_sort_key)
            if self.waveform_iterator is not None:
                yield from self.waveform_iterator(entries)
                return
            if self.waveform_loader is None:
                raise RuntimeError("Waveform loader is unavailable for saved-run analysis.")
            for entry in entries:
                if not isinstance(entry, WaveformIndexEntry):
                    raise TypeError("Waveform entry loader requires WaveformIndexEntry inputs.")
                yield self.waveform_loader(entry)

        @staticmethod
        def _record_sort_key(record) -> tuple[object, ...]:
            batch_index = getattr(record, "batch_index", None)
            timestamp = getattr(record, "timestamp", None)
            return (
                batch_index is None,
                int(batch_index) if batch_index is not None else 0,
                timestamp,
                str(getattr(record, "pv_id", "")),
            )

else:

    class WaveformAnalysisWorker:  # pragma: no cover - fallback for non-GUI tests
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("PyQt5 is required to create WaveformAnalysisWorker")
