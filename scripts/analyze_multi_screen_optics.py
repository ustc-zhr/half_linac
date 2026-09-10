#!/usr/bin/env python3
"""Report multi-screen transport projections for any configured machine model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_PARENT = REPO_ROOT.parent
if str(REPO_PARENT) not in sys.path:
    sys.path.insert(0, str(REPO_PARENT))

from half_linac.src.apps.emit_measure.multi_screen import (
    assess_multi_screen_observability,
    assess_multi_screen_prefixes,
    build_multi_screen_optics,
)
from half_linac.src.shared.beam_matrix import (
    normalized_emittance,
    simulate_beam_matrix_uncertainty,
)
from half_linac.src.shared.machine_profile import (
    build_model_backend,
    get_emit_multi_screen_preset,
    load_app_context,
    load_model_context,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze multi-screen beam-matrix observability without PV access."
    )
    parser.add_argument("--machine", help="Machine profile id; defaults to profile environment.")
    parser.add_argument("--model-backend", help="Configured model backend name.")
    parser.add_argument("--model-line", help="Model line containing reference and screens.")
    parser.add_argument("--energy-mev", type=float, help="Kinetic energy at the reference point.")
    parser.add_argument(
        "--preset",
        nargs="?",
        const="",
        metavar="ID",
        help="Use a configured multi-screen preset; omit ID to use its default.",
    )
    parser.add_argument("--reference", help="Common beam-matrix reference element.")
    parser.add_argument(
        "--screens",
        nargs="+",
        metavar="ELEMENT",
        help="Three or more observation element ids in beamline order.",
    )
    parser.add_argument(
        "--monte-carlo-trials",
        type=int,
        default=0,
        help="Run this many random-error trials for every 3..N screen prefix (default: off).",
    )
    parser.add_argument(
        "--relative-size-errors-percent",
        type=float,
        nargs="+",
        default=(1.0, 2.0, 5.0),
        metavar="PERCENT",
        help="Independent per-screen RMS-size errors used by Monte Carlo.",
    )
    parser.add_argument(
        "--normalized-emittance-mm-mrad",
        type=float,
        default=10.0,
        help="Representative normalized emittance used for both planes (default: 10).",
    )
    parser.add_argument("--seed", type=int, default=20260909, help="Monte Carlo seed.")
    parser.add_argument(
        "--good-condition-number",
        type=float,
        default=100.0,
        help="Condition-number threshold for Good observability (default: 100).",
    )
    parser.add_argument(
        "--marginal-condition-number",
        type=float,
        default=1_000.0,
        help="Upper condition-number threshold before Poor (default: 1000).",
    )
    return parser


def _diagnostic_payload(diagnostics):
    return {
        "measurement_matrix": diagnostics.measurement_matrix,
        "rank": diagnostics.rank,
        "degrees_of_freedom": diagnostics.degrees_of_freedom,
        "singular_values": diagnostics.singular_values,
        "condition_number": diagnostics.condition_number,
    }


def _observability_payload(observability):
    return {
        "status": observability.status,
        "message": observability.message,
        "x": _diagnostic_payload(observability.x),
        "y": _diagnostic_payload(observability.y),
    }


def _find_reference_twiss(backend, reference_element, plane):
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


def _monte_carlo_payload(args, backend, optics, energy_mev):
    if args.monte_carlo_trials < 0:
        raise ValueError("--monte-carlo-trials must be non-negative")
    if args.monte_carlo_trials == 0:
        return None
    if energy_mev is None or energy_mev <= 0.0:
        raise ValueError("positive --energy-mev is required for a Monte Carlo study")
    if args.normalized_emittance_mm_mrad <= 0.0:
        raise ValueError("--normalized-emittance-mm-mrad must be positive")
    if any(value < 0.0 for value in args.relative_size_errors_percent):
        raise ValueError("--relative-size-errors-percent values must be non-negative")

    normalized_emittance_m_rad = args.normalized_emittance_mm_mrad * 1.0e-6
    beta_gamma = normalized_emittance(1.0, energy_mev)
    geometric_emittance = normalized_emittance_m_rad / beta_gamma
    planes = {}
    for plane_index, (plane, projections, twiss_plane) in enumerate(
        (
            ("x", optics.x_projections, "xplane"),
            ("y", optics.y_projections, "yplane"),
        )
    ):
        twiss = _find_reference_twiss(backend, optics.reference_element, twiss_plane)
        beta = float(twiss["beta"])
        alpha = float(twiss["alpha"])
        moments = geometric_emittance * np.asarray(
            (beta, -alpha, (1.0 + alpha**2) / beta),
            dtype=float,
        )
        cases = []
        for error_index, error_percent in enumerate(args.relative_size_errors_percent):
            for screen_count in range(3, len(optics.observation_elements) + 1):
                summary = simulate_beam_matrix_uncertainty(
                    projections[:screen_count],
                    moments,
                    error_percent / 100.0,
                    trial_count=args.monte_carlo_trials,
                    seed=args.seed + plane_index * 10_000 + error_index,
                )
                cases.append(
                    {
                        "screen_count": screen_count,
                        "screens": optics.observation_elements[:screen_count],
                        "relative_size_error_percent": error_percent,
                        "valid_fraction": summary.valid_fraction,
                        "relative_bias_percent": _percent(summary.relative_bias),
                        "relative_standard_deviation_percent": _percent(
                            summary.relative_standard_deviation
                        ),
                        "relative_rmse_percent": _percent(summary.relative_rmse),
                        "invalid_status_counts": dict(summary.invalid_status_counts),
                    }
                )
        planes[plane] = {
            "reference_twiss": {"beta_m": beta, "alpha": alpha},
            "true_geometric_emittance_m_rad": geometric_emittance,
            "cases": cases,
        }
    return {
        "noise_model": "independent_gaussian_relative_rms_size_error",
        "trial_count_per_case": args.monte_carlo_trials,
        "seed": args.seed,
        "normalized_emittance_mm_mrad": args.normalized_emittance_mm_mrad,
        "planes": planes,
    }


def _percent(value):
    return None if value is None else 100.0 * value


def main(argv=None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    preset = None
    if args.preset is not None:
        if args.reference is not None or args.screens is not None:
            parser.error("--preset cannot be combined with --reference or --screens")
        context = load_app_context(
            "emit_measure",
            machine_id=args.machine,
            model_backend=args.model_backend,
        )
        preset = get_emit_multi_screen_preset(context, args.preset or None)
        reference = preset.reference_element
        screens = preset.screens
        model_line = args.model_line or preset.model_line
        energy_mev = args.energy_mev if args.energy_mev is not None else preset.energy_mev
    else:
        if args.reference is None or args.screens is None:
            parser.error("either --preset or both --reference and --screens are required")
        context = load_model_context(args.machine, args.model_backend)
        reference = args.reference
        screens = args.screens
        model_line = args.model_line
        energy_mev = args.energy_mev

    backend = build_model_backend(
        context,
        energy_mev=energy_mev,
        line_name=model_line,
    )
    optics = build_multi_screen_optics(
        backend,
        reference,
        screens,
    )
    path_elements = []
    for element in backend.get_line_elements(reference, optics.observation_elements[-1]):
        path_elements.append(
            {
                key: element[key]
                for key in ("NAME", "TYPE", "L", "K1")
                if key in element
            }
        )
    observability = assess_multi_screen_observability(
        optics,
        good_condition_number=args.good_condition_number,
        marginal_condition_number=args.marginal_condition_number,
    )
    prefix_observability = assess_multi_screen_prefixes(
        optics,
        good_condition_number=args.good_condition_number,
        marginal_condition_number=args.marginal_condition_number,
    )
    screens = []
    for element, x_projection, y_projection in zip(
        optics.observation_elements,
        optics.x_projections,
        optics.y_projections,
    ):
        screens.append(
            {
                "element": element,
                "x": {"r11": x_projection[0], "r12_m": x_projection[1]},
                "y": {"r33": y_projection[0], "r34_m": y_projection[1]},
            }
        )
    payload = {
        "machine": context.profile.machine.id,
        "model_backend": context.model_backend.name,
        "model_line": backend.line_name,
        "energy_mev": energy_mev,
        "reference_element": optics.reference_element,
        "transport_path": path_elements,
        "screens": screens,
        "x": _diagnostic_payload(optics.x_diagnostics),
        "y": _diagnostic_payload(optics.y_diagnostics),
        "observability": _observability_payload(observability),
        "prefix_observability": [
            {
                "screen_count": count,
                "screens": list(optics.observation_elements[:count]),
                **_observability_payload(prefix),
            }
            for count, prefix in enumerate(prefix_observability, start=3)
        ],
    }
    if preset is not None:
        payload["preset"] = preset.id
        payload["sampling"] = {
            "samples_per_screen": preset.sampling.samples_per_screen,
            "sample_interval_s": preset.sampling.sample_interval_s,
        }
    uncertainty_study = _monte_carlo_payload(args, backend, optics, energy_mev)
    if uncertainty_study is not None:
        payload["uncertainty_study"] = uncertainty_study
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
