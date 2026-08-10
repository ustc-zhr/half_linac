from __future__ import annotations

import math

try:
    from PyQt5 import QtCore
except ImportError:  # pragma: no cover - optional runtime dependency
    QtCore = None

from ..analysis.waveform import compute_waveform_features, estimate_waveform_delay
from ..domain.types import WaveformIndexEntry, WaveformRecord


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
            in_memory_records_by_pv=None,
            waveform_entries_by_pv=None,
            waveform_loader=None,
        ) -> None:
            super().__init__()
            self.waveform_ids = [str(item) for item in waveform_ids]
            self.labels_by_pv = {str(key): str(value) for key, value in dict(labels_by_pv).items()}
            self.request_signature = tuple(request_signature)
            self.roi_start_index = int(roi_start_index)
            self.roi_stop_index = int(roi_stop_index)
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
            self.signals = WaveformAnalysisSignals()
            self._record_cache: dict[tuple[str, int], WaveformRecord] = {}

        @QtCore.pyqtSlot()
        def run(self) -> None:
            try:
                record_counts = {
                    pv_id: self._record_count_for(pv_id)
                    for pv_id in self.waveform_ids
                }
                max_waveform_length = 0
                feature_rows = []
                for pv_id in self.waveform_ids:
                    count = record_counts.get(pv_id, 0)
                    if count <= 0:
                        continue
                    sample_indices = []
                    feature_series = {
                        "baseline_mean": [],
                        "peak_value": [],
                        "peak_time_sec": [],
                        "integral": [],
                        "rms": [],
                        "peak_to_peak": [],
                    }
                    valid_count = 0
                    for record_index in range(count):
                        waveform = self._resolve_record(pv_id, record_index)
                        max_waveform_length = max(max_waveform_length, len(waveform.values))
                        sample_indices.append(
                            int(waveform.batch_index) if waveform.batch_index is not None else int(record_index)
                        )
                        try:
                            features = compute_waveform_features(
                                waveform.values,
                                waveform.waveform_sample_interval_sec,
                                roi_start_index=self.roi_start_index,
                                roi_stop_index=self.roi_stop_index,
                            )
                        except Exception:
                            for key in feature_series:
                                feature_series[key].append(float("nan"))
                            continue
                        valid_count += 1
                        feature_series["baseline_mean"].append(float(features.baseline_mean))
                        feature_series["peak_value"].append(float(features.peak_value))
                        feature_series["peak_time_sec"].append(float(features.peak_time_sec))
                        feature_series["integral"].append(float(features.integral))
                        feature_series["rms"].append(float(features.rms))
                        feature_series["peak_to_peak"].append(float(features.peak_to_peak))
                    feature_rows.append(
                        {
                            "pv_id": pv_id,
                            "label": self.labels_by_pv.get(pv_id, pv_id),
                            "sample_indices": sample_indices,
                            "features": feature_series,
                            "valid_count": valid_count,
                            "record_count": count,
                        }
                    )

                delay_series = []
                delay_sample_indices = []
                delay_skipped = 0
                delay_summary = "Select two waveform objects to compute time delay."
                if self.primary_pv_id and self.secondary_pv_id:
                    left_count = record_counts.get(self.primary_pv_id, 0)
                    right_count = record_counts.get(self.secondary_pv_id, 0)
                    compare_count = min(left_count, right_count)
                    if compare_count > 0:
                        delay_summary = (
                            f"Delay is positive when {self.labels_by_pv.get(self.secondary_pv_id, self.secondary_pv_id)} "
                            f"lags {self.labels_by_pv.get(self.primary_pv_id, self.primary_pv_id)}."
                        )
                    for record_index in range(compare_count):
                        left_record = self._resolve_record(self.primary_pv_id, record_index)
                        right_record = self._resolve_record(self.secondary_pv_id, record_index)
                        delay_sample_indices.append(
                            int(left_record.batch_index) if left_record.batch_index is not None else int(record_index)
                        )
                        if not left_record.connected or not right_record.connected:
                            delay_series.append(float("nan"))
                            delay_skipped += 1
                            continue
                        if not math.isclose(
                            float(left_record.waveform_sample_interval_sec),
                            float(right_record.waveform_sample_interval_sec),
                            rel_tol=1.0e-9,
                            abs_tol=1.0e-12,
                        ):
                            delay_series.append(float("nan"))
                            delay_skipped += 1
                            continue
                        try:
                            estimate = estimate_waveform_delay(
                                left_record.values,
                                right_record.values,
                                left_record.waveform_sample_interval_sec,
                                roi_start_index=self.roi_start_index,
                                roi_stop_index=self.roi_stop_index,
                            )
                        except Exception:
                            delay_series.append(float("nan"))
                            delay_skipped += 1
                            continue
                        delay_series.append(float(estimate.delay_sec))
                    if compare_count <= 0:
                        delay_summary = "Need two waveform objects with at least one shared shot to compute time delay."
                    elif delay_skipped:
                        delay_summary += f" Skipped {delay_skipped} shot(s) with unusable waveform pairs."

                self.signals.finished.emit(
                    {
                        "request_signature": self.request_signature,
                        "feature_rows": feature_rows,
                        "delay_sample_indices": delay_sample_indices,
                        "delay_series": delay_series,
                        "delay_summary": delay_summary,
                        "record_counts": record_counts,
                        "max_waveform_length": max_waveform_length,
                    }
                )
            except Exception as exc:  # pragma: no cover - thread runtime integration
                self.signals.failed.emit(str(exc))

        def _record_count_for(self, pv_id: str) -> int:
            if pv_id in self.in_memory_records_by_pv:
                return len(self.in_memory_records_by_pv[pv_id])
            return len(self.waveform_entries_by_pv.get(pv_id, []))

        def _resolve_record(self, pv_id: str, record_index: int) -> WaveformRecord:
            cache_key = (pv_id, int(record_index))
            cached = self._record_cache.get(cache_key)
            if cached is not None:
                return cached
            if pv_id in self.in_memory_records_by_pv:
                record = self.in_memory_records_by_pv[pv_id][record_index]
            else:
                if self.waveform_loader is None:
                    raise RuntimeError("Waveform loader is unavailable for saved-run analysis.")
                entry = self.waveform_entries_by_pv.get(pv_id, [])[record_index]
                if not isinstance(entry, WaveformIndexEntry):
                    raise TypeError("Waveform entry loader requires WaveformIndexEntry inputs.")
                record = self.waveform_loader(entry)
            self._record_cache[cache_key] = record
            return record

else:

    class WaveformAnalysisWorker:  # pragma: no cover - fallback for non-GUI tests
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("PyQt5 is required to create WaveformAnalysisWorker")
