from __future__ import annotations

import sys
import unittest
from dataclasses import replace
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
    SolenoidCenteringMotionVerification,
    SolenoidCenteringScanRange,
    load_app_context,
    resolve_corrector_write_channel,
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


def _candidate(score: float, *, axis="h", hcorr=0.0, vcorr=0.0) -> scan.CandidateResult:
    return scan.CandidateResult(
        axis=axis,
        round_index=0,
        hcorr=hcorr,
        vcorr=vcorr,
        corrector_value=hcorr if axis == "h" else vcorr,
        solenoid_values=(-1.0, 0.0, 1.0),
        bpm_x_means=(0.0, 0.0, 0.0),
        bpm_y_means=(0.0, 0.0, 0.0),
        score=_score(score),
    )


def _solenoid_setpoint_pv(context, preset) -> str:
    if preset.solenoid:
        return resolve_channel(context, preset.solenoid, "current_set")
    assert preset.solenoid_setpoint_pv is not None
    return preset.solenoid_setpoint_pv


def _solenoid_readback_pv(context, preset) -> str | None:
    if preset.solenoid:
        return resolve_channel(context, preset.solenoid, "current_readback")
    return preset.solenoid_readback_pv


class MockIO:
    def __init__(self, values, *, track_readbacks=True, fail_writes=()):
        self.values = dict(values)
        self.writes = []
        self.track_readbacks = track_readbacks
        self.fail_writes = set(fail_writes)

    def read(self, pv_name):
        if pv_name not in self.values and pv_name.endswith(":ai"):
            setpoint_pv = pv_name[:-2] + "ao"
            if setpoint_pv in self.values:
                return self.values[setpoint_pv]
        return self.values[pv_name]

    def write(self, pv_name, value):
        if pv_name in self.fail_writes:
            raise ValueError(f"simulated write failure: {pv_name}")
        self.values[pv_name] = float(value)
        if self.track_readbacks and pv_name.endswith(":ao"):
            readback_pv = pv_name[:-2] + "ai"
            if readback_pv in self.values:
                self.values[readback_pv] = float(value)
        self.writes.append((pv_name, float(value)))


def _verified_preset(preset):
    return replace(
        preset,
        motion_verification=SolenoidCenteringMotionVerification(
            solenoid_readback_tolerance=0.01,
            corrector_readback_tolerance=0.01,
            readback_timeout_s=0.1,
            poll_interval_s=0.001,
        ),
    )


def _with_limits(context, preset, *, solenoid=(-20.0, 20.0), corrector=(-10.0, 10.0)):
    replacements = {
        preset.solenoid: solenoid,
        preset.hcorr: corrector,
        preset.vcorr: corrector,
    }
    elements = tuple(
        replace(element, limits={"low": replacements[element.id][0], "high": replacements[element.id][1]})
        if element.id in replacements
        else element
        for element in context.profile.elements
    )
    return replace(
        context,
        profile=replace(
            context.profile,
            elements=elements,
            _elements_by_id={element.id: element for element in elements},
        ),
    )


def _ready_fixture(*, settle_time_s=0.0, sample_interval_s=0.0):
    context = load_app_context(
        "solenoid_centering",
        machine_id="irfel",
        control_backend="real",
    )
    assert context.solenoid_centering_workflow is not None
    preset = replace(
        _verified_preset(context.solenoid_centering_workflow.presets_by_id["ms01_centering"]),
        settle_time_s=settle_time_s,
        sample_interval_s=sample_interval_s,
    )
    context = _with_limits(context, preset)
    solenoid_pv = _solenoid_setpoint_pv(context, preset)
    solenoid_readback_pv = _solenoid_readback_pv(context, preset)
    hcorr_pv = resolve_corrector_write_channel(context, preset.hcorr)
    vcorr_pv = resolve_corrector_write_channel(context, preset.vcorr)
    bpm_x_pv = resolve_channel(context, preset.bpm, "x")
    bpm_y_pv = resolve_channel(context, preset.bpm, "y")
    values = {
        solenoid_pv: 1.0,
        solenoid_readback_pv: 1.0,
        hcorr_pv: 0.0,
        vcorr_pv: 0.0,
        bpm_x_pv: 0.2,
        bpm_y_pv: -0.3,
    }
    return context, preset, values


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
        self.assertEqual(result.mode, scan.SCORING_MODE_SLOPE)
        self.assertAlmostEqual(result.slope_score, 2.0)
        self.assertAlmostEqual(result.trajectory_length, 4.0)

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

    def test_evaluate_solenoid_response_can_rank_by_trajectory_length(self):
        result = scan.evaluate_solenoid_response(
            [-1.0, 0.0, 1.0],
            [[0.0], [3.0], [3.0]],
            [[0.0], [4.0], [8.0]],
            scoring_mode="trajectory-length",
        )

        self.assertEqual(result.mode, scan.SCORING_MODE_TRAJECTORY_LENGTH)
        self.assertAlmostEqual(result.trajectory_length, 9.0)
        self.assertAlmostEqual(result.score, 9.0)
        self.assertGreater(result.slope_score, 0.0)

    def test_normalize_scoring_mode_rejects_unknown_mode(self):
        with self.assertRaisesRegex(ValueError, "Unsupported scoring mode"):
            scan.normalize_scoring_mode("area")

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
        context = load_app_context(
            "solenoid_centering",
            machine_id="irfel",
            control_backend="real",
        )
        assert context.solenoid_centering_workflow is not None
        preset = _verified_preset(
            context.solenoid_centering_workflow.presets_by_id["ms01_centering"]
        )
        context = _with_limits(context, preset)
        solenoid_pv = _solenoid_setpoint_pv(context, preset)
        solenoid_readback_pv = _solenoid_readback_pv(context, preset)
        hcorr_pv = resolve_corrector_write_channel(context, preset.hcorr)
        vcorr_pv = resolve_corrector_write_channel(context, preset.vcorr)
        bpm_x_pv = resolve_channel(context, preset.bpm, "x")
        bpm_y_pv = resolve_channel(context, preset.bpm, "y")
        io = MockIO(
            {
                solenoid_pv: 10.0,
                solenoid_readback_pv: 10.0,
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

        self.assertEqual(io.values[solenoid_pv], 10.0)
        self.assertEqual(io.values[hcorr_pv], 0.1)
        self.assertEqual(io.values[vcorr_pv], -0.2)
        self.assertIn((solenoid_pv, 10.0), io.writes)
        self.assertIn((hcorr_pv, 0.1), io.writes)
        self.assertIn((vcorr_pv, -0.2), io.writes)

    def test_scanner_preflight_rejects_solenoid_limit_violation_before_writes(self):
        context = load_app_context(
            "solenoid_centering",
            machine_id="irfel",
            control_backend="real",
        )
        assert context.solenoid_centering_workflow is not None
        preset = _verified_preset(
            context.solenoid_centering_workflow.presets_by_id["ms01_centering"]
        )
        context = _with_limits(context, preset)
        solenoid_element = context.profile.get_element(preset.solenoid)
        limited_solenoid = replace(solenoid_element, limits={"low": 0.0, "high": 1.0})
        elements_by_id = dict(context.profile._elements_by_id)
        elements_by_id[limited_solenoid.id] = limited_solenoid
        profile = replace(
            context.profile,
            elements=tuple(
                limited_solenoid if item.id == limited_solenoid.id else item
                for item in context.profile.elements
            ),
            _elements_by_id=elements_by_id,
        )
        context = replace(context, profile=profile)
        solenoid_pv = _solenoid_setpoint_pv(context, preset)
        solenoid_readback_pv = _solenoid_readback_pv(context, preset)
        hcorr_pv = resolve_channel(context, preset.hcorr, "setpoint")
        vcorr_pv = resolve_channel(context, preset.vcorr, "setpoint")
        bpm_x_pv = resolve_channel(context, preset.bpm, "x")
        bpm_y_pv = resolve_channel(context, preset.bpm, "y")
        io = MockIO(
            {
                solenoid_pv: 0.98,
                solenoid_readback_pv: 0.98,
                hcorr_pv: 0.0,
                vcorr_pv: 0.0,
                bpm_x_pv: 0.0,
                bpm_y_pv: 0.0,
            }
        )
        scanner = scan.SolenoidCenteringScanner(context, preset, io=io)

        report = scanner.preflight()

        self.assertFalse(report.is_ready)
        self.assertIn("OUT OF LIMIT", report.as_text())
        self.assertEqual(io.writes, [])

    def test_scanner_preflight_reports_ready_without_writes(self):
        context = load_app_context(
            "solenoid_centering",
            machine_id="irfel",
            control_backend="real",
        )
        assert context.solenoid_centering_workflow is not None
        preset = _verified_preset(
            context.solenoid_centering_workflow.presets_by_id["ms01_centering"]
        )
        context = _with_limits(context, preset)
        solenoid_pv = _solenoid_setpoint_pv(context, preset)
        solenoid_readback_pv = _solenoid_readback_pv(context, preset)
        hcorr_pv = resolve_channel(context, preset.hcorr, "setpoint")
        vcorr_pv = resolve_channel(context, preset.vcorr, "setpoint")
        bpm_x_pv = resolve_channel(context, preset.bpm, "x")
        bpm_y_pv = resolve_channel(context, preset.bpm, "y")
        io = MockIO(
            {
                solenoid_pv: 1.0,
                solenoid_readback_pv: 1.005,
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
        self.assertEqual(report.corrector_candidates, 11)
        self.assertEqual(report.solenoid_points, 5)
        self.assertIn("READY", report.as_text())
        self.assertIn("first-round", report.as_text())
        self.assertEqual(io.writes, [])

    def test_scanner_preflight_rejects_corrector_limit_violation_before_writes(self):
        context = load_app_context(
            "solenoid_centering",
            machine_id="irfel",
            control_backend="real",
        )
        assert context.solenoid_centering_workflow is not None
        preset = _verified_preset(
            context.solenoid_centering_workflow.presets_by_id["ms01_centering"]
        )
        solenoid_pv = _solenoid_setpoint_pv(context, preset)
        solenoid_readback_pv = _solenoid_readback_pv(context, preset)
        hcorr_pv = resolve_channel(context, preset.hcorr, "setpoint")
        vcorr_pv = resolve_channel(context, preset.vcorr, "setpoint")
        bpm_x_pv = resolve_channel(context, preset.bpm, "x")
        bpm_y_pv = resolve_channel(context, preset.bpm, "y")
        io = MockIO(
            {
                solenoid_pv: 1.0,
                solenoid_readback_pv: 1.0,
                hcorr_pv: 9.99,
                vcorr_pv: 0.0,
                bpm_x_pv: 0.0,
                bpm_y_pv: 0.0,
            }
        )
        scanner = scan.SolenoidCenteringScanner(context, preset, io=io)

        report = scanner.preflight()

        self.assertFalse(report.is_ready)
        self.assertIn("OUT OF LIMIT", report.as_text())
        self.assertEqual(io.writes, [])

    def test_scanner_reports_each_candidate_and_preserves_selected_scoring_mode(self):
        context = load_app_context(
            "solenoid_centering",
            machine_id="irfel",
            control_backend="real",
        )
        assert context.solenoid_centering_workflow is not None
        preset = replace(
            _verified_preset(
                context.solenoid_centering_workflow.presets_by_id["ms01_centering"]
            ),
            settle_time_s=0.0,
            sample_interval_s=0.0,
        )
        context = _with_limits(context, preset)
        solenoid_pv = _solenoid_setpoint_pv(context, preset)
        solenoid_readback_pv = _solenoid_readback_pv(context, preset)
        hcorr_pv = resolve_corrector_write_channel(context, preset.hcorr)
        vcorr_pv = resolve_corrector_write_channel(context, preset.vcorr)
        bpm_x_pv = resolve_channel(context, preset.bpm, "x")
        bpm_y_pv = resolve_channel(context, preset.bpm, "y")
        io = MockIO(
            {
                solenoid_pv: 1.0,
                solenoid_readback_pv: 1.0,
                hcorr_pv: 0.0,
                vcorr_pv: 0.0,
                bpm_x_pv: 0.2,
                bpm_y_pv: -0.3,
            }
        )
        candidates = []
        scanner = scan.SolenoidCenteringScanner(
            context,
            preset,
            io=io,
            scoring_mode=scan.SCORING_MODE_TRAJECTORY_LENGTH,
            candidate_finished=candidates.append,
        )

        with (
            patch.object(scan, "require_workflow_write_allowed", lambda *args, **kwargs: None),
            patch.object(scan, "write_scan_result"),
        ):
            result = scanner.run()

        self.assertEqual(result.scoring_mode, scan.SCORING_MODE_TRAJECTORY_LENGTH)
        self.assertEqual(len(candidates), 11)
        self.assertTrue(
            all(
                candidate.score.mode == scan.SCORING_MODE_TRAJECTORY_LENGTH
                for candidate in candidates
            )
        )

    def test_missing_motion_verification_blocks_scan_before_writes(self):
        context, preset, values = _ready_fixture()
        preset = replace(preset, motion_verification=None)
        io = MockIO(values)
        scanner = scan.SolenoidCenteringScanner(context, preset, io=io)

        with patch.object(scan, "require_workflow_write_allowed", lambda *args, **kwargs: None):
            with self.assertRaises(MachineProfileError):
                scanner.run()

        self.assertEqual(io.writes, [])

    def test_missing_solenoid_limit_blocks_scan_before_writes(self):
        context, preset, values = _ready_fixture()
        elements = tuple(
            replace(element, limits={}) if element.id == preset.solenoid else element
            for element in context.profile.elements
        )
        context = replace(
            context,
            profile=replace(
                context.profile,
                elements=elements,
                _elements_by_id={element.id: element for element in elements},
            ),
        )
        io = MockIO(values)
        scanner = scan.SolenoidCenteringScanner(context, preset, io=io)

        report = scanner.preflight()
        self.assertFalse(report.is_ready)
        self.assertIn("LIMIT UNCONFIGURED solenoid", report.as_text())
        with patch.object(scan, "require_workflow_write_allowed", lambda *args, **kwargs: None):
            with self.assertRaises(MachineProfileError):
                scanner.run()
        self.assertEqual(io.writes, [])

    def test_preflight_rejects_readback_deviation(self):
        context, preset, values = _ready_fixture()
        values[_solenoid_readback_pv(context, preset)] = 1.2
        report = scan.SolenoidCenteringScanner(context, preset, io=MockIO(values)).preflight()

        self.assertFalse(report.is_ready)
        self.assertIn("NOT VERIFIED solenoid", report.as_text())

    def test_result_quality_requires_five_percent_improvement(self):
        context, preset, values = _ready_fixture()
        scanner = scan.SolenoidCenteringScanner(context, preset, io=MockIO(values))

        improvement, available, status = scanner._recommendation_quality(
            _candidate(100.0),
            _candidate(96.0),
        )
        self.assertAlmostEqual(improvement, 0.04)
        self.assertFalse(available)
        self.assertIn("below required", status)

        improvement, available, status = scanner._recommendation_quality(
            _candidate(100.0),
            _candidate(94.0),
        )
        self.assertAlmostEqual(improvement, 0.06)
        self.assertTrue(available)
        self.assertEqual(status, "quality gate passed")

    def test_apply_rejects_machine_state_drift(self):
        context, preset, values = _ready_fixture()
        io = MockIO(values)
        scanner = scan.SolenoidCenteringScanner(context, preset, io=io)
        result = scan.CenteringResult(
            preset_id=preset.id,
            original_solenoid=1.0,
            original_hcorr=0.0,
            original_vcorr=0.0,
            recommended_hcorr=1.0,
            recommended_vcorr=1.0,
            best_score=1.0,
            axis_scans=(),
            recommendation_available=True,
            recommendation_status="quality gate passed",
        )
        io.values[scanner.hcorr_pv] = 0.5

        with patch.object(scan, "require_workflow_write_allowed", lambda *args, **kwargs: None):
            with self.assertRaises(scan.StateDriftError):
                scanner.apply_recommended(result)

        self.assertEqual(io.writes, [])

    def test_apply_rolls_back_hcorr_when_vcorr_write_fails(self):
        context, preset, values = _ready_fixture()

        class FailTargetMockIO(MockIO):
            def write(self, pv_name, value):
                if pv_name == scanner.vcorr_pv and float(value) == 2.0:
                    raise ValueError("simulated VCOR failure")
                super().write(pv_name, value)

        io = FailTargetMockIO(values)
        scanner = scan.SolenoidCenteringScanner(context, preset, io=io)
        result = scan.CenteringResult(
            preset_id=preset.id,
            original_solenoid=1.0,
            original_hcorr=0.0,
            original_vcorr=0.0,
            recommended_hcorr=1.0,
            recommended_vcorr=2.0,
            best_score=1.0,
            axis_scans=(),
            recommendation_available=True,
            recommendation_status="quality gate passed",
        )

        with patch.object(scan, "require_workflow_write_allowed", lambda *args, **kwargs: None):
            with self.assertRaisesRegex(scan.MotionVerificationError, "rollback succeeded"):
                scanner.apply_recommended(result)

        self.assertEqual(io.values[scanner.hcorr_pv], 0.0)
        self.assertEqual(io.values[scanner.vcorr_pv], 0.0)

    def test_result_archive_payload_includes_safety_audit_fields(self):
        context, preset, values = _ready_fixture()
        io = MockIO(values)
        scanner = scan.SolenoidCenteringScanner(context, preset, io=io)
        archived = {}

        with (
            patch.object(scan, "require_workflow_write_allowed", lambda *args, **kwargs: None),
            patch.object(scan, "write_scan_result", lambda _context, payload: archived.update(payload)),
        ):
            result = scanner.run()

        self.assertEqual(archived["schema_version"], 2)
        self.assertIn("preflight", archived)
        self.assertIn("baseline_candidate", archived)
        self.assertIn("scan_config", archived)
        self.assertIn("recommendation_status", archived)
        self.assertEqual(archived["selected_devices"]["hcorr"], preset.hcorr)
        self.assertEqual(archived["selected_devices"]["hcorr_setpoint_pv"], scanner.hcorr_pv)
        self.assertEqual(result.schema_version, 2)
        self.assertFalse(result.recommendation_available)
        self.assertIn("baseline score is too small", result.recommendation_status)

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
