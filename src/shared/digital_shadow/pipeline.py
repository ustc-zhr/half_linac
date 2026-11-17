"""Frozen per-run Elegant optics for a restricted transverse model segment."""
from copy import deepcopy
from dataclasses import replace
import math
from pathlib import Path

import numpy as np

from half_linac.src.shared.beam_matrix import transport_beam_moments
from half_linac.src.shared.elegant_backend import ElegantParser
from half_linac.src.shared.machine_profile.model_backend import build_model_backend
from half_linac.src.shared.runtime_state import write_runtime_state


SUPPORTED_TYPES = frozenset({"DRIF", "QUAD", "MONI", "WATCH", "MARK", "HKICK", "VKICK"})


def prepare_model(context, request):
    if context.model_backend is None or context.model_backend.engine != "elegant":
        raise ValueError("The first shadow pipeline requires an Elegant model backend")
    config = context.model_backend.config
    state = ElegantParser(config["source_lattice"], config["optics_ini_ele"], request.model_line).build_runtime_state()
    names, lattice = state["usedline"], state["lattice"]
    reference = request.reference_element
    if names.count(reference) != 1:
        raise ValueError("The reference must identify one unique model occurrence")
    quads = set()
    for diagnostic in request.diagnostics:
        target = diagnostic.element
        if names.count(target) != 1:
            raise ValueError(f"Diagnostic {target} must identify one unique model occurrence")
        if context.profile.get_element(target).kind != "flag" or lattice[target]["TYPE"].upper() != "WATCH":
            raise ValueError(f"{target} is not a modeled screen")
        if names.index(target) < names.index(reference):
            raise ValueError(f"{target} is upstream of the reference")
        start = names.index(reference) + (request.reference_edge == "exit")
        for name in names[start:names.index(target) + 1]:
            element = lattice[name]
            kind = element["TYPE"].upper()
            if kind not in SUPPORTED_TYPES:
                raise ValueError(f"Unsupported shadow element {name} ({kind}); select a non-RF, non-bending segment")
            if kind == "QUAD":
                if names.count(name) != 1:
                    raise ValueError(f"Repeated quadrupole {name} cannot be mapped unambiguously")
                if context.profile.get_element(name).kind != "quad":
                    raise ValueError(f"{name} is not a profile quadrupole")
                quads.add(name)
    return state, tuple((name, "K1") for name in sorted(quads))


def predict(context, request, state, model_snapshot, run_dir):
    """Serialize evaluated input once; use fresh workspaces for each target."""
    inputs = Path(run_dir) / "inputs"
    inputs.mkdir()
    frozen = deepcopy(state)
    control = frozen["control"]
    control["run_setup"].pop("p_central_mev", None)
    control["run_setup"]["p_central"] = str(math.sqrt(request.energy_mev * (request.energy_mev + 2 * 0.51099895)) / 0.51099895)
    for key in ("bunched_beam", "sdds_beam", "track"):
        control.pop(key, None)
    state_path = inputs / "evaluated_state.json"
    write_runtime_state(state_path, frozen)
    config = context.model_backend.config
    parser = ElegantParser(config["source_lattice"], config["optics_ini_ele"], request.model_line)
    source, template = inputs / "design.lte", inputs / "template.ele"
    parser.json_to_lte_ele(source, template, state_path, include_track=False)
    predictions, matrices = {}, {}
    for index, target in enumerate(dict.fromkeys(item.element for item in request.diagnostics)):
        workdir = Path(run_dir) / "model" / str(index)
        model_config = replace(context.model_backend, config={
            "source_lattice": str(source), "optics_ini_ele": str(template),
            "optics_working_dir": str(workdir), "line_name": request.model_line,
            **{f"optics_{suffix}": str(workdir / f"optics.{suffix}") for suffix in ("json", "lte", "ele", "mat")},
            "optics_log": "optics.log", "timeout_s": request.model_timeout_s,
        })
        backend = build_model_backend(replace(context, model_backend=model_config), energy_mev=request.energy_mev, line_name=request.model_line)
        if target == request.reference_element and request.reference_edge == "exit":
            matrix = np.eye(6)
        else:
            matrix = np.asarray(backend.get_map(
                request.reference_element, target, lattice_overrides=model_snapshot.lattice_overrides,
                seq="ent2exit" if request.reference_edge == "entrance" else "exit2exit", twiss_only=True,
            ), dtype=float)
        if matrix.shape != (6, 6) or not np.all(np.isfinite(matrix)):
            raise ValueError(f"Invalid transfer matrix for {target}")
        if np.any(np.abs(matrix[np.ix_([0, 1], [2, 3, 4, 5])]) > 1e-10) or np.any(np.abs(matrix[np.ix_([2, 3], [0, 1, 4, 5])]) > 1e-10):
            raise ValueError("Coupled or dispersive transport requires a six-dimensional beam input")
        matrices[target] = matrix.tolist()
        for channel, indices, moments in (("sigx", [0, 1], request.x_moments), ("sigy", [2, 3], request.y_moments)):
            variance = transport_beam_moments(matrix[np.ix_(indices, indices)], moments)[0]
            if not math.isfinite(variance) or variance <= 0:
                raise ValueError(f"Invalid predicted beam variance for {target}/{channel}")
            predictions[f"{target}/{channel}"] = math.sqrt(variance)
    return predictions, matrices
