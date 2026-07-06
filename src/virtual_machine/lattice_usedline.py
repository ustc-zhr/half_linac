from __future__ import annotations

import copy
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from half_linac.src.shared.elegant_backend import ElegantParser
from half_linac.src.shared.machine_profile import (
    MachineProfileError,
    get_workflow,
    resolve_bend_write_channel,
    resolve_channel,
    resolve_corrector_write_channel,
    resolve_machine_runtime,
    resolve_virtual_machine_usedline_workflow,
)
from half_linac.src.shared.runtime_state import read_runtime_state, update_runtime_state, write_runtime_state


PREWATCH_ID = "PREW"
PREWATCH_FILENAME = "pre.bun"
VM_PV_SYNC_CONNECTION_TIMEOUT_S = 0.5
VM_WRITABLE_LATTICE_FIELD_BY_CHANNEL = {
    ("quad", "k1"): "K1",
    ("corr", "kick"): "KICK",
    ("bend", "angle"): "ANGLE",
}
USEDLINE_CONTEXT_KEY = "usedline_context"


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


def reload_initial_runtime_state() -> list[str]:
    runtime = resolve_machine_runtime()
    parser = ElegantParser(
        runtime.vm.bootstrap_lattice,
        runtime.vm.bootstrap_ele,
        runtime.vm.line_name,
        runtime_json_path=runtime.vm.runtime_json,
        elegant_dir=runtime.vm.bootstrap_lattice.parent,
    )
    state = parser.build_runtime_state()
    _set_full_usedline_context(
        runtime,
        state,
        runtime.vm.line_name,
        source="reload_initial_lattice",
    )
    _ensure_writable_lattice_defaults(runtime, state)
    write_runtime_state(runtime.vm.runtime_json, state)
    _sync_writable_vm_pvs(runtime, state)
    print(
        f"reloaded VM runtime state from {runtime.vm.bootstrap_lattice.name} "
        f"and {runtime.vm.bootstrap_ele.name}: {len(state['usedline'])} element(s)."
    )
    return list(state["usedline"])


def describe_runtime_usedline(runtime=None) -> str:
    runtime = runtime or resolve_machine_runtime()
    try:
        state = read_runtime_state(runtime.vm.runtime_json)
    except FileNotFoundError:
        return "No runtime JSON"
    return format_usedline_context(infer_usedline_context(runtime, state))


def infer_usedline_context(runtime, state: Mapping[str, Any]) -> dict[str, Any]:
    usedline = _current_usedline(state)
    if not usedline:
        return {"mode": "empty", "count": 0}

    explicit = state.get(USEDLINE_CONTEXT_KEY)
    if _context_mode(explicit) == "prewatch" and _context_matches_usedline(explicit, usedline):
        return dict(explicit)

    lattice = state.get("lattice", {})
    if not isinstance(lattice, Mapping):
        return _custom_usedline_context(usedline)

    try:
        workflow = resolve_virtual_machine_usedline_workflow(runtime.profile)
        predefined = workflow.predefined_usedlines
    except MachineProfileError:
        predefined = ()

    for choice in predefined:
        try:
            expanded = expand_lattice_line(lattice, choice.id)
        except LatticeUsedlineError:
            continue
        if expanded == usedline:
            return _full_usedline_context(
                choice.id,
                len(usedline),
                label=choice.label,
                source="runtime_json",
            )

    for choice in predefined:
        try:
            parent = expand_lattice_line(lattice, choice.id)
        except LatticeUsedlineError:
            continue
        match = _find_contiguous_subsequence(parent, usedline)
        if match is None:
            continue
        return _segment_usedline_context(
            choice.id,
            usedline[0],
            usedline[-1],
            len(usedline),
            source="runtime_json",
        )

    return _custom_usedline_context(usedline)


def format_usedline_context(context: Mapping[str, Any]) -> str:
    mode = str(context.get("mode", "")).strip().lower()
    count = int(context.get("count") or 0)
    if mode == "full":
        line = str(context.get("line") or context.get("line_name") or "UNKNOWN")
        return f"{line} (full, {count} elements)"
    if mode == "segment":
        parent = str(context.get("parent_usedline") or "UNKNOWN")
        start = str(context.get("start") or "?")
        end = str(context.get("end") or "?")
        return f"{parent} / {start} -> {end} ({count} elements)"
    if mode == "prewatch":
        parent = str(context.get("parent_usedline") or "UNKNOWN")
        end = str(context.get("end") or "?")
        return f"Preparing {parent} -> {end} ({count} elements)"
    if mode == "custom":
        first = str(context.get("first") or "?")
        last = str(context.get("last") or "?")
        return f"Custom: {first} -> {last} ({count} elements)"
    return "No usedline"


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
    if wait_s is None:
        workflow = resolve_virtual_machine_usedline_workflow(runtime.profile)
        wait_s = workflow.segment_wait_s
    else:
        wait_s = float(wait_s)

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
        runtime_state[USEDLINE_CONTEXT_KEY] = {
            "mode": "prewatch",
            "parent_usedline": parent_usedline,
            "start": start_element,
            "end": end_element,
            "first": preline[0] if preline else None,
            "last": preline[-1] if preline else None,
            "count": len(preline),
            "source": "simplify_VM",
        }
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
        runtime_state[USEDLINE_CONTEXT_KEY] = _segment_usedline_context(
            parent_usedline,
            start_element,
            end_element,
            len(segment_usedline),
            source="simplify_VM",
        )
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


def reload_initial_runtime_state_cli(argv: Sequence[str] | None = None) -> int:
    del argv
    return _run_cli("reload initial runtime state", reload_initial_runtime_state)


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
        _set_full_usedline_context(
            runtime,
            runtime_state,
            line_name,
            source="apply_predefined_usedline",
        )
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


def _ensure_writable_lattice_defaults(runtime, state: dict[str, Any]) -> None:
    lattice = state.get("lattice", {})
    if not isinstance(lattice, dict):
        return

    for element in runtime.profile.elements:
        if element.kind != "corr":
            continue
        element_lattice = lattice.get(element.id)
        if isinstance(element_lattice, dict):
            element_lattice.setdefault("KICK", "0")


def _sync_writable_vm_pvs(runtime, state: Mapping[str, Any]) -> None:
    pv_names, pv_values = _collect_writable_vm_pvs(runtime, state)
    if not pv_names:
        return

    try:
        import epics

        results = epics.caput_many(
            pv_names,
            pv_values,
            wait=False,
            connection_timeout=VM_PV_SYNC_CONNECTION_TIMEOUT_S,
        )
    except Exception as exc:
        print(f"runtime JSON reloaded; IOC writable PV sync skipped: {exc}", file=sys.stderr)
        return

    failed_pvs = _failed_caput_pvs(pv_names, results)
    if failed_pvs:
        print(
            "runtime JSON reloaded; IOC writable PV sync reached "
            f"{len(pv_names) - len(failed_pvs)}/{len(pv_names)} PV(s); "
            f"first failed PV: {failed_pvs[0]}",
            file=sys.stderr,
        )
        return

    print(f"synchronized {len(pv_names)} VM writable PV(s) from runtime JSON.")


def _collect_writable_vm_pvs(runtime, state: Mapping[str, Any]) -> tuple[list[str], list[Any]]:
    lattice = state.get("lattice", {})
    if not isinstance(lattice, Mapping):
        return [], []

    pv_names: list[str] = []
    pv_values: list[Any] = []
    for element in runtime.profile.elements:
        element_lattice = lattice.get(element.id)
        if not isinstance(element_lattice, Mapping):
            continue

        for logical_channel, field_name in _writable_channels_for_element_kind(element.kind):
            pv_name = _resolve_vm_writable_channel(
                runtime.profile,
                element.id,
                element.kind,
                logical_channel,
            )
            if not pv_name or field_name not in element_lattice:
                continue
            pv_names.append(pv_name)
            pv_values.append(element_lattice[field_name])
    return pv_names, pv_values


def _current_usedline(state: Mapping[str, Any]) -> list[str]:
    raw_usedline = state.get("usedline", [])
    if not isinstance(raw_usedline, Sequence) or isinstance(raw_usedline, (str, bytes)):
        return []
    return [str(item) for item in raw_usedline]


def _set_full_usedline_context(runtime, state: dict[str, Any], line_name: str, *, source: str) -> None:
    usedline = _current_usedline(state)
    label = None
    try:
        workflow = resolve_virtual_machine_usedline_workflow(runtime.profile)
        for choice in workflow.predefined_usedlines:
            if choice.id == line_name:
                label = choice.label
                break
    except MachineProfileError:
        pass
    state[USEDLINE_CONTEXT_KEY] = _full_usedline_context(
        line_name,
        len(usedline),
        label=label,
        source=source,
    )


def _full_usedline_context(
    line_name: str,
    count: int,
    *,
    label: str | None = None,
    source: str,
) -> dict[str, Any]:
    return {
        "mode": "full",
        "line": line_name,
        "label": label or line_name,
        "count": count,
        "source": source,
    }


def _segment_usedline_context(
    parent_usedline: str,
    start: str,
    end: str,
    count: int,
    *,
    source: str,
) -> dict[str, Any]:
    return {
        "mode": "segment",
        "parent_usedline": parent_usedline,
        "start": start,
        "end": end,
        "count": count,
        "source": source,
    }


def _custom_usedline_context(usedline: Sequence[str]) -> dict[str, Any]:
    return {
        "mode": "custom",
        "first": usedline[0] if usedline else None,
        "last": usedline[-1] if usedline else None,
        "count": len(usedline),
        "source": "runtime_json",
    }


def _context_matches_usedline(context: Any, usedline: Sequence[str]) -> bool:
    if not isinstance(context, Mapping):
        return False
    count = context.get("count")
    if count is not None:
        try:
            if int(count) != len(usedline):
                return False
        except (TypeError, ValueError):
            return False
    mode = _context_mode(context)
    if mode == "full":
        return bool(context.get("line"))
    if mode == "segment":
        return context.get("start") == usedline[0] and context.get("end") == usedline[-1]
    if mode == "prewatch":
        return context.get("last") == usedline[-1]
    if mode == "custom":
        return context.get("first") == usedline[0] and context.get("last") == usedline[-1]
    return False


def _context_mode(context: Any) -> str:
    if not isinstance(context, Mapping):
        return ""
    return str(context.get("mode", "")).strip().lower()


def _find_contiguous_subsequence(parent: Sequence[str], candidate: Sequence[str]) -> tuple[int, int] | None:
    if not candidate or len(candidate) > len(parent):
        return None
    window = len(candidate)
    for start in range(0, len(parent) - window + 1):
        end = start + window
        if list(parent[start:end]) == list(candidate):
            return start, end - 1
    return None


def _writable_channels_for_element_kind(kind: str) -> tuple[tuple[str, str], ...]:
    writable = [
        (logical_channel, field_name)
        for (element_kind, logical_channel), field_name in VM_WRITABLE_LATTICE_FIELD_BY_CHANNEL.items()
        if element_kind == kind
    ]
    return tuple(writable)


def _resolve_vm_channel(profile, element_id: str, logical_channel: str) -> str | None:
    try:
        return resolve_channel(profile, element_id, logical_channel, "vm")
    except MachineProfileError:
        return None


def _resolve_vm_writable_channel(
    profile,
    element_id: str,
    element_kind: str,
    logical_channel: str,
) -> str | None:
    try:
        if element_kind == "corr" and logical_channel == "kick":
            return resolve_corrector_write_channel(profile, element_id, "vm")
        if element_kind == "bend" and logical_channel == "angle":
            return resolve_bend_write_channel(profile, element_id, "vm")
        return resolve_channel(profile, element_id, logical_channel, "vm")
    except MachineProfileError:
        return None


def _failed_caput_pvs(pv_names: Sequence[str], results) -> list[str]:
    if results is None:
        return []
    if isinstance(results, bool):
        return [] if results else list(pv_names)

    failed: list[str] = []
    for pv_name, result in zip(pv_names, results):
        if result != 1:
            failed.append(pv_name)
    return failed


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
    "describe_runtime_usedline",
    "expand_lattice_line",
    "format_usedline_context",
    "infer_usedline_context",
    "reload_initial_runtime_state",
    "restore_main_usedline",
    "select_esa_line_name",
    "simplify_usedline_segment",
    "switch_to_esa_usedline",
]
