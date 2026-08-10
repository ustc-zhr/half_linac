from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import loader as profile_loader
from .models import MachineProfile, MachineProfileError


@dataclass(frozen=True)
class ResolvedVmRuntimeConfig:
    root: Path
    ui_entrypoint: Path
    manager_entrypoint: Path
    runtime_json: Path
    bootstrap_lattice: Path
    bootstrap_ele: Path
    line_name: str


@dataclass(frozen=True)
class ResolvedSoftIocRuntimeConfig:
    root: Path
    substitutions_file: Path


@dataclass(frozen=True)
class ResolvedMachineRuntimeConfig:
    profile: MachineProfile
    vm: ResolvedVmRuntimeConfig
    softioc: ResolvedSoftIocRuntimeConfig


def resolve_machine_runtime(
    target: MachineProfile | str | None = None,
) -> ResolvedMachineRuntimeConfig:
    profile = _coerce_profile(target)
    runtime = profile.runtime
    if runtime is None:
        raise MachineProfileError(
            f"Machine profile {profile.machine.id!r} does not define a runtime section."
        )

    vm_root = _resolve_repo_path(runtime.vm.root)
    softioc_root = _resolve_repo_path(runtime.softioc.root)
    substitutions_file = Path(runtime.softioc.substitutions_file)
    if not substitutions_file.is_absolute():
        substitutions_file = softioc_root / substitutions_file

    return ResolvedMachineRuntimeConfig(
        profile=profile,
        vm=ResolvedVmRuntimeConfig(
            root=vm_root,
            ui_entrypoint=_resolve_repo_path(runtime.vm.ui_entrypoint),
            manager_entrypoint=_resolve_repo_path(runtime.vm.manager_entrypoint),
            runtime_json=_resolve_repo_path(runtime.vm.runtime_json),
            bootstrap_lattice=_resolve_repo_path(runtime.vm.bootstrap_lattice),
            bootstrap_ele=_resolve_repo_path(runtime.vm.bootstrap_ele),
            line_name=runtime.vm.line_name,
        ),
        softioc=ResolvedSoftIocRuntimeConfig(
            root=softioc_root,
            substitutions_file=substitutions_file,
        ),
    )


def resolve_vm_runtime(
    target: MachineProfile | str | None = None,
) -> ResolvedVmRuntimeConfig:
    return resolve_machine_runtime(target).vm


def resolve_softioc_runtime(
    target: MachineProfile | str | None = None,
) -> ResolvedSoftIocRuntimeConfig:
    return resolve_machine_runtime(target).softioc


def _coerce_profile(target: MachineProfile | str | None) -> MachineProfile:
    if isinstance(target, MachineProfile):
        return target
    return profile_loader.load_profile(target)


def _resolve_repo_path(raw_path: str) -> Path:
    path = Path(raw_path)
    return path if path.is_absolute() else profile_loader.repo_root() / path
