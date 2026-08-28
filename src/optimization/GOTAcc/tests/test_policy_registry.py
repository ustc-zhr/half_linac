import pytest

from gotacc.configs.schema import BackendConfig, MetaConfig, OptimizerConfig, TaskConfig
from gotacc.configs.validators import validate_task_config_strict
from gotacc.interfaces.epics import (
    BPMGuardConstraintPolicy,
    CompositeObjectivePolicy,
    EqualWritePolicy,
    FelEnergyGuardPolicy,
    ZeroGuardPolicy,
)
from gotacc.interfaces.factory import (
    build_constraint_policy,
    build_objective_policies,
    build_objective_policy,
    build_write_policy,
)
from gotacc.interfaces.policies import POLICY_REGISTRY, PolicyDefinition, PolicyRegistry


def test_builtin_policy_registry_exposes_canonical_names_aliases_and_defaults():
    assert POLICY_REGISTRY.names("write") == ("equal",)
    assert POLICY_REGISTRY.names("objective") == (
        "fel_energy_guard",
        "zero_guard",
        "sample_guard",
    )
    assert POLICY_REGISTRY.names("constraint") == ("bpm_guard", "sample_guard")
    assert POLICY_REGISTRY.default_name("objective", gui_only=True) == "sample_guard"
    assert POLICY_REGISTRY.default_name("constraint", gui_only=True) == "sample_guard"
    assert POLICY_REGISTRY.names("objective", gui_only=True) == ("sample_guard",)
    assert POLICY_REGISTRY.names("constraint", gui_only=True) == ("sample_guard",)
    assert POLICY_REGISTRY.preset_names("objective") == (
        "fel_energy_guard",
        "zero_guard",
    )
    assert POLICY_REGISTRY.preset_names("constraint") == ("bpm_guard",)
    assert POLICY_REGISTRY.names("objective", include_aliases=True) == (
        "fel_energy_guard",
        "zero_guard",
        "xiaosesan_zero_guard",
        "sample_guard",
    )
    assert POLICY_REGISTRY.resolve("constraint", "bpm_zero_guard").name == "bpm_guard"

    defaults = POLICY_REGISTRY.resolve("objective", "fel_energy_guard").defaults()
    defaults["target_col"] = 99
    assert POLICY_REGISTRY.resolve("objective", "fel_energy_guard").defaults()["target_col"] == 0

    fel_preset = POLICY_REGISTRY.expand_preset("objective", "fel_energy_guard")
    assert fel_preset["name"] == "sample_guard"
    assert fel_preset["kwargs"]["conditions"][0] == {
        "metric": "mean_abs",
        "operator": "gt",
        "value": 1e6,
    }
    migrated = POLICY_REGISTRY.expand_preset(
        "objective",
        "fel_energy_guard",
        legacy_kwargs={"target_col": 2, "large_threshold": 42, "change_threshold": 0.5},
    )
    assert migrated["kwargs"]["target_col"] == 2
    assert [condition["value"] for condition in migrated["kwargs"]["conditions"]] == [
        42.0,
        0.5,
    ]


def test_factory_builders_delegate_to_registry_without_changing_policy_behavior():
    write_policy = build_write_policy("xiaosesan_symmetry", {"pvlinks": [(1, "TEST:LINK")]})
    assert isinstance(write_policy, EqualWritePolicy)
    assert write_policy.extra_links == [(1, "TEST:LINK")]

    fel_policy = build_objective_policy("fel_energy_guard")
    assert isinstance(fel_policy, FelEnergyGuardPolicy)
    assert fel_policy.target_col == 0
    assert fel_policy.large_threshold == 1e6

    zero_policy = build_objective_policy("xiaosesan_zero_guard")
    assert isinstance(zero_policy, ZeroGuardPolicy)
    assert zero_policy.target_col == 1

    bpm_policy = build_constraint_policy("bpm_zero_guard")
    assert isinstance(bpm_policy, BPMGuardConstraintPolicy)
    assert bpm_policy.target_col == 0

    composite = build_objective_policies(
        [
            {"name": "fel_energy_guard", "kwargs": {"target_col": 0}},
            {"name": "zero_guard", "kwargs": {"target_col": 1}},
        ]
    )
    assert isinstance(composite, CompositeObjectivePolicy)
    assert [type(policy) for policy in composite.policies] == [
        FelEnergyGuardPolicy,
        ZeroGuardPolicy,
    ]


def test_policy_registry_rejects_duplicate_names_and_builds_from_copied_defaults():
    registry = PolicyRegistry()
    registry.register(
        PolicyDefinition(
            name="example",
            kind="objective",
            aliases=("example_alias",),
            default_kwargs={"values": [1]},
            factory=lambda kwargs: kwargs,
        )
    )

    built = registry.build("objective", "example_alias")
    built["values"].append(2)
    assert registry.resolve("objective", "example").defaults() == {"values": [1]}

    with pytest.raises(ValueError, match="already registered"):
        registry.register(
            PolicyDefinition(
                name="example_alias",
                kind="objective",
                factory=lambda kwargs: kwargs,
            )
        )


def test_strict_validation_uses_registry_policy_names():
    cfg = TaskConfig(
        meta=MetaConfig(name="registry_validation"),
        backend=BackendConfig(
            type="epics",
            bounds=[[-1.0, 1.0]],
            kwargs={
                "knobs_pvnames": ["TEST:K1"],
                "obj_pvnames": ["TEST:OBJ"],
                "obj_weights": [1.0],
                "obj_samples": 1,
                "obj_math": ["mean"],
                "set_interval": 0.0,
                "sample_interval": 0.0,
                "combine_mode": "weighted_sum",
                "write_policy": "xiaosesan_symmetry",
                "objective_policy": "xiaosesan_zero_guard",
                "constraint_policy": "bpm_zero_guard",
            },
        ),
        optimizer=OptimizerConfig(name="bo"),
    )

    assert validate_task_config_strict(cfg) is cfg
