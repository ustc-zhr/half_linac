from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PARENT = REPO_ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from half_linac.src.apps.energy_spectrum.profile_runtime import (
    resolve_energy_spectrum_runtime_paths,
)
from half_linac.src.apps.energy_spectrum.stations import (
    LEGACY_STATION_ID,
    resolve_energy_spectrum_stations,
)
from half_linac.src.shared.elegant_backend import ElegantParser
from half_linac.src.shared.machine_profile import (
    get_workflow,
    load_app_context,
    load_profile,
    resolve_channel,
    workflow_writes_allowed,
)


class EnergySpectrumStationTests(unittest.TestCase):
    def test_legacy_workflow_remains_a_single_station(self):
        workflow = {"flag_element": "PRFESA", "bend_element": "A3"}

        default_station, stations = resolve_energy_spectrum_stations(workflow)

        self.assertEqual(default_station, LEGACY_STATION_ID)
        self.assertEqual(stations[LEGACY_STATION_ID], workflow)

    def test_half_exposes_eny_and_prf02_station_configs(self):
        profile = load_profile("half")
        workflow = get_workflow(profile, "energy_spectrum")

        default_station, stations = resolve_energy_spectrum_stations(workflow)

        self.assertEqual(default_station, "eny")
        self.assertEqual(set(stations), {"eny", "prf02"})
        self.assertNotIn("flag_element", workflow)
        self.assertNotIn("bend_element", workflow)
        self.assertEqual(stations["eny"]["flag_element"], "ENY")
        self.assertEqual(stations["eny"]["flag_exposure_channel"], "exposure_time")
        self.assertEqual(stations["eny"]["bend_element"], "BENY")
        self.assertEqual(
            stations["eny"]["model_lines"],
            {"dispersion": "ESAlocal", "twiss": "ALL_ESA"},
        )
        self.assertEqual(stations["eny"]["energy_element"], "LINAC_ENERGY")
        self.assertEqual(stations["eny"]["energy_control_backends"], ["real"])
        self.assertNotIn("auto_tune_control_backends", stations["eny"])
        self.assertNotIn("auto_tune_actuator", stations["eny"])
        self.assertEqual(stations["eny"]["x_reference_mm"], 0)
        eny_scan = stations["eny"]["auto_tune"]["scan"]
        self.assertEqual(eny_scan["low"], 0)
        self.assertEqual(eny_scan["high"], 2450)
        self.assertEqual(eny_scan["unit"], "MeV")
        self.assertEqual(eny_scan["mode"], "absolute")
        self.assertEqual(eny_scan["coarse_steps"], 16)
        self.assertEqual(eny_scan["fine_steps"], 31)
        self.assertEqual(eny_scan["settle_time_s"], 1.0)
        self.assertEqual(stations["prf02"]["flag_element"], "PRF02")
        self.assertEqual(stations["prf02"]["flag_exposure_channel"], "exposure_time")
        self.assertEqual(stations["prf02"]["bend_element"], "BL01A")
        self.assertNotIn("twiss_target_element", stations["prf02"])
        self.assertEqual(stations["prf02"]["esa_quads"], ["QL01", "QL02"])
        self.assertEqual(stations["prf02"]["energy_element"], "PREINJECTOR_ENERGY")
        self.assertEqual(stations["prf02"]["energy_set_channel"], "setpoint")
        self.assertEqual(stations["prf02"]["energy_reference_channel"], "setpoint")
        self.assertNotIn("energy_set_pv", stations["prf02"])
        self.assertNotIn("energy_reference_pv", stations["prf02"])
        self.assertNotIn("auto_tune_actuator", stations["prf02"])
        self.assertEqual(stations["prf02"]["x_reference_mm"], 0)
        self.assertEqual(stations["prf02"]["energy_control_backends"], ["real"])
        self.assertNotIn("auto_tune_control_backends", stations["prf02"])
        self.assertEqual(stations["prf02"]["auto_tune"]["scan"]["low"], 0)
        self.assertEqual(stations["prf02"]["auto_tune"]["scan"]["high"], 130)
        self.assertEqual(
            stations["prf02"]["model_lines"],
            {"dispersion": "PRF02local", "twiss": "ALL_MAIN"},
        )
        self.assertEqual(stations["prf02"]["energy0_default_mev"], 114.16)
        self.assertNotIn("model_snapshot_source", stations["prf02"])
        self.assertAlmostEqual(stations["prf02"]["design_eta_m"], -0.3280857973174453)
        self.assertEqual(
            resolve_channel(profile, "PRF02", "image", "real"),
            "HALF:IN:FLAG:PRF02:image1:ArrayData",
        )
        self.assertEqual(
            resolve_channel(profile, "LINAC_ENERGY", "setpoint", "real"),
            "IN:LA:ENG",
        )
        self.assertEqual(
            resolve_channel(profile, "TRANSPORT_ENERGY", "setpoint", "real"),
            "IN:TL:ENG",
        )
        self.assertEqual(stations["eny"]["energy_element"], "LINAC_ENERGY")
        self.assertEqual(
            profile.get_element("LINAC_ENERGY").limits,
            {"setpoint": {"low": 0, "high": 2450, "unit": "MeV"}},
        )
        self.assertEqual(
            resolve_channel(profile, "PREINJECTOR_ENERGY", "setpoint", "real"),
            "IN:L01:ENG",
        )

        real_context = load_app_context(
            "energy_spectrum",
            machine_id="half",
            control_backend="real",
        )
        self.assertTrue(workflow_writes_allowed(real_context, "energy_spectrum"))

    def test_station_runtime_artifacts_are_isolated(self):
        context = load_app_context(
            "energy_spectrum",
            machine_id="half",
            control_backend="vm",
        )

        eny = resolve_energy_spectrum_runtime_paths(context, station_id="eny")
        prf02 = resolve_energy_spectrum_runtime_paths(context, station_id="prf02")

        self.assertNotEqual(eny["latest_dir"], prf02["latest_dir"])
        self.assertTrue(str(eny["latest_dir"]).endswith("latest/stations/eny"))
        self.assertTrue(str(prf02["runs_dir"]).endswith("runs/stations/prf02"))

    def test_prf02_local_model_line_uses_bl01a_and_ends_at_prf02(self):
        context = load_app_context("energy_spectrum", machine_id="half")
        assert context.model_backend is not None
        parser = ElegantParser(
            context.model_backend.config["source_lattice"],
            context.model_backend.config["energy_ini_ele"],
            "PRF02local",
        )

        usedline = parser.build_runtime_state()["usedline"]

        self.assertEqual(usedline[0], "BL01A")
        self.assertEqual(usedline[-1], "PRF02")
        self.assertIn("QL01", usedline)
        self.assertIn("QL02", usedline)

    def test_eny_local_model_line_uses_beny_and_ends_at_eny(self):
        context = load_app_context("energy_spectrum", machine_id="half")
        assert context.model_backend is not None
        parser = ElegantParser(
            context.model_backend.config["source_lattice"],
            context.model_backend.config["energy_ini_ele"],
            "ESAlocal",
        )

        usedline = parser.build_runtime_state()["usedline"]

        self.assertEqual(usedline[0], "BENY")
        self.assertEqual(usedline[-1], "ENY")
        self.assertNotIn("SM", usedline)


if __name__ == "__main__":
    unittest.main()
