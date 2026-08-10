from __future__ import annotations

import io
import time
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot


class WorkerStopRequested(Exception):
    """Internal control-flow exception for graceful GUI stop."""


class _SignalStream(io.TextIOBase):
    """Redirect optimizer stdout/stderr to GUI log signals."""

    def __init__(self, emit_fn) -> None:
        super().__init__()
        self._emit_fn = emit_fn
        self._buffer = ""

    def write(self, s: str) -> int:
        if not s:
            return 0
        self._buffer += s
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.rstrip()
            if line:
                self._emit_fn(line)
        return len(s)

    def flush(self) -> None:
        if self._buffer.strip():
            self._emit_fn(self._buffer.strip())
        self._buffer = ""


class EngineWorker(QObject):
    """GUI worker backed by GOTAcc runner components.

    Current scope
    -------------
    - Offline and online TaskConfig execution
    - Real GOTAcc backend / optimizer construction
    - Live evaluation callbacks from the backend wrapper to the GUI

    Not yet covered here
    --------------------
    - Dedicated progress callbacks from every optimizer internals
    - Full pause/stop responsiveness during long GP fitting sections
    """

    sig_log = pyqtSignal(str)
    sig_warning = pyqtSignal(str)
    sig_status = pyqtSignal(dict)
    sig_evaluation = pyqtSignal(dict)
    sig_finished = pyqtSignal(dict)
    sig_error = pyqtSignal(str)

    def __init__(self, task: Dict[str, Any], parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.task = task
        self._stop_requested = False
        self._pause_requested = False
        self._is_running = False

        self._start_ts = 0.0
        self._eval_count = 0
        self._feasible_count = 0
        self._best_value: Optional[float] = None
        self._best_x: Optional[np.ndarray] = None
        self._single_objective = True
        self._variable_names: List[str] = []
        self._task_name = str(task.get("task_name", "untitled_task"))
        self._algorithm = str(task.get("algorithm", "BO"))
        self._mode = str(task.get("mode", "Offline"))

    # ------------------------------------------------------------------
    # Main execution
    # ------------------------------------------------------------------
    @pyqtSlot()
    def run(self) -> None:
        backend = None
        task_cfg = None
        try:
            from gotacc.runners.task_runner import (
                build_optimizer,
                close_backend_if_possible,
                plot_convergence_if_possible,
                resolve_bounds,
                restore_initial_if_possible,
                save_history_if_possible,
                set_best_if_possible,
                validate_optimizer_backend_match,
            )
            from gotacc.interfaces.factory import build_backend

            try:
                from ..services.task_service import TaskService
            except ImportError:
                from task_service import TaskService

            self._is_running = True
            self._start_ts = time.time()
            self.sig_log.emit(
                f"Preparing real GOTAcc run for task '{self._task_name}' ({self._mode}, {self._algorithm})."
            )
            task_cfg = TaskService.build_task_config(self.task)
            TaskService.ensure_runtime_directories(task_cfg)
            validate_optimizer_backend_match(task_cfg)

            backend_task_cfg = TaskService.make_backend_build_ready_config(task_cfg)
            backend = build_backend(backend_task_cfg)
            x0 = np.asarray(backend.init_knob_value(), dtype=float).reshape(-1)
            bounds = resolve_bounds(task_cfg, x0)
            self._variable_names = list(task_cfg.backend.kwargs.get("variable_names", [])) or [f"x{i}" for i in range(len(x0))]
            self._constraint_bounds = list(task_cfg.optimizer.kwargs.get("constraint_bounds", []) or [])

            objective_count = max(1, len(TaskService._enabled_rows(self.task.get("objectives", []))))
            optimizer_name = str(task_cfg.optimizer.name).lower()
            self._single_objective = optimizer_name in {
                "bo",
                "bayesopt",
                "bayesian_optimization",
                "consbo",
                "constrained_bo",
                "constrained_bayesian_optimization",
                "turbo",
                "trust_region_bo",
                "rcds",
                "smggpo",
                "mggpo_so",
                "single_objective_mggpo",
                "single_objective_mg-gpo",
                "consmggpo_so",
                "constrained_mggpo_so",
                "single_objective_consmggpo",
                "single_objective_constrained_mggpo",
                "single_objective_constrained_mg-gpo",
            }
            constrained_optimizers = {
                "consbo",
                "constrained_bo",
                "constrained_bayesian_optimization",
                "consmggpo_so",
                "constrained_mggpo_so",
                "single_objective_consmggpo",
                "single_objective_constrained_mggpo",
                "single_objective_constrained_mg-gpo",
                "consmobo",
                "constrained_mobo",
                "constrained_multi_objective_bo",
                "consmggpo",
                "constrained_mggpo",
                "constrained_mg-gpo",
            }

            self.sig_log.emit(f"TaskConfig ready: optimizer={task_cfg.optimizer.name}, backend={task_cfg.backend.type}")
            self.sig_log.emit(f"Resolved bounds shape: {bounds.shape}")
            self.sig_status.emit(
                {
                    "state": "Running",
                    "elapsed_seconds": 0,
                    "eval_count": 0,
                    "best_value": None,
                    "feasibility_ratio": None,
                    "initial_x": self._x_to_dict(x0),
                }
            )

            evaluate_fn = backend.evaluate
            if optimizer_name in constrained_optimizers:
                if not hasattr(backend, "evaluate_with_constraints"):
                    raise TypeError("Constrained optimizer requires backend.evaluate_with_constraints().")
                evaluate_fn = backend.evaluate_with_constraints
            wrapped_callable = self._make_objective_wrapper(evaluate_fn)
            optimizer = build_optimizer(task_cfg=task_cfg, objective_callable=wrapped_callable, bounds=bounds)

            out_stream = _SignalStream(self.sig_log.emit)
            try:
                with redirect_stdout(out_stream), redirect_stderr(out_stream):
                    optimize_result = optimizer.optimize()
            except WorkerStopRequested:
                if task_cfg.runtime.restore_initial_on_keyboard_interrupt:
                    restore_initial_if_possible(backend, verbose=False)
                self.sig_finished.emit(
                    {
                        "state": "Aborted",
                        "elapsed_seconds": int(time.time() - self._start_ts),
                        "eval_count": self._eval_count,
                        "best_value": self._best_value,
                        "best_x": self._best_x_dict(),
                    }
                )
                return

            history_path = None
            plot_path = None
            if task_cfg.runtime.save_history:
                history_path = task_cfg.runtime.history_path
                with redirect_stdout(out_stream), redirect_stderr(out_stream):
                    save_history_if_possible(optimizer, history_path=history_path, verbose=False)
            if task_cfg.runtime.plot_convergence:
                plot_path = task_cfg.runtime.plot_path
                with redirect_stdout(out_stream), redirect_stderr(out_stream):
                    plot_convergence_if_possible(optimizer, plot_path=plot_path, verbose=False)
            if task_cfg.runtime.set_best:
                with redirect_stdout(out_stream), redirect_stderr(out_stream):
                    set_best_if_possible(backend, optimizer=optimizer, verbose=False)

            pareto_x = None
            pareto_y = None
            pareto_feasible = None
            pareto_constraints = None
            hypervolume_history = None
            if not self._single_objective:
                if hasattr(optimizer, "get_pareto_front"):
                    try:
                        pareto_x, pareto_y = optimizer.get_pareto_front()
                    except Exception:
                        pareto_x, pareto_y = None, None
                if pareto_x is not None:
                    pareto_feasible, pareto_constraints = self._pareto_metadata(optimizer, pareto_x)
                hv_raw = getattr(optimizer, "hypervolume_history", None)
                if hv_raw is not None:
                    try:
                        hypervolume_history = np.asarray(hv_raw, dtype=float).reshape(-1).tolist()
                    except Exception:
                        hypervolume_history = None

            payload = {
                "state": "Finished",
                "elapsed_seconds": int(time.time() - self._start_ts),
                "eval_count": self._eval_count,
                "best_value": self._best_value,
                "best_x": self._best_x_dict(),
                "history_path": history_path,
                "plot_path": plot_path,
                "optimize_result": optimize_result,
                "pareto_x": None if pareto_x is None else np.asarray(pareto_x, dtype=float).tolist(),
                "pareto_y": None if pareto_y is None else np.asarray(pareto_y, dtype=float).tolist(),
                "pareto_feasible": pareto_feasible,
                "pareto_constraints": pareto_constraints,
                "hypervolume_history": hypervolume_history,
            }
            self.sig_finished.emit(payload)
        except Exception as exc:  # pragma: no cover - runtime protection
            error_message = str(exc)
            if (
                task_cfg is not None
                and backend is not None
                and getattr(task_cfg.runtime, "restore_initial_on_error", False)
            ):
                try:
                    restore_initial_if_possible(backend, verbose=False)
                    self.sig_warning.emit("Run failed; initial machine state was restored.")
                except Exception as restore_exc:
                    restore_message = f"Restore initial failed after run error: {restore_exc}"
                    self.sig_warning.emit(restore_message)
                    error_message = f"{error_message}\n{restore_message}"
            self.sig_error.emit(error_message)
        finally:
            try:
                if backend is not None:
                    from gotacc.runners.task_runner import close_backend_if_possible
                    close_backend_if_possible(backend)
            except Exception:
                pass
            self._is_running = False

    # ------------------------------------------------------------------
    # Control slots
    # ------------------------------------------------------------------
    @pyqtSlot()
    def request_pause(self) -> None:
        self._pause_requested = True
        self.sig_log.emit("Pause requested.")

    @pyqtSlot()
    def request_resume(self) -> None:
        self._pause_requested = False
        self.sig_log.emit("Resume requested.")

    @pyqtSlot()
    def request_stop(self) -> None:
        self._stop_requested = True
        self.sig_log.emit("Stop requested.")

    # ------------------------------------------------------------------
    # Objective wrapper / live reporting
    # ------------------------------------------------------------------
    def _make_objective_wrapper(self, evaluate_fn):
        def wrapped(x):
            arr = np.asarray(x, dtype=float)
            if arr.ndim == 1:
                return self._evaluate_one(arr, evaluate_fn)

            outputs = [self._evaluate_one(row, evaluate_fn) for row in arr]
            return self._stack_outputs(outputs)

        return wrapped

    def _wait_if_paused(self) -> None:
        while self._pause_requested and not self._stop_requested:
            self.sig_status.emit(
                {
                    "state": "Paused",
                    "elapsed_seconds": int(time.time() - self._start_ts),
                    "eval_count": self._eval_count,
                    "best_value": self._best_value,
                    "feasibility_ratio": self._safe_feasibility_ratio(),
                }
            )
            time.sleep(0.2)

    def _evaluate_one(self, x: np.ndarray, evaluate_fn):
        self._wait_if_paused()
        if self._stop_requested:
            raise WorkerStopRequested()

        raw = evaluate_fn(np.asarray(x, dtype=float))
        normalized = self._normalize_output(raw)

        self._eval_count += 1
        if normalized["feasible"]:
            self._feasible_count += 1

        objective_values = normalized["objective_values"]
        scalar_for_display = objective_values[0] if objective_values.size > 0 else None
        best_changed = False
        if self._single_objective and scalar_for_display is not None and normalized["feasible"]:
            current = float(scalar_for_display)
            if self._best_value is None or current > self._best_value:
                self._best_value = current
                self._best_x = np.asarray(x, dtype=float).copy()
                best_changed = True

        payload = {
            "eval_id": self._eval_count,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": normalized["status"],
            "x_values": self._x_to_dict(x),
            "objective_value": float(scalar_for_display) if scalar_for_display is not None else None,
            "objective_values": objective_values.tolist(),
            "objective_summary": normalized["objective_summary"],
            "best_value": self._best_value,
            "constraint_summary": normalized["constraint_summary"],
            "feasibility_ratio": self._safe_feasibility_ratio(after_increment=True),
            "best_changed": best_changed,
        }
        self.sig_evaluation.emit(payload)
        self.sig_status.emit(
            {
                "state": "Running",
                "elapsed_seconds": int(time.time() - self._start_ts),
                "eval_count": self._eval_count,
                "best_value": self._best_value,
                "feasibility_ratio": self._safe_feasibility_ratio(),
            }
        )
        return raw

    def _normalize_output(self, raw: Any) -> Dict[str, Any]:
        if isinstance(raw, dict):
            obj = np.asarray(raw.get("objectives", []), dtype=float).reshape(-1)
            cons = np.asarray(raw.get("constraints", []), dtype=float).reshape(-1)
            status = str(raw.get("status", "ok"))
            feasible = bool(raw.get("feasible", np.all(cons <= 0) if cons.size else True))
        elif isinstance(raw, (tuple, list)) and len(raw) == 2:
            obj = np.asarray(raw[0], dtype=float).reshape(-1)
            cons = np.asarray(raw[1], dtype=float).reshape(-1)
            feasible = self._constraints_feasible(cons)
            status = "ok" if feasible else "infeasible"
        else:
            arr = np.asarray(raw, dtype=float)
            if arr.ndim == 0:
                obj = np.array([float(arr)], dtype=float)
            elif arr.ndim == 1:
                obj = arr.astype(float)
            elif arr.ndim == 2 and arr.shape[0] == 1:
                obj = arr.reshape(-1).astype(float)
            else:
                # This path is not expected in _evaluate_one, but keep it safe.
                obj = arr.reshape(-1).astype(float)
            cons = np.zeros((0,), dtype=float)
            status = "ok"
            feasible = True

        objective_summary = ", ".join(f"f{i}={v:.6f}" for i, v in enumerate(obj)) if obj.size else "--"
        if cons.size:
            constraint_summary = ", ".join(f"c{i}={v:.6f}" for i, v in enumerate(cons))
        else:
            constraint_summary = "--"

        return {
            "objective_values": obj,
            "status": status,
            "feasible": feasible,
            "objective_summary": objective_summary,
            "constraint_summary": constraint_summary,
        }

    def _constraints_feasible(self, cons: np.ndarray) -> bool:
        cons = np.asarray(cons, dtype=float).reshape(-1)
        bounds = getattr(self, "_constraint_bounds", []) or []
        if cons.size == 0 or not bounds:
            return True
        feasible = True
        for idx, value in enumerate(cons):
            if idx >= len(bounds):
                break
            lower, upper = bounds[idx]
            if lower is not None and value < float(lower):
                feasible = False
            if upper is not None and value > float(upper):
                feasible = False
        return feasible

    def _stack_outputs(self, outputs: List[Any]) -> Any:
        if not outputs:
            return np.array([])
        first = outputs[0]
        if isinstance(first, (tuple, list)) and len(first) == 2:
            obj_list = []
            cons_list = []
            for out in outputs:
                norm = self._normalize_output(out)
                obj_list.append(norm["objective_values"])
                cons = np.asarray(out[1], dtype=float).reshape(-1)
                cons_list.append(cons)
            return (
                np.vstack([np.asarray(v, dtype=float).reshape(1, -1) for v in obj_list]),
                np.vstack([np.asarray(v, dtype=float).reshape(1, -1) for v in cons_list]),
            )
        if isinstance(first, dict):
            obj_list = []
            cons_list = []
            status_list = []
            feasible_list = []
            for out in outputs:
                norm = self._normalize_output(out)
                obj_list.append(norm["objective_values"])
                status_list.append(norm["status"])
                feasible_list.append(norm["feasible"])
            return {
                "objectives": np.vstack([np.asarray(v, dtype=float).reshape(1, -1) for v in obj_list]),
                "constraints": np.zeros((len(outputs), 0), dtype=float),
                "status": status_list,
                "feasible": np.asarray(feasible_list, dtype=bool),
            }

        arrs = []
        for out in outputs:
            arr = np.asarray(out, dtype=float)
            if arr.ndim == 0:
                arrs.append(np.array([float(arr)], dtype=float))
            else:
                arrs.append(arr.reshape(-1).astype(float))
        if len(arrs[0]) == 1:
            return np.asarray([a[0] for a in arrs], dtype=float).reshape(-1, 1)
        return np.vstack(arrs)

    def _pareto_metadata(self, optimizer: Any, pareto_x: Any) -> tuple[list[bool] | None, list[list[float]] | None]:
        Xp = np.asarray(pareto_x, dtype=float)
        if Xp.size == 0:
            return [], []
        if Xp.ndim == 1:
            Xp = Xp.reshape(1, -1)

        feasible = np.ones(Xp.shape[0], dtype=bool)
        constraints: list[list[float]] = [[] for _ in range(Xp.shape[0])]

        hist_X_raw = getattr(optimizer, "history_X", None)
        if hist_X_raw is None:
            return feasible.tolist(), constraints

        hist_X = np.asarray(hist_X_raw, dtype=float)
        if hist_X.ndim == 1:
            hist_X = hist_X.reshape(1, -1)
        if hist_X.size == 0 or hist_X.shape[1] != Xp.shape[1]:
            return feasible.tolist(), constraints

        hist_C_raw = getattr(optimizer, "history_C", None)
        hist_C = None
        if hist_C_raw is not None:
            hist_C = np.asarray(hist_C_raw, dtype=float)
            if hist_C.ndim == 1:
                hist_C = hist_C.reshape(-1, 1)
            if hist_C.shape[0] != hist_X.shape[0] or hist_C.shape[1] == 0:
                hist_C = None

        hist_feasible = None
        if hasattr(optimizer, "_get_feasible_mask") and hist_C is not None:
            try:
                hist_feasible = np.asarray(optimizer._get_feasible_mask(hist_C), dtype=bool).reshape(-1)
            except Exception:
                hist_feasible = None
        if hist_feasible is None:
            raw_feasible = getattr(optimizer, "history_feasible", None)
            if raw_feasible is not None:
                hist_feasible = np.asarray(raw_feasible, dtype=bool).reshape(-1)
        if hist_feasible is not None and hist_feasible.shape[0] != hist_X.shape[0]:
            hist_feasible = None

        for i, x in enumerate(Xp):
            matches = np.where(
                np.all(np.isclose(hist_X, x.reshape(1, -1), rtol=1e-7, atol=1e-9), axis=1)
            )[0]
            if matches.size == 0:
                continue
            idx = int(matches[-1])
            if hist_feasible is not None:
                feasible[i] = bool(hist_feasible[idx])
            if hist_C is not None:
                constraints[i] = hist_C[idx].astype(float).reshape(-1).tolist()

        return feasible.tolist(), constraints

    def _x_to_dict(self, x: np.ndarray) -> Dict[str, float]:
        arr = np.asarray(x, dtype=float).reshape(-1)
        names = self._variable_names or [f"x{i}" for i in range(len(arr))]
        return {name: float(val) for name, val in zip(names, arr)}

    def _best_x_dict(self) -> Optional[Dict[str, float]]:
        if self._best_x is None:
            return None
        return self._x_to_dict(self._best_x)

    def _safe_feasibility_ratio(self, after_increment: bool = False) -> Optional[float]:
        denom = self._eval_count + (1 if after_increment else 0)
        if denom <= 0:
            return None
        return self._feasible_count / denom
