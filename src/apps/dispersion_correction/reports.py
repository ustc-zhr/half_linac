from __future__ import annotations

import csv
import json
from io import StringIO
from pathlib import Path
from typing import Any

from half_linac.src.apps.dispersion_correction.models import (
    CorrectionResult,
    CorrectionStep,
    DispersionMeasurement,
)


def result_to_dict(result: CorrectionResult) -> dict[str, Any]:
    return {
        "success": result.success,
        "reason": result.reason,
        "initial_rms_mm": result.initial.rms_mm,
        "final_rms_mm": result.final.rms_mm,
        "improvement": result.improvement,
        "reduction_percent": result.reduction_percent,
        "initial_knobs": result.initial_knobs,
        "final_knobs": result.final_knobs,
        "knob_delta": result.knob_delta,
        "safety": {
            "ok": result.safety.ok,
            "reason": result.safety.reason,
            "max_orbit_change_mm": result.safety.max_orbit_change_mm,
        },
        "bpm_table": _bpm_table(result.initial, result.final),
        "steps": [_step_to_dict(step) for step in result.steps],
        "response": None
        if result.response is None
        else {
            "matrix": result.response.matrix.tolist(),
            "bpm_names": list(result.response.bpm_names),
            "knob_names": list(result.response.knob_names),
            "singular_values": result.response.singular_values.tolist(),
            "condition_number": result.response.condition_number,
        },
    }


def _measurement_to_dict(measurement: DispersionMeasurement) -> dict[str, Any]:
    return {
        "bpm_names": list(measurement.bpm_names),
        "plane": measurement.plane,
        "delta": measurement.delta,
        "values_mm": measurement.values_mm.tolist(),
        "target_values_mm": measurement.target_values_mm.tolist(),
        "target_mask": measurement.target_mask.tolist(),
        "valid": measurement.valid.tolist(),
        "rms_mm": measurement.rms_mm,
    }


def _step_to_dict(step: CorrectionStep) -> dict[str, Any]:
    data = {
        "iteration": step.iteration,
        "gain": step.gain,
        "delta_knobs": step.delta_knobs,
        "accepted": step.accepted,
        "reason": step.reason,
        "rms_before_mm": step.rms_before_mm,
        "rms_after_mm": step.rms_after_mm,
        "restored": step.restored,
    }
    if step.measurement_before is not None:
        data["measurement_before"] = _measurement_to_dict(
            step.measurement_before
        )
    if step.measurement_after is not None:
        data["measurement_after"] = _measurement_to_dict(
            step.measurement_after
        )
    if step.knobs_before is not None:
        data["knobs_before"] = step.knobs_before
    if step.knobs_trial is not None:
        data["knobs_trial"] = step.knobs_trial
    if step.device_values_before is not None:
        data["device_values_before"] = step.device_values_before
    if step.device_values_trial is not None:
        data["device_values_trial"] = step.device_values_trial
    return data


def result_to_json(result: CorrectionResult, indent: int = 2) -> str:
    return json.dumps(result_to_dict(result), indent=indent, sort_keys=True)


def result_to_csv(result: CorrectionResult) -> str:
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "bpm",
            "role",
            "target_d_eff_mm",
            "initial_d_eff_mm",
            "final_d_eff_mm",
            "valid",
        ]
    )
    for row in _bpm_table(result.initial, result.final):
        writer.writerow(
            [
                row["bpm"],
                row["role"],
                row["target_d_eff_mm"],
                row["initial_d_eff_mm"],
                row["final_d_eff_mm"],
                row["valid"],
            ]
        )
    writer.writerow([])
    writer.writerow(["knob", "initial", "final", "delta"])
    for name, initial in result.initial_knobs.items():
        final = result.final_knobs.get(name, initial)
        writer.writerow([name, initial, final, final - initial])
    return output.getvalue()


def result_to_markdown(result: CorrectionResult) -> str:
    lines = [
        "# Dispersion Correction Report",
        "",
        f"- Success: {result.success}",
        f"- Reason: {result.reason}",
        f"- Initial RMS D_eff: {result.initial.rms_mm:.6g} mm",
        f"- Final RMS D_eff: {result.final.rms_mm:.6g} mm",
        f"- RMS reduction: {result.reduction_percent:.3g}%",
        f"- Improvement: {result.improvement:.6g} x",
        f"- Safety: {result.safety.reason}",
        "",
        "## BPM D_eff",
        "",
        "| BPM | Role | Target (mm) | Initial (mm) | Final (mm) | Valid |",
        "| --- | --- | ---: | ---: | ---: | :---: |",
    ]
    for row in _bpm_table(result.initial, result.final):
        lines.append(
            f"| {row['bpm']} | {row['role']} | "
            f"{_format_optional_number(row['target_d_eff_mm'])} | "
            f"{row['initial_d_eff_mm']:.6g} | "
            f"{row['final_d_eff_mm']:.6g} | {row['valid']} |"
        )
    lines.extend(["", "## Knobs", "", "| Knob | Initial | Final | Delta |", "| --- | ---: | ---: | ---: |"])
    for name, initial in result.initial_knobs.items():
        final = result.final_knobs.get(name, initial)
        lines.append(f"| {name} | {initial:.6g} | {final:.6g} | {final - initial:.6g} |")
    return "\n".join(lines) + "\n"


def write_result_files(result: CorrectionResult, output_dir: str | Path) -> dict[str, Path]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": directory / "dispersion_correction_result.json",
        "csv": directory / "dispersion_correction_result.csv",
        "markdown": directory / "dispersion_correction_result.md",
    }
    paths["json"].write_text(result_to_json(result) + "\n", encoding="utf-8")
    paths["csv"].write_text(result_to_csv(result), encoding="utf-8")
    paths["markdown"].write_text(result_to_markdown(result), encoding="utf-8")
    return paths


def _bpm_table(initial: DispersionMeasurement, final: DispersionMeasurement) -> list[dict[str, Any]]:
    if initial.bpm_names != final.bpm_names:
        raise ValueError("Initial and final BPM names must match")
    return [
        {
            "bpm": name,
            "role": (
                "correction"
                if bool(initial.target_mask[index])
                else "monitor"
            ),
            "target_d_eff_mm": (
                float(initial.target_values_mm[index])
                if bool(initial.target_mask[index])
                else None
            ),
            "initial_d_eff_mm": float(initial.values_mm[index]),
            "final_d_eff_mm": float(final.values_mm[index]),
            "valid": bool(initial.valid[index] and final.valid[index]),
        }
        for index, name in enumerate(initial.bpm_names)
    ]


def _format_optional_number(value: float | None) -> str:
    return "—" if value is None else f"{value:.6g}"
