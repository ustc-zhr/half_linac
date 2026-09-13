#!/usr/bin/env python3
"""Short offline Elegant matching checks. All generated optics live in /tmp."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from repo_bootstrap import ensure_repo_import_path
ensure_repo_import_path(__file__)
from half_linac.src.shared.machine_profile import load_app_context, build_model_backend
from half_linac.src.apps.emit_measure.matching import (
    Point, Twiss, MeasurementBaseline, MatchingRequest, MagnetLimit,
    solve_matching, compare_measurement, endpoint,
)
from half_linac.src.apps.emit_measure.matching_model import ElegantMatchingModel


def isolated_model(directory):
    context = load_app_context("emit_measure", machine_id="half", control_backend="vm")
    backend = build_model_backend(context, line_name="ALL_MAIN")
    for attribute, name in (("working_dir", ""), ("optics_lte", "optics.lte"),
                            ("optics_ele", "optics.ele"), ("optics_json", "optics.json"),
                            ("optics_mat", "optics.mat")):
        setattr(backend, attribute, Path(directory) / name)
    backend.optics_log = "optics.log"
    return ElegantMatchingModel(backend)


def run(*, cross_only=False):
    with tempfile.TemporaryDirectory(prefix="matching_elegant_") as directory:
        model = isolated_model(directory)
        for names, target in (([f"QL{i:02d}" for i in range(7, 13)], Point("QL12", "exit")),
                              ([f"QT{i:02d}" for i in range(1, 7)], Point("QT06", "exit")),
                              ([f"QL{i:02d}" for i in range(7, 13)], Point("QT01"))):
            if cross_only and target.element != "QT01":
                continue
            start = Point(names[0])
            design = model.design(start, target)
            # A modest, independently specified entrance mismatch.
            initial = {p: Twiss(design[p][0]["beta"] * 1.02,
                                design[p][0]["alpha"] + 0.02, 1e-8) for p in ("x", "y")}
            overrides = {q: {"K1": float(model.elements[q]["K1"])}
                         for q in model.required_quads([start, target])}
            energy = design["x"][0]["energy_mev"]
            baseline = MeasurementBaseline(start, energy, initial, "half", "vm", "ALL_MAIN", overrides,
                                           same_state_declared=True)
            if target.element == "QT01":
                # Exercise the operational case: downstream measurement, upstream
                # matching magnets, with the target even preceding the diagnostic.
                measured_at = Point("QT02")
                baseline.overrides.update({q: {"K1": float(model.elements[q]["K1"])}
                    for q in model.required_quads([start, measured_at])})
                measured_planes, measured_energy = model.transport(baseline, measured_at)
                baseline = MeasurementBaseline(measured_at, measured_energy, measured_planes,
                    "half", "vm", "ALL_MAIN", deepcopy(baseline.overrides), same_state_declared=True)
            request = MatchingRequest(baseline, target,
                {q: MagnetLimit(overrides[q]["K1"] - 2, overrides[q]["K1"] + 2, 1) for q in names},
                max_evaluations=120)
            result = solve_matching(model, request)
            assert result.status == "model_target_met", (names, result.status, result.diagnostics)
            assert result.diagnostics["rank"] == 4
            # Check both directions and both boundary kinds across actual RF.
            downstream, end_energy = model.transport(baseline, target)
            measured = MeasurementBaseline(target, end_energy, downstream, "half", "vm", "ALL_MAIN",
                                           deepcopy(baseline.overrides), same_state_declared=True)
            restored, restored_energy = model.transport(measured, start)
            for p in ("x", "y"):
                assert abs(restored[p].beta / initial[p].beta - 1) < 1e-6
                assert abs(restored[p].emittance / initial[p].emittance - 1) < 1e-6
            assert abs(restored_energy - energy) < 1e-6
            # Deliberately biased remeasurement must not be reported as verified.
            measured.planes = {p: Twiss(endpoint(result.candidate[p]).beta * 3,
                                       endpoint(result.candidate[p]).alpha + 1, downstream[p].emittance)
                               for p in ("x", "y")}
            comparison = compare_measurement(model, result, measured)
            assert not all(v["within_tolerance"] for v in comparison["planes"].values())
            print(names[0], "->", target, result.status, result.diagnostics["after_bmag"], flush=True)
        # RF override must affect full-line entrance energy inference.
        backend = model.backend
        backend.energy_mev = 2200
        first = model.names[0]
        a = backend._entrance_energy_for_measurement(first, "QT01")
        rf = next(n for n in model.names if n == "L1")
        volts = float(model.elements[rf]["VOLT"])
        b = backend._entrance_energy_for_measurement(first, "QT01", lattice_overrides={rf: {"VOLT": volts + 1e6}})
        assert abs(a - b) > 1
        print("RF override regression passed", flush=True)


if __name__ == "__main__":
    run()
