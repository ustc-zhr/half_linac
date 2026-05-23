from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
PARENT = REPO_ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from half_linac.src.shared.machine_profile import (
    AppContext,
    ElegantModelBackend,
    MachineProfile,
    MachineProfileError,
    build_model_backend,
    get_bba_preset,
    get_emit_preset,
    get_workflow,
    list_elements,
    load_app_context,
    load_profile,
    resolve_channel,
)
from half_linac.src.shared.machine_profile.runtime_selector import (
    default_control_backend_choices,
    list_machine_choices,
)


class MachineProfileTests(unittest.TestCase):
    def test_load_half_profile(self):
        profile = load_profile("half")
        self.assertEqual(profile.machine.id, "half")
        self.assertEqual(profile.machine.default_mode, "vm")
        self.assertEqual(profile.schema_version, "1")

    def test_load_orbit_correct_app_context(self):
        context = load_app_context("orbit_correct")
        self.assertIsInstance(context, AppContext)
        self.assertEqual(context.machine.id, "half")
        self.assertEqual(context.control_backend.name, "vm")
        self.assertIsNone(context.model_backend)
        self.assertIsNotNone(context.orbit_workflow)
        assert context.orbit_workflow is not None
        self.assertEqual(len(context.orbit_workflow.bpms), 41)
        self.assertEqual(context.orbit_workflow.xcors[18], "XC21")

    def test_load_bba_app_context(self):
        context = load_app_context("bba")
        self.assertIsInstance(context, AppContext)
        self.assertIsNotNone(context.bba_workflow)
        self.assertIsNotNone(context.model_backend)
        assert context.bba_workflow is not None
        self.assertEqual(context.bba_workflow.standard.default_preset, "bba1_default")
        self.assertEqual(context.bba_workflow.bba2.default_preset, "bba2_default")
        self.assertEqual(context.bba_workflow.standard.quads, ())
        self.assertEqual(context.bba_workflow.standard.correctors, ())
        self.assertEqual(context.bba_workflow.bba2.quads, ())
        self.assertEqual(context.bba_workflow.bba2.correctors, ())
        self.assertEqual(context.bba_workflow.bba2.control_backends, ())
        assert context.model_backend is not None
        self.assertEqual(context.model_backend.engine, "elegant")

    def test_load_emit_measure_app_context(self):
        context = load_app_context("emit_measure")
        self.assertIsInstance(context, AppContext)
        self.assertIsNotNone(context.emit_measure_workflow)
        self.assertIsNotNone(context.model_backend)
        assert context.emit_measure_workflow is not None
        self.assertEqual(context.emit_measure_workflow.default_preset, "emit_ql27_prf06")
        self.assertEqual(context.emit_measure_workflow.twiss_quads, ())
        assert context.model_backend is not None
        self.assertEqual(context.model_backend.engine, "elegant")

    def test_resolve_expected_half_channels(self):
        profile = load_profile("half")
        self.assertEqual(
            resolve_channel(profile, "BPM03", "x", "vm"),
            "HALF:IN:BPM:BPM03:X:ao",
        )
        self.assertEqual(
            resolve_channel(profile, "XC21", "setpoint", "real"),
            "IN:PS:LE16:XC21:current:ao",
        )
        self.assertEqual(
            resolve_channel(profile, "QL27", "k1", "vm"),
            "HALF:IN:AP:QUAD:QL27:K1:ao",
        )
        self.assertEqual(
            resolve_channel(profile, "PRF07", "sigx", "Virtual Machine"),
            "HALF:IN:FLAG:PRF07:sigx",
        )

    def test_real_backend_uses_raw_machine_pv_naming(self):
        profile = load_profile("half")
        self.assertEqual(
            resolve_channel(profile, "BPM01", "x", "real"),
            "IN:BD:LE05:DBPM:01:BPM_X",
        )
        self.assertEqual(
            resolve_channel(profile, "BPM20", "x", "real"),
            "IN:BD:LE14:DBPM:20:BPM_X",
        )
        self.assertEqual(
            resolve_channel(profile, "PRF04", "sigy", "real"),
            "IN:BD:PRF04:V:SIZE",
        )
        self.assertEqual(
            resolve_channel(profile, "XC00", "setpoint", "real"),
            "IN:PS:LE07:XC00:current:ao",
        )
        self.assertEqual(
            resolve_channel(profile, "MS:HC", "setpoint", "real"),
            "IN:PS:LE07:SM01-DX:current:ao",
        )
        self.assertEqual(
            resolve_channel(profile, "HC2", "setpoint", "real"),
            "IRFEL:PS:HC02:current:ao",
        )
        self.assertEqual(
            resolve_channel(profile, "QT01", "k1", "real"),
            "IN:MG:L002:QUAD:QT01:K1",
        )
        self.assertEqual(
            resolve_channel(profile, "QL03", "k1", "real"),
            "IN:PS:LE07:QL03:current:ao",
        )

    def test_vm_backend_uses_softioc_alias_naming_for_magnets(self):
        profile = load_profile("half")
        self.assertEqual(
            resolve_channel(profile, "XC00", "setpoint", "vm"),
            "HALF:IN:PS:XC00:current:ao",
        )
        self.assertEqual(
            resolve_channel(profile, "XC21", "setpoint", "vm"),
            "HALF:IN:PS:XC21:current:ao",
        )
        self.assertEqual(
            resolve_channel(profile, "HC01", "setpoint", "vm"),
            "HALF:IN:PS:HC01:current:ao",
        )
        self.assertEqual(
            resolve_channel(profile, "QL03", "k1", "vm"),
            "HALF:IN:AP:QUAD:QL03:K1:ao",
        )

    def test_resolve_channel_from_app_context(self):
        context = load_app_context("orbit_correct")
        self.assertEqual(
            resolve_channel(context, "BPM03", "x"),
            "HALF:IN:BPM:BPM03:X:ao",
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

    def test_context_preset_helpers_return_default_presets(self):
        bba_context = load_app_context("bba")
        emit_context = load_app_context("emit_measure")
        bba_preset = get_bba_preset(bba_context)
        emit_preset = get_emit_preset(emit_context)

        self.assertEqual(bba_preset.id, bba_context.bba_workflow.standard.default_preset)
        self.assertEqual(bba_preset.plane, "x")
        self.assertEqual(emit_preset.id, emit_context.emit_measure_workflow.default_preset)
        self.assertGreater(emit_preset.energy_mev, 0)

    def test_context_preset_helpers_support_explicit_bba_preset(self):
        bba_context = load_app_context("bba")
        preset = get_bba_preset(bba_context, "bba2_default")
        self.assertEqual(preset.family, "bba2")
        self.assertIsNone(preset.mode)
        self.assertEqual(preset.energy_mev, 2200)
        self.assertEqual(preset.corr, "XC21")
        self.assertEqual(preset.scan["quad_from"], -3)
        self.assertEqual(preset.analysis["energy_mev"], 2200)
        self.assertEqual(preset.analysis["bpm1_samples"], 1)
        self.assertEqual(preset.analysis.leff_by, 0.058287)

    def test_context_preset_helpers_support_default_emit_preset(self):
        emit_context = load_app_context("emit_measure")
        preset = get_emit_preset(emit_context)
        self.assertEqual(preset.quad, "QL27")
        self.assertEqual(preset.flag, "PRF06")
        self.assertEqual(preset.energy_mev, 2200)
        self.assertEqual(preset.scan.k1_steps, 15)
        self.assertEqual(preset.scan["samples"], 5)
        self.assertEqual(preset.analysis.energy_mev, 2200)

    def test_build_model_backend_returns_elegant_backend_for_measurement_apps(self):
        bba_backend = build_model_backend(load_app_context("bba"), energy_mev=2200)
        emit_backend = build_model_backend(load_app_context("emit_measure"), energy_mev=2200)
        self.assertIsInstance(bba_backend, ElegantModelBackend)
        self.assertIsInstance(emit_backend, ElegantModelBackend)

    def test_list_elements_filters_by_kind(self):
        profile = load_profile("half")
        bpm_elements = list_elements(profile, "bpm")
        flag_elements = list_elements(profile, "flag")
        corr_elements = list_elements(profile, "corr")
        quad_elements = list_elements(profile, "quad")
        corr_ids = {element.id for element in corr_elements}
        bpm_ids = {element.id for element in bpm_elements}
        quad_ids = {element.id for element in quad_elements}
        self.assertTrue(any(element.id == "BPM03" for element in bpm_elements))
        self.assertIn("BPM01", bpm_ids)
        self.assertIn("XC00", corr_ids)
        self.assertIn("YC02", corr_ids)
        self.assertNotIn("HIC01", corr_ids)
        self.assertNotIn("VIC02", corr_ids)
        self.assertIn("QL03", quad_ids)
        self.assertNotIn("CQ3", quad_ids)
        self.assertEqual({element.id for element in flag_elements}, {"PRF04", "PRF06", "PRF07", "PRF08"})

    def test_list_elements_supports_role_and_plane_filters(self):
        profile = load_profile("half")
        quad_ids = {element.id for element in list_elements(profile, kind="quad")}
        bpm_ids = {element.id for element in list_elements(profile, kind="bpm")}
        flag_ids = {element.id for element in list_elements(profile, kind="flag")}
        x_corrs = {element.id for element in list_elements(profile, kind="corr", plane="x")}
        y_corrs = {element.id for element in list_elements(profile, kind="corr", plane="y")}

        self.assertIn("QL03", quad_ids)
        self.assertIn("QT18", quad_ids)
        self.assertIn("BPM01", bpm_ids)
        self.assertIn("BPM29", bpm_ids)
        self.assertEqual(flag_ids, {"PRF04", "PRF06", "PRF07", "PRF08"})
        self.assertIn("XC00", x_corrs)
        self.assertIn("MS:HC", x_corrs)
        self.assertNotIn("YC00", x_corrs)
        self.assertIn("YC00", y_corrs)
        self.assertIn("LS:VC", y_corrs)
        self.assertNotIn("XC00", y_corrs)

    def test_half_elements_keep_simple_metadata_and_inferred_planes(self):
        profile = load_profile("half")
        ql03 = profile.get_element("QL03")
        xc21 = profile.get_element("XC21")
        yc21 = profile.get_element("YC21")

        self.assertEqual(ql03.roles, ())
        self.assertIn("bba_corr", xc21.roles)
        self.assertEqual(xc21.plane, "x")
        self.assertEqual(yc21.plane, "y")

    def test_load_profile_uses_env_machine_id_when_unspecified(self):
        with patch.dict(os.environ, {"HALF_MACHINE_ID": "half"}):
            profile = load_profile()
        self.assertEqual(profile.machine.id, "half")

    def test_load_app_context_uses_env_control_backend_when_unspecified(self):
        with patch.dict(os.environ, {"HALF_CONTROL_BACKEND": "real"}):
            context = load_app_context("orbit_correct")
        self.assertEqual(context.control_backend.name, "real")

    def test_runtime_selector_lists_half_machine_profile(self):
        choices = list_machine_choices()
        machine_ids = {choice.machine_id for choice in choices}
        self.assertIn("half", machine_ids)
        self.assertTrue(any(choice.display_name for choice in choices if choice.machine_id == "half"))

    def test_runtime_selector_discovers_control_backends_from_directory_profile(self):
        self.assertEqual(default_control_backend_choices("half"), ("vm", "real"))

    def test_half_model_backend_paths_are_resolved_from_directory_config(self):
        context = load_app_context("emit_measure")
        assert context.model_backend is not None
        source_json = Path(context.model_backend.config["source_json"])
        source_lattice = Path(context.model_backend.config["source_lattice"])
        self.assertTrue(source_json.is_absolute())
        self.assertTrue(source_lattice.is_absolute())
        self.assertTrue(str(source_json).endswith("src/virtual_machine/half_elegant/halflinac.json"))
        self.assertTrue(str(source_lattice).endswith("src/virtual_machine/half_elegant/elegant/lattice_ini.lte"))

    def test_softioc_substitutions_include_vm_aliases_for_profile_only_elements(self):
        substitutions = (
            REPO_ROOT / "src" / "softIOC" / "halflinac" / "db" / "halflinac.substitutions"
        ).read_text(encoding="utf-8")
        self.assertIn('pattern {QUAD, K1ALIAS}', substitutions)
        self.assertIn('pattern {COR, SETALIAS, READALIAS}', substitutions)
        self.assertIn('{ "QL03", "HALF:IN:AP:QUAD:QL03:K1:ao" }', substitutions)
        self.assertIn(
            '{ "XC00", "HALF:IN:PS:XC00:current:ao", "HALF:IN:PS:XC00:current:ai" }',
            substitutions,
        )
        self.assertIn(
            '{ "HC01", "HALF:IN:PS:HC01:current:ao", "HALF:IN:PS:HC01:current:ai" }',
            substitutions,
        )
        self.assertNotIn("CQ1", substitutions)
        self.assertNotIn("CQ3", substitutions)
        self.assertNotIn("MQ1", substitutions)
        self.assertNotIn("MQ11", substitutions)
        self.assertNotIn("HIC01", substitutions)
        self.assertNotIn("VIC01", substitutions)

    def test_invalid_machine_id_from_env_raises(self):
        with patch.dict(os.environ, {"HALF_MACHINE_ID": "../escape"}):
            with self.assertRaises(MachineProfileError):
                load_profile()

    def test_load_profile_falls_back_to_legacy_single_file_fixture(self):
        legacy_profile = {
            "schema_version": "1",
            "machine": {
                "id": "legacy",
                "family": "linac",
                "display_name": "Legacy Linac",
                "default_mode": "vm",
            },
            "elements": [
                {
                    "id": "BPM01",
                    "kind": "bpm",
                    "display_name": "BPM01",
                    "order": 1,
                    "tags": ["orbit", "bba"],
                    "limits": {},
                    "channels": {
                        "x": {"vm": "LEGACY:BPM01:X", "real": "REAL:BPM01:X"},
                        "y": {"vm": "LEGACY:BPM01:Y", "real": "REAL:BPM01:Y"},
                    },
                },
                {
                    "id": "XC01",
                    "kind": "corr",
                    "display_name": "XC01",
                    "order": 2,
                    "tags": ["orbit", "bba"],
                    "limits": {},
                    "channels": {
                        "setpoint": {"vm": "LEGACY:XC01", "real": "REAL:XC01"},
                    },
                },
                {
                    "id": "YC01",
                    "kind": "corr",
                    "display_name": "YC01",
                    "order": 3,
                    "tags": ["orbit", "bba"],
                    "limits": {},
                    "channels": {
                        "setpoint": {"vm": "LEGACY:YC01", "real": "REAL:YC01"},
                    },
                },
                {
                    "id": "Q01",
                    "kind": "quad",
                    "display_name": "Q01",
                    "order": 4,
                    "tags": ["bba", "emit_measure"],
                    "limits": {},
                    "channels": {
                        "k1": {"vm": "LEGACY:Q01:K1", "real": "REAL:Q01:K1"},
                    },
                },
                {
                    "id": "PRF01",
                    "kind": "flag",
                    "display_name": "PRF01",
                    "order": 5,
                    "tags": ["emit_measure"],
                    "limits": {},
                    "channels": {
                        "sigx": {"vm": "LEGACY:PRF01:SIGX", "real": "REAL:PRF01:SIGX"},
                        "sigy": {"vm": "LEGACY:PRF01:SIGY", "real": "REAL:PRF01:SIGY"},
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
                    "presets": [
                        {
                            "id": "legacy_bba",
                            "family": "standard",
                            "plane": "x",
                            "quad": "Q01",
                            "corr": "XC01",
                            "bpm1": "BPM01",
                            "bpm2": "BPM01",
                            "scan": {
                                "corr_from": -0.1,
                                "corr_end": 0.1,
                                "corr_steps": 3,
                                "quad_from": 0.0,
                                "quad_end": 1.0,
                                "quad_steps": 3,
                                "samples": 2,
                                "sleeptime": 0.1,
                            },
                        }
                    ],
                    "standard": {
                        "correctors": ["XC01"],
                        "quads": ["Q01"],
                        "bpm1": ["BPM01"],
                        "bpm2": ["BPM01"],
                        "default_preset": "legacy_bba",
                    },
                    "bba2": {
                        "correctors": ["XC01"],
                        "quads": ["Q01"],
                        "bpm1": ["BPM01"],
                        "bpm2": ["BPM01"],
                        "control_backends": ["vm", "real"],
                        "default_preset": "legacy_bba",
                    },
                },
                "emit_measure": {
                    "presets": [
                        {
                            "id": "legacy_emit",
                            "quad": "Q01",
                            "flag": "PRF01",
                            "energy_mev": 220.0,
                            "scan": {
                                "k1_from": 0.0,
                                "k1_end": 1.0,
                                "k1_steps": 3,
                                "samples": 2,
                                "sleeptime": 0.1,
                            },
                        }
                    ],
                    "default_preset": "legacy_emit",
                    "twiss_quads": ["Q01"],
                },
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            legacy_dir = temp_root / "configs" / "machines" / "legacy"
            legacy_dir.mkdir(parents=True)
            (legacy_dir / "profile.json").write_text(
                json.dumps(legacy_profile, indent=2),
                encoding="utf-8",
            )
            with patch("half_linac.src.shared.machine_profile.loader.repo_root", return_value=temp_root):
                profile = load_profile("legacy")
                context = load_app_context("orbit_correct", machine_id="legacy")

        self.assertEqual(profile.machine.id, "legacy")
        self.assertEqual(context.machine.id, "legacy")
        self.assertEqual(resolve_channel(profile, "BPM01", "x", "vm"), "LEGACY:BPM01:X")
        self.assertEqual(profile.get_element("XC01").plane, "x")
        self.assertEqual(profile.get_element("YC01").plane, "y")

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

    def test_missing_real_or_vm_channel_mapping_raises(self):
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
                        "y": {"vm": "BPM01:Y"},
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
                    "xcors": ["XC01"],
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

    def test_invalid_bba_plane_or_mode_raises(self):
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
                    "xcors": ["XC01"],
                    "ycors": ["YC01"],
                },
                "bba": {
                    "presets": [
                        {
                            "id": "bba_default",
                            "plane": "horizontal",
                            "quad": "Q1",
                            "corr": "XC01",
                            "bpm1": "BPM01",
                            "bpm2": "BPM01",
                            "mode": "offline",
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
                        "modes": ["offline"],
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
