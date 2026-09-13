import sys
import unittest
import json
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
PARENT = REPO_ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from half_linac.src.apps.emit_measure.multi_screen import (
    BeamSizeSample,
    MultiScreenAcquisition,
    assess_multi_screen_observability,
    assess_multi_screen_prefixes,
    build_multi_screen_optics,
    rebuild_multi_screen_optics,
    reconstruct_multi_screen_measurement,
    score_multi_screen_optics,
)


class FakeModelBackend:
    def __init__(self, maps):
        self.maps = maps
        self.calls = []

    def get_map(self, source, target, **kwargs):
        self.calls.append((source, target, kwargs))
        return self.maps[target]


class MultiScreenOpticsTests(unittest.TestCase):
    def test_builds_reference_identity_and_downstream_projections(self):
        prf07 = np.eye(6)
        prf07[0, 1] = 5.5
        prf07[2, 3] = 5.5
        prf08 = np.eye(6)
        prf08[0, 1] = 11.0
        prf08[2, 3] = 11.0
        backend = FakeModelBackend({"S2": prf07, "S3": prf08})

        result = build_multi_screen_optics(backend, "S1", ("S1", "S2", "S3"))

        np.testing.assert_array_equal(result.transfer_matrices[0], np.eye(6))
        self.assertEqual(result.x_projections, ((1.0, 0.0), (1.0, 5.5), (1.0, 11.0)))
        self.assertEqual(result.y_projections, result.x_projections)
        self.assertEqual(
            backend.calls,
            [
                (
                    "S1",
                    "S2",
                    {"lattice_overrides": None, "seq": "exit2exit", "twiss_only": True},
                ),
                (
                    "S1",
                    "S3",
                    {"lattice_overrides": None, "seq": "exit2exit", "twiss_only": True},
                ),
            ],
        )

    def test_requires_three_unique_observation_elements(self):
        backend = FakeModelBackend({})
        with self.assertRaisesRegex(ValueError, "at least 3"):
            build_multi_screen_optics(backend, "S1", ("S1", "S2"))
        with self.assertRaisesRegex(ValueError, "unique"):
            build_multi_screen_optics(backend, "S1", ("S1", "S2", "S2"))

    def test_rebuild_reuses_unaffected_transport_matrices(self):
        s2 = np.eye(6)
        s2[0, 1] = s2[2, 3] = 1.0
        original_s3 = np.eye(6)
        original_s3[0, 1] = original_s3[2, 3] = 2.0
        changed_s3 = np.eye(6)
        changed_s3[0, 1] = 3.0
        changed_s3[2, 3] = 4.0
        baseline_backend = FakeModelBackend({"S2": s2, "S3": original_s3})
        baseline = build_multi_screen_optics(
            baseline_backend,
            "S1",
            ("S1", "S2", "S3"),
        )
        changed_backend = FakeModelBackend({"S3": changed_s3})

        result = rebuild_multi_screen_optics(
            changed_backend,
            baseline,
            ("S3",),
            lattice_overrides={"Q1": {"K1": 2.0}},
        )

        self.assertIs(result.transfer_matrices[0], baseline.transfer_matrices[0])
        self.assertIs(result.transfer_matrices[1], baseline.transfer_matrices[1])
        np.testing.assert_array_equal(result.transfer_matrices[2], changed_s3)
        self.assertEqual(len(changed_backend.calls), 1)

    def test_acquisition_tracks_progress_and_aggregates_in_screen_order(self):
        acquisition = MultiScreenAcquisition.create(("S1", "S2", "S3"), 2)
        self.assertEqual(acquisition.next_screen, "S1")

        for screen, sigma_x, sigma_y in (
            ("S2", 2.0e-3, 3.0e-3),
            ("S1", 1.0e-3, 1.5e-3),
            ("S3", 4.0e-3, 5.0e-3),
            ("S1", 1.2e-3, 1.7e-3),
            ("S2", 2.2e-3, 3.2e-3),
            ("S3", 4.2e-3, 5.2e-3),
        ):
            acquisition = acquisition.add_sample(
                BeamSizeSample(screen, sigma_x, sigma_y)
            )

        self.assertTrue(acquisition.complete)
        self.assertIsNone(acquisition.next_screen)
        data = acquisition.aggregate()
        self.assertEqual(data.observation_elements, ("S1", "S2", "S3"))
        np.testing.assert_allclose(data.rms_x_m, (1.1e-3, 2.1e-3, 4.1e-3))
        self.assertIsNotNone(data.rms_x_errors_m)
        self.assertEqual(tuple(item.sample_count for item in data.estimates), (2, 2, 2))

    def test_sample_metadata_round_trips_through_archive(self):
        from tempfile import TemporaryDirectory
        from half_linac.src.apps.emit_measure.multi_screen import (
            MultiScreenMeasurementSession,
            load_multi_screen_archive,
            save_multi_screen_archive,
        )

        matrices = []
        maps = {}
        for screen, drift in zip(("S1", "S2", "S3"), (0.0, 1.0, 2.0)):
            matrix = np.eye(6)
            matrix[0, 1] = matrix[2, 3] = drift
            matrices.append(matrix)
            if screen != "S1":
                maps[screen] = matrix
        optics = build_multi_screen_optics(FakeModelBackend(maps), "S1", ("S1", "S2", "S3"))
        session = MultiScreenMeasurementSession.create(
            machine="half",
            backend="vm",
            preset="test",
            model_line="ALL_MAIN",
            energy_mev=2200.0,
            optics=optics,
            target_samples_per_screen=1,
        ).add_sample(BeamSizeSample("S1", 1e-3, 2e-3, source="manual", quality={"roi": "full"}))
        session = session.add_sample(BeamSizeSample("S1", None, None, enabled=False, quality={"rejected": True}))
        with TemporaryDirectory() as directory:
            path = save_multi_screen_archive(f"{directory}/measurement.json", session)
            payload = json.loads(path.read_text())
            del payload["samples"][0]["enabled"]  # Legacy archives default to active.
            path.write_text(json.dumps(payload))
            loaded = load_multi_screen_archive(path)
        self.assertTrue(loaded.acquisition.samples[0].enabled)
        self.assertFalse(loaded.acquisition.samples[1].enabled)
        self.assertIsNone(loaded.acquisition.samples[1].sigma_x_m)
        self.assertEqual(loaded.machine, "half")
        self.assertEqual(loaded.acquisition.samples[0].source, "manual")
        self.assertEqual(loaded.acquisition.samples[0].quality["roi"], "full")

    def test_archive_rejects_non_integer_target_sample_count(self):
        from tempfile import TemporaryDirectory
        from half_linac.src.apps.emit_measure.multi_screen import load_multi_screen_archive

        payload = {
            "schema": "emit_multi_screen_v1",
            "created_at": "2026-01-01T00:00:00+00:00",
            "machine": "half",
            "backend": "vm",
            "model_line": "ALL_MAIN",
            "reference_element": "S1",
            "observation_elements": ["S1", "S2", "S3"],
            "transport_matrices": [np.eye(6).tolist()] * 3,
            "x_projections": [{"r11": 1.0, "r12_m": value} for value in (0, 1, 2)],
            "y_projections": [{"r33": 1.0, "r34_m": value} for value in (0, 1, 2)],
            "sampling": {"target_samples_per_screen": 1.5},
            "samples": [],
        }
        with TemporaryDirectory() as directory:
            path = Path(directory) / "measurement.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must be an integer"):
                load_multi_screen_archive(path)

    def test_excluded_samples_do_not_count_or_bias_aggregation(self):
        acquisition = MultiScreenAcquisition.create(("S1", "S2", "S3"), 1)
        for screen in acquisition.observation_elements:
            acquisition = acquisition.add_sample(BeamSizeSample(screen, 1e-3, 2e-3))
        acquisition = acquisition.add_sample(BeamSizeSample("S1", 100e-3, 200e-3, enabled=False))
        acquisition = acquisition.add_sample(BeamSizeSample("S1", None, None, enabled=False))
        self.assertEqual(acquisition.sample_counts["S1"], 1)
        self.assertEqual(acquisition.aggregate().rms_x_m, (1e-3,) * 3)
        acquisition = acquisition.set_sample_enabled(0, False)
        self.assertFalse(acquisition.complete)
        with self.assertRaisesRegex(ValueError, "S1"):
            acquisition.aggregate()
        acquisition = acquisition.set_sample_enabled(0, True)
        self.assertTrue(acquisition.complete)
        self.assertEqual(len(acquisition.samples), 5)

    def test_acquisition_rejects_incomplete_or_unknown_screen_data(self):
        acquisition = MultiScreenAcquisition.create(("S1", "S2", "S3"), 1)
        with self.assertRaisesRegex(ValueError, "not in this acquisition"):
            acquisition.add_sample(BeamSizeSample("S4", 1.0e-3, 1.0e-3))
        with self.assertRaisesRegex(ValueError, "incomplete"):
            acquisition.aggregate()

    def test_aggregated_four_screen_data_reconstructs_both_planes(self):
        observations = ("S1", "S2", "S3", "S4")
        maps = {}
        matrices = []
        for screen, drift in zip(observations, (0.0, 1.0, 2.0, 4.0)):
            matrix = np.eye(6)
            matrix[0, 1] = drift
            matrix[2, 3] = drift
            matrices.append(matrix)
            if screen != "S1":
                maps[screen] = matrix
        optics = build_multi_screen_optics(FakeModelBackend(maps), "S1", observations)
        x_moments = np.array((4.0e-6, -0.3e-6, 0.5e-6))
        y_moments = np.array((3.0e-6, 0.2e-6, 0.4e-6))
        sigma_x = np.sqrt(optics.x_measurement_matrix @ x_moments)
        sigma_y = np.sqrt(optics.y_measurement_matrix @ y_moments)

        acquisition = MultiScreenAcquisition.create(observations, 3)
        for index, screen in enumerate(observations):
            for scale in (0.99, 1.0, 1.01):
                acquisition = acquisition.add_sample(
                    BeamSizeSample(
                        screen,
                        float(sigma_x[index] * scale),
                        float(sigma_y[index] * scale),
                    )
                )

        result = reconstruct_multi_screen_measurement(optics, acquisition.aggregate())

        self.assertTrue(result.valid)
        np.testing.assert_allclose(result.x.moments, x_moments, rtol=1e-12, atol=1e-18)
        np.testing.assert_allclose(result.y.moments, y_moments, rtol=1e-12, atol=1e-18)

    def test_scores_candidate_optics_and_enforces_screen_size_bounds(self):
        observations = ("S1", "S2", "S3", "S4")
        maps = {}
        for screen, drift in zip(observations[1:], (1.0, 2.0, 4.0)):
            matrix = np.eye(6)
            matrix[0, 1] = drift
            matrix[2, 3] = drift
            maps[screen] = matrix
        optics = build_multi_screen_optics(FakeModelBackend(maps), "S1", observations)
        x_moments = np.array((4.0e-6, -0.3e-6, 0.5e-6))
        y_moments = np.array((3.0e-6, 0.2e-6, 0.4e-6))

        score = score_multi_screen_optics(
            optics,
            x_moments,
            y_moments,
            0.01,
            min_rms_size_m=0.1e-3,
            max_rms_size_m=10e-3,
        )
        rejected = score_multi_screen_optics(
            optics,
            x_moments,
            y_moments,
            0.01,
            max_rms_size_m=1e-3,
        )

        self.assertTrue(score.feasible)
        self.assertTrue(np.isfinite(score.objective))
        self.assertFalse(rejected.feasible)
        self.assertTrue(np.isinf(rejected.objective))
        self.assertTrue(rejected.rejection_reasons)

    def test_observability_classifies_each_ordered_screen_prefix(self):
        observations = ("S1", "S2", "S3", "S4")
        maps = {}
        for screen, drift in zip(observations[1:], (1.0, 2.0, 4.0)):
            matrix = np.eye(6)
            matrix[0, 1] = drift
            matrix[2, 3] = drift
            maps[screen] = matrix
        optics = build_multi_screen_optics(FakeModelBackend(maps), "S1", observations)

        full = assess_multi_screen_observability(optics)
        prefixes = assess_multi_screen_prefixes(
            optics,
            good_condition_number=12.0,
            marginal_condition_number=12.5,
        )

        self.assertEqual(full.status, "good")
        self.assertEqual([item.status for item in prefixes], ["marginal", "good"])
        self.assertEqual([item.x.measurement_count for item in prefixes], [3, 4])
        self.assertEqual([item.y.rank for item in prefixes], [3, 3])

    def test_observability_rejects_invalid_thresholds(self):
        optics = build_multi_screen_optics(
            FakeModelBackend({"S2": np.eye(6), "S3": np.eye(6)}),
            "S1",
            ("S1", "S2", "S3"),
        )
        with self.assertRaisesRegex(ValueError, "greater than 1"):
            assess_multi_screen_observability(optics, good_condition_number=1.0)
        with self.assertRaisesRegex(ValueError, "above the good"):
            assess_multi_screen_observability(
                optics,
                good_condition_number=100.0,
                marginal_condition_number=100.0,
            )


if __name__ == "__main__":
    unittest.main()
