from __future__ import annotations

from typing import Any, Mapping

from gotacc.interfaces.epics import (
    BPMGuardConstraintPolicy,
    EqualWritePolicy,
    FelEnergyGuardPolicy,
    ZeroGuardPolicy,
)

from .registry import PolicyDefinition, PolicyPreset, PolicyRegistry
from .sample_guard import SampleGuardConstraintPolicy, SampleGuardObjectivePolicy


def _build_equal_write(kwargs: Mapping[str, Any]) -> EqualWritePolicy:
    return EqualWritePolicy(extra_links=kwargs["pvlinks"])


def _build_fel_energy_guard(kwargs: Mapping[str, Any]) -> FelEnergyGuardPolicy:
    return FelEnergyGuardPolicy(
        target_col=kwargs["target_col"],
        large_threshold=kwargs["large_threshold"],
        change_threshold=kwargs["change_threshold"],
    )


def _build_zero_guard(kwargs: Mapping[str, Any]) -> ZeroGuardPolicy:
    return ZeroGuardPolicy(
        target_col=kwargs["target_col"],
        zero_atol=kwargs["zero_atol"],
        offset=kwargs["offset"],
    )


def _build_bpm_guard(kwargs: Mapping[str, Any]) -> BPMGuardConstraintPolicy:
    return BPMGuardConstraintPolicy(
        target_col=kwargs["target_col"],
        zero_atol=kwargs["zero_atol"],
        delta_ratio=kwargs["delta_ratio"],
        delta_min=kwargs["delta_min"],
        scale_floor=kwargs["scale_floor"],
    )


def _build_objective_sample_guard(
    kwargs: Mapping[str, Any],
) -> SampleGuardObjectivePolicy:
    return SampleGuardObjectivePolicy(
        target=kwargs["target"],
        target_col=kwargs["target_col"],
        conditions=kwargs["conditions"],
        match=kwargs["match"],
        action=kwargs["action"],
    )


def _build_constraint_sample_guard(
    kwargs: Mapping[str, Any],
) -> SampleGuardConstraintPolicy:
    return SampleGuardConstraintPolicy(
        target=kwargs["target"],
        target_col=kwargs["target_col"],
        conditions=kwargs["conditions"],
        match=kwargs["match"],
        action=kwargs["action"],
    )


def _adapt_fel_guard(kwargs: Mapping[str, Any], rule: dict[str, Any]) -> Mapping[str, Any]:
    rule["target_col"] = int(kwargs.get("target_col", rule["target_col"]))
    rule["conditions"][0]["value"] = float(
        kwargs.get("large_threshold", rule["conditions"][0]["value"])
    )
    rule["conditions"][1]["value"] = float(
        kwargs.get("change_threshold", rule["conditions"][1]["value"])
    )
    return rule


def _adapt_zero_guard(kwargs: Mapping[str, Any], rule: dict[str, Any]) -> Mapping[str, Any]:
    rule["target_col"] = int(kwargs.get("target_col", rule["target_col"]))
    rule["conditions"][0]["atol"] = float(
        kwargs.get("zero_atol", rule["conditions"][0]["atol"])
    )
    rule["action"]["value"] = float(kwargs.get("offset", rule["action"]["value"]))
    return rule


def _adapt_bpm_guard(kwargs: Mapping[str, Any], rule: dict[str, Any]) -> Mapping[str, Any]:
    rule["target_col"] = int(kwargs.get("target_col", rule["target_col"]))
    rule["conditions"][0]["value"] = float(
        kwargs.get("zero_atol", rule["conditions"][0]["value"])
    )
    for key in ("delta_ratio", "delta_min", "scale_floor"):
        rule["action"][key] = float(kwargs.get(key, rule["action"][key]))
    return rule


POLICY_REGISTRY = PolicyRegistry()

POLICY_REGISTRY.register(
    PolicyDefinition(
        name="equal",
        kind="write",
        aliases=("xiaosesan_symmetry",),
        default_kwargs={"pvlinks": None},
        factory=_build_equal_write,
        description="Write selected knob values to additional linked PVs.",
    )
)

POLICY_REGISTRY.register(
    PolicyDefinition(
        name="fel_energy_guard",
        kind="objective",
        default_kwargs={
            "target_col": 0,
            "large_threshold": 1e6,
            "change_threshold": 1e-6,
        },
        factory=_build_fel_energy_guard,
        description="Replace abnormal or nearly constant FEL energy samples.",
        gui_visible=False,
    )
)

POLICY_REGISTRY.register(
    PolicyDefinition(
        name="zero_guard",
        kind="objective",
        aliases=("xiaosesan_zero_guard",),
        default_kwargs={
            "target_col": 1,
            "zero_atol": 1e-12,
            "offset": 100.0,
        },
        factory=_build_zero_guard,
        description="Add an offset when a reduced objective is effectively zero.",
        gui_visible=False,
    )
)

POLICY_REGISTRY.register(
    PolicyDefinition(
        name="bpm_guard",
        kind="constraint",
        aliases=("bpm_zero_guard",),
        default_kwargs={
            "target_col": 0,
            "zero_atol": 1e-9,
            "delta_ratio": 0.1,
            "delta_min": 1e-6,
            "scale_floor": 1.0,
        },
        factory=_build_bpm_guard,
        description="Treat all-zero BPM constraint samples as infeasible.",
        gui_visible=False,
    )
)

POLICY_REGISTRY.register(
    PolicyDefinition(
        name="sample_guard",
        kind="objective",
        default_kwargs={
            "target": None,
            "target_col": 0,
            "conditions": [
                {"metric": "mean_abs", "operator": "gt", "value": 1e6},
                {"metric": "peak_to_peak", "operator": "lt", "value": 1e-6},
            ],
            "match": "any",
            "action": {"type": "replace", "value": 0.0},
        },
        factory=_build_objective_sample_guard,
        description="Apply declarative sample conditions to an objective.",
        is_default=True,
    )
)

POLICY_REGISTRY.register(
    PolicyDefinition(
        name="sample_guard",
        kind="constraint",
        default_kwargs={
            "target": None,
            "target_col": 0,
            "conditions": [
                {"metric": "max_abs", "operator": "le", "value": 1e-9},
            ],
            "match": "all",
            "action": {
                "type": "violate_bound",
                "delta_ratio": 0.1,
                "delta_min": 1e-6,
                "scale_floor": 1.0,
            },
        },
        factory=_build_constraint_sample_guard,
        description="Apply declarative sample conditions to a constraint.",
        is_default=True,
    )
)

POLICY_REGISTRY.register_preset(
    PolicyPreset(
        name="fel_energy_guard",
        display_name="FEL Energy Guard",
        kind="objective",
        policy_name="sample_guard",
        kwargs={
            "target": None,
            "target_col": 0,
            "conditions": [
                {"metric": "mean_abs", "operator": "gt", "value": 1e6},
                {"metric": "peak_to_peak", "operator": "lt", "value": 1e-6},
            ],
            "match": "any",
            "action": {"type": "replace", "value": 0.0},
        },
        description=(
            "If FEL energy samples are implausibly large or nearly constant, "
            "replace the result with 0."
        ),
        legacy_kwargs_adapter=_adapt_fel_guard,
    )
)

POLICY_REGISTRY.register_preset(
    PolicyPreset(
        name="zero_guard",
        display_name="Zero Objective Guard",
        kind="objective",
        policy_name="sample_guard",
        kwargs={
            "target": None,
            "target_col": 1,
            "conditions": [
                {"metric": "reduced", "operator": "eq", "value": 0.0, "atol": 1e-12},
            ],
            "match": "all",
            "action": {"type": "add_offset", "value": 100.0},
        },
        description=(
            "If the reduced objective is effectively zero, add the configured offset."
        ),
        legacy_kwargs_adapter=_adapt_zero_guard,
    )
)

POLICY_REGISTRY.register_preset(
    PolicyPreset(
        name="bpm_guard",
        display_name="BPM Zero Guard",
        kind="constraint",
        policy_name="sample_guard",
        kwargs={
            "target": None,
            "target_col": 0,
            "conditions": [
                {"metric": "max_abs", "operator": "le", "value": 1e-9},
            ],
            "match": "all",
            "action": {
                "type": "violate_bound",
                "delta_ratio": 0.1,
                "delta_min": 1e-6,
                "scale_floor": 1.0,
            },
        },
        description=(
            "If all BPM samples are near zero, mark this constraint as infeasible."
        ),
        legacy_kwargs_adapter=_adapt_bpm_guard,
    )
)
