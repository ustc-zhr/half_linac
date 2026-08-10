from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
PARENT = REPO_ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from half_linac.src.apps.energy_spectrum.background_store import (
    BackgroundStoreError,
    load_background,
    save_background,
)


class EnergySpectrumBackgroundStoreTests(unittest.TestCase):
    def test_background_and_metadata_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image_path = root / "background.npy"
            metadata_path = root / "background.json"
            expected = np.arange(12, dtype=float).reshape(3, 4)

            save_background(
                expected,
                image_path,
                metadata_path,
                {"machine_id": "irfel", "exposure_s": 0.2},
            )
            actual, metadata = load_background(
                image_path,
                metadata_path,
                expected_shape=(3, 4),
            )

            np.testing.assert_array_equal(actual, expected)
            self.assertEqual(metadata["machine_id"], "irfel")
            self.assertEqual(metadata["exposure_s"], 0.2)

    def test_background_shape_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image_path = root / "background.npy"
            metadata_path = root / "background.json"
            save_background(np.zeros((3, 4)), image_path, metadata_path, {})

            with self.assertRaisesRegex(BackgroundStoreError, "does not match"):
                load_background(
                    image_path,
                    metadata_path,
                    expected_shape=(4, 3),
                )

    def test_non_finite_background_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            image = np.zeros((3, 4))
            image[1, 1] = np.nan

            with self.assertRaisesRegex(BackgroundStoreError, "non-finite"):
                save_background(
                    image,
                    Path(temp_dir) / "background.npy",
                    Path(temp_dir) / "background.json",
                    {},
                )


if __name__ == "__main__":
    unittest.main()
