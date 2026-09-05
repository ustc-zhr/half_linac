from __future__ import annotations

from typing import Any

from half_linac.src.apps.dispersion_correction.models import RunConfig
from half_linac.src.apps.dispersion_correction.calibration import (
    actuator_step_for_delta,
    effective_delta_for_integer_actuator_step,
    is_direct_delta_actuator,
)
from half_linac.src.apps.dispersion_correction.physics import momentum_delta


def build_operation_plan(config: RunConfig) -> dict[str, Any]:
    delta = momentum_delta(
        config.energy_knob.delta,
    )
    if config.energy_knob.round_actuator_step_to_integer:
        delta = effective_delta_for_integer_actuator_step(
            delta,
            config.energy_knob.calibration,
        )
    pv_map = config.backend.options.get("pv_map", {})
    energy_map = pv_map.get("energy_knob", {}) if isinstance(pv_map, dict) else {}
    bpm_map = pv_map.get("bpms", {}) if isinstance(pv_map, dict) else {}
    quadrupole_map = pv_map.get("quadrupoles", {}) if isinstance(pv_map, dict) else {}
    bpm_planes = config.measurement.planes

    warnings = []
    if config.backend.type == "epics" and config.backend.mode != "read_only":
        warnings.append("EPICS config is not read_only; verify write permission and machine protection before use.")
    if not is_direct_delta_actuator(config.energy_knob.actuator):
        if config.energy_knob.calibration:
            actuator_plan = actuator_step_for_delta(delta, config.energy_knob.calibration)
            if not actuator_plan.get("calibrated"):
                warnings.append(f"Energy actuator calibration is incomplete: {actuator_plan.get('reason')}")
        else:
            actuator_plan = {"calibrated": False, "reason": "Missing energy_knob.calibration"}
            warnings.append(
                "Physical energy actuator requires actuator-to-dp/p calibration."
            )
    else:
        actuator_plan = {
            "calibrated": True,
            "direct_delta": True,
            "actuator_step": delta,
            "plus_offset": delta,
            "minus_offset": -delta,
            "actuator_per_delta": 1.0,
        }
    return {
        "section": {
            "id": config.section.id,
            "display_name": config.section.display_name,
            "model_entrance": config.section.model_entrance,
            "model_exit": config.section.model_exit,
            "model_only": config.section.model_only,
            "diagnostic_only": config.section.diagnostic_only,
            "model_observables": [
                {
                    "name": observable.name,
                    "element": observable.element,
                    "component": observable.component,
                    "target": observable.target,
                    "unit": observable.unit,
                }
                for observable in config.section.model_observables
            ],
        },
        "backend": {"type": config.backend.type, "mode": config.backend.mode},
        "energy": {
            "name": config.energy_knob.name,
            "delta_configured": config.energy_knob.delta,
            "delta_momentum": delta,
            "unit": "delta_p_over_p",
            "actuator": config.energy_knob.actuator,
            "actuator_unit": config.energy_knob.actuator_unit,
            "actuator_plan": actuator_plan,
            "pv_map": energy_map,
        },
        "bpms": [
            {
                "name": name,
                "role": "monitor",
                "target_dispersion_mm": None,
                "read_pv": bpm_map.get(name, {}).get(bpm_planes[0])
                if isinstance(bpm_map.get(name), dict)
                else None,
                "read_pvs": {
                    plane: bpm_map.get(name, {}).get(plane)
                    for plane in bpm_planes
                }
                if isinstance(bpm_map.get(name), dict)
                else {},
            }
            for name in config.monitor_bpms
        ]
        + [
            {
                "name": name,
                "role": "correction",
                "target_dispersion_mm": config.section.target_dispersion_mm[index],
                "read_pv": bpm_map.get(name, {}).get(bpm_planes[0])
                if isinstance(bpm_map.get(name), dict)
                else None,
                "read_pvs": {
                    plane: bpm_map.get(name, {}).get(plane)
                    for plane in bpm_planes
                }
                if isinstance(bpm_map.get(name), dict)
                else {},
            }
            for index, name in enumerate(config.target_bpms)
        ],
        "knobs": [
            {
                "name": knob.name,
                "devices": [
                    {
                        "name": device,
                        "scale": scale,
                        "pv_map": quadrupole_map.get(device, {}) if isinstance(quadrupole_map, dict) else {},
                    }
                    for device, scale in knob.devices.items()
                ],
                "scan_step": knob.scan_step,
                "mode": knob.scan_mode,
                "unit": knob.unit or _knob_unit(knob.devices, quadrupole_map),
                "scan_targets": [
                    {"direction": "+", "delta": knob.scan_step},
                    {"direction": "-", "delta": -knob.scan_step},
                ],
                "limit": knob.limit,
            }
            for knob in config.knobs
        ],
        "workflow": (
            [
                "save the initial energy setpoint",
                "measure monitor BPM dispersion with +delta and -delta energy settings",
                "restore the initial energy setpoint",
                "display the measured points and optional model references",
            ]
            if config.section.diagnostic_only
            else [
                "snapshot current state",
                "measure initial D_eff with +delta and -delta energy settings",
                (
                    "measure the response matrix once and reuse it for later iterations"
                    if config.solver.response_update == "once"
                    else "remeasure the response matrix in every correction iteration"
                ),
                "restore the pre-scan state after each response column",
                "solve a normalized bounded least-squares correction step",
                "accept only if D_eff RMS improves and safety checks pass",
                "restore best accepted state and produce report",
            ]
        ),
        "solver": {
            "svd_cut": config.solver.svd_cut,
            "regularization": config.solver.regularization,
            "gain": config.solver.gain,
            "max_step_fraction": config.solver.max_step_fraction,
            "max_iter": config.solver.max_iter,
            "response_update": config.solver.response_update,
            "min_step_improvement": config.solver.min_step_improvement,
        },
        "measurement": {
            "planes": list(config.measurement.planes),
            "samples_per_step": config.measurement.samples_per_step,
            "sample_interval_s": config.measurement.sample_interval_s,
            "final_samples": config.measurement.final_samples,
            "settle_time_s": config.measurement.settle_time_s,
        },
        "safety": {
            "max_reference_orbit_change_mm": config.safety.max_reference_orbit_change_mm,
        },
        "warnings": warnings,
    }


def format_operation_plan(plan: dict[str, Any]) -> str:
    lines = [
        "Dispersion Correction Dry Run",
        "",
        f"Backend: {plan['backend']['type']} ({plan['backend']['mode']})",
        f"Section: {plan['section']['display_name']} ({plan['section']['id']})",
        (
            "Energy perturbation: "
            f"{plan['energy']['name']} +/-{plan['energy']['delta_momentum']} dp/p"
        ),
        f"BPMs: {', '.join(item['name'] for item in plan['bpms'])}",
    ]
    observables = plan["section"].get("model_observables", [])
    if observables:
        lines.append(
            "Model observables: "
            + ", ".join(
                f"{item['name']}={item['target']:.6g} {item['unit']}"
                for item in observables
            )
        )
    lines.extend(
        [
            f"Response update: {plan['solver']['response_update']}",
            f"Gain: {plan['solver']['gain']:g}",
            f"Max step: {100.0 * plan['solver']['max_step_fraction']:g}% of each knob limit",
            f"Scan samples: {plan['measurement']['samples_per_step']}",
            f"Sample interval: {plan['measurement']['sample_interval_s']:g} s",
            f"Verification samples: {plan['measurement']['final_samples']}",
            f"Settle time: {plan['measurement']['settle_time_s']:g} s",
            "",
            "Knobs:",
        ]
    )
    for knob in plan["knobs"]:
        devices = ", ".join(f"{item['name']}*{item['scale']:g}" for item in knob["devices"])
        unit = f" {knob['unit']}" if knob.get("unit") else ""
        lines.append(
            f"  {knob['name']}: {devices}; scan=+/-{knob['scan_step']:g}{unit}; "
            f"limit={knob['limit']:g}{unit}"
        )

    lines.extend(["", "Workflow:"])
    actuator_plan = plan["energy"].get("actuator_plan", {})
    if actuator_plan.get("calibrated"):
        lines.extend(
            [
                "",
                "Energy actuator:",
                (
                    f"  +/-{actuator_plan['actuator_step']:.6g} "
                    f"{plan['energy']['actuator_unit']} for +/-{plan['energy']['delta_momentum']:.6g} dp/p"
                ),
            ]
        )
    for index, step in enumerate(plan["workflow"], start=1):
        lines.append(f"  {index}. {step}")

    lines.extend(["", "Warnings:"])
    if plan["warnings"]:
        for warning in plan["warnings"]:
            lines.append(f"  - {warning}")
    else:
        lines.append("  - none")
    return "\n".join(lines) + "\n"


def _knob_unit(devices: dict[str, float], quadrupole_map: dict) -> str:
    controls = {
        str(quadrupole_map.get(device, {}).get("control", "k1")).lower()
        for device in devices
        if isinstance(quadrupole_map.get(device), dict)
    }
    if controls == {"current"}:
        return "A"
    if controls == {"k1"}:
        return "K1 [1/m²]"
    return ""
