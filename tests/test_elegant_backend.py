from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import sdds

REPO_ROOT = Path(__file__).resolve().parents[1]
PARENT = REPO_ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from half_linac.src.shared.elegant_backend import (
    ElegantParser,
    VmBpmPublishSpec,
    VmPublishPlan,
    VmPublisher,
    VmWatchImagePublishSpec,
    build_vm_publish_plan,
)
from half_linac.src.shared.machine_profile import load_profile, resolve_machine_runtime
from half_linac.src.virtual_machine.half_elegant.elegant_parser import elegant_parser


class ElegantBackendTests(unittest.TestCase):
    def setUp(self):
        self.elegant_dir = REPO_ROOT / "src/virtual_machine/half_elegant/elegant"
        self.lattice_file = self.elegant_dir / "lattice_ini.lte"
        self.ele_file = self.elegant_dir / "one_ini.ele"

    def test_shared_runtime_state_matches_half_wrapper_except_ap(self):
        shared_parser = ElegantParser(self.lattice_file, self.ele_file, "ALL")
        compat_parser = elegant_parser(str(self.lattice_file), str(self.ele_file), "ALL")

        shared_state = shared_parser.build_runtime_state()
        compat_state = compat_parser.build_runtime_state()

        self.assertEqual(shared_state["control"], compat_state["control"])
        self.assertEqual(shared_state["usedline"], compat_state["usedline"])

        stripped_compat_lattice = {
            element_id: {
                key: value
                for key, value in element.items()
                if key != "AP"
            }
            for element_id, element in compat_state["lattice"].items()
        }
        self.assertEqual(shared_state["lattice"], stripped_compat_lattice)
        self.assertTrue(
            any("AP" in element for element in compat_state["lattice"].values()),
            "HALF compatibility wrapper should still provide AP fields.",
        )
        self.assertFalse(
            any("AP" in element for element in shared_state["lattice"].values()),
            "Shared elegant backend must stay free of HALF-specific AP fields.",
        )

    def test_json_to_lte_ele_matches_half_wrapper_output(self):
        shared_parser = ElegantParser(self.lattice_file, self.ele_file, "ALL")
        compat_parser = elegant_parser(str(self.lattice_file), str(self.ele_file), "ALL")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            shared_dir = tmpdir_path / "shared"
            compat_dir = tmpdir_path / "compat"
            shared_dir.mkdir()
            compat_dir.mkdir()
            shared_json = shared_dir / "runtime.json"
            compat_json = compat_dir / "runtime.json"
            shared_lte = shared_dir / "lattice.lte"
            shared_ele = shared_dir / "one.ele"
            compat_lte = compat_dir / "lattice.lte"
            compat_ele = compat_dir / "one.ele"

            shared_parser.dump_runtime_state(shared_json)
            compat_parser.dump2json(str(compat_json))

            shared_parser.json_to_lte_ele(shared_lte, shared_ele, shared_json)
            compat_parser.json2lte_ele(str(compat_lte), str(compat_ele), str(compat_json))

            self.assertEqual(shared_lte.read_text(encoding="utf-8"), compat_lte.read_text(encoding="utf-8"))
            self.assertEqual(shared_ele.read_text(encoding="utf-8"), compat_ele.read_text(encoding="utf-8"))

    def test_half_compat_parser_defaults_follow_machine_runtime_metadata(self):
        runtime = resolve_machine_runtime()
        compat_parser = elegant_parser(str(self.lattice_file), str(self.ele_file), "ALL")

        self.assertEqual(compat_parser._resolve_runtime_json_path(), runtime.vm.runtime_json)
        self.assertEqual(
            compat_parser._resolve_runtime_json_path(runtime.vm.runtime_json.name),
            runtime.vm.runtime_json,
        )
        self.assertEqual(
            compat_parser._resolve_runtime_json_path(f"./{runtime.vm.runtime_json.name}"),
            runtime.vm.runtime_json,
        )
        self.assertEqual(
            compat_parser._resolve_elegant_path(None, compat_parser.default_lattice_output_path),
            compat_parser.default_lattice_output_path,
        )
        self.assertEqual(
            compat_parser._resolve_elegant_path(
                "./elegant/lattice.lte",
                compat_parser.default_lattice_output_path,
            ),
            compat_parser.default_lattice_output_path,
        )
        self.assertEqual(
            compat_parser._resolve_elegant_path(
                "./elegant/one.ele",
                compat_parser.default_ele_output_path,
            ),
            compat_parser.default_ele_output_path,
        )

    def test_load_bpm_centroids_matches_current_half_sample(self):
        if not hasattr(sdds, "SDDS"):
            self.skipTest("Legacy SDDS python binding is unavailable in this test environment.")
        parser = ElegantParser(self.lattice_file, self.ele_file, "ALL")
        try:
            bpm = parser.load_bpm_centroids(self.elegant_dir / "one.bpmcen")
        except RuntimeError as exc:
            self.skipTest(str(exc))
        self.assertIn("BPM01", bpm)
        self.assertIn("Cx", bpm["BPM01"])
        self.assertIn("Cy", bpm["BPM01"])
        self.assertIsInstance(bpm["BPM01"]["Cx"], float)
        self.assertIsInstance(bpm["BPM01"]["Cy"], float)

    def test_load_watch_image_matches_half_geometry(self):
        if not hasattr(sdds, "SDDS"):
            self.skipTest("Legacy SDDS python binding is unavailable in this test environment.")
        parser = ElegantParser(self.lattice_file, self.ele_file, "ALL")
        try:
            image = parser.load_watch_image(
                self.elegant_dir / "PRF06.out",
                pixel_shape=(360, 270),
                pixel_width_mm=0.02,
            )
        except RuntimeError as exc:
            self.skipTest(str(exc))
        self.assertIsInstance(image, np.ndarray)
        self.assertEqual(image.shape, (360 * 270,))
        self.assertGreaterEqual(float(np.sum(image)), 0.0)

    def test_shared_callers_no_longer_import_half_elegant_parser(self):
        managed_paths = [
            REPO_ROOT / "src/shared/machine_profile/model_backend.py",
            REPO_ROOT / "src/apps/energy_spectrum/main.py",
            REPO_ROOT / "src/softIOC/mainIOC.py",
            REPO_ROOT / "src/softIOC/pv_server.py",
        ]
        offenders = []
        for path in managed_paths:
            text = path.read_text(encoding="utf-8")
            if "half_elegant.elegant_parser" in text:
                offenders.append(str(path.relative_to(REPO_ROOT)))
        self.assertEqual(
            offenders,
            [],
            f"Shared/model/IOC callers must not import half_elegant.elegant_parser: {offenders}",
        )

    def test_build_vm_publish_plan_uses_profile_channel_mappings(self):
        profile = load_profile("half")
        plan = build_vm_publish_plan(profile)

        bpm_specs = {spec.element_id: spec for spec in plan.bpm_specs}
        self.assertEqual(bpm_specs["BPM03"].x_pv, "HALF:IN:BPM:BPM03:X:ao")
        self.assertEqual(bpm_specs["BPM03"].y_pv, "HALF:IN:BPM:BPM03:Y:ao")
        self.assertEqual(len(bpm_specs), 43)

        watch_specs = {
            (spec.target_element_id, spec.logical_channel): spec
            for spec in plan.watch_image_specs
        }
        for flag_id in ("PRF04", "PRF06", "PRF07", "PRF08"):
            spec = watch_specs[(flag_id, "image")]
            self.assertEqual(spec.source_watch_id, flag_id)
            self.assertEqual(spec.pv_name, f"HALF:IN:FLAG:{flag_id}:image1:ArrayData:vm")

        esa_spec = watch_specs[("PRF07", "esa_image")]
        self.assertEqual(esa_spec.source_watch_id, "PRFESA")
        self.assertEqual(esa_spec.target_element_id, "PRF07")
        self.assertEqual(esa_spec.pv_name, "HALF:IN:FLAG:PRFESA:image1:ArrayData:vm")
        self.assertEqual(esa_spec.pixel_shape, (720, 270))
        self.assertEqual(esa_spec.pixel_width_mm, 0.02)

    def test_shared_publisher_uses_plan_pvs_for_bpm_updates(self):
        publisher = VmPublisher()
        plan = VmPublishPlan(
            bpm_specs=(
                VmBpmPublishSpec(
                    element_id="BPMX",
                    x_pv="CUSTOM:BPMX:X",
                    y_pv="CUSTOM:BPMX:Y",
                ),
            )
        )

        with patch(
            "half_linac.src.shared.elegant_backend.publisher._load_bpm_centroids_from_sdds",
            return_value={"BPMX": {"Cx": 1.25, "Cy": -2.5}},
        ), patch(
            "half_linac.src.shared.elegant_backend.publisher.caput_many",
            side_effect=([True], [True]),
        ) as caput_many_mock:
            ok = publisher.publish_bpms(plan, REPO_ROOT / "fake.bpmcen")

        self.assertTrue(ok)
        self.assertEqual(caput_many_mock.call_count, 2)
        self.assertEqual(caput_many_mock.call_args_list[0].args[0], ["CUSTOM:BPMX:X"])
        self.assertEqual(caput_many_mock.call_args_list[0].args[1], [1.25])
        self.assertEqual(caput_many_mock.call_args_list[1].args[0], ["CUSTOM:BPMX:Y"])
        self.assertEqual(caput_many_mock.call_args_list[1].args[1], [-2.5])

    def test_shared_publisher_reports_missing_watch_source(self):
        publisher = VmPublisher()
        plan = VmPublishPlan(
            watch_image_specs=(
                VmWatchImagePublishSpec(
                    source_watch_id="PRFESA",
                    target_element_id="PRF07",
                    logical_channel="esa_image",
                    pv_name="CUSTOM:FLAG:ESA",
                    pixel_shape=(4, 3),
                    pixel_width_mm=0.02,
                ),
            )
        )

        with patch("builtins.print") as print_mock:
            ok = publisher.publish_watch_images(
                plan,
                lattice={},
                usedline=["PRFESA"],
                elegant_dir=self.elegant_dir,
            )

        self.assertFalse(ok)
        self.assertTrue(any("PRFESA" in str(call) for call in print_mock.call_args_list))

    def test_shared_publisher_skips_watch_not_in_usedline(self):
        publisher = VmPublisher()
        plan = VmPublishPlan(
            watch_image_specs=(
                VmWatchImagePublishSpec(
                    source_watch_id="PRF06",
                    target_element_id="PRF06",
                    logical_channel="image",
                    pv_name="CUSTOM:FLAG:PRF06",
                    pixel_shape=(4, 3),
                    pixel_width_mm=0.02,
                ),
            )
        )
        lattice = {
            "PRF06": {
                "TYPE": "WATCH",
                "MODE": "coord",
                "DISABLE": "0",
            }
        }

        with patch("half_linac.src.shared.elegant_backend.publisher.caput") as caput_mock:
            ok = publisher.publish_watch_images(
                plan,
                lattice=lattice,
                usedline=[],
                elegant_dir=self.elegant_dir,
            )

        self.assertTrue(ok)
        caput_mock.assert_not_called()

    def test_shared_publisher_sources_do_not_import_runtime_config_or_half_pv_prefixes(self):
        parser_source = (REPO_ROOT / "src/shared/elegant_backend/parser.py").read_text(encoding="utf-8")
        publisher_source = (
            REPO_ROOT / "src/shared/elegant_backend/publisher.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("runtime_config as st", parser_source)
        self.assertNotIn("runtime_config as st", publisher_source)
        self.assertNotIn("HALF:IN:", publisher_source)

    def test_machine_driven_gui_entrypoints_still_repopulate_generated_default_choices(self):
        beam_source = (REPO_ROOT / "src/apps/beam_monitor/main.py").read_text(encoding="utf-8")
        emit_source = (REPO_ROOT / "src/apps/emit_measure/main.py").read_text(encoding="utf-8")
        self.assertIn("self.flag_selec.clear()", beam_source)
        self.assertIn("self.flag_selec.addItems(self.flag_ids)", beam_source)
        self.assertIn("self._set_combo_items(self.comboBox_4, flag_items)", emit_source)

    def test_esa_auto_tuner_demo_uses_machine_profile_instead_of_half_bend_pv(self):
        source = (REPO_ROOT / "src/apps/energy_spectrum/esa_auto_tuner.py").read_text(encoding="utf-8")
        self.assertNotIn("HALF:IN:ESA:PRF01:CurrentSet", source)
        self.assertIn("load_profile()", source)
        self.assertIn("resolve_channel(profile", source)

    def test_half_vm_helper_scripts_use_runtime_resolver_for_paths(self):
        helper_paths = [
            REPO_ROOT / "src/virtual_machine/half_elegant/full_VM.py",
            REPO_ROOT / "src/virtual_machine/half_elegant/simply_VM.py",
            REPO_ROOT / "src/virtual_machine/half_elegant/err_gene_VM.py",
            REPO_ROOT / "src/virtual_machine/half_elegant/transfer_ESAline.py",
        ]
        offenders = []
        legacy_path_snippet = 'src/virtual_machine/half_elegant/halflinac.json'
        for path in helper_paths:
            text = path.read_text(encoding="utf-8")
            if legacy_path_snippet in text:
                offenders.append(str(path.relative_to(REPO_ROOT)))
        self.assertEqual(
            offenders,
            [],
            f"HALF VM helper scripts should resolve runtime JSON through machine runtime metadata: {offenders}",
        )

    def test_half_compat_parser_no_longer_hardcodes_half_runtime_default_paths(self):
        source = (
            REPO_ROOT / "src/virtual_machine/half_elegant/elegant_parser.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn('dump2json(self, j_file="halflinac.json")', source)
        self.assertNotIn(
            'json2lte_ele(self, lat_f="./elegant/lattice.lte", ele_f="./elegant/one.ele", j_file="halflinac.json")',
            source,
        )
