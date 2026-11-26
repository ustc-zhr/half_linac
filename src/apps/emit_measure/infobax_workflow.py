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

import numpy as np
from scipy.linalg import qr

from half_linac.src.optimization.infobax import FinitePoolSpec, InfoBAXCore, InfoBAXConfig, PointObservation
from half_linac.src.shared.beam_matrix import (
    DEFAULT_MAX_CONDITION_NUMBER,
)
from .control import emittance_lock
from .measurement_service import fingerprint, PointAcquisitionResult, fit_grid_results


DEFAULT_CURRENT_CANDIDATES = 5
DEFAULT_INITIAL_POINTS_PER_END = 3


class WorkflowInterrupted(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkflowConfig:
    current_low: float
    current_high: float
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
        if (not all(math.isfinite(v) for v in (self.current_low, self.current_high, self.energy_mev, self.max_minutes))
                or self.current_low >= self.current_high or self.energy_mev <= 0 or self.max_minutes <= 0):
            raise ValueError("Invalid current range, energy or time budget")
        if (self.plane not in {"x", "y"} or self.model not in {"M0", "transport_moments"}
                or type(self.active_queries) is not int or not 0 <= self.active_queries <= 3):
            raise ValueError("Invalid plane/model or active-query budget (0..3)")
        scans = tuple(float(v) for v in self.scan_values)
        projections = tuple(tuple(float(v) for v in row) for row in self.projections)
        if len(scans) < 3 or len(set(scans)) != len(scans) or not all(math.isfinite(v) for v in scans):
            raise ValueError("At least three distinct finite K1 values are required")
        if type(self.current_candidate_count) is not int or self.current_candidate_count < 2:
            raise ValueError("At least two current candidates are required")
        if type(self.initial_points_per_end) is not int or not 3 <= self.initial_points_per_end <= len(scans):
            raise ValueError("Initial K1 points per end must be between 3 and the number of K1 candidates")
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
        selected = sorted(int(i) for i in pivots[:3])
        if (np.linalg.matrix_rank(scaled) != 3
                or np.linalg.cond(scaled) > DEFAULT_MAX_CONDITION_NUMBER
                or np.linalg.cond(scaled[selected]) > DEFAULT_MAX_CONDITION_NUMBER):
            raise ValueError("Transport design is singular or ill-conditioned")

        # Keep the independent QR points and fill gaps for extra measurements.
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


def current_candidates(low, high, initial, count=DEFAULT_CURRENT_CANDIDATES):
    if not math.isfinite(initial) or not low <= initial <= high:
        raise ValueError("Initial current must be inside the selected search range")
    values = np.linspace(low, high, count)
    values[int(np.argmin(np.abs(values - initial)))] = initial
    values.sort()
    if len(np.unique(values)) != count:
        raise ValueError(f"{count} unique current candidates are required")
    return tuple(float(v) for v in values)


class InfoBAXWorkflow:
    """Persist every intent before motion; always restore under the control lease.

    ``approve(request, check)`` must block cancellably and return True only for
    explicit approval. Omitting the callback rejects execution, including Fake.
    """
    filename = "infobax_workflow.json"

    def __init__(self, config, machine, run_dir, *, approve=None, progress=None,
                 numerical_config=None, clock=time.monotonic, full_scan=None, control_lease=None):
        self.config, self.machine, self.run_dir = config, machine, Path(run_dir)
        self.full_scan, self.control_lease = full_scan, control_lease
        self.scan_results = {}
        self.approve, self.progress, self.clock = approve, progress, clock
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
        return {"schema": "half_infobax_workflow_v2", "config": self.config.to_dict(),
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

    def _approval(self, request):
        self.check()
        expected = self.machine.snapshot()
        self.status = "awaiting_approval"
        self.save()
        self.emit(request=request)
        if self.approve is None or not self.approve(request, self.check):
            raise WorkflowInterrupted("stopped")
        self.check()
        self.machine.verify_snapshot(expected)
        self.status = request["phase"]

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
                self._approval(record.copy())
            self.check()
            if self._last_confirmed is not None:
                self.machine.verify_snapshot(self._last_confirmed)
            self.machine.apply_query(candidate, record["scan_k1"])
            self.check()
            measurement = self.machine.measure_point(query_index, 0.0)
            self.check()
            record.update(state="observed", measurement=asdict(measurement))
            self._last_confirmed = dict(self.machine.snapshot())
        except BaseException as exc:
            record.update(state="interrupted" if isinstance(exc, WorkflowInterrupted) else "failed", error=str(exc))
            raise
        self.emit(record=record)
        return record

    def _pool(self):
        cfg = self.config
        scans = np.asarray(cfg.scan_values)
        currents = np.asarray(self.candidates)
        noise = [r["measurement"]["noise_std_mm2"] for r in self.baseline]
        return FinitePoolSpec(cfg.candidate_ids,
                              ((currents-currents.min())/np.ptp(currents))[:, None],
                              scans, (scans-scans.min())/np.ptp(scans), cfg.design,
                              cfg.energy_mev, np.tile(noise, len(currents)),
                              fingerprint({"machine": self.machine.fingerprint, "config": cfg.fingerprint}))

    def _create_core(self):
        n = len(self.config.scan_values)
        initial_index = self.candidates.index(self.initial["current_a"])
        indices = [initial_index*n+i for i in range(n)]
        endpoints = (0, len(self.candidates) - 1)
        scan_indices = self.config.initial_scan_indices()
        indices.extend(candidate*n + scan for candidate in endpoints for scan in scan_indices)
        indices = tuple(dict.fromkeys(indices))
        pool = self._pool()
        self.machine.validate_pool(pool)
        core = InfoBAXCore(pool, replace(self.numerical_config, initial_actions=len(indices)),
                          model=self.config.model, initial_queries=indices)
        for r in self.baseline:
            core.observe(PointObservation(r["query_index"], r["measurement"]["variance_mm2"],
                                          r["measurement"]["noise_std_mm2"]))
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
        result=self.full_scan(phase,completed,commit_point)
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
                    self._approval({"phase": "baseline", "current_a": self.initial["current_a"],
                                    "scan_values": self.config.scan_values[len(self.baseline):],
                                    "remaining_points": n-len(self.baseline)})
                    self.baseline_fit = self._full_scan_phase(initial_index, "baseline", self.baseline)
                elif self.baseline_fit is None:
                    self.baseline_fit = self._full_scan_phase(initial_index, "baseline", self.baseline)
                if self.core is None:
                    self.core = self._create_core()
                    self.save()
                budget = len(self.core.initial_design) + self.config.active_queries
                while len(self.core.observations) < min(budget, self.core.pool.query_count):
                    self.check()
                    self.status = "selecting"
                    self.emit()
                    suggestion = self.core.propose(check=self.check)
                    self.check()
                    if suggestion.status != "ready":
                        self.status = suggestion.status
                        raise WorkflowInterrupted(self.status)
                    phase = "initialization" if suggestion.phase == "initial" else "active"
                    record = self._point(suggestion.candidate_index, suggestion.scan_index, phase, suggestion=suggestion)
                    m = record["measurement"]
                    self.core.observe(PointObservation(suggestion.query_index, m["variance_mm2"], m["noise_std_mm2"]))
                    self.save()
                if self.recommendation is None:
                    self.recommendation = asdict(self.core.recommend())
                    self.save()
                candidate = self.recommendation["recommended_candidate_index"]
                if candidate is None:
                    self.status = "invalid_recommendation"
                    raise WorkflowInterrupted(self.status)
                if len(self.verification) < n:
                    self._approval({"phase": "verification", "current_a": self.candidates[candidate],
                                    "scan_values": self.config.scan_values[len(self.verification):],
                                    "remaining_points": n-len(self.verification)})
                    self.verification_fit = self._full_scan_phase(candidate, "verification", self.verification)
                elif self.verification_fit is None:
                    self.verification_fit = self._full_scan_phase(candidate, "verification", self.verification)
                self.confirmed = (self.verification_fit["normalized_emittance_mm_mrad"] <
                                  self.baseline_fit["normalized_emittance_mm_mrad"])
                self.status = "completed"
            except WorkflowInterrupted as exc:
                self.status = str(exc)
            except Exception as exc:
                self.status, self.error = "failed", str(exc)
            finally:
                if restore_needed:
                    try:
                        self.machine.restore(self.initial)
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
                    self.machine.apply_current(self.recommendation["recommended_candidate_index"])
                    self.restoration_status = "not_at_initial"
                else:
                    self.machine.restore(self.initial)
                    self.restoration_status = "succeeded"
                    if self.status in {"restore_failed", "recovery_required"}:
                        self.status, self.error = "paused", None
                action["state"] = "completed"
            except Exception as exc:
                action.update(state="failed", error=str(exc))
                self.confirmed = False
                try:
                    self.machine.restore(self.initial)
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
        if (data.get("schema") != "half_infobax_workflow_v2"
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
        obj.candidates = tuple(data["candidates"])
        if obj.status in {"awaiting_approval", "baseline", "initialization", "active", "selecting", "verification"}:
            obj.status = "paused" if obj.restoration_status == "succeeded" else "recovery_required"
        if data["core"] is not None:
            obj.core = InfoBAXCore.from_checkpoint(obj._pool(), data["core"])
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
