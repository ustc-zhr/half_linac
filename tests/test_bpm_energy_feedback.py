from __future__ import annotations

import csv
import json
import math
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
PARENT = REPO_ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from half_linac.src.apps.energy_feedback.measurement import (
    BpmTransportEnergyProvider,
)
from half_linac.src.apps.energy_feedback.csv_logger import EnergyFeedbackCsvLogger
from half_linac.src.apps.energy_feedback.models import FeedbackDecision, PVReading
from half_linac.src.apps.energy_feedback.profile_runtime import (
    activate_measurement_source,
    build_measurement_provider,
    load_runtime_config,
)
from half_linac.src.shared.energy_measurement import (
    BpmEnergySampler,
    BpmTransportModel,
    build_bpm_transport_model,
    discover_bpm_transport_segment,
)
from half_linac.src.shared.machine_profile import (
    MachineProfileError,
    load_app_context,
    validate_energy_feedback_workflow,
)


def _matrix() -> np.ndarray:
    return np.eye(6, dtype=float)


def _reading(
    key: str,
    value: float | None,
    *,
    timestamp: float | None = 100.0,
    connected: bool = True,
) -> PVReading:
    return PVReading(
        key=key,
        pv_name=f"TEST:{key}",
        value=value,
        connected=connected,
        source_timestamp=timestamp,
        error="" if connected else "offline",
    )


class _Client:
    def __init__(self, readings):
        self.readings = dict(readings)
        self.requests: list[tuple[str, ...]] = []

    def read_many(self, keys):
        request = tuple(keys)
        self.requests.append(request)
        return {key: self.readings[key] for key in request}


def _hls_model(*, offsets=(0.0, 0.0, 0.0)) -> BpmTransportModel:
    m1 = _matrix()
    m1[0, 0] = 0.7444
    m1[0, 1] = 1.7828
    m1[1, 0] = -0.1496
    m1[1, 1] = 0.985
    m2 = _matrix()
    m2[0, 0] = -0.1941
    m2[0, 1] = 0.3253
    m2[0, 5] = 0.4909
    return BpmTransportModel.from_matrices(
        ("BPM21", "BPM22", "BPM23"), offsets, m1, m2
    )


def _sampler_source() -> dict[str, object]:
    return {
        "bpm_ids": ("BPM21", "BPM22", "BPM23"),
        "bpm_keys": {
            "BPM21": "bpm21",
            "BPM22": "bpm22",
            "BPM23": "bpm23",
        },
        "bpm_scale_to_mm": 0.001,
        "reference_key": "e0",
        "k1_keys": {"QT05": "qt05", "QT06": "qt06"},
        "quadrupoles": ("QT05", "QT06"),
        "median_window": 3,
        "valid_range_mev": (0.0, 2450.0),
        "runtime": {
            "stale_timeout_s": 3.0,
            "max_bpm_timestamp_skew_s": 0.2,
        },
        "model": {
            "k1_abs_tolerance_per_m2": 1e-4,
            "k1_rel_tolerance": 1e-4,
            "zero_threshold_mm": 1e-6,
        },
    }


def _valid_readings() -> dict[str, PVReading]:
    return {
        "bpm21": _reading("bpm21", 500.0, timestamp=100.00),
        "bpm22": _reading("bpm22", 2200.0, timestamp=100.05),
        "bpm23": _reading("bpm23", -4000.0, timestamp=100.10),
        "e0": _reading("e0", 800.0, timestamp=None),
        "qt05": _reading("qt05", 0.1, timestamp=None),
        "qt06": _reading("qt06", -0.2, timestamp=None),
    }


class BpmTransportFormulaTests(unittest.TestCase):
    def test_hls_regression_offset_mm_conversion_and_absolute_energy(self):
        model = _hls_model(offsets=(0.2, 1.932, -4.541))
        delta, delta_energy, energy = model.energy_mev((0.5, 2.2, -4.0), 800.0)
        self.assertAlmostEqual(delta, 0.0012214055435573164, places=15)
        self.assertAlmostEqual(delta_energy, 0.9771244348458531, places=13)
        self.assertAlmostEqual(energy, 800.9771244348459, places=12)

    def test_coefficients_use_full_m1_determinant(self):
        m1 = _matrix()
        m1[0, 0], m1[0, 1] = 2.0, 3.0
        m1[1, 0], m1[1, 1] = 5.0, 7.0
        m2 = _matrix()
        m2[0, 0], m2[0, 1], m2[0, 5] = 11.0, 13.0, 17.0
        model = BpmTransportModel.from_matrices(
            ("B1", "B2", "B3"), (0.0, 0.0, 0.0), m1, m2
        )
        determinant = 2.0 * 7.0 - 3.0 * 5.0
        self.assertAlmostEqual(
            model.coefficients_per_m[0], 13.0 * determinant / (3.0 * 17.0)
        )

    def test_rejects_nonfinite_and_degenerate_matrices(self):
        m1 = _matrix()
        m2 = _matrix()
        m1[0, 1] = 0.0
        m2[0, 5] = 1.0
        with self.assertRaisesRegex(ValueError, "R12"):
            BpmTransportModel.from_matrices(
                ("B1", "B2", "B3"), (0.0, 0.0, 0.0), m1, m2
            )
        m1[0, 1] = 1.0
        m2[0, 5] = math.nan
        with self.assertRaisesRegex(ValueError, "non-finite"):
            BpmTransportModel.from_matrices(
                ("B1", "B2", "B3"), (0.0, 0.0, 0.0), m1, m2
            )


class BpmTransportModelRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.context = load_app_context(
            "energy_feedback", machine_id="half", control_backend="real"
        )

    def test_half_runtime_infers_qt05_through_qt08_and_k1_total(self):
        config = load_runtime_config(self.context)
        self.assertEqual(
            tuple(config["measurement_sources"]),
            ("energy_spectrum", "bpm21_22_23"),
        )
        source = config["measurement_sources"]["bpm21_22_23"]
        self.assertEqual(source["model_line"], "ALL_MAIN")
        self.assertEqual(source["quadrupoles"], ("QT05", "QT06", "QT07", "QT08"))
        self.assertEqual(source["bpm_scale_to_mm"], 0.001)
        self.assertEqual(
            [config["signals"][key]["channel"] for key in source["k1_keys"].values()],
            ["K1_total"] * 4,
        )
        self.assertEqual(
            config["pvs"][source["reference_key"]],
            "IN:LA:ENG",
        )
        activate_measurement_source(config, "bpm21_22_23")
        self.assertEqual(config["measurement_kind"], "bpm_transport")
        self.assertEqual(config["measurement_label"], "BPM21/22/23")

    def test_discovery_rejects_wrong_bpm_order(self):
        path = ("BPM21", "QT05", "BPM23", "QT06", "BPM22")

        class Backend:
            def get_measurement_model_line(self, _first, _last):
                return SimpleNamespace(name="TEST")

            def get_line_elements(self, _first, _last):
                return tuple({"NAME": name} for name in path)

        with self.assertRaisesRegex(MachineProfileError, "order"):
            discover_bpm_transport_segment(
                self.context,
                ("BPM21", "BPM22", "BPM23"),
                backend=Backend(),
            )

    def test_model_build_uses_two_exit_maps_and_k1_overrides(self):
        calls = []
        m1 = _matrix()
        m1[0, 1] = 2.0
        m2 = _matrix()
        m2[0, 5] = 3.0

        class Backend:
            def get_map(self, first, last, **kwargs):
                calls.append((first, last, kwargs))
                return m1 if last == "BPM22" else m2

        factory_calls = []

        def factory(context, **kwargs):
            factory_calls.append((context, kwargs))
            return Backend()

        source = {
            "model_line": "TEST",
            "bpm_ids": ("BPM21", "BPM22", "BPM23"),
            "quadrupoles": ("QT05", "QT06"),
            "offsets_mm": (0.0, 0.0, 0.0),
            "model": {"min_abs_r12_m": 1e-6, "min_abs_r16_m": 1e-6},
        }
        model = build_bpm_transport_model(
            self.context,
            source,
            800.0,
            {"QT05": 0.1, "QT06": -0.2},
            backend_factory=factory,
        )
        self.assertEqual(factory_calls[0][1], {"energy_mev": 800.0, "line_name": "TEST"})
        self.assertEqual([(first, last) for first, last, _ in calls], [
            ("BPM21", "BPM22"),
            ("BPM22", "BPM23"),
        ])
        for _first, _last, kwargs in calls:
            self.assertEqual(kwargs["seq"], "exit2exit")
            self.assertEqual(
                kwargs["lattice_overrides"],
                {"QT05": {"K1": 0.1}, "QT06": {"K1": -0.2}},
            )
        self.assertEqual(model.coordinate_definition, "exit2exit")

    def test_provider_startup_metadata_captures_pvs_k1_matrices_and_model_hashes(self):
        config = load_runtime_config(self.context)
        activate_measurement_source(config, "bpm21_22_23")
        source = config["measurement_sources"]["bpm21_22_23"]
        readings = {
            source["reference_key"]: _reading(
                source["reference_key"], 800.0, timestamp=None
            ),
            **{
                key: _reading(key, 0.1 * (index + 1), timestamp=None)
                for index, key in enumerate(source["k1_keys"].values())
            },
        }
        client = _Client(readings)
        with patch(
            "half_linac.src.apps.energy_feedback.profile_runtime.build_bpm_transport_model",
            return_value=_hls_model(),
        ) as build_model:
            provider = build_measurement_provider(
                config, client, context=self.context
            )
        self.assertIsInstance(provider, BpmTransportEnergyProvider)
        build_model.assert_called_once()
        metadata = provider.runtime_metadata
        self.assertEqual(metadata["source_id"], "bpm21_22_23")
        self.assertEqual(metadata["quadrupoles"], ["QT05", "QT06", "QT07", "QT08"])
        self.assertEqual(metadata["transport_model"]["coordinate_definition"], "exit2exit")
        self.assertEqual(
            set(metadata["resolved_signals"]), set(source["signal_keys"])
        )
        input_files = metadata["model_backend"]["input_files"]
        self.assertEqual(len(input_files["source_lattice"]["sha256"]), 64)
        self.assertEqual(len(input_files["optics_ini_ele"]["sha256"]), 64)


class BpmEnergySamplerTests(unittest.TestCase):
    def _sampler(self, readings=None):
        values = _valid_readings() if readings is None else readings
        client = _Client(values)
        sampler = BpmEnergySampler(
            client,
            _sampler_source(),
            _hls_model(),
            {"QT05": 0.1, "QT06": -0.2},
        )
        return client, sampler

    def test_valid_sample_uses_latest_bpm_timestamp_and_excludes_e0_from_sync(self):
        _client, sampler = self._sampler()
        result = sampler.sample(101.0)
        self.assertTrue(result.valid)
        self.assertEqual(result.source_timestamp, 100.10)
        self.assertAlmostEqual(result.timestamp_skew_s, 0.10)
        self.assertEqual(result.reference_energy_mev, 800.0)
        self.assertEqual(result.bpm_values_mm["BPM21"], 0.5)
        self.assertIsNotNone(result.energy_raw_mev)
        self.assertEqual(result.energy_filtered_mev, result.energy_raw_mev)

    def test_quality_gates_clear_filter_history(self):
        mutations = (
            ("bpm21", _reading("bpm21", 500.0, connected=False), "disconnected"),
            ("bpm21", _reading("bpm21", math.nan), "non-finite"),
            ("bpm21", _reading("bpm21", 500.0, timestamp=None), "no source timestamp"),
            ("bpm21", _reading("bpm21", 500.0, timestamp=90.0), "stale"),
            ("bpm23", _reading("bpm23", -4000.0, timestamp=100.3), "skew"),
            ("e0", _reading("e0", 2501.0, timestamp=None), "outside"),
        )
        for key, value, reason in mutations:
            readings = _valid_readings()
            readings[key] = value
            with self.subTest(reason=reason):
                _client, sampler = self._sampler(readings)
                result = sampler.sample(101.0)
                self.assertEqual(result.quality_state, "HOLD")
                self.assertIn(reason, result.quality_reason)
                self.assertIsNone(result.energy_filtered_mev)

        readings = _valid_readings()
        for key in ("bpm21", "bpm22", "bpm23"):
            readings[key] = _reading(key, 0.0)
        _client, sampler = self._sampler(readings)
        self.assertIn("near zero", sampler.sample(101.0).quality_reason)

    def test_invalid_sample_resets_median_and_k1_drift_is_latched_until_restart(self):
        client, sampler = self._sampler()
        first = sampler.sample(101.0)
        self.assertTrue(first.valid)
        client.readings["bpm21"] = _reading("bpm21", 500.0, timestamp=101.0)
        client.readings["bpm22"] = _reading("bpm22", 2200.0, timestamp=101.0)
        client.readings["bpm23"] = _reading("bpm23", -3000.0, timestamp=101.0)
        second = sampler.sample(101.5)
        self.assertNotEqual(second.energy_filtered_mev, second.energy_raw_mev)
        client.readings["bpm21"] = _reading("bpm21", None, connected=False)
        self.assertEqual(sampler.sample(102.0).quality_state, "HOLD")
        client.readings["bpm21"] = _reading("bpm21", 700.0, timestamp=102.0)
        client.readings["bpm22"] = _reading("bpm22", 2200.0, timestamp=102.0)
        client.readings["bpm23"] = _reading("bpm23", -3000.0, timestamp=102.0)
        recovered = sampler.sample(102.1)
        self.assertEqual(recovered.energy_filtered_mev, recovered.energy_raw_mev)

        client.readings["qt05"] = _reading("qt05", 0.101, timestamp=None)
        drifted = sampler.sample(102.2)
        self.assertIn("restart acquisition", drifted.quality_reason)
        client.readings["qt05"] = _reading("qt05", 0.1, timestamp=None)
        self.assertEqual(sampler.sample(102.3).quality_state, "HOLD")
        restarted = BpmEnergySampler(
            client,
            _sampler_source(),
            _hls_model(),
            {"QT05": 0.1, "QT06": -0.2},
        )
        self.assertTrue(restarted.sample(102.4).valid)

    def test_feedback_adapter_exposes_raw_bpm_diagnostics_and_hold_reason(self):
        client, sampler = self._sampler()
        provider = BpmTransportEnergyProvider(
            sampler, runtime_metadata={"model_line": "TEST"}
        )
        result = provider.sample(101.0)
        self.assertTrue(result.quality.valid)
        self.assertIn("bpm_transport.energy_raw_mev", result.diagnostics)
        self.assertEqual(
            result.diagnostic_source_timestamps["bpm_transport.BPM23.x_mm"],
            100.10,
        )
        client.readings["bpm22"] = _reading("bpm22", None, connected=False)
        held = provider.sample(101.1)
        self.assertEqual(held.quality.state, "HOLD")
        self.assertIn("BPM22 disconnected", held.quality.reason)


class BpmEnergyFeedbackConfigTests(unittest.TestCase):
    def test_invalid_bpm_alternatives_are_rejected(self):
        context = load_app_context(
            "energy_feedback", machine_id="half", control_backend="real"
        )
        workflow = context.profile.workflows["energy_feedback"]
        changes = []

        duplicate = deepcopy(workflow)
        duplicate["measurement_alternatives"][0]["bpm_elements"][1] = "BPM21"
        changes.append(duplicate)

        non_bpm = deepcopy(workflow)
        non_bpm["measurement_alternatives"][0]["bpm_elements"][1] = "QT05"
        non_bpm["measurement_alternatives"][0]["offset_mm"] = {
            "BPM21": 0.0,
            "QT05": 0.0,
            "BPM23": 0.0,
        }
        changes.append(non_bpm)

        nonfinite = deepcopy(workflow)
        nonfinite["measurement_alternatives"][0]["offset_mm"]["BPM21"] = math.nan
        changes.append(nonfinite)

        even_filter = deepcopy(workflow)
        even_filter["measurement_alternatives"][0]["median_window"] = 2
        changes.append(even_filter)

        wrong_channel = deepcopy(workflow)
        wrong_channel["measurement_alternatives"][0]["plane"] = "y"
        changes.append(wrong_channel)

        for candidate in changes:
            with self.subTest(candidate=candidate), self.assertRaises(MachineProfileError):
                validate_energy_feedback_workflow(context.profile, candidate)

    def test_no_standalone_monitor_launcher_or_app_remains(self):
        launcher = (REPO_ROOT / "src/apps/launcher/main.py").read_text(encoding="utf-8")
        self.assertNotIn("bpm_energy_monitor", launcher)
        self.assertFalse((REPO_ROOT / "src/apps/bpm_energy_monitor/main.py").exists())
        self.assertTrue((REPO_ROOT / "src/apps/energy_feedback/main.py").is_file())

    def test_bpm_samples_and_model_metadata_are_logged_in_feedback_run(self):
        context = load_app_context(
            "energy_feedback", machine_id="half", control_backend="real"
        )
        config = load_runtime_config(context)
        activate_measurement_source(config, "bpm21_22_23")
        client = _Client(_valid_readings())
        provider = BpmTransportEnergyProvider(
            BpmEnergySampler(
                client,
                _sampler_source(),
                _hls_model(),
                {"QT05": 0.1, "QT06": -0.2},
            ),
            runtime_metadata={
                "model_line": "ALL_MAIN",
                "transport_model": _hls_model().as_metadata(),
                "startup_k1_values_per_m2": {"QT05": 0.1, "QT06": -0.2},
            },
        )
        sample = provider.sample(101.0)
        decision = FeedbackDecision("HOLD", "monitoring", None)
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "half_linac.src.apps.energy_feedback.csv_logger.APP_DIR",
            Path(temp_dir),
        ):
            logger = EnergyFeedbackCsvLogger(context, config)
            logger.set_measurement_runtime_metadata(provider.runtime_metadata)
            logger.append(sample, decision)
            with logger.csv_path.open(newline="", encoding="utf-8") as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual(row["measurement_source"], "bpm21_22_23")
            self.assertEqual(row["quality"], "OK")
            diagnostics = json.loads(row["diagnostics_json"])
            timestamps = json.loads(row["diagnostic_source_timestamps_json"])
            self.assertIn("bpm_transport.energy_raw_mev", diagnostics)
            self.assertEqual(timestamps["bpm_transport.BPM23.x_mm"], 100.1)
            run_dir = logger.run_dir
            logger.close()
            metadata = json.loads(
                (run_dir / "metadata.json").read_text(encoding="utf-8")
            )
        self.assertEqual(metadata["measurement_provider"]["id"], "bpm21_22_23")
        self.assertEqual(metadata["measurement_runtime"]["model_line"], "ALL_MAIN")
        self.assertIn("M1", metadata["measurement_runtime"]["transport_model"])
        self.assertEqual(metadata["profile_snapshot"]["machine"]["id"], "half")


if __name__ == "__main__":
    unittest.main()
