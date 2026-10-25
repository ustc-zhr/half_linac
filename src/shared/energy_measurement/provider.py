"""Quality-gated sampling for a model-backed three-BPM energy source."""

from __future__ import annotations

import math
import statistics
import time
from collections import deque
from collections.abc import Mapping

from .bpm_transport import BpmEnergySample, BpmTransportModel


class BpmEnergySampler:
    def __init__(
        self,
        client,
        source: Mapping[str, object],
        model: BpmTransportModel,
        startup_k1_values: Mapping[str, float],
    ) -> None:
        self.client = client
        self.source = source
        self.model = model
        self.startup_k1_values = {
            str(key): float(value) for key, value in startup_k1_values.items()
        }
        self._median = deque(maxlen=int(source["median_window"]))
        self._k1_hold_reason: str | None = None

    @property
    def required_keys(self) -> tuple[str, ...]:
        return (
            *tuple(self.source["bpm_keys"].values()),
            str(self.source["reference_key"]),
            *tuple(self.source["k1_keys"].values()),
        )

    @property
    def k1_hold_latched(self) -> bool:
        return self._k1_hold_reason is not None

    def reset_filter(self) -> None:
        self._median.clear()

    def sample(self, sampled_at: float | None = None) -> BpmEnergySample:
        now = time.time() if sampled_at is None else float(sampled_at)
        try:
            readings = self.client.read_many(self.required_keys)
        except Exception as exc:  # noqa: BLE001 - represented as HOLD data
            self.reset_filter()
            return self._empty_hold(now, f"read failure: {exc}")
        return self.evaluate(readings, sampled_at=now)

    def evaluate(
        self, readings: Mapping[str, object], *, sampled_at: float
    ) -> BpmEnergySample:
        now = float(sampled_at)
        bpm_ids = tuple(self.source["bpm_ids"])
        scale = float(self.source["bpm_scale_to_mm"])
        bpm_values: dict[str, float | None] = {}
        bpm_timestamps: dict[str, float | None] = {}
        raw_values: dict[str, float | None] = {}
        for key in self.required_keys:
            raw_values[key] = _finite_or_none(
                getattr(readings.get(key), "value", None)
            )
        for bpm_id in bpm_ids:
            key = self.source["bpm_keys"][bpm_id]
            reading = readings.get(key)
            raw = _finite_or_none(getattr(reading, "value", None))
            bpm_values[bpm_id] = None if raw is None else raw * scale
            bpm_timestamps[bpm_id] = _finite_or_none(
                getattr(reading, "source_timestamp", None)
            )

        reference = _finite_or_none(
            getattr(readings.get(self.source["reference_key"]), "value", None)
        )
        k1_values = {
            quadrupole: _finite_or_none(
                getattr(readings.get(key), "value", None)
            )
            for quadrupole, key in self.source["k1_keys"].items()
        }
        reason = self._quality_reason(
            readings,
            bpm_values,
            bpm_timestamps,
            reference,
            k1_values,
            now,
        )
        timestamps = [
            value for value in bpm_timestamps.values() if value is not None
        ]
        skew = max(timestamps) - min(timestamps) if len(timestamps) == 3 else None
        if reason is not None:
            self.reset_filter()
            return BpmEnergySample(
                sampled_at=now,
                source_timestamp=None,
                bpm_values_mm=bpm_values,
                bpm_source_timestamps=bpm_timestamps,
                timestamp_skew_s=skew,
                reference_energy_mev=reference,
                delta=None,
                delta_energy_mev=None,
                energy_raw_mev=None,
                energy_filtered_mev=None,
                k1_values_per_m2=k1_values,
                quality_state="HOLD",
                quality_reason=reason,
                raw_pv_values=raw_values,
            )

        positions = [float(bpm_values[bpm_id]) for bpm_id in bpm_ids]
        delta, delta_energy, energy = self.model.energy_mev(
            positions, float(reference)
        )
        self._median.append(energy)
        return BpmEnergySample(
            sampled_at=now,
            source_timestamp=max(float(value) for value in bpm_timestamps.values()),
            bpm_values_mm=bpm_values,
            bpm_source_timestamps=bpm_timestamps,
            timestamp_skew_s=skew,
            reference_energy_mev=reference,
            delta=delta,
            delta_energy_mev=delta_energy,
            energy_raw_mev=energy,
            energy_filtered_mev=float(statistics.median(self._median)),
            k1_values_per_m2=k1_values,
            quality_state="OK",
            quality_reason="",
            raw_pv_values=raw_values,
        )

    def _quality_reason(
        self,
        readings: Mapping[str, object],
        bpm_values: Mapping[str, float | None],
        bpm_timestamps: Mapping[str, float | None],
        reference: float | None,
        k1_values: Mapping[str, float | None],
        sampled_at: float,
    ) -> str | None:
        bpm_ids = tuple(self.source["bpm_ids"])
        for bpm_id in bpm_ids:
            key = self.source["bpm_keys"][bpm_id]
            reading = readings.get(key)
            if reading is None or not bool(getattr(reading, "connected", False)):
                return f"{bpm_id} disconnected"
            if bpm_values[bpm_id] is None:
                return f"{bpm_id} returned a non-finite position"
            timestamp = bpm_timestamps[bpm_id]
            if timestamp is None:
                return f"{bpm_id} has no source timestamp"
            age = sampled_at - timestamp
            stale_timeout = float(self.source["runtime"]["stale_timeout_s"])
            if age > stale_timeout:
                return f"{bpm_id} timestamp is stale ({age:.3f} s)"
            if age < -stale_timeout:
                return f"{bpm_id} source timestamp is in the future"

        timestamps = [float(bpm_timestamps[bpm_id]) for bpm_id in bpm_ids]
        skew = max(timestamps) - min(timestamps)
        max_skew = float(self.source["runtime"]["max_bpm_timestamp_skew_s"])
        if skew > max_skew:
            return f"BPM timestamp skew {skew:.3f} s exceeds {max_skew:.3f} s"
        zero_threshold = float(self.source["model"].get("zero_threshold_mm", 1e-6))
        if all(abs(float(bpm_values[bpm_id])) <= zero_threshold for bpm_id in bpm_ids):
            return "all three BPM positions are near zero"

        reference_reading = readings.get(self.source["reference_key"])
        if reference_reading is None or not bool(
            getattr(reference_reading, "connected", False)
        ):
            return "LINAC_ENERGY disconnected"
        if reference is None:
            return "LINAC_ENERGY returned a non-finite value"
        low, high = self.source["valid_range_mev"]
        if not float(low) <= reference <= float(high):
            return (
                f"LINAC_ENERGY {reference:.6g} MeV is outside "
                f"[{float(low):.6g}, {float(high):.6g}] MeV"
            )

        for quadrupole, key in self.source["k1_keys"].items():
            reading = readings.get(key)
            if reading is None or not bool(getattr(reading, "connected", False)):
                return f"{quadrupole} K1_total disconnected"
            current = k1_values[quadrupole]
            if current is None:
                return f"{quadrupole} K1_total is non-finite"
            initial = self.startup_k1_values[quadrupole]
            absolute_tolerance = float(
                self.source["model"]["k1_abs_tolerance_per_m2"]
            )
            relative_tolerance = float(
                self.source["model"]["k1_rel_tolerance"]
            )
            difference = abs(current - initial)
            threshold = max(
                absolute_tolerance,
                relative_tolerance * abs(initial),
            )
            if difference > threshold and self._k1_hold_reason is None:
                self._k1_hold_reason = (
                    f"{quadrupole} K1_total drifted by {difference:.6g} 1/m^2 "
                    f"(limit {threshold:.6g}); restart acquisition to rebuild model"
                )
        return self._k1_hold_reason

    def _empty_hold(self, sampled_at: float, reason: str) -> BpmEnergySample:
        bpm_ids = tuple(self.source["bpm_ids"])
        quadrupoles = tuple(self.source["quadrupoles"])
        return BpmEnergySample(
            sampled_at=sampled_at,
            source_timestamp=None,
            bpm_values_mm={bpm_id: None for bpm_id in bpm_ids},
            bpm_source_timestamps={bpm_id: None for bpm_id in bpm_ids},
            timestamp_skew_s=None,
            reference_energy_mev=None,
            delta=None,
            delta_energy_mev=None,
            energy_raw_mev=None,
            energy_filtered_mev=None,
            k1_values_per_m2={quadrupole: None for quadrupole in quadrupoles},
            quality_state="HOLD",
            quality_reason=reason,
        )


def _finite_or_none(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None
