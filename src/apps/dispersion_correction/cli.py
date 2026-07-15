from __future__ import annotations

import argparse
from dataclasses import replace
import json
import math
import sys

from half_linac.src.apps.dispersion_correction.config import load_config
from half_linac.src.apps.dispersion_correction.calibration import load_phase_calibration_csv
from half_linac.src.apps.dispersion_correction.dryrun import build_operation_plan, format_operation_plan
from half_linac.src.apps.dispersion_correction.preflight import format_preflight, run_preflight
from half_linac.src.apps.dispersion_correction.profile_runtime import load_profile_run_config
from half_linac.src.apps.dispersion_correction.workflow import create_machine
from half_linac.src.apps.dispersion_correction.reports import result_to_json, result_to_markdown, write_result_files
from half_linac.src.apps.dispersion_correction.workflow import AchromatWorkflow


def run_command(argv: list[str] | None = None) -> int:
    parser = _base_parser("Run one horizontal effective dispersion correction workflow.")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    parser.add_argument("--output-dir", help="Optional directory for JSON/CSV/Markdown report files.")
    args = parser.parse_args(argv)

    config = _load_runtime_config(args.config, write_operation=True)
    workflow = AchromatWorkflow(config, log_callback=_stderr_log if args.verbose else None)
    result = workflow.run()
    if args.output_dir:
        paths = write_result_files(result, args.output_dir)
        print("Wrote reports:")
        for kind, path in paths.items():
            print(f"  {kind}: {path}")
    else:
        print(result_to_json(result) if args.format == "json" else result_to_markdown(result))
    return 0 if result.success else 2


def measure_command(argv: list[str] | None = None) -> int:
    parser = _base_parser("Measure current horizontal effective dispersion without correction.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)

    config = _load_runtime_config(args.config, write_operation=True)
    measurement = AchromatWorkflow(config).measure_dispersion(config.measurement.final_samples)
    if args.json:
        import json

        print(
            json.dumps(
                {
                    "plane": measurement.plane,
                    "delta": measurement.delta,
                    "rms_mm": measurement.rms_mm,
                    "bpm_names": list(measurement.bpm_names),
                    "values_mm": measurement.values_mm.tolist(),
                    "valid": measurement.valid.tolist(),
                },
                indent=2,
            )
        )
    else:
        print(f"D_eff RMS: {measurement.rms_mm:.6g} mm")
        for name, value, valid in zip(measurement.bpm_names, measurement.values_mm, measurement.valid):
            status = "valid" if valid else "invalid"
            print(f"  {name}: {value:.6g} mm ({status})")
    return 0


def status_command(argv: list[str] | None = None) -> int:
    parser = _base_parser("Read configured machine PVs without changing any setpoint.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)

    config = _load_runtime_config(args.config)
    if config.backend.type.lower() == "epics":
        config = replace(config, backend=replace(config.backend, mode="read_only"))
    machine = create_machine(config)
    bpm = machine.read_bpm(config.target_bpms)
    if hasattr(machine, "read_quadrupole_readbacks"):
        quadrupoles = machine.read_quadrupole_readbacks()
    else:
        quadrupoles = machine.get_knobs(tuple(knob.name for knob in config.knobs))
    try:
        energy_readback = machine.get_energy_delta()
    except (RuntimeError, ValueError):
        energy_readback = None
    data = {
        "backend": machine.backend_name,
        "mode": machine.mode,
        "bpms": [
            {
                "name": name,
                "x": _finite_or_none(float(bpm.x_mm[index])),
                "y": _finite_or_none(float(bpm.y_mm[index])),
                "valid": bool(bpm.valid[index]),
            }
            for index, name in enumerate(bpm.names)
        ],
        "energy_knob": _finite_or_none(energy_readback),
        "quadrupoles": {name: _finite_or_none(value) for name, value in quadrupoles.items()},
        "safe_to_read": machine.is_safe(),
    }
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print(f"Backend: {data['backend']} ({data['mode']})")
        print(f"Energy knob readback: {data['energy_knob']}")
        print("BPMs:")
        for item in data["bpms"]:
            print(f"  {item['name']}: x={item['x']} y={item['y']} valid={item['valid']}")
        print("Quadrupoles:")
        for name, value in data["quadrupoles"].items():
            print(f"  {name}: {value}")
    return 0


def plan_command(argv: list[str] | None = None) -> int:
    parser = _base_parser("Print a no-IO dry-run operation plan for a configuration.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)

    config = _load_runtime_config(args.config)
    plan = build_operation_plan(config)
    print(json.dumps(plan, indent=2, sort_keys=True) if args.json else format_operation_plan(plan))
    return 0


def calibrate_phase_command(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fit RF phase to dp/p calibration from a local CSV file.")
    parser.add_argument("--csv", required=True, help="CSV path with phase and delta columns.")
    parser.add_argument("--phase-column", default="phase_deg", help="Column containing RF phase values.")
    parser.add_argument("--delta-column", default="delta_p_over_p", help="Column containing measured dp/p values.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)

    fit = load_phase_calibration_csv(args.csv, args.phase_column, args.delta_column)
    if args.json:
        print(json.dumps(fit.as_dict(), indent=2, sort_keys=True))
    else:
        print("Phase calibration fit")
        print(f"  slope_delta_per_phase: {fit.slope_delta_per_phase:.12g}")
        print(f"  phase_per_delta: {fit.phase_per_delta:.12g}")
        print(f"  intercept_delta: {fit.intercept_delta:.12g}")
        print(f"  r_squared: {fit.r_squared:.12g}")
        print(f"  n_samples: {fit.n_samples}")
        print("")
        print("JSON calibration fragment:")
        print(
            json.dumps(
                {
                    "calibration": {
                        "kind": "linear",
                        "phase_per_delta": fit.phase_per_delta,
                    }
                },
                indent=2,
            )
        )
    return 0


def preflight_command(argv: list[str] | None = None) -> int:
    parser = _base_parser("Run static preflight checks for offline, read-only, or write-enabled operation.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)

    config = _load_runtime_config(args.config)
    result = run_preflight(config)
    print(json.dumps(result.as_dict(), indent=2, sort_keys=True) if args.json else format_preflight(result))
    return 0 if result.ok else 2


def _finite_or_none(value):
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _base_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--config",
        help=(
            "Optional JSON/YAML configuration path. When omitted, use the active "
            "half_linac machine profile."
        ),
    )
    parser.add_argument("--verbose", action="store_true", help="Print workflow progress to stderr.")
    return parser


def _stderr_log(message: str) -> None:
    print(message, file=sys.stderr)


def _load_runtime_config(config_path: str | None, *, write_operation: bool = False):
    if config_path:
        config = load_config(config_path)
        if write_operation and config.backend.type.lower() == "epics":
            raise PermissionError(
                "EPICS write operations must use the active half_linac machine profile; "
                "external --config files are limited to offline or read-only commands"
            )
        return config
    _, config = load_profile_run_config()
    return config


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description="Dispersion correction command-line tools.")
    parser.add_argument(
        "command",
        choices=("run", "measure", "status", "plan", "preflight", "calibrate-phase"),
    )
    namespace, remaining = parser.parse_known_args(arguments)
    commands = {
        "run": run_command,
        "measure": measure_command,
        "status": status_command,
        "plan": plan_command,
        "preflight": preflight_command,
        "calibrate-phase": calibrate_phase_command,
    }
    return commands[namespace.command](remaining)


if __name__ == "__main__":
    raise SystemExit(main())
