from __future__ import annotations

import unittest
import sys
from dataclasses import replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PARENT = REPO_ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from half_linac.src.apps.orbit_correct.profile_runtime import effective_corrector_limit
from half_linac.src.apps.bba.profile_runtime import resolve_limited_scan_values
from half_linac.src.apps.emit_measure.profile_runtime import effective_k1_scan_limit
from half_linac.src.shared.machine_profile import (
    LimitRange,
    MachineProfileError,
    effective_limit,
    load_app_context,
)


class LimitRangeTest(unittest.TestCase):
    def test_missing_ranges_are_unbounded(self):
        self.assertEqual(effective_limit(), LimitRange())

    def test_intersection_keeps_narrowest_bounds(self):
        result = effective_limit(
            LimitRange(-10, 10, "A"),
            LimitRange(-5, 5, "A"),
        )
        self.assertEqual(result, LimitRange(-5, 5, "A"))

    def test_relative_range_converts_before_intersection(self):
        application_absolute = LimitRange(-2, 2, "A").relative_to_absolute(9)
        result = effective_limit(application_absolute, LimitRange(0, 10, "A"))
        self.assertEqual(result, LimitRange(7, 10, "A"))
        self.assertEqual(result.absolute_to_relative(9), LimitRange(-2, 1, "A"))

    def test_unit_mismatch_is_rejected(self):
        with self.assertRaisesRegex(MachineProfileError, "units"):
            effective_limit(LimitRange(-1, 1, "A"), LimitRange(-1, 1, "rad"))

    def test_empty_intersection_is_rejected(self):
        with self.assertRaisesRegex(MachineProfileError, "do not overlap"):
            effective_limit(LimitRange(0, 1, "A"), LimitRange(2, 3, "A"))

    def test_non_finite_bound_is_rejected(self):
        with self.assertRaisesRegex(MachineProfileError, "finite"):
            LimitRange(float("inf"), None, "A")


class OrbitCorrectorEffectiveLimitTest(unittest.TestCase):
    def test_real_limit_intersects_application_and_machine_ranges(self):
        context = load_app_context(
            "orbit_correct", machine_id="half", control_backend="real"
        )
        self.assertEqual(
            effective_corrector_limit(context, "XC01", 10, "A"),
            LimitRange(-5, 5, "A"),
        )
        self.assertEqual(
            effective_corrector_limit(context, "XC03", 10, "A"),
            LimitRange(-10, 10, "A"),
        )

    def test_vm_policy_does_not_reuse_current_limit(self):
        context = load_app_context(
            "orbit_correct", machine_id="half", control_backend="vm"
        )
        self.assertEqual(
            effective_corrector_limit(context, "XC01", 0.001, "rad"),
            LimitRange(-0.001, 0.001, "rad"),
        )

    def test_legacy_element_limit_applies_only_to_real_current(self):
        vm_context = load_app_context(
            "orbit_correct", machine_id="irfel", control_backend="vm"
        )
        real_context = load_app_context(
            "orbit_correct", machine_id="irfel", control_backend="real"
        )
        self.assertEqual(
            effective_corrector_limit(vm_context, "HC01", 0.001, "rad"),
            LimitRange(-0.001, 0.001, "rad"),
        )
        self.assertEqual(
            effective_corrector_limit(real_context, "HC01", 20, "A"),
            LimitRange(-10, 10, "A"),
        )


class BBAScanEffectiveLimitTest(unittest.TestCase):
    def test_real_relative_corrector_range_intersects_current_limit(self):
        context = load_app_context("bba", machine_id="half", control_backend="real")
        values = resolve_limited_scan_values(
            context, "XC22", "current_set", -1, 1, 5, "relative", "A", 9.5
        )
        self.assertEqual(values.tolist(), [8.5, 8.875, 9.25, 9.625, 10.0])

    def test_current_outside_machine_limit_is_rejected(self):
        context = load_app_context("bba", machine_id="half", control_backend="real")
        with self.assertRaisesRegex(MachineProfileError, "outside physical limit"):
            resolve_limited_scan_values(
                context, "XC22", "current_set", -1, 1, 5, "relative", "A", 11
            )

    def test_vm_kick_does_not_reuse_real_current_limit(self):
        context = load_app_context("bba", machine_id="half", control_backend="vm")
        values = resolve_limited_scan_values(
            context, "XC22", "kick", -0.001, 0.001, 3, "absolute", "rad", 0
        )
        self.assertEqual(values.tolist(), [-0.001, 0.0, 0.001])

    def test_quad_k1_does_not_reuse_current_limit(self):
        context = load_app_context("bba", machine_id="half", control_backend="real")
        values = resolve_limited_scan_values(
            context, "QT04", "K1", -5, 5, 3, "relative", "1/m^2", -70
        )
        self.assertEqual(values.tolist(), [-75.0, -70.0, -65.0])


class EmitScanEffectiveLimitTest(unittest.TestCase):
    @staticmethod
    def _context_with_k1_limit(low, high):
        context = load_app_context("emit_measure", machine_id="half", control_backend="vm")
        elements = tuple(
            replace(
                element,
                limits={**element.limits, "K1": {"low": low, "high": high, "unit": "1/m^2"}},
            )
            if element.id == "QT02"
            else element
            for element in context.profile.elements
        )
        profile = replace(
            context.profile,
            elements=elements,
            _elements_by_id={element.id: element for element in elements},
        )
        return replace(context, profile=profile)

    def test_absolute_k1_policy_intersects_machine_limit(self):
        context = self._context_with_k1_limit(1.75, 3.0)
        self.assertEqual(
            effective_k1_scan_limit(
                context, "QT02", 1.5, 2.5, "absolute", "1/m^2", 2.0
            ),
            LimitRange(1.75, 2.5, "1/m^2"),
        )

    def test_relative_k1_policy_converts_before_intersection(self):
        context = self._context_with_k1_limit(-6, 6)
        self.assertEqual(
            effective_k1_scan_limit(
                context, "QT02", -2, 2, "relative", "1/m^2", 5
            ),
            LimitRange(3, 6, "1/m^2"),
        )

    def test_emit_k1_does_not_reuse_current_limit(self):
        context = load_app_context("emit_measure", machine_id="half", control_backend="real")
        self.assertEqual(
            effective_k1_scan_limit(
                context, "QT02", 1.5, 2.5, "absolute", "1/m^2", 2
            ),
            LimitRange(1.5, 2.5, "1/m^2"),
        )


if __name__ == "__main__":
    unittest.main()
