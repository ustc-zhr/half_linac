from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import sdds

REPO_ROOT = Path(__file__).resolve().parents[1]
PARENT = REPO_ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from half_linac.src.shared.elegant_backend import ElegantParser
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
