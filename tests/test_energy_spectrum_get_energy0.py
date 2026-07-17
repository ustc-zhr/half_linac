from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PARENT = REPO_ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from half_linac.src.apps.energy_spectrum.get_energy0 import (
    get_energy0,
    select_reference_energy_mev,
)
from half_linac.src.shared.machine_profile import (
    get_workflow,
    load_profile,
    resolve_default_energy_spectrum_station,
)


class Energy0ConversionTests(unittest.TestCase):
    def test_get_energy0_uses_configurable_conversion(self):
        default_energy = get_energy0(100.0)
        custom_energy = get_energy0(
            100.0,
            {
                "magnet_length_m": 1.0,
                "deflect_angle_rad": 0.5,
                "field_t_per_a": 1.0e-3,
            },
        )
        self.assertNotEqual(default_energy, custom_energy)
        self.assertAlmostEqual(custom_energy, 59.9584916, places=6)

    def test_half_energy_spectrum_workflow_exposes_bend_energy_conversion(self):
        profile = load_profile("half")
        workflow = resolve_default_energy_spectrum_station(
            get_workflow(profile, "energy_spectrum")
        )
        conversion = workflow["energy_from_bend_current"]
        self.assertAlmostEqual(conversion["magnet_length_m"], 2.7271)
        self.assertAlmostEqual(conversion["deflect_angle_rad"], 0.4363323129985824)
        self.assertAlmostEqual(conversion["field_t_per_a"], 0.000599792458)

    def test_energy_spectrum_workflows_expose_x_reference_mm_by_backend(self):
        for machine_id in ("half", "irfel"):
            profile = load_profile(machine_id)
            workflow = get_workflow(profile, "energy_spectrum")
            x_reference = workflow["x_reference_mm"]

            self.assertIn("vm", x_reference)
            self.assertIn("real", x_reference)
            self.assertIsInstance(float(x_reference["vm"]), float)
            self.assertIsInstance(float(x_reference["real"]), float)

    def test_reference_energy_prefers_coordinated_energy_pv(self):
        energy, source = select_reference_energy_mev(
            36.0,
            reference_energy_mev=35.8,
            bend_current=0.062,
            bend_conversion={
                "magnet_length_m": 2.7271,
                "deflect_angle_rad": 0.4363323129985824,
                "field_t_per_a": 0.000599792458,
            },
        )

        self.assertEqual(energy, 35.8)
        self.assertEqual(source, "reference_pv")

    def test_reference_energy_does_not_apply_implicit_bend_calibration(self):
        energy, source = select_reference_energy_mev(
            36.0,
            bend_current=0.062,
            bend_conversion=None,
        )

        self.assertEqual(energy, 36.0)
        self.assertEqual(source, "workflow_default")


if __name__ == "__main__":
    unittest.main()
