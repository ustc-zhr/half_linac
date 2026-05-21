from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PARENT = REPO_ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from half_linac.src.shared.machine_profile import (
    MachineProfile,
    MachineProfileError,
    get_workflow,
    list_elements,
    load_profile,
    resolve_channel,
)


class MachineProfileTests(unittest.TestCase):
    def test_load_half_profile(self):
        profile = load_profile("half")
        self.assertEqual(profile.machine.id, "half")
        self.assertEqual(profile.machine.default_mode, "vm")
        self.assertEqual(profile.schema_version, "1")

    def test_resolve_expected_half_channels(self):
        profile = load_profile("half")
        self.assertEqual(
            resolve_channel(profile, "BPM03", "x", "vm"),
            "HALF:IN:BPM:BPM03:X:ao",
        )
        self.assertEqual(
            resolve_channel(profile, "XC21", "setpoint", "real"),
            "HALF:IN:COR:XC21:ao",
        )
        self.assertEqual(
            resolve_channel(profile, "QL27", "k1", "vm"),
            "HALF:IN:QUAD:QL27:K1",
        )
        self.assertEqual(
            resolve_channel(profile, "PRF07", "sigx", "Virtual Machine"),
            "HALF:IN:FLAG:PRF07:sigx",
        )

    def test_orbit_workflow_matches_expected_shape(self):
        profile = load_profile("half")
        workflow = get_workflow(profile, "orbit")
        self.assertEqual(len(workflow["bpms"]), 41)
        self.assertEqual(workflow["bpms"][0], "BPM03")
        self.assertEqual(workflow["bpms"][-1], "BPM43")
        self.assertEqual(workflow["xcors"][18], "XC21")
        self.assertEqual(workflow["ycors"][26], "YC29")

    def test_bba_and_emit_defaults_exist(self):
        profile = load_profile("half")
        bba = get_workflow(profile, "bba")
        emit = get_workflow(profile, "emit_measure")

        standard_default = bba["standard"]["default_preset"]
        bba2_default = bba["bba2"]["default_preset"]
        emit_default = emit["default_preset"]

        preset_ids = {preset["id"] for preset in bba["presets"]}
        emit_ids = {preset["id"] for preset in emit["presets"]}

        self.assertIn(standard_default, preset_ids)
        self.assertIn(bba2_default, preset_ids)
        self.assertIn(emit_default, emit_ids)

    def test_list_elements_filters_by_kind(self):
        profile = load_profile("half")
        bpm_elements = list_elements(profile, "bpm")
        flag_elements = list_elements(profile, "flag")
        self.assertTrue(any(element.id == "BPM03" for element in bpm_elements))
        self.assertEqual({element.id for element in flag_elements}, {"PRF04", "PRF06", "PRF07", "PRF08"})

    def test_duplicate_element_ids_raise(self):
        bad = {
            "schema_version": "1",
            "machine": {
                "id": "dup",
                "family": "linac",
                "display_name": "Dup",
                "default_mode": "vm",
            },
            "elements": [
                {
                    "id": "BPM01",
                    "kind": "bpm",
                    "display_name": "BPM01",
                    "order": 1,
                    "tags": [],
                    "limits": {},
                    "channels": {
                        "x": {"vm": "PV:X", "real": "PV:X"},
                        "y": {"vm": "PV:Y", "real": "PV:Y"},
                    },
                },
                {
                    "id": "BPM01",
                    "kind": "bpm",
                    "display_name": "BPM01 copy",
                    "order": 2,
                    "tags": [],
                    "limits": {},
                    "channels": {
                        "x": {"vm": "PV2:X", "real": "PV2:X"},
                        "y": {"vm": "PV2:Y", "real": "PV2:Y"},
                    },
                },
            ],
            "workflows": {
                "orbit": {
                    "bpms": ["BPM01"],
                    "xcors": ["XC01"],
                    "ycors": ["YC01"],
                },
                "bba": {
                    "presets": [],
                    "standard": {
                        "correctors": ["XC01"],
                        "quads": ["Q1"],
                        "bpm1": ["BPM01"],
                        "bpm2": ["BPM01"],
                        "default_preset": "missing",
                    },
                    "bba2": {
                        "quads": ["Q1"],
                        "correctors": ["XC01"],
                        "bpm1": ["BPM01"],
                        "bpm2": ["BPM01"],
                        "modes": ["Virtual Machine"],
                        "default_preset": "missing",
                    },
                },
                "emit_measure": {
                    "presets": [],
                    "default_preset": "missing",
                    "twiss_quads": ["Q1"],
                },
            },
        }
        with self.assertRaises(MachineProfileError):
            MachineProfile.from_dict(bad)

    def test_orbit_length_mismatch_raises(self):
        bad = {
            "schema_version": "1",
            "machine": {
                "id": "bad",
                "family": "linac",
                "display_name": "Bad",
                "default_mode": "vm",
            },
            "elements": [
                {
                    "id": "BPM01",
                    "kind": "bpm",
                    "display_name": "BPM01",
                    "order": 1,
                    "tags": [],
                    "limits": {},
                    "channels": {
                        "x": {"vm": "BPM01:X", "real": "BPM01:X"},
                        "y": {"vm": "BPM01:Y", "real": "BPM01:Y"},
                    },
                },
                {
                    "id": "XC01",
                    "kind": "corr",
                    "display_name": "XC01",
                    "order": 2,
                    "tags": [],
                    "limits": {},
                    "channels": {"setpoint": {"vm": "XC01", "real": "XC01"}},
                },
                {
                    "id": "YC01",
                    "kind": "corr",
                    "display_name": "YC01",
                    "order": 3,
                    "tags": [],
                    "limits": {},
                    "channels": {"setpoint": {"vm": "YC01", "real": "YC01"}},
                },
                {
                    "id": "Q1",
                    "kind": "quad",
                    "display_name": "Q1",
                    "order": 4,
                    "tags": [],
                    "limits": {},
                    "channels": {"k1": {"vm": "Q1", "real": "Q1"}},
                },
                {
                    "id": "PRF01",
                    "kind": "flag",
                    "display_name": "PRF01",
                    "order": 5,
                    "tags": [],
                    "limits": {},
                    "channels": {
                        "sigx": {"vm": "PRF01:sigx", "real": "PRF01:sigx"},
                        "sigy": {"vm": "PRF01:sigy", "real": "PRF01:sigy"},
                    },
                },
            ],
            "workflows": {
                "orbit": {
                    "bpms": ["BPM01"],
                    "xcors": ["XC01", "XC01"],
                    "ycors": ["YC01"],
                },
                "bba": {
                    "presets": [
                        {
                            "id": "bba_default",
                            "plane": "X",
                            "quad": "Q1",
                            "corr": "XC01",
                            "bpm1": "BPM01",
                            "bpm2": "BPM01",
                        }
                    ],
                    "standard": {
                        "correctors": ["XC01"],
                        "quads": ["Q1"],
                        "bpm1": ["BPM01"],
                        "bpm2": ["BPM01"],
                        "default_preset": "bba_default",
                    },
                    "bba2": {
                        "quads": ["Q1"],
                        "correctors": ["XC01"],
                        "bpm1": ["BPM01"],
                        "bpm2": ["BPM01"],
                        "modes": ["Virtual Machine"],
                        "default_preset": "bba_default",
                    },
                },
                "emit_measure": {
                    "presets": [
                        {
                            "id": "emit_default",
                            "quad": "Q1",
                            "flag": "PRF01",
                            "energy_mev": 1,
                        }
                    ],
                    "default_preset": "emit_default",
                    "twiss_quads": ["Q1"],
                },
            },
        }
        with self.assertRaises(MachineProfileError):
            MachineProfile.from_dict(bad)


if __name__ == "__main__":
    unittest.main()
