from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Tuple

import numpy as np


# -----------------------------------------------------------------------------
# Small offline objective helpers used by the GUI.
# These are in-memory callables for the GUI-driven offline workflow.
# -----------------------------------------------------------------------------

def _sphere_vectorized(X: np.ndarray) -> np.ndarray:
    X = np.atleast_2d(np.asarray(X, dtype=float))
    return -np.sum(X**2, axis=1).reshape(-1, 1)


def _rosenbrock_vectorized(X: np.ndarray) -> np.ndarray:
    X = np.asarray(X, dtype=float)
    is_1d = X.ndim == 1
    X = np.atleast_2d(X)
    result = -np.sum(
        100.0 * (X[:, 1:] - X[:, :-1] ** 2.0) ** 2.0 + (1 - X[:, :-1]) ** 2.0,
        axis=1,
    )
    if is_1d:
        return result[0]
    return result.reshape(-1, 1)


def _ackley_vectorized(X: np.ndarray) -> np.ndarray:
    X = np.atleast_2d(np.asarray(X, dtype=float))
    dim = X.shape[1]
    sum_sq = np.sum(X**2, axis=1)
    cos_sum = np.sum(np.cos(2 * np.pi * X), axis=1)
    val = -(
        -20 * np.exp(-0.2 * np.sqrt(sum_sq / dim))
        - np.exp(cos_sum / dim)
        + 20
        + np.e
    )
    return val.reshape(-1, 1)


def _two_objective_tradeoff(X: np.ndarray) -> np.ndarray:
    """Simple smooth multi-objective test function for GUI use.

    The optimizer will maximize both outputs when `maximize=True` is passed in
    optimizer kwargs. The two objectives prefer different regions in parameter
    space, producing a non-trivial Pareto front.
    """
    X = np.atleast_2d(np.asarray(X, dtype=float))
    dim = X.shape[1]
    c1 = np.full(dim, 0.2, dtype=float)
    c2 = np.full(dim, 0.8, dtype=float)
    f1 = -np.sum((X - c1) ** 2, axis=1)
    f2 = -np.sum((X - c2) ** 2, axis=1)
    return np.column_stack([f1, f2])


SINGLE_OBJECTIVE_FUNCTIONS: dict[str, Callable[[np.ndarray], Any]] = {
    "sphere": _sphere_vectorized,
    "rosenbrock": _rosenbrock_vectorized,
    "ackley": _ackley_vectorized,
}

SUPPORTED_GUI_OPTIMIZERS = {
    "bo",
    "consbo",
    "turbo",
    "mggpo_so",
    "consmggpo_so",
    "mobo",
    "consmobo",
    "consmggpo",
    "mggpo",
    "mopso",
    "nsga2",
}

DEPRECATED_DYNAMIC_PARAM_KEYS = {
    "init_points",
    "kernel",
    "acquisition",
    "beta",
    "population_size",
    "maximize",
    "test_function",
}


class TaskService:
    """Collect, validate and translate GUI task data.

    Design notes
    ------------
    1. GUI task -> TaskConfig should support both offline test functions and
       future online EPICS tasks.
    2. GOTAcc runner currently validates optimizer/backend consistency from
       ``backend.kwargs['combine_mode']`` before backend construction. However,
       the current offline backend builder only consumes ``func`` / ``callable_path``
       and ``x0``. To bridge that inconsistency cleanly, this service provides:

       - build_task_config(...): full config for validation / optimizer logic
       - make_backend_build_ready_config(...): sanitized copy for build_backend()
    """

    # ------------------------------------------------------------------
    # Generic table helpers
    # ------------------------------------------------------------------
    @staticmethod
    def table_to_records(table) -> List[Dict[str, str]]:
        headers = []
        for col in range(table.columnCount()):
            item = table.horizontalHeaderItem(col)
            headers.append(item.text() if item is not None else f"col_{col}")

        records: List[Dict[str, str]] = []
        for row in range(table.rowCount()):
            record: Dict[str, str] = {}
            non_empty = False
            for col, header in enumerate(headers):
                widget = table.cellWidget(row, col)
                if widget is not None and hasattr(widget, "currentText"):
                    value = str(widget.currentText()).strip()
                else:
                    item = table.item(row, col)
                    value = item.text().strip() if item is not None else ""
                if value:
                    non_empty = True
                record[header] = value
            if non_empty:
                records.append(record)
        return records

    @staticmethod
    def _is_enabled(value: Any) -> bool:
        text = str(value).strip().lower()
        return text in {"y", "yes", "true", "1", "on", "enabled"}

    @staticmethod
    def _enabled_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [row for row in rows if TaskService._is_enabled(row.get("Enable", ""))]

    @staticmethod
    def _coerce_scalar(value: Any, declared_type: str | None = None) -> Any:
        if isinstance(value, (int, float, bool)) or value is None:
            return value

        text = str(value).strip()
        dtype = (declared_type or "").strip().lower()
        if not text:
            return text

        if dtype in {"json", "dict", "list"}:
            return json.loads(text)
        if dtype in {"bool", "boolean"}:
            return text.lower() in {"1", "true", "yes", "y", "on"}
        if dtype in {"int", "integer"}:
            return int(float(text))
        if dtype in {"float", "double"}:
            return float(text)

        lowered = text.lower()
        if lowered in {"true", "false"}:
            return lowered == "true"

        if text.startswith("{") or text.startswith("["):
            try:
                return json.loads(text)
            except Exception:
                pass

        try:
            if any(ch in text for ch in [".", "e", "E"]):
                return float(text)
            return int(text)
        except Exception:
            return text

    @staticmethod
    def _dynamic_params_to_dict(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for row in rows:
            key = str(row.get("Parameter", "")).strip()
            if key in {"qehvi_batch", "q_batch"}:
                key = "q_batch_size"
            if not key:
                continue
            val = row.get("Value", "")
            dtype = row.get("Type", "")
            coerced = TaskService._coerce_scalar(val, dtype)
            if key == "acq_opt_kwargs" and isinstance(coerced, Mapping) and "qehvi_batch" in coerced:
                migrated = dict(coerced)
                q_batch_value = migrated.pop("qehvi_batch")
                result.setdefault("q_batch_size", max(1, int(q_batch_value)))
                coerced = migrated
            result[key] = coerced
        return result

    @staticmethod
    def _deprecated_dynamic_params(rows: List[Dict[str, Any]]) -> list[str]:
        found: list[str] = []
        for row in rows:
            key = str(row.get("Parameter", "")).strip()
            if key in DEPRECATED_DYNAMIC_PARAM_KEYS and key not in found:
                found.append(key)
        return found

    @staticmethod
    def _coerce_torch_dtype(value: Any) -> Any:
        if value in {None, "", "none", "null"}:
            return None
        if not isinstance(value, str):
            return value
        try:
            import torch
        except Exception:
            return value

        name = value.strip().lower().replace("torch.", "")
        mapping = {
            "float16": torch.float16,
            "half": torch.float16,
            "float32": torch.float32,
            "float": torch.float32,
            "float64": torch.float64,
            "double": torch.float64,
            "bfloat16": torch.bfloat16,
        }
        return mapping.get(name, value)

    @staticmethod
    def _plain_data(value: Any) -> Any:
        if callable(value):
            module = getattr(value, "__module__", "")
            name = getattr(value, "__name__", repr(value))
            if module:
                return f"{module}:{name}"
            return name
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {str(k): TaskService._plain_data(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [TaskService._plain_data(v) for v in value]
        return value

    @staticmethod
    def _dump_serialized_payload(payload: Mapping[str, Any]) -> str:
        plain = TaskService._plain_data(dict(payload))
        try:
            import yaml
        except ImportError:
            return json.dumps(plain, indent=2, ensure_ascii=False)
        return yaml.safe_dump(
            plain,
            sort_keys=False,
            allow_unicode=True,
        )

    @staticmethod
    def _parse_json_text(text: str) -> Dict[str, Any]:
        text = str(text or "").strip()
        if not text:
            return {}
        try:
            obj = json.loads(text)
        except Exception as exc:
            raise ValueError(f"Failed to parse JSON kwargs text: {exc}") from exc
        if not isinstance(obj, dict):
            raise TypeError("Policy kwargs text must parse to a JSON object/dict.")
        return obj

    @staticmethod
    def _guess_offline_test_function(task: Dict[str, Any]) -> str:
        top_level_name = str(task.get("test_function", "")).strip().lower()
        if top_level_name in SINGLE_OBJECTIVE_FUNCTIONS:
            return top_level_name

        enabled_objectives = TaskService._enabled_rows(task.get("objectives", []))
        for row in enabled_objectives:
            name = str(row.get("Name", "")).strip().lower()
            if name in SINGLE_OBJECTIVE_FUNCTIONS:
                return name

        return "rosenbrock"

    @staticmethod
    def _direction_multiplier(direction: Any) -> float:
        text = str(direction or "").strip().lower()
        if text in {"min", "minimize", "minimization", "minimise"}:
            return -1.0
        return 1.0

    @staticmethod
    def _objective_direction_multipliers(objectives: List[Dict[str, Any]]) -> np.ndarray:
        if not objectives:
            return np.asarray([1.0], dtype=float)
        multipliers = [
            TaskService._direction_multiplier(row.get("Direction", "maximize"))
            for row in objectives
        ]
        return np.asarray(multipliers, dtype=float)

    @staticmethod
    def _apply_direction_multipliers(value: Any, multipliers: np.ndarray) -> Any:
        arr = np.asarray(value, dtype=float)
        multipliers = np.asarray(multipliers, dtype=float).reshape(-1)
        if multipliers.size == 0:
            multipliers = np.asarray([1.0], dtype=float)

        if arr.ndim == 0:
            return float(arr) * float(multipliers[0])
        if arr.ndim == 1:
            if arr.shape[0] == multipliers.size:
                return arr * multipliers
            return arr * float(multipliers[0])
        if arr.ndim == 2:
            if arr.shape[1] == multipliers.size:
                return arr * multipliers.reshape(1, -1)
            return arr * float(multipliers[0])
        return arr

    @staticmethod
    def _wrap_objective_with_directions(func: Callable[[np.ndarray], Any], multipliers: np.ndarray) -> Callable[[np.ndarray], Any]:
        def wrapped(X: np.ndarray) -> Any:
            return TaskService._apply_direction_multipliers(func(X), multipliers)

        return wrapped

    @staticmethod
    def _optimizer_name_from_gui(name: str) -> str:
        raw = str(name).strip().lower()
        mapping = {
            "bo": "bo",
            "bayesian optimization": "bo",
            "consbo": "consbo",
            "constrained bo": "consbo",
            "turbo": "turbo",
            "rcds": "rcds",
            "mggpo-so": "mggpo_so",
            "mggpo_so": "mggpo_so",
            "smggpo": "mggpo_so",
            "single objective mggpo": "mggpo_so",
            "single_objective_mggpo": "mggpo_so",
            "single objective mg-gpo": "mggpo_so",
            "single_objective_mg-gpo": "mggpo_so",
            "consmggpo-so": "consmggpo_so",
            "consmggpo_so": "consmggpo_so",
            "constrained mggpo so": "consmggpo_so",
            "constrained_mggpo_so": "consmggpo_so",
            "single objective consmggpo": "consmggpo_so",
            "single_objective_consmggpo": "consmggpo_so",
            "single objective constrained mggpo": "consmggpo_so",
            "single_objective_constrained_mggpo": "consmggpo_so",
            "single objective constrained mg-gpo": "consmggpo_so",
            "single_objective_constrained_mg-gpo": "consmggpo_so",
            "mobo": "mobo",
            "consmobo": "consmobo",
            "constrained mobo": "consmobo",
            "constrained multi objective bo": "consmobo",
            "constrained multi-objective bo": "consmobo",
            "consmggpo": "consmggpo",
            "constrained mggpo": "consmggpo",
            "constrained_mggpo": "consmggpo",
            "constrained mg-gpo": "consmggpo",
            "constrained_mg-gpo": "consmggpo",
            "mggpo": "mggpo",
            "mg-gpo": "mggpo",
            "mopso": "mopso",
            "nsga-ii": "nsga2",
            "nsga2": "nsga2",
        }
        return mapping.get(raw, raw)

    @staticmethod
    def _infer_combine_mode(task: Dict[str, Any], algorithm: str, n_objectives: int) -> str:
        objective_type = str(task.get("objective_type", "Single Objective")).strip().lower()
        if algorithm in {"mobo", "consmobo", "consmggpo", "mggpo", "mopso", "nsga2"}:
            return "vector"
        if algorithm in {"bo", "consbo", "turbo", "rcds", "mggpo_so", "consmggpo_so"}:
            return "weighted_sum"
        if objective_type == "multi objective":
            return "vector"
        if n_objectives > 1:
            return "vector"
        return "weighted_sum"

    @staticmethod
    def _build_optimizer_kwargs(task: Dict[str, Any], dyn: Dict[str, Any], n_objectives: int) -> Dict[str, Any]:
        algorithm = TaskService._optimizer_name_from_gui(task.get("algorithm", "BO"))
        max_evals = max(1, int(task.get("max_evaluations", 20)))
        seed = int(task.get("seed", 0))
        verbose = bool(dyn.get("verbose", True))
        device = str(dyn.get("device", "cpu") or "cpu")
        ref_point = dyn.get("ref_point", None)

        if algorithm in {"bo", "consbo", "turbo", "mobo", "consmobo"}:
            n_init = max(1, int(dyn.get("n_init", min(20, max_evals))))
            n_init = min(n_init, max_evals)
            iter_eval_cost = TaskService._bo_iteration_eval_cost(task, dyn, algorithm)
            # Keep the total BO-style budget aligned with the GUI max_evaluations setting,
            # including TuRBO trust-region parallelism and MOBO q-batch acquisitions.
            n_iter = max(0, (max_evals - n_init) // iter_eval_cost)
            kwargs: Dict[str, Any] = {
                "n_init": n_init,
                "n_iter": n_iter,
                "random_state": seed,
                "verbose": verbose,
                "device": device,
            }
        elif algorithm in {"mggpo", "consmggpo", "mggpo_so", "consmggpo_so"}:
            pop_size, evals_per_gen, n_generations = TaskService._mggpo_budget_params(task, dyn)
            kwargs = {
                "pop_size": pop_size,
                "evals_per_gen": evals_per_gen,
                "n_generations": n_generations,
                "random_state": seed,
                "verbose": verbose,
                "device": device,
            }
        elif algorithm in {"mopso", "nsga2"}:
            pop_size, n_generations = TaskService._population_budget_params(task, dyn)
            kwargs = {
                "pop_size": pop_size,
                "n_generations": n_generations,
                "random_state": seed,
                "verbose": verbose,
            }
        else:
            raise ValueError(
                f"Algorithm {task.get('algorithm', 'BO')!r} is not supported by the current GUI runner."
            )

        if "kernel_type" in dyn:
            kwargs["kernel_type"] = str(dyn["kernel_type"]).strip().lower()
        if "gp_restarts" in dyn:
            kwargs["gp_restarts"] = int(dyn["gp_restarts"])
        if "dtype" in dyn:
            dtype_value = TaskService._coerce_torch_dtype(dyn["dtype"])
            if dtype_value is not None:
                kwargs["dtype"] = dtype_value
        if "acq" in dyn:
            kwargs["acq"] = str(dyn["acq"]).strip().lower()
        elif algorithm == "consbo":
            kwargs["acq"] = "ei"
        elif algorithm == "consmobo":
            kwargs["acq"] = "qehvi"
        if "acq_mode" in dyn:
            kwargs["acq_mode"] = str(dyn["acq_mode"]).strip().lower()
        if "acq_optimizer" in dyn:
            kwargs["acq_optimizer"] = str(dyn["acq_optimizer"]).strip()
        if "acq_para" in dyn:
            kwargs["acq_para"] = float(dyn["acq_para"])
        if "acq_opt_kwargs" in dyn and isinstance(dyn["acq_opt_kwargs"], Mapping):
            kwargs["acq_opt_kwargs"] = dict(dyn["acq_opt_kwargs"])
            kwargs["acq_opt_kwargs"].pop("qehvi_batch", None)
        if "acq_para_kwargs" in dyn and isinstance(dyn["acq_para_kwargs"], Mapping):
            kwargs["acq_para_kwargs"] = dict(dyn["acq_para_kwargs"])
        if algorithm in {"mobo", "consmobo"}:
            kwargs.setdefault("acq_opt_kwargs", {})
            batch_size = TaskService._mobo_batch_eval_cost(task, dyn)
            if str(kwargs.get("acq", "ehvi")).strip().lower().startswith("q"):
                kwargs["acq_opt_kwargs"]["qehvi_batch"] = batch_size

        for key in [
            "n_trust_regions",
            "success_tolerance",
            "failure_tolerance",
            "length_init",
            "length_min",
        ]:
            if key in dyn:
                kwargs[key] = dyn[key]

        if algorithm in {"mobo", "consmobo", "consmggpo", "mggpo", "mopso", "nsga2"}:
            kwargs["n_objectives"] = int(dyn.get("n_objectives", n_objectives))
            kwargs["maximize"] = True
            if ref_point is not None:
                kwargs["ref_point"] = np.asarray(ref_point, dtype=float)
            if algorithm in {"mggpo", "consmggpo", "mopso", "nsga2"}:
                for key in ["mutation_eta", "crossover_eta"]:
                    if key in dyn:
                        kwargs[key] = dyn[key]
                if algorithm in {"mggpo", "consmggpo"}:
                    for key in [
                        "m1",
                        "m2",
                        "m3",
                        "ucb_beta",
                        "ucb_beta_kwargs",
                        "use_all_history_for_gp",
                        "gp_history_max",
                        "w",
                        "c1",
                        "c2",
                        "mutation_prob",
                        "crossover_prob",
                    ]:
                        if key in dyn and dyn[key] != "":
                            kwargs[key] = dyn[key]
                if algorithm == "mopso":
                    for key in ["w", "c1", "c2", "mutation_prob", "archive_size"]:
                        if key in dyn:
                            kwargs[key] = dyn[key]
                if algorithm == "nsga2":
                    for key in ["crossover_prob", "mutation_prob"]:
                        if key in dyn:
                            kwargs[key] = dyn[key]

        if algorithm in {"mggpo_so", "consmggpo_so"}:
            kwargs["maximize"] = True
            for key in ["mutation_eta", "crossover_eta"]:
                if key in dyn:
                    kwargs[key] = dyn[key]
            for key in [
                "m1",
                "m2",
                "m3",
                "ucb_beta",
                "ucb_beta_kwargs",
                "use_all_history_for_gp",
                "gp_history_max",
                "w",
                "c1",
                "c2",
                "mutation_prob",
                "crossover_prob",
                "archive_size",
            ]:
                if key in dyn and dyn[key] != "":
                    kwargs[key] = dyn[key]

        return kwargs

    @staticmethod
    def _constraint_bounds_from_rows(constraints: List[Dict[str, Any]]) -> list[tuple[float | None, float | None]]:
        bounds: list[tuple[float | None, float | None]] = []
        for idx, row in enumerate(constraints, start=1):
            lower_text = str(row.get("Lower", "")).strip()
            upper_text = str(row.get("Upper", "")).strip()
            if not lower_text and not upper_text:
                raise ValueError(f"Constraint row {idx} must define at least one of Lower or Upper.")
            lower = float(lower_text) if lower_text else None
            upper = float(upper_text) if upper_text else None
            if lower is not None and upper is not None and lower > upper:
                raise ValueError(f"Constraint row {idx} must satisfy Lower <= Upper.")
            bounds.append((lower, upper))
        return bounds

    @staticmethod
    def _bo_iteration_eval_cost(task: Dict[str, Any], dyn: Dict[str, Any], algorithm: str) -> int:
        if algorithm == "turbo":
            return max(1, int(dyn.get("n_trust_regions", 1)))
        if algorithm == "consmobo" and "acq" not in dyn:
            dyn = {**dyn, "acq": "qehvi"}
        if algorithm in {"mobo", "consmobo"}:
            return TaskService._mobo_batch_eval_cost(task, dyn)
        return 1

    @staticmethod
    def _mobo_batch_eval_cost(task: Dict[str, Any], dyn: Dict[str, Any]) -> int:
        acq = str(dyn.get("acq", "ehvi")).strip().lower()
        if not acq.startswith("q"):
            return 1
        return max(1, int(dyn.get("q_batch_size", 1)))

    @staticmethod
    def _population_budget_params(task: Dict[str, Any], dyn: Dict[str, Any]) -> Tuple[int, int]:
        max_evals = max(1, int(task.get("max_evaluations", 20)))
        requested_pop = dyn.get("pop_size")
        if requested_pop is None:
            pop_size = min(24, max(2, max_evals // 2))
        else:
            pop_size = int(requested_pop)
        pop_size = max(2, min(pop_size, max_evals))

        max_generations = max(0, (max_evals - pop_size) // pop_size)
        requested_generations = dyn.get("n_generations")
        if requested_generations is None:
            n_generations = max_generations
        else:
            n_generations = max(0, min(int(requested_generations), max_generations))
        return pop_size, n_generations

    @staticmethod
    def _mggpo_budget_params(task: Dict[str, Any], dyn: Dict[str, Any]) -> Tuple[int, int, int]:
        max_evals = max(1, int(task.get("max_evaluations", 20)))
        requested_pop = dyn.get("pop_size")
        if requested_pop in {None, ""}:
            pop_size = min(12, max(2, max_evals // 2))
        else:
            pop_size = int(requested_pop)
        pop_size = max(2, min(pop_size, max_evals))

        remaining = max(0, max_evals - pop_size)
        requested_evals_per_gen = dyn.get("evals_per_gen")
        if requested_evals_per_gen in {None, ""}:
            evals_per_gen = min(pop_size, max(1, remaining)) if remaining else 1
        else:
            evals_per_gen = int(requested_evals_per_gen)
        evals_per_gen = max(1, min(evals_per_gen, pop_size))

        max_generations = max(0, remaining // evals_per_gen)
        requested_generations = dyn.get("n_generations")
        if requested_generations in {None, ""}:
            n_generations = max_generations
        else:
            n_generations = max(0, min(int(requested_generations), max_generations))
        return pop_size, evals_per_gen, n_generations

    # ------------------------------------------------------------------
    # GUI collection / validation / preview
    # ------------------------------------------------------------------
    @staticmethod
    def collect_task_data(task_ui, machine_ui) -> Dict[str, Any]:
        task_name = task_ui.lineEdit_taskName.text().strip() or "untitled_task"
        workdir = task_ui.lineEdit_workdir.text().strip() or str(Path.cwd())
        mode_text = task_ui.comboBox_mode.currentText()
        test_function = (
            task_ui.comboBox_testFunction.currentText().strip().lower() or "rosenbrock"
            if mode_text == "Offline"
            else ""
        )

        task: Dict[str, Any] = {
            "task_name": task_name,
            "mode": mode_text,
            "objective_type": task_ui.comboBox_objectiveType.currentText(),
            "algorithm": task_ui.comboBox_algorithm.currentText(),
            "test_function": test_function,
            "seed": task_ui.spinBox_seed.value(),
            "max_evaluations": task_ui.spinBox_maxEval.value(),
            "workdir": workdir,
            "description": "",
            "variables": TaskService.table_to_records(task_ui.tableWidget_variables),
            "objectives": TaskService.table_to_records(task_ui.tableWidget_objectives),
            "constraints": TaskService.table_to_records(task_ui.tableWidget_constraints),
            "algorithm_params": TaskService.table_to_records(task_ui.tableWidget_dynamicParams),
            "machine": {
                "ca_address": machine_ui.lineEdit_caAddress.text().strip(),
                "confirm_before_write": machine_ui.checkBox_confirm.isChecked(),
                "restore_on_abort": machine_ui.checkBox_restore.isChecked(),
                "readback_check": machine_ui.checkBox_readbackCheck.isChecked(),
                "readback_tol": machine_ui.doubleSpinBox_readbackTol.value(),
                "set_interval": machine_ui.doubleSpinBox_setInterval.value(),
                "sample_interval": machine_ui.doubleSpinBox_sampleInterval.value(),
                "write_timeout": machine_ui.doubleSpinBox_timeout.value(),
                "write_policy": machine_ui.comboBox_policy.currentText(),
                "objective_policies": TaskService.table_to_records(machine_ui.tableWidget_objectivePolicies),
                "constraint_policies": TaskService.table_to_records(machine_ui.tableWidget_constraintPolicies),
                "mapping": TaskService.table_to_records(machine_ui.tableWidget_mapping),
                "write_links": TaskService.table_to_records(machine_ui.tableWidget_writeLinks),
            },
        }
        return task

    @staticmethod
    def _resolve_online_knob_pvs(task: Dict[str, Any], variables: List[Dict[str, Any]]) -> List[str]:
        mapping_rows = task.get("machine", {}).get("mapping", []) or []
        by_name: Dict[str, str] = {}
        for row in mapping_rows:
            role = str(row.get("Role", "")).strip().lower()
            pv = str(row.get("PV Name", "")).strip()
            name = str(row.get("Name", "")).strip()
            if role == "knob" and pv:
                if name:
                    by_name[name] = pv

        result: List[str] = []
        for i, row in enumerate(variables):
            name = str(row.get("Name", f"x{i}")).strip()
            if name in by_name:
                result.append(by_name[name])
                continue
            raise ValueError(
                f"Cannot resolve EPICS knob PV for variable row {i + 1} ({name!r}). "
                "Add a matching 'knob' row in Machine Setup -> PV Mapping."
            )
        return result

    @staticmethod
    def _resolve_online_knob_readback_pvs(task: Dict[str, Any], variables: List[Dict[str, Any]]) -> List[str]:
        mapping_rows = task.get("machine", {}).get("mapping", []) or []
        by_name: Dict[str, str] = {}
        for row in mapping_rows:
            role = str(row.get("Role", "")).strip().lower()
            pv = str(row.get("PV Name", "")).strip()
            readback = str(row.get("Readback", "")).strip()
            name = str(row.get("Name", "")).strip()
            if role == "knob" and pv:
                if name:
                    by_name[name] = readback or pv

        result: List[str] = []
        for i, row in enumerate(variables):
            name = str(row.get("Name", f"x{i}")).strip()
            if name in by_name:
                result.append(by_name[name])
                continue
            raise ValueError(
                f"Cannot resolve EPICS knob readback PV for variable row {i + 1} ({name!r}). "
                "Add a matching 'knob' row with Readback in Machine Setup -> PV Mapping."
            )
        return result

    @staticmethod
    def _resolve_online_objective_pvs(task: Dict[str, Any], objectives: List[Dict[str, Any]]) -> List[str]:
        mapping_rows = task.get("machine", {}).get("mapping", []) or []
        by_name: Dict[str, str] = {}
        for row in mapping_rows:
            role = str(row.get("Role", "")).strip().lower()
            pv = str(row.get("PV Name", "")).strip()
            name = str(row.get("Name", "")).strip()
            if role == "objective" and pv:
                if name:
                    by_name[name] = pv

        result: List[str] = []
        for i, row in enumerate(objectives):
            name = str(row.get("Name", f"obj{i}")).strip()
            if name in by_name:
                result.append(by_name[name])
                continue
            raise ValueError(
                f"Cannot resolve EPICS objective PV for objective row {i + 1} ({name!r}). "
                "Add a matching 'objective' row in Machine Setup -> PV Mapping."
            )
        return result

    @staticmethod
    def _resolve_online_constraint_pvs(task: Dict[str, Any], constraints: List[Dict[str, Any]]) -> List[str]:
        mapping_rows = task.get("machine", {}).get("mapping", []) or []
        by_name: Dict[str, str] = {}
        for row in mapping_rows:
            role = str(row.get("Role", "")).strip().lower()
            pv = str(row.get("PV Name", "")).strip()
            readback = str(row.get("Readback", "")).strip()
            name = str(row.get("Name", "")).strip()
            if role == "constraint" and pv and name:
                by_name[name] = readback or pv

        result: List[str] = []
        for i, row in enumerate(constraints):
            name = str(row.get("Name", f"cons{i}")).strip()
            if name in by_name:
                result.append(by_name[name])
                continue
            raise ValueError(
                f"Cannot resolve EPICS constraint PV for constraint row {i + 1} ({name!r}). "
                "Add a matching 'constraint' row in Machine Setup -> PV Mapping."
            )
        return result

    @staticmethod
    def _build_write_policy_kwargs(task: Dict[str, Any], variable_names: List[str]) -> Dict[str, Any]:
        machine = task.get("machine", {}) or {}
        rows = machine.get("write_links", []) or []
        pvlinks: List[tuple[int, str]] = []
        for row in rows:
            enabled_text = row.get("Enabled", "")
            if enabled_text and not TaskService._is_enabled(enabled_text):
                continue
            src_text = str(row.get("Source Index", "")).strip()
            tgt_pv = str(row.get("Target PV", "")).strip()
            if not tgt_pv:
                continue
            if src_text == "":
                continue
            try:
                src_idx = int(src_text)
            except ValueError:
                if src_text in variable_names:
                    src_idx = variable_names.index(src_text)
                else:
                    raise ValueError(f"Invalid write link source: {src_text!r}")
            pvlinks.append((src_idx, tgt_pv))
        return {"pvlinks": pvlinks}

    @staticmethod
    def _build_objective_policy_specs(task: Dict[str, Any]) -> List[Dict[str, Any]]:
        machine = task.get("machine", {}) or {}
        rows = machine.get("objective_policies", []) or []
        specs: List[Dict[str, Any]] = []
        supported = {"fel_energy_guard", "zero_guard"}
        for idx, row in enumerate(rows, start=1):
            enabled_text = row.get("Enabled", "")
            if enabled_text and not TaskService._is_enabled(enabled_text):
                continue
            name = str(row.get("Policy Name", "")).strip().lower()
            if not name:
                continue
            if name not in supported:
                raise ValueError(
                    f"Unsupported objective policy in row {idx}: {name!r}. "
                    f"Use one of: {', '.join(sorted(supported))}."
                )
            kwargs = TaskService._parse_json_text(row.get("Kwargs JSON", ""))
            target_text = kwargs.get("target_col", 0)
            try:
                target_col = int(float(target_text or 0))
            except Exception as exc:
                raise ValueError(
                    f"Objective policy row {idx} has invalid target_col in Kwargs JSON."
                ) from exc
            if target_col < 0:
                raise ValueError(
                    f"Objective policy row {idx} must have target_col >= 0 in Kwargs JSON."
                )
            kwargs["target_col"] = target_col
            specs.append({"name": name, "kwargs": kwargs})
        return specs

    @staticmethod
    def _build_constraint_policy_specs(task: Dict[str, Any]) -> List[Dict[str, Any]]:
        machine = task.get("machine", {}) or {}
        rows = machine.get("constraint_policies", []) or []
        specs: List[Dict[str, Any]] = []
        supported = {"bpm_guard", "bpm_zero_guard"}
        for idx, row in enumerate(rows, start=1):
            enabled_text = row.get("Enabled", "")
            if enabled_text and not TaskService._is_enabled(enabled_text):
                continue
            name = str(row.get("Policy Name", "")).strip().lower()
            if not name:
                continue
            if name not in supported:
                raise ValueError(
                    f"Unsupported constraint policy in row {idx}: {name!r}. "
                    f"Use one of: {', '.join(sorted(supported))}."
                )

            kwargs = TaskService._parse_json_text(row.get("Kwargs JSON", ""))
            target_text = kwargs.get("target_col", 0)
            try:
                target_col = int(float(target_text or 0))
            except Exception as exc:
                raise ValueError(
                    f"Constraint policy row {idx} has invalid target_col in Kwargs JSON."
                ) from exc
            if target_col < 0:
                raise ValueError(
                    f"Constraint policy row {idx} must have target_col >= 0 in Kwargs JSON."
                )
            kwargs["target_col"] = target_col

            for key in ("zero_atol", "delta_ratio", "delta_min", "scale_floor"):
                if key not in kwargs:
                    continue
                try:
                    kwargs[key] = float(kwargs[key])
                except Exception as exc:
                    raise ValueError(
                        f"Constraint policy row {idx} has invalid {key!r} in Kwargs JSON."
                    ) from exc
                if kwargs[key] < 0:
                    raise ValueError(
                        f"Constraint policy row {idx} must have {key} >= 0 in Kwargs JSON."
                    )

            normalized_name = "bpm_guard" if name == "bpm_zero_guard" else name
            specs.append({"name": normalized_name, "kwargs": kwargs})
        return specs

    @staticmethod
    def validate_task_data(task: Dict[str, Any]) -> Tuple[bool, List[str]]:
        errors: List[str] = []

        if not task.get("task_name"):
            errors.append("Task name is empty.")

        mode = str(task.get("mode", "")).strip()
        if mode not in {"Online EPICS", "Offline"}:
            errors.append(f"Unsupported mode {mode!r}.")

        objective_type = str(task.get("objective_type", "")).strip()
        if objective_type not in {"Single Objective", "Multi Objective"}:
            errors.append(f"Unsupported objective type {objective_type!r}.")

        workdir = str(task.get("workdir", "")).strip()
        if not workdir:
            errors.append("Working directory is empty.")

        variables = task.get("variables", [])
        enabled_variables = TaskService._enabled_rows(variables)
        if not enabled_variables:
            errors.append("At least one enabled variable is required.")

        objectives = task.get("objectives", [])
        enabled_objectives = TaskService._enabled_rows(objectives)
        if not enabled_objectives:
            errors.append("At least one enabled objective is required.")

        deprecated_keys = TaskService._deprecated_dynamic_params(task.get("algorithm_params", []))
        if deprecated_keys:
            errors.append(
                "Unsupported legacy dynamic parameter(s): " + ", ".join(deprecated_keys)
            )

        algorithm = TaskService._optimizer_name_from_gui(task.get("algorithm", "BO"))
        if algorithm not in SUPPORTED_GUI_OPTIMIZERS:
            errors.append(
                f"Algorithm {task.get('algorithm', 'BO')!r} is not wired into the current GUI runner."
            )
        if algorithm == "mggpo_so" and objective_type != "Single Objective":
            errors.append("MGGPO-SO requires Objective Type = Single Objective.")
        if algorithm in {"mggpo", "consmggpo", "mggpo_so", "consmggpo_so", "mopso", "nsga2"} and int(task.get("max_evaluations", 0) or 0) < 2:
            errors.append(
                f"Algorithm {task.get('algorithm', 'BO')!r} requires max_evaluations >= 2 for population initialization."
            )
        enabled_constraints = TaskService._enabled_rows(task.get("constraints", []))
        constrained_algorithms = {"consbo", "consmobo", "consmggpo", "consmggpo_so"}
        if algorithm in constrained_algorithms:
            if mode != "Online EPICS":
                errors.append(f"{task.get('algorithm', 'Constrained BO')} is currently wired for Online EPICS tasks in the GUI.")
            if algorithm == "consbo" and objective_type != "Single Objective":
                errors.append("ConsBO requires Objective Type = Single Objective.")
            if algorithm in {"consmobo", "consmggpo"} and objective_type != "Multi Objective":
                errors.append(f"{task.get('algorithm', 'Constrained MO optimizer')} requires Objective Type = Multi Objective.")
            if algorithm in {"consmobo", "consmggpo"} and len(enabled_objectives) < 2:
                errors.append(f"{task.get('algorithm', 'Constrained MO optimizer')} requires at least two enabled objectives.")
            if algorithm == "consmggpo_so" and objective_type != "Single Objective":
                errors.append("ConsMGGPO-SO requires Objective Type = Single Objective.")
            if not enabled_constraints:
                errors.append(f"{task.get('algorithm', 'Constrained BO')} requires at least one enabled output constraint.")
            dyn = TaskService._dynamic_params_to_dict(task.get("algorithm_params", []))
            default_acq = "qehvi" if algorithm == "consmobo" else "ei"
            acq = str(dyn.get("acq", default_acq)).strip().lower()
            if algorithm == "consbo" and acq != "ei":
                errors.append("ConsBO currently requires Algorithm Detail acq='ei'.")
            if algorithm == "consmobo" and acq not in {"qehvi", "qnehvi"}:
                errors.append("ConsMOBO currently requires Algorithm Detail acq='qehvi' or acq='qnehvi'.")
            try:
                TaskService._constraint_bounds_from_rows(enabled_constraints)
            except Exception as exc:
                errors.append(str(exc))

        for idx, row in enumerate(enabled_variables, start=1):
            try:
                lower = float(row.get("Lower", ""))
                upper = float(row.get("Upper", ""))
                initial = float(row.get("Initial", ""))
            except ValueError:
                errors.append(f"Variable row {idx} has non-numeric lower/upper/initial values.")
                continue
            if lower >= upper:
                errors.append(f"Variable row {idx} must satisfy Lower < Upper.")
            if not (lower <= initial <= upper):
                errors.append(f"Variable row {idx} initial value is out of bounds.")

        if mode.lower() == "online epics":
            try:
                TaskService._resolve_online_knob_pvs(task, enabled_variables)
            except Exception as exc:
                errors.append(str(exc))
            try:
                TaskService._resolve_online_objective_pvs(task, enabled_objectives)
            except Exception as exc:
                errors.append(str(exc))
            if algorithm in constrained_algorithms:
                try:
                    TaskService._resolve_online_constraint_pvs(task, enabled_constraints)
                except Exception as exc:
                    errors.append(str(exc))

            sample_values: list[int] = []
            for idx, row in enumerate(enabled_objectives, start=1):
                raw = row.get("Samples", "1")
                try:
                    value = int(float(raw or 1))
                except Exception:
                    errors.append(f"Objective row {idx} has invalid Samples value.")
                    continue
                if value < 1:
                    errors.append(f"Objective row {idx} must have Samples >= 1.")
                    continue
                sample_values.append(value)

            if sample_values and len(set(sample_values)) > 1:
                errors.append(
                    "Online EPICS backend currently requires a single shared Samples value across all enabled objectives."
                )

            write_policy = str(task.get("machine", {}).get("write_policy", "none")).strip().lower()
            if write_policy not in {"none", "equal"}:
                errors.append(
                    f"Unsupported write policy for current GUI flow: {write_policy!r}"
                )

            try:
                TaskService._build_objective_policy_specs(task)
            except Exception as exc:
                errors.append(str(exc))
            if algorithm in constrained_algorithms:
                try:
                    TaskService._build_constraint_policy_specs(task)
                except Exception as exc:
                    errors.append(str(exc))
            for idx, row in enumerate(enabled_objectives, start=1):
                math_op = str(row.get("Math", "mean")).strip().lower() or "mean"
                if math_op not in {"mean", "std"}:
                    errors.append(
                        f"Objective row {idx} has unsupported Math value {math_op!r}. "
                        "Use mean or std."
                    )
            for idx, row in enumerate(enabled_constraints, start=1):
                math_op = str(row.get("Math", "mean")).strip().lower() or "mean"
                if math_op not in {"mean", "std"}:
                    errors.append(
                        f"Constraint row {idx} has unsupported Math value {math_op!r}. "
                        "Use mean or std."
                    )
        elif objective_type == "Single Objective":
            test_function = str(task.get("test_function", "")).strip().lower() or "rosenbrock"
            if test_function not in SINGLE_OBJECTIVE_FUNCTIONS:
                errors.append(
                    "Offline single-objective tasks require test_function to be one of: "
                    + ", ".join(sorted(SINGLE_OBJECTIVE_FUNCTIONS))
                )

        return len(errors) == 0, errors

    @staticmethod
    def to_preview_text(task: Dict[str, Any]) -> str:
        try:
            task_cfg = TaskService.build_task_config(task)
        except Exception as exc:
            raw = json.dumps(task, indent=2, ensure_ascii=False)
            return f"# TaskConfig build error\n# {exc}\n\n{raw}"
        return TaskService._dump_serialized_payload(task_cfg.to_dict())

    @staticmethod
    def export_task_json(task: Dict[str, Any], filepath: str | Path) -> None:
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(task, f, indent=2, ensure_ascii=False)

    @staticmethod
    def export_task_config(task: Dict[str, Any], filepath: str | Path) -> None:
        task_cfg = TaskService.build_task_config(task)
        TaskService.ensure_runtime_directories(task_cfg)
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        serialized = TaskService._dump_serialized_payload(task_cfg.to_dict())
        with open(path, "w", encoding="utf-8") as f:
            f.write(serialized)

    @staticmethod
    def extract_machine_pvs(task: Dict[str, Any]) -> list[dict[str, str]]:
        mode = str(task.get("mode", "")).strip().lower()
        if mode != "online epics":
            return []

        rows: list[dict[str, str]] = []
        mapping_rows = task.get("machine", {}).get("mapping", []) or []
        for row in mapping_rows:
            pvname = str(row.get("PV Name", "")).strip()
            if not pvname:
                continue
            rows.append(
                {
                    "role": str(row.get("Role", "")).strip() or "unknown",
                    "name": str(row.get("Name", "")).strip() or pvname,
                    "pvname": pvname,
                }
            )

        if rows:
            return rows

        enabled_variables = TaskService._enabled_rows(task.get("variables", []))
        enabled_objectives = TaskService._enabled_rows(task.get("objectives", []))

        try:
            knob_pvs = TaskService._resolve_online_knob_pvs(task, enabled_variables)
            rows.extend(
                {
                    "role": "knob",
                    "name": str(row.get("Name", f"x{i}")).strip() or f"x{i}",
                    "pvname": knob_pvs[i],
                }
                for i, row in enumerate(enabled_variables)
            )
        except Exception:
            pass

        try:
            obj_pvs = TaskService._resolve_online_objective_pvs(task, enabled_objectives)
            rows.extend(
                {
                    "role": "objective",
                    "name": str(row.get("Name", f"obj{i}")).strip() or f"obj{i}",
                    "pvname": obj_pvs[i],
                }
                for i, row in enumerate(enabled_objectives)
            )
        except Exception:
            pass

        return rows

    # ------------------------------------------------------------------
    # GOTAcc TaskConfig conversion
    # ------------------------------------------------------------------
    @staticmethod
    def _build_offline_backend_kwargs(
        task: Dict[str, Any],
        variables: List[Dict[str, Any]],
        objectives: List[Dict[str, Any]],
        dyn: Dict[str, Any],
        algorithm: str,
        x0: List[float],
    ) -> tuple[Dict[str, Any], int]:
        objective_type = str(task.get("objective_type", "Single Objective")).strip().lower()
        direction_multipliers = TaskService._objective_direction_multipliers(objectives)
        variable_names = [
            (str(row.get("Name", "")).strip() or f"x{i}") for i, row in enumerate(variables)
        ]

        if algorithm in {"mobo", "mopso", "nsga2"} or objective_type == "multi objective":
            n_objectives = max(2, len(objectives))
            func = TaskService._wrap_objective_with_directions(_two_objective_tradeoff, direction_multipliers)
        else:
            func_name = TaskService._guess_offline_test_function(task)
            func = TaskService._wrap_objective_with_directions(
                SINGLE_OBJECTIVE_FUNCTIONS[func_name],
                direction_multipliers[:1],
            )
            n_objectives = 1

        combine_mode = TaskService._infer_combine_mode(task, algorithm, n_objectives)
        kwargs = {
            "func": func,
            "x0": np.asarray(x0, dtype=float),
            "combine_mode": combine_mode,
            "variable_names": variable_names,
        }
        return kwargs, n_objectives

    @staticmethod
    def _build_online_backend_kwargs(
        task: Dict[str, Any],
        variables: List[Dict[str, Any]],
        objectives: List[Dict[str, Any]],
        dyn: Dict[str, Any],
        algorithm: str,
    ) -> tuple[Dict[str, Any], int]:
        machine = task.get("machine", {}) or {}
        variable_names = [
            (str(row.get("Name", "")).strip() or f"x{i}") for i, row in enumerate(variables)
        ]
        objective_names = [
            (str(row.get("Name", "")).strip() or f"obj{i}") for i, row in enumerate(objectives)
        ]
        constraints = TaskService._enabled_rows(task.get("constraints", []))
        use_output_constraints = algorithm in {"consbo", "consmobo", "consmggpo", "consmggpo_so"}
        constraint_names = [
            (str(row.get("Name", "")).strip() or f"cons{i}") for i, row in enumerate(constraints)
        ] if use_output_constraints else []
        knobs_pvnames = TaskService._resolve_online_knob_pvs(task, variables)
        knob_readback_pvnames = TaskService._resolve_online_knob_readback_pvs(task, variables)
        obj_pvnames = TaskService._resolve_online_objective_pvs(task, objectives)
        constraint_pvnames = (
            TaskService._resolve_online_constraint_pvs(task, constraints)
            if use_output_constraints
            else []
        )
        constraint_bounds = (
            TaskService._constraint_bounds_from_rows(constraints)
            if use_output_constraints
            else []
        )

        n_objectives = max(1, len(objectives))
        combine_mode = TaskService._infer_combine_mode(task, algorithm, n_objectives)
        direction_multipliers = TaskService._objective_direction_multipliers(objectives)
        obj_weights = [
            float(row.get("Weight", 1.0) or 1.0) * float(direction_multipliers[i])
            for i, row in enumerate(objectives)
        ]
        sample_values = [
            int(float(row.get("Samples", 1) or 1))
            for row in objectives
        ]
        if not sample_values:
            obj_samples = 1
        elif len(set(sample_values)) == 1:
            obj_samples = sample_values[0]
        else:
            raise ValueError(
                "Online EPICS backend currently requires identical Samples values across enabled objectives."
            )
        obj_math = [
            str(row.get("Math", "mean")).strip().lower() or "mean"
            for row in objectives
        ]
        constraint_math = [
            str(row.get("Math", "mean")).strip().lower() or "mean"
            for row in constraints
        ] if use_output_constraints else []

        write_policy_name = str(machine.get("write_policy", "none")).strip().lower()
        objective_policy_specs = TaskService._build_objective_policy_specs(task)
        constraint_policy_specs = (
            TaskService._build_constraint_policy_specs(task)
            if use_output_constraints
            else []
        )
        readback_check = bool(machine.get("readback_check", False))
        readback_tol = machine.get("readback_tol", None)

        kwargs: Dict[str, Any] = {
            "knobs_pvnames": knobs_pvnames,
            "knob_readback_pvnames": knob_readback_pvnames,
            "obj_pvnames": obj_pvnames,
            "obj_weights": obj_weights,
            "obj_samples": obj_samples,
            "obj_math": obj_math,
            "constraint_pvnames": constraint_pvnames,
            "constraint_math": constraint_math,
            "constraint_bounds": constraint_bounds,
            "set_interval": float(machine.get("set_interval", 1.0)),
            "sample_interval": float(machine.get("sample_interval", 0.2)),
            "log_path": str(Path(task.get("workdir", Path.cwd())) / "save" / f"{task.get('task_name', 'task')}.opt"),
            "readback_check": readback_check,
            "readback_tol": readback_tol,
            "combine_mode": combine_mode,
            "best_selector_mode": dyn.get("best_selector_mode", None),
            "write_policy": write_policy_name,
            "write_policy_kwargs": TaskService._build_write_policy_kwargs(task, variable_names),
            "objective_policies": objective_policy_specs,
            "constraint_policies": constraint_policy_specs,
            # GUI-side helper metadata; harmless for preview/use outside builder,
            # but must be removed before build_backend() if strict factory is used.
            "variable_names": variable_names,
            "objective_names": objective_names,
            "constraint_names": constraint_names,
        }
        return kwargs, n_objectives

    @staticmethod
    def build_task_config(task: Dict[str, Any]):
        """Translate GUI task dict to GOTAcc TaskConfig.

        Supports
        --------
        - offline benchmark/test-function tasks
        - online EPICS tasks (config generation)

        Note
        ----
        The returned config intentionally keeps GUI helper metadata such as
        ``combine_mode`` and ``variable_names`` in backend.kwargs. This is useful
        for runner-side validation and GUI-side display. Before strict backend
        construction, call ``make_backend_build_ready_config()``.
        """
        from gotacc.configs.schema import BackendConfig, MetaConfig, OptimizerConfig, RuntimeConfig, TaskConfig

        deprecated_keys = TaskService._deprecated_dynamic_params(task.get("algorithm_params", []))
        if deprecated_keys:
            raise ValueError(
                "Unsupported legacy dynamic parameter(s): " + ", ".join(deprecated_keys)
            )

        mode_text = str(task.get("mode", "Offline")).strip()
        if mode_text not in {"Online EPICS", "Offline"}:
            raise ValueError(f"Unsupported mode {mode_text!r}.")

        objective_type_text = str(task.get("objective_type", "Single Objective")).strip()
        if objective_type_text not in {"Single Objective", "Multi Objective"}:
            raise ValueError(f"Unsupported objective type {objective_type_text!r}.")

        mode = mode_text.lower()
        variables = TaskService._enabled_rows(task.get("variables", []))
        if not variables:
            raise ValueError("No enabled variables found.")

        objectives = TaskService._enabled_rows(task.get("objectives", []))
        if not objectives:
            raise ValueError("No enabled objectives found.")

        bounds: List[List[float]] = []
        x0: List[float] = []
        for row in variables:
            lo = float(row.get("Lower", 0.0))
            hi = float(row.get("Upper", 1.0))
            x_init = float(row.get("Initial", 0.0))
            bounds.append([lo, hi])
            x0.append(x_init)

        dyn = TaskService._dynamic_params_to_dict(task.get("algorithm_params", []))
        algorithm = TaskService._optimizer_name_from_gui(task.get("algorithm", "BO"))
        if algorithm in {"mggpo", "consmggpo", "mggpo_so", "consmggpo_so", "mopso", "nsga2"} and int(task.get("max_evaluations", 0) or 0) < 2:
            raise ValueError(f"Algorithm {task.get('algorithm', 'BO')!r} requires max_evaluations >= 2 for population initialization.")
        constrained_algorithms = {"consbo", "consmobo", "consmggpo", "consmggpo_so"}
        if algorithm in constrained_algorithms and mode != "online epics":
            raise ValueError(f"{task.get('algorithm', 'Constrained BO')} is currently wired for Online EPICS tasks in the GUI.")
        if algorithm == "mggpo_so" and objective_type_text != "Single Objective":
            raise ValueError("MGGPO-SO requires Objective Type = Single Objective.")
        if algorithm == "consbo" and objective_type_text != "Single Objective":
            raise ValueError("ConsBO requires Objective Type = Single Objective.")
        if algorithm in {"consmobo", "consmggpo"} and objective_type_text != "Multi Objective":
            raise ValueError(f"{task.get('algorithm', 'Constrained MO optimizer')} requires Objective Type = Multi Objective.")
        if algorithm in {"consmobo", "consmggpo"} and len(objectives) < 2:
            raise ValueError(f"{task.get('algorithm', 'Constrained MO optimizer')} requires at least two enabled objectives.")
        if algorithm == "consmggpo_so" and objective_type_text != "Single Objective":
            raise ValueError("ConsMGGPO-SO requires Objective Type = Single Objective.")
        if algorithm in constrained_algorithms and not TaskService._enabled_rows(task.get("constraints", [])):
            raise ValueError(f"{task.get('algorithm', 'Constrained BO')} requires at least one enabled output constraint.")

        if mode == "online epics":
            backend_type = "epics"
            backend_kwargs, n_objectives = TaskService._build_online_backend_kwargs(
                task=task,
                variables=variables,
                objectives=objectives,
                dyn=dyn,
                algorithm=algorithm,
            )
            machine_label = str(task.get("machine", {}).get("ca_address", "")).strip() or "epics-machine"
        else:
            backend_type = "offline"
            backend_kwargs, n_objectives = TaskService._build_offline_backend_kwargs(
                task=task,
                variables=variables,
                objectives=objectives,
                dyn=dyn,
                algorithm=algorithm,
                x0=x0,
            )
            machine_label = "offline-test-function"

        optimizer_kwargs = TaskService._build_optimizer_kwargs(task, dyn, n_objectives=n_objectives)
        if algorithm in constrained_algorithms:
            optimizer_kwargs["constraint_bounds"] = TaskService._constraint_bounds_from_rows(
                TaskService._enabled_rows(task.get("constraints", []))
            )

        workdir = Path(task.get("workdir", Path.cwd()))
        save_dir = workdir / "save"

        runtime = RuntimeConfig(
            save_history=True,
            history_path=str(save_dir / f"{task.get('task_name', 'task')}_history.dat"),
            plot_convergence=False,
            plot_path=str(save_dir / f"{task.get('task_name', 'task')}_plot.png"),
            set_best=False,
            restore_initial_on_error=True,
            restore_initial_on_keyboard_interrupt=True,
            verbose=True,
        )

        cfg = TaskConfig(
            meta=MetaConfig(
                name=str(task.get("task_name", "untitled_task")),
                machine=machine_label,
                description=str(task.get("description", "")),
                tags=["gui", backend_type],
            ),
            backend=BackendConfig(
                type=backend_type,
                bounds=bounds,
                bounds_mode="absolute",
                kwargs=backend_kwargs,
            ),
            optimizer=OptimizerConfig(
                name=algorithm,
                kwargs=optimizer_kwargs,
            ),
            runtime=runtime,
        )
        return cfg

    @staticmethod
    def ensure_runtime_directories(task_cfg) -> None:
        """Create directories needed by a real run or explicit export."""
        for path_text in (task_cfg.runtime.history_path, task_cfg.runtime.plot_path):
            if path_text:
                Path(path_text).parent.mkdir(parents=True, exist_ok=True)

        log_path = task_cfg.backend.kwargs.get("log_path")
        if log_path:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def make_backend_build_ready_config(task_cfg):
        """Return a sanitized copy that can be passed to build_backend().

        Why this helper exists
        ----------------------
        GOTAcc currently validates optimizer/backend consistency using
        ``backend.kwargs['combine_mode']`` before backend construction, while the
        current offline backend builder rejects any kwargs other than
        ``func``/``callable_path`` and ``x0``/``initial_point``. This helper keeps
        validation happy on the original config, then strips GUI/helper-only
        fields on a copy before strict backend creation.
        """
        cfg = copy.deepcopy(task_cfg)
        kwargs = dict(cfg.backend.kwargs)
        if str(cfg.backend.type).lower() == "offline":
            kwargs.pop("combine_mode", None)
            kwargs.pop("variable_names", None)
            kwargs.pop("objective_names", None)
        elif str(cfg.backend.type).lower() == "epics":
            # GUI helper metadata should not be consumed by the strict EPICS factory.
            kwargs.pop("variable_names", None)
            kwargs.pop("objective_names", None)
            kwargs.pop("constraint_names", None)
        cfg.backend.kwargs = kwargs
        return cfg
