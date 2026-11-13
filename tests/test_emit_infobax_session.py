"""State-machine tests for the recoverable InfoBAX VM session."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch

import numpy as np

from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

from half_linac.src.apps.emit_measure.infobax_session import (  # noqa: E402
    InfoBAXSession,
    InfoBAXSessionConfig,
    PointMeasurement,
    SessionCompatibilityError,
    SessionDriftError,
    SessionRestoreFailure,
    SessionRunFailure,
)
from half_linac.src.optimization.infobax import (  # noqa: E402
    FinitePoolSpec,
    InfoBAXConfig,
    InfoBAXCore,
    PointObservation,
)
from half_linac.src.optimization.infobax.types import (  # noqa: E402
    EIGDiagnostic,
    QuerySuggestion,
)


def make_pool(candidate_count=3):
    return FinitePoolSpec(
        candidate_ids=tuple(f"candidate-{index}" for index in range(candidate_count)),
        candidate_coordinates=np.linspace(0.0, 1.0, candidate_count)[:, None],
        scan_values=np.asarray((-1.0, 0.0, 1.0)),
        scan_coordinates=np.asarray((0.0, 0.5, 1.0)),
        design_matrix=np.asarray(
            ((1.0, -2.0, 1.0), (1.0, 0.0, 0.0), (1.0, 2.0, 1.0))
        ),
        energy_mev=225.0,
        observation_noise_std_mm2=0.05,
    )


def make_core(pool=None):
    return InfoBAXCore(
        pool or make_pool(),
        InfoBAXConfig(
            seed=3,
            outer_fantasies=4,
            inner_samples=16,
            batch_count=2,
            direct_prior_samples=16,
            summary_samples=16,
        ),
    )


def positive_select(_estimator, candidates):
    diagnostics = tuple(
        EIGDiagnostic(
            query_index=index,
            raw_eig_nats=0.2 if offset == 0 else 0.1,
            eig_standard_error_nats=0.01,
            prior_entropy_nats=0.5,
            posterior_entropy_mean_nats=0.3,
            direct_prior_entropy_nats=0.5,
            prior_probability_l1=0.0,
            negative_estimate=False,
        )
        for offset, index in enumerate(candidates)
    )
    return diagnostics[0], diagnostics


class FakeMachine:
    def __init__(self, fingerprint="machine-a"):
        self.fingerprint = fingerprint
        self.state = {"WORK:K1": 0.25, "SCAN:K1": 0.0}
        self.applied = []
        self.measured = []
        self.restore_count = 0
        self.fail_measure = False
        self.fail_restore = False
        self.drifted = False

    def snapshot(self):
        return dict(self.state)

    def verify_snapshot(self, snapshot):
        if self.drifted or self.state != dict(snapshot):
            raise RuntimeError("machine drift")

    def apply_query(self, candidate_index, scan_value):
        self.applied.append((candidate_index, scan_value))
        self.state["WORK:K1"] = float(candidate_index)
        self.state["SCAN:K1"] = float(scan_value)

    def measure_point(self, query_index, expected_noise_std_mm2):
        self.measured.append(query_index)
        if self.fail_measure:
            raise RuntimeError("camera failed")
        return PointMeasurement(
            variance_mm2=1.0 + 0.02 * query_index,
            noise_std_mm2=expected_noise_std_mm2,
            metadata={"source": "fake", "query": query_index},
        )

    def restore(self, snapshot):
        self.restore_count += 1
        for key, value in snapshot.items():
            self.state[key] = value
        if self.fail_restore:
            raise RuntimeError("restore write failed")


class SequenceClock:
    def __init__(self, values):
        self.values = iter(values)
        self.last = 0.0

    def __call__(self):
        self.last = next(self.values, self.last)
        return self.last


class InfoBAXSessionTests(unittest.TestCase):
    def test_complete_initial_and_active_query_loop(self):
        core = make_core()
        machine = FakeMachine()
        with TemporaryDirectory() as directory, patch(
            "half_linac.src.optimization.infobax.core.InfoBAXEstimator.select",
            new=positive_select,
        ):
            session = InfoBAXSession(
                core,
                machine,
                directory,
                InfoBAXSessionConfig(max_observations=7),
            )
            recommendation = session.run()

            self.assertEqual(session.status, "completed")
            self.assertIsNotNone(recommendation)
            self.assertEqual(len(core.observations), 7)
            self.assertEqual(len(machine.applied), 7)
            self.assertEqual(machine.measured, [row.query_index for row in core.observations])
            self.assertTrue(all(row["state"] == "observed" for row in session.records))
            self.assertEqual(machine.state, {"WORK:K1": 0.25, "SCAN:K1": 0.0})
            payload = json.loads(session.checkpoint_path.read_text(encoding="utf-8"))
            self.assertIsNotNone(payload["recommendation"])
            self.assertEqual(len(payload["core_checkpoint"]["observations"]), 7)

    def test_each_record_is_one_candidate_scan_query(self):
        core = make_core()
        machine = FakeMachine()
        with TemporaryDirectory() as directory:
            session = InfoBAXSession(
                core,
                machine,
                directory,
                InfoBAXSessionConfig(max_observations=6),
            )
            session.run()
            for record in session.records:
                expected = (
                    record["candidate_index"] * core.pool.scan_count
                    + record["scan_index"]
                )
                self.assertEqual(record["query_index"], expected)

    def test_negative_eig_blocks_machine_execution(self):
        core = make_core()
        for query_index in core.initial_design:
            core.observe(PointObservation(query_index, 1.0, 0.05))
        suggestion = QuerySuggestion(
            query_index=next(iter(core.available_indices)),
            candidate_index=0,
            candidate_id="candidate-0",
            scan_index=0,
            scan_value=-1.0,
            phase="infobax",
            status="degraded_negative_eig",
            raw_eig_nats=-0.01,
            eig_standard_error_nats=0.02,
            negative_eig_fraction=1.0,
            evaluated_query_count=len(core.available_indices),
        )
        machine = FakeMachine()
        with TemporaryDirectory() as directory, patch.object(
            core, "propose", return_value=suggestion
        ):
            session = InfoBAXSession(
                core,
                machine,
                directory,
                InfoBAXSessionConfig(max_observations=7),
            )
            self.assertIsNone(session.run())
            self.assertEqual(session.status, "degraded_negative_eig")
            self.assertEqual(machine.applied, [])
            self.assertEqual(machine.measured, [])

    def test_pause_restores_and_resume_completes(self):
        core = make_core()
        machine = FakeMachine()
        holder = {}

        def progress(payload):
            record = payload.get("record")
            if record and record.get("state") == "observed":
                holder["session"].request_pause()

        with TemporaryDirectory() as directory:
            session = InfoBAXSession(
                core,
                machine,
                directory,
                InfoBAXSessionConfig(max_observations=6),
                progress=progress,
            )
            holder["session"] = session
            self.assertIsNone(session.run())
            self.assertEqual(session.status, "paused")
            self.assertEqual(len(core.observations), 1)
            self.assertEqual(machine.state, {"WORK:K1": 0.25, "SCAN:K1": 0.0})

            resumed = InfoBAXSession.from_run_dir(
                core.pool, machine, directory
            )
            result = resumed.run()
            self.assertEqual(resumed.status, "completed")
            self.assertIsNotNone(result)
            self.assertEqual(len(resumed.core.observations), 6)

    def test_resume_rejects_machine_drift_without_overwriting_it(self):
        core = make_core()
        machine = FakeMachine()
        cancelled = Event()
        cancelled.set()
        with TemporaryDirectory() as directory:
            InfoBAXSession(
                core,
                machine,
                directory,
                InfoBAXSessionConfig(max_observations=6),
                cancelled=cancelled,
            ).run()
            resumed = InfoBAXSession.from_run_dir(core.pool, machine, directory)
            machine.state["WORK:K1"] = 99.0
            with self.assertRaises(SessionDriftError):
                resumed.run()
            self.assertEqual(machine.state["WORK:K1"], 99.0)
            self.assertEqual(resumed.status, "drift_detected")

    def test_pending_record_is_marked_interrupted_on_restore(self):
        core = make_core()
        machine = FakeMachine()
        cancelled = Event()
        cancelled.set()
        with TemporaryDirectory() as directory:
            session = InfoBAXSession(
                core,
                machine,
                directory,
                InfoBAXSessionConfig(max_observations=6),
                cancelled=cancelled,
            )
            session.run()
            payload = json.loads(session.checkpoint_path.read_text(encoding="utf-8"))
            payload["records"].append(
                {
                    "sequence": 0,
                    "query_index": core.initial_design[0],
                    "state": "pending",
                    "error": None,
                }
            )
            session.checkpoint_path.write_text(json.dumps(payload), encoding="utf-8")
            resumed = InfoBAXSession.from_run_dir(core.pool, machine, directory)
            self.assertEqual(resumed.records[-1]["state"], "interrupted")

    def test_pool_and_machine_fingerprint_mismatch_rejected(self):
        core = make_core()
        machine = FakeMachine()
        cancelled = Event()
        cancelled.set()
        with TemporaryDirectory() as directory:
            InfoBAXSession(
                core,
                machine,
                directory,
                InfoBAXSessionConfig(max_observations=6),
                cancelled=cancelled,
            ).run()
            with self.assertRaises(SessionCompatibilityError):
                InfoBAXSession.from_run_dir(
                    make_pool(candidate_count=4), machine, directory
                )
            with self.assertRaises(SessionCompatibilityError):
                InfoBAXSession.from_run_dir(
                    core.pool, FakeMachine("machine-b"), directory
                )

    def test_time_budget_stops_before_first_query(self):
        core = make_core()
        machine = FakeMachine()
        with TemporaryDirectory() as directory:
            session = InfoBAXSession(
                core,
                machine,
                directory,
                InfoBAXSessionConfig(max_observations=6, max_minutes=1.0),
                clock=SequenceClock((0.0, 61.0, 61.0)),
            )
            session.run()
            self.assertEqual(session.status, "time_budget_exhausted")
            self.assertEqual(machine.applied, [])
            self.assertEqual(machine.restore_count, 1)

    def test_new_session_rejects_mismatched_candidate_ids(self):
        core = make_core()
        machine = FakeMachine()
        machine.candidate_ids = ("wrong",)
        with TemporaryDirectory() as directory, self.assertRaisesRegex(
            ValueError, "candidate IDs"
        ):
            InfoBAXSession(
                core,
                machine,
                directory,
                InfoBAXSessionConfig(max_observations=6),
            )

    def test_measurement_failure_restores_and_is_recorded(self):
        core = make_core()
        machine = FakeMachine()
        machine.fail_measure = True
        with TemporaryDirectory() as directory:
            session = InfoBAXSession(
                core,
                machine,
                directory,
                InfoBAXSessionConfig(max_observations=6),
            )
            with self.assertRaises(SessionRunFailure):
                session.run()
            self.assertEqual(session.status, "failed")
            self.assertEqual(session.records[-1]["state"], "failed")
            self.assertEqual(session.restoration_status, "succeeded")
            self.assertEqual(machine.state, {"WORK:K1": 0.25, "SCAN:K1": 0.0})

    def test_restore_failure_overrides_measurement_failure(self):
        core = make_core()
        machine = FakeMachine()
        machine.fail_measure = True
        machine.fail_restore = True
        with TemporaryDirectory() as directory:
            session = InfoBAXSession(
                core,
                machine,
                directory,
                InfoBAXSessionConfig(max_observations=6),
            )
            with self.assertRaises(SessionRestoreFailure):
                session.run()
            self.assertEqual(session.status, "restore_failed")
            self.assertEqual(session.restoration_status, "failed")

    def test_pool_exhaustion_is_explicit_and_recommendation_is_saved(self):
        pool = make_pool(candidate_count=2)
        core = make_core(pool)
        machine = FakeMachine()
        with TemporaryDirectory() as directory:
            session = InfoBAXSession(
                core,
                machine,
                directory,
                InfoBAXSessionConfig(max_observations=7),
            )
            result = session.run()
            self.assertEqual(session.status, "pool_exhausted")
            self.assertIsNotNone(result)
            payload = json.loads(session.checkpoint_path.read_text(encoding="utf-8"))
            self.assertIsNotNone(payload["recommendation"])


if __name__ == "__main__":
    unittest.main()
