from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any

import numpy as np

from half_linac.src.apps.dispersion_correction.calibration import (
    EnergyKnobCalibrationFit,
    actuator_step_for_delta,
    fit_actuator_to_delta,
    fit_quadratic_actuator_to_delta,
    predict_delta_from_fit,
)


@dataclass(frozen=True)
class EnergyCalibrationPoint:
    actuator_value: float | None = None
    measured_energy: float | None = None
    delta_p_over_p: float | None = None
    uncertainty: float | None = None
    note: str = ""
    enabled: bool = True


@dataclass(frozen=True)
class EnergyCalibrationDraft:
    actuator: str
    actuator_unit: str
    input_mode: str
    baseline_actuator: float
    reference_energy: float | None
    points: tuple[EnergyCalibrationPoint, ...]
    energy_unit: str = "MeV"
    machine_id: str = "standalone"
    backend: str = "offline"
    note: str = ""


@dataclass(frozen=True)
class EnergyCalibrationAnalysis:
    actuator_values: np.ndarray
    delta_values: np.ndarray
    fit: EnergyKnobCalibrationFit | None
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    target_delta: float
    target_actuator_step: float | None
    max_abs_residual: float | None
    directional_slope_mismatch: float | None
    plus_actuator_offset: float | None = None
    minus_actuator_offset: float | None = None

    @property
    def valid(self) -> bool:
        return self.fit is not None and not self.blockers


def analyze_energy_calibration_draft(
    draft: EnergyCalibrationDraft,
    *,
    target_delta: float,
    minimum_points: int = 5,
    minimum_r_squared: float = 0.98,
) -> EnergyCalibrationAnalysis:
    blockers: list[str] = []
    warnings: list[str] = []
    actuators: list[float] = []
    deltas: list[float] = []
    uncertainties_present = False

    mode = draft.input_mode.strip().lower()
    if mode not in {"measured_energy", "direct_delta"}:
        blockers.append(f"Unsupported input mode: {draft.input_mode}")
    reference_energy = draft.reference_energy
    if mode == "measured_energy" and (
        reference_energy is None
        or not np.isfinite(float(reference_energy))
        or float(reference_energy) <= 0
    ):
        blockers.append("Measured-energy mode requires a positive reference energy E0")

    for index, point in enumerate(draft.points, start=1):
        if not point.enabled:
            continue
        if (
            point.actuator_value is None
            and point.measured_energy is None
            and point.delta_p_over_p is None
            and point.uncertainty is None
            and not point.note.strip()
        ):
            continue
        if point.actuator_value is None or not np.isfinite(float(point.actuator_value)):
            blockers.append(f"Row {index} requires a finite actuator value")
            continue
        delta: float | None
        if mode == "measured_energy":
            if (
                point.measured_energy is None
                or not np.isfinite(float(point.measured_energy))
                or reference_energy is None
                or float(reference_energy) <= 0
            ):
                blockers.append(f"Row {index} requires a finite measured energy")
                continue
            delta = (
                float(point.measured_energy) - float(reference_energy)
            ) / float(reference_energy)
        else:
            delta = point.delta_p_over_p
            if delta is None or not np.isfinite(float(delta)):
                blockers.append(f"Row {index} requires a finite Δp/p")
                continue
        if point.uncertainty is not None and (
            not np.isfinite(float(point.uncertainty))
            or float(point.uncertainty) < 0
        ):
            blockers.append(f"Row {index} uncertainty must be non-negative")
            continue
        uncertainties_present = uncertainties_present or point.uncertainty is not None
        actuators.append(float(point.actuator_value))
        deltas.append(float(delta))

    actuator_array = np.asarray(actuators, dtype=float)
    delta_array = np.asarray(deltas, dtype=float)
    if len(actuators) < minimum_points:
        blockers.append(
            f"At least {minimum_points} enabled valid points are required"
        )
    if actuator_array.size and np.ptp(actuator_array) <= 0:
        blockers.append("Calibration points must span more than one actuator value")
    if delta_array.size and not (
        np.any(delta_array < 0) and np.any(delta_array > 0)
    ):
        blockers.append("Calibration points must cover both negative and positive Δp/p")
    if actuator_array.size and not (
        float(np.min(actuator_array))
        < float(draft.baseline_actuator)
        < float(np.max(actuator_array))
    ):
        blockers.append("Baseline actuator must lie inside the scanned actuator range")

    fit: EnergyKnobCalibrationFit | None = None
    target_step: float | None = None
    max_residual: float | None = None
    slope_mismatch: float | None = None
    if actuator_array.size >= 2 and np.ptp(actuator_array) > 0:
        directional_slope_blocker: str | None = None
        try:
            fit = fit_actuator_to_delta(
                actuator_array.tolist(),
                delta_array.tolist(),
            )
        except ValueError as exc:
            blockers.append(str(exc))
        if fit is not None:
            predicted = predict_delta_from_fit(fit, actuator_array)
            max_residual = float(np.max(np.abs(delta_array - predicted)))
            baseline_delta = (
                fit.slope_delta_per_actuator * float(draft.baseline_actuator)
                + fit.intercept_delta
            )
            baseline_tolerance = max(
                abs(float(target_delta)) * 0.25,
                2.0 * max_residual,
                1.0e-12,
            )
            if abs(baseline_delta) > baseline_tolerance:
                blockers.append(
                    "The fitted calibration does not give Δp/p≈0 at the "
                    "configured baseline actuator"
                )
            target_step = abs(float(target_delta) * fit.actuator_per_delta)
            if fit.r_squared < minimum_r_squared:
                blockers.append(
                    f"R² {fit.r_squared:.6g} is below the required "
                    f"{minimum_r_squared:.6g}"
                )
            lower_span = float(draft.baseline_actuator) - float(
                np.min(actuator_array)
            )
            upper_span = float(np.max(actuator_array)) - float(
                draft.baseline_actuator
            )
            if target_step > min(lower_span, upper_span):
                blockers.append(
                    "Target energy step lies outside the calibration range on "
                    "one side of the baseline"
                )
            slope_mismatch = _directional_slope_mismatch(
                actuator_array,
                delta_array,
                float(draft.baseline_actuator),
            )
            if slope_mismatch is None:
                warnings.append(
                    "At least two points on each side are recommended for "
                    "directional linearity checking"
                )
            elif slope_mismatch > 0.35:
                directional_slope_blocker = (
                    "Positive/negative directional slopes differ by "
                    f"{100.0 * slope_mismatch:.1f}%"
                )
                blockers.append(directional_slope_blocker)
            elif slope_mismatch > 0.20:
                warnings.append(
                    "Positive/negative directional slopes differ by "
                    f"{100.0 * slope_mismatch:.1f}%"
                )
        if directional_slope_blocker is not None and actuator_array.size >= 5:
            quadratic_fit, quadratic_blockers, quadratic_warnings = (
                _quadratic_fallback_analysis(
                    actuator_array,
                    delta_array,
                    baseline_actuator=float(draft.baseline_actuator),
                    target_delta=float(target_delta),
                    minimum_r_squared=float(minimum_r_squared),
                    linear_max_residual=max_residual,
                )
            )
            if quadratic_fit is not None and not quadratic_blockers:
                fit = quadratic_fit
                predicted = predict_delta_from_fit(fit, actuator_array)
                max_residual = float(np.max(np.abs(delta_array - predicted)))
                plan = actuator_step_for_delta(
                    float(target_delta),
                    _calibration_payload_for_fit(
                        draft,
                        fit,
                        actuator_array,
                        source_path="",
                    ),
                )
                target_step = (
                    max(abs(float(plan["plus_offset"])), abs(float(plan["minus_offset"])))
                    if plan.get("calibrated")
                    else None
                )
                blockers = [
                    item
                    for item in blockers
                    if not _linear_fit_blocker(item, directional_slope_blocker)
                ]
                warnings.append(directional_slope_blocker)
                warnings.append(
                    "Using quadratic calibration because linear directional slopes differ"
                )
                warnings.extend(quadratic_warnings)
            else:
                blockers.extend(quadratic_blockers)
    if uncertainties_present:
        warnings.append(
            "Uncertainty values are archived for traceability; the current fit is unweighted"
        )

    plus_offset = minus_offset = None
    if fit is not None:
        plan = actuator_step_for_delta(
            float(target_delta),
            _calibration_payload_for_fit(
                draft,
                fit,
                actuator_array,
                source_path="",
            ),
        )
        if plan.get("calibrated"):
            plus_offset = float(plan["plus_offset"])
            minus_offset = float(plan["minus_offset"])

    return EnergyCalibrationAnalysis(
        actuator_values=actuator_array,
        delta_values=delta_array,
        fit=fit,
        blockers=tuple(dict.fromkeys(blockers)),
        warnings=tuple(dict.fromkeys(warnings)),
        target_delta=float(target_delta),
        target_actuator_step=target_step,
        max_abs_residual=max_residual,
        directional_slope_mismatch=slope_mismatch,
        plus_actuator_offset=plus_offset,
        minus_actuator_offset=minus_offset,
    )


def calibration_fragment(
    draft: EnergyCalibrationDraft,
    analysis: EnergyCalibrationAnalysis,
    *,
    source_path: str,
) -> dict[str, Any]:
    if not analysis.valid or analysis.fit is None:
        raise ValueError("Calibration draft has not passed quality checks")
    return _calibration_payload_for_fit(
        draft,
        analysis.fit,
        analysis.actuator_values,
        source_path=source_path,
    )


def _calibration_payload_for_fit(
    draft: EnergyCalibrationDraft,
    fit: EnergyKnobCalibrationFit,
    actuator_values: np.ndarray,
    *,
    source_path: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": "linear_relative",
        "actuator_per_delta": fit.actuator_per_delta,
        "session_override": True,
        "source": str(source_path),
        "baseline_actuator": float(draft.baseline_actuator),
        "fit_r_squared": fit.r_squared,
        "fit_points": fit.n_samples,
        "valid_actuator_min": float(np.min(actuator_values)),
        "valid_actuator_max": float(np.max(actuator_values)),
    }
    if fit.order == 2 and fit.coefficients:
        payload.update(
            {
                "kind": "polynomial_relative",
                "order": 2,
                "coefficients": list(fit.coefficients),
                "actuator_per_delta": fit.actuator_per_delta,
            }
        )
    else:
        payload["actuator_per_delta"] = fit.actuator_per_delta
    return payload


def save_energy_calibration_draft(
    directory: str | Path,
    draft: EnergyCalibrationDraft,
    analysis: EnergyCalibrationAnalysis,
) -> dict[str, Path]:
    draft_dir = Path(directory)
    draft_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
    archive_path = draft_dir / f"energy_knob_calibration_{timestamp}.json"
    latest_path = draft_dir / "latest.json"
    payload = {
        "schema_version": 1,
        "status": "draft",
        "saved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "draft": {
            **asdict(draft),
            "points": [asdict(point) for point in draft.points],
        },
        "analysis": _analysis_payload(analysis),
    }
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    archive_path.write_text(text, encoding="utf-8")
    latest_path.write_text(text, encoding="utf-8")
    return {"archive": archive_path, "latest": latest_path}


def load_energy_calibration_draft(path: str | Path) -> EnergyCalibrationDraft:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    raw = payload.get("draft", payload)
    if not isinstance(raw, dict):
        raise ValueError("Calibration draft must contain an object")
    raw_points = raw.get("points", [])
    if not isinstance(raw_points, list):
        raise ValueError("Calibration draft points must be a list")
    points = tuple(
        EnergyCalibrationPoint(
            actuator_value=_optional_float(item.get("actuator_value")),
            measured_energy=_optional_float(item.get("measured_energy")),
            delta_p_over_p=_optional_float(item.get("delta_p_over_p")),
            uncertainty=_optional_float(item.get("uncertainty")),
            note=str(item.get("note", "")),
            enabled=bool(item.get("enabled", True)),
        )
        for item in raw_points
        if isinstance(item, dict)
    )
    return EnergyCalibrationDraft(
        actuator=str(raw.get("actuator", "")),
        actuator_unit=str(raw.get("actuator_unit", "")),
        input_mode=str(raw.get("input_mode", "measured_energy")),
        baseline_actuator=float(raw.get("baseline_actuator", 0.0)),
        reference_energy=_optional_float(raw.get("reference_energy")),
        points=points,
        energy_unit=str(raw.get("energy_unit", "MeV")),
        machine_id=str(raw.get("machine_id", "standalone")),
        backend=str(raw.get("backend", "offline")),
        note=str(raw.get("note", "")),
    )


def _directional_slope_mismatch(
    actuators: np.ndarray,
    deltas: np.ndarray,
    baseline: float,
) -> float | None:
    negative = actuators < baseline
    positive = actuators > baseline
    if np.count_nonzero(negative) < 2 or np.count_nonzero(positive) < 2:
        return None
    negative_slope = float(np.polyfit(actuators[negative], deltas[negative], 1)[0])
    positive_slope = float(np.polyfit(actuators[positive], deltas[positive], 1)[0])
    scale = max(abs(negative_slope), abs(positive_slope), 1.0e-30)
    return abs(positive_slope - negative_slope) / scale


def _linear_fit_blocker(item: str, directional_slope_blocker: str | None) -> bool:
    return (
        item == directional_slope_blocker
        or item.startswith("The fitted calibration does not give")
        or item.startswith("R² ")
        or item.startswith("Target energy step lies outside")
    )


def _quadratic_fallback_analysis(
    actuators: np.ndarray,
    deltas: np.ndarray,
    *,
    baseline_actuator: float,
    target_delta: float,
    minimum_r_squared: float,
    linear_max_residual: float | None,
) -> tuple[EnergyKnobCalibrationFit | None, list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    try:
        fit = fit_quadratic_actuator_to_delta(
            actuators.tolist(),
            deltas.tolist(),
            baseline_actuator=baseline_actuator,
        )
    except ValueError as exc:
        return None, [f"Quadratic calibration failed: {exc}"], warnings

    predicted = predict_delta_from_fit(fit, actuators)
    max_residual = float(np.max(np.abs(deltas - predicted)))
    baseline_delta = float(np.polyval(np.asarray(fit.coefficients), 0.0))
    baseline_tolerance = max(
        abs(float(target_delta)) * 0.25,
        2.0 * max_residual,
        1.0e-12,
    )
    if abs(baseline_delta) > baseline_tolerance:
        blockers.append(
            "Quadratic calibration does not give Δp/p≈0 at the configured baseline actuator"
        )
    if fit.r_squared < minimum_r_squared:
        blockers.append(
            f"Quadratic R² {fit.r_squared:.6g} is below the required {minimum_r_squared:.6g}"
        )
    if linear_max_residual is not None and max_residual > linear_max_residual * 1.05:
        warnings.append("Quadratic calibration residual is not better than the linear fit")

    negative = actuators < baseline_actuator
    positive = actuators > baseline_actuator
    if np.count_nonzero(negative) < 2 or np.count_nonzero(positive) < 2:
        warnings.append(
            "At least two points on each side are recommended for quadratic calibration"
        )

    calibration = {
        "kind": "polynomial_relative",
        "order": 2,
        "baseline_actuator": float(baseline_actuator),
        "coefficients": list(fit.coefficients),
        "valid_actuator_min": float(np.min(actuators)),
        "valid_actuator_max": float(np.max(actuators)),
        "actuator_per_delta": fit.actuator_per_delta,
    }
    plan = actuator_step_for_delta(float(target_delta), calibration)
    if not plan.get("calibrated"):
        blockers.append(f"Quadratic target step is unavailable: {plan.get('reason')}")

    plus = fit.slope_delta_per_actuator
    a = fit.coefficients[0] if fit.coefficients else 0.0
    offsets = np.linspace(
        float(np.min(actuators)) - baseline_actuator,
        float(np.max(actuators)) - baseline_actuator,
        80,
    )
    local_slopes = 2.0 * float(a) * offsets + float(plus)
    if np.any(np.sign(local_slopes) != np.sign(plus)):
        blockers.append("Quadratic calibration is not monotonic across the scanned range")

    return fit, blockers, warnings


def _analysis_payload(analysis: EnergyCalibrationAnalysis) -> dict[str, Any]:
    return {
        "valid": analysis.valid,
        "blockers": list(analysis.blockers),
        "warnings": list(analysis.warnings),
        "target_delta": analysis.target_delta,
        "target_actuator_step": analysis.target_actuator_step,
        "max_abs_residual": analysis.max_abs_residual,
        "directional_slope_mismatch": analysis.directional_slope_mismatch,
        "plus_actuator_offset": analysis.plus_actuator_offset,
        "minus_actuator_offset": analysis.minus_actuator_offset,
        "fit": analysis.fit.as_dict() if analysis.fit is not None else None,
    }


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)
