from __future__ import annotations

import copy
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import half_linac.runtime_config as st

from half_linac.src.shared.elegant_backend import ElegantParser
from half_linac.src.shared.machine_profile import (
    MachineProfileError,
    get_workflow,
    resolve_machine_runtime,
    resolve_virtual_machine_usedline_workflow,
)
from half_linac.src.shared.runtime_state import read_runtime_state, update_runtime_state


PREWATCH_ID = "PREW"
PREWATCH_FILENAME = "pre.bun"


class LatticeUsedlineError(RuntimeError):
    pass


def expand_lattice_line(
    lattice: Mapping[str, Mapping[str, Any]],
    line_name: str,
) -> list[str]:
    line_name = _require_line_name(line_name)
    _require_line_element(lattice, line_name)
    return _expand_line_tokens(lattice, _split_line(lattice[line_name].get("LINE", "")), stack=(line_name,))


def select_esa_line_name(
    lattice: Mapping[str, Mapping[str, Any]],
    *,
    configured_line_name: str | None = None,
) -> str:
    candidates = [configured_line_name, "ALL_ESA", "ESA"]
    for candidate in candidates:
        if not candidate:
            continue
        line_name = str(candidate).strip()
        if line_name in lattice and str(lattice[line_name].get("TYPE", "")).upper() == "LINE":
            return line_name
    raise LatticeUsedlineError("No ESA usedline was found. Define ALL_ESA or ESA in the VM lattice.")


def restore_main_usedline() -> list[str]:
    runtime = resolve_machine_runtime()
    line_name = _default_predefined_usedline(runtime)
    return _set_usedline_to_lattice_line(
        line_name,
        success_label="main",
    )


def apply_predefined_usedline(line_name: str) -> list[str]:
    line_name = _require_line_name(line_name)
    return _set_usedline_to_lattice_line(
        line_name,
        success_label="selected",
    )


def switch_to_esa_usedline(line_name: str | None = None) -> list[str]:
    if line_name is not None:
        return apply_predefined_usedline(line_name)

    runtime = resolve_machine_runtime()
    state = read_runtime_state(runtime.vm.runtime_json)
    configured_line = _configured_energy_usedline(runtime)
    line_name = select_esa_line_name(state["lattice"], configured_line_name=configured_line)
    return _set_usedline_to_lattice_line(
        line_name,
        success_label="ESA",
    )


def simplify_usedline_segment(
    parent_usedline: str,
    start_element: str,
    end_element: str,
    *,
    wait_s: float | None = None,
) -> list[str]:
    parent_usedline = _require_line_name(parent_usedline)
    start_element = _require_line_name(start_element)
    end_element = _require_line_name(end_element)
    runtime = resolve_machine_runtime()
    wait_s = st.runtime_vmmachine if wait_s is None else float(wait_s)

    state = read_runtime_state(runtime.vm.runtime_json)
    baseline_control = _build_baseline_control()
    parent_elements = expand_lattice_line(state["lattice"], parent_usedline)
    start_index = _require_element_in_usedline(
        start_element,
        parent_elements,
        "start_element",
        parent_usedline,
    )
    end_index = _require_element_in_usedline(
        end_element,
        parent_elements,
        "end_element",
        parent_usedline,
    )
    if end_index < start_index:
        raise LatticeUsedlineError(
            f"Cannot simplify usedline from {start_element} to {end_element}: "
            f"end_element is upstream of start_element in parent usedline {parent_usedline!r}."
        )

    preline = [*parent_elements[:start_index], PREWATCH_ID]
    segment_usedline = parent_elements[start_index : end_index + 1]

    def prepare_pre_bunch(runtime_state: dict[str, Any]) -> bool:
        runtime_state["lattice"][PREWATCH_ID] = {
            "NAME": PREWATCH_ID,
            "TYPE": "WATCH",
            "FILENAME": PREWATCH_FILENAME,
            "MODE": "COORD",
            "DISABLE": "0",
        }
        runtime_state["usedline"] = preline
        _restore_baseline_control(runtime_state, baseline_control)
        return True

    update_runtime_state(runtime.vm.runtime_json, prepare_pre_bunch)
    time.sleep(wait_s)
    p_central = _read_last_pcentral(runtime.vm.bootstrap_lattice.parent / "one.cen")

    def apply_simplified_segment(runtime_state: dict[str, Any]) -> bool:
        _restore_baseline_control(runtime_state, baseline_control)
        control = runtime_state["control"]
        control.pop("bunched_beam", None)
        sdds_beam = copy.deepcopy(control.get("sdds_beam", {}))
        sdds_beam.update(
            {
                "input": PREWATCH_FILENAME,
                "center_arrival_time": "1",
                "reuse_bunch": "1",
            }
        )
        control["sdds_beam"] = sdds_beam

        run_setup = control.setdefault("run_setup", {})
        run_setup.pop("p_central_mev", None)
        run_setup["p_central"] = str(p_central)
        run_setup["use_beamline"] = runtime.vm.line_name
        runtime_state["usedline"] = segment_usedline
        return True

    update_runtime_state(runtime.vm.runtime_json, apply_simplified_segment)
    print(
        f"simplified VM usedline {parent_usedline}: {start_element} -> {end_element} "
        f"with {len(segment_usedline)} element(s)."
    )
    return segment_usedline


def restore_main_usedline_cli(argv: Sequence[str] | None = None) -> int:
    del argv
    return _run_cli("restore main usedline", restore_main_usedline)


def switch_to_esa_usedline_cli(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) > 1:
        print("usage: transfer_ESAline.py [LINE_ID]", file=sys.stderr)
        return 2
    line_name = args[0] if args else None
    if line_name:
        return _run_cli("switch selected usedline", lambda: apply_predefined_usedline(line_name))
    return _run_cli("switch to ESA usedline", switch_to_esa_usedline)


def simplify_usedline_segment_cli(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 3:
        parent_usedline, start_element, end_element = args
    elif len(args) == 2:
        parent_usedline = _default_segment_parent_usedline()
        start_element, end_element = args
    else:
        print("usage: simply_VM.py [PARENT_USEDLINE] START_ELEMENT END_ELEMENT", file=sys.stderr)
        return 2
    return _run_cli(
        "simplify usedline segment",
        lambda: simplify_usedline_segment(parent_usedline, start_element, end_element),
    )


def _set_usedline_to_lattice_line(line_name: str, *, success_label: str) -> list[str]:
    runtime = resolve_machine_runtime()
    baseline_control = _build_baseline_control()
    result: list[str] = []

    def switch_line(runtime_state: dict[str, Any]) -> bool:
        nonlocal result
        runtime_state["lattice"].pop(PREWATCH_ID, None)
        result = expand_lattice_line(runtime_state["lattice"], line_name)
        runtime_state["usedline"] = result
        _restore_baseline_control(runtime_state, baseline_control)
        runtime_state["control"].setdefault("run_setup", {})["use_beamline"] = runtime.vm.line_name
        return True

    update_runtime_state(runtime.vm.runtime_json, switch_line)
    print(f"{success_label} VM usedline is ready: {line_name} ({len(result)} element(s)).")
    return result


def _build_baseline_control() -> dict[str, dict[str, str]]:
    runtime = resolve_machine_runtime()
    parser = ElegantParser(
        runtime.vm.bootstrap_lattice,
        runtime.vm.bootstrap_ele,
        runtime.vm.line_name,
        runtime_json_path=runtime.vm.runtime_json,
        elegant_dir=runtime.vm.bootstrap_lattice.parent,
    )
    return parser.build_runtime_state()["control"]


def _restore_baseline_control(
    runtime_state: dict[str, Any],
    baseline_control: Mapping[str, Mapping[str, str]],
) -> None:
    current_control = runtime_state.get("control", {})
    preserved_error_sections = {
        section: copy.deepcopy(current_control[section])
        for section in ("error_control", "error_element")
        if section in current_control
    }
    runtime_state["control"] = copy.deepcopy(baseline_control)
    runtime_state["control"].update(preserved_error_sections)


def _read_last_pcentral(centroid_path: Path) -> float:
    import sdds

    if not centroid_path.is_file():
        raise LatticeUsedlineError(
            f"Cannot simplify VM usedline because centroid file is missing: {centroid_path}"
        )

    dataset = sdds.SDDS(0)
    dataset.load(str(centroid_path))
    try:
        column_index = dataset.columnName.index("pCentral")
    except ValueError as exc:
        raise LatticeUsedlineError(f"{centroid_path} does not contain pCentral.") from exc
    values = dataset.columnData[column_index][0]
    if not values:
        raise LatticeUsedlineError(f"{centroid_path} pCentral column is empty.")
    return float(values[-1])


def _default_predefined_usedline(runtime) -> str:
    try:
        workflow = resolve_virtual_machine_usedline_workflow(runtime.profile)
    except MachineProfileError:
        return runtime.vm.line_name
    return workflow.default_usedline or runtime.vm.line_name


def _configured_energy_usedline(runtime) -> str | None:
    try:
        workflow = resolve_virtual_machine_usedline_workflow(runtime.profile)
        for choice in workflow.predefined_usedlines:
            role = choice.role.lower()
            if role in {"energy_spectrum", "esa"}:
                return choice.id
        for choice in workflow.predefined_usedlines:
            if "ESA" in choice.id.upper() or "ESA" in choice.label.upper():
                return choice.id
    except MachineProfileError:
        pass

    legacy_workflow = (
        get_workflow(runtime.profile, "virtual_machine")
        if "virtual_machine" in runtime.profile.workflows
        else {}
    )
    if isinstance(legacy_workflow, Mapping):
        return legacy_workflow.get("esa_line_id")
    return None


def _default_segment_parent_usedline() -> str:
    runtime = resolve_machine_runtime()
    try:
        workflow = resolve_virtual_machine_usedline_workflow(runtime.profile)
        for segment in workflow.local_segments:
            if segment.id == workflow.default_segment_id:
                return segment.parent_usedline
        if workflow.local_segments:
            return workflow.local_segments[0].parent_usedline
    except MachineProfileError:
        pass
    return runtime.vm.line_name


def _require_element_in_usedline(
    element_id: str,
    usedline: Sequence[str],
    location: str,
    parent_usedline: str,
) -> int:
    try:
        return list(usedline).index(element_id)
    except ValueError as exc:
        raise LatticeUsedlineError(
            f"{location} {element_id!r} is not present in VM usedline {parent_usedline!r}."
        ) from exc


def _require_line_element(
    lattice: Mapping[str, Mapping[str, Any]],
    line_name: str,
) -> None:
    if line_name not in lattice:
        raise LatticeUsedlineError(f"VM lattice does not define line {line_name!r}.")
    if str(lattice[line_name].get("TYPE", "")).upper() != "LINE":
        raise LatticeUsedlineError(f"VM lattice entry {line_name!r} is not a LINE.")


def _expand_line_tokens(
    lattice: Mapping[str, Mapping[str, Any]],
    tokens: Sequence[str],
    *,
    stack: tuple[str, ...],
) -> list[str]:
    result: list[str] = []
    for token in tokens:
        if token not in lattice:
            raise LatticeUsedlineError(f"VM lattice line references unknown element {token!r}.")
        element = lattice[token]
        if str(element.get("TYPE", "")).upper() == "LINE":
            if token in stack:
                cycle = " -> ".join((*stack, token))
                raise LatticeUsedlineError(f"VM lattice line recursion detected: {cycle}.")
            result.extend(
                _expand_line_tokens(
                    lattice,
                    _split_line(element.get("LINE", "")),
                    stack=(*stack, token),
                )
            )
            continue
        result.append(token)
    return result


def _split_line(line: Any) -> list[str]:
    return [token.strip() for token in str(line).split(",") if token.strip()]


def _require_line_name(value: str) -> str:
    text = str(value).strip()
    if not text:
        raise LatticeUsedlineError("VM usedline element name must not be empty.")
    return text


def _run_cli(label: str, action) -> int:
    try:
        action()
        return 0
    except (LatticeUsedlineError, MachineProfileError, OSError, RuntimeError) as exc:
        print(f"{label} failed: {exc}", file=sys.stderr)
        return 1


__all__ = [
    "LatticeUsedlineError",
    "apply_predefined_usedline",
    "expand_lattice_line",
    "restore_main_usedline",
    "select_esa_line_name",
    "simplify_usedline_segment",
    "switch_to_esa_usedline",
]
