"""Tests for safe VM lattice element editing."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from half_linac.src.virtual_machine.lattice_editor import (
    LATTICE_EDITOR_STATE_KEY,
    LatticeEditorError,
    apply_lattice_element_settings,
    displayed_element_values,
    editable_lattice_elements,
    element_is_enabled,
    reset_lattice_element_to_design,
)


def _state():
    return {
        "lattice": {
            "CCC": {"NAME": "CCC", "TYPE": "CLEAN", "MODE": "ABSDEV", "TLIMIT": "2.5e-11"},
            "ES01": {"NAME": "ES01", "TYPE": "RCOL", "X_MAX": "0.004", "Y_MAX": "0.016"},
            "COLLI": {"NAME": "COLLI", "TYPE": "ECOL", "X_MAX": "0.006", "Y_MAX": "0.006"},
            "AP": {"NAME": "AP", "TYPE": "MAXAMP", "X_MAX": "0.028", "Y_MAX": "0.018", "ELLIPTICAL": "0"},
            "Q1": {"NAME": "Q1", "TYPE": "QUAD", "K1": "1"},
        },
        "control": {"run_setup": {"p_central_mev": "223.4"}},
        "usedline": ["Q1", "ES01", "CCC", "ES01"],
    }


class LatticeEditorTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.runtime_path = Path(self.temporary_directory.name) / "runtime.json"
        self.design_state = _state()
        self.runtime_path.write_text(json.dumps(self.design_state), encoding="utf-8")
        self.runtime = SimpleNamespace(vm=SimpleNamespace(runtime_json=self.runtime_path))

    def read_state(self):
        return json.loads(self.runtime_path.read_text(encoding="utf-8"))

    def test_lists_supported_elements_in_usedline_then_lattice_order(self):
        self.assertEqual(editable_lattice_elements(self.design_state), ("ES01", "CCC", "AP", "COLLI"))

    def test_apply_and_disable_preserve_values_for_reenable(self):
        updated = apply_lattice_element_settings(
            self.runtime, "ES01", {"X_MAX": 0.005, "Y_MAX": 0.020}, enabled=True
        )
        self.assertEqual(updated["lattice"]["ES01"]["X_MAX"], "0.005")
        self.assertEqual(updated["usedline"], self.design_state["usedline"])
        self.assertEqual(updated["control"], self.design_state["control"])

        disabled = apply_lattice_element_settings(
            self.runtime, "ES01", {"X_MAX": 0.005, "Y_MAX": 0.020}, enabled=False
        )
        self.assertEqual(disabled["lattice"]["ES01"]["X_MAX"], "0")
        self.assertEqual(disabled["lattice"]["ES01"]["Y_MAX"], "0")
        self.assertFalse(element_is_enabled(disabled["lattice"]["ES01"]))
        self.assertEqual(displayed_element_values(disabled, "ES01"), {"X_MAX": 0.005, "Y_MAX": 0.02})

    def test_clean_disable_clears_all_elegant_limit_fields(self):
        state = self.read_state()
        state["lattice"]["CCC"]["XLIMIT"] = "0.001"
        self.runtime_path.write_text(json.dumps(state), encoding="utf-8")
        disabled = apply_lattice_element_settings(
            self.runtime, "CCC", {"TLIMIT": 2.5e-11}, enabled=False
        )
        for field in ("XLIMIT", "XPLIMIT", "YLIMIT", "YPLIMIT", "TLIMIT", "DELTALIMIT"):
            self.assertEqual(disabled["lattice"]["CCC"][field], "0")

    def test_maxamp_updates_shape(self):
        updated = apply_lattice_element_settings(
            self.runtime,
            "AP",
            {"X_MAX": 0.03, "Y_MAX": 0.02, "ELLIPTICAL": True},
            enabled=True,
        )
        self.assertEqual(updated["lattice"]["AP"]["ELLIPTICAL"], "1")

    def test_reset_restores_design_and_removes_editor_metadata(self):
        apply_lattice_element_settings(
            self.runtime, "ES01", {"X_MAX": 0.005, "Y_MAX": 0.020}, enabled=False
        )
        with patch(
            "half_linac.src.virtual_machine.lattice_editor.bootstrap_runtime_state",
            return_value=copy.deepcopy(self.design_state),
        ):
            reset = reset_lattice_element_to_design(self.runtime, "ES01")
        self.assertEqual(reset["lattice"]["ES01"], self.design_state["lattice"]["ES01"])
        self.assertNotIn(LATTICE_EDITOR_STATE_KEY, reset)

    def test_rejects_unsupported_and_invalid_enabled_limits(self):
        with self.assertRaises(LatticeEditorError):
            apply_lattice_element_settings(self.runtime, "Q1", {}, enabled=True)
        with self.assertRaises(LatticeEditorError):
            apply_lattice_element_settings(
                self.runtime, "ES01", {"X_MAX": 0, "Y_MAX": 0.02}, enabled=True
            )


if __name__ == "__main__":
    unittest.main()
