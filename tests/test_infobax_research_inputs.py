"""Verify checked-in calibration inputs retain their recorded provenance."""
import hashlib
import json
import unittest
from pathlib import Path


class ResearchInputTests(unittest.TestCase):
    def test_recorded_inputs_match_their_original_sha256(self):
        root = Path(__file__).parent / "fixtures" / "infobax_research"
        manifest = json.loads((root / "manifest.json").read_text())
        for relative, expected in manifest["files_sha256"].items():
            with self.subTest(input=relative):
                self.assertEqual(hashlib.sha256((root / relative).read_bytes()).hexdigest(), expected)


if __name__ == "__main__":
    unittest.main()
