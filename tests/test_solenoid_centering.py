from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
PARENT = REPO_ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from half_linac.src.apps.solenoid_centering import scan
from half_linac.src.shared.machine_profile import (
    MachineProfile,
    MachineProfileError,
    SolenoidCenteringScanRange,
    load_app_context,
    resolve_channel,
)


def _score(value: float) -> scan.ResponseScore:
    return scan.ResponseScore(
        score=value,
        slope_x=value,
        slope_y=0.0,
        offset_x=0.0,
        offset_y=0.0,
        scale_x=1.0,
        scale_y=1.0,
        rms_x=0.0,
        rms_y=0.0,
        residual_rms_x=0.0,
        residual_rms_y=0.0,
        mean_x=0.0,
        mean_y=0.0,
        std_x=0.0,
        std_y=0.0,
    )


class MockIO:
    def __init__(self, values):
        self.values = dict(values)
        self.writes = []

    def read(self, pv_name):
        return self.values[pv_name]

    def write(self, pv_name, value):
        self.values[pv_name] = float(value)
        self.writes.append((pv_name, float(value)))


class SolenoidCenteringTests(unittest.TestCase):
    def test_evaluate_solenoid_response_uses_slope_norm(self):
        result = scan.evaluate_solenoid_response(
            [-1.0, 0.0, 1.0],
            [[-2.0], [0.0], [2.0]],
            [[0.0], [0.0], [0.0]],
        )

        self.assertAlmostEqual(result.slope_x, 2.0)
        self.assertAlmostEqual(result.slope_y, 0.0)
        self.assertAlmostEqual(result.scale_x, 1.0)
        self.assertAlmostEqual(result.score, 2.0)

    def test_evaluate_solenoid_response_uses_noise_scale_when_available(self):
        result = scan.evaluate_solenoid_response(
            [-1.0, 0.0, 1.0],
            [[-2.0, -1.0], [0.0, 1.0], [2.0, 3.0]],
            [[0.0, 0.0], [0.0, 0.0], [0.0, 0.0]],
        )

        self.assertGreater(result.scale_x, 0.0)
        self.assertNotEqual(result.scale_x, 1.0)
        self.assertAlmostEqual(result.scale_y, 1.0)
        self.assertAlmostEqual(result.score, 2.0 / result.scale_x)

    def test_coordinate_descent_keeps_x_y_coupled_score(self):
        scan_range = SolenoidCenteringScanRange(relative_from=-1.0, relative_to=1.0, steps=3)

        def evaluator(axis, round_index, hcorr, vcorr):
            value = (hcorr - 1.0) ** 2 + (vcorr + 2.0) ** 2
            return scan.CandidateResult(
                axis=axis,
                round_index=round_index,
                hcorr=hcorr,
                vcorr=vcorr,
                corrector_value=hcorr if axis == "h" else vcorr,
                solenoid_values=(-1.0, 0.0, 1.0),
                bpm_x_means=(0.0, 0.0, 0.0),
                bpm_y_means=(0.0, 0.0, 0.0),
                score=_score(value),
            )

        hcorr, vcorr, axis_scans = scan.coordinate_descent(
            0.0,
            0.0,
            scan_range,
            2,
            evaluator,
        )

        self.assertAlmostEqual(hcorr, 1.0)
        self.assertAlmostEqual(vcorr, -2.0)
        self.assertEqual(len(axis_scans), 4)

    def test_scanner_restores_devices_when_stopped(self):
        context = load_app_context("solenoid_centering")
        assert context.solenoid_centering_workflow is not None
        preset = context.solenoid_centering_workflow.presets_by_id["ls_centering"]
        hcorr_pv = "HALF:IN:COR:LS:HC:ao"
        vcorr_pv = "HALF:IN:COR:LS:VC:ao"
        bpm_x_pv = resolve_channel(context, preset.bpm, "x")
        bpm_y_pv = resolve_channel(context, preset.bpm, "y")
        io = MockIO(
            {
                preset.solenoid_setpoint_pv: 10.0,
                preset.solenoid_readback_pv: 10.0,
                hcorr_pv: 0.1,
                vcorr_pv: -0.2,
                bpm_x_pv: 0.0,
                bpm_y_pv: 0.0,
            }
        )
        scanner = scan.SolenoidCenteringScanner(
            context,
            preset,
            io=io,
            stop_requested=lambda: True,
        )

        with patch.object(scan, "require_workflow_write_allowed", lambda *args, **kwargs: None):
            with self.assertRaises(scan.StopRequested):
                scanner.run()

        self.assertEqual(io.values[preset.solenoid_setpoint_pv], 10.0)
        self.assertEqual(io.values[hcorr_pv], 0.1)
        self.assertEqual(io.values[vcorr_pv], -0.2)
        self.assertIn((preset.solenoid_setpoint_pv, 10.0), io.writes)
        self.assertIn((hcorr_pv, 0.1), io.writes)
        self.assertIn((vcorr_pv, -0.2), io.writes)

    def test_scanner_preflight_rejects_solenoid_limit_violation_before_writes(self):
        context = load_app_context(
            "solenoid_centering",
            machine_id="irfel",
            control_backend="real",
        )
        assert context.solenoid_centering_workflow is not None
        preset = context.solenoid_centering_workflow.presets_by_id["ms01_centering"]
        hcorr_pv = resolve_channel(context, preset.hcorr, "setpoint")
        vcorr_pv = resolve_channel(context, preset.vcorr, "setpoint")
        bpm_x_pv = resolve_channel(context, preset.bpm, "x")
        bpm_y_pv = resolve_channel(context, preset.bpm, "y")
        io = MockIO(
            {
                preset.solenoid_setpoint_pv: 0.02,
                preset.solenoid_readback_pv: 0.02,
                hcorr_pv: 0.0,
                vcorr_pv: 0.0,
                bpm_x_pv: 0.0,
                bpm_y_pv: 0.0,
            }
        )
        scanner = scan.SolenoidCenteringScanner(context, preset, io=io)

        with self.assertRaisesRegex(MachineProfileError, "outside configured limits"):
            scanner.run()

        self.assertEqual(io.writes, [])

    def test_scanner_preflight_reports_ready_without_writes(self):
        context = load_app_context(
            "solenoid_centering",
            machine_id="irfel",
            control_backend="real",
        )
        assert context.solenoid_centering_workflow is not None
        preset = context.solenoid_centering_workflow.presets_by_id["ms01_centering"]
        hcorr_pv = resolve_channel(context, preset.hcorr, "setpoint")
        vcorr_pv = resolve_channel(context, preset.vcorr, "setpoint")
        bpm_x_pv = resolve_channel(context, preset.bpm, "x")
        bpm_y_pv = resolve_channel(context, preset.bpm, "y")
        io = MockIO(
            {
                preset.solenoid_setpoint_pv: 1.0,
                preset.solenoid_readback_pv: 1.01,
                hcorr_pv: 0.0,
                vcorr_pv: 0.0,
                bpm_x_pv: 0.2,
                bpm_y_pv: -0.3,
            }
        )
        scanner = scan.SolenoidCenteringScanner(context, preset, io=io)

        report = scanner.preflight()

        self.assertTrue(report.is_ready)
        self.assertEqual(report.machine_id, "irfel")
        self.assertEqual(report.backend, "real")
        self.assertEqual(report.preset_id, "ms01_centering")
        self.assertEqual(report.corrector_candidates, 6)
        self.assertEqual(report.solenoid_points, 3)
        self.assertIn("READY", report.as_text())
        self.assertEqual(io.writes, [])

    def test_scanner_preflight_rejects_corrector_limit_violation_before_writes(self):
        context = load_app_context(
            "solenoid_centering",
            machine_id="irfel",
            control_backend="real",
        )
        assert context.solenoid_centering_workflow is not None
        preset = context.solenoid_centering_workflow.presets_by_id["ms01_centering"]
        hcorr_pv = resolve_channel(context, preset.hcorr, "setpoint")
        vcorr_pv = resolve_channel(context, preset.vcorr, "setpoint")
        bpm_x_pv = resolve_channel(context, preset.bpm, "x")
        bpm_y_pv = resolve_channel(context, preset.bpm, "y")
        io = MockIO(
            {
                preset.solenoid_setpoint_pv: 1.0,
                preset.solenoid_readback_pv: 1.0,
                hcorr_pv: 4.99,
                vcorr_pv: 0.0,
                bpm_x_pv: 0.0,
                bpm_y_pv: 0.0,
            }
        )
        scanner = scan.SolenoidCenteringScanner(context, preset, io=io)

        with self.assertRaisesRegex(MachineProfileError, "outside configured limits"):
            scanner.run()

        self.assertEqual(io.writes, [])

    def test_workflow_validation_rejects_wrong_corrector_kind(self):
        profile = {
            "schema_version": "1",
            "machine": {
                "id": "fixture",
                "family": "linac",
                "display_name": "Fixture",
                "default_mode": "vm",
            },
            "control_backends": ["vm"],
            "elements": [
                {
                    "id": "BPM1",
                    "kind": "bpm",
                    "display_name": "BPM1",
                    "order": 1,
                    "tags": [],
                    "limits": {},
                    "channels": {"x": {"vm": "BPM1:X"}, "y": {"vm": "BPM1:Y"}},
                },
                {
                    "id": "HC1",
                    "kind": "bpm",
                    "display_name": "HC1",
                    "order": 2,
                    "tags": [],
                    "limits": {},
                    "channels": {"setpoint": {"vm": "HC1:SP"}},
                },
                {
                    "id": "VC1",
                    "kind": "corr",
                    "display_name": "VC1",
                    "order": 3,
                    "tags": [],
                    "limits": {},
                    "channels": {"setpoint": {"vm": "VC1:SP"}},
                },
            ],
            "workflows": {
                "solenoid_centering": {
                    "presets": [
                        {
                            "id": "bad",
                            "display_name": "Bad",
                            "solenoid_setpoint_pv": "SOL:SP",
                            "hcorr": "HC1",
                            "vcorr": "VC1",
                            "bpm": "BPM1",
                            "solenoid_scan": {
                                "relative_from": -1.0,
                                "relative_to": 1.0,
                                "steps": 3,
                            },
                            "corrector_scan": {
                                "relative_from": -1.0,
                                "relative_to": 1.0,
                                "steps": 3,
                            },
                            "samples_per_point": 1,
                            "settle_time_s": 0.0,
                            "sample_interval_s": 0.0,
                            "max_rounds": 1,
                        }
                    ]
                }
            },
        }

        with self.assertRaises(MachineProfileError):
            MachineProfile.from_dict(profile)


if __name__ == "__main__":
    unittest.main()
