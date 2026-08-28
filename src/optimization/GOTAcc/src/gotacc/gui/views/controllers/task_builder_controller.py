from __future__ import annotations

import ast
import copy
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:  # pragma: no cover
    from ..main_window import MainWindow

try:
    from ...services.task_service import TaskService
    from ..algorithm_ui_specs import parameter_ui_spec
    from ..tool_dialogs import AlgorithmDetailDialog
    from ..tool_dialogs import BoundsToolsDialog
except ImportError:  # pragma: no cover - local script fallback
    CURRENT_DIR = Path(__file__).resolve().parent
    GUI_ROOT = CURRENT_DIR.parents[1]
    for path in (GUI_ROOT, GUI_ROOT / "services", GUI_ROOT / "views"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    from task_service import TaskService
    from algorithm_ui_specs import parameter_ui_spec
    from tool_dialogs import AlgorithmDetailDialog
    from tool_dialogs import BoundsToolsDialog


CURRENT_DIR = Path(__file__).resolve().parent
GOTACC_ROOT = CURRENT_DIR.parents[2]
REPO_ROOT = CURRENT_DIR.parents[4]
CONFIG_ROOT = REPO_ROOT / "config"
GUI_PROJECT_DIR = CONFIG_ROOT / "gui_projects"
TASK_CONFIG_YAML_DIR = CONFIG_ROOT / "task_configs" / "yaml"

ALGORITHM_INIT_SOURCES = {
    "BO": (GOTACC_ROOT / "algorithms" / "single_objective" / "bo.py", "BOOptimizer"),
    "ConsBO": (GOTACC_ROOT / "algorithms" / "single_objective" / "consbo.py", "ConsBOOptimizer"),
    "TuRBO": (GOTACC_ROOT / "algorithms" / "single_objective" / "turbo.py", "TuRBOOptimizer"),
    "RCDS": (GOTACC_ROOT / "algorithms" / "single_objective" / "rcds.py", "RCDSOptimizer"),
    "MGGPO-SO": (GOTACC_ROOT / "algorithms" / "single_objective" / "mggpo_so.py", "MGGPOSOOptimizer"),
    "ConsMGGPO-SO": (GOTACC_ROOT / "algorithms" / "single_objective" / "consmggpo_so.py", "ConsMGGPOSOOptimizer"),
    "MOBO": (GOTACC_ROOT / "algorithms" / "multi_objective" / "mobo.py", "MOBOOptimizer"),
    "ConsMOBO": (GOTACC_ROOT / "algorithms" / "multi_objective" / "consmobo.py", "ConsMOBOOptimizer"),
    "ConsMGGPO": (GOTACC_ROOT / "algorithms" / "multi_objective" / "consmggpo.py", "MultiObjectiveMGGPO"),
    "MGGPO": (GOTACC_ROOT / "algorithms" / "multi_objective" / "mggpo.py", "MGGPOOptimizer"),
    "MOPSO": (GOTACC_ROOT / "algorithms" / "multi_objective" / "mopso.py", "MOPSOOptimizer"),
    "NSGA-II": (GOTACC_ROOT / "algorithms" / "multi_objective" / "nsga2.py", "NSGA2Optimizer"),
}

EXCLUDED_INIT_PARAMS = {
    "BO": {"self", "func", "bounds", "random_state", "n_iter"},
    "ConsBO": {"self", "func", "bounds", "random_state", "n_iter", "constraint_bounds"},
    "TuRBO": {"self", "func", "bounds", "random_state", "n_iter"},
    "RCDS": {"self", "func", "bounds", "vrange", "x0", "Dmat0", "random_state", "maxEval"},
    "MGGPO-SO": {"self", "func", "bounds", "random_state", "n_objectives", "n_constraints", "maximize", "ref_point"},
    "ConsMGGPO-SO": {"self", "func", "bounds", "random_state", "n_objectives", "constraint_bounds", "maximize", "ref_point"},
    "MOBO": {"self", "func", "bounds", "random_state", "n_objectives", "n_iter", "maximize"},
    "ConsMOBO": {"self", "func", "bounds", "random_state", "n_objectives", "n_iter", "maximize", "constraint_bounds"},
    "ConsMGGPO": {"self", "func", "bounds", "random_state", "n_objectives", "maximize", "constraint_bounds"},
    "MGGPO": {"self", "func", "bounds", "random_state", "n_objectives", "n_constraints", "maximize"},
    "MOPSO": {"self", "func", "bounds", "random_state", "n_objectives", "maximize"},
    "NSGA-II": {"self", "func", "bounds", "random_state", "n_objectives", "maximize"},
}

PARAM_NOTES = {
    "kernel_type": "Kernel type from optimizer __init__.",
    "gp_restarts": "GP fitting retry count.",
    "acq": "Acquisition function name.",
    "acq_para": "Acquisition tuning parameter.",
    "acq_para_kwargs": "Additional acquisition parameter kwargs as JSON.",
    "acq_optimizer": "Acquisition optimizer name.",
    "acq_opt_kwargs": "Acquisition optimizer kwargs as JSON.",
    "q_batch_size": "q-acquisition batch size. Only used by MOBO/ConsMOBO when acquisition starts with q.",
    "acq_mode": "MGGPO acquisition mode: ucb, ehvi or combine.",
    "n_init": "Number of evaluations collected before model-guided optimization begins. Total evaluations are still capped by Max Evaluations.",
    "device": "Torch device name, for example cpu or cuda.",
    "dtype": "Torch dtype name, for example float64.",
    "n_trust_regions": "TuRBO trust-region count.",
    "success_tolerance": "TuRBO consecutive success threshold.",
    "failure_tolerance": "TuRBO consecutive failure threshold.",
    "length_init": "Initial trust-region length.",
    "length_min": "Minimum trust-region length before restart.",
    "step": "RCDS initial normalized line-search step.",
    "noise": "RCDS objective noise tolerance used during bracket search.",
    "tol": "RCDS relative convergence tolerance.",
    "maxIt": "RCDS maximum direction-set iterations.",
    "maximize": "Maximize the objective when true; minimize when false.",
    "ref_point": "Reference point as JSON array.",
    "pop_size": "Population size.",
    "n_generations": "Generation count. Budget is still clipped by Max Evaluations.",
    "evals_per_gen": "MGGPO true evaluations selected from each generated offspring pool.",
    "m1": "MGGPO mutation offspring count.",
    "m2": "MGGPO crossover offspring count.",
    "m3": "MGGPO PSO-assisted offspring count.",
    "ucb_beta": "MGGPO UCB exploration coefficient.",
    "ucb_beta_kwargs": "MGGPO beta schedule kwargs as JSON.",
    "use_all_history_for_gp": "Use all evaluated points for GP fitting instead of recent/history-filtered data.",
    "gp_history_max": "Maximum recent history rows used for GP fitting. Leave blank for all available rows.",
    "w": "PSO inertia weight.",
    "c1": "PSO cognitive coefficient.",
    "c2": "PSO social coefficient.",
    "mutation_prob": "Optional mutation probability. Leave blank to use the optimizer default.",
    "archive_size": "Archive size.",
    "crossover_prob": "Crossover probability.",
    "crossover_eta": "SBX crossover eta.",
    "mutation_eta": "Polynomial mutation eta.",
    "verbose": "Print optimizer progress logs.",
}

_NO_DEFAULT_OVERRIDE = object()

ALGORITHM_PARAM_DEFAULT_OVERRIDES = {
    "BO": {
        "acq_para_kwargs": {"beta_strategy": "inv_decay", "beta_lam": 0.01},
        "acq_opt_kwargs": {"num_restarts": 8, "raw_samples": 256, "n_candidates": 8192},
    },
    "ConsBO": {
        "acq": "ei",
        "acq_para_kwargs": {"beta_strategy": "inv_decay", "beta_lam": 0.01},
        "acq_opt_kwargs": {"num_restarts": 8, "raw_samples": 256, "n_candidates": 8192},
    },
    "TuRBO": {
        "acq_opt_kwargs": {"num_restarts": 8, "raw_samples": 512, "n_candidates": 8192},
    },
    "RCDS": {
        "step": 0.2,
        "maxIt": 20,
        "tol": 1e-6,
        "maximize": True,
    },
    "MGGPO-SO": {
        "pop_size": 50,
        "evals_per_gen": 50,
        "n_generations": 2,
        "acq_mode": "ucb",
        "ucb_beta_kwargs": {"beta_strategy": "scale_decay", "beta_lam": 0.85},
        "gp_history_max": 100,
        "mutation_prob": 0.5,
        "c1": 2.0,
        "c2": 2.0,
    },
    "ConsMGGPO-SO": {
        "pop_size": 50,
        "evals_per_gen": 50,
        "n_generations": 2,
        "acq_mode": "ucb",
        "ucb_beta_kwargs": {"beta_strategy": "scale_decay", "beta_lam": 0.85},
        "gp_history_max": 100,
        "mutation_prob": 0.5,
        "c1": 2.0,
        "c2": 2.0,
    },
    "MOBO": {
        "acq_opt_kwargs": {"num_restarts": 8, "raw_samples": 256},
        "ref_point": [0.0, 0.0],
    },
    "ConsMOBO": {
        "acq": "qehvi",
        "acq_opt_kwargs": {"num_restarts": 8, "raw_samples": 256},
        "ref_point": [0.0, 0.0],
    },
    "ConsMGGPO": {
        "pop_size": 50,
        "evals_per_gen": 50,
        "n_generations": 2,
        "acq_mode": "ucb",
        "ref_point": [0.0, 0.0],
        "ucb_beta_kwargs": {"beta_strategy": "scale_decay", "beta_lam": 0.85},
        "gp_history_max": 100,
        "mutation_prob": 0.5,
        "c1": 3.0,
        "c2": 3.0,
    },
    "MGGPO": {
        "pop_size": 50,
        "evals_per_gen": 50,
        "n_generations": 2,
        "acq_mode": "ucb",
        "ref_point": [0.0, 0.0],
        "ucb_beta_kwargs": {"beta_strategy": "scale_decay", "beta_lam": 0.85},
        "gp_history_max": 100,
        "mutation_prob": 0.5,
        "c1": 3.0,
        "c2": 3.0,
    },
    "MOPSO": {
        "ref_point": [0.0, 0.0],
    },
    "NSGA-II": {
        "ref_point": [0.0, 0.0],
    },
}

OBJECTIVE_MATH_OPTIONS = ("mean", "std")

SINGLE_OBJECTIVE_ALGORITHMS = ("BO", "ConsBO", "TuRBO", "RCDS", "MGGPO-SO", "ConsMGGPO-SO")
MULTI_OBJECTIVE_ALGORITHMS = ("MOBO", "ConsMOBO", "MGGPO", "ConsMGGPO", "MOPSO", "NSGA-II")
Q_BATCH_PARAM_NAME = "q_batch_size"
LEGACY_Q_BATCH_PARAM_NAMES = {"qehvi_batch", "q_batch"}
ALGORITHM_OBJECTIVE_TYPE = {
    **{name: "Single Objective" for name in SINGLE_OBJECTIVE_ALGORITHMS},
    **{name: "Multi Objective" for name in MULTI_OBJECTIVE_ALGORITHMS},
}


def _safe_eval_ast(node: ast.AST):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple, ast.Dict, ast.Set)):
        return ast.literal_eval(node)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        operand = _safe_eval_ast(node.operand)
        return +operand if isinstance(node.op, ast.UAdd) else -operand
    if isinstance(node, ast.BinOp):
        left = _safe_eval_ast(node.left)
        right = _safe_eval_ast(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.FloorDiv):
            return left // right
        if isinstance(node.op, ast.Mod):
            return left % right
        if isinstance(node.op, ast.Pow):
            return left**right
    raise ValueError(f"Unsupported default AST node: {type(node).__name__}")


def _annotation_to_dtype(name: str, annotation_text: str, default):
    ann = (annotation_text or "").replace(" ", "").lower()
    if name == "dtype":
        return "str"
    if any(token in ann for token in ("dict", "mapping", "ndarray", "list", "tuple")):
        return "json"
    if "bool" in ann:
        return "bool"
    if "int" in ann:
        return "int"
    if "float" in ann:
        return "float"
    if "str" in ann:
        return "str"

    if isinstance(default, bool):
        return "bool"
    if isinstance(default, int) and not isinstance(default, bool):
        return "int"
    if isinstance(default, float):
        return "float"
    if isinstance(default, (dict, list, tuple)):
        return "json"
    return "str"


class TaskBuilderController:
    def __init__(self, window: "MainWindow") -> None:
        self.window = window
        self.view = window.view_adapter
        self._algorithm_param_widgets: dict[str, tuple[QWidget, str, str]] = {}
        self._syncing_algorithm_param_form = False
        self._algorithm_param_specs_cache: dict[str, list[tuple[str, str, str, str]]] = {}
        self._algorithm_overrides_expanded = False
        self._algorithm_detail_dialog: AlgorithmDetailDialog | None = None
        self._syncing_objective_algorithm = False
        self._last_bounds_preview = ""
        self._bounds_preview_plan: list[dict[str, object]] = []
        self._bounds_dialog: BoundsToolsDialog | None = None
        self._bounds_tool_state: dict[str, object] = {
            "source": "Current machine readback",
            "mode": "± absolute delta",
            "selected_only": False,
            "update_initial": True,
            "primary": 0.1,
            "secondary": 1.0,
        }

    def algorithm_template_key(self, algorithm_text: str) -> str:
        text = (algorithm_text or "").strip()
        lowered = text.lower()
        if lowered == "turbo":
            return "TuRBO"
        if lowered == "consbo":
            return "ConsBO"
        if lowered == "rcds":
            return "RCDS"
        if lowered in {
            "mggpo-so",
            "mggpo_so",
            "smggpo",
            "single objective mggpo",
            "single_objective_mggpo",
            "single objective mg-gpo",
            "single_objective_mg-gpo",
        }:
            return "MGGPO-SO"
        if lowered in {
            "consmggpo-so",
            "consmggpo_so",
            "constrained mggpo so",
            "constrained_mggpo_so",
            "single objective consmggpo",
            "single_objective_consmggpo",
            "single objective constrained mggpo",
            "single_objective_constrained_mggpo",
            "single objective constrained mg-gpo",
            "single_objective_constrained_mg-gpo",
        }:
            return "ConsMGGPO-SO"
        if lowered == "mobo":
            return "MOBO"
        if lowered == "consmobo":
            return "ConsMOBO"
        if lowered in {"consmggpo", "constrained mggpo", "constrained_mggpo", "constrained mg-gpo", "constrained_mg-gpo"}:
            return "ConsMGGPO"
        if lowered in {"mggpo", "mg-gpo"}:
            return "MGGPO"
        if lowered == "bo":
            return "BO"
        return text

    def objective_type_for_algorithm(self, algorithm_text: str) -> str:
        algorithm = self.algorithm_template_key(algorithm_text)
        return ALGORITHM_OBJECTIVE_TYPE.get(algorithm, "Single Objective")

    def algorithms_for_objective_type(self, objective_type_text: str) -> tuple[str, ...]:
        text = str(objective_type_text or "").strip().lower()
        if text == "multi objective":
            return MULTI_OBJECTIVE_ALGORITHMS
        return SINGLE_OBJECTIVE_ALGORITHMS

    def sync_algorithm_options_with_objective_type(
        self,
        *,
        preferred_algorithm: str | None = None,
        update_params: bool = True,
    ) -> str:
        objective_type = self.window.task_ui.comboBox_objectiveType.currentText()
        allowed = self.algorithms_for_objective_type(objective_type)
        combo = self.window.task_ui.comboBox_algorithm
        previous = self.algorithm_template_key(preferred_algorithm or combo.currentText())
        selected = previous if previous in allowed else allowed[0]

        old_state = combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItems(list(allowed))
            combo.setCurrentText(selected)
        finally:
            combo.blockSignals(old_state)

        if update_params and previous != selected and not self.window._suppress_autofill:
            self.apply_recommended_dynamic_params(selected, preserve_custom=False, log_change=True)
            self.update_algorithm_guidance()
        return selected

    def dynamic_table_records(self):
        return self._parameter_table_records(self.window.task_ui.tableWidget_dynamicParams)

    def _parameter_table_records(self, table) -> list[list[str]]:
        records = []
        for row in range(table.rowCount()):
            values = []
            has_content = False
            for col in range(table.columnCount()):
                item = table.item(row, col)
                value = item.text().strip() if item is not None else ""
                values.append(value)
                has_content = has_content or bool(value)
            if has_content:
                records.append(values)
        return records

    @staticmethod
    def _canonical_param_name(name: str) -> str:
        normalized = str(name or "").strip()
        if normalized in LEGACY_Q_BATCH_PARAM_NAMES:
            return Q_BATCH_PARAM_NAME
        return normalized

    def _recommended_param_specs(self, algorithm_text: str, records: list[list[str]] | None = None):
        algorithm_key = self.algorithm_template_key(algorithm_text)
        specs = list(self._load_algorithm_param_specs(algorithm_key))
        if self._should_show_q_batch_param(algorithm_key, records):
            specs.append(
                (
                    Q_BATCH_PARAM_NAME,
                    "1",
                    "int",
                    PARAM_NOTES[Q_BATCH_PARAM_NAME],
                )
            )
        return algorithm_key, specs

    def _algorithm_param_spec_lookup(self, algorithm_key: str) -> dict[str, tuple[str, str, str]]:
        return {
            name: (default, dtype, note)
            for name, default, dtype, note in self._load_algorithm_param_specs(algorithm_key)
        }

    def _current_acquisition_name(self, algorithm_key: str, records: list[list[str]] | None = None) -> str:
        default_acq_by_algorithm = {
            "BO": "ucb",
            "ConsBO": "ei",
            "MOBO": "ehvi",
            "ConsMOBO": "qehvi",
        }
        if records is None:
            lookup = self._dynamic_param_lookup()
        else:
            lookup: dict[str, tuple[str, str, str]] = {}
            for record in records:
                key = self._canonical_param_name(record[0] if len(record) > 0 else "")
                if not key:
                    continue
                value = str(record[1] if len(record) > 1 else "").strip()
                dtype = str(record[2] if len(record) > 2 else "").strip() or "str"
                note = str(record[3] if len(record) > 3 else "").strip()
                lookup[key] = (value, dtype, note)
        if "acq" in lookup:
            return str(lookup["acq"][0]).strip().lower()

        spec_lookup = self._algorithm_param_spec_lookup(algorithm_key)
        if "acq" in spec_lookup:
            default_value = spec_lookup["acq"][0]
            if str(default_value).strip():
                return str(default_value).strip().lower()
        return default_acq_by_algorithm.get(algorithm_key, "")

    def _should_show_q_batch_param(self, algorithm_key: str, records: list[list[str]] | None = None) -> bool:
        return algorithm_key in {"MOBO", "ConsMOBO"}

    def _load_algorithm_param_specs(self, algorithm_key: str) -> list[tuple[str, str, str, str]]:
        if algorithm_key in self._algorithm_param_specs_cache:
            return self._algorithm_param_specs_cache[algorithm_key]

        source_info = ALGORITHM_INIT_SOURCES.get(algorithm_key)
        if source_info is None:
            self._algorithm_param_specs_cache[algorithm_key] = []
            return []

        path, class_name = source_info
        specs: list[tuple[str, str, str, str]] = []
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except Exception:
            self._algorithm_param_specs_cache[algorithm_key] = specs
            return specs

        class_node = next(
            (
                node
                for node in tree.body
                if isinstance(node, ast.ClassDef) and node.name == class_name
            ),
            None,
        )
        if class_node is None:
            self._algorithm_param_specs_cache[algorithm_key] = specs
            return specs

        init_node = next(
            (
                node
                for node in class_node.body
                if isinstance(node, ast.FunctionDef) and node.name == "__init__"
            ),
            None,
        )
        if init_node is None:
            self._algorithm_param_specs_cache[algorithm_key] = specs
            return specs

        excluded = EXCLUDED_INIT_PARAMS.get(algorithm_key, set())
        args = list(init_node.args.args)
        defaults = [None] * (len(args) - len(init_node.args.defaults)) + list(init_node.args.defaults)
        for arg_node, default_node in zip(args, defaults):
            name = arg_node.arg
            if name in excluded:
                continue

            annotation_text = ast.unparse(arg_node.annotation) if arg_node.annotation is not None else ""
            if default_node is None:
                default_value = ""
            else:
                try:
                    default_value = _safe_eval_ast(default_node)
                except Exception:
                    default_value = ast.unparse(default_node)

            dtype = _annotation_to_dtype(name, annotation_text, default_value)
            override_value = self._algorithm_param_default_override(algorithm_key, name)
            if override_value is not _NO_DEFAULT_OVERRIDE:
                default_value = override_value
            elif default_value is None:
                structured_default = self._structured_none_default(name, annotation_text, dtype)
                if structured_default is not None:
                    default_value = structured_default
            default_text = self._format_dynamic_param_value(default_value, dtype, name=name)
            note = PARAM_NOTES.get(name, f"{class_name}.__init__ parameter.")
            specs.append((name, default_text, dtype, note))

        self._algorithm_param_specs_cache[algorithm_key] = specs
        return specs

    @staticmethod
    def _format_param_label(name: str) -> str:
        if str(name or "").strip() == Q_BATCH_PARAM_NAME:
            return "q-Batch Size"
        return str(name).replace("_", " ").strip().title()

    @staticmethod
    def _structured_none_default(name: str, annotation_text: str = "", dtype: str = ""):
        normalized_name = str(name or "").strip().lower()
        ann = (annotation_text or "").replace(" ", "").lower()
        normalized_dtype = str(dtype or "").strip().lower()
        if normalized_dtype not in {"json", "dict", "list"}:
            return None
        if normalized_name.endswith("_kwargs") or "dict" in ann or "mapping" in ann:
            return {}
        if normalized_name in {"ref_point", "ref_points"} or any(
            token in ann for token in ("ndarray", "list", "tuple", "sequence")
        ):
            return []
        return None

    @staticmethod
    def _algorithm_param_default_override(algorithm_key: str, name: str):
        if str(name or "").strip() == "n_init" and str(algorithm_key or "").strip() in {
            "BO",
            "ConsBO",
            "TuRBO",
            "MOBO",
            "ConsMOBO",
        }:
            return 20
        return ALGORITHM_PARAM_DEFAULT_OVERRIDES.get(str(algorithm_key or "").strip(), {}).get(
            str(name or "").strip(),
            _NO_DEFAULT_OVERRIDE,
        )

    @staticmethod
    def _format_dynamic_param_value(value, dtype: str, name: str = "") -> str:
        normalized_dtype = str(dtype or "").strip().lower()
        if value is None:
            if normalized_dtype in {"json", "dict", "list"}:
                structured_default = TaskBuilderController._structured_none_default(name, "", dtype)
                if structured_default is not None:
                    return json.dumps(structured_default, ensure_ascii=False)
            return ""
        if normalized_dtype in {"bool", "boolean"}:
            return "true" if bool(value) else "false"
        if normalized_dtype in {"int", "integer"}:
            return str(int(value))
        if normalized_dtype in {"float", "double"}:
            return format(float(value), "g")
        if normalized_dtype in {"json", "dict", "list"}:
            if isinstance(value, str):
                return value
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    def _normalize_algorithm_param_records(
        self,
        algorithm_text: str,
        records: list[list[str]],
        *,
        legacy_task_batch_size: int | None = None,
    ) -> list[list[str]]:
        algorithm_key = self.algorithm_template_key(algorithm_text)
        spec_lookup = self._algorithm_param_spec_lookup(algorithm_key)
        normalized_rows: dict[str, list[str]] = {}
        migrated_q_batch_value: str | None = None
        had_explicit_q_batch = False

        for record in records:
            name = self._canonical_param_name(record[0] if len(record) > 0 else "")
            if not name:
                continue
            value = str(record[1] if len(record) > 1 else "").strip()
            dtype = str(record[2] if len(record) > 2 else "").strip()
            note = str(record[3] if len(record) > 3 else "").strip()

            if name == "acq_opt_kwargs":
                parsed_value = TaskService._coerce_scalar(value, dtype or "json")
                if isinstance(parsed_value, dict) and "qehvi_batch" in parsed_value:
                    if migrated_q_batch_value is None:
                        migrated_q_batch_value = self._format_dynamic_param_value(
                            parsed_value.pop("qehvi_batch"),
                            "int",
                            name=Q_BATCH_PARAM_NAME,
                        )
                    value = self._format_dynamic_param_value(parsed_value, "json", name=name)
                dtype = dtype or "json"
                note = note or PARAM_NOTES.get(name, "")
            elif name == Q_BATCH_PARAM_NAME:
                had_explicit_q_batch = True
                dtype = dtype or "int"
                note = note or PARAM_NOTES[Q_BATCH_PARAM_NAME]
            elif name in spec_lookup:
                _, spec_dtype, spec_note = spec_lookup[name]
                dtype = dtype or spec_dtype
                note = note or spec_note

            normalized_rows[name] = [name, value, dtype or "str", note]

        if legacy_task_batch_size is not None and legacy_task_batch_size > 0 and migrated_q_batch_value is None:
            migrated_q_batch_value = str(int(legacy_task_batch_size))

        if self._should_show_q_batch_param(algorithm_key, list(normalized_rows.values())):
            row = normalized_rows.get(Q_BATCH_PARAM_NAME)
            if row is None:
                normalized_rows[Q_BATCH_PARAM_NAME] = [
                    Q_BATCH_PARAM_NAME,
                    migrated_q_batch_value or "1",
                    "int",
                    PARAM_NOTES[Q_BATCH_PARAM_NAME],
                ]
            elif migrated_q_batch_value is not None and not had_explicit_q_batch:
                row[1] = migrated_q_batch_value
                row[2] = "int"
                row[3] = PARAM_NOTES[Q_BATCH_PARAM_NAME]
            elif not str(row[1]).strip():
                row[1] = migrated_q_batch_value or "1"
                row[2] = "int"
                row[3] = PARAM_NOTES[Q_BATCH_PARAM_NAME]
        else:
            normalized_rows.pop(Q_BATCH_PARAM_NAME, None)

        ordered_specs = self._recommended_param_specs(algorithm_text, list(normalized_rows.values()))[1]
        ordered_names = [name for name, *_rest in ordered_specs]
        return [
            normalized_rows[name]
            for name in ordered_names
            if name in normalized_rows
        ]

    @staticmethod
    def _clear_layout(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            child = item.widget()
            nested = item.layout()
            if child is not None:
                child.deleteLater()
            elif nested is not None:
                TaskBuilderController._clear_layout(nested)

    def _dynamic_param_lookup(self) -> dict[str, tuple[str, str, str]]:
        lookup: dict[str, tuple[str, str, str]] = {}
        for record in self.dynamic_table_records():
            key = self._canonical_param_name(record[0] if len(record) > 0 else "")
            if not key:
                continue
            value = str(record[1] if len(record) > 1 else "").strip()
            dtype = str(record[2] if len(record) > 2 else "").strip() or "str"
            note = str(record[3] if len(record) > 3 else "").strip()
            lookup[key] = (value, dtype, note)
        return lookup

    def _upsert_dynamic_param_row(self, name: str, value_text: str, dtype: str, note: str) -> None:
        table = self.window.task_ui.tableWidget_dynamicParams
        canonical_name = self._canonical_param_name(name)
        target_row = None
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            current_name = self._canonical_param_name(item.text() if item is not None else "")
            if current_name == canonical_name:
                target_row = row
                break
        old_state = table.blockSignals(True)
        try:
            if target_row is None:
                target_row = table.rowCount()
                table.insertRow(target_row)
            values = [canonical_name, value_text, dtype, note]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if col != 1:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                table.setItem(target_row, col, item)
        finally:
            table.blockSignals(old_state)

    def _sync_algorithm_param_state(
        self,
        algorithm_text: str,
        *,
        records: list[list[str]] | None = None,
        legacy_task_batch_size: int | None = None,
        refresh_preview: bool = True,
    ) -> None:
        normalized_records = self._normalize_algorithm_param_records(
            algorithm_text,
            self.dynamic_table_records() if records is None else records,
            legacy_task_batch_size=legacy_task_batch_size,
        )
        self._populate_dynamic_param_table(normalized_records)
        self.render_algorithm_param_form(algorithm_text)
        self._update_algorithm_detail_summary(algorithm_text)
        if refresh_preview:
            self.refresh_task_preview()

    def _populate_parameter_table(self, table, records: list[list[str]]) -> None:
        old_state = table.blockSignals(True)
        try:
            table.setRowCount(0)
            for row_index, record in enumerate(records):
                table.insertRow(row_index)
                for col, value in enumerate(record):
                    item = QTableWidgetItem(str(value))
                    item.setToolTip(str(value))
                    if col != 1:
                        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    table.setItem(row_index, col, item)
        finally:
            table.blockSignals(old_state)

    def _populate_dynamic_param_table(self, records: list[list[str]]) -> None:
        table = self.window.task_ui.tableWidget_dynamicParams
        self._populate_parameter_table(table, records)

    def _param_widget_value(self, widget: QWidget, dtype: str) -> str:
        if isinstance(widget, QCheckBox):
            return "true" if widget.isChecked() else "false"
        if isinstance(widget, QSpinBox):
            return str(widget.value())
        if isinstance(widget, QDoubleSpinBox):
            return format(widget.value(), "g")
        if isinstance(widget, QLineEdit):
            return widget.text().strip()
        return ""

    def _on_algorithm_param_widget_changed(self, name: str) -> None:
        if self._syncing_algorithm_param_form:
            return
        widget_meta = self._algorithm_param_widgets.get(name)
        if widget_meta is None:
            return
        widget, dtype, note = widget_meta
        self._upsert_dynamic_param_row(name, self._param_widget_value(widget, dtype), dtype, note)
        self._sync_algorithm_param_state(self.window.task_ui.comboBox_algorithm.currentText())

    def render_algorithm_param_form(self, algorithm_text: str) -> None:
        form_layout = getattr(self.window.task_ui, "formLayout_algorithmParams", None)
        summary_label = getattr(self.window.task_ui, "label_algorithmParamSummary", None)
        algorithm_key, specs = self._recommended_param_specs(algorithm_text, self.dynamic_table_records())
        if summary_label is not None:
            summary_label.setText(f"{algorithm_key} Parameters · {len(specs)} field(s)")
        if form_layout is None or summary_label is None:
            self._algorithm_param_widgets = {}
            return

        current_lookup = self._dynamic_param_lookup()
        self._syncing_algorithm_param_form = True
        try:
            self._clear_layout(form_layout)
            self._algorithm_param_widgets = {}

            for name, default, dtype, note in specs:
                value_text, _dtype, _note = current_lookup.get(name, (str(default), dtype, note))
                parsed_value = TaskService._coerce_scalar(value_text, dtype)
                label = QLabel(self._format_param_label(name), self.window.task_ui.groupBox_dynamicParams)
                label.setToolTip(note)

                normalized_dtype = str(dtype).strip().lower()
                if (parsed_value == "" or parsed_value is None) and normalized_dtype in {
                    "bool",
                    "boolean",
                    "int",
                    "integer",
                    "float",
                    "double",
                }:
                    widget = QLineEdit(self.window.task_ui.groupBox_dynamicParams)
                    widget.setText("" if parsed_value in {"", None} else str(parsed_value))
                    widget.editingFinished.connect(lambda param=name: self._on_algorithm_param_widget_changed(param))
                elif normalized_dtype in {"bool", "boolean"}:
                    widget: QWidget = QCheckBox(self.window.task_ui.groupBox_dynamicParams)
                    widget.setChecked(bool(parsed_value))
                    widget.toggled.connect(lambda *_args, param=name: self._on_algorithm_param_widget_changed(param))
                elif normalized_dtype in {"int", "integer"}:
                    widget = QSpinBox(self.window.task_ui.groupBox_dynamicParams)
                    widget.setMaximum(999999999)
                    widget.setValue(int(parsed_value))
                    widget.valueChanged.connect(lambda *_args, param=name: self._on_algorithm_param_widget_changed(param))
                elif normalized_dtype in {"float", "double"}:
                    widget = QDoubleSpinBox(self.window.task_ui.groupBox_dynamicParams)
                    widget.setDecimals(8)
                    widget.setMaximum(999999999.0)
                    widget.setValue(float(parsed_value))
                    widget.valueChanged.connect(lambda *_args, param=name: self._on_algorithm_param_widget_changed(param))
                else:
                    widget = QLineEdit(self.window.task_ui.groupBox_dynamicParams)
                    widget.setText(self._format_dynamic_param_value(parsed_value, dtype, name=name))
                    widget.editingFinished.connect(lambda param=name: self._on_algorithm_param_widget_changed(param))
                widget.setToolTip(note)
                form_layout.addRow(label, widget)
                self._algorithm_param_widgets[name] = (widget, dtype, note)
        finally:
            self._syncing_algorithm_param_form = False

    def apply_recommended_dynamic_params(
        self,
        algorithm_text: str,
        *,
        preserve_custom: bool = True,
        log_change: bool = True,
        legacy_task_batch_size: int | None = None,
    ) -> None:
        algorithm_key, recommended = self._recommended_param_specs(
            algorithm_text,
            self.dynamic_table_records() if preserve_custom else [],
        )
        if not recommended:
            self.refresh_task_preview()
            return

        current_lookup = self._dynamic_param_lookup()
        migrated_q_batch_value: str | None = None
        acq_opt_entry = current_lookup.get("acq_opt_kwargs")
        if acq_opt_entry is not None:
            acq_opt_value, acq_opt_dtype, _acq_opt_note = acq_opt_entry
            parsed_acq_opt = TaskService._coerce_scalar(acq_opt_value, acq_opt_dtype or "json")
            if isinstance(parsed_acq_opt, dict) and "qehvi_batch" in parsed_acq_opt:
                migrated_q_batch_value = self._format_dynamic_param_value(
                    parsed_acq_opt["qehvi_batch"],
                    "int",
                    name=Q_BATCH_PARAM_NAME,
                )
        if migrated_q_batch_value is None and legacy_task_batch_size is not None and legacy_task_batch_size > 0:
            migrated_q_batch_value = str(int(legacy_task_batch_size))

        recommended_records = []
        for name, default, dtype, note in recommended:
            if preserve_custom:
                if name == Q_BATCH_PARAM_NAME and name not in current_lookup and migrated_q_batch_value is not None:
                    value_text, stored_dtype, stored_note = (
                        migrated_q_batch_value,
                        dtype,
                        note,
                    )
                else:
                    value_text, stored_dtype, stored_note = current_lookup.get(name, (str(default), dtype, note))
            else:
                value_text, stored_dtype, stored_note = str(default), dtype, note
            recommended_records.append(
                [
                    name,
                    value_text,
                    stored_dtype or dtype,
                    stored_note or note,
                ]
            )

        self._sync_algorithm_param_state(
            algorithm_text,
            records=recommended_records,
            legacy_task_batch_size=legacy_task_batch_size,
            refresh_preview=True,
        )
        if log_change:
            self.view.log_console(f"Loaded recommended {algorithm_key} parameters.")

    def set_algorithm_overrides_expanded(self, expanded: bool) -> None:
        self._algorithm_overrides_expanded = bool(expanded)
        table = getattr(self.window.task_ui, "tableWidget_dynamicParams", None)
        toggle = getattr(self.window.task_ui, "toolButton_toggleAlgorithmOverrides", None)
        if table is not None:
            table.setVisible(self._algorithm_overrides_expanded)
        if toggle is not None:
            old_state = toggle.blockSignals(True)
            try:
                toggle.setChecked(self._algorithm_overrides_expanded)
                toggle.setText("Hide" if self._algorithm_overrides_expanded else "Show")
            finally:
                toggle.blockSignals(old_state)

    def toggle_algorithm_overrides(self, checked: bool) -> None:
        self.set_algorithm_overrides_expanded(bool(checked))

    def _update_algorithm_detail_summary(self, algorithm_text: str | None = None) -> None:
        summary_label = getattr(self.window.task_ui, "label_algorithmDetailSummary", None)
        if summary_label is None:
            return
        if algorithm_text is None:
            algorithm_text = self.window.task_ui.comboBox_algorithm.currentText()
        algorithm_key = self.algorithm_template_key(algorithm_text)
        specs = self._recommended_param_specs(algorithm_key, self.dynamic_table_records())[1]
        defaults = {name: (default, dtype) for name, default, dtype, _note in specs}
        custom_count = 0
        for record in self.dynamic_table_records():
            name = self._canonical_param_name(record[0] if record else "")
            if name not in defaults or parameter_ui_spec(algorithm_key, name).hidden:
                continue
            default, dtype = defaults[name]
            value = record[1] if len(record) > 1 else ""
            if TaskService._coerce_scalar(value, dtype) != TaskService._coerce_scalar(default, dtype):
                custom_count += 1
        state = "Recommended" if custom_count == 0 else f"{custom_count} custom"
        summary_label.setText(f"{algorithm_key} · {state}")

    def open_algorithm_detail_dialog(self) -> None:
        algorithm_key, specs = self._recommended_param_specs(
            self.window.task_ui.comboBox_algorithm.currentText(),
            self.dynamic_table_records(),
        )
        dialog = AlgorithmDetailDialog(
            algorithm=algorithm_key,
            specs=specs,
            records=self.dynamic_table_records(),
            evaluation_budget=self.window.task_ui.spinBox_maxEval.value(),
            parent=self.window,
        )
        self._algorithm_detail_dialog = dialog
        if dialog.exec_() != QDialog.Accepted:
            self._algorithm_detail_dialog = None
            return

        records = dialog.parameter_records()
        self._sync_algorithm_param_state(
            self.window.task_ui.comboBox_algorithm.currentText(),
            records=records,
            refresh_preview=True,
        )
        self.view.log_console("Updated algorithm detail parameters.")
        self._algorithm_detail_dialog = None

    def on_algorithm_changed(self, text: str) -> None:
        if self._syncing_objective_algorithm:
            return
        expected_objective_type = self.objective_type_for_algorithm(text)
        current_objective_type = self.window.task_ui.comboBox_objectiveType.currentText()
        if expected_objective_type != current_objective_type:
            self._syncing_objective_algorithm = True
            try:
                self.window.task_ui.comboBox_objectiveType.setCurrentText(expected_objective_type)
                self.sync_algorithm_options_with_objective_type(
                    preferred_algorithm=text,
                    update_params=False,
                )
            finally:
                self._syncing_objective_algorithm = False
        if self.window._suppress_autofill:
            return
        self.apply_recommended_dynamic_params(text, preserve_custom=False, log_change=True)
        self.update_algorithm_guidance()

    def on_objective_type_changed(self, text: str) -> None:
        if self._syncing_objective_algorithm:
            return
        previous_algorithm = self.window.task_ui.comboBox_algorithm.currentText()
        selected_algorithm = self.sync_algorithm_options_with_objective_type(
            preferred_algorithm=previous_algorithm,
            update_params=not self.window._suppress_autofill,
        )
        if not self.window._suppress_autofill:
            self._update_test_function_control(self.view.current_task())
            self.apply_offline_benchmark_template(
                self.window.task_ui.comboBox_testFunction.currentText(),
                refresh=False,
                log_change=False,
            )
            if self.algorithm_template_key(previous_algorithm) != selected_algorithm:
                self.view.log_console(
                    f"Objective Type changed to {text}; Algorithm switched to {selected_algorithm}."
                )
            self.update_algorithm_guidance()
            self.refresh_task_preview()

    def apply_offline_benchmark_template(
        self,
        test_function: str,
        *,
        refresh: bool = True,
        log_change: bool = True,
    ) -> None:
        if self.window._suppress_autofill:
            return
        if self.window.task_ui.comboBox_mode.currentText().strip() != "Offline":
            if refresh:
                self.refresh_task_preview()
            return
        template = TaskService.offline_benchmark_template(test_function)
        self.fill_table_from_records(
            self.window.task_ui.tableWidget_variables,
            template["variables"],
        )
        self.fill_table_from_records(
            self.window.task_ui.tableWidget_objectives,
            template["objectives"],
        )
        self.fill_table_from_records(
            self.window.task_ui.tableWidget_constraints,
            [],
        )
        if log_change:
            self.view.log_console(
                f"Loaded {str(test_function).upper()} offline benchmark variables and objectives."
            )
        if refresh:
            self.refresh_task_preview()

    def on_test_function_changed(self, text: str) -> None:
        if self.window._suppress_autofill:
            return
        self.apply_offline_benchmark_template(text)

    def on_dynamic_param_table_changed(self) -> None:
        self._sync_algorithm_param_state(self.window.task_ui.comboBox_algorithm.currentText())

    def table_headers(self, table) -> list[str]:
        headers = []
        for col in range(table.columnCount()):
            item = table.horizontalHeaderItem(col)
            headers.append(item.text() if item is not None else f"col_{col}")
        return headers

    def _install_objective_math_widgets(self, table) -> None:
        if table not in {self.window.task_ui.tableWidget_objectives, self.window.task_ui.tableWidget_constraints}:
            return
        headers = self.table_headers(table)
        if "Math" not in headers:
            return
        math_col = headers.index("Math")
        old_state = table.blockSignals(True)
        try:
            for row in range(table.rowCount()):
                current_item = table.item(row, math_col)
                current_value = (
                    current_item.text().strip().lower()
                    if current_item is not None and current_item.text().strip()
                    else "mean"
                )
                combo = QComboBox(table)
                for option in OBJECTIVE_MATH_OPTIONS:
                    combo.addItem(option)
                combo.setCurrentText(current_value if current_value in OBJECTIVE_MATH_OPTIONS else "mean")
                combo.currentTextChanged.connect(lambda *_args: self.refresh_task_preview())
                table.setCellWidget(row, math_col, combo)
                if current_item is None:
                    table.setItem(row, math_col, QTableWidgetItem(combo.currentText()))
                else:
                    current_item.setText(combo.currentText())
        finally:
            table.blockSignals(old_state)

    def _current_write_link_variable_names(self) -> list[str]:
        rows = TaskService.table_to_records(self.window.task_ui.tableWidget_variables)
        enabled_rows = TaskService._enabled_rows(rows)
        names: list[str] = []
        for row in enabled_rows:
            name = str(row.get("Name", "")).strip()
            if name:
                names.append(name)
        return names

    @staticmethod
    def _normalize_write_link_source_value(value: str, variable_names: list[str]) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if text in variable_names:
            return text
        try:
            index = int(text)
        except Exception:
            return text
        if 0 <= index < len(variable_names):
            return variable_names[index]
        return text

    def _install_write_link_source_widgets(self, table) -> None:
        if table is not self.window.machine_ui.tableWidget_writeLinks:
            return
        headers = self.table_headers(table)
        if "Source Index" not in headers:
            return
        source_col = headers.index("Source Index")
        variable_names = self._current_write_link_variable_names()
        old_state = table.blockSignals(True)
        try:
            for row in range(table.rowCount()):
                existing_widget = table.cellWidget(row, source_col)
                if existing_widget is not None and hasattr(existing_widget, "currentText"):
                    current_value = str(existing_widget.currentText()).strip()
                else:
                    current_item = table.item(row, source_col)
                    current_value = current_item.text().strip() if current_item is not None else ""
                normalized_value = self._normalize_write_link_source_value(current_value, variable_names)
                combo = QComboBox(table)
                combo.addItem("")
                for name in variable_names:
                    combo.addItem(name)
                if normalized_value and combo.findText(normalized_value, Qt.MatchFixedString) < 0:
                    combo.addItem(normalized_value)
                combo.setCurrentText(normalized_value)
                combo.currentTextChanged.connect(lambda *_args: self.refresh_task_preview())
                table.setCellWidget(row, source_col, combo)
                item = table.item(row, source_col)
                if item is None:
                    item = QTableWidgetItem(combo.currentText())
                    table.setItem(row, source_col, item)
                else:
                    item.setText(combo.currentText())
        finally:
            table.blockSignals(old_state)

    @staticmethod
    def _normalize_write_link_enabled_value(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return "True"
        return "True" if TaskService._is_enabled(text) else "False"

    def _install_write_link_enabled_widgets(self, table) -> None:
        if table is not self.window.machine_ui.tableWidget_writeLinks:
            return
        headers = self.table_headers(table)
        if "Enabled" not in headers:
            return
        enabled_col = headers.index("Enabled")
        old_state = table.blockSignals(True)
        try:
            for row in range(table.rowCount()):
                existing_widget = table.cellWidget(row, enabled_col)
                if existing_widget is not None and hasattr(existing_widget, "currentText"):
                    current_value = str(existing_widget.currentText()).strip()
                else:
                    current_item = table.item(row, enabled_col)
                    current_value = current_item.text().strip() if current_item is not None else ""
                normalized_value = self._normalize_write_link_enabled_value(current_value)
                combo = QComboBox(table)
                combo.addItems(["True", "False"])
                combo.setCurrentText(normalized_value)
                combo.currentTextChanged.connect(lambda *_args: self.refresh_task_preview())
                table.setCellWidget(row, enabled_col, combo)
                item = table.item(row, enabled_col)
                if item is None:
                    item = QTableWidgetItem(combo.currentText())
                    table.setItem(row, enabled_col, item)
                else:
                    item.setText(combo.currentText())
        finally:
            table.blockSignals(old_state)

    def refresh_write_link_editors(self) -> None:
        table = self.window.machine_ui.tableWidget_writeLinks
        self._install_write_link_source_widgets(table)
        self._install_write_link_enabled_widgets(table)

    def init_bounds_tool(self) -> None:
        return

    def _bounds_ui(self):
        if self._bounds_dialog is None:
            return None
        return self._bounds_dialog.ui

    def _save_bounds_tool_state(self) -> None:
        ui = self._bounds_ui()
        if ui is None:
            return
        self._bounds_tool_state = {
            "source": ui.comboBox_boundsSource.currentText(),
            "mode": ui.comboBox_boundsMode.currentText(),
            "selected_only": ui.checkBox_boundsSelectedOnly.isChecked(),
            "update_initial": ui.checkBox_boundsUpdateInitial.isChecked(),
            "primary": float(ui.doubleSpinBox_boundsPrimary.value()),
            "secondary": float(ui.doubleSpinBox_boundsSecondary.value()),
        }

    def _restore_bounds_tool_state(self) -> None:
        ui = self._bounds_ui()
        if ui is None:
            return
        state = self._bounds_tool_state
        ui.comboBox_boundsSource.setCurrentText(str(state.get("source", "Current machine readback")))
        ui.comboBox_boundsMode.setCurrentText(str(state.get("mode", "± absolute delta")))
        ui.checkBox_boundsSelectedOnly.setChecked(bool(state.get("selected_only", False)))
        ui.checkBox_boundsUpdateInitial.setChecked(bool(state.get("update_initial", True)))
        ui.doubleSpinBox_boundsPrimary.setValue(float(state.get("primary", 0.1)))
        ui.doubleSpinBox_boundsSecondary.setValue(float(state.get("secondary", 1.0)))

    def update_bounds_tool_controls(self) -> None:
        ui = self._bounds_ui()
        if ui is None:
            return

        mode = ui.comboBox_boundsMode.currentText().strip().lower()
        primary_label = ui.label_boundsPrimary
        secondary_label = ui.label_boundsSecondary
        primary_spin = ui.doubleSpinBox_boundsPrimary
        secondary_spin = ui.doubleSpinBox_boundsSecondary
        source_combo = ui.comboBox_boundsSource

        offline = self.window.task_ui.comboBox_mode.currentText().strip() == "Offline"
        source_combo.model().item(0).setEnabled(not offline)
        if offline and source_combo.currentIndex() == 0:
            old_state = source_combo.blockSignals(True)
            try:
                source_combo.setCurrentIndex(1)
            finally:
                source_combo.blockSignals(old_state)

        if "percent" in mode:
            primary_label.setText("Percent")
            primary_spin.setMinimum(0.0)
            primary_spin.setMaximum(1000000.0)
            secondary_label.setVisible(False)
            secondary_spin.setVisible(False)
        elif "fixed" in mode:
            primary_label.setText("Lower")
            primary_spin.setMinimum(-1000000000.0)
            primary_spin.setMaximum(1000000000.0)
            secondary_label.setText("Upper")
            secondary_label.setVisible(True)
            secondary_spin.setVisible(True)
        else:
            primary_label.setText("Delta")
            primary_spin.setMinimum(0.0)
            primary_spin.setMaximum(1000000000.0)
            secondary_label.setVisible(False)
            secondary_spin.setVisible(False)

        source_required = "fixed" not in mode or ui.checkBox_boundsUpdateInitial.isChecked()
        ui.label_boundsSource.setEnabled(source_required)
        source_combo.setEnabled(source_required)

        if self._last_bounds_preview:
            ui.label_boundsToolSummary.setText(self._last_bounds_preview)
        else:
            scope = "selected rows" if ui.checkBox_boundsSelectedOnly.isChecked() else "enabled rows"
            source = ui.comboBox_boundsSource.currentText().strip().lower()
            if source.startswith("current machine"):
                source_text = "current machine readback"
            else:
                source_text = "current Initial values"
            ui.label_boundsToolSummary.setText(
                f"Generate bounds for {scope} using {source_text}."
            )

    def _table_records_with_row_indices(self, table) -> list[tuple[int, dict[str, str]]]:
        headers = self.table_headers(table)
        rows: list[tuple[int, dict[str, str]]] = []
        for row in range(table.rowCount()):
            record: dict[str, str] = {}
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
                rows.append((row, record))
        return rows

    def _target_variable_rows(self) -> list[tuple[int, dict[str, str]]]:
        ui = self._bounds_ui()
        if ui is None:
            raise ValueError("Bounds Tools dialog is not open.")
        table = self.window.task_ui.tableWidget_variables
        rows = self._table_records_with_row_indices(table)
        if ui.checkBox_boundsSelectedOnly.isChecked():
            selected_rows = {index.row() for index in table.selectionModel().selectedRows()}
            if not selected_rows:
                raise ValueError("Select one or more variables, or turn off 'Only selected variables'.")
            targets = [(row_index, row) for row_index, row in rows if row_index in selected_rows]
        else:
            targets = [
                (row_index, row)
                for row_index, row in rows
                if TaskService._is_enabled(row.get("Enable", ""))
            ]

        if not targets:
            raise ValueError("No variable rows are available for bounds generation.")
        return targets

    @staticmethod
    def _parse_required_float(row: dict[str, str], key: str, *, row_number: int) -> float:
        text = str(row.get(key, "")).strip()
        try:
            return float(text)
        except Exception as exc:
            raise ValueError(f"Variable row {row_number} has invalid {key} value {text!r}.") from exc

    def _resolve_bounds_source_values(
        self,
        task: dict,
        target_rows: list[tuple[int, dict[str, str]]],
    ) -> list[float]:
        ui = self._bounds_ui()
        if ui is None:
            raise ValueError("Bounds Tools dialog is not open.")
        source = ui.comboBox_boundsSource.currentText().strip().lower()
        variable_payloads = [row for _, row in target_rows]
        if source.startswith("current machine"):
            return self.window.machine_controller.read_current_knob_values(task, variable_payloads)
        return [
            self._parse_required_float(row, "Initial", row_number=row_index + 1)
            for row_index, row in target_rows
        ]

    def _compute_bounds_from_source(self, source_value: float) -> tuple[float, float]:
        ui = self._bounds_ui()
        if ui is None:
            raise ValueError("Bounds Tools dialog is not open.")
        mode = ui.comboBox_boundsMode.currentText().strip().lower()
        primary = float(ui.doubleSpinBox_boundsPrimary.value())
        secondary = float(ui.doubleSpinBox_boundsSecondary.value())

        if "fixed" in mode:
            lower, upper = primary, secondary
        elif "percent" in mode:
            if abs(source_value) < 1e-15:
                raise ValueError(
                    "Percent mode cannot expand around a zero source value. Use ± absolute delta instead."
                )
            delta = abs(source_value) * primary / 100.0
            lower, upper = source_value - delta, source_value + delta
        else:
            delta = primary
            lower, upper = source_value - delta, source_value + delta

        if lower >= upper:
            raise ValueError(f"Computed bounds are invalid: lower={lower:g}, upper={upper:g}.")
        return lower, upper

    def _build_bounds_plan(self, task: dict) -> list[dict[str, object]]:
        ui = self._bounds_ui()
        if ui is None:
            raise ValueError("Bounds Tools dialog is not open.")
        target_rows = self._target_variable_rows()
        update_initial = ui.checkBox_boundsUpdateInitial.isChecked()
        mode = ui.comboBox_boundsMode.currentText().strip().lower()
        source_required = "fixed" not in mode or update_initial
        source_values: list[float | None]
        if source_required:
            source_values = self._resolve_bounds_source_values(task, target_rows)
        else:
            source_values = [None] * len(target_rows)
        plan: list[dict[str, object]] = []
        for (row_index, row), source_value in zip(target_rows, source_values):
            lower, upper = self._compute_bounds_from_source(
                0.0 if source_value is None else source_value
            )
            plan.append(
                {
                    "row_index": row_index,
                    "name": str(row.get("Name", "")).strip() or f"x{row_index}",
                    "source": None if source_value is None else float(source_value),
                    "lower": float(lower),
                    "upper": float(upper),
                    "initial": float(source_value) if update_initial and source_value is not None else None,
                }
            )
        return plan

    def _show_bounds_plan(self, plan: list[dict[str, object]]) -> str:
        ui = self._bounds_ui()
        if ui is None:
            raise ValueError("Bounds Tools dialog is not open.")
        table = ui.tableWidget_boundsPreview
        table.setRowCount(len(plan))
        for row_index, entry in enumerate(plan):
            initial = entry["initial"]
            source = entry["source"]
            values = (
                str(entry["name"]),
                "Not used" if source is None else format(float(source), "g"),
                format(float(entry["lower"]), "g"),
                format(float(entry["upper"]), "g"),
                "Unchanged" if initial is None else format(float(initial), "g"),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column > 0:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                table.setItem(row_index, column, item)

        scope = "selected" if ui.checkBox_boundsSelectedOnly.isChecked() else "enabled"
        preview = f"Preview ready for {len(plan)} {scope} variable row(s)."
        self._last_bounds_preview = preview
        ui.label_boundsToolSummary.setText(preview)
        ui.pushButton_applyBounds.setEnabled(bool(plan))
        return preview

    def _apply_bounds_plan(self, plan: list[dict[str, object]]) -> None:
        table = self.window.task_ui.tableWidget_variables
        headers = self.table_headers(table)
        lower_col = headers.index("Lower")
        upper_col = headers.index("Upper")
        initial_col = headers.index("Initial")
        old_state = table.blockSignals(True)
        try:
            for entry in plan:
                row_index = int(entry["row_index"])
                for column, value in (
                    (lower_col, entry["lower"]),
                    (upper_col, entry["upper"]),
                ):
                    item = table.item(row_index, column)
                    if item is None:
                        item = QTableWidgetItem()
                        table.setItem(row_index, column, item)
                    item.setText(format(float(value), "g"))

                if entry["initial"] is not None:
                    item = table.item(row_index, initial_col)
                    if item is None:
                        item = QTableWidgetItem()
                        table.setItem(row_index, initial_col, item)
                    item.setText(format(float(entry["initial"]), "g"))
        finally:
            table.blockSignals(old_state)

    def preview_bounds_tool(self) -> None:
        task = self.view.current_task()
        try:
            plan = self._build_bounds_plan(task)
            self._bounds_preview_plan = plan
            preview = self._show_bounds_plan(plan)
        except Exception as exc:
            self._bounds_preview_plan = []
            self._last_bounds_preview = f"Bounds preview failed: {exc}"
            ui = self._bounds_ui()
            if ui is not None:
                ui.label_boundsToolSummary.setText(self._last_bounds_preview)
                ui.tableWidget_boundsPreview.setRowCount(0)
                ui.pushButton_applyBounds.setEnabled(False)
            self.view.log_warning(f"Bounds preview failed: {exc}")
            QMessageBox.warning(self.window, "Bounds Preview", str(exc))
            return
        self._save_bounds_tool_state()
        self.view.log_console(f"Bounds preview ready. {preview}")
        self.view.status_message("Bounds preview updated.", 3000)

    def apply_bounds_tool(self) -> None:
        if not self._bounds_preview_plan:
            QMessageBox.information(
                self.window,
                "Apply Bounds",
                "Preview the bounds before applying them.",
            )
            return
        try:
            plan = list(self._bounds_preview_plan)
            self._apply_bounds_plan(plan)
        except Exception as exc:
            self._last_bounds_preview = f"Bounds apply failed: {exc}"
            ui = self._bounds_ui()
            if ui is not None:
                ui.label_boundsToolSummary.setText(self._last_bounds_preview)
            self.view.log_warning(f"Bounds apply failed: {exc}")
            QMessageBox.warning(self.window, "Apply Bounds", str(exc))
            return

        ui = self._bounds_ui()
        self._bounds_preview_plan = []
        preview = f"Applied previewed bounds to {len(plan)} variable row(s)."
        self._last_bounds_preview = preview
        if ui is not None:
            ui.label_boundsToolSummary.setText(preview)
            ui.pushButton_applyBounds.setEnabled(False)
        self._save_bounds_tool_state()
        self.view.log_console(f"Applied generated bounds. {preview}")
        self.view.append_overview_activity("Bounds", status="Updated knob bounds from bounds tool.")
        self.view.status_message("Bounds applied to variable table.", 4000)
        self.refresh_task_preview()

    def _on_bounds_tool_settings_changed(self) -> None:
        self._save_bounds_tool_state()
        self._last_bounds_preview = ""
        self._bounds_preview_plan = []
        ui = self._bounds_ui()
        if ui is not None:
            ui.tableWidget_boundsPreview.setRowCount(0)
            ui.pushButton_applyBounds.setEnabled(False)
        self.update_bounds_tool_controls()

    def _on_bounds_dialog_finished(self, _result: int) -> None:
        self._save_bounds_tool_state()
        self._bounds_preview_plan = []
        self._bounds_dialog = None

    def open_bounds_tool_dialog(self) -> None:
        dialog = BoundsToolsDialog(self.window)
        self._bounds_dialog = dialog
        self._last_bounds_preview = ""
        self._bounds_preview_plan = []
        self._restore_bounds_tool_state()
        ui = dialog.ui
        ui.comboBox_boundsSource.currentTextChanged.connect(self._on_bounds_tool_settings_changed)
        ui.comboBox_boundsMode.currentTextChanged.connect(self._on_bounds_tool_settings_changed)
        ui.checkBox_boundsSelectedOnly.toggled.connect(self._on_bounds_tool_settings_changed)
        ui.checkBox_boundsUpdateInitial.toggled.connect(self._on_bounds_tool_settings_changed)
        ui.doubleSpinBox_boundsPrimary.valueChanged.connect(self._on_bounds_tool_settings_changed)
        ui.doubleSpinBox_boundsSecondary.valueChanged.connect(self._on_bounds_tool_settings_changed)
        ui.pushButton_previewBounds.clicked.connect(self.preview_bounds_tool)
        ui.pushButton_applyBounds.clicked.connect(self.apply_bounds_tool)
        dialog.finished.connect(self._on_bounds_dialog_finished)
        self.update_bounds_tool_controls()
        dialog.exec_()

    def fill_table_from_records(self, table, records) -> None:
        headers = self.table_headers(table)
        old_state = table.blockSignals(True)
        try:
            table.setRowCount(0)
            for record in records or []:
                row = table.rowCount()
                table.insertRow(row)
                for col, header in enumerate(headers):
                    value = record.get(header, "") if isinstance(record, dict) else ""
                    table.setItem(row, col, QTableWidgetItem(str(value)))
        finally:
            table.blockSignals(old_state)
        self._install_objective_math_widgets(table)
        self.refresh_write_link_editors()
        self.refresh_task_table_empty_states()

    def _task_table(self, field: str):
        tables = {
            "variables": self.window.task_ui.tableWidget_variables,
            "objectives": self.window.task_ui.tableWidget_objectives,
            "constraints": self.window.task_ui.tableWidget_constraints,
        }
        try:
            return tables[field]
        except KeyError as exc:
            raise ValueError(f"Unknown Task Builder field: {field!r}") from exc

    def _next_task_row_name(self, field: str) -> str:
        table = self._task_table(field)
        existing = {
            str(row.get("Name", "")).strip()
            for row in TaskService.table_to_records(table)
            if str(row.get("Name", "")).strip()
        }
        prefix = {
            "variables": "variable",
            "objectives": "objective",
            "constraints": "constraint",
        }[field]
        index = 1
        while f"{prefix}_{index}" in existing:
            index += 1
        return f"{prefix}_{index}"

    def add_task_table_row(self, field: str) -> None:
        table = self._task_table(field)
        name = self._next_task_row_name(field)
        defaults = {
            "variables": ["Y", name, "", "", "", "main"],
            "objectives": ["Y", name, "maximize", "1.0", "1", "mean"],
            "constraints": ["Y", name, "", "", "mean"],
        }[field]
        row = table.rowCount()
        table.insertRow(row)
        for column, value in enumerate(defaults):
            table.setItem(row, column, QTableWidgetItem(value))
        self._install_objective_math_widgets(table)
        table.selectRow(row)
        table.setCurrentCell(row, 1)
        self.refresh_task_table_empty_states()
        self.refresh_task_preview()

    def remove_selected_task_rows(self, field: str) -> None:
        table = self._task_table(field)
        rows = sorted(
            {index.row() for index in table.selectionModel().selectedRows()},
            reverse=True,
        )
        if not rows and table.currentRow() >= 0:
            rows = [table.currentRow()]
        if not rows:
            QMessageBox.information(self.window, "Remove Rows", "Select one or more rows first.")
            return
        for row in rows:
            table.removeRow(row)
        self.refresh_task_table_empty_states()
        self.refresh_task_preview()

    def refresh_task_table_empty_states(self) -> None:
        ui = self.window.task_ui
        if not hasattr(ui, "label_variablesEmptyState"):
            return
        online = ui.comboBox_mode.currentText().strip() == "Online EPICS"
        messages = {
            "variables": (
                "Load a Machine Profile, then Sync To Task."
                if online
                else "Add at least one benchmark variable."
            ),
            "objectives": (
                "Load a Machine Profile, then Sync To Task."
                if online
                else "Add at least one benchmark objective."
            ),
            "constraints": (
                "No constraints configured. Profile constraints appear after Sync To Task."
                if online
                else "No constraints configured. Add rows only for constrained benchmarks."
            ),
        }
        for field, message in messages.items():
            table = self._task_table(field)
            label = getattr(ui, f"label_{field}EmptyState")
            add_button = getattr(ui, f"pushButton_add{field.title()[:-1]}Row")
            remove_button = getattr(ui, f"pushButton_remove{field.title()[:-1]}Rows")
            label.setText(message)
            label.setVisible(table.rowCount() == 0)
            add_button.setVisible(not online)
            remove_button.setEnabled(table.rowCount() > 0)

    def apply_task_payload(
        self,
        task: dict,
        *,
        source_label: str | None = None,
        goto_builder: bool = True,
    ) -> None:
        self.window._suppress_autofill = True
        try:
            self.window.task_ui.lineEdit_taskName.setText(str(task.get("task_name", "untitled_task")))
            self.window.task_ui.comboBox_mode.setCurrentText(str(task.get("mode", "Online EPICS")))
            self.window.task_ui.comboBox_objectiveType.setCurrentText(
                str(task.get("objective_type", "Single Objective"))
            )
            self.sync_algorithm_options_with_objective_type(
                preferred_algorithm=str(task.get("algorithm", "BO")),
                update_params=False,
            )
            self.window.task_ui.comboBox_algorithm.setCurrentText(str(task.get("algorithm", "BO")))
            test_function = str(task.get("test_function", "rosenbrock")).strip().lower() or "rosenbrock"
            available_test_functions = TaskService.offline_test_function_names(
                str(task.get("objective_type", "Single Objective"))
            )
            self.window.task_ui.comboBox_testFunction.clear()
            self.window.task_ui.comboBox_testFunction.addItems(list(available_test_functions))
            test_function_index = self.window.task_ui.comboBox_testFunction.findText(
                test_function,
                Qt.MatchFixedString,
            )
            if test_function_index >= 0:
                self.window.task_ui.comboBox_testFunction.setCurrentIndex(test_function_index)
            self.window.task_ui.spinBox_seed.setValue(int(task.get("seed", 0)))
            self.window.task_ui.spinBox_maxEval.setValue(int(task.get("max_evaluations", 20)))
            self.window.task_ui.lineEdit_workdir.setText(str(task.get("workdir", Path.cwd())))

            self.fill_table_from_records(self.window.task_ui.tableWidget_variables, task.get("variables", []))
            self.fill_table_from_records(self.window.task_ui.tableWidget_objectives, task.get("objectives", []))
            self.fill_table_from_records(self.window.task_ui.tableWidget_constraints, task.get("constraints", []))
            self.fill_table_from_records(
                self.window.task_ui.tableWidget_dynamicParams,
                task.get("algorithm_params", []),
            )
            self.apply_recommended_dynamic_params(
                self.window.task_ui.comboBox_algorithm.currentText(),
                preserve_custom=True,
                log_change=False,
                legacy_task_batch_size=int(task.get("batch_size", 1) or 1),
            )

            self.apply_machine_payload(task.get("machine", {}) or {}, refresh=False)
        finally:
            self.window._suppress_autofill = False

        self.refresh_task_preview()
        if source_label:
            self.view.log_console(source_label)
            self.view.status_message(source_label, 5000)
            self.view.append_overview_activity("Task Update", status=source_label)
        if goto_builder:
            self.view.go_to_page(self.window.PAGE_TASK_BUILDER)

    def apply_machine_payload(self, machine: dict, *, refresh: bool = True) -> None:
        self.window.machine_ui.lineEdit_caAddress.setText(str(machine.get("ca_address", "")))
        self.window.machine_ui.checkBox_restore.setChecked(
            bool(machine.get("restore_on_abort", True))
        )
        self.window.machine_ui.checkBox_readbackCheck.setChecked(
            bool(machine.get("readback_check", False))
        )
        self.window.machine_ui.doubleSpinBox_readbackTol.setValue(
            float(machine.get("readback_tol", 1e-6) or 0.0)
        )
        self.window.machine_ui.doubleSpinBox_setInterval.setValue(
            float(machine.get("set_interval", 1.0))
        )
        self.window.machine_ui.doubleSpinBox_sampleInterval.setValue(
            float(machine.get("sample_interval", 0.2))
        )
        self.window.machine_ui.doubleSpinBox_timeout.setValue(
            float(machine.get("write_timeout", 2.0))
        )
        self.window.machine_ui.comboBox_policy.setCurrentText(
            str(
                machine.get(
                    "write_policy",
                    self.window.machine_ui.comboBox_policy.currentText(),
                )
            )
        )
        self.fill_table_from_records(
            self.window.machine_ui.tableWidget_mapping,
            machine.get("mapping", []),
        )
        self.fill_table_from_records(
            self.window.machine_ui.tableWidget_writeLinks,
            machine.get("write_links", []),
        )
        self.window._load_policy_presets(machine)
        self.window._load_policy_bindings(machine)
        self.window.machine_ui.machine_profile = copy.deepcopy(
            machine.get("profile", {})
            or {
                "profile_id": "embedded",
                "name": "Embedded Machine",
                "version": 1,
                "source": "",
            }
        )
        if hasattr(self.window, "machine_controller"):
            self.window.machine_controller.refresh_machine_profile_bar()
        if refresh:
            self.refresh_task_preview()

    def create_new_offline_task(self) -> None:
        old_suppress = self.window._suppress_autofill
        self.window._suppress_autofill = True
        try:
            self.window.task_ui.lineEdit_taskName.setText("offline_task")
            self.window.task_ui.comboBox_mode.setCurrentText("Offline")
            self.window.task_ui.comboBox_objectiveType.setCurrentText("Single Objective")
            self.sync_algorithm_options_with_objective_type(
                preferred_algorithm="BO", update_params=False
            )
            self.window.task_ui.comboBox_algorithm.setCurrentText("BO")
            self.window.task_ui.comboBox_testFunction.setCurrentText("rosenbrock")
            self.window.task_ui.spinBox_seed.setValue(0)
            self.window.task_ui.spinBox_maxEval.setValue(100)
            self.fill_table_from_records(
                self.window.task_ui.tableWidget_variables,
                [
                    {
                        "Enable": "Y",
                        "Name": name,
                        "Lower": "-2.0",
                        "Upper": "2.0",
                        "Initial": "0.0",
                        "Group": "main",
                    }
                    for name in ("x0", "x1")
                ],
            )
            self.fill_table_from_records(
                self.window.task_ui.tableWidget_objectives,
                [
                    {
                        "Enable": "Y",
                        "Name": "rosenbrock",
                        "Direction": "maximize",
                        "Weight": "1.0",
                        "Samples": "1",
                        "Math": "mean",
                    }
                ],
            )
            self.fill_table_from_records(self.window.task_ui.tableWidget_constraints, [])
            self.apply_recommended_dynamic_params(
                "BO", preserve_custom=False, log_change=False
            )
            self._reset_machine_for_new_task()
        finally:
            self.window._suppress_autofill = old_suppress
        self.refresh_task_preview()
        self.view.go_to_page(self.window.PAGE_TASK_BUILDER)
        self.view.log_console("Created a new offline task.")
        self.view.append_overview_activity("Task", status="Created offline task.")

    def create_new_online_task(self) -> None:
        old_suppress = self.window._suppress_autofill
        self.window._suppress_autofill = True
        try:
            self.window.task_ui.lineEdit_taskName.setText("online_task")
            self.window.task_ui.comboBox_mode.setCurrentText("Online EPICS")
            self.window.task_ui.comboBox_objectiveType.setCurrentText("Single Objective")
            self.sync_algorithm_options_with_objective_type(
                preferred_algorithm="TuRBO", update_params=False
            )
            self.window.task_ui.comboBox_algorithm.setCurrentText("TuRBO")
            self.window.task_ui.comboBox_testFunction.setCurrentText("rosenbrock")
            self.window.task_ui.spinBox_seed.setValue(0)
            self.window.task_ui.spinBox_maxEval.setValue(100)
            self.fill_table_from_records(self.window.task_ui.tableWidget_variables, [])
            self.fill_table_from_records(self.window.task_ui.tableWidget_objectives, [])
            self.fill_table_from_records(self.window.task_ui.tableWidget_constraints, [])
            self.apply_recommended_dynamic_params(
                "TuRBO", preserve_custom=False, log_change=False
            )
            self._reset_machine_for_new_task()
        finally:
            self.window._suppress_autofill = old_suppress
        self.refresh_task_preview()
        self.view.go_to_page(self.window.PAGE_TASK_BUILDER)
        self.view.log_console("Created a new online task.")
        self.view.append_overview_activity("Task", status="Created online task.")

    def _reset_machine_for_new_task(self) -> None:
        self.apply_machine_payload(
            {
                "restore_on_abort": True,
                "readback_check": False,
                "readback_tol": 1e-6,
                "set_interval": 1.0,
                "sample_interval": 0.2,
                "write_timeout": 2.0,
                "write_policy": "none",
                "mapping": [],
                "write_links": [],
                "policy_bindings": [],
                "policy_presets": [],
                "profile": {
                    "profile_id": "embedded",
                    "name": "Embedded Machine",
                    "version": 1,
                    "source": "",
                },
            },
            refresh=False,
        )

    def browse_workdir(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self.window,
            "Select working directory",
            self.window.task_ui.lineEdit_workdir.text(),
        )
        if directory:
            self.window.task_ui.lineEdit_workdir.setText(directory)

    def refresh_task_preview(self) -> None:
        self.refresh_task_table_empty_states()
        task = self.view.current_task()
        self.window.machine_controller.invalidate_machine_check_if_stale(task)
        self._set_validation_status(
            "Not validated",
            "subtle",
            "The task changed after its most recent validation.",
        )
        self.refresh_write_link_editors()
        self._update_test_function_control(task)
        self.update_bounds_tool_controls()
        self.update_algorithm_guidance(task)
        self._update_algorithm_detail_summary(task.get("algorithm"))
        self.set_algorithm_overrides_expanded(self._algorithm_overrides_expanded)
        enabled_variables = len(TaskService._enabled_rows(task.get("variables", [])))
        enabled_objectives = len(TaskService._enabled_rows(task.get("objectives", [])))
        enabled_constraints = len(TaskService._enabled_rows(task.get("constraints", [])))
        self.window.task_ui.label_builderSummary.setText(
            f"{task['objective_type']} · {enabled_variables} variable(s) · {enabled_objectives} objective(s) · "
            f"{enabled_constraints} constraint(s) · budget {int(task.get('max_evaluations', 0) or 0)}"
        )
        self.window.ui.label_statusTaskValue.setText(task["task_name"])
        self.window.ui.label_statusModeValue.setText(task["mode"])
        self.window.ui.label_statusAlgorithmValue.setText(task["algorithm"])
        self.window._sync_workspace_status(task)
        self.window._refresh_overview_cards(task)
        if not self.window.state.latest_task_snapshot:
            self.window.runtime_status_controller.update_evaluation_label()
        self.window.machine_controller.update_pv_library_summary()
        self.window.machine_controller.refresh_machine_summary()
        self.view.refresh_overview_readiness()
        if self.window.state.run.phase in {"Idle", "Finished", "Error", "Aborted"}:
            self.view.reset_plot_data()
            if hasattr(self.window, "obj_canvas") and hasattr(self.window, "pareto_canvas"):
                self.view.redraw_plots()

    def _update_test_function_control(self, task: dict | None = None) -> None:
        if task is None:
            task = self.view.current_task()
        offline_task = str(task.get("mode", "")).strip() == "Offline"
        objective_type = str(task.get("objective_type", "")).strip() or "Single Objective"
        combo = self.window.task_ui.comboBox_testFunction
        label = self.window.task_ui.label_testFunction
        available = TaskService.offline_test_function_names(objective_type)
        configured = str(task.get("test_function", "")).strip().lower()
        selected = configured if configured in available else available[0]
        current_items = tuple(combo.itemText(index) for index in range(combo.count()))
        if current_items != available or combo.currentText().strip().lower() != selected:
            old_state = combo.blockSignals(True)
            try:
                combo.clear()
                combo.addItems(list(available))
                combo.setCurrentText(selected)
            finally:
                combo.blockSignals(old_state)
        combo.setEnabled(offline_task)
        label.setEnabled(offline_task)
        tooltip = (
            f"Offline {objective_type.lower()} benchmark function."
            if offline_task
            else "Offline benchmark function. Disabled because the current task uses Online EPICS."
        )
        combo.setToolTip(tooltip)
        label.setToolTip(tooltip)
        if hasattr(self.window, "offline_ui"):
            self.window.offline_ui.label_offlineHint.setText(
                "Built-in benchmark used for local optimization tests. "
                "ZDT1, ZDT2 and DTLZ2 use the standard [0, 1] variable domain."
                if objective_type == "Multi Objective"
                else "Built-in benchmark used for local single-objective optimization tests."
            )

    def update_algorithm_guidance(self, task: dict | None = None) -> None:
        if task is None:
            task = self.view.current_task()

        dyn = TaskService._dynamic_params_to_dict(task.get("algorithm_params", []))
        algorithm = TaskService._optimizer_name_from_gui(task.get("algorithm", "BO"))
        budget_text = self._build_budget_hint(task, dyn, algorithm)

        if hasattr(self.window.task_ui, "label_budgetHint"):
            self.window.task_ui.label_budgetHint.setText(budget_text)
            self.window.task_ui.label_budgetHint.setToolTip(budget_text)
        self.window.task_ui.spinBox_maxEval.setToolTip(budget_text)
        self.window.task_ui.comboBox_algorithm.setToolTip(
            "Select the optimizer family. Different algorithms consume evaluation budget differently; "
            "see the budget summary below."
        )
        uses_random_seed = algorithm != "rcds"
        self.window.task_ui.spinBox_seed.setEnabled(uses_random_seed)
        self.window.task_ui.label_seed.setEnabled(uses_random_seed)
        seed_hint = (
            "Controls reproducible randomized initialization and sampling."
            if uses_random_seed
            else "RCDS is deterministic in the current implementation and does not use Random Seed."
        )
        self.window.task_ui.spinBox_seed.setToolTip(seed_hint)
        self.window.task_ui.label_seed.setToolTip(seed_hint)
        self.window.task_ui.tableWidget_dynamicParams.setToolTip(
            self._dynamic_params_tooltip(algorithm)
        )
        self.window.task_ui.groupBox_dynamicParams.setToolTip(
            self._dynamic_params_tooltip(algorithm)
        )
        if hasattr(self.window.task_ui, "pushButton_openAlgorithmDetail"):
            self.window.task_ui.pushButton_openAlgorithmDetail.setToolTip(
                self._dynamic_params_tooltip(algorithm)
            )

    def _build_budget_hint(self, task: dict, dyn: dict, algorithm: str) -> str:
        enabled_objectives = TaskService._enabled_rows(task.get("objectives", []))
        n_objectives = max(1, len(enabled_objectives))
        max_evals = max(1, int(task.get("max_evaluations", 20)))
        normalized_name = self.algorithm_template_key(task.get("algorithm", "BO"))

        try:
            kwargs = TaskService._build_optimizer_kwargs(task, dyn, n_objectives)
        except Exception as exc:
            return (
                "Max Evaluations is treated as a total objective-call cap.\n"
                f"Current parameter mapping could not be derived for {normalized_name}: {exc}"
            )

        if algorithm in {"bo", "consbo", "turbo", "mobo", "consmobo"}:
            n_init = int(kwargs.get("n_init", 0))
            n_iter = int(kwargs.get("n_iter", 0))
            iter_cost = int(TaskService._bo_iteration_eval_cost(task, dyn, algorithm))
            planned = n_init + n_iter * iter_cost
            slack = max(0, max_evals - planned)
            slack_text = f" · {slack} unused" if slack else ""
            return f"{n_init} initial + {n_iter} x {iter_cost}/step = {planned}/{max_evals}{slack_text}"

        if algorithm == "rcds":
            return f"RCDS hard limit: {max_evals} objective evaluations"

        pop_size = int(kwargs.get("pop_size", 0))
        evals_per_gen = int(kwargs.get("evals_per_gen", pop_size))
        n_generations = int(kwargs.get("n_generations", 0))
        if algorithm in {"mggpo", "consmggpo", "mggpo_so", "consmggpo_so"}:
            planned = pop_size + n_generations * evals_per_gen
            slack = max(0, max_evals - planned)
            slack_text = f" · {slack} unused" if slack else ""
            return f"{pop_size} initial + {n_generations} x {evals_per_gen}/generation = {planned}/{max_evals}{slack_text}"

        planned = pop_size * (1 + n_generations)
        slack = max(0, max_evals - planned)
        slack_text = f" · {slack} unused" if slack else ""
        return f"{pop_size} x (1 + {n_generations} generations) = {planned}/{max_evals}{slack_text}"

    def show_task_preview(self) -> None:
        task = self.view.current_task()
        preview_text = TaskService.to_preview_text(task)

        dialog = QDialog(self.window)
        dialog.setWindowTitle("Task Preview")
        dialog.resize(900, 700)

        layout = QVBoxLayout(dialog)
        editor = QPlainTextEdit(dialog)
        editor.setReadOnly(True)
        editor.setPlainText(preview_text)
        layout.addWidget(editor)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        export_button = QPushButton("Export TaskConfig", dialog)
        export_button.setProperty("inlineAction", True)
        export_button.setFixedSize(142, 28)
        export_button.clicked.connect(self.export_config)
        button_row.addWidget(export_button)
        close_button = QPushButton("Close", dialog)
        close_button.setProperty("inlineAction", True)
        close_button.setFixedSize(104, 28)
        close_button.clicked.connect(dialog.accept)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)

        dialog.exec_()

    def _bo_budget_detail(self, task: dict, dyn: dict, algorithm: str, iter_cost: int) -> str:
        if algorithm == "turbo":
            trust_regions = int(dyn.get("n_trust_regions", 1))
            return (
                f"Current TuRBO step cost follows n_trust_regions={trust_regions}. "
                "Configure per-step cost through Algorithm Detail."
            )
        if algorithm in {"mobo", "consmobo"}:
            acq = str(dyn.get("acq", "ehvi")).strip().lower()
            if "q" in acq:
                label = "ConsMOBO" if algorithm == "consmobo" else "MOBO"
                return (
                    f"Current {label} step cost follows acquisition={acq} with q-batch={iter_cost}. "
                    "Configure q-batch through Algorithm Detail -> q-Batch Size."
                )
            label = "ConsMOBO" if algorithm == "consmobo" else "MOBO"
            return (
                f"Current {label} acquisition={acq} evaluates one new point per iteration. "
                "q-Batch Size stays available in Algorithm Detail, but it is ignored unless acquisition starts with q."
            )
        if algorithm == "consbo":
            return "Current ConsBO wiring evaluates one constrained point per iteration after the initial design."
        return "Current BO wiring evaluates one new point per iteration after the initial design."

    def _dynamic_params_tooltip(self, algorithm: str) -> str:
        common = (
            "Algorithm-specific setup grouped into basic, advanced and execution settings. "
            "The evaluation budget remains controlled by Max Evaluations."
        )
        if algorithm == "turbo":
            return common + " For TuRBO, n_trust_regions determines the per-iteration evaluation cost."
        if algorithm == "mobo":
            return common + " For MOBO, q-Batch Size stays visible for convenience and only affects qEHVI/qNEHVI."
        if algorithm == "consmobo":
            return common + " For ConsMOBO, constraints come from Task Builder constraints and Machine PV Mapping; q-Batch Size is used by qEHVI/qNEHVI."
        if algorithm == "consmggpo":
            return common + " For ConsMGGPO, constraints come from Task Builder constraints and Machine PV Mapping; total evaluations scale with pop_size and evals_per_gen per generation."
        if algorithm == "consmggpo_so":
            return common + " For ConsMGGPO-SO, constraints come from Task Builder constraints and Machine PV Mapping; total evaluations scale with pop_size and evals_per_gen per generation."
        if algorithm == "mggpo_so":
            return common + " For MGGPO-SO, total evaluations scale with pop_size and evals_per_gen per generation."
        if algorithm == "consbo":
            return common + " For ConsBO, constraints come from Task Builder constraints and Machine PV Mapping."
        if algorithm in {"mopso", "nsga2"}:
            return common + " For population algorithms, total evaluations scale as pop_size x (1 + n_generations)."
        return common

    def apply_task_to_dashboard(self) -> None:
        self.refresh_task_preview()
        self.view.log_console("Applied current task settings to dashboard summary.")
        self.view.append_overview_activity("Dashboard", status="Applied current task snapshot.")
        self.view.go_to_page(self.window.PAGE_DASHBOARD)

    def validate_task_build(self, task: dict) -> tuple[bool, list[str]]:
        try:
            TaskService.build_task_config(task)
        except Exception as exc:
            return False, [str(exc)]
        return True, []

    def validate_task(self) -> bool:
        task = self.view.current_task()
        ok, errors = TaskService.validate_task_data(task)
        build_ok, build_errors = self.validate_task_build(task)
        if not build_ok:
            ok = False
            errors.extend(build_errors)
        errors = list(dict.fromkeys(errors))
        if not ok:
            self._set_validation_status("Validation failed", "danger", "\n".join(errors))
            self.view.log_warning("Validation failed.")
            for err in errors:
                self.view.log_warning(f" - {err}")
            QMessageBox.warning(self.window, "Validation Failed", "\n".join(errors))
            return False
        self._set_validation_status("Validated", "success", "Task validation passed.")
        self.view.log_console("Task validation passed.")
        QMessageBox.information(self.window, "Validation", "Task validation passed.")
        return True

    def validate_task_silent(self, task: dict | None = None) -> bool:
        task = task if task is not None else self.view.current_task()
        ok, errors = TaskService.validate_task_data(task)
        build_ok, build_errors = self.validate_task_build(task)
        if not build_ok:
            ok = False
            errors.extend(build_errors)
        errors = list(dict.fromkeys(errors))
        if not ok:
            self._set_validation_status("Validation failed", "danger", "\n".join(errors))
            self.view.log_warning("Silent validation failed.")
            for err in errors:
                self.view.log_warning(f" - {err}")
        else:
            self._set_validation_status("Validated", "success", "Task validation passed.")
        return ok

    def _set_validation_status(self, text: str, tone: str, tooltip: str) -> None:
        label = getattr(self.window.ui, "label_validationStatus", None)
        if label is None:
            return
        label.setText(text)
        label.setToolTip(tooltip)
        label.setProperty("tone", tone)
        label.style().unpolish(label)
        label.style().polish(label)
        self.window._refresh_overview_cards()

    def export_config(self) -> None:
        task = self.view.current_task()
        TASK_CONFIG_YAML_DIR.mkdir(parents=True, exist_ok=True)
        default_name = f"{task.get('task_name', 'task')}_task_config.yaml"
        path, _ = QFileDialog.getSaveFileName(
            self.window,
            "Export TaskConfig",
            str(TASK_CONFIG_YAML_DIR / default_name),
            "YAML Files (*.yaml *.yml);;All Files (*)",
        )
        if not path:
            return
        if not path.lower().endswith((".yaml", ".yml")):
            path = f"{path}.yaml"
        TaskService.export_task_config(task, path)
        self.view.log_console(f"Task exported to: {path}")

    def open_config(self) -> None:
        GUI_PROJECT_DIR.mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getOpenFileName(
            self.window,
            "Open Project",
            str(GUI_PROJECT_DIR),
            "GOTAcc Project Files (*.json);;All Files (*)",
        )
        if not path:
            return
        try:
            self.load_task_draft(path)
        except Exception as exc:
            self.view.log_warning(f"Open project failed: {exc}")
            QMessageBox.critical(self.window, "Open Project Failed", str(exc))

    def save_project(self) -> None:
        task = self.view.current_task()
        GUI_PROJECT_DIR.mkdir(parents=True, exist_ok=True)
        default_path = GUI_PROJECT_DIR / f"{task['task_name']}_project.json"
        path, _ = QFileDialog.getSaveFileName(
            self.window,
            "Save Project",
            str(default_path),
            "GOTAcc Project Files (*.json);;All Files (*)",
        )
        if not path:
            return
        if not path.lower().endswith(".json"):
            path = f"{path}.json"
        TaskService.export_task_json(task, path)
        self.view.log_console(f"Project saved to: {path}")
        self.view.status_message(f"Project saved: {path}", 5000)
        self.view.append_overview_activity("Project", status=f"Saved project to {Path(path).name}.")

    def load_task_draft(self, path: str | Path) -> None:
        draft_path = Path(path)
        with open(draft_path, "r", encoding="utf-8") as f:
            task = json.load(f)
        self.apply_task_payload(
            task,
            source_label=f"Draft loaded: {draft_path}",
            goto_builder=True,
        )
