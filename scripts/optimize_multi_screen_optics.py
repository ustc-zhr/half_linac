#!/usr/bin/env python3
"""Grid-search multi-screen measurement optics using a configured model only."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from itertools import product
import json
from pathlib import Path
import sys

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_PARENT = REPO_ROOT.parent
if str(REPO_PARENT) not in sys.path:
    sys.path.insert(0, str(REPO_PARENT))

from half_linac.src.apps.emit_measure.multi_screen import (
    build_multi_screen_optics,
    rebuild_multi_screen_optics,
    score_multi_screen_optics,
)
from half_linac.src.shared.beam_matrix import (
    normalized_emittance,
    simulate_beam_matrix_uncertainty,
    transport_beam_moments,
)
from half_linac.src.shared.machine_profile import (
    build_model_backend,
    get_emit_multi_screen_preset,
    load_app_context,
)


@dataclass(frozen=True)
class GridVariable:
    element: str
    field: str
    low: float
    high: float
    steps: int

    @property
    def values(self) -> np.ndarray:
        return np.linspace(self.low, self.high, self.steps)


@dataclass(frozen=True)
class FixedSetting:
    element: str
    field: str
    value: float


def _grid_variable(value: str) -> GridVariable:
    try:
        element, field, low, high, steps = value.split(":")
        variable = GridVariable(element, field, float(low), float(high), int(steps))
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(
            "expected ELEMENT:FIELD:LOW:HIGH:STEPS"
        ) from exc
    if not element or not field or variable.low >= variable.high or variable.steps < 2:
        raise argparse.ArgumentTypeError(
            "variable requires names, LOW < HIGH, and STEPS >= 2"
        )
    return variable


def _fixed_setting(value: str) -> FixedSetting:
    try:
        element, field, selected = value.split(":")
        setting = FixedSetting(element, field, float(selected))
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("expected ELEMENT:FIELD:VALUE") from exc
    if not element or not field or not np.isfinite(setting.value):
        raise argparse.ArgumentTypeError("fixed setting requires names and a finite value")
    return setting


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Offline grid search minimizing the worse X/Y first-order emittance "
            "uncertainty. No PVs are read or written."
        )
    )
    parser.add_argument("--machine", required=True, help="Machine profile id.")
    parser.add_argument("--model-backend", help="Configured model backend name.")
    parser.add_argument(
        "--preset",
        help="Configured multi-screen preset id; defaults to the profile default.",
    )
    parser.add_argument(
        "--variable",
        action="append",
        required=True,
        type=_grid_variable,
        metavar="ELEMENT:FIELD:LOW:HIGH:STEPS",
        help="Grid variable; repeat for multiple model fields.",
    )
    parser.add_argument(
        "--fixed",
        action="append",
        type=_fixed_setting,
        default=[],
        metavar="ELEMENT:FIELD:VALUE",
        help="Fixed model override applied to every candidate; repeat as needed.",
    )
    parser.add_argument(
        "--twiss-source",
        help=(
            "Fixed upstream beam-matrix location. When set, candidate maps transport "
            "its design Twiss to the measurement reference."
        ),
    )
    parser.add_argument("--size-error-percent", type=float, default=1.0)
    parser.add_argument("--normalized-emittance-mm-mrad", type=float, default=10.0)
    parser.add_argument("--min-rms-size-mm", type=float, default=0.1)
    parser.add_argument("--max-rms-size-mm", type=float, default=3.0)
    parser.add_argument("--validation-trials", type=int, default=3_000)
    parser.add_argument("--seed", type=int, default=20260909)
    parser.add_argument("--top", type=int, default=10)
    return parser


def _reference_twiss(backend, reference_element, plane):
    matches = [
        row
        for row in backend.get_design_twiss_profile(plane).rows
        if row["element_name"] == reference_element
    ]
    if not matches:
        raise ValueError(
            f"design Twiss profile does not contain reference element {reference_element!r}"
        )
    return matches[-1]


def _moments(emittance, twiss):
    beta = float(twiss["beta"])
    alpha = float(twiss["alpha"])
    return emittance * np.asarray(
        (beta, -alpha, (1.0 + alpha**2) / beta),
        dtype=float,
    )


def _merge_overrides(*setting_groups):
    overrides = {}
    for settings in setting_groups:
        for setting, value in settings:
            overrides.setdefault(setting.element, {})[setting.field] = float(value)
    return overrides


def _reference_moments(
    backend,
    twiss_source,
    reference_element,
    source_x_moments,
    source_y_moments,
    overrides,
):
    if twiss_source == reference_element:
        return source_x_moments, source_y_moments
    matrix = np.asarray(
        backend.get_map(
            twiss_source,
            reference_element,
            lattice_overrides=overrides,
            seq="exit2exit",
            twiss_only=True,
        ),
        dtype=float,
    )
    if matrix.shape != (6, 6):
        raise ValueError("source-to-reference model map must have shape (6, 6)")
    coupling = np.concatenate(
        (matrix[0:2, 2:4].ravel(), matrix[2:4, 0:2].ravel())
    )
    if np.any(np.abs(coupling) > 1.0e-12):
        raise ValueError("source-to-reference map contains x-y coupling")
    return (
        np.asarray(transport_beam_moments(matrix[0:2, 0:2], source_x_moments)),
        np.asarray(transport_beam_moments(matrix[2:4, 2:4], source_y_moments)),
    )


def _twiss_payload(moments):
    a, b, c = (float(value) for value in moments)
    emittance = np.sqrt(a * c - b * b)
    return {"beta_m": a / emittance, "alpha": -b / emittance}


def _score_payload(optics, score, settings, x_moments, y_moments):
    return {
        "settings": settings,
        "feasible": score.feasible,
        "objective_percent": None if not score.feasible else 100.0 * score.objective,
        "x_relative_standard_deviation_percent": (
            100.0 * score.x_uncertainty.relative_emittance_standard_deviation
        ),
        "y_relative_standard_deviation_percent": (
            100.0 * score.y_uncertainty.relative_emittance_standard_deviation
        ),
        "x_rms_sizes_mm": [1000.0 * value for value in score.x_uncertainty.true_rms_sizes_m],
        "y_rms_sizes_mm": [1000.0 * value for value in score.y_uncertainty.true_rms_sizes_m],
        "last_screen_projection": {
            "x": optics.x_projections[-1],
            "y": optics.y_projections[-1],
        },
        "reference_twiss": {
            "x": _twiss_payload(x_moments),
            "y": _twiss_payload(y_moments),
        },
        "rejection_reasons": score.rejection_reasons,
    }


def _monte_carlo_payload(optics, x_moments, y_moments, relative_error, trials, seed):
    payload = {}
    for index, (plane, projections, moments) in enumerate(
        (
            ("x", optics.x_projections, x_moments),
            ("y", optics.y_projections, y_moments),
        )
    ):
        summary = simulate_beam_matrix_uncertainty(
            projections,
            moments,
            relative_error,
            trial_count=trials,
            seed=seed + index,
        )
        payload[plane] = {
            "valid_fraction": summary.valid_fraction,
            "relative_bias_percent": _percent(summary.relative_bias),
            "relative_standard_deviation_percent": _percent(
                summary.relative_standard_deviation
            ),
            "relative_rmse_percent": _percent(summary.relative_rmse),
            "invalid_status_counts": dict(summary.invalid_status_counts),
        }
    return payload


def _percent(value):
    return None if value is None else 100.0 * value


def main(argv=None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.size_error_percent <= 0.0:
        parser.error("--size-error-percent must be positive")
    if args.normalized_emittance_mm_mrad <= 0.0:
        parser.error("--normalized-emittance-mm-mrad must be positive")
    if args.min_rms_size_mm <= 0.0 or args.max_rms_size_mm <= args.min_rms_size_mm:
        parser.error("RMS size bounds must satisfy 0 < min < max")
    if args.validation_trials <= 0 or args.top <= 0:
        parser.error("--validation-trials and --top must be positive")
    variable_keys = [(variable.element, variable.field) for variable in args.variable]
    if len(set(variable_keys)) != len(variable_keys):
        parser.error("--variable entries must identify unique element fields")
    fixed_keys = [(setting.element, setting.field) for setting in args.fixed]
    if len(set(fixed_keys)) != len(fixed_keys):
        parser.error("--fixed entries must identify unique element fields")
    if set(variable_keys) & set(fixed_keys):
        parser.error("the same element field cannot be both --variable and --fixed")

    context = load_app_context(
        "emit_measure",
        machine_id=args.machine,
        model_backend=args.model_backend,
    )
    preset = get_emit_multi_screen_preset(context, args.preset)
    backend = build_model_backend(
        context,
        energy_mev=preset.energy_mev,
        line_name=preset.model_line,
    )
    if preset.energy_mev is None:
        raise ValueError("multi-screen preset must define analysis.energy_mev")

    source_element = args.twiss_source or preset.reference_element
    segment_elements = {
        str(element["NAME"])
        for element in backend.get_line_elements(
            source_element,
            preset.screens[-1],
        )
    }
    requested_elements = [
        *(variable.element for variable in args.variable),
        *(setting.element for setting in args.fixed),
    ]
    outside = sorted(
        element for element in requested_elements if element not in segment_elements
    )
    if outside:
        parser.error(
            "model overrides must be downstream of the Twiss source and no later than "
            f"the final screen: {', '.join(outside)}"
        )
    variable_elements = {variable.element for variable in args.variable}
    if source_element == preset.reference_element:
        variable_affects_reference = False
    else:
        reference_segment_elements = {
            str(element["NAME"])
            for element in backend.get_line_elements(
                source_element,
                preset.reference_element,
            )
        }
        variable_affects_reference = bool(
            variable_elements & reference_segment_elements
        )
    affected_observations = []
    for screen in preset.screens:
        if screen == preset.reference_element:
            continue
        upstream_elements = {
            str(element["NAME"])
            for element in backend.get_line_elements(preset.reference_element, screen)
        }
        if variable_elements & upstream_elements:
            affected_observations.append(screen)

    beta_gamma = normalized_emittance(1.0, preset.energy_mev)
    geometric_emittance = args.normalized_emittance_mm_mrad * 1.0e-6 / beta_gamma
    x_twiss = _reference_twiss(backend, source_element, "xplane")
    y_twiss = _reference_twiss(backend, source_element, "yplane")
    source_x_moments = _moments(geometric_emittance, x_twiss)
    source_y_moments = _moments(geometric_emittance, y_twiss)
    relative_error = args.size_error_percent / 100.0
    bounds = (args.min_rms_size_mm / 1000.0, args.max_rms_size_mm / 1000.0)
    fixed_pairs = tuple((setting, setting.value) for setting in args.fixed)
    baseline_overrides = _merge_overrides(fixed_pairs)

    baseline_optics = build_multi_screen_optics(
        backend,
        preset.reference_element,
        preset.screens,
        lattice_overrides=baseline_overrides,
    )
    baseline_x_moments, baseline_y_moments = _reference_moments(
        backend,
        source_element,
        preset.reference_element,
        source_x_moments,
        source_y_moments,
        baseline_overrides,
    )
    baseline_score = score_multi_screen_optics(
        baseline_optics,
        baseline_x_moments,
        baseline_y_moments,
        relative_error,
        min_rms_size_m=bounds[0],
        max_rms_size_m=bounds[1],
    )

    candidates = []
    failed_candidates = 0
    for values in product(*(variable.values for variable in args.variable)):
        variable_pairs = tuple(zip(args.variable, values))
        settings = {
            **{
                f"{setting.element}.{setting.field}": setting.value
                for setting in args.fixed
            },
            **{
            f"{variable.element}.{variable.field}": float(value)
            for variable, value in zip(args.variable, values)
            },
        }
        overrides = _merge_overrides(fixed_pairs, variable_pairs)
        try:
            optics = rebuild_multi_screen_optics(
                backend,
                baseline_optics,
                affected_observations,
                lattice_overrides=overrides,
            )
            if variable_affects_reference:
                x_moments, y_moments = _reference_moments(
                    backend,
                    source_element,
                    preset.reference_element,
                    source_x_moments,
                    source_y_moments,
                    overrides,
                )
            else:
                x_moments, y_moments = (
                    baseline_x_moments,
                    baseline_y_moments,
                )
            score = score_multi_screen_optics(
                optics,
                x_moments,
                y_moments,
                relative_error,
                min_rms_size_m=bounds[0],
                max_rms_size_m=bounds[1],
            )
        except Exception:
            failed_candidates += 1
            continue
        candidates.append(
            (score.objective, optics, score, settings, x_moments, y_moments)
        )

    feasible = sorted(
        (candidate for candidate in candidates if candidate[2].feasible),
        key=lambda candidate: candidate[0],
    )
    if not feasible:
        raise RuntimeError("grid search found no feasible optics candidate")
    (
        _,
        best_optics,
        best_score,
        best_settings,
        best_x_moments,
        best_y_moments,
    ) = feasible[0]

    payload = {
        "machine": context.profile.machine.id,
        "model_backend": context.model_backend.name,
        "preset": preset.id,
        "reference_element": preset.reference_element,
        "twiss_source_element": source_element,
        "screens": preset.screens,
        "objective": "minimize max(relative_sigma_emittance_x, relative_sigma_emittance_y)",
        "assumptions": {
            "size_error_percent": args.size_error_percent,
            "normalized_emittance_mm_mrad": args.normalized_emittance_mm_mrad,
            "min_rms_size_mm": args.min_rms_size_mm,
            "max_rms_size_mm": args.max_rms_size_mm,
            "x_source_twiss": {"beta_m": x_twiss["beta"], "alpha": x_twiss["alpha"]},
            "y_source_twiss": {"beta_m": y_twiss["beta"], "alpha": y_twiss["alpha"]},
        },
        "grid": {
            "candidate_count": int(np.prod([variable.steps for variable in args.variable])),
            "evaluated_count": len(candidates),
            "feasible_count": len(feasible),
            "failed_count": failed_candidates,
        },
        "baseline": _score_payload(
            baseline_optics,
            baseline_score,
            {
                f"{setting.element}.{setting.field}": setting.value
                for setting in args.fixed
            },
            baseline_x_moments,
            baseline_y_moments,
        ),
        "best": _score_payload(
            best_optics,
            best_score,
            best_settings,
            best_x_moments,
            best_y_moments,
        ),
        "top_candidates": [
            _score_payload(optics, score, settings, x_moments, y_moments)
            for _, optics, score, settings, x_moments, y_moments in feasible[: args.top]
        ],
        "monte_carlo_validation": {
            "trial_count": args.validation_trials,
            "baseline": _monte_carlo_payload(
                baseline_optics,
                baseline_x_moments,
                baseline_y_moments,
                relative_error,
                args.validation_trials,
                args.seed,
            ),
            "best": _monte_carlo_payload(
                best_optics,
                best_x_moments,
                best_y_moments,
                relative_error,
                args.validation_trials,
                args.seed,
            ),
        },
    }
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
