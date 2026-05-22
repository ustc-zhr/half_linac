"""Quick energy-consistency check using one corrector/BPM pair.

This utility follows the orbit machine profile instead of assuming HALF PV
prefixes or BPM/corrector numbering rules in code.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
from epics import PV

_REPO_BOOTSTRAP_ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / "repo_bootstrap.py").is_file()
)
if str(_REPO_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_BOOTSTRAP_ROOT))

from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

import half_linac.runtime_config as st
from half_linac.src.shared.machine_profile import load_app_context, resolve_channel


class EnergyCheckError(RuntimeError):
    """Raised when the one-corrector energy check cannot be completed."""


def _mean_pv_value(pv: PV, samples: int, interval_s: float) -> float:
    readings: list[float] = []
    for index in range(samples):
        value = pv.get()
        if value is None:
            raise EnergyCheckError(f"Failed to read PV {pv.pvname}.")
        readings.append(float(value))
        if index < samples - 1:
            time.sleep(interval_s)
    return float(np.mean(readings))


def _load_inverse_response_block(response_file: Path, n_bpm: int, n_cor: int, plane: str) -> np.ndarray:
    response_matrix = np.loadtxt(response_file)
    if response_matrix.shape != (2 * n_bpm, 2 * n_cor):
        raise EnergyCheckError(
            f"Unexpected response matrix shape {response_matrix.shape},"
            f" expected {(2 * n_bpm, 2 * n_cor)}."
        )

    if plane == "x":
        block = response_matrix[0:n_bpm, 0:n_cor]
    else:
        block = response_matrix[n_bpm : n_bpm * 2, n_cor : n_cor * 2]
    return np.linalg.inv(block)


def estimate_energy_consistency(
    corrector_id: str,
    *,
    kick_rad: float = 0.01e-3,
    samples: int = 2,
    interval_s: float | None = None,
) -> dict[str, float | str]:
    app_context = load_app_context("orbit_correct")
    workflow = app_context.orbit_workflow
    if workflow is None:
        raise EnergyCheckError("Orbit workflow is not available in the current app context.")
    mode = app_context.control_backend.name

    bpm_ids = list(workflow.bpms)
    xcor_ids = list(workflow.xcors)
    ycor_ids = list(workflow.ycors)
    n_bpm = len(bpm_ids)
    n_cor = len(xcor_ids)

    if corrector_id in xcor_ids:
        plane = "x"
        cor_ids = xcor_ids
    elif corrector_id in ycor_ids:
        plane = "y"
        cor_ids = ycor_ids
    else:
        raise EnergyCheckError(f"Corrector {corrector_id!r} is not part of workflows.orbit.")

    index = cor_ids.index(corrector_id)
    bpm_id = bpm_ids[index]
    bpm_pv = PV(resolve_channel(app_context, bpm_id, plane))
    cor_pv = PV(resolve_channel(app_context, corrector_id, "setpoint"))

    response_file = Path(st.rootpath) / "src" / "apps" / "orbit_correct" / "response.txt"
    inverse_block = _load_inverse_response_block(response_file, n_bpm, n_cor, plane)

    wait_s = interval_s
    if wait_s is None:
        wait_s = st.runtime_vmmachine if mode == "vm" else st.runtime_machine

    original_corrector = cor_pv.get()
    if original_corrector is None:
        raise EnergyCheckError(f"Failed to read corrector PV {cor_pv.pvname}.")
    original_corrector = float(original_corrector)

    try:
        bpm_initial = _mean_pv_value(bpm_pv, samples=samples, interval_s=wait_s)
        cor_pv.put(original_corrector + kick_rad)
        time.sleep(wait_s)
        bpm_kicked = _mean_pv_value(bpm_pv, samples=samples, interval_s=wait_s)
    finally:
        cor_pv.put(original_corrector)

    bpm_delta = bpm_kicked - bpm_initial
    if np.isclose(bpm_delta, 0.0):
        raise EnergyCheckError("Measured BPM change is zero; cannot estimate consistency.")

    measured_delta = bpm_initial / bpm_delta * kick_rad
    theoretical_delta = bpm_initial * inverse_block[index, index] * kick_rad * 2
    if np.isclose(theoretical_delta, 0.0):
        raise EnergyCheckError("Theoretical reference is zero; cannot estimate consistency.")

    ratio = measured_delta / theoretical_delta
    if ratio > 1.01:
        verdict = "energy > theory"
    elif ratio < 0.99:
        verdict = "energy < theory"
    else:
        verdict = "energy = theory"

    return {
        "corrector": corrector_id,
        "bpm": bpm_id,
        "plane": plane,
        "mode": mode,
        "ratio": float(ratio),
        "measured_delta": float(measured_delta),
        "theoretical_delta": float(theoretical_delta),
        "verdict": verdict,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corrector", help="Corrector id from workflows.orbit, such as XC07 or YC21.")
    parser.add_argument("--kick-rad", type=float, default=0.01e-3, help="Kick size in rad.")
    parser.add_argument("--samples", type=int, default=2, help="Number of BPM samples to average.")
    parser.add_argument("--interval-s", type=float, default=None, help="Delay between samples and after the kick.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = estimate_energy_consistency(
            args.corrector,
            kick_rad=args.kick_rad,
            samples=args.samples,
            interval_s=args.interval_s,
        )
    except Exception as exc:
        print(f"Energy consistency check failed: {exc}", file=sys.stderr)
        return 1

    print(
        f"{result['verdict']} | mode={result['mode']} plane={result['plane']} "
        f"corrector={result['corrector']} bpm={result['bpm']} ratio={result['ratio']:.6f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
