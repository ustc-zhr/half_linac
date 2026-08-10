from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
import math
import time
from typing import Any

from half_linac.src.apps.dispersion_correction.calibration import (
    calibration_actuator_per_delta,
    is_direct_delta_actuator,
)
from half_linac.src.apps.dispersion_correction.models import RunConfig


@dataclass(frozen=True)
class PreflightResult:
    level: str
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    checks: dict[str, bool]

    @property
    def ok(self) -> bool:
        return not self.blockers

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "level": self.level,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "checks": dict(self.checks),
        }


@dataclass(frozen=True)
class LivePreflightResult:
    static: PreflightResult
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    checks: dict[str, bool]
    readings: dict[str, Any]

    @property
    def ok(self) -> bool:
        return self.static.ok and not self.blockers

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "static": self.static.as_dict(),
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "checks": dict(self.checks),
            "readings": dict(self.readings),
        }


def run_preflight(config: RunConfig) -> PreflightResult:
    blockers: list[str] = []
    warnings: list[str] = []
    checks: dict[str, bool] = {}

    pv_map = config.backend.options.get("pv_map", {})
    pv_map_ok = isinstance(pv_map, dict)
    checks["pv_map_is_mapping"] = pv_map_ok
    if config.backend.type == "epics" and not pv_map_ok:
        blockers.append("EPICS backend requires backend.options.pv_map")

    bpms = pv_map.get("bpms", {}) if pv_map_ok else {}
    bpms_ok = _configured_bpms(config, bpms)
    checks["measurement_bpm_pvs_configured"] = bpms_ok
    if config.measurement.plane in {"x", "xy"}:
        checks["target_bpm_x_pvs_configured"] = bpms_ok
    if config.backend.type == "epics" and not bpms_ok:
        required = (
            "x and y"
            if config.measurement.plane == "xy"
            else config.measurement.plane
        )
        blockers.append(
            f"Every measurement BPM needs {required} PV mapping(s) "
            "in pv_map.bpms"
        )

    quadrupoles = pv_map.get("quadrupoles", {}) if pv_map_ok else {}
    quad_pvs_ok = _configured_quadrupoles(config, quadrupoles)
    checks["quadrupole_pvs_configured"] = quad_pvs_ok
    if config.backend.type == "epics" and not quad_pvs_ok:
        blockers.append("Every knob device needs quadrupole PV mapping")

    energy_map = pv_map.get("energy_knob", {}) if pv_map_ok else {}
    energy_pv_ok = isinstance(energy_map, dict) and bool(_energy_read_pv(energy_map))
    checks["energy_pv_configured"] = energy_pv_ok
    if config.backend.type == "epics" and not energy_pv_ok:
        blockers.append("Energy knob PV is not configured")
    energy_independent_readback = isinstance(energy_map, dict) and bool(
        energy_map.get("readback") or energy_map.get("phase_readback")
    )
    checks["energy_independent_readback"] = energy_independent_readback
    if config.backend.type == "epics" and energy_pv_ok and not energy_independent_readback:
        warnings.append("Energy verification uses the setpoint PV because no independent readback is configured")

    direct_delta = is_direct_delta_actuator(config.energy_knob.actuator)
    try:
        actuator_per_delta = calibration_actuator_per_delta(
            config.energy_knob.calibration
        )
    except (TypeError, ValueError):
        actuator_per_delta = None
    calibration_ok = direct_delta or actuator_per_delta is not None
    checks["energy_calibration_available"] = calibration_ok
    if not direct_delta and not calibration_ok:
        blockers.append(
            "Physical energy actuator requires calibration.actuator_per_delta "
            "before quantitative D_eff measurement"
        )

    safe_limits_ok = (
        config.safety.max_reference_orbit_change_mm > 0
    )
    checks["safety_limits_positive"] = safe_limits_ok
    if not safe_limits_ok:
        blockers.append("BPM orbit threshold must be positive")

    knob_limits_ok = all(
        knob.scan_step > 0 and knob.limit > 0 and knob.scan_step <= knob.limit
        for knob in config.runtime_knobs
    )
    checks["knob_limits_ordered"] = knob_limits_ok
    if not knob_limits_ok:
        blockers.append("Require 0 < scan_step <= limit for every knob")

    response_target_count = (
        len(config.section.joint_response_analysis.targets)
        if config.section.joint_response_analysis.enabled
        else len(config.target_bpms)
    )
    response_dimensions_ok = response_target_count >= len(config.runtime_knobs)
    checks["response_dimensions_sufficient"] = response_dimensions_ok
    if not response_dimensions_ok and not config.section.diagnostic_only:
        target_label = (
            "target observations"
            if config.section.joint_response_analysis.enabled
            else "target BPMs"
        )
        warnings.append(
            f"Underdetermined response: {len(config.runtime_knobs)} correction knobs and "
            f"{response_target_count} {target_label}. Response measurement is allowed; "
            "effective SVD modes will be checked before correction."
        )

    timing_ok = (
        config.backend.type != "epics"
        or (
            config.measurement.settle_time_s > 0
            and config.measurement.sample_interval_s > 0
        )
    )
    checks["real_machine_timing_positive"] = timing_ok
    if not timing_ok:
        blockers.append(
            "EPICS operation requires positive measurement.settle_time_s and sample_interval_s"
        )

    if config.backend.type == "epics" and config.backend.mode == "write_enabled":
        energy_write_ok = isinstance(energy_map, dict) and bool(_energy_set_pv(energy_map))
        quadrupole_write_ok = _configured_quadrupole_writes(config, quadrupoles)
        checks["energy_write_pv_configured"] = energy_write_ok
        checks["quadrupole_write_pvs_configured"] = quadrupole_write_ok
        if not energy_write_ok:
            blockers.append("write_enabled requires an energy set PV")
        if not quadrupole_write_ok:
            blockers.append("write_enabled requires same-unit setpoint and readback PVs for every knob device")
        quadrupole_independent_readbacks = _configured_independent_quadrupole_readbacks(config, quadrupoles)
        checks["quadrupole_independent_readbacks"] = quadrupole_independent_readbacks
        if not quadrupole_independent_readbacks and not config.section.diagnostic_only:
            warnings.append("Quadrupole verification uses setpoint PVs because independent readbacks are not configured")
        write_ready = calibration_ok and safe_limits_ok and knob_limits_ok and not blockers
        checks["write_ready"] = write_ready
        if not write_ready:
            blockers.append("write_enabled requires calibration, limits, PV mapping, and static checks to pass")
    else:
        checks["write_ready"] = False

    level = _readiness_level(config, blockers, checks)
    return PreflightResult(level=level, blockers=tuple(blockers), warnings=tuple(warnings), checks=checks)


def run_live_preflight(config: RunConfig, machine=None) -> LivePreflightResult:
    """Read every required live value, retrying transient CA failures without writes."""

    if config.backend.type != "epics":
        return _run_live_preflight_once(config, machine)

    if machine is None:
        from half_linac.src.apps.dispersion_correction.machine.epics import EpicsMachine

        readonly_config = replace(
            config,
            backend=replace(config.backend, mode="read_only"),
        )
        machine = EpicsMachine(readonly_config)

    attempts = max(
        1,
        min(
            5,
            int(config.backend.options.get("live_preflight_attempts", 2)),
        ),
    )
    retry_interval = max(
        0.0,
        min(
            2.0,
            float(
                config.backend.options.get(
                    "live_preflight_retry_interval_s",
                    0.25,
                )
            ),
        ),
    )
    result: LivePreflightResult | None = None
    for attempt in range(1, attempts + 1):
        result = _run_live_preflight_once(config, machine)
        result = replace(
            result,
            readings={
                **result.readings,
                "live_preflight_attempt": attempt,
                "live_preflight_attempts_allowed": attempts,
            },
        )
        if result.ok or not result.static.ok or attempt == attempts:
            return result
        if retry_interval > 0:
            time.sleep(retry_interval)
    assert result is not None
    return result


def _run_live_preflight_once(
    config: RunConfig,
    machine=None,
) -> LivePreflightResult:
    """Perform one read-only pass over every required live value."""

    static = run_preflight(config)
    if config.backend.type != "epics":
        return LivePreflightResult(
            static=static,
            blockers=(),
            warnings=(),
            checks={"live_io_required": False},
            readings={},
        )

    blockers: list[str] = []
    warnings: list[str] = []
    checks: dict[str, bool] = {"live_io_required": True}
    readings: dict[str, Any] = {}

    try:
        energy_value = float(machine.get_energy_delta())
    except Exception as exc:
        energy_value = float("nan")
        blockers.append(f"Energy setpoint is not readable: {exc}")
    energy_ok = math.isfinite(energy_value)
    checks["energy_setpoint_readable"] = energy_ok
    readings["energy_value"] = energy_value if energy_ok else None

    if config.section.diagnostic_only:
        quadrupole_readbacks = {}
        quadrupole_setpoints = {}
        quadrupoles_ok = True
    else:
        try:
            quadrupole_readbacks = machine.read_quadrupole_readbacks()
            quadrupole_setpoints = machine.read_quadrupole_setpoints()
        except Exception as exc:
            quadrupole_readbacks = {}
            quadrupole_setpoints = {}
            blockers.append(f"Quadrupole values are not readable: {exc}")
        quadrupoles_ok = bool(quadrupole_readbacks) and set(quadrupole_readbacks) == set(quadrupole_setpoints)
    if quadrupoles_ok:
        quadrupoles_ok = all(
            math.isfinite(float(quadrupole_readbacks[name]))
            and math.isfinite(float(quadrupole_setpoints[name]))
            for name in quadrupole_readbacks
        )
    checks["quadrupole_values_readable"] = quadrupoles_ok
    readings["quadrupole_readbacks"] = quadrupole_readbacks
    readings["quadrupole_setpoints"] = quadrupole_setpoints
    if not quadrupoles_ok and not any(item.startswith("Quadrupole") for item in blockers):
        blockers.append("Quadrupole setpoints/readbacks are incomplete or non-finite")

    quadrupole_matches = quadrupoles_ok
    mismatches: dict[str, dict[str, float]] = {}
    if quadrupoles_ok:
        for name, readback in quadrupole_readbacks.items():
            tolerance = float(machine.quadrupole_readback_tolerance(name))
            difference = abs(float(quadrupole_setpoints[name]) - float(readback))
            if difference > tolerance:
                quadrupole_matches = False
                mismatches[name] = {
                    "difference": difference,
                    "tolerance": tolerance,
                }
    checks["quadrupole_setpoint_readback_match"] = quadrupole_matches
    readings["quadrupole_mismatches"] = mismatches
    if quadrupoles_ok and not quadrupole_matches:
        blockers.append("Quadrupole setpoint/readback mismatch exceeds configured tolerance")

    try:
        bpm = machine.read_bpm(config.measurement_bpms)
        valid_count = int(bpm.valid.sum())
    except Exception as exc:
        bpm = None
        valid_count = 0
        blockers.append(f"Measurement BPMs are not readable: {exc}")
    required_valid = len(config.measurement_bpms)
    bpm_ok = valid_count >= required_valid
    checks["all_measurement_bpms_valid"] = bpm_ok
    checks["all_target_bpms_valid"] = bpm_ok
    readings["valid_bpm_count"] = valid_count
    readings["required_valid_bpm_count"] = required_valid
    if bpm is not None:
        readings["bpms"] = {
            name: {
                "x_mm": float(bpm.x_mm[index]) if math.isfinite(float(bpm.x_mm[index])) else None,
                "y_mm": float(bpm.y_mm[index]) if math.isfinite(float(bpm.y_mm[index])) else None,
                "valid": bool(bpm.valid[index]),
            }
            for index, name in enumerate(bpm.names)
        }
    if not bpm_ok and not any(
        item.startswith("Measurement BPMs") for item in blockers
    ):
        blockers.append(
            "All measurement BPMs must be valid before the energy scan "
            f"({valid_count}/{required_valid} valid)"
        )

    readings["planned_energy_delta"] = config.energy_knob.delta
    readings["planned_knob_ranges"] = {
        knob.name: {
            "response_scan": knob.scan_step,
            "cumulative_limit": knob.limit,
            "max_solver_step": knob.limit * config.solver.max_step_fraction,
        }
        for knob in config.runtime_knobs
    }
    return LivePreflightResult(
        static=static,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
        checks=checks,
        readings=readings,
    )


def format_preflight(result: PreflightResult) -> str:
    lines = [
        "Dispersion Correction Preflight",
        "",
        f"Level: {result.level}",
        f"OK: {result.ok}",
        "",
        "Checks:",
    ]
    for name, passed in result.checks.items():
        lines.append(f"  {'PASS' if passed else 'FAIL'}  {name}")

    lines.extend(["", "Blockers:"])
    if result.blockers:
        lines.extend(f"  - {item}" for item in result.blockers)
    else:
        lines.append("  - none")

    lines.extend(["", "Warnings:"])
    if result.warnings:
        lines.extend(f"  - {item}" for item in result.warnings)
    else:
        lines.append("  - none")
    return "\n".join(lines) + "\n"


def _configured_bpms(config: RunConfig, bpms: Any) -> bool:
    if config.backend.type == "offline" and not bpms:
        return True
    if not isinstance(bpms, dict):
        return False
    required_planes = config.measurement.planes
    for name in config.measurement_bpms:
        item = bpms.get(name)
        if (
            not isinstance(item, dict)
            or any(not item.get(plane) for plane in required_planes)
        ):
            return False
    return True


def _energy_read_pv(energy_map: dict[str, Any]) -> object:
    return (
        energy_map.get("readback")
        or energy_map.get("phase_readback")
        or energy_map.get("set")
        or energy_map.get("phase_set")
    )


def _energy_set_pv(energy_map: dict[str, Any]) -> object:
    return energy_map.get("set") or energy_map.get("phase_set")


def _configured_quadrupoles(config: RunConfig, quadrupoles: Any) -> bool:
    if config.backend.type == "offline" and not quadrupoles:
        return True
    if not isinstance(quadrupoles, dict):
        return False
    for knob in config.runtime_knobs:
        for device in knob.devices:
            item = quadrupoles.get(device)
            if not isinstance(item, dict):
                return False
            control = str(item.get("control", "k1")).lower()
            if control == "current" and not (item.get("current_readback") or item.get("current_set")):
                return False
            if control == "k1" and not (item.get("K1") or item.get("K1_readback")):
                return False
            if control not in {"current", "k1"}:
                return False
    return True


def _configured_quadrupole_writes(config: RunConfig, quadrupoles: Any) -> bool:
    if not isinstance(quadrupoles, dict):
        return False
    for knob in config.runtime_knobs:
        for device in knob.devices:
            item = quadrupoles.get(device)
            if not isinstance(item, dict):
                return False
            control = str(item.get("control", "k1")).lower()
            if control == "current" and not item.get("current_set"):
                return False
            if control == "current" and not (item.get("current_readback") or item.get("current_set")):
                return False
            if control == "k1" and not (item.get("K1_set") or item.get("K1")):
                return False
            if control == "k1" and not (item.get("K1_readback") or item.get("K1")):
                return False
            if control not in {"current", "k1"}:
                return False
    return True


def _configured_independent_quadrupole_readbacks(config: RunConfig, quadrupoles: Any) -> bool:
    if not isinstance(quadrupoles, dict):
        return False
    for knob in config.runtime_knobs:
        for device in knob.devices:
            item = quadrupoles.get(device)
            if not isinstance(item, dict):
                return False
            control = str(item.get("control", "k1")).lower()
            readback_key = "current_readback" if control == "current" else "K1_readback"
            if not item.get(readback_key):
                return False
    return True


def _readiness_level(
    config: RunConfig,
    blockers: list[str],
    checks: dict[str, bool],
) -> str:
    if blockers:
        return "blocked"
    if config.backend.type == "offline":
        return "offline-ready"
    if config.backend.mode != "write_enabled":
        return "read-only-ready"
    if checks.get("energy_calibration_available"):
        return "write-ready"
    return "measurement-ready"
