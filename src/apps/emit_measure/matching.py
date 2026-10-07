"""Offline, dual-plane Twiss matching. No Qt or control-system dependencies.

All emittances are geometric, in m rad; energy is kinetic MeV.
A model implements position(), required_quads(), transport(), profile(), design(),
fingerprint and snapshot. A baseline is never re-fitted inside the optimizer.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import csv
import json
import math
from pathlib import Path
from typing import Callable

import numpy as np
from scipy.optimize import least_squares

SCHEMA = "emit_matching_v1"
FAILURE_SCHEMA = "emit_matching_failure_v1"


class Cancelled(RuntimeError):
    pass


def positive(value, name):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return value


@dataclass(frozen=True)
class Point:
    element: str
    edge: str = "entrance"

    def __post_init__(self):
        if not self.element or self.edge not in ("entrance", "exit"):
            raise ValueError("Reference requires an element and entrance/exit")


@dataclass(frozen=True)
class Twiss:
    beta: float
    alpha: float
    emittance: float

    def __post_init__(self):
        positive(self.beta, "beta")
        positive(self.emittance, "geometric emittance")
        if not math.isfinite(self.alpha):
            raise ValueError("alpha must be finite")

    @property
    def gamma(self):
        return (1 + self.alpha**2) / self.beta

    def initial(self):
        return {"beta0": self.beta, "alpha0": self.alpha, "gamma0": self.gamma}


def propagate(twiss, matrix):
    sigma = np.array([[twiss.beta, -twiss.alpha], [-twiss.alpha, twiss.gamma]])
    transported = matrix @ sigma @ matrix.T
    scale = math.sqrt(float(np.linalg.det(transported)))
    return Twiss(float(transported[0, 0] / scale),
                 float(-transported[0, 1] / scale), twiss.emittance * scale)


def mismatch(value, design):
    return float((value.beta * design.gamma + value.gamma * design.beta
                  - 2 * value.alpha * design.alpha) / 2)


@dataclass
class MeasurementBaseline:
    point: Point
    energy_mev: float
    planes: dict[str, Twiss]
    machine: str
    backend: str
    line: str
    overrides: dict
    provenance: dict = field(default_factory=dict)
    same_state_declared: bool = False

    def validate(self):
        positive(self.energy_mev, "measurement kinetic energy")
        if set(self.planes) != {"x", "y"}:
            raise ValueError("Both planes from the same measurement are required")
        if not self.machine or not self.backend or not self.line:
            raise ValueError("Machine, control backend and line are required")
        if not self.same_state_declared:
            raise ValueError("Declare that both planes, energy and snapshot describe the same state")


@dataclass(frozen=True)
class MagnetLimit:
    lower: float
    upper: float
    max_change: float

    def bounds(self, current):
        if not all(math.isfinite(v) for v in (current, self.lower, self.upper)):
            raise ValueError("K1 values and limits must be finite")
        positive(self.max_change, "maximum absolute K1 change")
        low, high = max(self.lower, current - self.max_change), min(self.upper, current + self.max_change)
        if not low <= current <= high or low >= high:
            raise ValueError("Current K1 must be inside nonempty limits")
        return low, high


@dataclass
class MatchingRequest:
    measurement: MeasurementBaseline
    target: Point
    magnets: dict[str, MagnetLimit]
    tolerance: float = 0.01
    envelope_m: dict[str, float | None] = field(default_factory=lambda: {"x": None, "y": None})
    initial_evaluations: int | None = None
    max_evaluations: int = 100


@dataclass
class MatchingResult:
    request: dict
    model: dict
    start: dict
    initial: dict
    design: dict
    current: dict
    candidate: dict
    magnets: dict
    status: str
    diagnostics: dict
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def check_snapshot(model, measurement, points):
    measurement.validate()
    missing = [q for q in model.required_quads(points)
               if "K1" not in measurement.overrides.get(q, {})]
    if missing:
        raise ValueError("Snapshot does not cover: " + ", ".join(missing)
                         + ". Supply a same-state snapshot; do not substitute design K1.")
    for q in model.required_quads(points):
        if not math.isfinite(float(measurement.overrides[q]["K1"])):
            raise ValueError(f"Invalid snapshot K1: {q}")


def endpoint(profile):
    row = profile[-1]
    return Twiss(row["beta"], row["alpha"], row["emittance"])


def solve_matching(model, request: MatchingRequest, cancelled: Callable[[], bool] = lambda: False):
    baseline = deepcopy(request.measurement)
    positive(request.tolerance, "matching tolerance")
    if request.max_evaluations < 1:
        raise ValueError("Maximum evaluation budget must be positive")
    if request.initial_evaluations is not None and request.initial_evaluations < 1:
        raise ValueError("Initial evaluation budget must be positive")
    if not request.magnets:
        raise ValueError("Select matching quadrupoles")
    names = sorted(request.magnets, key=lambda name: model.position(Point(name)))
    start = Point(names[0])
    if any(model.position(Point(q, "exit")) > model.position(request.target) for q in names):
        raise ValueError("All adjustable quadrupoles must precede the target boundary")
    required = model.required_quads([start, request.target])
    if any(q not in required for q in names):
        raise ValueError("Adjustable elements must be quadrupoles on the matching path")
    check_snapshot(model, baseline, [baseline.point, start, request.target])
    for plane in ("x", "y"):
        if request.envelope_m.get(plane) is not None:
            positive(request.envelope_m[plane], "RMS envelope limit")
    original = np.array([float(baseline.overrides[q]["K1"]) for q in names])
    bounds = np.array([request.magnets[q].bounds(k) for q, k in zip(names, original)])
    scales = np.array([request.magnets[q].max_change for q in names])
    fingerprint = model.fingerprint

    def check():
        if cancelled():
            raise Cancelled("Matching cancelled")
        model.assert_unchanged(fingerprint)

    check()
    # This call is deliberately outside every residual/candidate evaluation.
    initial, start_energy = model.transport(baseline, start)
    design_profiles = model.design(start, request.target)
    # Compare envelopes at the measured normalized emittance, not an arbitrary
    # design emittance. Design alpha/beta and energy remain immutable.
    for p in ("x", "y"):
        rows = design_profiles[p]
        if "energy_mev" in rows[0]:
            mass = 0.51099895
            ref_p = math.sqrt(start_energy * (start_energy + 2 * mass))
            for row in rows:
                e = row["energy_mev"]
                row["emittance"] = initial[p].emittance * ref_p / math.sqrt(e * (e + 2 * mass))
                row["sigma_m"] = math.sqrt(row["beta"] * row["emittance"])
    design = {p: endpoint(design_profiles[p]) for p in ("x", "y")}
    calls = 0
    cache = {}

    def evaluate(values):
        nonlocal calls
        check()
        key = tuple(values)
        if key not in cache:
            if calls >= request.max_evaluations:
                raise RuntimeError("Matching evaluation budget exhausted")
            overrides = deepcopy(baseline.overrides)
            for q, k in zip(names, values):
                overrides[q]["K1"] = float(k)
            cache[key] = model.profile(start, request.target, initial, start_energy, overrides)
            calls += 1
        return cache[key]

    def optical_residual(profiles):
        residual = []
        for p in ("x", "y"):
            t, d = endpoint(profiles[p]), design[p]
            residual.extend([math.log(t.beta / d.beta), t.alpha - d.alpha * t.beta / d.beta])
        return np.array(residual)

    def residual(values):
        profiles = evaluate(values)
        result = list(optical_residual(profiles))
        result.extend(0.01 * (values - original) / scales)
        for p in ("x", "y"):
            limit = request.envelope_m.get(p)
            result.append(0.0 if limit is None else
                          10 * max(0, max(r["sigma_m"] for r in profiles[p]) / limit - 1))
        return result

    current = evaluate(original)
    initial_evaluations = (
        request.max_evaluations
        if request.initial_evaluations is None
        else request.initial_evaluations
    )
    if initial_evaluations > request.max_evaluations:
        raise ValueError("Initial evaluation budget must not exceed maximum budget")
    minimum_attempt_budget = len(names) + 3
    if request.max_evaluations < minimum_attempt_budget:
        raise ValueError(
            f"Evaluation budget must be at least {minimum_attempt_budget} "
            f"for {len(names)} adjustable quadrupoles"
        )

    budget_limit = min(
        request.max_evaluations,
        max(initial_evaluations, minimum_attempt_budget),
    )
    attempt_start = original
    attempts = []
    fit = None
    candidate = current
    while True:
        evaluations_before = calls
        residual_before = residual(attempt_start)
        remaining = budget_limit - calls
        max_nfev = max(1, (remaining - 1) // (len(names) + 1))
        fit = least_squares(
            residual,
            attempt_start,
            bounds=(bounds[:, 0], bounds[:, 1]),
            x_scale=scales,
            max_nfev=max_nfev,
            ftol=1e-7,
            xtol=1e-7,
            gtol=1e-7,
        )
        candidate = evaluate(fit.x)
        residual_after = residual(fit.x)
        attempt_target_met = all(
            mismatch(endpoint(candidate[plane]), design[plane]) - 1 <= request.tolerance
            for plane in ("x", "y")
        )
        norm_before = float(np.linalg.norm(residual_before))
        norm_after = float(np.linalg.norm(residual_after))
        improvement_floor = max(1e-12, norm_before * 1e-6)
        residual_improved = norm_before - norm_after > improvement_floor
        can_retry = (
            fit.status == 0
            and not attempt_target_met
            and residual_improved
            and budget_limit < request.max_evaluations
        )
        if can_retry:
            retry_decision = "retry_with_larger_budget"
        elif attempt_target_met:
            retry_decision = "stop_target_met"
        elif fit.status != 0:
            retry_decision = "stop_solver_terminated"
        elif not residual_improved:
            retry_decision = "stop_no_residual_improvement"
        else:
            retry_decision = "stop_maximum_budget_reached"
        attempts.append({
            "attempt": len(attempts) + 1,
            "evaluation_limit": budget_limit,
            "evaluations_before": evaluations_before,
            "evaluations_after": calls,
            "solver_nfev": int(fit.nfev),
            "solver_njev": None if fit.njev is None else int(fit.njev),
            "solver_status": int(fit.status),
            "solver_success": bool(fit.success),
            "solver_message": str(fit.message),
            "cost": float(fit.cost),
            "optimality": float(fit.optimality),
            "residual_norm_before": norm_before,
            "residual_norm_after": norm_after,
            "residual_improved": residual_improved,
            "target_met": attempt_target_met,
            "retry_decision": retry_decision,
        })
        if not can_retry:
            break
        attempt_start = fit.x.copy()
        budget_limit = min(
            request.max_evaluations,
            max(budget_limit + 1, budget_limit * 2),
        )
    assert fit is not None
    # Exclude regularization/constraint rows: they must not mask optical rank loss.
    jac = np.asarray(fit.jac[:4]) * scales[np.newaxis, :]
    singular = np.linalg.svd(jac, compute_uv=False)
    rank = int(np.sum(singular > max(1e-10, singular[0] * 1e-6))) if len(singular) else 0
    before = {p: mismatch(endpoint(current[p]), design[p]) for p in ("x", "y")}
    after = {p: mismatch(endpoint(candidate[p]), design[p]) for p in ("x", "y")}
    violations = []
    for q, k, old in zip(names, fit.x, original):
        lim = request.magnets[q]
        if not lim.lower - 1e-10 <= k <= lim.upper + 1e-10 or abs(k-old) > lim.max_change + 1e-10:
            violations.append(f"{q}: K1 constraint")
    for p in ("x", "y"):
        limit = request.envelope_m.get(p)
        if limit is not None and max(r["sigma_m"] for r in candidate[p]) > limit * (1 + 1e-8):
            violations.append(f"{p}: RMS envelope constraint")
    satisfied = all(v - 1 <= request.tolerance for v in after.values())
    improved = sum(after.values()) < sum(before.values()) - 1e-9
    active_bounds = {
        name: "lower" if marker < 0 else "upper" if marker > 0 else None
        for name, marker in zip(names, fit.active_mask)
    }
    termination_reason = (
        "evaluation_budget_exhausted"
        if fit.status == 0
        else "solver_converged"
        if fit.status > 0
        else "solver_failed"
    )
    status = "no_usable_suggestion"
    if violations:
        outcome_reason = "constraint_violation"
    elif rank != 4:
        outcome_reason = "rank_deficient"
    elif satisfied:
        status = "model_target_met"
        outcome_reason = "target_met"
    elif improved:
        status = "model_improved"
        outcome_reason = "improved_target_not_met"
    else:
        outcome_reason = "no_improvement"
    check()
    return MatchingResult(
        request=asdict(request), model=deepcopy(model.snapshot), start=asdict(start),
        initial={"energy_mev": start_energy, "planes": {p: asdict(t) for p, t in initial.items()}},
        design=design_profiles, current=current, candidate=candidate,
        magnets={q: {"current": float(old), "suggested": float(new), "change": float(new-old)}
                 for q, old, new in zip(names, original, fit.x)}, status=status,
        diagnostics={
            "before_bmag": before,
            "after_bmag": after,
            "rank": rank,
            "singular_values": singular.tolist(),
            "violations": violations,
            "evaluations": calls,
            "evaluation_budget": {
                "initial": initial_evaluations,
                "effective_initial": attempts[0]["evaluation_limit"],
                "maximum": request.max_evaluations,
            },
            "attempts": attempts,
            "retry_count": len(attempts) - 1,
            "termination_reason": termination_reason,
            "outcome_reason": outcome_reason,
            "solver_status": int(fit.status),
            "solver_success": bool(fit.success),
            "solver_message": str(fit.message),
            "solver_nfev": int(fit.nfev),
            "solver_njev": None if fit.njev is None else int(fit.njev),
            "cost": float(fit.cost),
            "optimality": float(fit.optimality),
            "active_bounds": active_bounds,
            "envelope_sampling": "quadrupole analytic extrema + element boundaries; RF/other interiors not certified",
        })


def compare_measurement(model, result: MatchingResult, measurement: MeasurementBaseline, tolerance=None):
    old = result.request["measurement"]
    if any(getattr(measurement, k) != old[k] for k in ("machine", "backend", "line")):
        raise ValueError("Remeasurement machine/backend/line differs from matching request")
    model.assert_unchanged(result.model["fingerprint"])
    target = Point(**result.request["target"])
    check_snapshot(model, measurement, [measurement.point, target])
    # Actual executed settings are mandatory even when measuring at the target.
    missing = set(result.magnets) - {q for q, v in measurement.overrides.items() if "K1" in v}
    if missing:
        raise ValueError("Executed K1 snapshot missing: " + ", ".join(sorted(missing)))
    actual, energy = model.transport(measurement, target)
    predicted_energy = result.candidate["x"][-1].get("energy_mev")
    if predicted_energy is not None and not math.isclose(energy, predicted_energy, rel_tol=1e-6, abs_tol=1e-6):
        raise ValueError("Target energy differs from the matching baseline; recalculate for the new energy state")
    tolerance = result.request["tolerance"] if tolerance is None else positive(tolerance, "acceptance tolerance")
    comparisons = {}
    for p in ("x", "y"):
        d = endpoint(result.design[p])
        bmag = mismatch(actual[p], d)
        predicted = endpoint(result.candidate[p])
        comparisons[p] = {"before": asdict(endpoint(result.current[p])), "predicted": asdict(predicted),
                          "measured": asdict(actual[p]), "bmag": bmag,
                          "improvement": result.diagnostics["before_bmag"][p] - bmag,
                          "prediction_error": {"beta": actual[p].beta - predicted.beta,
                                               "alpha": actual[p].alpha - predicted.alpha},
                          "within_tolerance": bmag - 1 <= tolerance}
    return {"measurement": asdict(measurement), "target": asdict(target), "energy_mev": energy,
            "kind": "measured" if measurement.point == target else "remeasurement_transport",
            "tolerance": tolerance, "planes": comparisons,
            "executed_k1": {q: measurement.overrides[q]["K1"] for q in result.magnets},
            "executed_minus_suggested": {q: float(measurement.overrides[q]["K1"]) - v["suggested"]
                                         for q, v in result.magnets.items()},
            "uncertainty": measurement.provenance.get("uncertainty"),
            "note": "Twiss tolerance only; no claim of statistical significance or beam transmission acceptance."}


def save_result(path, result, comparison=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema": SCHEMA, "result": asdict(result), "comparison": comparison}
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(path)


def save_failure(path, request, model, failure):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": FAILURE_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "request": asdict(request) if isinstance(request, MatchingRequest) else deepcopy(request),
        "model": deepcopy(model),
        "failure": deepcopy(failure),
    }
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(path)


def load_result(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema") != SCHEMA:
        raise ValueError("Unsupported matching archive")
    return MatchingResult(**payload["result"]), payload.get("comparison")


def export_csv(path, result):
    if result.status == "no_usable_suggestion":
        raise ValueError("No usable suggestion; diagnostic JSON can still be saved")
    with Path(path).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["element", "current_K1_m^-2", "suggested_K1_m^-2", "change_K1_m^-2", "status"])
        for q, v in result.magnets.items():
            writer.writerow([q, v["current"], v["suggested"], v["change"], result.status])
