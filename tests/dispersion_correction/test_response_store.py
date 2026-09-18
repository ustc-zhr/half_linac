import copy
from dataclasses import replace
import json

import numpy as np
import pytest

from half_linac.src.apps.dispersion_correction.machine.offline import OfflineMachine
from half_linac.src.apps.dispersion_correction.profile_runtime import default_offline_config
from half_linac.src.apps.dispersion_correction.response_store import load_response, save_response
from half_linac.src.apps.dispersion_correction.workflow import AchromatWorkflow
from half_linac.src.apps.dispersion_correction.joint_analysis import JointResponseAnalyzer
from half_linac.tests.dispersion_correction.test_joint_analysis import _joint_config


def _measure(config):
    records = []
    machine = OfflineMachine(config)
    AchromatWorkflow(config, machine=machine, response_callback=records.append).build_response_matrix()
    return records[0]


def test_response_round_trip_keeps_scan_context(tmp_path):
    config = default_offline_config()
    record = _measure(config)
    path = save_response(tmp_path, record)
    loaded = load_response(path)
    assert loaded.compatibility_error(config) is None
    np.testing.assert_array_equal(loaded.matrix, record.matrix)
    assert loaded.config == record.config
    assert loaded.snapshot == record.snapshot
    assert loaded.measurement == record.measurement
    assert loaded.created_at == record.created_at
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("change", ["plane", "bpm_order", "weights", "unit", "energy", "calibration", "section", "channels"])
def test_incompatible_response_is_rejected_before_machine_io(change):
    config = default_offline_config()
    record = _measure(config)
    if change == "plane":
        config = replace(config, measurement=replace(config.measurement, plane="y"))
    elif change == "bpm_order":
        config = replace(config, target_bpms=tuple(reversed(config.target_bpms)))
    elif change == "weights":
        first = config.knobs[0]
        config = replace(config, knobs=(replace(first, devices={name: -weight for name, weight in first.devices.items()}), *config.knobs[1:]))
    elif change == "unit":
        config = replace(config, knobs=(replace(config.knobs[0], unit="A"), *config.knobs[1:]))
    elif change == "energy":
        config = replace(config, energy_knob=replace(config.energy_knob, name="another"))
    elif change == "calibration":
        config = replace(config, energy_knob=replace(config.energy_knob, calibration={"actuator_per_delta": 2}))
    elif change == "section":
        config = replace(config, section=replace(config.section, id="another"))
    else:
        config = replace(config, backend=replace(config.backend, options={"pv_map": {"other": "PV"}}))
    assert record.compatibility_error(config)
    with pytest.raises(ValueError, match="Incompatible"):
        AchromatWorkflow(config, machine=object()).run(record)


@pytest.mark.parametrize("bad_matrix", [np.zeros((4, 2)), np.ones((3, 2)), np.full((4, 2), np.nan)])
def test_invalid_matrix_is_not_reusable(bad_matrix):
    config = default_offline_config()
    record = replace(_measure(config), matrix=bad_matrix)
    assert record.compatibility_error(config)


def test_corrupt_or_unknown_file_version_is_rejected(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"schema_version": 99}))
    with pytest.raises(ValueError, match="version"):
        load_response(path)


@pytest.mark.parametrize("policy, expected_scans", [("once", 0), ("every_iteration", 2)])
def test_reuse_uses_fresh_dispersion_and_respects_update_policy(policy, expected_scans):
    config = default_offline_config()
    record = _measure(config)
    config = replace(config, solver=replace(config.solver, max_iter=3, response_update=policy))
    machine = OfflineMachine(config, initial_dispersion_mm=np.array([40., 45., 50., 55.]))
    scans = []
    workflow = AchromatWorkflow(config, machine=machine, response_callback=scans.append)
    result = workflow.run(record)
    assert result.success
    if policy == "once":
        assert result.response.source_created_at == record.created_at
    assert len(scans) == expected_scans
    np.testing.assert_allclose(result.initial.values_mm, [40, 45, 50, 55])
    assert result.final.rms_mm < result.initial.rms_mm
    assert machine.get_energy_setpoint_delta() == 0


def test_reuse_still_rolls_back_a_rejected_trial():
    config = default_offline_config()
    record = _measure(config)
    machine = OfflineMachine(config, response_matrix=-record.matrix)
    initial = machine.snapshot()
    result = AchromatWorkflow(config, machine=machine).run(record)
    assert not any(step.accepted for step in result.steps)
    np.testing.assert_allclose(result.final.values_mm, result.initial.values_mm)
    assert machine.snapshot().energy_delta == initial.energy_delta
    assert machine.get_knobs(tuple(knob.name for knob in config.knobs)) == {
        knob.name: 0.0 for knob in config.knobs
    }


def test_changed_operating_point_is_reported_without_inventing_a_threshold():
    config = default_offline_config()
    record = _measure(config)
    record = replace(record, snapshot=replace(record.snapshot, device_values={"Q1": 7.0}))
    assert "Δ +1" in record.operating_point_summary({"Q1": 8.0}, 0.01)
    assert "current unknown" in record.operating_point_summary()


@pytest.mark.parametrize("policy", ["once", "every_iteration"])
def test_joint_response_can_be_saved_and_reused(tmp_path, policy):
    config = _joint_config()
    analysis = config.section.joint_response_analysis
    config = replace(config, section=replace(config.section, diagnostic_only=False,
        joint_response_analysis=replace(analysis, targets=tuple(replace(target, normalization_scale_mm=0.01) for target in analysis.targets))),
        solver=replace(config.solver, max_iter=3, response_update=policy))
    records = []
    JointResponseAnalyzer(config, response_callback=records.append).run()
    record = load_response(save_response(tmp_path, records[0]))
    assert record.compatibility_error(config) is None
    legacy_config = copy.deepcopy(record.config)
    for target in legacy_config["section"]["joint_response_analysis"]["targets"]:
        target["tolerance_mm"] = target.pop("normalization_scale_mm")
    assert replace(record, config=legacy_config).compatibility_error(config) is None
    measured = []
    result = JointResponseAnalyzer(config, response_callback=measured.append).run_automatic(record)
    assert result.success
    assert result.steps[0].response.source_created_at == record.created_at
    assert result.normalized_rms_after < result.normalized_rms_before
    assert len(measured) == (0 if policy == "once" else len(result.steps) - 1)
