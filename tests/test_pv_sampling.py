from __future__ import annotations

from datetime import datetime, timezone

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
