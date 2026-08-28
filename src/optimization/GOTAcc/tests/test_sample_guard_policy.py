from types import SimpleNamespace

import numpy as np
import pytest

from gotacc.interfaces.policies import POLICY_REGISTRY
from gotacc.interfaces.policies.sample_guard import (
    SampleGuardConstraintPolicy,
    SampleGuardObjectivePolicy,
)


def _backend_context():
    return SimpleNamespace(
        objective_names=["fel_energy", "beam_current"],
        obj_pvnames=["FEL:ENERGY", "BEAM:CURRENT"],
        constraint_names=["orbit_x"],
        constraint_pvnames=["BPM:01:X"],
        constraint_bounds=[(-1.0, 1.0)],
    )


def test_objective_sample_guard_supports_named_target_and_any_condition():
    policy = POLICY_REGISTRY.build(
        "objective",
        "sample_guard",
        {
            "target": "fel_energy",
            "conditions": [
                {"metric": "mean_abs", "operator": "gt", "value": 1e6},
                {"metric": "peak_to_peak", "operator": "lt", "value": 1e-6},
            ],
            "match": "any",
            "action": {"type": "replace", "value": 0.0},
        },
    )
    assert isinstance(policy, SampleGuardObjectivePolicy)
    backend = _backend_context()
    policy.validate_backend(backend)

    total = np.asarray([[2e6, 4.0], [3e6, 5.0], [4e6, 6.0]])
    results = np.asarray([3e6, 5.0])
    guarded = policy.post_reduce(results, total, backend)

    np.testing.assert_allclose(guarded, [0.0, 5.0])
    np.testing.assert_allclose(results, [3e6, 5.0])


def test_objective_sample_guard_supports_all_match_and_add_offset():
    policy = SampleGuardObjectivePolicy(
        target="beam_current",
        conditions=[
            {"metric": "mean", "operator": "ge", "value": 5.0},
            {"metric": "std", "operator": "lt", "value": 0.1},
        ],
        match="all",
        action={"type": "add_offset", "value": 10.0},
    )
    backend = _backend_context()
    total = np.asarray([[1.0, 5.0], [2.0, 5.0], [3.0, 5.0]])

    guarded = policy.post_reduce(np.asarray([2.0, 5.0]), total, backend)

    np.testing.assert_allclose(guarded, [2.0, 15.0])


def test_constraint_sample_guard_derives_infeasible_value_from_bounds():
    policy = POLICY_REGISTRY.build(
        "constraint",
        "sample_guard",
        {
            "target": "orbit_x",
            "conditions": [
                {"metric": "max_abs", "operator": "le", "value": 1e-9},
            ],
            "action": {
                "type": "violate_bound",
                "delta_ratio": 0.1,
                "delta_min": 1e-6,
                "scale_floor": 1.0,
            },
        },
    )
    assert isinstance(policy, SampleGuardConstraintPolicy)
    backend = _backend_context()
    policy.validate_backend(backend)

    guarded = policy.post_reduce(
        np.asarray([0.0]),
        np.zeros((3, 1), dtype=float),
        backend,
    )

    np.testing.assert_allclose(guarded, [1.2])


def test_sample_guard_reports_a_concise_event_only_when_triggered():
    policy = SampleGuardObjectivePolicy(
        target="beam_current",
        conditions=[{"metric": "mean", "operator": "ge", "value": 5.0}],
        action={"type": "add_offset", "value": 10.0},
    )
    events = []
    policy.set_event_sink(events.append)
    backend = _backend_context()

    policy.post_reduce(
        np.asarray([2.0, 5.0]),
        np.asarray([[1.0, 5.0], [2.0, 5.0], [3.0, 5.0]]),
        backend,
    )
    policy.post_reduce(
        np.asarray([2.0, 4.0]),
        np.asarray([[1.0, 4.0], [2.0, 4.0], [3.0, 4.0]]),
        backend,
    )

    assert events == ["Policy triggered for beam_current [add offset]: 5 → 15"]


def test_sample_guard_rejects_invalid_rules_and_unknown_targets_before_evaluation():
    with pytest.raises(ValueError, match="metric"):
        POLICY_REGISTRY.validate(
            "objective",
            "sample_guard",
            {
                "conditions": [
                    {"metric": "python_expression", "operator": "gt", "value": 0},
                ]
            },
        )

    policy = SampleGuardObjectivePolicy(
        target="missing_signal",
        conditions=[{"metric": "mean", "operator": "gt", "value": 0}],
        action={"type": "replace", "value": 0},
    )
    with pytest.raises(ValueError, match="Cannot resolve"):
        policy.validate_backend(_backend_context())


def test_epics_backend_rejects_unknown_named_target_before_any_write(monkeypatch):
    from gotacc.interfaces import epics as epics_module

    writes = []
    monkeypatch.setattr(
        epics_module,
        "_load_epics",
        lambda: (
            lambda *_args, **_kwargs: 0.0,
            lambda *args, **_kwargs: writes.append(args),
            lambda *args, **_kwargs: writes.append(args),
            lambda *_args, **_kwargs: [0.0],
        ),
    )
    policy = SampleGuardObjectivePolicy(
        target="missing_signal",
        conditions=[{"metric": "mean", "operator": "gt", "value": 0}],
        action={"type": "replace", "value": 0},
    )

    with pytest.raises(ValueError, match="Cannot resolve"):
        epics_module.EpicsObjective(
            knobs_pvnames=["TEST:K1"],
            obj_pvnames=["TEST:OBJ"],
            objective_names=["known_signal"],
            obj_weights=[1.0],
            obj_samples=1,
            obj_math=["mean"],
            set_interval=0.0,
            sample_interval=0.0,
            objective_policy=policy,
        )

    assert writes == []


def test_gui_backend_ready_config_preserves_names_for_policy_targeting(monkeypatch):
    from gotacc.configs.schema import BackendConfig, MetaConfig, OptimizerConfig, TaskConfig
    from gotacc.gui.services.task_service import TaskService
    from gotacc.interfaces import epics as epics_module
    from gotacc.interfaces.factory import build_backend

    monkeypatch.setattr(
        epics_module,
        "_load_epics",
        lambda: (
            lambda *_args, **_kwargs: 0.0,
            lambda *_args, **_kwargs: None,
            lambda *_args, **_kwargs: None,
            lambda *_args, **_kwargs: [0.0],
        ),
    )
    cfg = TaskConfig(
        meta=MetaConfig(name="named_policy_target"),
        backend=BackendConfig(
            type="epics",
            bounds=[[-1.0, 1.0]],
            kwargs={
                "knobs_pvnames": ["TEST:K1"],
                "obj_pvnames": ["TEST:OBJ"],
                "objective_names": ["transmission"],
                "variable_names": ["Q1"],
                "obj_weights": [1.0],
                "obj_samples": 1,
                "obj_math": ["mean"],
                "set_interval": 0.0,
                "sample_interval": 0.0,
                "combine_mode": "weighted_sum",
                "objective_policies": [
                    {
                        "name": "sample_guard",
                        "kwargs": {
                            "target": "transmission",
                            "conditions": [
                                {"metric": "mean", "operator": "lt", "value": 0.0}
                            ],
                            "action": {"type": "replace", "value": 0.0},
                        },
                    }
                ],
            },
        ),
        optimizer=OptimizerConfig(name="bo"),
    )

    ready = TaskService.make_backend_build_ready_config(cfg)
    backend = build_backend(ready)

    assert "variable_names" not in ready.backend.kwargs
    assert ready.backend.kwargs["objective_names"] == ["transmission"]
    assert backend.objective_names == ["transmission"]
