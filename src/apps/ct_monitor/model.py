from __future__ import annotations

import math
import statistics
import threading
from collections import deque
from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class SignalSample:
    value: float | None
    timestamp: float | None
    connected: bool
    units: str = ""
    status: int | None = None
    severity: int | None = None


@dataclass(frozen=True)
class TransmissionSample:
    timestamp: float
    upstream_nc: float
    downstream_nc: float
    efficiency_percent: float


@dataclass(frozen=True)
class PairingResult:
    status: str
    sample: TransmissionSample | None = None


@dataclass(frozen=True)
class PairingBatch:
    status: str
    samples: tuple[TransmissionSample, ...] = ()
    mismatched_samples: int = 0


class MonitorStore:
    """Thread-safe latest-value and bounded event queues for pyepics callbacks."""

    def __init__(self, queue_size: int = 512) -> None:
        if queue_size < 2:
            raise ValueError("queue_size must be at least 2")
        self._lock = threading.Lock()
        self._samples: dict[str, SignalSample] = {}
        self._queues: dict[str, deque[SignalSample]] = {}
        self._queue_size = queue_size

    def update(
        self,
        key: str,
        *,
        value: object,
        timestamp: object,
        connected: bool = True,
        units: str = "",
        status: int | None = None,
        severity: int | None = None,
    ) -> None:
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            numeric_value = None
        try:
            numeric_timestamp = float(timestamp)
        except (TypeError, ValueError):
            numeric_timestamp = None
        sample = SignalSample(
            value=numeric_value,
            timestamp=numeric_timestamp,
            connected=bool(connected),
            units=str(units or ""),
            status=status,
            severity=severity,
        )
        with self._lock:
            self._samples[key] = sample
            queue = self._queues.setdefault(key, deque(maxlen=self._queue_size))
            if not queue or queue[-1].timestamp != sample.timestamp:
                queue.append(sample)

    def set_connected(self, key: str, connected: bool) -> None:
        with self._lock:
            previous = self._samples.get(key)
            if previous is None:
                self._samples[key] = SignalSample(None, None, bool(connected))
                return
            self._samples[key] = SignalSample(
                value=previous.value,
                timestamp=previous.timestamp,
                connected=bool(connected),
                units=previous.units,
                status=previous.status,
                severity=previous.severity,
            )

    def snapshot(self) -> dict[str, SignalSample]:
        with self._lock:
            return dict(self._samples)

    def queued_snapshot(self) -> dict[str, tuple[SignalSample, ...]]:
        with self._lock:
            return {key: tuple(queue) for key, queue in self._queues.items()}

    def clear_queues(self, *keys: str) -> None:
        with self._lock:
            selected = keys or tuple(self._queues)
            for key in selected:
                queue = self._queues.get(key)
                if queue is not None:
                    queue.clear()


def calculate_efficiency(
    upstream_nc: float,
    downstream_nc: float,
    minimum_upstream_charge_nc: float,
) -> float | None:
    values = (upstream_nc, downstream_nc, minimum_upstream_charge_nc)
    if not all(math.isfinite(value) for value in values):
        return None
    if abs(upstream_nc) < minimum_upstream_charge_nc:
        return None
    return abs(downstream_nc) / abs(upstream_nc) * 100.0


def rolling_statistics(
    samples: list[TransmissionSample],
    window: int,
) -> tuple[float | None, float | None]:
    values = [sample.efficiency_percent for sample in samples[-window:]]
    if not values:
        return None, None
    mean = statistics.fmean(values)
    stddev = statistics.pstdev(values) if len(values) > 1 else 0.0
    return mean, stddev


def parse_bounded_integer_input(
    text: str,
    minimum: int,
    maximum: int,
) -> int | None:
    """Parse a user-entered integer without silently clamping invalid values."""

    try:
        value = int(text.strip())
    except (AttributeError, TypeError, ValueError):
        return None
    if value < minimum or value > maximum:
        return None
    return value


def downsample_transmission_samples(
    samples: Sequence[TransmissionSample],
    max_points: int,
) -> list[TransmissionSample]:
    """Keep synchronized extrema from each plot series within bounded buckets."""

    if max_points < 12:
        raise ValueError("max_points must be at least 12")
    if len(samples) <= max_points:
        return list(samples)

    bucket_count = max(1, (max_points - 2) // 6)
    interior_count = len(samples) - 2
    bucket_width = max(1, math.ceil(interior_count / bucket_count))
    selected = {0, len(samples) - 1}
    value_getters = (
        lambda sample: sample.upstream_nc,
        lambda sample: sample.downstream_nc,
        lambda sample: sample.efficiency_percent,
    )
    for start in range(1, len(samples) - 1, bucket_width):
        stop = min(len(samples) - 1, start + bucket_width)
        indices = range(start, stop)
        for getter in value_getters:
            selected.add(min(indices, key=lambda index: getter(samples[index])))
            selected.add(max(indices, key=lambda index: getter(samples[index])))
    return [samples[index] for index in sorted(selected)]


def downsample_scalar_points(
    points: Sequence[tuple[float, float]],
    max_points: int,
) -> list[tuple[float, float]]:
    if max_points < 4:
        raise ValueError("max_points must be at least 4")
    if len(points) <= max_points:
        return list(points)

    bucket_count = max(1, (max_points - 2) // 2)
    interior_count = len(points) - 2
    bucket_width = max(1, math.ceil(interior_count / bucket_count))
    selected = {0, len(points) - 1}
    for start in range(1, len(points) - 1, bucket_width):
        stop = min(len(points) - 1, start + bucket_width)
        indices = range(start, stop)
        selected.add(min(indices, key=lambda index: points[index][1]))
        selected.add(max(indices, key=lambda index: points[index][1]))
    return [points[index] for index in sorted(selected)]


class ShotPairer:
    def __init__(self) -> None:
        self._consumed_timestamps: dict[str, float] = {}

    def reset(self) -> None:
        self._consumed_timestamps.clear()

    def mark_current_consumed(
        self,
        samples: Mapping[str, SignalSample],
        *keys: str,
    ) -> None:
        for key in keys:
            sample = samples.get(key)
            if sample is not None and sample.timestamp is not None:
                self._consumed_timestamps[key] = sample.timestamp

    def try_pair(
        self,
        samples: Mapping[str, SignalSample],
        upstream_key: str,
        downstream_key: str,
        *,
        now: float,
        scale_to_nc: float,
        tolerance_s: float,
        stale_timeout_s: float | None,
        minimum_upstream_charge_nc: float,
    ) -> PairingResult:
        upstream = samples.get(upstream_key)
        downstream = samples.get(downstream_key)
        if upstream is None or downstream is None:
            return PairingResult("waiting for data")
        if not upstream.connected or not downstream.connected:
            return PairingResult("PV disconnected")
        if upstream.value is None or downstream.value is None:
            return PairingResult("invalid value")
        if (upstream.severity or 0) >= 2 or (downstream.severity or 0) >= 2:
            return PairingResult("PV alarm")
        if upstream.timestamp is None or downstream.timestamp is None:
            return PairingResult("missing timestamp")
        if not math.isfinite(upstream.timestamp) or not math.isfinite(downstream.timestamp):
            return PairingResult("missing timestamp")
        if not math.isfinite(upstream.value) or not math.isfinite(downstream.value):
            return PairingResult("invalid value")
        if stale_timeout_s is not None and (
            now - upstream.timestamp > stale_timeout_s
            or now - downstream.timestamp > stale_timeout_s
        ):
            return PairingResult("stale data")

        upstream_consumed = upstream.timestamp <= self._consumed_timestamps.get(
            upstream_key, float("-inf")
        )
        downstream_consumed = downstream.timestamp <= self._consumed_timestamps.get(
            downstream_key, float("-inf")
        )
        if upstream_consumed or downstream_consumed:
            return PairingResult("waiting for paired update")

        if abs(upstream.timestamp - downstream.timestamp) > tolerance_s:
            older_key, older_timestamp = (
                (upstream_key, upstream.timestamp)
                if upstream.timestamp < downstream.timestamp
                else (downstream_key, downstream.timestamp)
            )
            self._consumed_timestamps[older_key] = older_timestamp
            return PairingResult("timestamp mismatch")

        self._consumed_timestamps[upstream_key] = upstream.timestamp
        self._consumed_timestamps[downstream_key] = downstream.timestamp
        upstream_nc = upstream.value * scale_to_nc
        downstream_nc = downstream.value * scale_to_nc
        efficiency = calculate_efficiency(
            upstream_nc,
            downstream_nc,
            minimum_upstream_charge_nc,
        )
        if efficiency is None:
            return PairingResult("upstream below threshold")
        return PairingResult(
            "valid",
            TransmissionSample(
                timestamp=max(upstream.timestamp, downstream.timestamp),
                upstream_nc=upstream_nc,
                downstream_nc=downstream_nc,
                efficiency_percent=efficiency,
            ),
        )

    def pair_queued(
        self,
        queues: Mapping[str, Sequence[SignalSample]],
        latest: Mapping[str, SignalSample],
        upstream_key: str,
        downstream_key: str,
        *,
        now: float,
        scale_to_nc: float,
        tolerance_s: float,
        stale_timeout_s: float | None,
        minimum_upstream_charge_nc: float,
    ) -> PairingBatch:
        """Consume every currently pairable event, preserving shots between GUI refreshes."""

        latest_status = self._latest_status(
            latest,
            upstream_key,
            downstream_key,
            now=now,
            stale_timeout_s=stale_timeout_s,
        )
        if latest_status in {"waiting for data", "PV disconnected"}:
            return PairingBatch(latest_status)

        upstream = self._unconsumed_samples(queues.get(upstream_key, ()), upstream_key)
        downstream = self._unconsumed_samples(queues.get(downstream_key, ()), downstream_key)
        if not upstream or not downstream:
            status = latest_status if latest_status != "ready" else "waiting for paired update"
            return PairingBatch(status)

        paired: list[TransmissionSample] = []
        mismatched = 0
        last_rejection: str | None = None
        up_index = 0
        down_index = 0
        while up_index < len(upstream) and down_index < len(downstream):
            up = upstream[up_index]
            down = downstream[down_index]
            assert up.timestamp is not None
            assert down.timestamp is not None
            delta = up.timestamp - down.timestamp
            if abs(delta) > tolerance_s:
                if delta < 0:
                    self._consumed_timestamps[upstream_key] = up.timestamp
                    up_index += 1
                else:
                    self._consumed_timestamps[downstream_key] = down.timestamp
                    down_index += 1
                mismatched += 1
                continue

            self._consumed_timestamps[upstream_key] = up.timestamp
            self._consumed_timestamps[downstream_key] = down.timestamp
            up_index += 1
            down_index += 1
            result = self._build_pair(
                up,
                down,
                now=now,
                scale_to_nc=scale_to_nc,
                stale_timeout_s=stale_timeout_s,
                minimum_upstream_charge_nc=minimum_upstream_charge_nc,
            )
            if result.sample is not None:
                paired.append(result.sample)
            else:
                last_rejection = result.status

        if paired:
            return PairingBatch(last_rejection or "valid", tuple(paired), mismatched)
        if last_rejection is not None:
            return PairingBatch(last_rejection, mismatched_samples=mismatched)
        if mismatched:
            return PairingBatch("timestamp mismatch", mismatched_samples=mismatched)
        return PairingBatch("waiting for paired update")

    def _unconsumed_samples(
        self,
        samples: Sequence[SignalSample],
        key: str,
    ) -> list[SignalSample]:
        consumed = self._consumed_timestamps.get(key, float("-inf"))
        return sorted(
            (
                sample
                for sample in samples
                if sample.timestamp is not None
                and math.isfinite(sample.timestamp)
                and sample.timestamp > consumed
            ),
            key=lambda sample: sample.timestamp,
        )

    @staticmethod
    def _latest_status(
        samples: Mapping[str, SignalSample],
        upstream_key: str,
        downstream_key: str,
        *,
        now: float,
        stale_timeout_s: float | None,
    ) -> str:
        upstream = samples.get(upstream_key)
        downstream = samples.get(downstream_key)
        if upstream is None or downstream is None:
            return "waiting for data"
        if not upstream.connected or not downstream.connected:
            return "PV disconnected"
        if upstream.value is None or downstream.value is None:
            return "invalid value"
        if (upstream.severity or 0) >= 2 or (downstream.severity or 0) >= 2:
            return "PV alarm"
        if upstream.timestamp is None or downstream.timestamp is None:
            return "missing timestamp"
        if not math.isfinite(upstream.timestamp) or not math.isfinite(downstream.timestamp):
            return "missing timestamp"
        if not math.isfinite(upstream.value) or not math.isfinite(downstream.value):
            return "invalid value"
        if stale_timeout_s is not None and (
            now - upstream.timestamp > stale_timeout_s
            or now - downstream.timestamp > stale_timeout_s
        ):
            return "stale data"
        return "ready"

    @staticmethod
    def _build_pair(
        upstream: SignalSample,
        downstream: SignalSample,
        *,
        now: float,
        scale_to_nc: float,
        stale_timeout_s: float | None,
        minimum_upstream_charge_nc: float,
    ) -> PairingResult:
        if upstream.value is None or downstream.value is None:
            return PairingResult("invalid value")
        if (upstream.severity or 0) >= 2 or (downstream.severity or 0) >= 2:
            return PairingResult("PV alarm")
        if not math.isfinite(upstream.value) or not math.isfinite(downstream.value):
            return PairingResult("invalid value")
        assert upstream.timestamp is not None
        assert downstream.timestamp is not None
        if stale_timeout_s is not None and (
            now - upstream.timestamp > stale_timeout_s
            or now - downstream.timestamp > stale_timeout_s
        ):
            return PairingResult("stale data")
        upstream_nc = upstream.value * scale_to_nc
        downstream_nc = downstream.value * scale_to_nc
        efficiency = calculate_efficiency(
            upstream_nc,
            downstream_nc,
            minimum_upstream_charge_nc,
        )
        if efficiency is None:
            return PairingResult("upstream below threshold")
        return PairingResult(
            "valid",
            TransmissionSample(
                timestamp=max(upstream.timestamp, downstream.timestamp),
                upstream_nc=upstream_nc,
                downstream_nc=downstream_nc,
                efficiency_percent=efficiency,
            ),
        )
