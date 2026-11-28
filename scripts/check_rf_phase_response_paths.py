#!/usr/bin/env python3
"""Check HALF RF response candidates on the nominal lattice, using offline Elegant maps."""

import argparse
from copy import deepcopy
from itertools import groupby
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory

from half_linac.src.shared.elegant_backend import ElegantParser
from half_linac.src.shared.elegant_runtime import run_elegant_input
from half_linac.src.shared.machine_profile import get_workflow, resolve_channel, resolve_write_target
from half_linac.src.shared.machine_profile.loader import load_model_context
from half_linac.src.shared.machine_profile.model_backend import _load_sdds_columns


def rf_positions(state, stages):
    """HALF has two RFCW structures per station, grouped by transmission BPM."""
    line, lattice = state["usedline"], state["lattice"]
    start = 0
    positions = {}
    for bpm, group in groupby(stages, key=lambda stage: stage["target_bpm"]):
        stations = [stage["rf_element"] for stage in group]
        end = line.index(bpm, start)
        structures = [i for i in range(start, end) if lattice[line[i]]["TYPE"] == "RFCW"]
        if len(structures) != 2 * len(stations):
            raise ValueError(f"{bpm}: expected two lattice RF structures per station.")
        for i, rf in enumerate(stations):
            positions[rf] = structures[2 * i:2 * i + 2]
        start = end + 1
    return positions


def run_map(parser, state, directory):
    (directory / "model.json").write_text(json.dumps(state), encoding="utf-8")
    parser.json_to_lte_ele(
        directory / "model.lte", directory / "model.ele", directory / "model.json", include_track=False,
    )
    try:
        run_elegant_input("model.ele", "model.log", workdir=directory)
    except RuntimeError as exc:
        details = (directory / "model.log").read_text(encoding="utf-8", errors="replace")
        raise RuntimeError(details[-2000:] or str(exc)) from exc


def momentum_profile(parser, state, directory):
    control = {key: value for key, value in state["control"].items() if key != "matrix_output"}
    run_map(parser, {**state, "control": control}, directory)
    columns = _load_sdds_columns(directory / "model.twi", ("ElementName", "pCentral0"))
    return dict(zip(columns["ElementName"], map(float, columns["pCentral0"]), strict=True))


def vary_station(state, positions, change_mv):
    varied = deepcopy(state)
    for position in positions:
        name = f"RF_TEST_{position}"
        element = dict(varied["lattice"][varied["usedline"][position]])
        element.update(NAME=name, VOLT=float(element.get("VOLT", 0)) + change_mv * 1e6 / len(positions))
        varied["lattice"][name] = element
        varied["usedline"][position] = name
    return varied


def response_at_bpm(parser, state, directory, start, end, quads, nominal, momentum):
    """Return R12 times the kick scaling for a fixed corrector field."""
    segment = deepcopy(state)
    corrector = segment["usedline"][start]
    segment["usedline"] = segment["usedline"][start + 1:end + 1]
    segment["control"].pop("twiss_output")
    segment["control"]["run_setup"]["p_central"] = momentum[corrector]
    # K1 is normalized to momentum. Hold the corresponding magnet gradients fixed.
    for quad in quads:
        element = segment["lattice"][quad]
        element["K1"] = float(element["K1"]) * nominal[quad] / momentum[quad]
    run_map(parser, segment, directory)
    columns = _load_sdds_columns(directory / "model.mat", ("R12",))
    return float(columns["R12"][0]) * nominal[corrector] / momentum[corrector]


def check_channels(profile, rf, target, path, other_rf):
    corrector, bpm = path["corrector"], path["bpm"]
    readbacks = {
        rf: ("phase_readback", "amplitude_readback", "kly_forward_power"),
        corrector: ("current_readback",),
        bpm: ("x", "sum"),
        **{quad: ("K1_total", "current_readback") for quad in path["quadrupoles"]},
        **{station: ("phase_readback", "amplitude_readback", "kly_forward_power") for station in other_rf},
    }
    resolve_channel(profile, target, "sum", mode="real")
    for element, channels in readbacks.items():
        for channel in channels:
            resolve_channel(profile, element, channel, mode="real")
    for element, channel in ((rf, "phase_set"), (rf, "amplitude_set"), (corrector, "current_set")):
        resolve_write_target(profile, element, logical_channel=channel, mode="real")


def check_paths(profile, parser, directory, step_mv):
    recovery = get_workflow(profile, "rf_phase_scan")["transmission_recovery"]
    stages = recovery["stages"]
    state = parser.build_runtime_state()
    line, lattice = state["usedline"], state["lattice"]
    positions = rf_positions(state, stages)
    paths_by_bpm = recovery["phase_response_candidates"]
    last_bpm = max(line.index(path["bpm"]) for paths in paths_by_bpm.values() for path in paths)
    state["usedline"] = line[:last_bpm + 1]
    for key in ("error_control", "error_element", "bunched_beam"):
        state["control"].pop(key, None)
    for element in lattice.values():
        for key in ("ZWAKEFILE", "TRWAKEFILE"):
            element.pop(key, None)
    nominal = momentum_profile(parser, state, directory)
    nominal_responses = {}

    print("Station | Transmission BPM | Response path | Focusing quads | Other RF | Nominal voltage (MV) | "
          "Response change | Model result")
    print("--- | --- | --- | --- | --- | ---: | ---: | ---")
    for stage in stages:
        rf, target = stage["rf_element"], stage["target_bpm"]
        paths = paths_by_bpm.get(target, ())
        if not paths:
            continue
        station_positions = positions[rf]
        voltage_mv = sum(float(lattice[line[i]].get("VOLT", 0)) for i in station_positions) / 1e6
        low, high = (vary_station(state, station_positions, sign * step_mv) for sign in (-1, 1))
        low_p, high_p = (momentum_profile(parser, variant, directory) for variant in (low, high))
        for path in paths:
            corrector, bpm = path["corrector"], path["bpm"]
            start, end = line.index(corrector), line.index(bpm)
            quads = [name for name in line[start + 1:end] if lattice[name]["TYPE"] == "QUAD"]
            intervening_rf = [name for name, indices in positions.items() if any(start < i < end for i in indices)]
            if not line.index(target) < start < end or end <= station_positions[-1]:
                raise ValueError(f"{rf}: unexpected beamline order for {target}, {corrector}, {bpm}.")
            if quads != path["quadrupoles"] or intervening_rf != path["intervening_rf"]:
                raise ValueError(f"{rf}: {corrector} → {bpm} differs from the configured beamline path.")
            other_rf = [
                name for name, indices in positions.items() if name != rf
                and any(min(start, station_positions[-1]) < i < end for i in indices)
            ]
            check_channels(profile, rf, target, path, other_rf)
            key = (corrector, bpm)
            if key not in nominal_responses:
                nominal_responses[key] = response_at_bpm(parser, state, directory, start, end, quads, nominal, nominal)
            middle = nominal_responses[key]
            lower, higher = (
                response_at_bpm(parser, model, directory, start, end, quads, nominal, momentum)
                for model, momentum in ((low, low_p), (high, high_p))
            )
            expected = lower * middle > 0 and middle * higher > 0 and abs(lower) > abs(middle) > abs(higher)
            change = (abs(higher) - abs(lower)) / abs(middle) if middle else math.nan
            focusing = [quad for quad in quads if float(lattice[quad]["K1"]) > 0]
            model_result = "decreases" if expected else "review response direction"
            print(f"{rf} | {target} | {corrector} → {bpm} | {', '.join(focusing) or '—'} | "
                  f"{', '.join(other_rf) or '—'} | {voltage_mv:g} | {change:+.2%} | {model_result}", flush=True)


def main():
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument("--voltage-step-mv", type=float, default=10.0,
                           help="Added/subtracted total station voltage in the model (default: 10 MV).")
    step = arguments.parse_args().voltage_step_mv
    if not math.isfinite(step) or step <= 0:
        arguments.error("--voltage-step-mv must be finite and positive")

    context = load_model_context("half", model_backend="simulation")
    model = context.model_backend.config
    parser = ElegantParser(model["source_lattice"], model["optics_ini_ele"], model["line_name"])
    print(f"Nominal {model['line_name']} lattice, station voltage varied by ±{step:g} MV.")
    with TemporaryDirectory(prefix="half_rf_response_") as directory:
        check_paths(context.profile, parser, Path(directory), step)


if __name__ == "__main__":
    main()
