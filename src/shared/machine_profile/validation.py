from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from half_linac.src.shared.elegant_backend.parser import ElegantParser

from .loader import SUPPORTED_APP_NAMES, resolve_machine_id
from .model_backend import ElegantModelBackend, build_model_backend
from .models import AppContext, MachineProfile, MachineProfileError, ModelBackendConfig
from .resolver import resolve_channel
from .runtime_resolver import resolve_machine_runtime
from .compatibility import (
    describe_app_support,
    load_app_context,
    load_profile,
    real_commissioning_status,
    resolve_virtual_machine_usedline_workflow,
)


PASS = "pass"
SKIP = "skip"
FAIL = "fail"


@dataclass(frozen=True)
class MachineValidationCheck:
    name: str
    status: str
    detail: str

    @property
    def ok(self) -> bool:
        return self.status != FAIL


@dataclass(frozen=True)
class MachineValidationReport:
    machine_id: str
    checks: tuple[MachineValidationCheck, ...]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    @property
    def passed(self) -> tuple[MachineValidationCheck, ...]:
        return tuple(check for check in self.checks if check.status == PASS)

    @property
    def skipped(self) -> tuple[MachineValidationCheck, ...]:
        return tuple(check for check in self.checks if check.status == SKIP)

    @property
    def failed(self) -> tuple[MachineValidationCheck, ...]:
        return tuple(check for check in self.checks if check.status == FAIL)

    def get_check(self, name: str) -> MachineValidationCheck | None:
        for check in self.checks:
            if check.name == name:
                return check
        return None

    def format_text(self) -> str:
        lines = [f"Machine validation report: {self.machine_id}"]
        for check in self.checks:
            lines.append(f"{check.status.upper():4} {check.name}: {check.detail}")
        summary = (
            f"Validation passed: {len(self.passed)} pass, "
            f"{len(self.skipped)} skip, {len(self.failed)} fail."
            if self.ok
            else f"Validation failed: {len(self.failed)} fail, "
            f"{len(self.passed)} pass, {len(self.skipped)} skip."
        )
        lines.append(summary)
        return "\n".join(lines)


def validate_machine_profile(machine_id: str | None = None) -> MachineValidationReport:
    resolved_machine_id = resolve_machine_id(machine_id)
    checks: list[MachineValidationCheck] = []

    try:
        profile = load_profile(resolved_machine_id)
    except MachineProfileError as exc:
        checks.append(MachineValidationCheck("profile", FAIL, str(exc)))
        return MachineValidationReport(resolved_machine_id, tuple(checks))

    checks.append(
        MachineValidationCheck(
            "profile",
            PASS,
            f"loaded {profile.machine.display_name} with {len(profile.elements)} elements and "
            f"{len(profile.control_backends)} control backend(s).",
        )
    )

    checks.append(_validate_channel_resolution(profile))

    runtime_check, runtime = _validate_runtime(profile)
    checks.append(runtime_check)

    if "vm" in profile.control_backends or "virtual_machine" in profile.workflows:
        checks.append(_validate_virtual_machine_segments(profile, runtime))
    else:
        checks.append(
            MachineValidationCheck(
                "virtual_machine",
                SKIP,
                "profile does not declare a vm backend or virtual_machine workflow.",
            )
        )

    if "vm" in profile.control_backends:
        plan_check, parser = _validate_vm_publish_plan(profile, runtime)
        checks.append(plan_check)
        if parser is not None:
            checks.append(_validate_vm_publish_sources(profile, runtime, parser))
    else:
        checks.append(
            MachineValidationCheck(
                "vm_publish_plan",
                SKIP,
                "profile does not declare a vm backend.",
            )
        )
        checks.append(
            MachineValidationCheck(
                "vm_publish_sources",
                SKIP,
                "profile does not declare a vm backend.",
            )
        )

    for app_name in sorted(SUPPORTED_APP_NAMES):
        checks.extend(_validate_app(profile, app_name))

    return MachineValidationReport(resolved_machine_id, tuple(checks))


def _validate_channel_resolution(profile: MachineProfile) -> MachineValidationCheck:
    resolved_count = 0
    try:
        for element in profile.elements:
            for logical_channel, channel_modes in element.channels.items():
                for backend_name in channel_modes:
                    resolve_channel(profile, element.id, logical_channel, backend_name)
                    resolved_count += 1
    except MachineProfileError as exc:
        return MachineValidationCheck("channels", FAIL, str(exc))

    return MachineValidationCheck(
        "channels",
        PASS,
        f"resolved {resolved_count} declared logical channel mapping(s) across "
        f"{len(profile.control_backends)} backend(s).",
    )


def _validate_runtime(
    profile: MachineProfile,
) -> tuple[MachineValidationCheck, Any | None]:
    requires_vm_runtime = "vm" in profile.control_backends or "virtual_machine" in profile.workflows
    if profile.runtime is None:
        if requires_vm_runtime:
            return (
                MachineValidationCheck(
                    "runtime",
                    FAIL,
                    f"Machine profile {profile.machine.id!r} requires a runtime section for vm workflows.",
                ),
                None,
            )
        return (
            MachineValidationCheck(
                "runtime",
                SKIP,
                "profile does not declare a vm backend, so runtime/softIOC validation is skipped.",
            ),
            None,
        )

    try:
        runtime = resolve_machine_runtime(profile)
    except MachineProfileError as exc:
        return MachineValidationCheck("runtime", FAIL, str(exc)), None

    missing: list[str] = []
    expected_dirs = {
        "runtime.vm.root": runtime.vm.root,
        "runtime.softioc.root": runtime.softioc.root,
    }
    expected_files = {
        "runtime.vm.ui_entrypoint": runtime.vm.ui_entrypoint,
        "runtime.vm.manager_entrypoint": runtime.vm.manager_entrypoint,
        "runtime.vm.bootstrap_lattice": runtime.vm.bootstrap_lattice,
        "runtime.vm.bootstrap_ele": runtime.vm.bootstrap_ele,
    }
    generated_targets = {
        "runtime.vm.runtime_json": runtime.vm.runtime_json,
        "runtime.softioc.substitutions_file": runtime.softioc.substitutions_file,
    }

    for label, path in expected_dirs.items():
        if not path.is_dir():
            missing.append(f"{label} directory not found: {path}")
    for label, path in expected_files.items():
        if not path.is_file():
            missing.append(f"{label} file not found: {path}")
    for label, path in generated_targets.items():
        if not path.parent.is_dir():
            missing.append(f"{label} parent directory not found: {path.parent}")

    if missing:
        return MachineValidationCheck("runtime", FAIL, "; ".join(missing)), runtime

    runtime_json_state = "present" if runtime.vm.runtime_json.exists() else "parent-ready"
    substitutions_state = "present" if runtime.softioc.substitutions_file.exists() else "parent-ready"
    return (
        MachineValidationCheck(
            "runtime",
            PASS,
            "validated VM/softIOC runtime roots and source files; "
            f"runtime_json={runtime_json_state}, substitutions={substitutions_state}.",
        ),
        runtime,
    )


def _validate_virtual_machine_segments(
    profile: MachineProfile,
    runtime: Any | None,
) -> MachineValidationCheck:
    try:
        workflow = resolve_virtual_machine_usedline_workflow(profile)
    except MachineProfileError as exc:
        return MachineValidationCheck("virtual_machine", FAIL, str(exc))

    detail = (
        f"{len(workflow.predefined_usedlines)} predefined usedline(s), "
        f"{len(workflow.local_segments)} local segment definition(s), "
        f"default usedline {workflow.default_usedline!r}, "
        f"default segment {workflow.default_segment_id!r}."
    )
    if runtime is None:
        return MachineValidationCheck("virtual_machine", PASS, detail)

    try:
        parser = ElegantParser(
            runtime.vm.bootstrap_lattice,
            runtime.vm.bootstrap_ele,
            runtime.vm.line_name,
            runtime_json_path=runtime.vm.runtime_json,
            elegant_dir=runtime.vm.bootstrap_lattice.parent,
        )
        problems = _validate_virtual_machine_usedlines_against_lattice(
            workflow,
            parser.lattice,
        )
    except Exception as exc:
        return MachineValidationCheck(
            "virtual_machine",
            FAIL,
            f"{detail} Failed to parse VM lattice sources: {exc}",
        )

    if problems:
        return MachineValidationCheck("virtual_machine", FAIL, "; ".join(problems))

    return MachineValidationCheck(
        "virtual_machine",
        PASS,
        detail,
    )


def _validate_virtual_machine_usedlines_against_lattice(
    workflow: Any,
    lattice: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    problems: list[str] = []
    predefined_ids = {choice.id for choice in workflow.predefined_usedlines}
    for choice in workflow.predefined_usedlines:
        if choice.id not in lattice:
            problems.append(f"predefined usedline {choice.id!r} is not defined in VM lattice")
            continue
        if str(lattice[choice.id].get("TYPE", "")).upper() != "LINE":
            problems.append(f"predefined usedline {choice.id!r} is not a LINE")

    if workflow.default_usedline not in predefined_ids:
        problems.append(
            f"default usedline {workflow.default_usedline!r} is not in predefined usedlines"
        )

    for segment in workflow.local_segments:
        if segment.parent_usedline not in predefined_ids:
            problems.append(
                f"local segment {segment.id!r} parent {segment.parent_usedline!r} "
                "is not in predefined usedlines"
            )
            continue
        if segment.parent_usedline not in lattice:
            continue
        if str(lattice[segment.parent_usedline].get("TYPE", "")).upper() != "LINE":
            continue

        try:
            parent_usedline = _expand_lattice_line_for_validation(lattice, segment.parent_usedline)
        except MachineProfileError as exc:
            problems.append(str(exc))
            continue

        parent_set = set(parent_usedline)
        for element_id in segment.start_ids:
            if element_id not in parent_set:
                problems.append(
                    f"local segment {segment.id!r} start {element_id!r} is not in "
                    f"parent usedline {segment.parent_usedline!r}"
                )
        for element_id in segment.end_ids:
            if element_id not in parent_set:
                problems.append(
                    f"local segment {segment.id!r} end {element_id!r} is not in "
                    f"parent usedline {segment.parent_usedline!r}"
                )

    return problems


def _expand_lattice_line_for_validation(
    lattice: Mapping[str, Mapping[str, Any]],
    line_name: str,
) -> list[str]:
    if line_name not in lattice:
        raise MachineProfileError(f"VM lattice does not define line {line_name!r}.")
    if str(lattice[line_name].get("TYPE", "")).upper() != "LINE":
        raise MachineProfileError(f"VM lattice entry {line_name!r} is not a LINE.")
    return _expand_lattice_line_tokens_for_validation(
        lattice,
        _split_lattice_line_for_validation(lattice[line_name].get("LINE", "")),
        stack=(line_name,),
    )


def _expand_lattice_line_tokens_for_validation(
    lattice: Mapping[str, Mapping[str, Any]],
    tokens: Sequence[str],
    *,
    stack: tuple[str, ...],
) -> list[str]:
    result: list[str] = []
    for token in tokens:
        if token not in lattice:
            raise MachineProfileError(f"VM lattice line references unknown element {token!r}.")
        element = lattice[token]
        if str(element.get("TYPE", "")).upper() == "LINE":
            if token in stack:
                cycle = " -> ".join((*stack, token))
                raise MachineProfileError(f"VM lattice line recursion detected: {cycle}.")
            result.extend(
                _expand_lattice_line_tokens_for_validation(
                    lattice,
                    _split_lattice_line_for_validation(element.get("LINE", "")),
                    stack=(*stack, token),
                )
            )
            continue
        result.append(token)
    return result


def _split_lattice_line_for_validation(line: Any) -> list[str]:
    return [token.strip() for token in str(line).split(",") if token.strip()]


def _validate_vm_publish_plan(
    profile: MachineProfile,
    runtime: Any | None,
) -> tuple[MachineValidationCheck, ElegantParser | None]:
    from half_linac.src.shared.elegant_backend.publisher import build_vm_publish_plan

    try:
        plan = build_vm_publish_plan(profile)
    except MachineProfileError as exc:
        return MachineValidationCheck("vm_publish_plan", FAIL, str(exc)), None

    detail = (
        f"{len(plan.bpm_specs)} BPM publish spec(s), "
        f"{len(plan.watch_image_specs)} watch-image publish spec(s)."
    )
    if runtime is None:
        return MachineValidationCheck("vm_publish_plan", PASS, detail), None

    try:
        parser = ElegantParser(
            runtime.vm.bootstrap_lattice,
            runtime.vm.bootstrap_ele,
            runtime.vm.line_name,
            runtime_json_path=runtime.vm.runtime_json,
            elegant_dir=runtime.vm.bootstrap_lattice.parent,
        )
    except Exception as exc:
        return (
            MachineValidationCheck(
                "vm_publish_plan",
                FAIL,
                f"{detail} Failed to parse VM lattice sources: {exc}",
            ),
            None,
        )

    return MachineValidationCheck("vm_publish_plan", PASS, detail), parser


def _validate_vm_publish_sources(
    profile: MachineProfile,
    runtime: Any,
    parser: ElegantParser,
) -> MachineValidationCheck:
    from half_linac.src.shared.elegant_backend.publisher import build_vm_publish_plan

    try:
        plan = build_vm_publish_plan(profile)
    except MachineProfileError as exc:
        return MachineValidationCheck("vm_publish_sources", FAIL, str(exc))

    problems: list[str] = []
    for spec in plan.watch_image_specs:
        element = parser.lattice.get(spec.source_watch_id)
        if element is None:
            problems.append(f"{spec.source_watch_id} not found in {runtime.vm.bootstrap_lattice.name}")
            continue
        if element.get("TYPE") != "WATCH":
            problems.append(f"{spec.source_watch_id} is not a WATCH element")

    if problems:
        return MachineValidationCheck("vm_publish_sources", FAIL, "; ".join(problems))

    return MachineValidationCheck(
        "vm_publish_sources",
        PASS,
        f"validated {len(plan.watch_image_specs)} VM watch source(s) against {runtime.vm.bootstrap_lattice.name}.",
    )


def _validate_app(profile: MachineProfile, app_name: str) -> list[MachineValidationCheck]:
    supported, reason = describe_app_support(profile.machine.id, app_name)
    if not supported:
        return [
            MachineValidationCheck(
                f"app:{app_name}",
                SKIP,
                reason or "app is not supported by this machine profile.",
            )
        ]

    contexts: list[AppContext] = []
    failures: list[str] = []
    for backend_name in profile.control_backends:
        try:
            contexts.append(
                load_app_context(
                    app_name,
                    machine_id=profile.machine.id,
                    control_backend=backend_name,
                )
            )
        except MachineProfileError as exc:
            failures.append(f"{backend_name}: {exc}")

    if failures:
        return [
            MachineValidationCheck(
                f"app:{app_name}",
                FAIL,
                "; ".join(failures),
            )
        ]

    checks = [
        MachineValidationCheck(
            f"app:{app_name}",
            PASS,
            f"loaded {len(contexts)} app context(s) across backend(s): "
            + ", ".join(profile.control_backends),
        )
    ]

    if app_name in {"bba", "emit_measure"} and contexts:
        checks.append(_validate_elegant_model_backend(app_name, contexts[0]))
    elif app_name == "energy_spectrum" and contexts:
        checks.append(_validate_energy_spectrum_model_config(contexts[0].model_backend))

    if profile.machine.id == "irfel":
        checks.append(_validate_real_commissioning_status(profile, app_name))

    return checks


def _validate_real_commissioning_status(
    profile: MachineProfile,
    app_name: str,
) -> MachineValidationCheck:
    try:
        status = real_commissioning_status(profile, app_name)
    except MachineProfileError as exc:
        return MachineValidationCheck(f"commissioning:{app_name}", FAIL, str(exc))

    return MachineValidationCheck(
        f"commissioning:{app_name}",
        PASS,
        f"real_status={status}.",
    )


MODEL_VALIDATED_APP_NAMES = frozenset({"bba", "emit_measure", "energy_spectrum"})


def describe_app_model_support(machine_id: str | None, app_name: str) -> tuple[bool, str | None]:
    if app_name not in MODEL_VALIDATED_APP_NAMES:
        return True, None

    try:
        context = load_app_context(app_name, machine_id=machine_id)
    except MachineProfileError as exc:
        return False, str(exc)

    if app_name in {"bba", "emit_measure"}:
        check = _validate_elegant_model_backend(app_name, context)
    else:
        check = _validate_energy_spectrum_model_config(context.model_backend)

    if check.ok:
        return True, None
    return False, check.detail


def _validate_elegant_model_backend(app_name: str, context: AppContext) -> MachineValidationCheck:
    try:
        backend = build_model_backend(context)
    except MachineProfileError as exc:
        return MachineValidationCheck(f"model:{app_name}", FAIL, str(exc))

    if not isinstance(backend, ElegantModelBackend):
        return MachineValidationCheck(
            f"model:{app_name}",
            SKIP,
            f"validator only checks elegant model backends, got {type(backend).__name__}.",
        )

    missing: list[str] = []
    required_files = {
        "source_json": backend.source_json,
        "source_lattice": backend.source_lattice,
        "emit_ini_ele": backend.emit_ini_ele,
    }
    required_dirs = {
        "working_dir": backend.working_dir,
    }
    generated_targets = {
        "emit_lte": backend.emit_lte,
        "emit_ele": backend.emit_ele,
        "emit_json": backend.emit_json,
        "emit_mat": backend.emit_mat,
    }

    for label, path in required_files.items():
        if not path.is_file():
            missing.append(f"{label} file not found: {path}")
    for label, path in required_dirs.items():
        if not path.is_dir():
            missing.append(f"{label} directory not found: {path}")
    for label, path in generated_targets.items():
        if not path.parent.is_dir():
            missing.append(f"{label} parent directory not found: {path.parent}")

    if missing:
        return MachineValidationCheck(f"model:{app_name}", FAIL, "; ".join(missing))

    if app_name == "emit_measure" and context.emit_measure_workflow is not None:
        try:
            runtime_state = ElegantParser(
                backend.source_lattice,
                backend.emit_ini_ele,
                backend.line_name,
                elegant_dir=backend.working_dir,
            ).build_runtime_state()
        except Exception as exc:
            return MachineValidationCheck(
                f"model:{app_name}",
                FAIL,
                f"failed to parse elegant model lattice for emit_measure: {exc}",
            )

        lattice = runtime_state.get("lattice", {})
        line_names = {backend.line_name}
        line_names.update(
            preset.model_line
            for preset in context.emit_measure_workflow.presets
            if preset.model_line
        )
        for line_name in sorted(line_names):
            line = lattice.get(line_name)
            if line is None:
                return MachineValidationCheck(
                    f"model:{app_name}",
                    FAIL,
                    f"emit_measure model line {line_name!r} is not defined in {backend.source_lattice}.",
                )
            if str(line.get("TYPE", "")).upper() != "LINE":
                return MachineValidationCheck(
                    f"model:{app_name}",
                    FAIL,
                    f"emit_measure model line {line_name!r} is not a LINE in {backend.source_lattice}.",
                )

    return MachineValidationCheck(
        f"model:{app_name}",
        PASS,
        "validated elegant model backend input files and output parents.",
    )


def _validate_energy_spectrum_model_config(
    model_backend: ModelBackendConfig | None,
) -> MachineValidationCheck:
    if model_backend is None:
        return MachineValidationCheck(
            "model:energy_spectrum",
            FAIL,
            "energy_spectrum does not define a model backend.",
        )
    if model_backend.engine != "elegant":
        return MachineValidationCheck(
            "model:energy_spectrum",
            SKIP,
            f"validator only checks elegant energy-spectrum backends, got {model_backend.engine!r}.",
        )

    config = model_backend.config
    missing: list[str] = []
    required_files = {
        "working_dir": _require_model_path(config, "working_dir"),
        "source_lattice": _require_model_path(config, "source_lattice"),
        "energy_ini_ele_file": _require_model_path(config, "energy_ini_ele_file"),
    }
    generated_targets = {
        "energy_json_path": _require_model_path(config, "energy_json_path"),
        "energy_lte_file": _require_model_path(config, "energy_lte_file"),
        "energy_ele_file": _require_model_path(config, "energy_ele_file"),
        "energy_mat_file": _require_model_path(config, "energy_mat_file"),
        "energy_twi_file": _require_model_path(config, "energy_twi_file"),
    }

    working_dir = required_files["working_dir"]
    if not working_dir.is_dir():
        missing.append(f"working_dir directory not found: {working_dir}")
    for label in ("source_lattice", "energy_ini_ele_file"):
        path = required_files[label]
        if not path.is_file():
            missing.append(f"{label} file not found: {path}")
    for label, path in generated_targets.items():
        if not path.parent.is_dir():
            missing.append(f"{label} parent directory not found: {path.parent}")

    if missing:
        return MachineValidationCheck("model:energy_spectrum", FAIL, "; ".join(missing))

    return MachineValidationCheck(
        "model:energy_spectrum",
        PASS,
        "validated energy-spectrum elegant input files and output parents.",
    )


def _require_model_path(config: Mapping[str, Any], key: str) -> Path:
    raw_value = config.get(key)
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise MachineProfileError(f"Model backend config is missing {key!r}.")
    return Path(raw_value)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run offline machine-profile acceptance checks for one machine profile.",
    )
    parser.add_argument(
        "machine_id",
        nargs="?",
        default=None,
        help=(
            "Machine profile id under configs/machines/. Defaults to "
            "HALF_LINAC_MACHINE_ID, then legacy HALF_MACHINE_ID, then 'half'."
        ),
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    report = validate_machine_profile(args.machine_id)
    print(report.format_text())
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
