from __future__ import annotations

import copy
import math
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from half_linac.src.shared.elegant_backend import EleParser, ElegantParser
from half_linac.src.shared.runtime_state import ensure_runtime_state, read_runtime_state, update_runtime_state


BEAM_SOURCE_CONFIG_KEY = "beam_source_config"
BUNCHED_BEAM = "bunched_beam"
SDDS_BEAM = "sdds_beam"
BEAM_MODES = (BUNCHED_BEAM, SDDS_BEAM)

BUNCHED_FIELDS = (
    "n_particles_per_bunch",
    "emit_nx",
    "emit_ny",
    "sigma_s",
    "sigma_dp",
    "distribution_type[0]",
    "distribution_type[1]",
    "distribution_type[2]",
    "distribution_cutoff[0]",
    "distribution_cutoff[1]",
    "distribution_cutoff[2]",
)
TWISS_FIELDS = ("beta_x", "beta_y", "alpha_x", "alpha_y")
SDDS_FIELDS = (
    "input",
    "input_type",
    "sample_interval",
    "center_arrival_time",
    "reuse_bunch",
    "p_lower",
    "p_upper",
)


class BeamSourceError(ValueError):
    pass


def reference_momentum_key(control: Mapping[str, Any]) -> str:
    run_setup = control.get("run_setup", {})
    if "p_central_mev" in run_setup:
        return "p_central_mev"
    return "p_central"


def quote_elegant_string(value: str) -> str:
    clean = unquote_elegant_string(value).strip()
    return f'"{clean}"'


def unquote_elegant_string(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        return text[1:-1]
    return text


def bootstrap_runtime_state(runtime) -> dict[str, Any]:
    return ElegantParser(
        runtime.vm.bootstrap_lattice,
        runtime.vm.bootstrap_ele,
        runtime.vm.line_name,
        runtime_json_path=runtime.vm.runtime_json,
        elegant_dir=runtime.vm.bootstrap_lattice.parent,
    ).build_runtime_state()


def bootstrap_control(runtime) -> dict[str, dict[str, str]]:
    return copy.deepcopy(EleParser(runtime.vm.bootstrap_ele).control)


def load_beam_source_config(runtime) -> dict[str, Any]:
    default_control = bootstrap_control(runtime)
    try:
        state = read_runtime_state(runtime.vm.runtime_json)
    except FileNotFoundError:
        state = {"control": default_control}

    metadata = state.get(BEAM_SOURCE_CONFIG_KEY)
    if isinstance(metadata, Mapping):
        config = copy.deepcopy(dict(metadata))
    else:
        usedline_context = state.get("usedline_context", {})
        usedline_mode = (
            str(usedline_context.get("mode", "")).lower()
            if isinstance(usedline_context, Mapping)
            else ""
        )
        effective_control = (
            default_control
            if usedline_mode in {"segment", "prewatch"}
            else state.get("control", default_control)
        )
        config = _config_from_control(effective_control)

    defaults = _config_from_control(default_control)
    for mode in BEAM_MODES:
        if not isinstance(config.get(mode), Mapping):
            config[mode] = copy.deepcopy(defaults.get(mode, {}))
    if not isinstance(config.get("twiss_output"), Mapping):
        config["twiss_output"] = copy.deepcopy(defaults["twiss_output"])
    if not isinstance(config.get("run_setup"), Mapping):
        config["run_setup"] = copy.deepcopy(defaults["run_setup"])
    if config.get("selected") not in BEAM_MODES:
        config["selected"] = defaults["selected"]

    sdds = config[SDDS_BEAM]
    sdds.setdefault("input_type", '"elegant"')
    sdds.setdefault("sample_interval", "1")
    sdds.setdefault("center_arrival_time", "1")
    sdds.setdefault("reuse_bunch", "1")
    return config


def normalize_beam_source_config(
    raw_config: Mapping[str, Any],
    *,
    elegant_dir: str | Path,
    reference_key: str,
) -> dict[str, Any]:
    selected = str(raw_config.get("selected", ""))
    if selected not in BEAM_MODES:
        raise BeamSourceError("Select either bunched_beam or sdds_beam.")
    if reference_key not in {"p_central", "p_central_mev"}:
        raise BeamSourceError(f"Unsupported reference momentum parameter: {reference_key}.")

    bunched = _mapping_copy(raw_config.get(BUNCHED_BEAM))
    sdds = _mapping_copy(raw_config.get(SDDS_BEAM))
    twiss = _mapping_copy(raw_config.get("twiss_output"))
    run_setup = _mapping_copy(raw_config.get("run_setup"))

    reference_value = _positive_float(run_setup.get(reference_key), reference_key)
    normalized_run_setup = {reference_key: reference_value}

    normalized_bunched = copy.deepcopy(bunched)
    normalized_sdds = copy.deepcopy(sdds)
    normalized_twiss = copy.deepcopy(twiss)

    if selected == BUNCHED_BEAM:
        normalized_bunched["n_particles_per_bunch"] = _positive_int(
            bunched.get("n_particles_per_bunch"), "n_particles_per_bunch"
        )
        for key in ("emit_nx", "emit_ny", "sigma_s", "sigma_dp"):
            normalized_bunched[key] = _nonnegative_float(bunched.get(key), key)
        for index in range(3):
            type_key = f"distribution_type[{index}]"
            cutoff_key = f"distribution_cutoff[{index}]"
            distribution_type = unquote_elegant_string(bunched.get(type_key)).strip()
            if not distribution_type:
                raise BeamSourceError(f"{type_key} is required.")
            normalized_bunched[type_key] = quote_elegant_string(distribution_type)
            normalized_bunched[cutoff_key] = _positive_float(bunched.get(cutoff_key), cutoff_key)
        normalized_bunched.setdefault("use_twiss_command_values", "1")
        for key in ("beta_x", "beta_y"):
            normalized_twiss[key] = _positive_float(twiss.get(key), key)
        for key in ("alpha_x", "alpha_y"):
            normalized_twiss[key] = _finite_float(twiss.get(key), key)
        seed = str(run_setup.get("random_number_seed", "")).strip()
        normalized_run_setup["random_number_seed"] = _positive_int(seed, "random_number_seed")
    else:
        input_value = unquote_elegant_string(sdds.get("input")).strip()
        if not input_value:
            raise BeamSourceError("SDDS input file is required.")
        resolved_input = Path(input_value).expanduser()
        if not resolved_input.is_absolute():
            resolved_input = Path(elegant_dir) / resolved_input
        resolved_input = resolved_input.resolve()
        if not resolved_input.is_file() or not os.access(resolved_input, os.R_OK):
            raise BeamSourceError(f"SDDS input file is not readable: {resolved_input}")
        try:
            stored_input = resolved_input.relative_to(Path(elegant_dir).resolve()).as_posix()
        except ValueError:
            stored_input = str(resolved_input)
        normalized_sdds["input"] = quote_elegant_string(stored_input)
        normalized_sdds["input_type"] = '"elegant"'
        normalized_sdds["sample_interval"] = _positive_int(
            sdds.get("sample_interval"), "sample_interval"
        )
        for key in ("center_arrival_time", "reuse_bunch"):
            normalized_sdds[key] = "1" if _as_bool(sdds.get(key)) else "0"
        for key in ("p_lower", "p_upper"):
            value = str(sdds.get(key, "")).strip()
            if value:
                normalized_sdds[key] = _finite_float(value, key)
            else:
                normalized_sdds.pop(key, None)
        if "p_lower" in normalized_sdds and "p_upper" in normalized_sdds:
            if float(normalized_sdds["p_lower"]) >= float(normalized_sdds["p_upper"]):
                raise BeamSourceError("p_lower must be less than p_upper.")

    return {
        "selected": selected,
        BUNCHED_BEAM: normalized_bunched,
        SDDS_BEAM: normalized_sdds,
        "twiss_output": normalized_twiss,
        "run_setup": normalized_run_setup,
    }


def apply_beam_source_config(runtime, raw_config: Mapping[str, Any]) -> dict[str, Any]:
    default_control = bootstrap_control(runtime)
    ensure_runtime_state(runtime.vm.runtime_json, lambda: bootstrap_runtime_state(runtime))
    try:
        state = read_runtime_state(runtime.vm.runtime_json)
        if not isinstance(state["control"], dict):
            raise TypeError("control is not a mapping")
    except (KeyError, TypeError) as exc:
        raise BeamSourceError("VM runtime state does not contain a valid control section.") from exc
    reference_key = reference_momentum_key(default_control)
    config = normalize_beam_source_config(
        raw_config,
        elegant_dir=runtime.vm.bootstrap_lattice.parent,
        reference_key=reference_key,
    )

    def apply(runtime_state: dict[str, Any]) -> bool:
        runtime_state[BEAM_SOURCE_CONFIG_KEY] = copy.deepcopy(config)
        apply_preferred_beam_source(runtime_state["control"], config)
        return True

    update_runtime_state(runtime.vm.runtime_json, apply)
    return config


def apply_preferred_beam_source(
    control: dict[str, Any],
    config: Mapping[str, Any] | None,
) -> None:
    if not isinstance(config, Mapping):
        return
    selected = config.get("selected")
    if selected not in BEAM_MODES:
        return

    control.pop(BUNCHED_BEAM, None)
    control.pop(SDDS_BEAM, None)
    section = config.get(selected)
    if isinstance(section, Mapping):
        control[selected] = copy.deepcopy(dict(section))

    run_setup = control.setdefault("run_setup", {})
    configured_run_setup = config.get("run_setup", {})
    if isinstance(configured_run_setup, Mapping):
        for key in ("p_central", "p_central_mev"):
            if key in configured_run_setup:
                run_setup.pop("p_central", None)
                run_setup.pop("p_central_mev", None)
                run_setup[key] = str(configured_run_setup[key])
        if selected == BUNCHED_BEAM and "random_number_seed" in configured_run_setup:
            run_setup["random_number_seed"] = str(configured_run_setup["random_number_seed"])

    if selected == BUNCHED_BEAM:
        configured_twiss = config.get("twiss_output", {})
        if isinstance(configured_twiss, Mapping):
            twiss = control.setdefault("twiss_output", {})
            for key in TWISS_FIELDS:
                if key in configured_twiss:
                    twiss[key] = str(configured_twiss[key])


def _config_from_control(control: Mapping[str, Any]) -> dict[str, Any]:
    selected = BUNCHED_BEAM if BUNCHED_BEAM in control else SDDS_BEAM
    run_setup = control.get("run_setup", {})
    reference_key = reference_momentum_key(control)
    return {
        "selected": selected,
        BUNCHED_BEAM: copy.deepcopy(dict(control.get(BUNCHED_BEAM, {}))),
        SDDS_BEAM: copy.deepcopy(dict(control.get(SDDS_BEAM, {}))),
        "twiss_output": {
            key: str(control.get("twiss_output", {}).get(key, "")) for key in TWISS_FIELDS
        },
        "run_setup": {
            reference_key: str(run_setup.get(reference_key, "")),
            "random_number_seed": str(run_setup.get("random_number_seed", "")),
        },
    }


def _mapping_copy(value: Any) -> dict[str, Any]:
    return copy.deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _finite_float(value: Any, name: str) -> str:
    text = str(value or "").strip()
    try:
        number = float(text)
    except ValueError as exc:
        raise BeamSourceError(f"{name} must be a number.") from exc
    if not math.isfinite(number):
        raise BeamSourceError(f"{name} must be finite.")
    return text


def _positive_float(value: Any, name: str) -> str:
    text = _finite_float(value, name)
    if float(text) <= 0:
        raise BeamSourceError(f"{name} must be greater than zero.")
    return text


def _nonnegative_float(value: Any, name: str) -> str:
    text = _finite_float(value, name)
    if float(text) < 0:
        raise BeamSourceError(f"{name} must not be negative.")
    return text


def _positive_int(value: Any, name: str) -> str:
    text = str(value or "").strip()
    try:
        number = int(text)
    except ValueError as exc:
        raise BeamSourceError(f"{name} must be an integer.") from exc
    if number <= 0:
        raise BeamSourceError(f"{name} must be greater than zero.")
    return str(number)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


__all__ = [
    "BEAM_SOURCE_CONFIG_KEY",
    "BUNCHED_BEAM",
    "SDDS_BEAM",
    "BeamSourceError",
    "apply_beam_source_config",
    "apply_preferred_beam_source",
    "bootstrap_control",
    "bootstrap_runtime_state",
    "load_beam_source_config",
    "normalize_beam_source_config",
    "quote_elegant_string",
    "reference_momentum_key",
    "unquote_elegant_string",
]
