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
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QApplication, QFrame, QLabel, QMessageBox

    from half_linac.src.apps.solenoid_centering.main import MainWindow, ScanFailureReport
    from half_linac.src.apps.solenoid_centering.mplwidget import MplWidget
    from half_linac.src.apps.solenoid_centering.scan import CenteringResult, RestoreOutcome
except ImportError:
    QApplication = None


@unittest.skipIf(QApplication is None, "PyQt5 or Matplotlib Qt backend is not installed")
class SolenoidCenteringMatplotlibCompatibilityTests(unittest.TestCase):
    def test_trajectory_colors_use_supported_colormap_api(self):
        colors = MplWidget._trajectory_colors(3)

        self.assertEqual(len(colors), 3)
        self.assertTrue(all(len(color) == 4 for color in colors))


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
            restore=RestoreOutcome(status="verified"),
        )

    def test_non_actionable_result_disables_apply(self):
        self.window._on_scan_finished(self._result(actionable=False))

        self.assertFalse(self.window.apply_button.isEnabled())
        self.assertTrue(self.window.apply_button.isHidden())
        self.assertTrue(self.window.restore_button.isHidden())
        self.assertEqual(
            self.window.status_strip.items["RESULT QUALITY"].value_label.text(),
            "NO VALID RECOMMENDATION",
        )

    def test_valid_result_shows_only_header_apply_action(self):
        self.window._on_scan_finished(self._result(actionable=True))

        self.assertFalse(self.window.apply_button.isHidden())
        self.assertTrue(self.window.apply_button.isEnabled())
        self.assertTrue(self.window.restore_button.isHidden())
        self.assertEqual(self.window.result_card.layout().count(), 2)

        self.window._set_result_action("restore")

        self.assertTrue(self.window.apply_button.isHidden())
        self.assertFalse(self.window.restore_button.isHidden())
        self.assertTrue(self.window.restore_button.isEnabled())

    def test_control_panel_uses_outer_card_and_compact_scan_labels(self):
        self.assertIsNotNone(self.window.findChild(QFrame, "controlCard"))
        self.assertIs(
            self.window.hcorr_combo.parentWidget(),
            self.window.vcorr_combo.parentWidget(),
        )

        labels = {label.text() for label in self.window.findChildren(QLabel)}
        self.assertIn("Correctors", labels)
        self.assertIn("Devices", labels)
        self.assertIn("Scan", labels)
        self.assertIn("Relative Scan Range", labels)
        self.assertIn("Acquisition", labels)
        self.assertIn("SOL", labels)
        self.assertIn("COR", labels)
        self.assertIn("Samples/Step", labels)
        self.assertIn("Settle Time", labels)
        self.assertNotIn("Solenoid offset min", labels)
        self.assertNotIn("Corrector offset min", labels)
        self.assertIs(
            self.window.scoring_mode_combo.parentWidget(),
            self.window.run_card,
        )
        self.assertIs(self.window.max_iters.parentWidget(), self.window.run_card)

    def test_abort_action_is_visible_only_while_scan_is_running(self):
        self.assertFalse(self.window.start_button.isHidden())
        self.assertTrue(self.window.stop_button.isHidden())

        self.window._set_scan_action_running(True)

        self.assertTrue(self.window.start_button.isHidden())
        self.assertFalse(self.window.stop_button.isHidden())
        self.assertTrue(self.window.stop_button.isEnabled())
        self.assertEqual(self.window.stop_button.text(), "Abort")

        self.window._set_scan_action_running(True, stopping=True)

        self.assertFalse(self.window.stop_button.isEnabled())
        self.assertEqual(self.window.stop_button.text(), "Stopping...")

        self.window._set_scan_action_running(False)

        self.assertFalse(self.window.start_button.isHidden())
        self.assertTrue(self.window.stop_button.isHidden())

    def test_workspace_uses_linked_horizontal_and_vertical_splitters(self):
        self.assertEqual(self.window.splitter.orientation(), Qt.Horizontal)
        self.assertEqual(self.window.splitter.count(), 2)
        self.assertFalse(self.window.splitter.childrenCollapsible())
        self.assertEqual(self.window.workspace_splitter.orientation(), Qt.Vertical)
        self.assertEqual(self.window.workspace_splitter.count(), 2)
        self.assertFalse(self.window.workspace_splitter.childrenCollapsible())
        self.assertIs(self.window.workspace_splitter.widget(0), self.window.plot_card)
        self.assertIs(self.window.workspace_splitter.widget(1), self.window.result_card)
        self.assertGreater(self.window.result_table.maximumHeight(), 220)

    def test_status_strip_is_content_packed_with_trailing_stretch(self):
        layout = self.window.status_strip.layout()

        self.assertIsNotNone(layout.itemAt(layout.count() - 1).spacerItem())
        self.assertEqual(layout.stretch(layout.count() - 1), 1)
        self.assertTrue(
            all(
                layout.stretch(index) == 0
                for index in range(layout.count() - 1)
            )
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

    def test_preflight_failure_shows_not_ready_and_readback_failed(self):
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

    def test_parameter_change_invalidates_successful_preflight(self):
        self.window.preflight_ready = True
        self.window.start_button.setEnabled(True)

        self.window.sol_from.setValue(self.window.sol_from.value() - 0.1)

        self.assertFalse(self.window.preflight_ready)
        self.assertFalse(self.window.start_button.isEnabled())
        self.assertEqual(
            self.window.status_strip.items["READINESS"].value_label.text(),
            "UNCHECKED",
        )

    def test_successful_current_revision_preflight_enables_start(self):
        report = type(
            "Report",
            (),
            {"is_ready": True, "as_text": lambda self: "READY"},
        )()
        self.window.active_preflight_revision = self.window.configuration_revision

        with patch.object(self.window, "_show_preflight_report"):
            self.window._on_preflight_finished(report)
        self.window._on_preflight_done()

        self.assertTrue(self.window.preflight_ready)
        self.assertTrue(self.window.start_button.isEnabled())

    def test_stopped_and_restored_has_structured_log_state(self):
        report = ScanFailureReport(
            status="stopped",
            termination_code="operator_stopped",
            reason="Solenoid centering scan stopped.",
            restore_status="verified",
        )

        with patch(
            "half_linac.src.apps.solenoid_centering.main.QMessageBox.warning"
        ):
            self.window._on_scan_failed(report)

        self.assertEqual(
            self.window.status_strip.items["READINESS"].value_label.text(),
            "STOPPED",
        )
        self.assertEqual(
            self.window.status_strip.items["READBACK VERIFIED"].value_label.text(),
            "VERIFIED",
        )
        log = self.window.log_view.toPlainText()
        self.assertIn("Scan termination: operator_stopped", log)
        self.assertIn("Restore status: VERIFIED", log)

    def test_restore_failure_lists_error_in_log(self):
        report = ScanFailureReport(
            status="restore_failed",
            termination_code="restore_failed",
            reason="Device restore failed",
            restore_status="failed",
            restore_errors=("HCOR (TEST:HCOR:SP): timeout",),
        )

        with patch(
            "half_linac.src.apps.solenoid_centering.main.QMessageBox.warning"
        ):
            self.window._on_scan_failed(report)

        self.assertEqual(
            self.window.status_strip.items["READINESS"].value_label.text(),
            "RESTORE FAILED",
        )
        self.assertIn("HCOR (TEST:HCOR:SP): timeout", self.window.log_view.toPlainText())


if __name__ == "__main__":
    unittest.main()
