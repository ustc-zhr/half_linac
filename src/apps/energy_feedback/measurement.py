"""Measurement-provider interface and direct-energy implementation."""

from __future__ import annotations

import math
import statistics
import time
from collections import deque
from collections.abc import Mapping
from typing import Callable, Protocol

from .client import PVClient
from .models import EnergyMeasurement, MeasurementQuality, PVReading


class MeasurementProvider(Protocol):
    @property
    def required_keys(self) -> tuple[str, ...]:
        ...

    def sample(self, now: float | None = None) -> EnergyMeasurement:
        ...

    def reset(self) -> None:
        ...


class DirectEnergyProvider:
    """Use one absolute-energy PV and an optional external target PV."""

    def __init__(
        self,
        client: PVClient,
        *,
        valid_range_mev: tuple[float, float],
        stale_timeout_s: float,
        diagnostic_keys: tuple[str, ...] = (),
        marker_key: str | None = None,
        median_window: int = 1,
        target_key: str | None = "target",
    ) -> None:
        self.client = client
        self.valid_range_mev = tuple(float(value) for value in valid_range_mev)
        self.stale_timeout_s = float(stale_timeout_s)
        self.diagnostic_keys = tuple(diagnostic_keys)
        self.marker_key = marker_key
        self.target_key = target_key
        self._energy_history: deque[float] = deque(maxlen=int(median_window))

    @property
    def required_keys(self) -> tuple[str, ...]:
        keys = ["energy", *self.diagnostic_keys]
        if self.target_key:
            keys.append(self.target_key)
        if self.marker_key:
            keys.append(self.marker_key)
        return tuple(keys)

    def reset(self) -> None:
        self._energy_history.clear()

    def sample(self, now: float | None = None) -> EnergyMeasurement:
        sampled_at = time.time() if now is None else float(now)
        try:
            readings = self.client.read_many(self.required_keys)
        except Exception as exc:  # noqa: BLE001 - all acquisition errors become HOLD
            self.reset()
            return self._invalid(sampled_at, f"read failure: {exc}", connected=False)

        energy = readings.get("energy")
        target = readings.get(self.target_key) if self.target_key else None
        error = self._validate_required(energy, target, sampled_at)
        diagnostics, diagnostic_errors, diagnostic_timestamps = self._diagnostics(
            readings, sampled_at
        )
        marker = readings.get(self.marker_key) if self.marker_key else None

        if error:
            self.reset()
            return EnergyMeasurement(
                sampled_at=sampled_at,
                source_timestamp=energy.source_timestamp if energy else None,
                target_source_timestamp=target.source_timestamp if target else None,
                energy_mev=_finite_value(energy),
                target_mev=_finite_value(target),
                error_mev=None,
                diagnostics=diagnostics,
                diagnostic_errors=diagnostic_errors,
                quality=MeasurementQuality(
                    "HOLD",
                    error,
                    connected=bool(
                        energy
                        and energy.connected
                        and (target is None or target.connected)
                    ),
                    stale="stale" in error.lower(),
                ),
                diagnostic_source_timestamps=diagnostic_timestamps,
                marker_value=_finite_value(marker),
                marker_source_timestamp=marker.source_timestamp if marker else None,
            )

        assert energy is not None and energy.value is not None
        self._energy_history.append(float(energy.value))
        filtered_energy = float(statistics.median(self._energy_history))
        target_mev = _finite_value(target)
        return EnergyMeasurement(
            sampled_at=sampled_at,
            source_timestamp=energy.source_timestamp,
            target_source_timestamp=(target.source_timestamp if target else None),
            energy_mev=filtered_energy,
            target_mev=target_mev,
            error_mev=(
                filtered_energy - target_mev if target_mev is not None else None
            ),
            diagnostics=diagnostics,
            diagnostic_errors=diagnostic_errors,
            quality=MeasurementQuality("OK", "measurement valid", connected=True),
            diagnostic_source_timestamps=diagnostic_timestamps,
            marker_value=_finite_value(marker),
            marker_source_timestamp=marker.source_timestamp if marker else None,
        )

    def _validate_required(
        self,
        energy: PVReading | None,
        target: PVReading | None,
        now: float,
    ) -> str:
        if energy is None:
            return "energy result missing"
        if not energy.connected:
            return f"energy disconnected: {energy.error or 'not connected'}"
        if energy.error:
            return f"energy invalid: {energy.error}"
        if energy.value is None or not math.isfinite(energy.value):
            return "energy is not finite"
        if energy.source_timestamp is None:
            return "energy source timestamp missing"
        age = now - energy.source_timestamp
        if age > self.stale_timeout_s:
            return f"energy source timestamp stale ({age:.2f} s)"
        if age < -self.stale_timeout_s:
            return "energy source timestamp is in the future"
        assert energy is not None and energy.value is not None
        low, high = self.valid_range_mev
        if not low <= energy.value <= high:
            return f"energy outside valid range [{low:g}, {high:g}] MeV"
        if self.target_key:
            if target is None:
                return "target result missing"
            if not target.connected:
                return f"target disconnected: {target.error or 'not connected'}"
            if target.error:
                return f"target invalid: {target.error}"
            if target.value is None or not math.isfinite(target.value):
                return "target is not finite"
            if not low <= target.value <= high:
                return f"target outside valid range [{low:g}, {high:g}] MeV"
        return ""

    def _diagnostics(
        self,
        readings: Mapping[str, PVReading],
        now: float,
    ) -> tuple[
        dict[str, float | None],
        dict[str, str],
        dict[str, float | None],
    ]:
        values: dict[str, float | None] = {}
        errors: dict[str, str] = {}
        timestamps: dict[str, float | None] = {}
        for key in self.diagnostic_keys:
            reading = readings.get(key)
            value = _finite_value(reading)
            values[key] = value
            timestamps[key] = reading.source_timestamp if reading else None
            if reading is None:
                errors[key] = "result missing"
            elif not reading.connected:
                errors[key] = reading.error or "disconnected"
            elif value is None:
                errors[key] = reading.error or "not finite"
            elif reading.source_timestamp is None:
                errors[key] = "source timestamp missing"
            elif now - reading.source_timestamp > self.stale_timeout_s:
                errors[key] = "source timestamp stale"
        return values, errors, timestamps

    @staticmethod
    def _invalid(sampled_at: float, reason: str, *, connected: bool) -> EnergyMeasurement:
        return EnergyMeasurement(
            sampled_at=sampled_at,
            source_timestamp=None,
            target_source_timestamp=None,
            energy_mev=None,
            target_mev=None,
            error_mev=None,
            diagnostics={},
            diagnostic_errors={},
            quality=MeasurementQuality("HOLD", reason, connected=connected),
            diagnostic_source_timestamps={},
        )


def _finite_value(reading: PVReading | None) -> float | None:
    if reading is None or reading.value is None:
        return None
    value = float(reading.value)
    return value if math.isfinite(value) else None


PROVIDER_FACTORIES: dict[str, Callable[..., MeasurementProvider]] = {
    "direct_energy": DirectEnergyProvider,
    "energy_spectrum_target": DirectEnergyProvider,
}


def register_measurement_provider(
    kind: str,
    factory: Callable[..., MeasurementProvider],
) -> None:
    """Register a future provider without changing the acquisition engine."""

    name = str(kind).strip()
    if not name:
        raise ValueError("Measurement provider kind must not be empty.")
    if name in PROVIDER_FACTORIES:
        raise ValueError(f"Measurement provider {name!r} is already registered.")
    PROVIDER_FACTORIES[name] = factory
