from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from statistics import median
from threading import Event, Lock

from half_linac.src.shared.machine_profile import (
    EnergyControlLock,
    MachineProfileError,
    list_elements,
    require_workflow_write_allowed,
    resolve_channel,
    resolve_write_target,
)
from half_linac.src.shared.runtime_state import write_runtime_state


OUTPUT_CHANNEL = "kly_forward_power"
REQUIRED_CHANNELS = ("amplitude_set", "amplitude_readback", OUTPUT_CHANNEL)
READBACK_TIMEOUT_S = 2.0
POLL_INTERVAL_S = 0.05


def finite(value) -> float:
    if isinstance(value, bool):
        raise ValueError("Expected a finite number.")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Expected a finite number.") from exc
    if not math.isfinite(number):
        raise ValueError("Expected a finite number.")
    return number


@dataclass(frozen=True)
class AmpSettings:
    unit: str
    half_range: float
    points: int
    settle_time_s: float
    samples_per_point: int
    sample_interval_s: float
    sample_timeout_s: float
    readback_tolerance: float
    relative_output_tolerance: float
    power_unit: str | None = None
    execution_mode: str = "serial"

    @property
    def step(self) -> float:
        return self.half_range / (self.points // 2)

    def validate(self) -> None:
        if self.execution_mode not in ("serial", "parallel"):
            raise ValueError("Scan mode must be serial or parallel.")
        if type(self.points) is not int or self.points < 3 or self.points % 2 == 0:
            raise ValueError("Points must be an odd integer of at least 3.")
        if type(self.samples_per_point) is not int or self.samples_per_point < 1:
            raise ValueError("Samples per point must be a positive integer.")
        if not 0 < self.readback_tolerance < finite(self.step):
            raise ValueError("AMP spacing must exceed the configured readback tolerance.")
        if (
            finite(self.settle_time_s) < 0 or finite(self.sample_interval_s) < 0
            or finite(self.sample_timeout_s) <= POLL_INTERVAL_S
            or not 0 < self.relative_output_tolerance < 1
        ):
            raise ValueError("Invalid power scan timing or output tolerance.")

    @classmethod
    def from_config(cls, config: Mapping) -> AmpSettings:
        try:
            unit = config["unit"]
            if not isinstance(unit, str) or not unit.strip():
                raise ValueError("unit must be nonempty")
            power_unit = config.get("power_unit")
            if power_unit is not None and (not isinstance(power_unit, str) or not power_unit.strip()):
                raise ValueError("power_unit must be a nonempty string")
            points = config.get("points", 5)
            half_range = config["half_range"] if "half_range" in config else finite(config["step"]) * (points // 2)
            settings = cls(
                unit=unit.strip(),
                half_range=finite(half_range),
                points=points,
                settle_time_s=finite(config["settle_time_s"]),
                samples_per_point=config.get("samples_per_point", 5),
                sample_interval_s=finite(config.get("sample_interval_s", POLL_INTERVAL_S)),
                sample_timeout_s=finite(config["sample_timeout_s"]),
                readback_tolerance=finite(config["readback_tolerance"]),
                relative_output_tolerance=finite(config["relative_output_tolerance"]),
                power_unit=power_unit.strip() if power_unit is not None else None,
                execution_mode=config.get("execution_mode", "serial"),
            )
            settings.validate()
            return settings
        except (KeyError, TypeError, ValueError) as exc:
            raise MachineProfileError(f"Invalid Power Scan settings: {exc}") from exc


@dataclass(frozen=True)
class AmpStation:
    element_id: str
    display_name: str
    set_pv: str
    readback_pv: str
    output_pv: str
    window: tuple[float, float] | None = None

    def contains(self, amp: float) -> bool:
        return self.window is not None and self.window[0] <= amp <= self.window[1]


def discover_amp_stations(context, config: Mapping, settings: AmpSettings) -> tuple[AmpStation, ...]:
    backend = context.control_backend.name
    approved = config.get("approved_stations", {})
    if not isinstance(approved, Mapping):
        raise MachineProfileError("Power Scan approved_stations must be a mapping.")
    stations = []
    for element in list_elements(context, kind="rf"):
        if not all(backend in element.channels.get(channel, {}) for channel in REQUIRED_CHANNELS):
            continue
        target = resolve_write_target(
            context, element.id, logical_channel="amplitude_set", unit=settings.unit
        )
        readback_pv = resolve_channel(context, element.id, "amplitude_readback")
        output_pv = resolve_channel(context, element.id, OUTPUT_CHANNEL)
        window = None
        if element.id in approved:
            spec = approved[element.id]
            try:
                if not isinstance(spec, Mapping):
                    raise ValueError("station settings must be a mapping")
                machine_low = finite(target.machine_limit.low)
                machine_high = finite(target.machine_limit.high)
                low = max(finite(spec.get("low", machine_low)), machine_low)
                high = min(finite(spec.get("high", machine_high)), machine_high)
                if low >= high:
                    raise ValueError("approved window does not overlap the machine limits")
                window = (low, high)
            except (KeyError, TypeError, ValueError, MachineProfileError) as exc:
                raise MachineProfileError(f"Invalid AMP approval for {element.id}: {exc}") from exc
        stations.append(AmpStation(
            element.id, element.display_name, target.pv_name, readback_pv, output_pv, window,
        ))
    if backend == "real":
        unknown = set(approved) - {station.element_id for station in stations}
        if unknown:
            raise MachineProfileError(f"AMP approval has incomplete RF mappings: {', '.join(sorted(unknown))}")
    return tuple(stations)


@dataclass(frozen=True)
class AmpReading:
    value: float
    timestamp: float | None


class EpicsAmpClient:
    def __init__(self) -> None:
        import epics

        epics.ca.use_initial_context()
        self.epics = epics
        self.pvs = {}

    def _pv(self, name):
        if name not in self.pvs:
            self.pvs[name] = self.epics.PV(name, form="time", auto_monitor=False)
        return self.pvs[name]

    def read(self, name, timeout=0.5) -> AmpReading:
        pv = self._pv(name)
        data = pv.get_with_metadata(use_monitor=False, timeout=timeout)
        if not data or data.get("severity") != 0:
            raise RuntimeError(f"{name}: read failed or PV alarm.")
        if pv.nelm != 1:
            raise RuntimeError(f"{name}: expected a scalar PV.")
        try:
            value = finite(data["value"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"{name}: invalid scalar value.") from exc
        try:
            timestamp = finite(data.get("timestamp"))
        except (TypeError, ValueError):
            timestamp = None
        return AmpReading(value, timestamp)

    def write(self, name, value) -> None:
        if self._pv(name).put(value, wait=True, timeout=READBACK_TIMEOUT_S) != 1:
            raise RuntimeError(f"{name}: AMP write was not confirmed.")

    def close(self) -> None:
        for pv in self.pvs.values():
            pv.disconnect()
        self.pvs.clear()


class ScanStopped(RuntimeError):
    pass


class StationUnavailable(RuntimeError):
    """A point or window cannot be evaluated under otherwise valid controls."""


class AmpScan:
    def __init__(
        self, *, context, stations, settings: AmpSettings, path: Path,
        on_update: Callable = lambda _record: None, client_factory=EpicsAmpClient,
        lock_factory=EnergyControlLock.for_machine,
        monotonic=time.monotonic, wait=None,
    ) -> None:
        settings.validate()
        self.context = context
        self.stations = {station.element_id: station for station in stations}
        self.settings = settings
        self.path = path
        self.on_update = on_update
        self.client_factory = client_factory
        self.lock_factory = lock_factory
        self.monotonic = monotonic
        self.stopping = Event()
        self.wait = self.stopping.wait if wait is None else wait
        self.recovery_wait = time.sleep if wait is None else wait
        self._updates_lock = Lock()
        self.started_at = datetime.now().astimezone().isoformat()
        self.records = {
            key: {"station": key, "status": "pending", "points": [],
                  "window": station.window, "output_pv": station.output_pv}
            for key, station in self.stations.items()
        }
        self.status = "pending"
        self.error = ""

    def stop(self) -> None:
        self.stopping.set()

    def _check_stop(self) -> None:
        if self.stopping.is_set():
            raise ScanStopped("Stopped by operator.")

    def _result(self) -> dict:
        return {
            "machine": self.context.machine.id,
            "backend": self.context.control_backend.name,
            "started_at": self.started_at,
            "updated_at": datetime.now().astimezone().isoformat(),
            "settings": {**asdict(self.settings), "step": self.settings.step},
            "status": self.status,
            "error": self.error,
            "stations": list(self.records.values()),
            "path": str(self.path),
        }

    def _save(self) -> None:
        write_runtime_state(self.path, self._result())

    def _update(self, station, *, point=None, **values) -> None:
        with self._updates_lock:
            record = self.records[station.element_id]
            record.update(values)
            if point is not None:
                record["points"].append(point)
            self._save()
            self.on_update(deepcopy(record))

    @staticmethod
    def _same(left, right) -> bool:
        return math.isclose(left, right, rel_tol=1e-7, abs_tol=1e-6)

    def _owned(self, client, station, *expected) -> float:
        actual = client.read(station.set_pv).value
        if not any(self._same(actual, value) for value in expected):
            raise RuntimeError(f"{station.element_id}: AMP changed externally (setpoint {actual:g}).")
        return actual

    def _verify(self, client, station, value, *, recovering=False) -> float:
        deadline = self.monotonic() + READBACK_TIMEOUT_S
        while True:
            if not recovering:
                self._check_stop()
            self._owned(client, station, value)
            actual = client.read(station.readback_pv).value
            if abs(actual - value) <= self.settings.readback_tolerance:
                return actual
            if self.monotonic() >= deadline:
                raise RuntimeError(f"{station.element_id}: AMP readback did not reach {value:g}.")
            if recovering:
                self.recovery_wait(POLL_INTERVAL_S)
            else:
                self.wait(POLL_INTERVAL_S)

    def _check_write(self, client, station, value, expected) -> None:
        self._owned(client, station, expected)
        if not station.contains(value):
            raise RuntimeError(f"{station.element_id}: AMP {value:g} is outside its approved window.")

    def _restore(self, client, station, initial, expected) -> None:
        actual = self._owned(client, station, *expected)
        if not self._same(actual, initial):
            self._check_write(client, station, initial, actual)
            client.write(station.set_pv, initial)
        self._verify(client, station, initial, recovering=True)

    def _point(self, client, station, amp, purpose) -> float:
        readback = self._verify(client, station, amp)
        self.wait(self.settings.settle_time_s)
        self._check_stop()
        watermark = client.read(station.output_pv).timestamp
        samples = self.settings.samples_per_point
        interval = self.settings.sample_interval_s
        deadline = self.monotonic() + self.settings.sample_timeout_s + (samples - 1) * interval
        point = {"amp": amp, "readback": readback, "purpose": purpose, "samples": []}
        try:
            while watermark is not None and len(point["samples"]) < samples:
                self._check_stop()
                remaining = deadline - self.monotonic()
                if remaining <= 0:
                    break
                reading = client.read(station.output_pv, timeout=min(remaining, 0.5))
                if reading.timestamp is not None and reading.timestamp > watermark:
                    point["samples"].append(asdict(reading))
                    watermark = reading.timestamp
                    if len(point["samples"]) < samples:
                        self.wait(min(interval, remaining))
                else:
                    self.wait(min(POLL_INTERVAL_S, remaining))
            self._verify(client, station, amp)
            if len(point["samples"]) < samples:
                raise StationUnavailable("Insufficient new KLY power readings.")
            point["output"] = median(sample["value"] for sample in point["samples"])
            return point["output"]
        finally:
            self._update(station, point=point)

    def _scan_station(self, client, station) -> None:
        if station.window is None:
            raise StationUnavailable("Station is not enabled for Power Scan.")
        initial = client.read(station.set_pv).value
        half_points = self.settings.points // 2
        grid = [
            initial + direction * self.settings.half_range * (index / half_points)
            for index in range(1, half_points + 1)
            for direction in (-1, 1)
        ]
        self._update(station, initial=initial, status="scanning")
        if not all(station.contains(value) for value in (initial, *grid)):
            raise StationUnavailable("Scan range exceeds the approved AMP window.")
        expected = (initial,)
        touched = False

        def measure(value, purpose):
            nonlocal expected, touched
            self._check_stop()
            actual = self._owned(client, station, *expected)
            if not self._same(actual, value):
                self._check_write(client, station, value, actual)
                self._check_stop()
                expected = (actual, value)
                touched = True
                client.write(station.set_pv, value)
                expected = (value,)
            output = self._point(client, station, value, purpose)
            expected = (value,)
            return output

        try:
            first = measure(initial, "baseline")
            self._update(station, initial_output=first)
            if first <= 0:
                raise StationUnavailable("KLY forward power is zero or invalid.")
            readings = {value: measure(value, "scan") for value in grid}
            last = measure(initial, "baseline_repeat")
            scan_peak_amp = min(
                readings, key=lambda amp: (-readings[amp], abs(amp - initial), amp),
            )
            peak = readings[scan_peak_amp]
            measured_peak = (
                {"amp": initial, "output": first} if first >= peak
                else {"amp": scan_peak_amp, "output": peak}
            )
            self._update(station, measured_peak=measured_peak)
            tolerance = first * self.settings.relative_output_tolerance
            if abs(last - first) > tolerance:
                raise StationUnavailable("KLY power baseline drift exceeded the output tolerance.")
            baseline = (first + last) / 2
            if peak - baseline <= tolerance:
                self._update(station, status="keep", initial_output=baseline)
                return
            if any(peak - readings[edge] <= tolerance for edge in (min(grid), max(grid))):
                raise StationUnavailable("Highest output is at the scan boundary or on an open plateau.")
            best = min(
                (value for value, output in readings.items()
                 if peak - output <= tolerance and output - baseline > tolerance),
                key=lambda value: (abs(value - initial), -readings[value], value),
            )
            repeat = measure(best, "candidate_repeat")
            if abs(repeat - readings[best]) > tolerance or repeat - baseline <= tolerance:
                raise StationUnavailable("Candidate output did not reproduce.")
            self._update(station, status="candidate", initial_output=baseline,
                         suggestion=best, suggested_output=(repeat + readings[best]) / 2)
        except (ScanStopped, StationUnavailable):
            raise
        except Exception:
            self.stopping.set()
            raise
        finally:
            if touched:
                try:
                    self._restore(client, station, initial, expected)
                except Exception as exc:
                    self._update(station, status="restore_failed", error=str(exc))
                    raise RuntimeError(f"{station.element_id}: AMP restore could not be verified. {exc}") from exc
                self._update(station, restored=True)

    def _apply_station(self, client, station, apply_source) -> None:
        record = self.records[station.element_id]
        initial = record["initial"]
        target = (
            record["measured_peak"]["amp"] if apply_source == "measured_peak" else record["suggestion"]
        )
        self._update(
            station, status="applying", apply_source=apply_source, apply_target=target,
            scan_status=record["status"], scan_error=record.get("error", ""), error="",
        )
        try:
            self._verify(client, station, initial)
            self._check_stop()
            self._check_write(client, station, target, initial)
        except Exception as exc:
            self._update(station, status="apply_failed", error=str(exc))
            raise
        try:
            client.write(station.set_pv, target)
            self._verify(client, station, target)
        except Exception as exc:
            try:
                self._restore(client, station, initial, (initial, target))
            except Exception as restore_error:
                self._update(station, status="restore_failed", error=str(restore_error))
                raise RuntimeError(f"{station.element_id}: AMP apply restore failed. {restore_error}") from exc
            self._update(station, status="apply_failed", error=str(exc), apply_restored=True)
            raise
        self._update(station, status="applied", applied=target)

    def _run_station(self, client, station, *, apply_source=None):
        self._check_stop()
        try:
            if apply_source is not None:
                self._apply_station(client, station, apply_source)
            else:
                self._scan_station(client, station)
        except StationUnavailable as exc:
            self._update(station, status="undetermined", error=str(exc))
        except ScanStopped:
            if apply_source is None:
                self._update(station, status="stopped")
            raise
        except Exception as exc:
            if self.records[station.element_id]["status"] not in {"apply_failed", "restore_failed"}:
                self._update(station, status="failed", error=str(exc))
            raise

    def _parallel_station(self, station):
        self._check_stop()
        try:
            with closing(self.client_factory()) as client:
                self._run_station(client, station)
        except ScanStopped:
            raise
        except Exception as exc:
            self.stopping.set()
            if self.records[station.element_id]["status"] not in {"failed", "restore_failed"}:
                self._update(station, status="failed", error=str(exc))
            raise RuntimeError(f"{station.element_id}: {exc}") from exc

    def _scan_parallel(self, ids):
        if not ids:
            return
        stations = [self.stations[element_id] for element_id in ids]
        if len({station.set_pv for station in stations}) != len(stations):
            raise RuntimeError("Parallel Power Scan requires a distinct setpoint PV for each selected station.")
        error = None
        with ThreadPoolExecutor(max_workers=len(stations)) as pool:
            futures = [pool.submit(self._parallel_station, station) for station in stations]
            for future in as_completed(futures):
                try:
                    future.result()
                except ScanStopped:
                    continue
                except Exception as exc:
                    if error is None:
                        error = exc
        if error is not None:
            raise error

    def _execute(self, ids, *, apply_source=None) -> dict:
        apply = apply_source is not None
        operation = {
            None: "Power Scan", "suggestion": "Apply AMP suggestions",
            "measured_peak": "Apply measured AMP peak",
        }[apply_source]
        self.status, self.error = ("applying" if apply else "scanning"), ""
        try:
            require_workflow_write_allowed(self.context, "rf_phase_scan", operation)
            with self.lock_factory(self.context.machine.id, {"operation": operation}):
                self._save()
                self._check_stop()
                # Initialize Channel Access in the coordinator before creating station workers.
                with closing(self.client_factory()) as client:
                    if not apply and self.settings.execution_mode == "parallel":
                        self._scan_parallel(tuple(ids))
                    else:
                        for element_id in ids:
                            self._run_station(client, self.stations[element_id], apply_source=apply_source)
                self._check_stop()
            self.status = "completed"
        except ScanStopped as exc:
            self.status, self.error = "stopped", str(exc)
        except Exception as exc:
            self.status, self.error = "failed", str(exc)
        finally:
            self._save()
        return deepcopy(self._result())

    def run(self) -> dict:
        if self.status != "pending":
            raise RuntimeError("Start a new Power Scan to acquire a new result.")
        return self._execute(self.stations)

    def apply(self, selected) -> dict:
        if self.status != "completed":
            raise RuntimeError("A completed Power Scan is required.")
        selected = set(selected)
        eligible = {
            key for key, record in self.records.items()
            if record["status"] == "candidate" and record.get("restored")
        }
        if not selected or not selected <= eligible:
            raise RuntimeError("Select verified AMP suggestions.")
        return self._execute((key for key in self.stations if key in selected), apply_source="suggestion")

    def can_apply_measured_peak(self, element_id) -> bool:
        record = self.records.get(element_id, {})
        peak = record.get("measured_peak")
        return (
            self.status == "completed"
            and record.get("status") in {"candidate", "keep", "undetermined"}
            and record.get("restored") is True
            and peak is not None
            and self.stations[element_id].contains(peak["amp"])
            and not self._same(peak["amp"], record["initial"])
        )

    def apply_measured_peak(self, element_id) -> dict:
        if not self.can_apply_measured_peak(element_id):
            raise RuntimeError("Select a measured peak from a completed Power Scan with verified AMP restore.")
        return self._execute((element_id,), apply_source="measured_peak")
