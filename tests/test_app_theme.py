import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
PARENT = REPO_ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from half_linac.src.shared.app_theme import (
    INITIAL_THEME_ENV,
    environment_with_initial_theme,
    resolve_initial_theme,
)


class AppThemeTests(unittest.TestCase):
    def test_default_theme_is_dark(self):
        self.assertEqual(resolve_initial_theme(environ={}), "dark")

    def test_valid_initial_theme_is_normalized(self):
        environ = {INITIAL_THEME_ENV: " LIGHT "}
        self.assertEqual(resolve_initial_theme(environ=environ), "light")

    def test_invalid_initial_theme_falls_back_to_default(self):
        environ = {INITIAL_THEME_ENV: "unexpected"}
        self.assertEqual(resolve_initial_theme("light", environ=environ), "light")

    def test_child_environment_does_not_mutate_source(self):
        source = {"EXISTING": "value"}
        child = environment_with_initial_theme("light", environ=source)

        self.assertEqual(source, {"EXISTING": "value"})
        self.assertEqual(child["EXISTING"], "value")
        self.assertEqual(child[INITIAL_THEME_ENV], "light")

    def test_process_environment_is_not_mutated(self):
        with patch.dict(os.environ, {}, clear=True):
            child = environment_with_initial_theme("dark")

            self.assertNotIn(INITIAL_THEME_ENV, os.environ)
            self.assertEqual(child[INITIAL_THEME_ENV], "dark")

    def test_invalid_explicit_theme_is_rejected(self):
        with self.assertRaises(ValueError):
            environment_with_initial_theme("blue")


if __name__ == "__main__":
    unittest.main()
