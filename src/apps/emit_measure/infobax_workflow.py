"""Standalone, approval-driven emittance workflow. No Qt or EPICS dependency."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import Path
from threading import Event
from contextlib import contextmanager
import fcntl
import json
import math
import os
import time
from uuid import uuid4

import numpy as np
from scipy.linalg import qr
from scipy.spatial.distance import pdist
from scipy.stats import qmc

from half_linac.src.optimization.infobax import (
    FinitePoolSpec, ICT1Observation, InfoBAXCore, InfoBAXConfig, PointObservation,
)
from half_linac.src.shared.beam_matrix import (
    DEFAULT_MAX_CONDITION_NUMBER,
)
from .control import emittance_lock
from .infobax_ict1 import numerical_ict1_config, read_ict1_window, scan_charge_summary
from .measurement_service import fingerprint, PointAcquisitionResult, fit_grid_results


DEFAULT_CURRENT_CANDIDATES = 5
DEFAULT_INITIAL_POINTS_PER_END = 3
WORKFLOW_SCHEMA = "half_infobax_workflow_v2"
ICT1_WORKFLOW_SCHEMA = "half_infobax_workflow_v3"
FRAMEWORK_WORKFLOW_SCHEMAS = {WORKFLOW_SCHEMA, ICT1_WORKFLOW_SCHEMA}


def minimum_initial_points_per_end(model):
    return 1 if model == "M0" else 3


def current_vector(value):
    return tuple(value) if isinstance(value, (tuple, list)) else (value,)


class WorkflowInterrupted(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkflowConfig:
    current_low: float | tuple[float, ...]
    current_high: float | tuple[float, ...]
    scan_values: tuple[float, ...]
    projections: tuple[tuple[float, float], ...]
    energy_mev: float
    plane: str = "x"
    model: str = "transport_moments"
    active_queries: int = 3
    max_minutes: float = 120.0
    settings_json: str = "{}"  # frozen profile/model/measurement snapshot
    current_candidate_count: int = DEFAULT_CURRENT_CANDIDATES
    initial_points_per_end: int = DEFAULT_INITIAL_POINTS_PER_END

    def __post_init__(self):
        low, high = current_vector(self.current_low), current_vector(self.current_high)
        if (not low or len(low) != len(high)
                or not all(math.isfinite(v) for v in (*low, *high, self.energy_mev, self.max_minutes))
                or any(a >= b for a, b in zip(low, high)) or self.energy_mev <= 0 or self.max_minutes <= 0):
            raise ValueError("Invalid current range, energy or time budget")
        object.__setattr__(self, "current_low", low[0] if len(low) == 1 else low)
        object.__setattr__(self, "current_high", high[0] if len(high) == 1 else high)
        if self.plane not in {"x", "y"} or self.model not in {"M0", "transport_moments"}:
            raise ValueError("Invalid plane or posterior model")
        if type(self.active_queries) is not int or self.active_queries < 0:
            raise ValueError("Active query limit must be a non-negative integer")
        scans = tuple(float(v) for v in self.scan_values)
        projections = tuple(tuple(float(v) for v in row) for row in self.projections)
        if len(scans) < 3 or len(set(scans)) != len(scans) or not all(math.isfinite(v) for v in scans):
            raise ValueError("At least three distinct finite K1 values are required")
        if type(self.current_candidate_count) is not int or self.current_candidate_count < 2:
            raise ValueError("At least two current candidates are required")
        minimum = minimum_initial_points_per_end(self.model)
        if type(self.initial_points_per_end) is not int or not minimum <= self.initial_points_per_end <= len(scans):
            raise ValueError(f"Initial K1 points per end must be between {minimum} and the number of K1 candidates for {self.model}")
        p = np.asarray(projections)
        if p.shape != (len(scans), 2) or not np.all(np.isfinite(p)):
            raise ValueError("Transport projections must match scan values")
        if not isinstance(json.loads(self.settings_json), dict):
            raise ValueError("settings_json must contain a JSON object")
        object.__setattr__(self, "scan_values", scans)
        object.__setattr__(self, "projections", projections)
        self.initial_scan_indices()  # Fail before acquiring any device.

    @property
    def design(self):
        p = np.asarray(self.projections)
        return np.column_stack((p[:, 0]**2, 2*p[:, 0]*p[:, 1], p[:, 1]**2))

    @property
    def candidate_ids(self):
        return tuple(f"current-{i}" for i in range(self.current_candidate_count))

    def initial_scan_indices(self):
        design = self.design
        scales = np.linalg.norm(design, axis=0)
        if np.any(scales == 0):
            raise ValueError("Transport design is rank deficient")
        scaled = design / scales
        _, _, pivots = qr(scaled.T, pivoting=True, mode="economic")
        independent = [int(i) for i in pivots[:3]]
        if (np.linalg.matrix_rank(scaled) != 3
                or np.linalg.cond(scaled) > DEFAULT_MAX_CONDITION_NUMBER
                or np.linalg.cond(scaled[sorted(independent)]) > DEFAULT_MAX_CONDITION_NUMBER):
            raise ValueError("Transport design is singular or ill-conditioned")

        # Use QR pivot order for sparse M0 initialization and fill gaps beyond three points.
        selected = sorted(independent[:self.initial_points_per_end])
        scans = np.asarray(self.scan_values)
        distances = np.min(np.abs(scans[:, None] - scans[selected]), axis=1)
        while len(selected) < self.initial_points_per_end:
            index = int(np.argmax(distances))
            selected.append(index)
            distances = np.minimum(distances, np.abs(scans - scans[index]))
        return tuple(sorted(selected))

    def to_dict(self):
        data = asdict(self)
        # Keep default configurations compatible with existing archive and pool hashes.
        if self.current_candidate_count == DEFAULT_CURRENT_CANDIDATES:
            data.pop("current_candidate_count")
        if self.initial_points_per_end == DEFAULT_INITIAL_POINTS_PER_END:
            data.pop("initial_points_per_end")
        return data

    @property
    def fingerprint(self):
        return fingerprint(self.to_dict())

    @property
    def ict1_settings(self):
        return json.loads(self.settings_json).get("ict1")


def current_candidates(low, high, initial, count=DEFAULT_CURRENT_CANDIDATES):
    if len(current_vector(low)) > 1:
        lower, upper, start = (np.asarray(v, dtype=float) for v in (low, high, initial))
        if start.shape != lower.shape or not np.all(np.isfinite(start) & (lower <= start) & (start <= upper)):
            raise ValueError("Every initial current must be inside its selected search range")
        design = qmc.LatinHypercube(d=len(lower), seed=0).random(count)
        nearest = np.argmin(np.linalg.norm(design - (start - lower) / (upper - lower), axis=1))
        values = qmc.scale(design, lower, upper)
        values[nearest] = start
        if len(np.unique(values, axis=0)) != count:
            raise ValueError(f"{count} unique current candidates are required")
        return tuple(tuple(row) for row in values.tolist())
    if not math.isfinite(initial) or not low <= initial <= high:
        raise ValueError("Initial current must be inside the selected search range")
    values = np.linspace(low, high, count)
    values[int(np.argmin(np.abs(values - initial)))] = initial
    values.sort()
    if len(np.unique(values)) != count:
        raise ValueError(f"{count} unique current candidates are required")
    return tuple(float(v) for v in values)


def workflow_pool(config, candidates, baseline, machine_fingerprint):
    scans = np.asarray(config.scan_values)
    currents = np.asarray([current_vector(point) for point in candidates])
    if currents.shape[1] == 1:
        coordinates = (currents - currents.min()) / np.ptp(currents)
    else:
        low, high = (np.asarray(v) for v in (config.current_low, config.current_high))
        coordinates = (currents - low) / (high - low)
    noise = [record["measurement"]["noise_std_mm2"] for record in baseline]
    return FinitePoolSpec(config.candidate_ids, coordinates, scans, (scans - scans.min()) / np.ptp(scans),
                          config.design, config.energy_mev, np.tile(noise, len(currents)),
                          fingerprint({"machine": machine_fingerprint, "config": config.fingerprint}))


class InfoBAXWorkflow:
    """Persist every intent before motion; always restore under the control lease.

    With ``automatic_measurements=True``, starting the workflow authorizes its
    measurements. Otherwise ``approve(request, check)`` must approve each request.
    """
    filename = "infobax_workflow.json"

    def __init__(self, config, machine, run_dir, *, approve=None, automatic_measurements=False, progress=None,
                 numerical_config=None, clock=time.monotonic, full_scan=None, control_lease=None):
        self.config, self.machine, self.run_dir = config, machine, Path(run_dir)
        self.full_scan, self.control_lease = full_scan, control_lease
        self.scan_results = {}
        self.ict1_settings = config.ict1_settings
        self.ict1_baseline = self.ict1_verification = self.restore_readback = None
        self.eig_seconds = 0.0
        self.approve, self.progress, self.clock = approve, progress, clock
        self.automatic_measurements = automatic_measurements
        self.numerical_config = numerical_config or InfoBAXConfig()
        self.cancelled, self.pause_requested = Event(), Event()
        self.status, self.error, self.restoration_status = "ready", None, "not_required"
        self.initial = None
        self.candidates = ()
        self.baseline, self.verification, self.records, self.actions = [], [], [], []
        self.core, self.recommendation = None, None
        self.baseline_fit, self.verification_fit = None, None
        self.confirmed = False
        self.elapsed_seconds = 0.0
        self._content = None
        self._started = None
        self._last_confirmed = None

    @property
    def path(self):
        return self.run_dir / self.filename

    def request_pause(self):
        self.pause_requested.set()

    def check(self):
        if self.cancelled.is_set():
            raise WorkflowInterrupted("stopped")
        if self.pause_requested.is_set():
            raise WorkflowInterrupted("paused")
        if self.elapsed() >= self.config.max_minutes * 60:
            raise WorkflowInterrupted("time_budget_exhausted")

    def elapsed(self):
        return self.elapsed_seconds + (0 if self._started is None else max(0, self.clock()-self._started))

    def emit(self, **extra):
        if self.progress:
            self.progress({"status": self.status, "record_count": len(self.records),
                           "confirmed": self.confirmed, "elapsed_seconds": self.elapsed(), **extra})

    def payload(self):
        payload = {"schema": WORKFLOW_SCHEMA, "config": self.config.to_dict(),
                "config_fingerprint": self.config.fingerprint,
                "machine_fingerprint": self.machine.fingerprint,
                "numerical_config": self.numerical_config.to_dict(),
                "initial": self.initial, "candidates": self.candidates,
                "baseline": self.baseline, "verification": self.verification,
                "records": self.records, "actions": self.actions,
                "core": None if self.core is None else self.core.to_checkpoint(),
                "recommendation": self.recommendation,
                "baseline_fit": self.baseline_fit, "verification_fit": self.verification_fit,
                "scan_results": self.scan_results,
                "confirmed": self.confirmed, "status": self.status, "error": self.error,
                "restoration_status": self.restoration_status,
                "elapsed_seconds": self.elapsed(),
                "cost": {"attempted_points": len(self.records),
                         "raw_frame_count": sum(1 for _ in self.run_dir.rglob("*.npz")) if self.run_dir.exists() else 0,
                         "observed_points": sum(r["state"] == "observed" for r in self.records),
                         "accepted_frames": sum(r.get("measurement", {}).get("metadata", {}).get("frame_count", 0)
                                                for r in self.records)},
                "camera_statistics": dict(getattr(getattr(self.machine, "camera", None), "statistics", {}))}
        if self.ict1_settings is not None:
            payload.update(schema=ICT1_WORKFLOW_SCHEMA, ict1={
                "baseline": self.ict1_baseline, "verification": self.ict1_verification,
                "restore_readback": self.restore_readback, "eig_seconds": self.eig_seconds,
            })
        return payload

    def save(self):
        self.run_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w") as stream:
            json.dump(self.payload(), stream, allow_nan=False, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.path)
        self._content = self.path.read_bytes()

    @contextmanager
    def exclusive(self):
        self.run_dir.mkdir(parents=True, exist_ok=True)
        with (self.run_dir / ".workflow.lock").open("a+") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                current = self.path.read_bytes() if self.path.exists() else None
                if current != self._content:
                    raise ValueError("Run directory changed; load its checkpoint before continuing")
                from contextlib import nullcontext
                from half_linac.src.shared.machine_profile.control_lock import MachineControlLock
                expected = emittance_lock(self.machine.machine_id, self.machine.backend)
                inherited = self.control_lease
                if inherited is not None and (not isinstance(inherited, MachineControlLock)
                        or inherited.path != expected.path or inherited._stream is None):
                    raise RuntimeError("A matching held control lease is required")
                with (expected if inherited is None else nullcontext(inherited)) as lease:
                    self.control_lease = lease
                    try:
                        yield
                    finally:
                        self.control_lease = inherited
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _begin_measurement(self, request):
        self.check()
        if not self.automatic_measurements:
            expected = self.machine.snapshot()
            self.status = "awaiting_approval"
            self.save()
            self.emit(request=request)
            if self.approve is None or not self.approve(request, self.check):
                raise WorkflowInterrupted("stopped")
            self.check()
            self.machine.verify_snapshot(expected)
        self.status = request["phase"]
        if self.automatic_measurements:
            self.save()
            self.emit(request=request)
            self.check()

    @property
    def _ict1_directory(self):
        return self.run_dir / "ict1_windows"

    def _capture_ict1(self, candidate, phase, k1, *, sequence=None):
        window = {"window_id": uuid4().hex, "phase": phase, "sequence": sequence,
                  "candidate_index": candidate, "target_current_a": self.candidates[candidate],
                  "target_k1": k1, "samples": [], "completed": False}
        directory = self._ict1_directory
        directory.mkdir(exist_ok=True)
        path = directory / (window["window_id"] + ".json")
        try:
            self.machine.measure_ict1(window)
            window["completed"] = True
        except BaseException as error:
            window["summary"] = {"status": "invalid", "error": str(error)}
            raise
        finally:
            window["closed_at"] = time.time()
            if self.ict1_baseline is not None:
                self._assess_ict1(window["summary"])
            temporary = path.with_suffix(".tmp")
            with temporary.open("w") as stream:
                json.dump(window, stream, allow_nan=False, indent=2, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
        return window

    def _assess_ict1(self, summary):
        minimum = self.ict1_baseline["minimum_nc"]
        summary["minimum_nc"] = minimum
        summary["feasible"] = summary["status"] == "valid" and summary["value"] >= minimum

    def _commit_point_charge(self, record, window):
        summary = window["summary"]
        record.update(ict1_window_id=window["window_id"], ict1=summary)
        if summary["status"] == "valid" and window["phase"] in {"initialization", "active"}:
            self.core.observe_ict1(ICT1Observation(window["window_id"], window["candidate_index"],
                                                 summary["value"], summary["standard_error_nc"]))

    def _full_scan_charge(self, records):
        windows = [read_ict1_window(self._ict1_directory, record["ict1_window_id"])
                   for record in records]
        return scan_charge_summary(self.ict1_settings, windows)

    def _active_attempts(self):
        return sum(record["phase"] == "active" and record.get("motion_started", False)
                   for record in self.records)

    def _restore_initial(self):
        self.restore_readback = None
        readback = self.machine.restore(self.initial)
        if self.ict1_settings is not None:
            self.restore_readback = readback

    def _point(self, candidate, scan_index, phase, *, approved=False, suggestion=None):
        self.check()
        n = len(self.config.scan_values)
        query_index = candidate*n + scan_index
        record = {"sequence": len(self.records), "query_index": query_index,
                  "candidate_index": candidate, "current_a": self.candidates[candidate],
                  "scan_index": scan_index, "scan_k1": self.config.scan_values[scan_index],
                  "phase": phase, "state": "pending"}
        if suggestion is not None:
            record["suggestion"] = asdict(suggestion)
        self.records.append(record)
        self.save()
        try:
            if not approved:
                self._begin_measurement(record.copy())
            self.check()
            if self._last_confirmed is not None:
                self.machine.verify_snapshot(self._last_confirmed)
            if self.ict1_settings is not None:
                record["motion_started"] = True
                self.save()
            self.machine.apply_query(candidate, record["scan_k1"])
            self.check()
            options = {}
            if self.ict1_settings is not None:
                def before_images(k1):
                    window = self._capture_ict1(candidate, phase, k1, sequence=record["sequence"])
                    self._commit_point_charge(record, window)
                    self.save()
                    return window["window_id"]

                options["before_images"] = before_images
            measurement = self.machine.measure_point(query_index, 0.0, **options)
            self.check()
            record.update(state="observed", measurement=asdict(measurement))
            self._last_confirmed = dict(self.machine.snapshot())
        except BaseException as exc:
            record.update(state="interrupted" if isinstance(exc, WorkflowInterrupted) else "failed", error=str(exc))
            raise
        self.emit(record=record)
        return record

    def _pool(self):
        return workflow_pool(self.config, self.candidates, self.baseline, self.machine.fingerprint)

    def _create_core(self):
        n = len(self.config.scan_values)
        initial_index = self.candidates.index(self.initial["current_a"])
        indices = [initial_index*n+i for i in range(n)]
        pool = self._pool()
        if pool.candidate_dimension == 1:
            endpoints = (0, pool.candidate_count - 1)
        else:
            left, right = np.triu_indices(pool.candidate_count, k=1)
            index = int(np.argmax(pdist(pool.candidate_coordinates)))
            endpoints = (int(left[index]), int(right[index]))
        scan_indices = self.config.initial_scan_indices()
        indices.extend(candidate*n + scan for candidate in endpoints for scan in scan_indices)
        indices = tuple(dict.fromkeys(indices))
        self.machine.validate_pool(pool)
        ict1 = None if self.ict1_settings is None else numerical_ict1_config(self.ict1_settings, self.ict1_baseline)
        core = InfoBAXCore(pool, replace(self.numerical_config, initial_actions=len(indices)),
                          model=self.config.model, initial_queries=indices, ict1=ict1)
        for r in self.baseline:
            core.observe(PointObservation(r["query_index"], r["measurement"]["variance_mm2"],
                                          r["measurement"]["noise_std_mm2"]))
        if ict1 is not None:
            core.observe_ict1(ICT1Observation("baseline", initial_index, ict1.baseline_nc,
                                             self.ict1_baseline["standard_error_nc"]))
        return core

    def _fit(self, records):
        # Headless/Fake runs use the same grid solver as the application.
        return fit_grid_results(self.config, records)

    def _full_scan_phase(self, candidate, phase, records):
        if self.full_scan is None:
            for i in range(len(records), len(self.config.scan_values)):
                records.append(self._point(candidate, i, phase, approved=True))
                self.save()
            return self._fit(records)
        if self.automatic_measurements:
            self.machine.verify_snapshot(self._last_confirmed)
        self.machine.apply_current(candidate)
        completed = [r["measurement"]["metadata"]["point_result"] for r in records]
        def commit_point(result):
            index = min(range(len(self.config.scan_values)), key=lambda i: abs(self.config.scan_values[i]-result.k1))
            if abs(self.config.scan_values[index]-result.k1)>1e-10:
                raise ValueError("Scan returned a K1 outside the frozen grid")
            if any(r["scan_index"]==index for r in records):
                raise ValueError("Scan returned a duplicate completed K1")
            record={"sequence":len(self.records),"query_index":candidate*len(self.config.scan_values)+index,
                    "candidate_index":candidate,"current_a":self.candidates[candidate],
                    "scan_index":index,"scan_k1":result.k1,"phase":phase,"state":"pending",
                    "point_result":result.to_dict()}
            self.records.append(record)
            if result.ict1_window_id is not None:
                window = read_ict1_window(self._ict1_directory, result.ict1_window_id)
                self._commit_point_charge(record, window)
            camera=getattr(self.machine,"camera",None)
            if camera is not None:
                camera.statistics["accepted_frames"]=camera.statistics.get("accepted_frames",0)+sum(
                    "sigx" in f and "sigy" in f for f in result.frames)
            try:
                measurement = result.observation(self.config.plane)
                metadata = dict(measurement.metadata, point_result=result.to_dict())
                record.update(state="observed",measurement={"variance_mm2":measurement.variance_mm2,
                    "noise_std_mm2":measurement.noise_std_mm2,"metadata":metadata})
                records.append(record)
                records.sort(key=lambda r:r["scan_index"])
            except Exception as error:
                record.update(state="failed",error=str(error))
                self.save()
                raise
            self.save()
            self.emit(record=record)
        options = {}
        if self.ict1_settings is not None:
            def before_images(k1):
                return self._capture_ict1(candidate, phase, k1)["window_id"]

            options["before_images"] = before_images
        result=self.full_scan(phase,completed,commit_point,**options)
        self.scan_results[phase]=result
        self.save()
        if not result.get("restored"):
            raise RuntimeError(result.get("restore_error") or "Scan quadrupole restore failed")
        self._last_confirmed=dict(self.machine.snapshot())
        self.check()
        from .optimization import measurement_values
        value=measurement_values(result, "grid", required_planes=(self.config.plane,))[self.config.plane]
        if len(records)!=len(self.config.scan_values):
            raise ValueError("Full scan did not complete the frozen K1 grid")
        return {"normalized_emittance_mm_mrad":value,"standard_error_mm_mrad":None,
                "status":"valid","source":"existing_scan","archive":result.get("archive")}

    def run(self):
        if getattr(self.machine,"recovery_only",False):
            raise RuntimeError("Restore-only handle: prepare the shared measurement before resuming")
        with self.exclusive():
            if self.status == "completed":
                return self.payload()
            if self.initial is not None and self.restoration_status not in {"succeeded", "not_required"}:
                raise RuntimeError("Recover initial devices before resuming")
            self._started = self.clock()
            self.machine.check = self.check
            self.machine.archive_dir = self.run_dir
            self.error, self.confirmed = None, False
            restore_needed = False
            try:
                self.check()
                if self.initial is None:
                    self.initial = dict(self.machine.snapshot())
                    self.candidates = current_candidates(
                        self.config.current_low, self.config.current_high,
                        self.initial["current_a"], self.config.current_candidate_count,
                    )
                else:
                    self.machine.verify_snapshot(self.initial)
                self.machine.configure(self.candidates)
                self._last_confirmed = dict(self.machine.snapshot())
                self.save()  # Snapshot is durable before the first motion.
                restore_needed = True
                self.restoration_status = "pending"
                initial_index = self.candidates.index(self.initial["current_a"])
                n = len(self.config.scan_values)
                if len(self.baseline) < n:
                    self._begin_measurement({"phase": "baseline", "current_a": self.initial["current_a"],
                                             "scan_values": self.config.scan_values[len(self.baseline):],
                                             "remaining_points": n-len(self.baseline)})
                    self.baseline_fit = self._full_scan_phase(initial_index, "baseline", self.baseline)
                elif self.baseline_fit is None:
                    self.baseline_fit = self._full_scan_phase(initial_index, "baseline", self.baseline)
                if self.ict1_settings is not None and self.ict1_baseline is None:
                    baseline = self._full_scan_charge(self.baseline)
                    if baseline["status"] != "valid":
                        raise ValueError(baseline["error"])
                    self.ict1_baseline = baseline
                    baseline["minimum_nc"] = baseline["value"] * self.ict1_settings["retention"]
                    self.save()
                if self.core is None:
                    self.core = self._create_core()
                    self.save()
                budget = len(self.core.initial_design) + self.config.active_queries
                while len(self.core.observations) < min(budget, self.core.pool.query_count):
                    self.check()
                    if (self.ict1_settings is not None
                            and len(self.core.observations) >= len(self.core.initial_design)
                            and self._active_attempts() >= self.config.active_queries):
                        break
                    self.status = "selecting"
                    self.emit()
                    if self.ict1_settings is None:
                        suggestion = self.core.propose(check=self.check)
                    else:
                        started = self.clock()
                        try:
                            suggestion = self.core.propose(check=self.check)
                        finally:
                            self.eig_seconds += self.clock() - started
                    self.check()
                    if suggestion.status != "ready":
                        self.status = suggestion.status
                        raise WorkflowInterrupted(self.status)
                    phase = "initialization" if suggestion.phase == "initial" else "active"
                    record = self._point(suggestion.candidate_index, suggestion.scan_index, phase, suggestion=suggestion)
                    m = record["measurement"]
                    self.core.observe(PointObservation(suggestion.query_index, m["variance_mm2"], m["noise_std_mm2"]))
                    self.save()
                    if self.ict1_settings is not None and record["ict1"]["status"] != "valid":
                        raise ValueError(record["ict1"]["error"])
                if self.recommendation is None:
                    self.recommendation = asdict(self.core.recommend())
                    self.save()
                candidate = self.recommendation["recommended_candidate_index"]
                if candidate is None:
                    self.status = ("invalid_recommendation" if self.ict1_settings is None else
                                   self.recommendation["status"])
                    raise WorkflowInterrupted(self.status)
                if len(self.verification) < n:
                    self._begin_measurement({"phase": "verification", "current_a": self.candidates[candidate],
                                             "scan_values": self.config.scan_values[len(self.verification):],
                                             "remaining_points": n-len(self.verification)})
                    self.verification_fit = self._full_scan_phase(candidate, "verification", self.verification)
                elif self.verification_fit is None:
                    self.verification_fit = self._full_scan_phase(candidate, "verification", self.verification)
                if self.ict1_settings is not None:
                    self.ict1_verification = self._full_scan_charge(self.verification)
                    self._assess_ict1(self.ict1_verification)
                    if self.ict1_verification["status"] != "valid":
                        raise ValueError(self.ict1_verification["error"])
                self.confirmed = (self.verification_fit["normalized_emittance_mm_mrad"] <
                                  self.baseline_fit["normalized_emittance_mm_mrad"] and
                                  (self.ict1_verification is None or self.ict1_verification["feasible"]))
                self.status = "completed"
            except WorkflowInterrupted as exc:
                self.status = str(exc)
            except Exception as exc:
                self.status, self.error = "failed", str(exc)
            finally:
                if restore_needed:
                    try:
                        self._restore_initial()
                        self.restoration_status = "succeeded"
                    except Exception as exc:
                        self.status, self.error = "restore_failed", str(exc)
                        self.restoration_status, self.confirmed = "failed", False
                self.elapsed_seconds = self.elapsed()
                self._started = None
                self.pause_requested.clear()
                self.save()
                self.emit(baseline_fit=self.baseline_fit, verification_fit=self.verification_fit,
                          error=self.error, restoration_status=self.restoration_status)
            return self.payload()

    def manual_move(self, *, apply_verified=False):
        with self.exclusive():
            if apply_verified and getattr(self.machine,"recovery_only",False):
                raise ValueError("Restore-only handle cannot apply recommendations")
            if self.initial is None:
                raise ValueError("No initial snapshot exists")
            if apply_verified and (not self.confirmed or self.restoration_status != "succeeded"):
                raise ValueError("No verified improvement is available")
            action = {"apply_verified": apply_verified, "state": "pending", "time": time.time()}
            self.actions.append(action)
            self.save()
            try:
                self.machine.check = lambda: None
                self.machine.configure(self.candidates)
                if apply_verified:
                    self.machine.verify_snapshot(self.initial)
                    candidate = self.recommendation["recommended_candidate_index"]
                    self.machine.apply_current(candidate)
                    if self.ict1_settings is not None:
                        window = self._capture_ict1(candidate, "apply", self.initial["scan_k1"])
                        action.update(ict1_window_id=window["window_id"], ict1=window["summary"])
                        if not window["summary"]["feasible"]:
                            raise ValueError(window["summary"].get("error", "ICT1 charge below the apply limit"))
                    self.restoration_status = "not_at_initial"
                else:
                    self._restore_initial()
                    self.restoration_status = "succeeded"
                    if self.status in {"restore_failed", "recovery_required"}:
                        self.status, self.error = "paused", None
                action["state"] = "completed"
            except Exception as exc:
                action.update(state="failed", error=str(exc))
                self.confirmed = False
                try:
                    self._restore_initial()
                    self.restoration_status = "succeeded"
                except Exception as restore_error:
                    self.restoration_status = "failed"
                    self.error = str(restore_error)
                raise
            finally:
                self.save()
                self.emit(action=action, restoration_status=self.restoration_status)

    @classmethod
    def load(cls, config, machine, run_dir, **kwargs):
        content = (Path(run_dir)/cls.filename).read_bytes()
        data = json.loads(content)
        expected_schema = WORKFLOW_SCHEMA if config.ict1_settings is None else ICT1_WORKFLOW_SCHEMA
        if (data.get("schema") != expected_schema
                or data["config_fingerprint"] != config.fingerprint
                or data["machine_fingerprint"] != machine.fingerprint):
            raise ValueError("Workflow configuration or machine fingerprint changed")
        obj = cls(config, machine, run_dir,
                  numerical_config=InfoBAXConfig.from_dict(data["numerical_config"]), **kwargs)
        for key in ("initial", "baseline", "verification", "records", "actions", "recommendation",
                    "baseline_fit", "verification_fit", "confirmed", "status", "error",
                    "restoration_status", "elapsed_seconds"):
            setattr(obj, key, data[key])
        obj.scan_results = data.get("scan_results", {})
        if expected_schema == ICT1_WORKFLOW_SCHEMA:
            obj.ict1_baseline = data["ict1"]["baseline"]
            obj.ict1_verification = data["ict1"]["verification"]
            obj.restore_readback = data["ict1"]["restore_readback"]
            obj.eig_seconds = data["ict1"]["eig_seconds"]
        obj.candidates = tuple(tuple(point) if isinstance(point, list) else point for point in data["candidates"])
        if obj.initial is not None and isinstance(obj.initial["current_a"], list):
            obj.initial["current_a"] = tuple(obj.initial["current_a"])
        if obj.status in {"awaiting_approval", "baseline", "initialization", "active", "selecting", "verification"}:
            obj.status = "paused" if obj.restoration_status == "succeeded" else "recovery_required"
        if data["core"] is not None:
            obj.core = InfoBAXCore.from_checkpoint(obj._pool(), data["core"])
            if (obj.core.ict1 is None) != (expected_schema == WORKFLOW_SCHEMA):
                raise ValueError("Workflow and checkpoint ICT1 settings disagree")
            if expected_schema == ICT1_WORKFLOW_SCHEMA and not getattr(machine, "recovery_only", False):
                obj._recover_point_charges()
        for action in obj.actions:
            if action["state"] == "pending":
                action.update(state="interrupted", error="Interrupted manual device operation")
                obj.status, obj.restoration_status, obj.confirmed = "recovery_required", "pending", False
        for record in obj.records:
            if record["state"] == "pending":
                record.update(state="interrupted", error="Interrupted before observation commit")
        if hasattr(machine, "camera"):
            machine.camera.statistics.update(data.get("camera_statistics", {}))
        obj._content = content
        return obj

    def _recover_point_charges(self):
        # A process can stop between writing the stable window and committing
        # it to the workflow. Its beam-size observation will still be pending.
        committed = {observation.window_id for observation in self.core.ict1_observations}
        pending = {record["sequence"]: record for record in self.records
                   if record["state"] in {"pending", "interrupted"} and "ict1_window_id" not in record}
        if not pending:
            return
        for path in self._ict1_directory.glob("*.json"):
            window = read_ict1_window(self._ict1_directory, path.stem)
            record = pending.get(window["sequence"])
            if (record is not None and window["window_id"] not in committed
                    and window["phase"] == record["phase"]
                    and window["candidate_index"] == record["candidate_index"]
                    and window["target_k1"] == record["scan_k1"]):
                self._commit_point_charge(record, window)
