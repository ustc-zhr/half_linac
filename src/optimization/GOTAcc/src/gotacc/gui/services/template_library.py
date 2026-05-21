from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TemplateDefinition:
    key: str
    category: str
    title: str
    description: str
    task: dict[str, Any]


def _base_machine_config() -> dict[str, Any]:
    return {
        "ca_address": "127.0.0.1",
        "confirm_before_write": True,
        "restore_on_abort": True,
        "readback_check": False,
        "readback_tol": 1e-6,
        "set_interval": 1.0,
        "sample_interval": 0.2,
        "write_timeout": 1.0,
        "write_policy": "none",
        "objective_policies": [],
        "mapping": [],
        "write_links": [],
    }


def _offline_variables() -> list[dict[str, str]]:
    return [
        {
            "Enable": "Y",
            "Name": "x0",
            "Lower": "-1.0",
            "Upper": "1.0",
            "Initial": "0.0",
            "Group": "main",
        },
        {
            "Enable": "Y",
            "Name": "x1",
            "Lower": "-1.0",
            "Upper": "1.0",
            "Initial": "0.0",
            "Group": "main",
        },
    ]


def _single_objective(name: str) -> list[dict[str, str]]:
    return [
        {
            "Enable": "Y",
            "Name": name,
            "Direction": "maximize",
            "Weight": "1.0",
            "Samples": "1",
            "Math": "mean",
        }
    ]


def _multi_objectives() -> list[dict[str, str]]:
    return [
        {
            "Enable": "Y",
            "Name": "obj0",
            "Direction": "maximize",
            "Weight": "1.0",
            "Samples": "1",
            "Math": "mean",
        },
        {
            "Enable": "Y",
            "Name": "obj1",
            "Direction": "maximize",
            "Weight": "1.0",
            "Samples": "1",
            "Math": "mean",
        },
    ]


def _online_variables() -> list[dict[str, str]]:
    return [
        {
            "Enable": "Y",
            "Name": "k0",
            "Lower": "-1.0",
            "Upper": "1.0",
            "Initial": "0.0",
            "Group": "knob",
        }
    ]


def _online_objectives() -> list[dict[str, str]]:
    return [
        {
            "Enable": "Y",
            "Name": "beam_energy",
            "Direction": "maximize",
            "Weight": "1.0",
            "Samples": "3",
            "Math": "mean",
        }
    ]


def _online_multi_objectives() -> list[dict[str, str]]:
    return [
        {
            "Enable": "Y",
            "Name": "obj0",
            "Direction": "maximize",
            "Weight": "1.0",
            "Samples": "3",
            "Math": "mean",
        },
        {
            "Enable": "Y",
            "Name": "obj1",
            "Direction": "maximize",
            "Weight": "1.0",
            "Samples": "3",
            "Math": "mean",
        },
    ]


def _online_constraints() -> list[dict[str, str]]:
    return [
        {
            "Enable": "Y",
            "Name": "cons0",
            "Lower": "",
            "Upper": "1.0",
            "Math": "mean",
        }
    ]


def _online_mapping() -> list[dict[str, str]]:
    return [
        {
            "Role": "knob",
            "Name": "k0",
            "PV Name": "TEST:K0",
            "Readback": "TEST:K0",
            "Group": "main",
            "Note": "Demo knob PV",
        },
        {
            "Role": "objective",
            "Name": "beam_energy",
            "PV Name": "TEST:Y0",
            "Readback": "TEST:Y0",
            "Group": "metric",
            "Note": "Demo objective PV",
        },
    ]


def _online_constrained_multi_mapping() -> list[dict[str, str]]:
    return [
        {
            "Role": "knob",
            "Name": "k0",
            "PV Name": "TEST:K0",
            "Readback": "TEST:K0",
            "Group": "main",
            "Note": "Demo knob PV",
        },
        {
            "Role": "objective",
            "Name": "obj0",
            "PV Name": "TEST:Y0",
            "Readback": "TEST:Y0",
            "Group": "metric",
            "Note": "Demo objective PV",
        },
        {
            "Role": "objective",
            "Name": "obj1",
            "PV Name": "TEST:Y1",
            "Readback": "TEST:Y1",
            "Group": "metric",
            "Note": "Demo second objective PV",
        },
        {
            "Role": "constraint",
            "Name": "cons0",
            "PV Name": "TEST:C0",
            "Readback": "TEST:C0",
            "Group": "constraint",
            "Note": "Demo output constraint PV",
        },
    ]


def _task(
    *,
    task_name: str,
    mode: str,
    objective_type: str,
    algorithm: str,
    description: str,
    test_function: str = "",
    variables: list[dict[str, str]],
    objectives: list[dict[str, str]],
    algorithm_params: list[dict[str, str]],
    constraints: list[dict[str, str]] | None = None,
    machine: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "task_name": task_name,
        "mode": mode,
        "objective_type": objective_type,
        "algorithm": algorithm,
        "test_function": test_function,
        "seed": 0,
        "max_evaluations": 24,
        "workdir": ".",
        "description": description,
        "variables": variables,
        "objectives": objectives,
        "constraints": constraints if constraints is not None else [],
        "algorithm_params": algorithm_params,
        "machine": machine if machine is not None else _base_machine_config(),
    }


TEMPLATE_LIBRARY: tuple[TemplateDefinition, ...] = (
    TemplateDefinition(
        key="offline_bo_rosenbrock",
        category="Offline",
        title="Single Objective / BO",
        description="Two-variable offline BO on Rosenbrock. Good default for smoke-testing the full GUI runner chain.",
        task=_task(
            task_name="offline_bo_rosenbrock",
            mode="Offline",
            objective_type="Single Objective",
            algorithm="BO",
            description="Built-in offline BO template using the Rosenbrock benchmark.",
            test_function="rosenbrock",
            variables=_offline_variables(),
            objectives=_single_objective("rosenbrock"),
            algorithm_params=[
                {"Parameter": "n_init", "Value": "8", "Type": "int", "Description": "Initial random evaluations"},
                {"Parameter": "kernel_type", "Value": "matern", "Type": "str", "Description": "Kernel type"},
                {"Parameter": "acq", "Value": "ucb", "Type": "str", "Description": "Acquisition function"},
            ],
        ),
    ),
    TemplateDefinition(
        key="offline_turbo_ackley",
        category="Offline",
        title="Single Objective / TuRBO",
        description="Offline TuRBO setup on Ackley. Useful for checking trust-region controls and pause/stop behavior.",
        task=_task(
            task_name="offline_turbo_ackley",
            mode="Offline",
            objective_type="Single Objective",
            algorithm="TuRBO",
            description="Built-in offline TuRBO template using the Ackley benchmark.",
            test_function="ackley",
            variables=_offline_variables(),
            objectives=_single_objective("ackley"),
            algorithm_params=[
                {"Parameter": "n_init", "Value": "10", "Type": "int", "Description": "Initial random evaluations"},
                {"Parameter": "n_trust_regions", "Value": "1", "Type": "int", "Description": "Trust region count"},
                {"Parameter": "length_init", "Value": "0.8", "Type": "float", "Description": "Initial region length"},
                {"Parameter": "length_min", "Value": "0.05", "Type": "float", "Description": "Minimum region length"},
            ],
        ),
    ),
    TemplateDefinition(
        key="offline_mobo_tradeoff",
        category="Offline",
        title="Multi Objective / MOBO",
        description="Two-objective offline MOBO template using the built-in smooth tradeoff function.",
        task=_task(
            task_name="offline_mobo_tradeoff",
            mode="Offline",
            objective_type="Multi Objective",
            algorithm="MOBO",
            description="Built-in offline MOBO template using a synthetic two-objective tradeoff.",
            test_function="tradeoff",
            variables=_offline_variables(),
            objectives=_multi_objectives(),
            algorithm_params=[
                {"Parameter": "n_init", "Value": "12", "Type": "int", "Description": "Initial random evaluations"},
                {"Parameter": "ref_point", "Value": "[-2.0, -2.0]", "Type": "json", "Description": "Reference point"},
            ],
        ),
    ),
    TemplateDefinition(
        key="offline_nsga2_tradeoff",
        category="Offline",
        title="Multi Objective / NSGA-II",
        description="Population-based multi-objective template for testing Pareto front rendering and run summaries.",
        task=_task(
            task_name="offline_nsga2_tradeoff",
            mode="Offline",
            objective_type="Multi Objective",
            algorithm="NSGA-II",
            description="Built-in offline NSGA-II template using a synthetic two-objective tradeoff.",
            variables=_offline_variables(),
            objectives=_multi_objectives(),
            algorithm_params=[
                {"Parameter": "pop_size", "Value": "24", "Type": "int", "Description": "Population size"},
                {"Parameter": "n_generations", "Value": "8", "Type": "int", "Description": "Generation count"},
                {"Parameter": "ref_point", "Value": "[-2.0, -2.0]", "Type": "json", "Description": "Reference point"},
            ],
        ),
    ),
    TemplateDefinition(
        key="online_epics_bo_guarded",
        category="Online",
        title="EPICS / BO",
        description="Single-objective EPICS BO template with FEL energy guard policy enabled.",
        task=_task(
            task_name="online_epics_bo",
            mode="Online EPICS",
            objective_type="Single Objective",
            algorithm="BO",
            description="Built-in online EPICS BO template with one knob and one guarded objective.",
            variables=_online_variables(),
            objectives=_online_objectives(),
            algorithm_params=[
                {"Parameter": "n_init", "Value": "5", "Type": "int", "Description": "Initial random evaluations"},
            ],
            machine={
                **_base_machine_config(),
                "ca_address": "127.0.0.1",
                "set_interval": 1.0,
                "sample_interval": 0.2,
                "objective_policies": [
                    {
                        "Enabled": "True",
                        "Policy Name": "fel_energy_guard",
                        "Kwargs JSON": '{\n  "target_col": 0,\n  "large_threshold": 1000000.0,\n  "change_threshold": 1e-06\n}',
                    }
                ],
                "mapping": _online_mapping(),
            },
        ),
    ),
    TemplateDefinition(
        key="online_epics_turbo",
        category="Online",
        title="EPICS / TuRBO",
        description="Single-objective EPICS TuRBO template for faster local exploration around a small knob set.",
        task=_task(
            task_name="online_epics_turbo",
            mode="Online EPICS",
            objective_type="Single Objective",
            algorithm="TuRBO",
            description="Built-in online EPICS TuRBO template with one knob and one objective.",
            variables=_online_variables(),
            objectives=_online_objectives(),
            algorithm_params=[
                {"Parameter": "n_init", "Value": "6", "Type": "int", "Description": "Initial random evaluations"},
                {"Parameter": "n_trust_regions", "Value": "1", "Type": "int", "Description": "Trust region count"},
                {"Parameter": "length_init", "Value": "0.8", "Type": "float", "Description": "Initial region length"},
                {"Parameter": "length_min", "Value": "0.05", "Type": "float", "Description": "Minimum region length"},
            ],
            machine={
                **_base_machine_config(),
                "ca_address": "127.0.0.1",
                "set_interval": 1.0,
                "sample_interval": 0.2,
                "mapping": _online_mapping(),
            },
        ),
    ),
    TemplateDefinition(
        key="online_epics_consmggpo",
        category="Online",
        title="EPICS / ConsMGGPO",
        description="Constrained multi-objective EPICS template using output constraints and MG-GPO population search.",
        task=_task(
            task_name="online_epics_consmggpo",
            mode="Online EPICS",
            objective_type="Multi Objective",
            algorithm="ConsMGGPO",
            description="Built-in online EPICS ConsMGGPO template with one knob, two objectives and one output constraint.",
            variables=_online_variables(),
            objectives=_online_multi_objectives(),
            constraints=_online_constraints(),
            algorithm_params=[
                {"Parameter": "pop_size", "Value": "12", "Type": "int", "Description": "Initial population size"},
                {"Parameter": "evals_per_gen", "Value": "6", "Type": "int", "Description": "True evaluations per generation"},
                {"Parameter": "n_generations", "Value": "2", "Type": "int", "Description": "Generation count"},
                {"Parameter": "acq_mode", "Value": "ucb", "Type": "str", "Description": "MGGPO acquisition mode"},
                {"Parameter": "ref_point", "Value": "[0.0, 0.0]", "Type": "json", "Description": "Hypervolume reference point"},
            ],
            machine={
                **_base_machine_config(),
                "ca_address": "127.0.0.1",
                "set_interval": 1.0,
                "sample_interval": 0.2,
                "mapping": _online_constrained_multi_mapping(),
            },
        ),
    ),
)


def list_templates() -> list[TemplateDefinition]:
    return list(TEMPLATE_LIBRARY)


def grouped_templates() -> dict[str, list[TemplateDefinition]]:
    grouped: dict[str, list[TemplateDefinition]] = {}
    for template in TEMPLATE_LIBRARY:
        grouped.setdefault(template.category, []).append(template)
    return grouped


def clone_template_task(template: TemplateDefinition, new_task_name: str | None = None) -> dict[str, Any]:
    task = copy.deepcopy(template.task)
    if new_task_name:
        task["task_name"] = new_task_name
    return task


def template_detail_text(template: TemplateDefinition) -> str:
    task = template.task
    lines = [
        f"Template: {template.title}",
        f"Category: {template.category}",
        "",
        template.description,
        "",
        f"Mode: {task.get('mode', '--')}",
        f"Objective Type: {task.get('objective_type', '--')}",
        f"Algorithm: {task.get('algorithm', '--')}",
        f"Variables: {len(task.get('variables', []))}",
        f"Objectives: {len(task.get('objectives', []))}",
        "",
        "Dynamic Parameters:",
    ]
    if task.get("test_function"):
        lines.insert(7, f"Test Function: {task.get('test_function', '--')}")
    for row in task.get("algorithm_params", []):
        key = row.get("Parameter", "")
        value = row.get("Value", "")
        note = row.get("Description", "")
        lines.append(f"  - {key} = {value}  ({note})")
    return "\n".join(lines)
