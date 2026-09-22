import sys
import json
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from types import SimpleNamespace
from PyQt5.QtCore import Qt

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT.parent))

from PyQt5.QtWidgets import QApplication, QDialog

from half_linac.src.apps.emit_measure.multi_screen import (
    BeamSizeSample,
    MultiScreenMeasurementSession,
    load_multi_screen_archive,
    save_multi_screen_archive,
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
        self.addCleanup(workspace.stop)
        self.addCleanup(workspace.close)
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
        self.assertEqual(workspace.samples_table.columnCount(), 6)

    def test_failed_previous_screen_does_not_reject_current_screen(self):
        axis = np.linspace(-3, 3, 81)
        image = np.outer(np.exp(-axis**2 / .32), np.exp(-axis**2 / .32))
        workspace = self._workspace(image)
        calls = []
        def reader(screen):
            calls.append(screen)
            return {"image": np.zeros_like(image) if screen == "PRF07" else image,
                    "extent": (-3, 3, -3, 3)}
        workspace.image_reader = reader
        workspace.screen_list.setCurrentRow(1)
        workspace.acquire_sample()
        workspace.screen_list.setCurrentRow(2)
        workspace.acquire_sample()
        self.assertEqual(calls, ["PRF07", "PRF08"])
        self.assertEqual(workspace.session.acquisition.sample_counts["PRF08"], 1)
        self.assertIn("PRF08", workspace.beam_fit_summary_label.text())
        self.assertIn("X: usable", workspace.beam_fit_summary_label.text())

    def test_switch_after_five_samples_and_retry_failed_fit(self):
        axis = np.linspace(-3, 3, 81)
        image = np.outer(np.exp(-axis**2 / 0.32), np.exp(-axis**2 / 0.32))
        workspace = self._workspace(image)
        workspace.session = replace(workspace.session, acquisition=replace(
            workspace.session.acquisition, target_samples_per_screen=5))
        for _ in range(5):
            workspace.acquire_sample()
        self.assertEqual(workspace.session.acquisition.sample_counts["PRF06"], 5)
        workspace.screen_list.setCurrentRow(1)
        self.assertEqual(workspace._screen_id(workspace.screen_list.currentItem()), "PRF07")
        reader = workspace.image_reader
        workspace.image_reader = lambda screen: {"image": np.zeros_like(image), "extent": (-3, 3, -3, 3)}
        workspace.acquire_sample()
        self.assertEqual(workspace.session.acquisition.sample_counts["PRF07"], 0)
        self.assertTrue(workspace.acquire_button.isEnabled())
        workspace.image_reader = reader
        workspace.acquire_sample()
        self.assertEqual(workspace.session.acquisition.sample_counts["PRF06"], 5)
        self.assertEqual(workspace.session.acquisition.sample_counts["PRF07"], 1)

    def test_width_method_controls_preview_sampling_and_archive(self):
        axis = np.linspace(-3, 3, 201)
        profile = np.exp(-axis**2 / .08) + .08 * np.exp(-axis**2 / .8)
        image = np.outer(profile, profile)
        workspace = self._workspace(image)
        workspace.image_reader = lambda _screen: {
            "image": image, "extent": (-3., 3., -3., 3.),
        }
        workspace.preview_sample()
        gaussian_width = workspace._last_fit.sigx_mm
        workspace._set_beam_width_method("RMS moments")
        rms_width = workspace._last_fit.sigx_mm
        self.assertGreater(rms_width, gaussian_width * 1.3)
        self.assertIn("RMS moments", workspace.beam_fit_summary_label.text())
        self.assertIsNone(workspace._last_fit.x_projection.fitted_projection)
        workspace.acquire_sample()
        sample = workspace.session.acquisition.samples[0]
        self.assertAlmostEqual(sample.sigma_x_m * 1000, rms_width)
        self.assertEqual(sample.quality["beam_width_method"], "RMS moments")
        workspace._set_beam_width_method("Gaussian fit")
        self.assertEqual(workspace.beam_width_method, "RMS moments")
        with TemporaryDirectory() as directory:
            path = save_multi_screen_archive(Path(directory) / "width.json", workspace.session)
            restored = load_multi_screen_archive(path)
            self.assertEqual(restored.beam_width_method, "RMS moments")
            workspace.session = restored
            workspace.beam_width_method = "Gaussian fit"
            workspace._load_session_controls()
            self.assertEqual(workspace.beam_width_method, "RMS moments")
            payload = json.loads(path.read_text())
            del payload["beam_width_method"]
            path.write_text(json.dumps(payload))
            self.assertEqual(load_multi_screen_archive(path).beam_width_method, "Gaussian fit")

    def test_vmin_fit_control_is_frozen_with_multi_screen_samples(self):
        axis = np.linspace(-3, 3, 201)
        profile = np.exp(-axis**2 / .08) + .08 * np.exp(-axis**2 / .8)
        image = np.outer(profile, profile)
        workspace = self._workspace(image)
        workspace.image_reader = lambda _screen: {
            "image": image, "extent": (-3., 3., -3., 3.),
        }
        workspace.width_method_combo.setCurrentIndex(1)
        workspace.preview_sample()
        full_width = workspace._last_fit.sigx_mm
        workspace.beam_image_vmin = 0.03
        workspace.fit_intensity_checkbox.setChecked(True)
        self.assertLess(workspace._last_fit.sigx_mm, full_width)
        workspace.acquire_sample()
        self.assertEqual(workspace.session.fit_vmin, 0.03)
        self.assertFalse(workspace.fit_intensity_checkbox.isEnabled())
        with TemporaryDirectory() as directory:
            path = save_multi_screen_archive(Path(directory) / "threshold.json", workspace.session)
            self.assertEqual(load_multi_screen_archive(path).fit_vmin, 0.03)

    def test_background_apply_control_lives_in_manage_dialog(self):
        workspace = self._workspace(np.zeros((20, 20)))
        with patch.object(QDialog, "exec_", return_value=0):
            workspace._show_background_info()
        self.assertIs(
            workspace.beam_image_background_checkbox.parentWidget(),
            workspace.background_dialog,
        )
        self.assertFalse(workspace.beam_image_background_checkbox.isChecked())

    def test_invalid_fit_is_displayed_but_not_accepted(self):
        image = np.zeros((20, 20), dtype=float)
        workspace = self._workspace(image)
        workspace.preview_sample()
        self.assertEqual(len(workspace.session.acquisition.samples), 0)
        workspace.acquire_sample()
        self.assertEqual(len(workspace.session.acquisition.samples), 1)
        self.assertFalse(workspace.session.acquisition.samples[0].enabled)
        self.assertEqual(workspace.session.acquisition.sample_counts["PRF06"], 0)
        self.assertFalse(workspace.samples_table.item(0, 0).flags() & Qt.ItemIsEnabled)
        self.assertIn("low_signal", workspace.status_label.text())

    def test_archived_session_disables_measurement_actions(self):
        workspace = self._workspace(np.zeros((20, 20), dtype=float))
        workspace._set_state("Archived", "read-only")
        self.assertFalse(workspace.auto_refresh_checkbox.isEnabled())
        self.assertFalse(workspace.acquire_button.isEnabled())
        self.assertFalse(hasattr(workspace, "manual_button"))
        self.assertFalse(workspace.reconstruct_button.isEnabled())

    def test_auto_refresh_defaults_to_two_seconds_and_stops_when_archived(self):
        workspace = self._workspace(np.zeros((20, 20), dtype=float))
        self.assertTrue(workspace.auto_refresh_checkbox.isChecked())
        self.assertEqual(workspace._auto_refresh_timer.interval(), 2000)
        self.assertTrue(workspace._auto_refresh_timer.isActive())
        workspace._set_state("Archived", "read-only")
        self.assertFalse(workspace._auto_refresh_timer.isActive())

    def test_enabling_auto_refresh_previews_immediately(self):
        workspace = self._workspace(np.zeros((20, 20), dtype=float))
        workspace.auto_refresh_checkbox.setChecked(False)
        with patch.object(workspace, "_auto_refresh_current_image") as refresh:
            workspace.auto_refresh_checkbox.setChecked(True)
            refresh.assert_called_once_with()

    def test_configuration_change_discards_prepared_session(self):
        workspace = self._workspace(np.zeros((20, 20), dtype=float))
        workspace.energy_spin.setValue(workspace.energy_spin.value() + 1.0)
        self.assertIsNone(workspace.session)
        self.assertEqual(workspace.state, "Configuring")

    def _complete_workspace(self):
        workspace = self._workspace(np.zeros((20, 20)))
        optics = workspace.session.optics
        x = np.sqrt(optics.x_measurement_matrix @ np.array([4e-6, -0.3e-6, 0.5e-6]))
        y = np.sqrt(optics.y_measurement_matrix @ np.array([3e-6, 0.2e-6, 0.4e-6]))
        for screen, sx, sy in zip(optics.observation_elements, x, y):
            workspace._accept_sample(screen, sx, sy, "test")
        return workspace

    def test_exclude_restore_and_replacement_invalidate_result(self):
        workspace = self._complete_workspace()
        with patch.object(workspace, "_write_runtime_archives", return_value=None):
            workspace.reconstruct()
        self.assertTrue(workspace.reconstruction.valid)
        original = workspace.session.acquisition.samples[0]
        workspace.samples_table.item(0, 0).setCheckState(Qt.Unchecked)
        self.assertIsNone(workspace.reconstruction)
        self.assertFalse(workspace.reconstruct_button.isEnabled())
        self.assertTrue(workspace.acquire_button.isEnabled())
        self.assertIn("PRF06 0/1", workspace.status_label.text())
        self.assertEqual(workspace._screen_id(workspace.screen_list.item(0)), "PRF06")
        self.assertIn("0/1", workspace.screen_list.item(0).text())
        workspace._accept_sample("PRF06", original.sigma_x_m, original.sigma_y_m, "test")
        self.assertEqual(len(workspace.session.acquisition.samples), 5)
        self.assertTrue(workspace.reconstruct_button.isEnabled())
        self.assertFalse(workspace.session.acquisition.samples[0].enabled)
        workspace._restore_samples()
        self.assertEqual(workspace.session.acquisition.sample_counts["PRF06"], 2)

    def test_recalculate_checked_samples_below_sampling_target(self):
        workspace = self._complete_workspace()
        acquisition = workspace.session.acquisition
        original = acquisition.samples[0]
        acquisition = acquisition.add_sample(replace(original, sigma_x_m=original.sigma_x_m * 2))
        workspace.session = replace(workspace.session, acquisition=replace(
            acquisition, target_samples_per_screen=5))
        workspace._samples_changed()
        workspace.samples_table.item(4, 0).setCheckState(Qt.Unchecked)
        self.assertFalse(workspace.session.acquisition.complete)
        self.assertTrue(workspace.recalculate_button.isEnabled())
        self.assertIn("Recalculate available", workspace.status_label.text())
        data = workspace.session.acquisition.aggregate(require_target=False)
        self.assertEqual(data.estimates[0].sample_count, 1)
        self.assertEqual(data.estimates[0].sigma_x_m, original.sigma_x_m)
        with patch.object(workspace, "_write_runtime_archives", return_value=None):
            workspace.recalculate_button.click()
        self.assertTrue(workspace.reconstruction.valid)
        workspace.samples_table.item(0, 0).setCheckState(Qt.Unchecked)
        self.assertFalse(workspace.recalculate_button.isEnabled())
        with self.assertRaisesRegex(ValueError, "incomplete"):
            workspace.session.acquisition.aggregate(require_target=False)

    def test_plot_selection_and_exclude_button(self):
        workspace = self._complete_workspace()
        artist = next(artist for artist in workspace.sample_axes[0].collections
                      if getattr(artist, "_sample_rows", None) == [2])
        workspace._sample_picked(SimpleNamespace(artist=artist, ind=[0]))
        self.assertEqual(workspace.samples_table.currentRow(), 2)
        workspace.exclude_samples_button.click()
        self.assertFalse(workspace.session.acquisition.samples[2].enabled)
        self.assertEqual(workspace.samples_table.rowCount(), 4)

    def test_archive_review_can_reconstruct_without_acquiring_or_auto_saving(self):
        workspace = self._complete_workspace()
        with TemporaryDirectory() as directory:
            path = save_multi_screen_archive(Path(directory) / "source.json", workspace.session)
            source = path.read_bytes()
            with patch("half_linac.src.apps.emit_measure.multi_screen_workspace.QFileDialog.getOpenFileName",
                       return_value=(str(path), "JSON")):
                workspace.load_archive()
            self.assertEqual(workspace.review_tabs.currentIndex(), 1)
            workspace.samples_table.item(0, 0).setCheckState(Qt.Unchecked)
            self.assertFalse(workspace.acquire_button.isEnabled())
            workspace._restore_samples()
            with patch.object(workspace, "_read_image_payload") as read, patch.object(workspace, "_write_runtime_archives") as save:
                workspace.acquire_sample()
                workspace.preview_sample()
                workspace.reconstruct()
                read.assert_not_called()
                save.assert_not_called()
            self.assertTrue(workspace.reconstruction.valid)
            self.assertFalse(workspace._auto_refresh_timer.isActive())
            workspace.samples_table.item(0, 0).setCheckState(Qt.Unchecked)
            output = Path(directory) / "review.json"
            with patch("half_linac.src.apps.emit_measure.multi_screen_workspace.QFileDialog.getSaveFileName",
                       return_value=(str(output), "JSON")):
                workspace.save_archive()
            self.assertFalse(load_multi_screen_archive(output).acquisition.samples[0].enabled)
            self.assertEqual(path.read_bytes(), source)

    def test_finite_quality_rejection_is_recorded_and_can_be_reviewed(self):
        workspace = self._workspace(np.ones((20, 20)))
        workspace.image_reader = lambda screen: (0.4, 0.2)
        # Supply a valid legacy fit and force a quality rejection.
        from half_linac.src.apps.emit_measure.multi_screen_workspace import _SyntheticFit
        fit = _SyntheticFit(0.4, 0.2)
        with patch.object(workspace, "_read_image_payload", return_value={"fit": fit}), \
             patch.object(workspace, "_display_payload"), \
             patch.object(workspace, "_fit_quality", return_value={"x_status": "poor_fit", "y_status": "usable"}):
            workspace.acquire_sample()
        sample = workspace.session.acquisition.samples[0]
        self.assertFalse(sample.enabled)
        self.assertTrue(sample.quality["rejected"])
        workspace.samples_table.item(0, 0).setCheckState(Qt.Checked)
        self.assertEqual(workspace.session.acquisition.sample_counts["PRF06"], 1)


if __name__ == "__main__":
    unittest.main()
