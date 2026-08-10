from __future__ import annotations

from half_linac.src.shared.machine_profile import (
    AppContext,
    LimitRange,
    MachineProfile,
    MachineProfileError,
    effective_limit,
    resolve_write_target,
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
    try:
        write_target = resolve_write_target(
            target,
            element_id,
            quantity="K1",
            unit=unit,
        )
        application_limit = LimitRange(low, high, unit)
        normalized_mode = str(mode or "absolute").strip().lower()
        if normalized_mode == "relative":
            application_limit = application_limit.relative_to_absolute(center)
        elif normalized_mode != "absolute":
            raise MachineProfileError(f"Unsupported scan mode: {mode!r}.")

        machine_limit = write_target.machine_limit
        if machine_limit is not None and not machine_limit.contains(center):
            raise MachineProfileError(
                f"Current value {float(center):g} is outside physical limit "
                f"{machine_limit.describe()}."
            )
        return effective_limit(application_limit, machine_limit)
    except (TypeError, ValueError, MachineProfileError) as exc:
        raise MachineProfileError(f"Invalid Emit scan range for {element_id}.K1: {exc}") from exc
