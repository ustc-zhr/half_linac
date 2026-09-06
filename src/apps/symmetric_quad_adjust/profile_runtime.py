from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from half_linac.src.apps.symmetric_quad_adjust.model import QuadPair
from half_linac.src.shared.machine_profile import (
    AppContext,
    MachineProfileError,
    load_app_context,
    normalize_mode,
    require_workflow_write_allowed,
    resolve_channel,
    resolve_write_target,
)


@dataclass(frozen=True)
class QuadTarget:
    element_id: str
    display_name: str
    pv_name: str
    readback_pv: str


@dataclass(frozen=True)
class PairRuntime:
    pair: QuadPair
    left: QuadTarget
    right: QuadTarget


@dataclass(frozen=True)
class SymmetricQuadRuntime:
    context: AppContext
    pairs: tuple[PairRuntime, ...]
    logical_channel: str
    default_step: float
    step_choices: tuple[float, ...]
    custom_step_minimum: float
    custom_step_maximum: float
    button_repeat_delay_ms: int
    button_repeat_interval_ms: int
    display_decimals: int
    readback_tolerance: float

    @property
    def targets(self) -> tuple[QuadTarget, ...]:
        return tuple(target for pair in self.pairs for target in (pair.left, pair.right))


def load_symmetric_quad_runtime() -> SymmetricQuadRuntime:
    context = load_app_context("symmetric_quad_adjust")
    raw = context.profile.workflows.get("symmetric_quad_adjust")
    if not isinstance(raw, Mapping):
        raise MachineProfileError(
            "Missing symmetric_quad_adjust workflow configuration."
        )
    backend = context.control_backend.name
    configured_backends = tuple(
        normalize_mode(value, "symmetric_quad_adjust.control_backends[]")
        for value in _string_list(raw.get("control_backends"), "control_backends")
    )
    if backend not in configured_backends:
        raise MachineProfileError(
            f"symmetric_quad_adjust does not support backend {backend!r}."
        )
    require_workflow_write_allowed(
        context,
        "symmetric_quad_adjust",
        "Symmetric quadrupole K1 write",
    )

    logical_channel = str(raw.get("write_channel", "K1")).strip()
    if logical_channel.casefold() != "k1":
        raise MachineProfileError(
            "symmetric_quad_adjust.write_channel must be K1."
        )
    readback_channels = raw.get("readback_channel")
    if not isinstance(readback_channels, Mapping):
        raise MachineProfileError(
            "symmetric_quad_adjust.readback_channel must map each backend to a channel."
        )
    readback_channel = str(readback_channels.get(backend, "")).strip()
    if not readback_channel:
        raise MachineProfileError(
            f"symmetric_quad_adjust.readback_channel is missing backend {backend!r}."
        )
    raw_pairs = raw.get("pairs")
    if not isinstance(raw_pairs, list) or not raw_pairs:
        raise MachineProfileError("symmetric_quad_adjust.pairs must be a non-empty list.")

    pairs: list[PairRuntime] = []
    used: set[str] = set()
    for index, raw_pair in enumerate(raw_pairs):
        if not isinstance(raw_pair, Mapping):
            raise MachineProfileError(
                f"symmetric_quad_adjust.pairs[{index}] must be a mapping."
            )
        left_id = _element_id(raw_pair.get("left"), index, "left")
        right_id = _element_id(raw_pair.get("right"), index, "right")
        if left_id == right_id:
            raise MachineProfileError(
                f"symmetric_quad_adjust.pairs[{index}] must contain two different quadrupoles."
            )
        duplicate = next((name for name in (left_id, right_id) if name in used), None)
        if duplicate:
            raise MachineProfileError(
                f"Quadrupole {duplicate} belongs to more than one symmetric pair."
            )
        used.update((left_id, right_id))
        targets = []
        for element_id in (left_id, right_id):
            element = context.profile.get_element(element_id)
            if element.kind != "quad":
                raise MachineProfileError(f"{element_id} is not a quadrupole.")
            write_target = resolve_write_target(
                context, element_id, logical_channel=logical_channel
            )
            targets.append(
                QuadTarget(
                    element_id,
                    element.display_name,
                    write_target.pv_name,
                    resolve_channel(context, element_id, readback_channel),
                )
            )
        pairs.append(
            PairRuntime(QuadPair(left_id, right_id), targets[0], targets[1])
        )

    step_choices = _number_list(raw.get("step_choices"), "step_choices")
    default_step = _positive(raw.get("default_step"), "default_step")
    if not any(math.isclose(default_step, choice) for choice in step_choices):
        raise MachineProfileError(
            "symmetric_quad_adjust.default_step must belong to step_choices."
        )
    custom_step_minimum = _positive(
        raw.get("custom_step_minimum"), "custom_step_minimum"
    )
    custom_step_maximum = _positive(
        raw.get("custom_step_maximum"), "custom_step_maximum"
    )
    if custom_step_minimum >= custom_step_maximum:
        raise MachineProfileError(
            "symmetric_quad_adjust custom step minimum must be below maximum."
        )
    if not custom_step_minimum <= default_step <= custom_step_maximum:
        raise MachineProfileError(
            "symmetric_quad_adjust.default_step is outside the custom step range."
        )
    decimals = raw.get("display_decimals", 6)
    if isinstance(decimals, bool) or not isinstance(decimals, int) or not 1 <= decimals <= 12:
        raise MachineProfileError(
            "symmetric_quad_adjust.display_decimals must be an integer from 1 to 12."
        )
    return SymmetricQuadRuntime(
        context=context,
        pairs=tuple(pairs),
        logical_channel="K1",
        default_step=default_step,
        step_choices=step_choices,
        custom_step_minimum=custom_step_minimum,
        custom_step_maximum=custom_step_maximum,
        button_repeat_delay_ms=_positive_int(
            raw.get("button_repeat_delay_ms"), "button_repeat_delay_ms"
        ),
        button_repeat_interval_ms=_positive_int(
            raw.get("button_repeat_interval_ms"), "button_repeat_interval_ms"
        ),
        display_decimals=decimals,
        readback_tolerance=_positive(
            raw.get("readback_tolerance"), "readback_tolerance"
        ),
    )


def _element_id(value: Any, pair_index: int, side: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise MachineProfileError(
            f"symmetric_quad_adjust.pairs[{pair_index}].{side} must not be empty."
        )
    return text


def _string_list(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise MachineProfileError(f"symmetric_quad_adjust.{name} must be a non-empty list.")
    values = tuple(str(item).strip() for item in value)
    if any(not item for item in values):
        raise MachineProfileError(f"symmetric_quad_adjust.{name} contains an empty value.")
    return values


def _number_list(value: Any, name: str) -> tuple[float, ...]:
    if not isinstance(value, list) or not value:
        raise MachineProfileError(f"symmetric_quad_adjust.{name} must be a non-empty list.")
    return tuple(_positive(item, f"{name}[]") for item in value)


def _positive(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MachineProfileError(
            f"symmetric_quad_adjust.{name} must be numeric."
        ) from exc
    if not math.isfinite(number) or number <= 0:
        raise MachineProfileError(
            f"symmetric_quad_adjust.{name} must be positive."
        )
    return number


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise MachineProfileError(
            f"symmetric_quad_adjust.{name} must be a positive integer."
        )
    return value
