"""Validation helpers for the generic energy-feedback workflow."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from .models import MachineProfile, MachineProfileError


WORKFLOW_NAME = "energy_feedback"


def resolve_energy_feedback_workflow(profile: MachineProfile) -> dict[str, Any]:
    workflow = profile.workflows.get(WORKFLOW_NAME)
    if not isinstance(workflow, Mapping):
        raise MachineProfileError(
            f"Machine profile {profile.machine.id!r} does not configure {WORKFLOW_NAME!r}."
        )
    resolved = dict(workflow)
    validate_energy_feedback_workflow(profile, resolved)
    return resolved


def _fail(profile: MachineProfile, message: str) -> None:
    raise MachineProfileError(
        f"Invalid {WORKFLOW_NAME!r} workflow for machine {profile.machine.id!r}: {message}"
    )


def _mapping(profile: MachineProfile, value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(profile, f"{name} must be an object")
    return value


def _sequence(profile: MachineProfile, value: Any, name: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        _fail(profile, f"{name} must be an array")
    return value


def _number(
    profile: MachineProfile,
    value: Any,
    name: str,
    *,
    positive: bool = False,
    nonnegative: bool = False,
) -> float:
    if isinstance(value, bool):
        _fail(profile, f"{name} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError):
        _fail(profile, f"{name} must be a finite number")
    if not math.isfinite(result):
        _fail(profile, f"{name} must be a finite number")
    if positive and result <= 0:
        _fail(profile, f"{name} must be greater than zero")
    if nonnegative and result < 0:
        _fail(profile, f"{name} must not be negative")
    return result


def _integer(profile: MachineProfile, value: Any, name: str, *, positive: bool = False) -> int:
    if isinstance(value, bool):
        _fail(profile, f"{name} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError):
        _fail(profile, f"{name} must be an integer")
    if result != value:
        _fail(profile, f"{name} must be an integer")
    if positive and result <= 0:
        _fail(profile, f"{name} must be greater than zero")
    return result


def _text(profile: MachineProfile, value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(profile, f"{name} must be a non-empty string")
    return value.strip()


def _range(profile: MachineProfile, value: Any, name: str) -> tuple[float, float]:
    values = _sequence(profile, value, name)
    if len(values) != 2:
        _fail(profile, f"{name} must contain [minimum, maximum]")
    low = _number(profile, values[0], f"{name}[0]")
    high = _number(profile, values[1], f"{name}[1]")
    if low >= high:
        _fail(profile, f"{name} minimum must be less than maximum")
    return low, high


def _operator_range(
    profile: MachineProfile,
    raw: Any,
    name: str,
    *,
    positive: bool = False,
) -> tuple[float, float]:
    spec = _mapping(profile, raw, name)
    low = _number(
        profile,
        spec.get("minimum"),
        f"{name}.minimum",
        positive=positive,
    )
    high = _number(
        profile,
        spec.get("maximum"),
        f"{name}.maximum",
        positive=positive,
    )
    if low >= high:
        _fail(profile, f"{name} minimum must be less than maximum")
    decimals = _integer(
        profile,
        spec.get("decimals", 3),
        f"{name}.decimals",
    )
    if not 0 <= decimals <= 6:
        _fail(profile, f"{name}.decimals must be between 0 and 6")
    if "label" in spec:
        _text(profile, spec["label"], f"{name}.label")
    return low, high


def _signal(
    profile: MachineProfile,
    raw: Any,
    name: str,
    backends: Sequence[str],
    *,
    expected_unit: str | None = None,
) -> tuple[str, str]:
    signal = _mapping(profile, raw, name)
    element_id = _text(profile, signal.get("element"), f"{name}.element")
    channel = _text(profile, signal.get("channel"), f"{name}.channel")
    unit = signal.get("unit")
    if expected_unit is not None and unit != expected_unit:
        _fail(profile, f"{name}.unit must be {expected_unit!r}")
    try:
        element = profile.get_element(element_id)
    except MachineProfileError as exc:
        _fail(profile, f"{name} references unknown element {element_id!r}: {exc}")
    for backend in backends:
        try:
            pv_name = element.channels[channel][backend]
        except KeyError:
            _fail(profile, f"{name} channel is unavailable on backend {backend!r}")
        if not str(pv_name).strip():
            _fail(profile, f"{name} channel is empty on backend {backend!r}")
        resolved_unit = element.limits_for(channel).get("unit")
        if resolved_unit and expected_unit and resolved_unit != expected_unit:
            _fail(
                profile,
                f"{name} resolves unit {resolved_unit!r}, expected {expected_unit!r}",
            )
    return element_id, channel


def _element_limit(
    profile: MachineProfile, element_id: str, channel: str
) -> tuple[float, float] | None:
    element = profile.get_element(element_id)
    limit = element.limits_for(channel)
    if not limit or limit.get("low") is None or limit.get("high") is None:
        return None
    return float(limit["low"]), float(limit["high"])


def validate_energy_feedback_workflow(
    profile: MachineProfile,
    workflow: Mapping[str, Any],
    *,
    control_backend: str | None = None,
) -> None:
    """Validate monitoring, shadow calculation, and explicitly enabled writes."""

    backends = [
        _text(profile, item, "control_backends[]")
        for item in _sequence(
            profile, workflow.get("control_backends"), "control_backends"
        )
    ]
    if not backends:
        _fail(profile, "control_backends must not be empty")
    if len(set(backends)) != len(backends):
        _fail(profile, "control_backends must not contain duplicates")
    if set(backends) != {"real"}:
        _fail(profile, "the first release supports only the real backend")
    unknown = sorted(set(backends) - set(profile.control_backends))
    if unknown:
        _fail(profile, f"control_backends contains unsupported values: {unknown}")
    if control_backend is not None and control_backend not in backends:
        _fail(profile, f"backend {control_backend!r} is not configured")
    real_status = workflow.get("real_status")
    if real_status not in {"read_only", "write_smoke_passed", "commissioned"}:
        _fail(
            profile,
            "real_status must be 'read_only', 'write_smoke_passed', or 'commissioned'",
        )

    write_control = _mapping(profile, workflow.get("write_control"), "write_control")
    if write_control.get("default") != "blocked":
        _fail(profile, "write_control.default must be 'blocked'")
    for key, value in write_control.items():
        if value not in {"allowed", "blocked"}:
            _fail(profile, f"write_control.{key} must be 'allowed' or 'blocked'")
    write_backends = {
        backend
        for backend in backends
        if write_control.get(backend, write_control.get("default")) == "allowed"
    }
    for backend in backends:
        if write_control.get(backend, write_control.get("default")) not in {
            "allowed",
            "blocked",
        }:
            _fail(profile, f"write_control for backend {backend!r} is invalid")
    if write_backends and real_status not in {"write_smoke_passed", "commissioned"}:
        _fail(profile, "write-enabled real backend requires a write-capable real_status")
    if not write_backends and real_status != "read_only":
        _fail(profile, "a non-writing profile must use real_status='read_only'")

    measurement = _mapping(profile, workflow.get("measurement"), "measurement")
    measurement_kind = _text(
        profile, measurement.get("kind"), "measurement.kind"
    )
    if measurement_kind not in {"direct_energy", "energy_spectrum_target"}:
        _fail(
            profile,
            "measurement.kind must be 'direct_energy' or "
            "'energy_spectrum_target'",
        )
    _text(profile, measurement.get("label"), "measurement.label")
    capabilities = _sequence(
        profile, measurement.get("capabilities"), "measurement.capabilities"
    )
    capability_names = [
        _text(profile, capability, f"measurement.capabilities[{index}]")
        for index, capability in enumerate(capabilities)
    ]
    if len(capability_names) != len(set(capability_names)):
        _fail(profile, "measurement.capabilities must not contain duplicates")
    if "energy" not in capability_names:
        _fail(profile, "measurement.capabilities must include 'energy'")
    energy_id, energy_channel = _signal(
        profile,
        measurement.get("energy"),
        "measurement.energy",
        backends,
        expected_unit="MeV",
    )
    diagnostics = _mapping(
        profile, measurement.get("diagnostics", {}), "measurement.diagnostics"
    )
    for diagnostic_name, signal in diagnostics.items():
        diagnostic = _mapping(
            profile,
            signal,
            f"measurement.diagnostics.{diagnostic_name}",
        )
        unit = _text(
            profile,
            diagnostic.get("unit"),
            f"measurement.diagnostics.{diagnostic_name}.unit",
        )
        _signal(
            profile,
            diagnostic,
            f"measurement.diagnostics.{diagnostic_name}",
            backends,
            expected_unit=unit,
        )
    diagnostic_capabilities = set(capability_names) - {"energy"}
    if set(diagnostics) != diagnostic_capabilities:
        _fail(
            profile,
            "measurement.diagnostics keys must exactly match non-energy "
            "measurement.capabilities",
        )
    valid_range = _range(
        profile, measurement.get("valid_range_mev"), "measurement.valid_range_mev"
    )
    median_window = _integer(
        profile, measurement.get("median_window", 1), "measurement.median_window", positive=True
    )
    if median_window % 2 == 0:
        _fail(profile, "measurement.median_window must be odd")

    target = workflow.get("target")
    if target is not None:
        target_id, target_channel = _signal(
            profile,
            target,
            "target",
            backends,
            expected_unit="MeV",
        )
        target_limit = _element_limit(profile, target_id, target_channel)
        if target_limit is None:
            _fail(profile, "target channel must define a machine-profile absolute limit")
        if valid_range[0] < target_limit[0] or valid_range[1] > target_limit[1]:
            _fail(
                profile,
                "measurement.valid_range_mev must stay inside the target machine limit",
            )
    energy_limit = _element_limit(profile, energy_id, energy_channel)
    if energy_limit and (
        valid_range[0] < energy_limit[0] or valid_range[1] > energy_limit[1]
    ):
        _fail(profile, "measurement.valid_range_mev exceeds the measurement machine limit")

    pulse = _mapping(profile, workflow.get("pulse"), "pulse")
    require_marker = pulse.get("require_marker_for_write")
    if not isinstance(require_marker, bool):
        _fail(profile, "pulse.require_marker_for_write must be boolean")
    marker = pulse.get("marker")
    if marker is not None:
        _signal(profile, marker, "pulse.marker", backends)
    if write_backends and require_marker and marker is None:
        _fail(profile, "write-enabled feedback requires a configured pulse marker")

    groups = _sequence(profile, workflow.get("control_groups"), "control_groups")
    group_ids: set[str] = set()
    setpoint_pvs: set[str] = set()
    for group_index, raw_group in enumerate(groups):
        base = f"control_groups[{group_index}]"
        group = _mapping(profile, raw_group, base)
        group_id = _text(profile, group.get("id"), f"{base}.id")
        if group_id in group_ids:
            _fail(profile, f"duplicate control-group id {group_id!r}")
        group_ids.add(group_id)
        _text(profile, group.get("label", group_id), f"{base}.label")
        response = _number(
            profile,
            group.get("response_mev_per_knob_unit"),
            f"{base}.response_mev_per_knob_unit",
        )
        if response == 0:
            _fail(profile, f"{base}.response_mev_per_knob_unit must be signed and non-zero")
        gain = _number(profile, group.get("loop_gain"), f"{base}.loop_gain", positive=True)
        if gain > 1:
            _fail(profile, f"{base}.loop_gain must not exceed 1")
        _number(
            profile, group.get("max_knob_step"), f"{base}.max_knob_step", positive=True
        )
        deadband = _number(
            profile, group.get("deadband_mev", 0), f"{base}.deadband_mev", nonnegative=True
        )
        action_window = _number(
            profile,
            group.get("action_window_mev"),
            f"{base}.action_window_mev",
            positive=True,
        )
        if action_window <= deadband:
            _fail(profile, f"{base}.action_window_mev must exceed deadband_mev")

        actuators = _sequence(profile, group.get("actuators"), f"{base}.actuators")
        if not actuators:
            _fail(profile, f"{base}.actuators must not be empty")
        actuator_ids: set[str] = set()
        for actuator_index, raw_actuator in enumerate(actuators):
            item = f"{base}.actuators[{actuator_index}]"
            actuator = _mapping(profile, raw_actuator, item)
            actuator_id = _text(profile, actuator.get("id"), f"{item}.id")
            if actuator_id in actuator_ids:
                _fail(profile, f"duplicate actuator id {actuator_id!r} in group {group_id!r}")
            actuator_ids.add(actuator_id)
            element_id = _text(profile, actuator.get("element"), f"{item}.element")
            setpoint = _text(profile, actuator.get("setpoint"), f"{item}.setpoint")
            readback = _text(profile, actuator.get("readback"), f"{item}.readback")
            if setpoint == readback:
                _fail(profile, f"{item} requires independent setpoint and readback channels")
            unit = _text(profile, actuator.get("unit"), f"{item}.unit")
            _signal(
                profile,
                {"element": element_id, "channel": setpoint, "unit": unit},
                f"{item}.setpoint",
                backends,
                expected_unit=unit,
            )
            _signal(
                profile,
                {"element": element_id, "channel": readback},
                f"{item}.readback",
                backends,
            )
            element = profile.get_element(element_id)
            for backend in backends:
                setpoint_pv = element.channels[setpoint][backend]
                readback_pv = element.channels[readback][backend]
                if setpoint_pv == readback_pv:
                    _fail(
                        profile,
                        f"{item} setpoint and readback resolve to the same PV on {backend}",
                    )
                if setpoint_pv in setpoint_pvs:
                    _fail(
                        profile,
                        f"{item}.setpoint duplicates another actuator PV on {backend}",
                    )
                setpoint_pvs.add(setpoint_pv)
            coefficient = _number(
                profile, actuator.get("coefficient"), f"{item}.coefficient"
            )
            if coefficient == 0:
                _fail(profile, f"{item}.coefficient must be non-zero")
            max_step = _number(
                profile, actuator.get("max_step"), f"{item}.max_step", positive=True
            )
            max_session_delta = _number(
                profile,
                actuator.get("max_session_delta"),
                f"{item}.max_session_delta",
                positive=True,
            )
            if max_session_delta < max_step:
                _fail(profile, f"{item}.max_session_delta must be at least max_step")
            _number(
                profile,
                actuator.get("verify_tolerance"),
                f"{item}.verify_tolerance",
                positive=True,
            )
            operating_range = _range(
                profile, actuator.get("operating_range"), f"{item}.operating_range"
            )
            cyclic_period = actuator.get("cyclic_period")
            if cyclic_period is not None:
                cyclic_period = _number(
                    profile,
                    cyclic_period,
                    f"{item}.cyclic_period",
                    positive=True,
                )
                if not math.isclose(
                    operating_range[1] - operating_range[0],
                    cyclic_period,
                    rel_tol=1e-9,
                    abs_tol=1e-9,
                ):
                    _fail(
                        profile,
                        f"{item}.operating_range must span one cyclic_period",
                    )
            machine_limit = _element_limit(profile, element_id, setpoint)
            if machine_limit is None:
                _fail(profile, f"{item}.setpoint must define a machine-profile absolute limit")
            if (
                operating_range[0] < machine_limit[0]
                or operating_range[1] > machine_limit[1]
            ):
                _fail(profile, f"{item}.operating_range exceeds the machine-profile limit")

    if write_backends and len(groups) != 1:
        _fail(profile, "write-enabled feedback requires exactly one control group")

    write_verification = _mapping(
        profile,
        workflow.get("write_verification", {}),
        "write_verification",
    )
    if write_backends:
        timeout = _number(
            profile,
            write_verification.get("timeout_s"),
            "write_verification.timeout_s",
            positive=True,
        )
        poll_interval = _number(
            profile,
            write_verification.get("poll_interval_s"),
            "write_verification.poll_interval_s",
            positive=True,
        )
        if poll_interval > timeout:
            _fail(profile, "write_verification.poll_interval_s must not exceed timeout_s")
        precision = _integer(
            profile,
            write_verification.get("command_precision"),
            "write_verification.command_precision",
        )
        if not 0 <= precision <= 6:
            _fail(profile, "write_verification.command_precision must be between 0 and 6")

        operator_controls = _mapping(
            profile,
            workflow.get("operator_controls", {}),
            "operator_controls",
        )
        target_range = _operator_range(
            profile,
            operator_controls.get("target"),
            "operator_controls.target",
        )
        if target_range[0] < valid_range[0] or target_range[1] > valid_range[1]:
            _fail(profile, "operator_controls.target exceeds measurement valid range")
        gain_range = _operator_range(
            profile,
            operator_controls.get("gain"),
            "operator_controls.gain",
            positive=True,
        )
        if gain_range[1] > 1:
            _fail(profile, "operator_controls.gain maximum must not exceed 1")
        _operator_range(
            profile,
            operator_controls.get("max_knob_step"),
            "operator_controls.max_knob_step",
            positive=True,
        )
        _operator_range(
            profile,
            operator_controls.get("action_window"),
            "operator_controls.action_window",
            positive=True,
        )
        _operator_range(
            profile,
            operator_controls.get("max_session_delta"),
            "operator_controls.max_session_delta",
            positive=True,
        )
        ratio = _mapping(
            profile,
            operator_controls.get("actuator_ratio"),
            "operator_controls.actuator_ratio",
        )
        ratio_actuator = _text(
            profile,
            ratio.get("actuator"),
            "operator_controls.actuator_ratio.actuator",
        )
        actuator_ids = {
            str(item["id"])
            for item in groups[0]["actuators"]
        }
        if ratio_actuator not in actuator_ids:
            _fail(profile, "operator_controls.actuator_ratio.actuator is not configured")
        ratio_sign = _number(
            profile,
            ratio.get("sign"),
            "operator_controls.actuator_ratio.sign",
        )
        if ratio_sign not in {-1.0, 1.0}:
            _fail(profile, "operator_controls.actuator_ratio.sign must be -1 or 1")
        ratio_min = _number(
            profile,
            ratio.get("minimum"),
            "operator_controls.actuator_ratio.minimum",
            positive=True,
        )
        ratio_max = _number(
            profile,
            ratio.get("maximum"),
            "operator_controls.actuator_ratio.maximum",
            positive=True,
        )
        if ratio_min >= ratio_max:
            _fail(profile, "operator_controls.actuator_ratio range is invalid")
        ratio_decimals = _integer(
            profile,
            ratio.get("decimals", 3),
            "operator_controls.actuator_ratio.decimals",
        )
        if not 0 <= ratio_decimals <= 6:
            _fail(
                profile,
                "operator_controls.actuator_ratio.decimals must be between 0 and 6",
            )

    runtime = _mapping(profile, workflow.get("runtime"), "runtime")
    for key in (
        "sample_period_s",
        "plot_period_s",
        "connection_timeout_s",
        "stale_timeout_s",
    ):
        _number(profile, runtime.get(key), f"runtime.{key}", positive=True)
    _integer(
        profile,
        runtime.get("max_history_points"),
        "runtime.max_history_points",
        positive=True,
    )

    logging = _mapping(profile, workflow.get("logging"), "logging")
    _text(profile, logging.get("file_prefix"), "logging.file_prefix")
    _integer(
        profile,
        logging.get("flush_every_n_rows"),
        "logging.flush_every_n_rows",
        positive=True,
    )
