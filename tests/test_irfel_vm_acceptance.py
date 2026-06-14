from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
PARENT = REPO_ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from half_linac.src.shared.elegant_backend.publisher import build_vm_publish_plan
from half_linac.src.shared.machine_profile import (
    REAL_STATUS_NOT_SUPPORTED,
    REAL_STATUS_READ_ONLY,
    REAL_STATUS_WRITE_BLOCKED,
    MachineProfileError,
    get_workflow,
    load_app_context,
    load_profile,
    real_commissioning_status,
    require_workflow_write_allowed,
    validate_machine_profile,
    workflow_writes_allowed,
)


class IRFELVmAcceptanceTests(unittest.TestCase):
    def test_irfel_vm_acceptance_contexts_and_publish_plan(self):
        with patch.dict(
            os.environ,
            {"HALF_LINAC_MACHINE_ID": "irfel", "HALF_LINAC_CONTROL_BACKEND": "vm"},
        ):
            profile = load_profile()
            report = validate_machine_profile()
            contexts = {
                app_name: load_app_context(app_name)
                for app_name in (
                    "orbit_correct",
                    "orbit_display",
                    "beam_monitor",
                    "energy_spectrum",
                    "emit_measure",
                    "bba",
                )
            }

        self.assertEqual(profile.machine.id, "irfel")
        self.assertTrue(report.ok, report.format_text())
        for app_name, context in contexts.items():
            self.assertEqual(context.machine.id, "irfel", app_name)
            self.assertEqual(context.control_backend.name, "vm", app_name)

        plan = build_vm_publish_plan(profile)
        self.assertEqual(len(plan.bpm_specs), 10)
        self.assertEqual(len(plan.watch_image_specs), 6)
        watch_targets = {
            (spec.target_element_id, spec.logical_channel)
            for spec in plan.watch_image_specs
        }
        self.assertIn(("PRF03", "image"), watch_targets)
        self.assertIn(("PRFESA", "image"), watch_targets)

        self.assertTrue(workflow_writes_allowed(contexts["orbit_correct"], "orbit"))
        self.assertTrue(workflow_writes_allowed(contexts["beam_monitor"], "beam_monitor"))
        self.assertTrue(workflow_writes_allowed(contexts["energy_spectrum"], "energy_spectrum"))
        self.assertTrue(workflow_writes_allowed(contexts["emit_measure"], "emit_measure"))

    def test_irfel_real_boundaries_remain_explicit_and_blocked(self):
        profile = load_profile("irfel")
        real_contexts = {
            "orbit": load_app_context(
                "orbit_correct",
                machine_id="irfel",
                control_backend="real",
            ),
            "beam_monitor": load_app_context(
                "beam_monitor",
                machine_id="irfel",
                control_backend="real",
            ),
            "energy_spectrum": load_app_context(
                "energy_spectrum",
                machine_id="irfel",
                control_backend="real",
            ),
            "emit_measure": load_app_context(
                "emit_measure",
                machine_id="irfel",
                control_backend="real",
            ),
        }

        for workflow_name, context in real_contexts.items():
            self.assertFalse(workflow_writes_allowed(context, workflow_name))
            with self.assertRaises(MachineProfileError):
                require_workflow_write_allowed(context, workflow_name, "test write")

        bba_workflow = get_workflow(profile, "bba")
        self.assertEqual(bba_workflow["standard"]["control_backends"], ["vm"])
        self.assertEqual(bba_workflow["bba2"]["control_backends"], ["vm"])
        self.assertEqual(real_commissioning_status(profile, "orbit_display"), REAL_STATUS_READ_ONLY)
        self.assertEqual(real_commissioning_status(profile, "orbit_correct"), REAL_STATUS_WRITE_BLOCKED)
        self.assertEqual(real_commissioning_status(profile, "beam_monitor"), REAL_STATUS_WRITE_BLOCKED)
        self.assertEqual(real_commissioning_status(profile, "energy_spectrum"), REAL_STATUS_WRITE_BLOCKED)
        self.assertEqual(real_commissioning_status(profile, "emit_measure"), REAL_STATUS_WRITE_BLOCKED)
        self.assertEqual(real_commissioning_status(profile, "bba"), REAL_STATUS_NOT_SUPPORTED)


if __name__ == "__main__":
    unittest.main()
