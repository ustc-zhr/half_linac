from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from types import SimpleNamespace

import pytest

from half_linac.src.shared.machine_state import CapturePoint, SampleQuality, StateClass
from half_linac.src.shared.pv_connection import PvEndpoint
from half_linac.src.shared.pv_sampling import sample_capture_points


class FakePv:
    def __init__(
        self,
        name,
        *,
        connected=True,
        count=1,
        value=1.25,
        timestamp=100.0,
        severity=0,
        units="A",
        raises=None,
    ):
        self.name = name
        self.connected = connected
        self.count = count
        self.value = value
        self.timestamp = timestamp
        self.severity = severity
        self.units = units
        self.raises = raises
        self.read_count = 0
        self.disconnected = False

    def get_with_metadata(self, **_kwargs):
        self.read_count += 1
        if self.raises:
            raise self.raises
        return {
            "value": self.value,
            "timestamp": self.timestamp,
            "status": 0,
            "severity": self.severity,
            "units": self.units,
            "count": self.count,
        }

    def clear_callbacks(self):
        pass

    def disconnect(self):
        self.disconnected = True


def _point(element_id: str, pv_name: str) -> CapturePoint:
    return CapturePoint(
        endpoint=PvEndpoint("vm", element_id, "quad", "current_set", pv_name),
        element_order=1,
        display_name=element_id,
        state_class=StateClass.SETTING,
        capture_group="settings",
        unit="A",
    )


def test_sampling_deduplicates_pvs_and_maps_each_logical_point() -> None:
    created = []
    fake = FakePv("SAME")

    def factory(name):
        created.append(name)
        return fake

    result = sample_capture_points(
        (_point("Q1", "SAME"), _point("Q2", "SAME")),
        1.0,
        pv_factory=factory,
        poll_function=lambda _interval: None,
        utc_now=lambda: datetime(2026, 8, 27, tzinfo=timezone.utc),
    )

    assert created == ["SAME"]
    assert fake.read_count == 1
    assert [entry.value for entry in result.entries] == [1.25, 1.25]
    assert result.status == "complete"
    assert fake.disconnected


def test_sampling_preserves_partial_results_for_disconnect_error_nan_and_array() -> None:
    pvs = {
        "OK": FakePv("OK"),
        "MISSING": FakePv("MISSING", connected=False),
        "ERROR": FakePv("ERROR", raises=RuntimeError("bad read")),
        "NAN": FakePv("NAN", value=float("nan")),
        "ARRAY": FakePv("ARRAY", count=16, value=list(range(16))),
    }
    ticks = iter((0.0, 0.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0))
    result = sample_capture_points(
        tuple(_point(name, name) for name in pvs),
        1.0,
        pv_factory=pvs.__getitem__,
        poll_function=lambda _interval: None,
        monotonic=lambda: next(ticks, 2.0),
    )
    qualities = {entry.pv_name: entry.quality for entry in result.entries}

    assert qualities["OK"] == SampleQuality.READ_ERROR
    assert qualities["MISSING"] == SampleQuality.DISCONNECTED
    assert result.status == "failed"

    # Read failures are exercised with a connected batch and a non-expiring clock.
    result = sample_capture_points(
        tuple(_point(name, name) for name in ("OK", "ERROR", "NAN", "ARRAY")),
        1.0,
        pv_factory=pvs.__getitem__,
        poll_function=lambda _interval: None,
        monotonic=lambda: 0.0,
    )
    qualities = {entry.pv_name: entry.quality for entry in result.entries}
    assert qualities == {
        "OK": SampleQuality.OK,
        "ERROR": SampleQuality.READ_ERROR,
        "NAN": SampleQuality.INVALID_NUMBER,
        "ARRAY": SampleQuality.UNSUPPORTED_ARRAY,
    }
    assert result.status == "partial"
    assert pvs["ARRAY"].read_count == 0


def test_sampling_alarm_missing_timestamp_and_cancellation() -> None:
    pvs = {
        "ALARM": FakePv("ALARM", severity=2),
        "NO_TS": FakePv("NO_TS", timestamp=None),
    }
    result = sample_capture_points(
        (_point("A", "ALARM"), _point("B", "NO_TS")),
        1.0,
        pv_factory=pvs.__getitem__,
        poll_function=lambda _interval: None,
        monotonic=lambda: 0.0,
    )
    assert [entry.quality for entry in result.entries] == [
        SampleQuality.ALARM,
        SampleQuality.TIMESTAMP_MISSING,
    ]
    assert result.status == "complete"

    cancelled = sample_capture_points(
        (_point("A", "ALARM"),),
        1.0,
        pv_factory=pvs.__getitem__,
        poll_function=lambda _interval: None,
        stop_requested=lambda: True,
    )
    assert cancelled.status == "cancelled"
    assert cancelled.entries[0].quality == SampleQuality.CANCELLED


def test_sampling_does_not_read_when_native_count_is_unknown() -> None:
    pv = FakePv("UNKNOWN", count=None)
    result = sample_capture_points(
        (_point("Q1", "UNKNOWN"),),
        1.0,
        pv_factory=lambda _name: pv,
        poll_function=lambda _interval: None,
        monotonic=lambda: 0.0,
    )
    assert result.entries[0].quality == SampleQuality.READ_ERROR
    assert "element count" in result.entries[0].detail
    assert pv.read_count == 0


@pytest.fixture
def ca_batch(monkeypatch):
    from epics import ca

    names = ("FAST", "SLOW", "MISSING", "ARRAY", "ERROR", "PRIOR")
    pvs = {name: FakePv(name, connected=name != "MISSING", count=2 if name == "ARRAY" else 1)
           for name in names}
    caches = {name: SimpleNamespace(lock=RLock(), get_results={}) for name in names}
    for name, pv in pvs.items():
        pv.chid = name
    requests, completions = [], []
    clock = SimpleNamespace(now=0.0, stop=False)
    caches["PRIOR"].get_results["time"] = [ca.GET_PENDING]

    def get(name, *, ftype, count, wait, timeout):
        assert count == 1 and not wait and timeout == 0
        requests.append((name, ftype))
        caches[name].get_results[ftype] = [ca.GET_PENDING]

    def flush():
        for name, kind in requests:
            if name == "SLOW" and kind == "ctrl":
                continue
            metadata = {"value": 12, "timestamp": 123.0, "severity": 0, "status": 4} if kind == "time" else {
                "value": -1, "units": "A", "severity": 2, "status": 9,
            }
            caches[name].get_results[kind][0] = metadata

    def complete(name, *, ftype, count, timeout):
        assert count == 1 and timeout == 0
        assert {(pv, form) for pv in ("FAST", "SLOW", "ERROR") for form in ("time", "ctrl")} <= set(requests)
        completions.append((name, ftype))
        if name == "ERROR":
            raise RuntimeError("IOC read error")
        return caches[name].get_results[ftype][0]

    monkeypatch.setattr("epics.PV", lambda name, **_: pvs[name])
    monkeypatch.setattr(ca, "isConnected", lambda name: pvs[name].connected)
    monkeypatch.setattr(ca, "element_count", lambda name: pvs[name].count)
    monkeypatch.setattr(ca, "get_cache", caches.__getitem__)
    monkeypatch.setattr(ca, "promote_type", lambda name, *, use_time=False, use_ctrl=False: "time" if use_time else "ctrl")
    monkeypatch.setattr(ca, "get_with_metadata", get)
    monkeypatch.setattr(ca, "get_complete_with_metadata", complete)
    monkeypatch.setattr(ca, "flush_io", flush)
    return SimpleNamespace(pvs=pvs, requests=requests, completions=completions, clock=clock)


def test_native_batch_sends_all_requests_before_draining_and_preserves_ready_samples(ca_batch):
    clock = ca_batch.clock

    def poll(interval):
        clock.now += interval

    names = ("SLOW", "MISSING", "FAST", "ARRAY", "ERROR", "PRIOR")
    points = [*(_point(name, name) for name in names), _point("FAST_ALIAS", "FAST")]
    result = sample_capture_points(points, 0.03, poll_function=poll, monotonic=lambda: clock.now, batch_reads=True)

    assert [entry.pv_name for entry in result.entries] == [*names, "FAST"]
    samples = {entry.pv_name: entry for entry in result.entries}
    assert {name: sample.quality for name, sample in samples.items()} == {
        "SLOW": SampleQuality.READ_ERROR, "MISSING": SampleQuality.DISCONNECTED, "FAST": SampleQuality.OK,
        "ARRAY": SampleQuality.UNSUPPORTED_ARRAY, "ERROR": SampleQuality.READ_ERROR, "PRIOR": SampleQuality.READ_ERROR,
    }
    assert samples["FAST"].value == 12 and samples["FAST"].unit == "A"
    assert (samples["FAST"].source_timestamp, samples["FAST"].alarm_status, samples["FAST"].alarm_severity) == (123.0, 4, 0)
    assert result.status == "partial" and clock.now == pytest.approx(0.03)
    assert len(ca_batch.requests) == 6
    assert all(pv.read_count == 0 and pv.disconnected for pv in ca_batch.pvs.values())


def test_native_batch_cancels_outstanding_reads_and_keeps_completed_samples(ca_batch):
    clock = ca_batch.clock

    def stop_after_poll(_interval):
        clock.stop = True

    result = sample_capture_points(
        (_point("FAST", "FAST"), _point("SLOW", "SLOW"), _point("ERROR", "ERROR")),
        1.0, poll_function=stop_after_poll, monotonic=lambda: clock.now,
        stop_requested=lambda: clock.stop, batch_reads=True,
    )
    assert result.status == "cancelled"
    assert [entry.quality for entry in result.entries] == [SampleQuality.OK, SampleQuality.CANCELLED, SampleQuality.READ_ERROR]
