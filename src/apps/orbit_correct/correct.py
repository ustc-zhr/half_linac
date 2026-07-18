from __future__ import annotations

import time
import logging
import numpy as np
import sys
from pathlib import Path

_REPO_BOOTSTRAP_ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / "repo_bootstrap.py").is_file()
)
if str(_REPO_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_BOOTSTRAP_ROOT))

from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

from typing import List, Optional
from epics import PV, caget_many, caput
from scipy.linalg import svd
from half_linac.src.shared.machine_profile import (
    load_app_context,
    require_workflow_write_allowed,
    resolve_channel,
    resolve_corrector_write_channel,
)
from half_linac.src.apps.orbit_correct.profile_runtime import (
    CORRECT_LOG_PATH,
    display_unit,
    load_orbit_runtime_settings,
    resolve_active_response_matrix,
)


# configure the correction log
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(CORRECT_LOG_PATH),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

MIN_RESPONSE = 1e-12
CORRECTOR_EPS = 1e-12
LOCAL_RESPONSE_MEASURE_LIVE = "measure_live"
LOCAL_RESPONSE_ACTIVE_MATRIX = "active_matrix"
LOCAL_RESPONSE_SOURCES = {
    LOCAL_RESPONSE_MEASURE_LIVE,
    LOCAL_RESPONSE_ACTIVE_MATRIX,
}


def _parse_target_arg(arg: str, scale: float = 1.0) -> List[float]:
    values = [item for item in arg.split(',') if item]
    return [float(value) * scale for value in values]


def _optional_float_arg(argv: list[str], index: int) -> float | None:
    return float(argv[index]) if len(argv) > index and argv[index] else None


def _optional_int_arg(argv: list[str], index: int) -> int | None:
    return int(argv[index]) if len(argv) > index and argv[index] else None


def _optional_csv_arg(argv: list[str], index: int) -> List[str] | None:
    if len(argv) <= index or not argv[index].strip():
        return None
    return [item.strip() for item in argv[index].split(',') if item.strip()]

class OrbitCorrector:
    """
    support two correction methods: 1. one-to-one 2. global
    
    参数:
        timer_interval: 采样间隔时间(s)
        cor_accuracy: 校正精度(m)
        samples_perstep: 每次采样次数
        target_BPMlist: 目标BPM列表
        target_BPMx_values: 目标X轨道值列表(m)
        target_BPMy_values: 目标Y轨道值列表(m)
    """
    
    def __init__(self, 
                sample_interval: Optional[float] = None,
                cor_accuracy: Optional[float] = None,
                samples_perstep: Optional[int] = None,
                target_BPMlist: Optional[List[str]] = None,
                target_BPMx_values: Optional[List[float]] = None,
                target_BPMy_values: Optional[List[float]] = None,
                corrector_limit: Optional[float] = None,
                global_max_iter: Optional[int] = None,
                one_to_one_max_iter: Optional[int] = None,
                correction_gain: Optional[float] = None,
                correction_max_step_fraction: Optional[float] = None,
                response_kick: Optional[float] = None,
                global_xcor_list: Optional[List[str]] = None,
                global_ycor_list: Optional[List[str]] = None,
                correction_settle_s: Optional[float] = None,
                local_response_source: Optional[str] = None,
                svd_relative_cutoff: Optional[float] = None):
        self.app_context = load_app_context("orbit_correct")
        self.machine_profile = self.app_context.profile
        self.orbit_workflow = self.app_context.orbit_workflow
        self.machine_mode = self.app_context.control_backend.name
        self.orbit_runtime = load_orbit_runtime_settings(self.app_context)
        self.runtime_defaults = self.orbit_runtime["runtime_defaults"]
        self.bpm_position_scale_to_m = self.orbit_runtime["bpm_position_scale_to_m"]
        self.correction_settle_s = self._select_nonnegative_float(
            correction_settle_s,
            float(self.orbit_runtime["correction_settle_s"]),
            "correction_settle_s",
        )
        self.svd_relative_cutoff = self._select_fraction(
            svd_relative_cutoff,
            float(self.orbit_runtime["svd_relative_cutoff"]),
            "svd_relative_cutoff",
        )
        self.local_response_source = self._select_local_response_source(
            local_response_source,
            str(self.runtime_defaults["local_response_source"]),
        )

        # constant definition
        self.response_matrix_path: Path | None = None
        self.corrector_state_path = Path(self.orbit_runtime["corrector_state_path"])
        self.profile_max_value = self.orbit_runtime["corrector_upperlimit"]
        self.max_value_unit = display_unit(self.orbit_runtime["corrector_upperlimit_unit"])
        self.max_value = self._select_corrector_limit(corrector_limit)
        self.d_value = self._select_response_kick(response_kick)
        self.global_max_iter = self._select_positive_int(
            global_max_iter,
            int(self.runtime_defaults["global_max_iter"]),
            "global_max_iter",
        )
        self.one_to_one_max_iter = self._select_positive_int(
            one_to_one_max_iter,
            int(self.runtime_defaults["one_to_one_max_iter"]),
            "one_to_one_max_iter",
        )
        self.correction_gain = self._select_fraction(
            correction_gain,
            float(self.runtime_defaults["correction_gain"]),
            "correction_gain",
        )
        self.correction_max_step_fraction = self._select_fraction(
            correction_max_step_fraction,
            float(self.runtime_defaults["correction_max_step_pct"]) / 100.0,
            "correction_max_step_fraction",
        )
        
        # all cor and bpm lists
        if self.orbit_workflow is None:
            raise ValueError("Orbit workflow is not available in the current app context.")
        self.cor_x_list_all = list(self.orbit_workflow.xcors)
        self.cor_y_list_all = list(self.orbit_workflow.ycors)
        self.bpm_list_all = list(self.orbit_workflow.bpms)
        self.N_BPM = len(self.bpm_list_all)
        self.N_COR = len(self.cor_x_list_all)
        self.global_xcor_list, self.global_xcor_indices = self._select_global_correctors(
            global_xcor_list,
            self.cor_x_list_all,
            "X",
        )
        self.global_ycor_list, self.global_ycor_indices = self._select_global_correctors(
            global_ycor_list,
            self.cor_y_list_all,
            "Y",
        )
        
        # parameter initialization
        self.sample_interval = sample_interval
        self.cor_accuracy = cor_accuracy
        self.samples_perstep = samples_perstep
        
        # 初始化目标设备列表
        if target_BPMlist:
            index_map = {name: idx for idx, name in enumerate(self.bpm_list_all)}
            missing = [name for name in target_BPMlist if name not in index_map]
            if missing:
                raise ValueError(f"Unknown target BPMs: {', '.join(missing)}")
            self.target_indices = [index_map[name] for name in target_BPMlist]
            self.bpm_list_target = list(target_BPMlist)
            self.cor_x_list_target = [self.cor_x_list_all[idx] for idx in self.target_indices]
            self.cor_y_list_target = [self.cor_y_list_all[idx] for idx in self.target_indices]
            self.target_BPMx_values = target_BPMx_values
            self.target_BPMy_values = target_BPMy_values
        else:
            self.target_indices = []
            self.bpm_list_target = []
            self.cor_x_list_target = []
            self.cor_y_list_target = []
            self.target_BPMx_values = []
            self.target_BPMy_values = []

    def _select_corrector_limit(self, override: Optional[float]) -> float:
        value = self.profile_max_value if override is None else float(override)
        if value <= 0:
            raise ValueError("corrector_limit must be greater than 0.")
        if value > self.profile_max_value:
            raise ValueError(
                f"corrector_limit {value:g} {self.max_value_unit} exceeds profile limit "
                f"{self.profile_max_value:g} {self.max_value_unit}."
            )
        return value

    def _select_response_kick(self, override: Optional[float]) -> float:
        if override is None:
            value = self.max_value * float(self.runtime_defaults["local_response_kick_fraction"])
        else:
            value = float(override)
        if value <= 0:
            raise ValueError("response_kick must be greater than 0.")
        if value > self.max_value:
            raise ValueError("response_kick cannot exceed corrector_limit.")
        return value

    @staticmethod
    def _select_positive_int(value: Optional[int], default: int, label: str) -> int:
        selected = default if value is None else int(value)
        if selected <= 0:
            raise ValueError(f"{label} must be greater than 0.")
        return selected

    @staticmethod
    def _select_fraction(value: Optional[float], default: float, label: str) -> float:
        selected = default if value is None else float(value)
        if selected <= 0 or selected > 1:
            raise ValueError(f"{label} must be in the range (0, 1].")
        return selected

    @staticmethod
    def _select_nonnegative_float(
        value: Optional[float],
        default: float,
        label: str,
    ) -> float:
        selected = default if value is None else float(value)
        if selected < 0:
            raise ValueError(f"{label} must be >= 0.")
        return selected

    @staticmethod
    def _select_local_response_source(value: Optional[str], default: str) -> str:
        selected = default if value is None else str(value)
        selected = selected.strip().lower()
        if selected not in LOCAL_RESPONSE_SOURCES:
            raise ValueError(
                "local_response_source must be one of: "
                + ", ".join(sorted(LOCAL_RESPONSE_SOURCES))
            )
        return selected

    @staticmethod
    def _select_global_correctors(
        requested: Optional[List[str]],
        available: List[str],
        plane: str,
    ) -> tuple[List[str], List[int]]:
        if requested is None:
            return list(available), list(range(len(available)))

        selected = []
        seen = set()
        for name in requested:
            if name not in seen:
                selected.append(name)
                seen.add(name)
        if not selected:
            raise ValueError(f"Select at least one {plane} corrector for global correction.")

        index_map = {name: idx for idx, name in enumerate(available)}
        missing = [name for name in selected if name not in index_map]
        if missing:
            raise ValueError(f"Unknown {plane} corrector(s): {', '.join(missing)}")
        return selected, [index_map[name] for name in selected]

    def _bpm_pv(self, bpm_name: str, plane: str) -> str:
        return resolve_channel(self.app_context, bpm_name, plane)

    def _cor_pv(self, cor_name: str) -> str:
        return resolve_corrector_write_channel(self.app_context, cor_name)

    def _find_positions(self, main_list: List[str], sub_list: List[str]) -> List[int]:
        """在设备列表中查找目标设备的位置索引"""
        return [i for i, elem in enumerate(main_list) if elem in sub_list]

    def _require_targets(self) -> None:
        if not self.bpm_list_target:
            raise ValueError("No target BPMs selected for orbit correction.")

        if len(self.target_BPMx_values) != len(self.bpm_list_target):
            raise ValueError("Target BPM X values do not match the selected BPM list.")

        if len(self.target_BPMy_values) != len(self.bpm_list_target):
            raise ValueError("Target BPM Y values do not match the selected BPM list.")

    def _require_write_allowed(self, operation: str) -> None:
        require_workflow_write_allowed(self.app_context, "orbit", operation)

    @staticmethod
    def _validate_values(values, expected_count: int, label: str) -> np.ndarray:
        if values is None:
            raise ValueError(f"Failed to read {label}.")
        if len(values) != expected_count:
            raise ValueError(
                f"{label} read count mismatch: got {len(values)}, expected {expected_count}."
            )
        if any(value is None for value in values):
            raise ValueError(f"{label} contains unreadable PV values.")
        array = np.asarray(values, dtype=float)
        if not np.all(np.isfinite(array)):
            raise ValueError(f"{label} contains NaN or Inf.")
        return array

    def _read_many_pvs(self, pv_names: List[str], label: str) -> np.ndarray:
        return self._validate_values(caget_many(pv_names), len(pv_names), label)

    @staticmethod
    def _read_pv(pv: PV, label: str) -> float:
        value = pv.get()
        if value is None:
            raise ValueError(f"Failed to read {label}: {pv.pvname}")
        value = float(value)
        if not np.isfinite(value):
            raise ValueError(f"{label} returned NaN or Inf: {pv.pvname}")
        return value

    @staticmethod
    def _write_pv(pv: PV, value: float, label: str) -> None:
        status = pv.put(float(value), wait=True, timeout=2.0)
        if status is False:
            raise ValueError(f"Failed to write {label}: {pv.pvname} = {value:g}")

    @staticmethod
    def _write_many_pvs(pv_names: List[str], values, label: str) -> None:
        array = np.asarray(values, dtype=float)
        if len(pv_names) != len(array):
            raise ValueError(
                f"{label} write count mismatch: got {len(array)} values for {len(pv_names)} PVs."
            )
        if not np.all(np.isfinite(array)):
            raise ValueError(f"{label} write values contain NaN or Inf.")
        for pv_name, value in zip(pv_names, array):
            status = caput(pv_name, float(value), wait=True, timeout=2.0)
            if not status:
                raise ValueError(f"Failed to write {label}: {pv_name} = {value:g}")
    
    
    def init_BPM_pv(self) -> None:
        """初始化BPM的PV连接"""
        self.pvBPMx = []
        self.pvBPMy = []
        self.pvnameBPMx = []
        self.pvnameBPMy = []
        for bpm in self.bpm_list_target:
            bpm_x_pv = self._bpm_pv(bpm, "x")
            bpm_y_pv = self._bpm_pv(bpm, "y")
            self.pvBPMx.append(PV(bpm_x_pv))
            self.pvBPMy.append(PV(bpm_y_pv))
            self.pvnameBPMx.append(bpm_x_pv)
            self.pvnameBPMy.append(bpm_y_pv)

    def init_COR_pv(self) -> None:
        """初始化校正铁的PV连接"""
        self.pvCORx = []
        self.pvCORy = []
        self.pvnameCORx = []
        self.pvnameCORy = []
        for cor in self.cor_x_list_target:
            cor_pv = self._cor_pv(cor)
            self.pvCORx.append(PV(cor_pv))
            self.pvnameCORx.append(cor_pv)
        for cor in self.cor_y_list_target:
            cor_pv = self._cor_pv(cor)
            self.pvCORy.append(PV(cor_pv))
            self.pvnameCORy.append(cor_pv)
    
    def save_origin_cor(self) -> None:
        all_pvname_corx = [self._cor_pv(corx) for corx in self.cor_x_list_all]
        all_pvname_cory = [self._cor_pv(cory) for cory in self.cor_y_list_all]

        expected_count = len(self.cor_x_list_all)
        hcor_array = self._validate_values(
            caget_many(all_pvname_corx),
            expected_count,
            "X corrector backup",
        )
        vcor_array = self._validate_values(
            caget_many(all_pvname_cory),
            expected_count,
            "Y corrector backup",
        )
        
        self.corrector_state_path.parent.mkdir(parents=True, exist_ok=True)
        with self.corrector_state_path.open('w', encoding='utf-8') as file:
            for item1, item2 in zip(hcor_array, vcor_array):
                file.write(f"{item1}\t{item2}\n")



    def _get_avg_readings(self, pv_list: List[PV], *, bpm_count: int = 0) -> List[float]:
        """同时获取多个PV的多次采样平均值"""
        if not pv_list or self.samples_perstep is None or self.samples_perstep <= 0:
            return [0.0] * len(pv_list)
        if bpm_count < 0 or bpm_count > len(pv_list):
            raise ValueError("bpm_count must match the leading BPM PV values.")

        results = [0.0] * len(pv_list)
        
        for sample_index in range(self.samples_perstep):
            if sample_index:
                time.sleep(self.sample_interval)
            for i, pv in enumerate(pv_list):
                results[i] += self._read_pv(pv, "orbit correction sample")
        
        averaged = [r / self.samples_perstep for r in results]
        for index in range(bpm_count):
            averaged[index] *= self.bpm_position_scale_to_m
        return averaged

    def _wait_for_correction_settle(self) -> None:
        if self.correction_settle_s > 0:
            time.sleep(self.correction_settle_s)

    def _max_correction_step(self) -> float:
        return self.max_value * self.correction_max_step_fraction

    def _bounded_correction_delta(self, delta) -> np.ndarray:
        raw_delta = self.correction_gain * np.asarray(delta, dtype=float)
        max_step = self._max_correction_step()
        return np.clip(raw_delta, -max_step, max_step)

    def _bounded_correction_step(self, error: float, response: float) -> float:
        return float(self._bounded_correction_delta(error / response))

    def _clip_corrector(self, value: float) -> float:
        return float(np.clip(value, -self.max_value, self.max_value))

    def correct_one_to_one(self) -> bool:
        """one-to-one method"""
        self._require_write_allowed("One-to-one orbit correction")
        self._require_targets()
        local_response_matrix = None
        if self.local_response_source == LOCAL_RESPONSE_ACTIVE_MATRIX:
            local_response_matrix = self._load_valid_response_matrix()
            logger.info(
                "one-to-one correction uses local responses from active matrix: %s",
                self.response_matrix_path,
            )
        else:
            logger.info("one-to-one correction measures local responses live")
        failures: list[str] = []
        for j in range(len(self.bpm_list_target)):
            logger.info(f"开始校正: {self.bpm_list_target[j]}")
            
            # 同时获取初始值
            xbpm_val0, ybpm_val0, hcorrVal, vcorrVal = self._get_avg_readings([
                self.pvBPMx[j],
                self.pvBPMy[j],
                self.pvCORx[j],
                self.pvCORy[j]
            ], bpm_count=2)

            initial_x_err = abs(self.target_BPMx_values[j] - xbpm_val0)
            initial_y_err = abs(self.target_BPMy_values[j] - ybpm_val0)
            if initial_x_err < self.cor_accuracy and initial_y_err < self.cor_accuracy:
                logger.info(
                    "%s already within one-to-one accuracy: error X=%.3e, Y=%.3e",
                    self.bpm_list_target[j],
                    initial_x_err,
                    initial_y_err,
                )
                continue
            
            if local_response_matrix is not None:
                Rx, Ry = self._local_response_coefficients(
                    local_response_matrix,
                    self.target_indices[j],
                )
            else:
                # 微调并测量响应。X/Y corrector 分开 kick，避免交叉影响响应系数。
                try:
                    self._write_pv(self.pvCORx[j], hcorrVal + self.d_value, "X corrector response kick")
                    self._wait_for_correction_settle()
                    xbpm_val1 = self._get_avg_readings(
                        [self.pvBPMx[j]], bpm_count=1
                    )[0]
                    self._write_pv(self.pvCORx[j], hcorrVal, "X corrector restore")

                    self._write_pv(self.pvCORy[j], vcorrVal + self.d_value, "Y corrector response kick")
                    self._wait_for_correction_settle()
                    ybpm_val1 = self._get_avg_readings(
                        [self.pvBPMy[j]], bpm_count=1
                    )[0]
                finally:
                    self._write_pv(self.pvCORx[j], hcorrVal, "X corrector restore")
                    self._write_pv(self.pvCORy[j], vcorrVal, "Y corrector restore")
                    self._wait_for_correction_settle()

                Rx = (xbpm_val1 - xbpm_val0) / self.d_value
                Ry = (ybpm_val1 - ybpm_val0) / self.d_value
            logger.info(
                "response coefficient (%s): Rx=%.6g, Ry=%.6g",
                self.local_response_source,
                Rx,
                Ry,
            )

            if abs(Rx) < MIN_RESPONSE or abs(Ry) < MIN_RESPONSE:
                logger.warning(
                    "%s response is too small for stable one-to-one correction: Rx=%s, Ry=%s",
                    self.bpm_list_target[j],
                    Rx,
                    Ry,
                )
                failures.append(f"{self.bpm_list_target[j]} response too small")
                continue

            previous_pair: tuple[float, float] | None = None
            converged = False
            x_err = initial_x_err
            y_err = initial_y_err
            for loop in range(1, self.one_to_one_max_iter + 1):
                xbpm_val1, ybpm_val1 = self._get_avg_readings([
                    self.pvBPMx[j],
                    self.pvBPMy[j]
                ], bpm_count=2)

                x_delta = self.target_BPMx_values[j] - xbpm_val1
                y_delta = self.target_BPMy_values[j] - ybpm_val1
                x_err = abs(x_delta)
                y_err = abs(y_delta)

                if x_err < self.cor_accuracy and y_err < self.cor_accuracy:
                    converged = True
                    break

                logger.info(
                    "%s one-to-one iteration %d: error X=%.3e, Y=%.3e, corrector=(%.6g, %.6g)",
                    self.bpm_list_target[j],
                    loop,
                    x_err,
                    y_err,
                    hcorrVal,
                    vcorrVal,
                )

                next_hcorr = self._clip_corrector(hcorrVal + self._bounded_correction_step(x_delta, Rx))
                next_vcorr = self._clip_corrector(vcorrVal + self._bounded_correction_step(y_delta, Ry))

                if previous_pair is not None and (
                    abs(next_hcorr - previous_pair[0]) < CORRECTOR_EPS
                    and abs(next_vcorr - previous_pair[1]) < CORRECTOR_EPS
                ):
                    logger.warning(
                        "%s one-to-one correction stopped: corrector oscillation detected.",
                        self.bpm_list_target[j],
                    )
                    break

                if (
                    abs(next_hcorr - hcorrVal) < CORRECTOR_EPS
                    and abs(next_vcorr - vcorrVal) < CORRECTOR_EPS
                ):
                    logger.warning(
                        "%s one-to-one correction stopped: corrector limit reached without convergence.",
                        self.bpm_list_target[j],
                    )
                    break

                previous_pair = (hcorrVal, vcorrVal)
                hcorrVal = next_hcorr
                vcorrVal = next_vcorr
                self._write_pv(self.pvCORx[j], hcorrVal, "X corrector correction")
                self._write_pv(self.pvCORy[j], vcorrVal, "Y corrector correction")
                self._wait_for_correction_settle()

            if not converged:
                logger.warning(
                    "%s one-to-one correction did not converge within %d iteration(s).",
                    self.bpm_list_target[j],
                    self.one_to_one_max_iter,
                )
                failures.append(
                    f"{self.bpm_list_target[j]} not converged: "
                    f"error X={x_err:.3e}, Y={y_err:.3e}"
                )
            
            logger.info(f"correction finished: {self.bpm_list_target[j]}")
            logger.info(f"corrector: ({hcorrVal:.3f}, {vcorrVal:.3f})")
            logger.info(f"BPM: ({xbpm_val1:.3e}, {ybpm_val1:.3e})")

        final_failures = self._final_orbit_failures()
        failures.extend(final_failures)
        if failures:
            for failure in failures:
                logger.warning("one-to-one final status: %s", failure)
            return False

        logger.info("one-to-one correction reached requested accuracy for all target BPMs.")
        return True

    def _final_orbit_failures(self) -> list[str]:
        bpm_pvs = self.pvBPMx + self.pvBPMy
        xy_vals = self._get_avg_readings(bpm_pvs, bpm_count=len(bpm_pvs))
        length_half = len(xy_vals) // 2
        x_vals = xy_vals[:length_half]
        y_vals = xy_vals[length_half:]
        failures = []
        for bpm, target_x, target_y, x_value, y_value in zip(
            self.bpm_list_target,
            self.target_BPMx_values,
            self.target_BPMy_values,
            x_vals,
            y_vals,
        ):
            x_err = abs(target_x - x_value)
            y_err = abs(target_y - y_value)
            logger.info(
                "one-to-one final check %s: error X=%.3e, Y=%.3e",
                bpm,
                x_err,
                y_err,
            )
            if x_err >= self.cor_accuracy or y_err >= self.cor_accuracy:
                failures.append(f"{bpm} final error X={x_err:.3e}, Y={y_err:.3e}")
        return failures

    def _expected_response_shape(self) -> tuple[int, int]:
        return (2 * self.N_BPM, 2 * self.N_COR)

    def _local_response_coefficients(
        self,
        matrix: np.ndarray,
        target_index: int,
    ) -> tuple[float, float]:
        return (
            float(matrix[target_index, target_index]),
            float(matrix[self.N_BPM + target_index, self.N_COR + target_index]),
        )

    def _load_valid_response_matrix(self) -> np.ndarray:
        expected_shape = self._expected_response_shape()
        matrix_path = resolve_active_response_matrix(self.app_context)
        self.response_matrix_path = matrix_path
        matrix = np.loadtxt(matrix_path)
        if matrix.shape != expected_shape:
            raise ValueError(
                "Response matrix shape mismatch for "
                f"{self.machine_profile.machine.id}/{self.machine_mode}: "
                f"{matrix_path} has shape {matrix.shape}, expected {expected_shape}. "
                "Run Measure Response Matrix for the current machine/backend before using "
                "matrix-based correction."
            )
        return matrix

    def _compute_svd(self, min_singular_value: float | None = None):
        """计算响应矩阵的SVD分解"""
        self._require_targets()
        relative_cutoff = (
            self.svd_relative_cutoff
            if min_singular_value is None
            else self._select_fraction(
                min_singular_value,
                self.svd_relative_cutoff,
                "svd_relative_cutoff",
            )
        )
        RM = self._load_valid_response_matrix()
        selected = np.array(self.target_indices, dtype=int)
        selected_xcors = np.array(self.global_xcor_indices, dtype=int)
        selected_ycors = np.array(self.global_ycor_indices, dtype=int)
        ORM_x_full = RM[0:self.N_BPM, 0:self.N_COR]
        ORM_y_full = RM[self.N_BPM:self.N_BPM * 2, self.N_COR:self.N_COR * 2]
        ORM_x = ORM_x_full[np.ix_(selected, selected_xcors)]
        ORM_y = ORM_y_full[np.ix_(selected, selected_ycors)]
        logger.info(
            "global correction uses %sx%s X and %sx%s Y response submatrices for BPMs: %s; "
            "X correctors: %s; Y correctors: %s",
            ORM_x.shape[0],
            ORM_x.shape[1],
            ORM_y.shape[0],
            ORM_y.shape[1],
            ", ".join(self.bpm_list_target),
            ", ".join(self.global_xcor_list),
            ", ".join(self.global_ycor_list),
        )

        self.pseudo_inverse_x = self._truncated_pseudo_inverse(
            ORM_x,
            relative_cutoff,
            diagnostics_label="X",
        )
        self.pseudo_inverse_y = self._truncated_pseudo_inverse(
            ORM_y,
            relative_cutoff,
            diagnostics_label="Y",
        )

    @staticmethod
    def _truncated_pseudo_inverse(
        matrix: np.ndarray,
        min_singular_value: float,
        *,
        diagnostics_label: str | None = None,
    ) -> np.ndarray:
        U, singular_values, Vt = svd(matrix, full_matrices=False)
        s_inv = np.zeros_like(singular_values)
        if singular_values.size == 0:
            return Vt.T @ np.diag(s_inv) @ U.T
        relative_cutoff = abs(float(min_singular_value)) * np.max(np.abs(singular_values))
        retained = np.abs(singular_values) > relative_cutoff
        if diagnostics_label is not None:
            logger.info(
                "SVD %s: relative cutoff=%.6g (%.4g%%), absolute cutoff=%.6g, "
                "retained modes=%s/%s",
                diagnostics_label,
                min_singular_value,
                min_singular_value * 100.0,
                relative_cutoff,
                int(np.count_nonzero(retained)),
                singular_values.size,
            )
        for i, value in enumerate(singular_values):
            if retained[i]:
                s_inv[i] = 1 / value
        return Vt.T @ np.diag(s_inv) @ U.T

    def _get_avg_readings2(self, pv_list: List[str]) -> List[float]:
        """同时获取多个PV的多次采样平均值"""
        if not pv_list or self.samples_perstep <= 0:
            return [0.0] * len(pv_list)
            
        results = np.zeros(len(pv_list), dtype=float)
        
        for sample_index in range(self.samples_perstep):
            if sample_index:
                time.sleep(self.sample_interval)
            results += (
                self._read_many_pvs(pv_list, "orbit readings")
                * self.bpm_position_scale_to_m
            )
        
        return list(results / self.samples_perstep)
     
    def correct_global(self, max_iter: int | None = None) -> bool:
        """全局校正方法"""
        self._require_write_allowed("Global orbit correction")
        self._require_targets()
        max_iter = self.global_max_iter if max_iter is None else max_iter

        logger.info("global correction uses SVD pseudo-inverse")
        logger.info(
            "correction timing uses settle=%.3f s, sample interval=%.3f s, "
            "samples/step=%d, SVD cutoff=%.6g (%.4g%%)",
            self.correction_settle_s,
            self.sample_interval,
            self.samples_perstep,
            self.svd_relative_cutoff,
            self.svd_relative_cutoff * 100.0,
        )
        self._compute_svd()
        pvname_corx = [self._cor_pv(cor) for cor in self.global_xcor_list]
        pvname_cory = [self._cor_pv(cor) for cor in self.global_ycor_list]

        for iteration in range(0, max_iter):
            # 获取当前轨道数据
            xy_vals = self._get_avg_readings2(self.pvnameBPMx + self.pvnameBPMy)
            length_half = len(xy_vals) // 2
            x_vals = xy_vals[:length_half]
            y_vals = xy_vals[length_half:]
            
            # 计算误差
            x_err = [tx - x for tx, x in zip(self.target_BPMx_values, x_vals)]
            y_err = [ty - y for ty, y in zip(self.target_BPMy_values, y_vals)]
            
            # 检查收敛
            x_err_abs = [abs(e) for e in x_err]
            y_err_abs = [abs(e) for e in y_err]
            max_x_err = max(x_err_abs)
            max_y_err = max(y_err_abs)
            max_x_err_index = int(np.argmax(x_err_abs))
            max_y_err_index = int(np.argmax(y_err_abs))
            x_abs = [abs(x) for x in x_vals]
            y_abs = [abs(y) for y in y_vals]
            max_x_abs_index = int(np.argmax(x_abs))
            max_y_abs_index = int(np.argmax(y_abs))
            
            logger.info(f"iteration {iteration}: max error X={max_x_err:.3e} , Y={max_y_err:.3e}")
            logger.info(
                "iteration %s: max target error X=%s %.3e m (%.3f mm), "
                "Y=%s %.3e m (%.3f mm)",
                iteration,
                self.bpm_list_target[max_x_err_index],
                max_x_err,
                max_x_err * 1e3,
                self.bpm_list_target[max_y_err_index],
                max_y_err,
                max_y_err * 1e3,
            )
            logger.info(
                "iteration %s: max absolute orbit X=%s %.3e m (%.3f mm), "
                "Y=%s %.3e m (%.3f mm)",
                iteration,
                self.bpm_list_target[max_x_abs_index],
                x_vals[max_x_abs_index],
                x_vals[max_x_abs_index] * 1e3,
                self.bpm_list_target[max_y_abs_index],
                y_vals[max_y_abs_index],
                y_vals[max_y_abs_index] * 1e3,
            )

            if max_x_err < self.cor_accuracy and max_y_err < self.cor_accuracy:
                logger.info(f"correction is finished at iteration: {iteration}")
                return True
            
            delt_corrh = self._bounded_correction_delta(
                np.dot(self.pseudo_inverse_x, np.array(x_err))
            )
            delt_corrv = self._bounded_correction_delta(
                np.dot(self.pseudo_inverse_y, np.array(y_err))
            )


            # 应用校正
            hcor_vals = self._read_many_pvs(pvname_corx, "X corrector setpoints")
            vcor_vals = self._read_many_pvs(pvname_cory, "Y corrector setpoints")
            new_hcor = np.clip(
                hcor_vals + delt_corrh,
                -1*self.max_value, 1*self.max_value
            )
            new_vcor = np.clip(
                vcor_vals + delt_corrv,
                -1*self.max_value, 1*self.max_value
            )
            saturated_x = int(np.count_nonzero(np.isclose(np.abs(new_hcor), self.max_value)))
            saturated_y = int(np.count_nonzero(np.isclose(np.abs(new_vcor), self.max_value)))
            if saturated_x or saturated_y:
                logger.warning(
                    "iteration %s: corrector limit reached: X %s/%s, Y %s/%s at +/-%.3e %s",
                    iteration,
                    saturated_x,
                    len(new_hcor),
                    saturated_y,
                    len(new_vcor),
                    self.max_value,
                    self.max_value_unit,
                )
            
            self._write_many_pvs(pvname_corx, new_hcor, "X corrector setpoints")
            self._write_many_pvs(pvname_cory, new_vcor, "Y corrector setpoints")
            self._wait_for_correction_settle()
        
        logger.info(f"reach the max iteration: {max_iter}")
        return False
    
    def reset_cor(self) -> None:
        """Reset the selected correctors, or all of them if no selection is provided."""
        self._require_write_allowed("Corrector reset")
        cor_x_list = self.cor_x_list_target or self.cor_x_list_all
        cor_y_list = self.cor_y_list_target or self.cor_y_list_all

        pv_corx = [self._cor_pv(name) for name in cor_x_list]
        pv_cory = [self._cor_pv(name) for name in cor_y_list]
        values = [0.0] * len(pv_corx)
        self._write_many_pvs(pv_corx, values, "X corrector reset")
        self._write_many_pvs(pv_cory, values, "Y corrector reset") 

    def cor_recover(self) -> None:
        """recvoer all corrector to the value before cor"""
        self._require_write_allowed("Corrector recover")
        cor_path = self.corrector_state_path
        if not cor_path.exists():
            raise FileNotFoundError(f"Corrector backup file not found: {cor_path}")

        cor = np.loadtxt(cor_path, ndmin=2)
        if cor.shape[1] != 2:
            raise ValueError(f"Unexpected corrector backup shape: {cor.shape}")
        expected_count = len(self.cor_x_list_all)
        if cor.shape[0] != expected_count:
            raise ValueError(
                f"Corrector backup row count mismatch: got {cor.shape[0]}, expected {expected_count}."
            )
        if not np.all(np.isfinite(cor)):
            raise ValueError(f"Corrector backup contains NaN or Inf: {cor_path}")

        corx = cor[:, 0]
        cory = cor[:, 1]
        logger.debug("Recovering correctors from %s: X=%s, Y=%s", cor_path, corx, cory)

        self.pvCORx = []
        self.pvCORy = []
        for j in self.cor_x_list_all:  
            pv_CORx = self._cor_pv(j)
            self.pvCORx.append(pv_CORx)  
        for j in self.cor_y_list_all:  
            pv_CORy = self._cor_pv(j)
            self.pvCORy.append(pv_CORy)
        self._write_many_pvs(self.pvCORx, corx, "X corrector recover")
        self._write_many_pvs(self.pvCORy, cory, "Y corrector recover") 

if __name__ == '__main__':
    try:
        logger.info(f"INPUT PARAMETERS: {sys.argv}")
        
        if sys.argv[1] == "start_cor":
            method = sys.argv[2]
            samp_interval = float(sys.argv[3])
            cor_accuracy = float(sys.argv[4]) * 1e-6
            samples_perstep = int(sys.argv[5])
            target_BPMlist = [item for item in sys.argv[6].split(',') if item]
            target_BPMx_values = _parse_target_arg(sys.argv[7], scale=1e-3)
            target_BPMy_values = _parse_target_arg(sys.argv[8], scale=1e-3)
            corrector_limit = _optional_float_arg(sys.argv, 9)
            global_max_iter = _optional_int_arg(sys.argv, 10)
            one_to_one_max_iter = _optional_int_arg(sys.argv, 11)
            correction_gain = _optional_float_arg(sys.argv, 12)
            correction_max_step_fraction = _optional_float_arg(sys.argv, 13)
            response_kick = _optional_float_arg(sys.argv, 14)
            global_xcor_list = _optional_csv_arg(sys.argv, 15)
            global_ycor_list = _optional_csv_arg(sys.argv, 16)
            correction_settle_s = _optional_float_arg(sys.argv, 17)
            local_response_source = sys.argv[18] if len(sys.argv) > 18 else None
            svd_relative_cutoff = _optional_float_arg(sys.argv, 19)
            
            corrector = OrbitCorrector(
                samp_interval, cor_accuracy, samples_perstep,
                target_BPMlist, target_BPMx_values, target_BPMy_values,
                corrector_limit=corrector_limit,
                global_max_iter=global_max_iter,
                one_to_one_max_iter=one_to_one_max_iter,
                correction_gain=correction_gain,
                correction_max_step_fraction=correction_max_step_fraction,
                response_kick=response_kick,
                global_xcor_list=global_xcor_list,
                global_ycor_list=global_ycor_list,
                correction_settle_s=correction_settle_s,
                local_response_source=local_response_source,
                svd_relative_cutoff=svd_relative_cutoff,
            )
            corrector._require_targets()
            corrector.init_BPM_pv()
            corrector.init_COR_pv()
            corrector.save_origin_cor()

            
            if method == "one-to-one":
                if not corrector.correct_one_to_one():
                    sys.exit(2)
            elif method == "global":
                if not corrector.correct_global():
                    sys.exit(2)
            else:
                raise ValueError(f"Unknown correction method: {method}")
        
        elif sys.argv[1] == "cor_off":
            target_BPMlist = [item for item in sys.argv[2].split(',') if item] if len(sys.argv) > 2 else []
            corrector = OrbitCorrector(target_BPMlist=target_BPMlist)
            corrector.reset_cor()
        
        elif sys.argv[1] == "cor_recover":
            # target_BPMlist = sys.argv[2].split(',')
            corrector = OrbitCorrector()
            corrector.cor_recover()
    
    except Exception as e:
        logger.error(f"校正错误: {e}", exc_info=True)
