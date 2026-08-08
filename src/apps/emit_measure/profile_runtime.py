from __future__ import annotations

from half_linac.src.shared.machine_profile import (
    AppContext,
    LimitRange,
    MachineProfile,
    MachineProfileError,
    effective_limit,
)


def effective_k1_scan_limit(
    target: MachineProfile | AppContext,
    element_id: str,
    low: float,
    high: float,
    mode: str,
    unit: str,
    center: float,
) -> LimitRange:
    """Intersect an Emit application range with the machine K1 channel limit."""
    profile = target.profile if isinstance(target, AppContext) else target
    element = profile.get_element(element_id)
    try:
        application_limit = LimitRange(low, high, unit)
        normalized_mode = str(mode or "absolute").strip().lower()
        if normalized_mode == "relative":
            application_limit = application_limit.relative_to_absolute(center)
        elif normalized_mode != "absolute":
            raise MachineProfileError(f"Unsupported scan mode: {mode!r}.")

        raw_machine_limit = element.limits_for("K1")
        machine_limit = LimitRange.from_mapping(raw_machine_limit) if raw_machine_limit else None
        if machine_limit is not None and not machine_limit.contains(center):
            raise MachineProfileError(
                f"Current value {float(center):g} is outside physical limit "
                f"{machine_limit.describe()}."
            )
        return effective_limit(application_limit, machine_limit)
    except (TypeError, ValueError, MachineProfileError) as exc:
        raise MachineProfileError(f"Invalid Emit scan range for {element_id}.K1: {exc}") from exc
