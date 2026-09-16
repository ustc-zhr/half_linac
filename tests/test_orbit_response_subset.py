"""Offline regression checks for selected-BPM response matrices."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from half_linac.src.shared.machine_profile import load_app_context
from half_linac.src.apps.orbit_correct import profile_runtime as runtime
from half_linac.src.apps.orbit_correct.correct import OrbitCorrector
from half_linac.src.apps.orbit_correct.findresponse import ResponseMatrixCalculator


class ResponseSubsetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = patch.object(runtime, "ORBIT_RUNTIME_ROOT", Path(self.temp.name))
        self.root.start()
        self.addCleanup(self.root.stop)
        self.env = patch.dict(os.environ, {
            "HALF_LINAC_MACHINE_ID": "irfel", "HALF_LINAC_CONTROL_BACKEND": "vm"
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.context = load_app_context("orbit_correct")

    def corrector(self, bpms, xcors, ycors):
        return OrbitCorrector(target_BPMlist=bpms,
                              target_BPMx_values=[0.] * len(bpms),
                              target_BPMy_values=[0.] * len(bpms),
                              global_xcor_list=xcors, global_ycor_list=ycors)

    def test_noncontiguous_subset_and_name_order(self):
        record = runtime.write_response_matrix_snapshot(
            self.context, np.diag([2., 3., 5., 7.]),
            selected_bpms=["BPM10", "BPM07"])
        self.assertEqual(record["xcors"], ["HC07", "HC02"])
        self.assertEqual(record["shape"], [4, 4])
        self.assertEqual(len(runtime.list_response_matrix_records(self.context)), 1)
        cor = self.corrector(["BPM07", "BPM10"], ["HC02", "HC07"], ["VC02", "VC07"])
        cor._compute_svd()
        np.testing.assert_allclose(cor.pseudo_inverse_x, np.diag([1/3, 1/2]))
        np.testing.assert_allclose(cor.pseudo_inverse_y, np.diag([1/7, 1/5]))
        matrix = cor._load_valid_response_matrix()
        self.assertEqual(cor._local_response_coefficients(matrix, 1), (3., 7.))

    def test_single_bpm_and_missing_coverage(self):
        runtime.write_response_matrix_snapshot(self.context, np.eye(2), selected_bpms=["BPM07"])
        cor = self.corrector(["BPM07"], ["HC02"], ["VC02"])
        cor._compute_svd()
        self.assertEqual(cor.pseudo_inverse_x.shape, (1, 1))
        outside = self.corrector(["BPM10"], ["HC07"], ["VC07"])
        with self.assertRaisesRegex(ValueError, "does not cover BPM10"):
            outside._compute_svd()
        outside = self.corrector(["BPM07"], ["HC01"], ["VC02"])
        with self.assertRaisesRegex(ValueError, "does not cover HC01"):
            outside._compute_svd()

    def test_full_matrix_remains_usable_for_subset(self):
        runtime.write_response_matrix_snapshot(self.context, np.diag(np.arange(1., 11.)))
        cor = self.corrector(["BPM10", "BPM07"], ["HC02", "HC07"], ["VC07", "VC02"])
        cor._compute_svd()
        np.testing.assert_allclose(cor.pseudo_inverse_x, [[0, 1/2], [1/5, 0]])
        np.testing.assert_allclose(cor.pseudo_inverse_y, [[1/10, 0], [0, 1/7]])

    def test_bad_selection_and_pair_metadata_rejected(self):
        for bpms in ([], ["BPM07", "BPM07"], ["unknown"]):
            with self.subTest(bpms=bpms), self.assertRaises(ValueError):
                runtime.response_measurement_ids(self.context, bpms)
        record = runtime.write_response_matrix_snapshot(self.context, np.eye(2), selected_bpms=["BPM07"])
        path = Path(record["metadata_path"])
        payload = json.loads(path.read_text())
        payload["xcors"] = ["HC01"]
        path.write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError, "X corrector list"):
            runtime.get_active_response_matrix_record(self.context)

    def test_measurement_reads_and_scans_only_selected_pairs(self):
        with patch("signal.signal"):
            calc = ResponseMatrixCalculator(selected_bpms=["BPM10", "BPM07"])
        calc.init_BPM_pv()
        calc.init_COR_pv()
        self.assertEqual(calc.bpm_ids, ["BPM10", "BPM07"])
        self.assertEqual(calc.xcor_ids, ["HC07", "HC02"])
        self.assertEqual(len(calc.pvBPMx), 2)
        with patch.object(calc, "_measure_response", side_effect=np.eye(4).T) as measure:
            calc.calculate_response_matrix()
        self.assertEqual([call.args[0] for call in measure.call_args_list],
                         ["HC07", "HC02", "VC07", "VC02"])
        calc.save_matrix()
        self.assertEqual(runtime.get_active_response_matrix_record(self.context)["bpms"], calc.bpm_ids)


if __name__ == "__main__":
    unittest.main()
