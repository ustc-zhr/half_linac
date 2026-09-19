"""Coupled centering: sequential modulation, shared response fit, verified rollback.

Machine-specific topology is supplied by joint_centering configuration. No PVs
are contacted until a caller explicitly requests preflight or a scan.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import time
from typing import Callable

import numpy as np

from half_linac.src.apps.solenoid_centering.scan import (
    MachineProfileError, RestoreFailed, SolenoidCenteringScanner,
    StateDriftError, require_workflow_write_allowed, resolve_channel,
    resolve_write_target,
)
from half_linac.src.apps.solenoid_centering.profile_runtime import write_scan_result


@dataclass(frozen=True)
class JointTarget:
    preset_id: str
    bpms: tuple[str, ...]
    modulation_a: float


@dataclass(frozen=True)
class JointPlan:
    id: str
    display_name: str
    targets: tuple[JointTarget, ...]
    probe_a: float = 0.2
    max_step_a: float = 0.25
    max_excursion_a: float = 0.5
    max_iterations: int = 2
    svd_cutoff: float = 0.02
    damping: float = 0.05
    response_floor_mm: float = 0.01
    minimum_improvement: float = 0.05
    max_target_degradation: float = 0.2

    def validate(self):
        if not self.targets or len({t.preset_id for t in self.targets}) != len(self.targets):
            raise MachineProfileError("Joint targets must be nonempty and unique.")
        for target in self.targets:
            if (not target.bpms or len(set(target.bpms)) != len(target.bpms)
                    or not np.isfinite(target.modulation_a) or target.modulation_a <= 0):
                raise MachineProfileError("Invalid joint BPM selection or modulation amplitude.")
        for value in (self.probe_a, self.max_step_a, self.max_excursion_a,
                      self.damping, self.response_floor_mm):
            if not np.isfinite(value) or value <= 0:
                raise MachineProfileError("Joint amplitudes, damping and response floor must be positive.")
        for value in (self.svd_cutoff, self.minimum_improvement, self.max_target_degradation):
            if not np.isfinite(value) or not 0 < value < 1:
                raise MachineProfileError("Joint relative thresholds must be between zero and one.")
        if (type(self.max_iterations) is not int or not 1 <= self.max_iterations <= 10
                or self.probe_a > self.max_excursion_a):
            raise MachineProfileError("Invalid iteration count or probe exceeds excursion limit.")


def load_joint_plans(context) -> tuple[JointPlan, ...]:
    raw = context.profile.workflows.get("solenoid_centering", {}).get("joint_centering", {})
    plans = []
    for group in raw.get("groups", []):
        options = dict(raw.get("defaults", {}))
        options.update(group.get("options", {}))
        plan = JointPlan(
            id=group["id"], display_name=group.get("display_name", group["id"]),
            targets=tuple(JointTarget(t["preset"], tuple(t["bpms"]), float(t["modulation_a"]))
                          for t in group["targets"]), **options,
        )
        plan.validate()
        for target in plan.targets:
            if target.preset_id not in context.solenoid_centering_workflow.presets_by_id:
                raise MachineProfileError(f"Unknown joint preset: {target.preset_id}")
            for bpm in target.bpms:
                if context.profile.get_element(bpm).kind != "bpm":
                    raise MachineProfileError(f"{bpm} is not a BPM.")
        plans.append(plan)
    if len({p.id for p in plans}) != len(plans):
        raise MachineProfileError("Duplicate joint group ids.")
    return tuple(plans)


def solve_joint_step(matrix, residual, *, cutoff, damping):
    """Dimensionless probe units; suppress unobservable/weak singular modes."""
    matrix, residual = np.asarray(matrix, float), np.asarray(residual, float)
    if not np.all(np.isfinite(matrix)) or not np.all(np.isfinite(residual)):
        raise ValueError("Nonfinite joint response.")
    u, s, vt = np.linalg.svd(matrix, full_matrices=False)
    keep = s > max(1e-12, (s[0] if len(s) else 0) * cutoff)
    gains = np.zeros_like(s)
    gains[keep] = s[keep] / (s[keep] ** 2 + (damping * s[0]) ** 2)
    step = -vt.T @ (gains * (u.T @ residual))
    return step, {"rank": int(keep.sum()), "singular_values": s.tolist(),
                  "columns": matrix.shape[1], "rows": matrix.shape[0]}


class JointScanner:
    def __init__(self, context, plan: JointPlan, *, io=None,
                 progress: Callable[[str], None] | None = None, stop_requested=None):
        plan.validate()
        self.context, self.plan = context, plan
        self.progress = progress or (lambda message: None)
        workflow = context.solenoid_centering_workflow
        self.presets = [workflow.presets_by_id[t.preset_id] for t in plan.targets]
        self.helper = SolenoidCenteringScanner(context, self.presets[0], io=io,
                                              stop_requested=stop_requested)
        self.io = self.helper.io
        self.correctors = tuple(dict.fromkeys(c for p in self.presets for c in (p.hcorr, p.vcorr)))
        self.solenoids = tuple(p.solenoid for p in self.presets)
        if None in self.solenoids or len(set(self.solenoids)) != len(self.solenoids):
            raise MachineProfileError("Joint scan requires distinct machine-profile solenoid elements.")
        # Guard other configured front-end optics too, even for a smaller group.
        ids = tuple(dict.fromkeys(e for p in workflow.presets
                                 for e in (p.solenoid, p.hcorr, p.vcorr) if e))
        self.devices = {e: resolve_write_target(context, e) for e in ids}
        self.readbacks = {e: resolve_channel(context, e, "current_readback") for e in ids}
        self.tolerances = {}
        for p in workflow.presets:
            if p.motion_verification is None:
                raise MachineProfileError(f"Missing readback verification: {p.id}")
            for e in (p.hcorr, p.vcorr):
                self.tolerances[e] = p.motion_verification.corrector_readback_tolerance
            if p.solenoid:
                self.tolerances[p.solenoid] = p.motion_verification.solenoid_readback_tolerance
        for e in (*self.correctors, *self.solenoids):
            if self.devices[e].unit != "A":
                raise MachineProfileError("Joint centering currently requires current channels in A.")
        self.bpms = {
            b: tuple(resolve_channel(context, b, plane) for plane in ("x", "y"))
            for t in plan.targets for b in t.bpms
        }
        self.scale = context.machine.bpm_scale_to_mm(context.control_backend.name)
        self.original = {}
        self.expected = {}
        self.touched = set()
        self.records = []
        self.last_result = None
        self.prepared_state = None

    def _read(self, pv):
        value = float(self.io.read(pv))
        if not np.isfinite(value):
            raise ValueError(f"Nonfinite read from {pv}")
        return value

    def _check_state(self, state):
        for e, value in state.items():
            for pv in (self.devices[e].pv_name, self.readbacks[e]):
                if abs(self._read(pv) - value) > self.tolerances[e]:
                    raise StateDriftError(f"{e}: state changed; expected {value:g} A")

    def preflight(self):
        require_workflow_write_allowed(self.context, "solenoid_centering", "Joint centering")
        original = {e: self._read(t.pv_name) for e, t in self.devices.items()}
        self._check_state(original)
        ranges = {}
        for e in (*self.correctors, *self.solenoids):
            amplitude = (self.plan.max_excursion_a if e in self.correctors else
                         self.plan.targets[self.solenoids.index(e)].modulation_a)
            values = (original[e] - amplitude, original[e] + amplitude)
            for value in values:
                self.helper._check_single_value_limit(self.devices[e], value)
            ranges[e] = list(values)
        for pvs in self.bpms.values():
            for pv in pvs:
                self._read(pv)
        # Upper bound on point count, excluding readback/communication latency.
        passes = 2 + 2 * len(self.correctors) + self.plan.max_iterations
        points = len(self.solenoids) * (10 + 2 * passes)
        point_time = sum((10 + 2 * passes) * (p.settle_time_s +
                         (p.samples_per_point - 1) * p.sample_interval_s) for p in self.presets)
        settling_s = (sum(p.settle_time_s for p in self.presets) * (passes + 2)
                      + self.presets[0].settle_time_s * (3 * len(self.correctors)
                                                        + self.plan.max_iterations + 1))
        return {"original": original, "ranges_a": ranges, "points_upper_bound": points,
                "estimated_minimum_seconds": point_time + settling_s,
                "correctors": list(self.correctors), "plan": asdict(self.plan)}

    def _write(self, e, value, *, restoring=False):
        if not restoring:
            self.helper._raise_if_stopped()
        if not np.isfinite(value):
            raise ValueError("Nonfinite current request")
        self.helper._check_single_value_limit(self.devices[e], value)
        self.touched.add(e)  # A failed write may still have reached the power supply.
        self.helper._write_and_verify(e, self.devices[e].pv_name, self.readbacks[e],
                                      float(value), self.tolerances[e],
                                      stop_sensitive=not restoring)
        self.expected[e] = float(value)

    def _set_correctors(self, values):
        self._check_state(self.expected)
        for e, value in zip(self.correctors, values):
            self._write(e, value)
        self.helper._sleep(self.presets[0].settle_time_s)

    def _measure(self, label, *, full=False):
        self.progress(label)
        self._check_state(self.expected)
        offsets = np.linspace(-1, 1, 5) if full else np.array([-1., 1.])
        residual, scores = [], []
        for p, target in zip(self.presets, self.plan.targets):
            e = p.solenoid
            samples = []
            currents = self.original[e] + offsets * target.modulation_a
            record = {"label": label, "solenoid": e, "bpms": list(target.bpms),
                      "correctors": {c: self.expected[c] for c in self.correctors},
                      "currents_a": [], "samples_mm": []}
            self.records.append(record)
            for value in currents:
                self._check_state(self.expected)
                self._write(e, float(value))
                self.helper._sleep(p.settle_time_s)
                rows = []
                for n in range(p.samples_per_point):
                    self.helper._raise_if_stopped()
                    if n:
                        self.helper._sleep(p.sample_interval_s)
                    rows.append([self._read(pv) * self.scale for b in target.bpms
                                 for pv in self.bpms[b]])
                samples.append(np.mean(rows, axis=0))
                record["currents_a"].append(float(value))
                record["samples_mm"].append(rows)
            self._write(e, self.original[e])
            self.helper._sleep(p.settle_time_s)
            means = np.asarray(samples)
            delta = means - means.mean(axis=0) if full else (means[1:] - means[:1]) / 2
            scores.append(float(np.sqrt(np.mean(delta ** 2))))
            # Equal weight per target irrespective of number of BPMs/scan points.
            residual.extend((delta.ravel() / np.sqrt(delta.size)
                             / self.plan.response_floor_mm).tolist())
        self._check_state(self.expected)
        return np.asarray(residual), scores

    def _restore(self, state):
        errors = []
        for e in self.devices:
            if e not in self.touched:
                continue
            try:
                self._write(e, state[e], restoring=True)
            except Exception as exc:
                errors.append(f"{e}: {exc}")
        if errors:
            raise RestoreFailed("Joint restore failed: " + "; ".join(errors))
        self._check_state(state)

    def _acceptable(self, before, after):
        before, after = np.asarray(before), np.asarray(after)
        improvement = 1 - np.linalg.norm(after) / max(np.linalg.norm(before), 1e-15)
        protected = np.all(after <= np.maximum(
            before * (1 + self.plan.max_target_degradation), self.plan.response_floor_mm))
        resolved = np.max(before) > self.plan.response_floor_mm
        return bool(resolved and protected and improvement >= self.plan.minimum_improvement), float(improvement)

    def run(self):
        self.last_result = None
        if self.prepared_state is not None:
            self._check_state(self.prepared_state)
        report = self.preflight()
        started = time.monotonic()
        self.original = dict(report["original"])
        self.expected = dict(self.original)
        self.touched = set()
        self.records = []
        result = {"schema_version": 1, "mode": "joint_centering", "preset_id": "joint_" + self.plan.id,
                  "machine_id": self.context.machine.id, "backend": self.context.control_backend.name,
                  "preflight": report, "original": dict(self.original), "records": self.records,
                  "recommendation_available": False, "iterations": []}
        error = None
        try:
            _, baseline_full = self._measure("Baseline: five-point validation", full=True)
            base, base_scores = self._measure("Baseline: two-point modulation")
            current = np.array([self.original[c] for c in self.correctors])
            columns = []
            for j, c in enumerate(self.correctors):
                pair = []
                for sign in (-1, 1):
                    probe = current.copy()
                    probe[j] += sign * self.plan.probe_a
                    self._set_correctors(probe)
                    pair.append(self._measure(f"Response: {c} {sign * self.plan.probe_a:+g} A")[0])
                columns.append((pair[1] - pair[0]) / 2)
                self._set_correctors(current)
            matrix = np.column_stack(columns)
            # Recheck baseline after identification; do not fit against stale data.
            base, base_scores = self._measure("Recheck baseline after response measurement")
            result["response_matrix"] = matrix.tolist()
            for iteration in range(self.plan.max_iterations):
                step, diagnostics = solve_joint_step(matrix, base, cutoff=self.plan.svd_cutoff,
                                                     damping=self.plan.damping)
                result["diagnostics"] = diagnostics
                if diagnostics["rank"] == 0:
                    result["termination"] = "No measurable corrector response"
                    break
                step *= self.plan.probe_a
                step *= min(1., self.plan.max_step_a / max(np.max(np.abs(step)), 1e-15))
                origin = np.array([self.original[c] for c in self.correctors])
                # Scale the whole step to preserve the SVD direction within total bounds.
                factors = [1.]
                for value, delta, initial in zip(current, step, origin):
                    if abs(delta) > 1e-15:
                        edge = initial + np.sign(delta) * self.plan.max_excursion_a
                        factors.append(max(0., (edge - value) / delta))
                step *= min(factors)
                if np.max(np.abs(step)) < 1e-6:
                    result["termination"] = "Correction too small or excursion bound reached"
                    break
                candidate = current + step
                self._set_correctors(candidate)
                measured, scores = self._measure(f"Iteration {iteration + 1}")
                accepted, improvement = self._acceptable(base_scores, scores)
                result["iterations"].append({"correctors_a": candidate.tolist(), "scores_mm": scores,
                                              "accepted": accepted, "improvement": improvement})
                if not accepted:
                    self._set_correctors(current)
                    result["termination"] = "Candidate failed measured improvement/protection gate"
                    break
                current, base, base_scores = candidate, measured, scores
            _, final_full = self._measure("Final: five-point validation", full=True)
            accepted, improvement = self._acceptable(baseline_full, final_full)
            accepted = accepted and any(item["accepted"] for item in result["iterations"])
            result.update(baseline_scores_mm=baseline_full, final_scores_mm=final_full,
                          relative_improvement=improvement,
                          recommended={c: float(v) for c, v in zip(self.correctors, current)},
                          recommendation_available=accepted, operation_status="completed")
        except Exception as exc:
            error = exc
            result.update(operation_status="failed", error=str(exc), recommendation_available=False)
        finally:
            try:
                self._restore(self.original)
                result["restore"] = "verified"
            except Exception as exc:
                result.update(restore="failed", restore_error=str(exc), recommendation_available=False)
                error = exc
            # Separate filename namespace from single-solenoid results.
            result["preset_id"] = "joint_" + self.plan.id
            self.last_result = result
            result["elapsed_seconds"] = time.monotonic() - started
            result["archive_path"] = str(write_scan_result(self.context, result, namespace="joint"))
        if error is not None:
            raise error
        return result

    def apply(self, result):
        require_workflow_write_allowed(self.context, "solenoid_centering", "Apply joint result")
        if result is not self.last_result or not result.get("recommendation_available"):
            raise ValueError("No validated result from this scanner")
        self._check_state(result["original"])
        self.expected = dict(result["original"])
        self.touched = set()
        try:
            self._set_correctors([result["recommended"][c] for c in self.correctors])
            self._check_state(self.expected)
        except Exception as exc:
            result["apply_error"] = str(exc)
            try:
                self._restore(result["original"])
                result["restore"] = "verified"
            except Exception as restore_exc:
                result.update(restore="failed", restore_error=str(restore_exc),
                              recommendation_available=False)
                raise
            finally:
                write_scan_result(self.context, result, namespace="joint")
            raise
        result["applied"] = True
        write_scan_result(self.context, result, namespace="joint")

    def restore_applied(self, result):
        require_workflow_write_allowed(self.context, "solenoid_centering", "Restore joint result")
        if result is not self.last_result or not result.get("applied"):
            raise ValueError("No applied result from this scanner")
        applied = dict(result["original"])
        applied.update(result["recommended"])
        self._check_state(applied)
        self.touched = set(self.correctors)
        try:
            self._restore(result["original"])
        except Exception as exc:
            result.update(restore="failed", restore_error=str(exc), recommendation_available=False)
            write_scan_result(self.context, result, namespace="joint")
            raise
        result["restore"] = "verified"
        result["applied"] = False
        write_scan_result(self.context, result, namespace="joint")
