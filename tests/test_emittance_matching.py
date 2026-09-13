"""Offline matching regression tests; no EPICS calls or persistent model outputs."""
from copy import deepcopy
from dataclasses import asdict
import json
import math
from pathlib import Path
import tempfile
import unittest

import numpy as np
from repo_bootstrap import ensure_repo_import_path
ensure_repo_import_path(__file__)
from half_linac.src.apps.emit_measure.matching import (
    Point, Twiss, MeasurementBaseline, MatchingRequest, MagnetLimit, Cancelled,
    solve_matching, compare_measurement, save_result, load_result, export_csv,
    propagate, mismatch,
)
from half_linac.src.apps.emit_measure.matching_import import import_measurement


class AnalyticModel:
    """Four independent optical knobs for solver/control-flow tests."""
    fingerprint = "test-model"
    snapshot = {"fingerprint": fingerprint}
    names = ["Q1", "Q2", "Q3", "Q4", "END"]

    def __init__(self):
        self.transports = 0
        self.inputs = []
        self.changed = False
        self.rank_deficient = False

    def position(self, point):
        return self.names.index(point.element) + (point.edge == "exit")

    def required_quads(self, points):
        indexes = [self.position(p) for p in points]
        return [q for q in self.names[min(indexes):max(indexes)] if q.startswith("Q")]

    def assert_unchanged(self, fingerprint):
        if self.changed or fingerprint != self.fingerprint: raise ValueError("Model changed")

    def transport(self, measurement, target):
        self.transports += 1
        return deepcopy(measurement.planes), measurement.energy_mev

    @staticmethod
    def row(beta, alpha, emittance=1e-6):
        return {"s_m": 0.0, "beta": beta, "alpha": alpha, "emittance": emittance,
                "sigma_m": math.sqrt(beta * emittance)}

    def design(self, start, target):
        return {p: [self.row(2, 0)] for p in ("x", "y")}

    def profile(self, start, target, initial, energy, overrides):
        self.inputs.append(deepcopy(initial))
        values = [overrides[q]["K1"] for q in self.names[:4]]
        if self.rank_deficient: values[3] = 0
        return {p: [self.row(2 * math.exp(values[i] + 0.2), values[i+1] + 0.15)]
                for p, i in (("x", 0), ("y", 2))}


def fixture():
    model = AnalyticModel()
    baseline = MeasurementBaseline(Point("Q1"), 100, {p: Twiss(2, 0, 1e-6) for p in ("x", "y")},
                                   "half", "vm", "TEST", {q: {"K1": 0.0} for q in model.names[:4]},
                                   same_state_declared=True)
    request = MatchingRequest(baseline, Point("END", "exit"),
                              {q: MagnetLimit(-2, 2, 1) for q in model.names[:4]})
    return model, request


class MatchingTests(unittest.TestCase):
    def test_recoverable_fixed_boundary(self):
        model, request = fixture()
        result = solve_matching(model, request)
        self.assertEqual(result.status, "model_target_met")
        self.assertEqual(model.transports, 1)
        self.assertTrue(all(value == model.inputs[0] for value in model.inputs))
        self.assertEqual(request.measurement.overrides["Q1"]["K1"], 0)
        self.assertEqual(result.design["x"][0]["beta"], 2)
        self.assertLessEqual(result.diagnostics["evaluations"], request.max_evaluations)

    def test_bounded_unreachable(self):
        model, request = fixture()
        request.magnets = {q: MagnetLimit(-0.001, 0.001, 0.001) for q in request.magnets}
        request.tolerance = 1e-7
        result = solve_matching(model, request)
        self.assertNotEqual(result.status, "model_target_met")
        self.assertTrue(all(abs(v["change"]) <= 0.00100001 for v in result.magnets.values()))

    def test_rank_and_envelope(self):
        model, request = fixture()
        model.rank_deficient = True
        result = solve_matching(model, request)
        self.assertLess(result.diagnostics["rank"], 4)
        self.assertEqual(result.status, "no_usable_suggestion")
        model.rank_deficient = False
        request.envelope_m["x"] = 1e-6
        result = solve_matching(model, request)
        self.assertEqual(result.status, "no_usable_suggestion")
        self.assertTrue(result.diagnostics["violations"])

    def test_cancel_backend_failure_and_missing_state(self):
        model, request = fixture()
        with self.assertRaises(Cancelled): solve_matching(model, request, lambda: True)
        model.changed = True
        with self.assertRaisesRegex(ValueError, "Model changed"): solve_matching(model, request)
        model.changed = False
        del request.measurement.overrides["Q3"]
        with self.assertRaisesRegex(ValueError, "Q3"): solve_matching(model, request)
        model, request = fixture()
        request.measurement.same_state_declared = False
        with self.assertRaisesRegex(ValueError, "Declare"): solve_matching(model, request)

    def test_archive_and_remeasurement_bias(self):
        model, request = fixture()
        result = solve_matching(model, request)
        measurement = deepcopy(request.measurement)
        measurement.point = request.target
        measurement.planes = {p: Twiss(10, 1, 2e-6) for p in ("x", "y")}
        comparison = compare_measurement(model, result, measurement)
        self.assertFalse(comparison["planes"]["x"]["within_tolerance"])
        self.assertNotEqual(comparison["planes"]["x"]["prediction_error"]["beta"], 0)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "result.json"
            save_result(path, result, comparison)
            loaded, actual = load_result(path)
            self.assertEqual(asdict(loaded), asdict(result))
            self.assertEqual(actual, comparison)
            export_csv(Path(d) / "k1.csv", loaded)
        measurement.machine = "other"
        with self.assertRaisesRegex(ValueError, "differs"): compare_measurement(model, result, measurement)

    def test_transported_comparison_requires_executed_snapshot(self):
        model, request = fixture()
        result = solve_matching(model, request)
        measurement = deepcopy(request.measurement)
        comparison = compare_measurement(model, result, measurement)
        self.assertEqual(comparison["kind"], "remeasurement_transport")
        del measurement.overrides["Q2"]
        with self.assertRaisesRegex(ValueError, "Snapshot"): compare_measurement(model, result, measurement)

    def test_scan_import_units_and_both_planes(self):
        payload = {"machine_id": "half", "backend": "vm", "quad": "Q1", "energy_mev": 100,
                   "model_line": "TEST", "fit_summary": {"xplane": {"status": "valid", "beta": 2,
                   "alpha": 0, "emittance": 0, "determinant": 0.25},
                   "yplane": {"status": "valid", "beta": 3, "alpha": 1, "emittance": 2}}}
        result = import_measurement(payload)
        self.assertEqual(result.planes["x"].emittance, 0.5e-6)
        self.assertEqual(result.planes["y"].emittance, 2e-6)
        self.assertFalse(result.same_state_declared)
        del payload["fit_summary"]["yplane"]
        with self.assertRaises(ValueError): import_measurement(payload)

    def test_matrix_acceleration_and_inverse(self):
        twiss = Twiss(3, 0.5, 2e-6)
        matrix = np.array([[1, 2], [0, 0.5]])
        downstream = propagate(twiss, matrix)
        self.assertAlmostEqual(downstream.emittance, 1e-6)
        restored = propagate(downstream, np.linalg.inv(matrix))
        self.assertAlmostEqual(restored.beta, twiss.beta)
        self.assertAlmostEqual(restored.alpha, twiss.alpha)
        self.assertAlmostEqual(restored.emittance, twiss.emittance)
        self.assertAlmostEqual(mismatch(restored, twiss), 1)

    def test_quadrupole_interior_maximum_is_included(self):
        from half_linac.src.apps.emit_measure.matching_model import ElegantMatchingModel
        model = ElegantMatchingModel.__new__(ElegantMatchingModel)
        model.names = ["Q"]
        model.elements = {"Q": {"TYPE": "QUAD", "K1": "1"}}
        rows = [{"element": "Q", "edge": edge, "s_m": s, "beta": 0.1,
                 "alpha": 0., "emittance": 1e-6, "sigma_m": math.sqrt(1e-7)}
                for edge, s in (("entrance", 0.), ("exit", math.pi))]
        sampled = model._quad_extrema(rows, "x", {})
        self.assertAlmostEqual(max(r["beta"] for r in sampled), 10.)
        self.assertTrue(any(r["edge"] == "interior" for r in sampled))

    def test_changed_energy_cannot_pass_remeasurement(self):
        model, request = fixture()
        result = solve_matching(model, request)
        result.candidate["x"][-1]["energy_mev"] = 100
        changed = deepcopy(request.measurement)
        changed.energy_mev = 101
        with self.assertRaisesRegex(ValueError, "Target energy differs"):
            compare_measurement(model, result, changed)


if __name__ == "__main__":
    unittest.main()
