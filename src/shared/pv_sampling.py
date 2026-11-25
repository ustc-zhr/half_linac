from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

from .machine_state import (
    CapturePoint,
    SampleQuality,
    SnapshotEntry,
    value_type,
)


@dataclass(frozen=True)
class CaptureSamplingResult:
    entries: tuple[SnapshotEntry, ...]
    status: str
    requested_count: int


@dataclass(frozen=True)
class _PvSample:
    value: Any
    unit: str | None
    source_timestamp: float | None
    received_at: str | None
    alarm_status: int | str | None
    alarm_severity: int | None
    native_count: int | None
    quality: SampleQuality
    detail: str


@dataclass(frozen=True)
class _CaRead:
    chid: Any
    cache: Any
    time_type: int
    ctrl_type: int


def sample_capture_points(
    points: Iterable[CapturePoint],
    total_timeout_s: float,
    *,
    on_progress: Callable[[SnapshotEntry, int, int], None] | None = None,
    stop_requested: Callable[[], bool] | None = None,
    pv_factory: Callable[[str], Any] | None = None,
    poll_function: Callable[[float], None] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    utc_now: Callable[[], datetime] | None = None,
    batch_reads: bool = False,
) -> CaptureSamplingResult:
    """Read a capture plan using one bounded, read-only EPICS batch."""
    if total_timeout_s <= 0:
        raise ValueError("Capture timeout must be greater than zero.")
    if batch_reads and pv_factory is not None:
        raise ValueError("Batch reads require the native Channel Access reader")

    capture_points = tuple(points)
    should_stop = stop_requested or (lambda: False)
    progress = on_progress or (lambda _entry, _index, _total: None)
    now_utc = utc_now or (lambda: datetime.now(timezone.utc))
    factory, poll = _default_epics_helpers(pv_factory, poll_function)
    deadline = monotonic() + total_timeout_s

    unique_names = tuple(
        dict.fromkeys(point.endpoint.pv_name for point in capture_points)
    )
    pv_by_name: dict[str, Any] = {}
    samples: dict[str, _PvSample] = {}
    cancelled = False

    try:
        for pv_name in unique_names:
            if should_stop():
                cancelled = True
                break
            try:
                pv_by_name[pv_name] = factory(pv_name)
            except Exception as exc:
                samples[pv_name] = _error_sample(
                    SampleQuality.READ_ERROR,
                    f"{type(exc).__name__}: {exc}",
                )

        read = _read_ca_batch if batch_reads else _read_sequentially
        samples.update(read(pv_by_name, deadline, total_timeout_s, should_stop, poll, monotonic, now_utc))
        cancelled = cancelled or any(sample.quality == SampleQuality.CANCELLED for sample in samples.values())
    finally:
        for pv in pv_by_name.values():
            try:
                clear_callbacks = getattr(pv, "clear_callbacks", None)
                if callable(clear_callbacks):
                    clear_callbacks()
            except Exception:
                pass
            try:
                disconnect = getattr(pv, "disconnect", None)
                if callable(disconnect):
                    disconnect()
            except Exception:
                pass

    entries = []
    total = len(capture_points)
    for index, point in enumerate(capture_points, start=1):
        sample = samples.get(point.endpoint.pv_name)
        if sample is None:
            sample = _error_sample(SampleQuality.CANCELLED, "Capture cancelled")
            cancelled = True
        detail = sample.detail
        unit = point.unit or sample.unit
        if point.unit and sample.unit and point.unit.casefold() != sample.unit.casefold():
            mismatch = f"EPICS unit {sample.unit!r} differs from profile unit {point.unit!r}"
            detail = f"{detail}; {mismatch}" if detail else mismatch
        entry = SnapshotEntry(
            key=point.key,
            element_id=point.endpoint.element_id,
            element_kind=point.endpoint.element_kind,
            element_order=point.element_order,
            display_name=point.display_name,
            logical_channel=point.endpoint.logical_channel,
            pv_name=point.endpoint.pv_name,
            state_class=point.state_class,
            value=sample.value,
            value_type=value_type(sample.value),
            unit=unit,
            source_timestamp=sample.source_timestamp,
            received_at=sample.received_at,
            alarm_status=sample.alarm_status,
            alarm_severity=sample.alarm_severity,
            native_count=sample.native_count,
            quality=sample.quality,
            detail=detail,
        )
        entries.append(entry)
        progress(entry, index, total)

    status = _capture_status(tuple(entries), cancelled)
    return CaptureSamplingResult(tuple(entries), status, len(capture_points))


def _read_sequentially(pvs, deadline, timeout_s, should_stop, poll, monotonic, now_utc):
    pending = set(pvs)
    while pending and not should_stop():
        pending = {name for name in pending if not bool(getattr(pvs[name], "connected", False))}
        remaining = deadline - monotonic()
        if not pending or remaining <= 0:
            break
        poll(min(0.02, remaining))

    samples = {name: _unread_sample(should_stop(), SampleQuality.DISCONNECTED,
                                   f"No connection within {timeout_s:g} s") for name in pending}
    for name, pv in pvs.items():
        if name in samples:
            continue
        stopped = should_stop()
        remaining = deadline - monotonic()
        if stopped or remaining <= 0:
            samples[name] = _unread_sample(stopped, SampleQuality.READ_ERROR,
                                           f"Capture exceeded shared timeout of {timeout_s:g} s")
        else:
            samples[name] = _read_one(pv, remaining, now_utc)
    return samples


def _read_ca_batch(pvs, deadline, timeout_s, should_stop, poll, monotonic, now_utc):
    from epics import ca

    waiting, active, samples = dict(pvs), {}, {}
    while (waiting or active) and not should_stop() and monotonic() < deadline:
        sent = False
        for name, pv in tuple(waiting.items()):
            if should_stop() or monotonic() >= deadline:
                break
            try:
                if not pv.connected or not ca.isConnected(pv.chid):
                    continue
                del waiting[name]
                count = ca.element_count(pv.chid)
                if (error := _invalid_count(count, None, now_utc)) is not None:
                    samples[name] = error
                    continue
                cache = ca.get_cache(name)
                time_type = ca.promote_type(pv.chid, use_time=True)
                ctrl_type = ca.promote_type(pv.chid, use_ctrl=True)
                types = tuple(dict.fromkeys((time_type, ctrl_type)))
                # A previous timed-out GET is still owned by its original batch.
                # PyEPICS would otherwise attach a new get() to that old reply.
                with cache.lock:
                    if any(cache.get_results.get(kind, [None])[0] is ca.GET_PENDING for kind in types):
                        raise RuntimeError("An earlier Channel Access read is still pending")
                for kind in types:
                    ca.get_with_metadata(pv.chid, ftype=kind, count=1, wait=False, timeout=0)
                active[name] = _CaRead(pv.chid, cache, time_type, ctrl_type)
                sent = True
            except Exception as exc:
                waiting.pop(name, None)
                samples[name] = _error_sample(SampleQuality.READ_ERROR, f"{type(exc).__name__}: {exc}")
        if sent:
            ca.flush_io()

        for name, request in tuple(active.items()):
            if should_stop() or monotonic() >= deadline:
                break
            # get_complete_with_metadata waits if pending. Inspect the PyEPICS
            # callback slots so a single slow PV cannot hold up ready replies.
            with request.cache.lock:
                slots = request.cache.get_results
                replies = [slots.get(kind, [None])[0] for kind in (request.time_type, request.ctrl_type)]
            if any(reply is ca.GET_PENDING for reply in replies):
                continue
            del active[name]
            try:
                if any(reply is None for reply in replies):
                    raise RuntimeError("EPICS value or control metadata was not requested")
                metadata = ca.get_complete_with_metadata(request.chid, ftype=request.time_type, count=1, timeout=0)
                if metadata is not None and request.ctrl_type != request.time_type:
                    control = ca.get_complete_with_metadata(request.chid, ftype=request.ctrl_type, count=1, timeout=0)
                    if control is None:
                        raise RuntimeError("EPICS control metadata was not received")
                    metadata = {**metadata, "units": control.get("units")}
                samples[name] = _sample_from_metadata(metadata, 1, now_utc)
            except Exception as exc:
                samples[name] = _error_sample(SampleQuality.READ_ERROR, f"{type(exc).__name__}: {exc}", count=1)

        remaining = deadline - monotonic()
        if (waiting or active) and remaining > 0 and not should_stop():
            poll(min(0.005, remaining))

    stopped = should_stop()
    for name in waiting:
        samples[name] = _unread_sample(stopped, SampleQuality.DISCONNECTED,
                                       f"No connection within {timeout_s:g} s")
    for name in active:
        samples[name] = _unread_sample(stopped, SampleQuality.READ_ERROR,
                                       f"EPICS value or control metadata did not arrive within {timeout_s:g} s")
    return samples


def _unread_sample(stopped, quality, detail):
    return _error_sample(SampleQuality.CANCELLED if stopped else quality, "Capture cancelled" if stopped else detail)


def _default_epics_helpers(
    pv_factory: Callable[[str], Any] | None,
    poll_function: Callable[[float], None] | None,
) -> tuple[Callable[[str], Any], Callable[[float], None]]:
    if pv_factory is not None and poll_function is not None:
        return pv_factory, poll_function

    import epics

    factory = pv_factory or (
        lambda pv_name: epics.PV(
            pv_name,
            auto_monitor=False,
            form="time",
        )
    )
    poll = poll_function or (
        lambda interval: epics.ca.poll(evt=interval, iot=max(interval, 0.001))
    )
    return factory, poll


def _read_one(
    pv: Any,
    timeout_s: float,
    utc_now: Callable[[], datetime],
) -> _PvSample:
    count = _optional_int(getattr(pv, "count", None))
    if (error := _invalid_count(count, pv, utc_now)) is not None:
        return error
    try:
        metadata = pv.get_with_metadata(
            with_ctrlvars=True,
            use_monitor=False,
            timeout=max(0.001, timeout_s),
        )
    except Exception as exc:
        return _error_sample(
            SampleQuality.READ_ERROR,
            f"{type(exc).__name__}: {exc}",
            count=count,
        )
    return _sample_from_metadata(metadata, count, utc_now, pv=pv)


def _invalid_count(count, pv, utc_now):
    if count is None or count < 1:
        return _error_sample(
            SampleQuality.READ_ERROR,
            "EPICS native element count is unavailable; value was not read",
            count=count,
        )
    if count is not None and count > 1:
        return _PvSample(
            value=None,
            unit=_optional_text(getattr(pv, "units", None)),
            source_timestamp=None,
            received_at=utc_now().isoformat(),
            alarm_status=None,
            alarm_severity=None,
            native_count=count,
            quality=SampleQuality.UNSUPPORTED_ARRAY,
            detail=f"Native element count is {count}; arrays are excluded from snapshot v1",
        )
    return None


def _sample_from_metadata(metadata, count, utc_now, *, pv=None):
    if metadata is None:
        return _error_sample(
            SampleQuality.READ_ERROR,
            "EPICS read returned no metadata",
            count=count,
        )
    if not isinstance(metadata, Mapping):
        try:
            metadata = vars(metadata)
        except TypeError:
            return _error_sample(
                SampleQuality.READ_ERROR,
                "EPICS metadata has an unsupported shape",
                count=count,
            )

    value, invalid_detail = _normalize_scalar(metadata.get("value"))
    timestamp = _optional_float(metadata.get("timestamp"))
    severity = _optional_int(metadata.get("severity"))
    status = _alarm_status(metadata.get("status"))
    unit = _optional_text(metadata.get("units") or getattr(pv, "units", None))
    native_count = _optional_int(metadata.get("count")) or count or 1
    quality = SampleQuality.OK
    detail = ""
    if invalid_detail:
        quality = SampleQuality.INVALID_NUMBER
        detail = invalid_detail
    elif severity is not None and severity > 0:
        quality = SampleQuality.ALARM
        detail = f"EPICS alarm severity {severity}"
    elif timestamp is None:
        quality = SampleQuality.TIMESTAMP_MISSING
        detail = "EPICS source timestamp is unavailable"
    return _PvSample(
        value=value,
        unit=unit,
        source_timestamp=timestamp,
        received_at=utc_now().isoformat(),
        alarm_status=status,
        alarm_severity=severity,
        native_count=native_count,
        quality=quality,
        detail=detail,
    )


def _normalize_scalar(value: Any) -> tuple[Any, str]:
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            value = value.item()
        except (ValueError, TypeError):
            pass
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, float) and not math.isfinite(value):
        return None, "PV value is NaN or Infinity"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value, ""
    return None, f"PV returned non-scalar value type {type(value).__name__}"


def _capture_status(entries: tuple[SnapshotEntry, ...], cancelled: bool) -> str:
    if cancelled:
        return "cancelled"
    usable = sum(
        entry.value is not None
        and entry.quality
        not in {
            SampleQuality.DISCONNECTED,
            SampleQuality.READ_ERROR,
            SampleQuality.INVALID_NUMBER,
            SampleQuality.UNSUPPORTED_ARRAY,
            SampleQuality.CANCELLED,
        }
        for entry in entries
    )
    if usable == 0:
        return "failed"
    failed = any(
        entry.quality
        in {
            SampleQuality.DISCONNECTED,
            SampleQuality.READ_ERROR,
            SampleQuality.INVALID_NUMBER,
            SampleQuality.CANCELLED,
        }
        for entry in entries
    )
    return "partial" if failed else "complete"


def _error_sample(
    quality: SampleQuality,
    detail: str,
    *,
    count: int | None = None,
) -> _PvSample:
    return _PvSample(
        value=None,
        unit=None,
        source_timestamp=None,
        received_at=None,
        alarm_status=None,
        alarm_severity=None,
        native_count=count,
        quality=quality,
        detail=detail,
    )


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    text = str(value).strip()
    return text or None


def _alarm_status(value: Any) -> int | str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return str(value)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None
