import sys
import unittest
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT.parent))

from PyQt5.QtWidgets import QApplication

from half_linac.src.apps.emit_measure.multi_screen import (
    MultiScreenMeasurementSession,
    build_multi_screen_optics,
)
from half_linac.src.apps.emit_measure.multi_screen_workspace import MultiScreenWorkspace
from half_linac.src.shared.beam_diagnostics import analyze_beam_image
from half_linac.src.shared.machine_profile import load_app_context


class _Backend:
    def get_map(self, _source, target, **_kwargs):
        matrix = np.eye(6)
        matrix[0, 1] = matrix[2, 3] = {"PRF07": 1.0, "PRF08": 2.0, "PRF09": 3.0}[target]
        return matrix


class MultiScreenWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])
        cls.context = load_app_context("emit_measure", machine_id="half", control_backend="vm")

    def _workspace(self, image, fit=None):
        if fit is None:
            _, fit = analyze_beam_image(image, extent=(-3.0, 3.0, -3.0, 3.0))

        def reader(_screen):
            return {
                "image": image,
                "fit": fit,
                "extent": (-3.0, 3.0, -3.0, 3.0),
                "pv_sigx": 9.0,
                "pv_sigy": 10.0,
            }

        workspace = MultiScreenWorkspace(self.context, image_reader=reader)
        optics = build_multi_screen_optics(
            _Backend(), "PRF06", ("PRF06", "PRF07", "PRF08", "PRF09")
        )
        workspace.session = MultiScreenMeasurementSession.create(
            machine="half",
            backend="vm",
            preset="half_default",
            model_line="ALL_MAIN",
            energy_mev=2200.0,
            optics=optics,
            target_samples_per_screen=1,
        )
        workspace._set_state("Ready", "test")
        return workspace

    def test_preview_does_not_accept_sample_and_separates_pv_cross_check(self):
        grid = np.indices((40, 40))
        image = 0.01 + np.exp(-((grid[1] - 20) ** 2 + (grid[0] - 20) ** 2) / 32.0)
        workspace = self._workspace(image)
        workspace.preview_sample()
        self.assertEqual(len(workspace.session.acquisition.samples), 0)
        self.assertIn("not used", workspace.pv_cross_check_label.text())
        workspace.acquire_sample()
        self.assertEqual(len(workspace.session.acquisition.samples), 1)
        self.assertEqual(workspace.session.acquisition.samples[0].source, "image")
        self.assertEqual(workspace.samples_table.columnCount(), 8)

    def test_invalid_fit_is_displayed_but_not_accepted(self):
        image = np.zeros((20, 20), dtype=float)
        workspace = self._workspace(image)
        workspace.preview_sample()
        self.assertEqual(len(workspace.session.acquisition.samples), 0)
        workspace.acquire_sample()
        self.assertEqual(len(workspace.session.acquisition.samples), 0)
        self.assertIn("low_signal", workspace.status_label.text())

    def test_archived_session_disables_measurement_actions(self):
        workspace = self._workspace(np.zeros((20, 20), dtype=float))
        workspace._set_state("Archived", "read-only")
        self.assertFalse(workspace.preview_button.isEnabled())
        self.assertFalse(workspace.acquire_button.isEnabled())
        self.assertFalse(workspace.manual_button.isEnabled())
        self.assertFalse(workspace.reconstruct_button.isEnabled())

    def test_auto_refresh_defaults_to_two_seconds_and_stops_when_archived(self):
        workspace = self._workspace(np.zeros((20, 20), dtype=float))
        self.assertTrue(workspace.auto_refresh_checkbox.isChecked())
        self.assertEqual(workspace._auto_refresh_timer.interval(), 2000)
        self.assertTrue(workspace._auto_refresh_timer.isActive())
        workspace._set_state("Archived", "read-only")
        self.assertFalse(workspace._auto_refresh_timer.isActive())

    def test_configuration_change_discards_prepared_session(self):
        workspace = self._workspace(np.zeros((20, 20), dtype=float))
        workspace.energy_spin.setValue(workspace.energy_spin.value() + 1.0)
        self.assertIsNone(workspace.session)
        self.assertEqual(workspace.state, "Configuring")


if __name__ == "__main__":
    unittest.main()
