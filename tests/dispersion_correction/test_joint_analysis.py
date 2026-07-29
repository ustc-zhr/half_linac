import numpy as np
import pytest
from dataclasses import replace

from half_linac.src.apps.dispersion_correction.config import parse_config
from half_linac.src.apps.dispersion_correction.joint_analysis import (
    JointResponseAnalyzer,
)


def _joint_config():
    return parse_config(
        {
            "backend": {
                "type": "offline",
                "mode": "read_only",
                "options": {
                    "model": {
                        "initial_dispersion_mm": [2.0, -1.0],
                        "initial_dispersion_y_mm": [1.0, 3.0],
                        "response_matrix": [[10.0, 0.0], [0.0, 10.0]],
                        "response_matrix_y": [[5.0, 5.0], [5.0, -5.0]],
                    }
                },
            },
            "energy_knob": {"delta": 1.0e-4},
            "target_bpms": [],
            "monitor_bpms": ["BPM1", "BPM2"],
            "knobs": [],
            "section": {
                "id": "joint",
                "display_name": "Joint analysis",
                "diagnostic_only": True,
                "joint_response_analysis": {
                    "targets": [
                        {"bpm": "BPM1", "plane": "x", "target_mm": 0.0},
                        {"bpm": "BPM2", "plane": "x", "target_mm": 0.0},
                        {"bpm": "BPM1", "plane": "y", "target_mm": 0.0},
                        {"bpm": "BPM2", "plane": "y", "target_mm": 0.0},
                    ],
                    "knobs": [
                        {
                            "name": "Q_A",
                            "devices": {"Q1": 1.0},
                            "scan_step": 0.01,
                            "limit": 1.0,
                        },
                        {
                            "name": "Q_B",
                            "devices": {"Q2": 1.0},
                            "scan_step": 0.01,
                            "limit": 1.0,
                        },
                    ],
                },
            },
            "measurement": {
                "plane": "xy",
                "samples_per_step": 1,
                "final_samples": 1,
            },
            "solver": {
                "gain": 0.5,
                "max_step_fraction": 1.0,
                "regularization": 0.0,
            },
            "safety": {"max_reference_orbit_change_mm": 1.0},
        }
    )


def test_joint_analysis_stacks_planes_and_restores_scan_state() -> None:
    analyzer = JointResponseAnalyzer(_joint_config())
    result = analyzer.run()

    assert result.target_names == (
        "BPM1 ηx",
        "BPM2 ηx",
        "BPM1 ηy",
        "BPM2 ηy",
    )
    assert result.matrix == pytest.approx(
        np.asarray(
            [
                [10.0, 0.0],
                [0.0, 10.0],
                [5.0, 5.0],
                [5.0, -5.0],
            ]
        )
    )
    assert result.retained_rank == 2
    assert result.normalized_rms_after < result.normalized_rms_before
    assert analyzer.workflow.machine.get_knobs(("Q_A", "Q_B")) == {
        "Q_A": 0.0,
        "Q_B": 0.0,
    }


def test_joint_analysis_requires_targets_and_knobs_together() -> None:
    raw = {
        "backend": {"type": "offline"},
        "energy_knob": {"delta": 1.0e-4},
        "target_bpms": [],
        "monitor_bpms": ["BPM1"],
        "knobs": [],
        "section": {
            "diagnostic_only": True,
            "joint_response_analysis": {
                "targets": [{"bpm": "BPM1", "plane": "x"}],
            },
        },
        "measurement": {"plane": "xy"},
        "safety": {"max_reference_orbit_change_mm": 1.0},
    }
    with pytest.raises(ValueError, match="requires both targets and knobs"):
        parse_config(raw)


def test_joint_correction_applies_and_verifies_outside_diagnostics() -> None:
    config = _joint_config()
    config = replace(
        config,
        section=replace(config.section, diagnostic_only=False),
    )
    analyzer = JointResponseAnalyzer(config)
    recommendation = analyzer.run()
    result = analyzer.apply_recommendation(recommendation)

    assert result.success
    assert result.normalized_rms_after < result.normalized_rms_before
    assert result.steps[0].accepted


def test_automatic_joint_correction_runs_bounded_generations() -> None:
    config = _joint_config()
    config = replace(
        config,
        section=replace(config.section, diagnostic_only=False),
        solver=replace(config.solver, max_iter=3),
    )
    result = JointResponseAnalyzer(config).run_automatic()

    assert result.success
    assert 1 <= len(result.steps) <= 3
    assert result.normalized_rms_after < result.normalized_rms_before
