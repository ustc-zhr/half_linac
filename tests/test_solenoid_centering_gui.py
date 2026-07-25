from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

GUI_TEST_ENABLED = os.environ.get("QT_QPA_PLATFORM") == "offscreen"

REPO_ROOT = Path(__file__).resolve().parents[1]
PARENT = REPO_ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

try:
    from PyQt5.QtWidgets import QApplication, QMessageBox

    from half_linac.src.apps.solenoid_centering.main import MainWindow
    from half_linac.src.apps.solenoid_centering.scan import CenteringResult
except ImportError:
    QApplication = None


@unittest.skipUnless(
    GUI_TEST_ENABLED and QApplication is not None,
    "requires a working QT_QPA_PLATFORM=offscreen plugin",
)
class SolenoidCenteringGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()

    def _result(self, *, actionable: bool) -> CenteringResult:
        return CenteringResult(
            preset_id="ms01_centering",
            original_solenoid=1.0,
            original_hcorr=0.0,
            original_vcorr=0.0,
            recommended_hcorr=0.2,
            recommended_vcorr=-0.2,
            best_score=90.0,
            axis_scans=(),
            relative_improvement=0.1,
            recommendation_available=actionable,
            recommendation_status=(
                "quality gate passed" if actionable else "improvement 1.0% is below required 5.0%"
            ),
            preflight={
                "range_checks": [
                    {"label": "HCOR", "limit_low": -1.0, "limit_high": 1.0},
                    {"label": "VCOR", "limit_low": -2.0, "limit_high": 2.0},
                ]
            },
            selected_devices={
                "hcorr_setpoint_pv": "TEST:HCOR:SP",
                "vcorr_setpoint_pv": "TEST:VCOR:SP",
            },
        )

    def test_non_actionable_result_disables_apply(self):
        self.window._on_scan_finished(self._result(actionable=False))

        self.assertFalse(self.window.apply_button.isEnabled())
        self.assertEqual(
            self.window.status_strip.items["RESULT QUALITY"].value_label.text(),
            "NO VALID RECOMMENDATION",
        )

    def test_confirmation_shows_pvs_limits_and_quality(self):
        captured = {}

        def question(_parent, _title, text, *_args):
            captured["text"] = text
            return QMessageBox.Cancel

        with patch(
            "half_linac.src.apps.solenoid_centering.main.QMessageBox.question",
            question,
        ):
            accepted = self.window._confirm_result_action(
                "Apply Recommended",
                self._result(actionable=True),
                apply=True,
            )

        self.assertFalse(accepted)
        self.assertIn("TEST:HCOR:SP", captured["text"])
        self.assertIn("limits [-1, 1]", captured["text"])
        self.assertIn("relative improvement 10.0%", captured["text"])

    def test_preflight_failure_shows_not_ready_and_motion_failed(self):
        report = type(
            "Report",
            (),
            {"is_ready": False, "as_text": lambda self: "NOT READY\nreadback mismatch"},
        )()
        with patch.object(self.window, "_show_preflight_report") as show_report:
            self.window._on_preflight_finished(report)

        self.assertEqual(
            self.window.status_strip.items["READINESS"].value_label.text(),
            "NOT READY",
        )
        self.assertEqual(
            self.window.status_strip.items["READBACK VERIFIED"].value_label.text(),
            "FAILED",
        )
        self.assertIn("readback mismatch", self.window.log_view.toPlainText())
        show_report.assert_called_once_with(
            "NOT READY\nreadback mismatch",
            ready=False,
        )


if __name__ == "__main__":
    unittest.main()
