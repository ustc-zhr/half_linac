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

from typing import List, Optional, Tuple
from epics import caput_many, PV, caget_many
from scipy.linalg import svd
from half_linac.src.shared.machine_profile import (
    load_app_context,
    require_workflow_write_allowed,
    resolve_channel,
)
from half_linac.src.apps.orbit_correct.profile_runtime import (
    CORRECT_LOG_PATH,
    RESPONSE_MATRIX_PATH,
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
                one_to_one_gain: Optional[float] = None,
                one_to_one_max_step_fraction: Optional[float] = None,
                response_kick: Optional[float] = None,
                global_xcor_list: Optional[List[str]] = None,
                global_ycor_list: Optional[List[str]] = None):
        self.app_context = load_app_context("orbit_correct")
        self.machine_profile = self.app_context.profile
        self.orbit_workflow = self.app_context.orbit_workflow
        self.machine_mode = self.app_context.control_backend.name
        self.orbit_runtime = load_orbit_runtime_settings(self.app_context)
        self.runtime_defaults = self.orbit_runtime["runtime_defaults"]

        # constant definition
        self.response_matrix_path: Path | None = None
        self.corrector_state_path = Path(self.orbit_runtime["corrector_state_path"])
        self.profile_max_value = self.orbit_runtime["corrector_upperlimit"]
        self.max_value_unit = self.orbit_runtime["corrector_upperlimit_unit"]
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
        self.one_to_one_gain = self._select_fraction(
            one_to_one_gain,
            float(self.runtime_defaults["one_to_one_gain"]),
            "one_to_one_gain",
        )
        self.one_to_one_max_step_fraction = self._select_fraction(
            one_to_one_max_step_fraction,
            float(self.runtime_defaults["one_to_one_max_step_pct"]) / 100.0,
            "one_to_one_max_step_fraction",
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
        
        
        
        # PV连接
        self.pvBPMx: List[PV] = []
        self.pvBPMy: List[PV] = []
        self.pvCORx: List[PV] = []
        self.pvCORy: List[PV] = []

    def _bpm_pv(self, bpm_name: str, plane: str) -> str:
        return resolve_channel(self.app_context, bpm_name, plane)

    def _cor_pv(self, cor_name: str) -> str:
        return resolve_channel(self.app_context, cor_name, "setpoint")

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
        # print('BPMxPVNAME:', self.pvnameBPMx)

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

        hcor_vals = caget_many(all_pvname_corx)
        vcor_vals = caget_many(all_pvname_cory)
        
        self.corrector_state_path.parent.mkdir(parents=True, exist_ok=True)
        with self.corrector_state_path.open('w', encoding='utf-8') as file:
            for item1, item2 in zip(hcor_vals, vcor_vals):
                file.write(f"{item1}\t{item2}\n")



        
    
       
    # def _get_avg_readings(self, pv_groups: List[List[PV]]) -> List[float]:
    #     """同时获取多组PV的多次采样平均值"""
    #     results = [0.0] * len(pv_groups)
        
    #     for _ in range(self.samples_perstep):
    #         time.sleep(self.sample_interval)
    #         for i, pvs in enumerate(pv_groups):
    #             results[i] += sum(pv.get() for pv in pvs)
        
    #     return [r / (len(pvs) * self.samples_perstep) for r, pvs in zip(results, pv_groups)]
    
    def _get_avg_readings(self, pv_list: List[PV]) -> List[float]:
        """同时获取多个PV的多次采样平均值"""
        if not pv_list or self.samples_perstep is None or self.samples_perstep <= 0:
            return [0.0] * len(pv_list)

        results = [0.0] * len(pv_list)
        
        for _ in range(self.samples_perstep):
            time.sleep(self.sample_interval)
            for i, pv in enumerate(pv_list):
                results[i] += pv.get() 
        
        return [r / self.samples_perstep for r in results]

    def _bounded_correction_step(self, error: float, response: float) -> float:
        raw_step = self.one_to_one_gain * error / response
        max_step = max(self.max_value * self.one_to_one_max_step_fraction, self.d_value)
        return float(np.clip(raw_step, -max_step, max_step))

    def _clip_corrector(self, value: float) -> float:
        return float(np.clip(value, -self.max_value, self.max_value))

    def correct_one_to_one(self) -> bool:
        """one-to-one method"""
        self._require_write_allowed("One-to-one orbit correction")
        self._require_targets()
        failures: list[str] = []
        for j in range(len(self.bpm_list_target)):
            logger.info(f"开始校正: {self.bpm_list_target[j]}")
            
            # 同时获取初始值
            xbpm_val0, ybpm_val0, hcorrVal, vcorrVal = self._get_avg_readings([
                self.pvBPMx[j],
                self.pvBPMy[j],
                self.pvCORx[j],
                self.pvCORy[j]
            ])

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
            
            # 微调并测量响应。X/Y corrector 分开 kick，避免交叉影响响应系数。
            try:
                self.pvCORx[j].put(hcorrVal + self.d_value)
                xbpm_val1 = self._get_avg_readings([self.pvBPMx[j]])[0]
                self.pvCORx[j].put(hcorrVal)

                self.pvCORy[j].put(vcorrVal + self.d_value)
                ybpm_val1 = self._get_avg_readings([self.pvBPMy[j]])[0]
            finally:
                self.pvCORx[j].put(hcorrVal)
                self.pvCORy[j].put(vcorrVal)
            
            # cal response coefficient
            Rx = (xbpm_val1 - xbpm_val0) / self.d_value
            Ry = (ybpm_val1 - ybpm_val0) / self.d_value
            logger.info(f"response coefficient: Rx={Rx:.3f}, Ry={Ry:.3f}")

            if abs(Rx) < MIN_RESPONSE or abs(Ry) < MIN_RESPONSE:
                self.pvCORx[j].put(hcorrVal)
                self.pvCORy[j].put(vcorrVal)
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
                ])

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
                self.pvCORx[j].put(hcorrVal)
                self.pvCORy[j].put(vcorrVal)
                time.sleep(self.sample_interval)

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
        xy_vals = self._get_avg_readings(self.pvBPMx + self.pvBPMy)
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


    def _load_response_matrix(self) -> Tuple[np.ndarray, np.ndarray]:
        """加载并计算响应矩阵的逆矩阵"""
        try:
            RM = self._load_valid_response_matrix()
            logger.debug("Loaded response matrix from %s with shape %s", self.response_matrix_path, RM.shape)
            ORM_x = RM[0:self.N_BPM, 0:self.N_COR]
            ORM_y = RM[self.N_BPM:self.N_BPM * 2, self.N_COR:self.N_COR * 2]
            return np.linalg.inv(ORM_x), np.linalg.inv(ORM_y)
        except Exception as e:
            logger.error(f"加载响应矩阵失败: {e}")
            raise

    def _expected_response_shape(self) -> tuple[int, int]:
        return (2 * self.N_BPM, 2 * self.N_COR)

    def _load_valid_response_matrix(self) -> np.ndarray:
        expected_shape = self._expected_response_shape()
        legacy_path = RESPONSE_MATRIX_PATH if self.machine_profile.machine.id == "half" else None
        matrix_path = resolve_active_response_matrix(
            self.app_context,
            legacy_matrix_path=legacy_path,
        )
        self.response_matrix_path = matrix_path
        matrix = np.loadtxt(matrix_path)
        if matrix.shape != expected_shape:
            raise ValueError(
                "Response matrix shape mismatch for "
                f"{self.machine_profile.machine.id}/{self.machine_mode}: "
                f"{matrix_path} has shape {matrix.shape}, expected {expected_shape}. "
                "Run Measure Response Matrix for the current machine/backend before using global correction."
            )
        return matrix

    def _compute_svd(self, min_singular_value=0.01):
        """计算响应矩阵的SVD分解"""
        self._require_targets()
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

        self.pseudo_inverse_x = self._truncated_pseudo_inverse(ORM_x, min_singular_value)
        self.pseudo_inverse_y = self._truncated_pseudo_inverse(ORM_y, min_singular_value)

    @staticmethod
    def _truncated_pseudo_inverse(matrix: np.ndarray, min_singular_value: float) -> np.ndarray:
        U, singular_values, Vt = svd(matrix, full_matrices=False)
        s_inv = np.zeros_like(singular_values)
        for i, value in enumerate(singular_values):
            if abs(value) > min_singular_value:
                s_inv[i] = 1 / value
        return Vt.T @ np.diag(s_inv) @ U.T

    def _get_avg_readings2(self, pv_list: List[str]) -> List[float]:
        """同时获取多个PV的多次采样平均值"""
        if not pv_list or self.samples_perstep <= 0:
            return [0.0] * len(pv_list)
            
        results = [0.0] * len(pv_list)
        
        for _ in range(self.samples_perstep):
            time.sleep(self.sample_interval)
            readings = caget_many(pv_list)
            # print(readings)
            if readings:  # 确保readings不是None
                results = [r1 + r2 for r1, r2 in zip(results, readings)]
        
        return [r / self.samples_perstep for r in results]
     
    def correct_global(self, max_iter: int | None = None) -> bool:
        """全局校正方法"""
        self._require_write_allowed("Global orbit correction")
        self._require_targets()
        max_iter = self.global_max_iter if max_iter is None else max_iter

        # 加载响应矩阵
        # self.ORMx_n, self.ORMy_n = self._load_response_matrix()
        logger.info("global correction uses SVD pseudo-inverse")
        self._compute_svd()
        pvname_corx = [self._cor_pv(cor) for cor in self.global_xcor_list]
        pvname_cory = [self._cor_pv(cor) for cor in self.global_ycor_list]

        for iteration in range(0, max_iter):
            # 获取当前轨道数据
            xy_vals = self._get_avg_readings2(self.pvnameBPMx + self.pvnameBPMy)
            length_half = len(xy_vals) // 2
            x_vals = xy_vals[:length_half]
            y_vals = xy_vals[length_half:]
            # print("获取当前x轨道数据", x_vals)
            
            # 计算误差
            x_err = [tx - x for tx, x in zip(self.target_BPMx_values, x_vals)]
            y_err = [ty - y for ty, y in zip(self.target_BPMy_values, y_vals)]
            # print("获取x目标轨道差距", x_err)
            
            # 检查收敛
            max_x_err = max(abs(e) for e in x_err)
            max_y_err = max(abs(e) for e in y_err)
            
            logger.info(f"iteration {iteration}: max error X={max_x_err:.3e} , Y={max_y_err:.3e}")

            if max_x_err < self.cor_accuracy and max_y_err < self.cor_accuracy:
                logger.info(f"correction is finished at iteration: {iteration}")
                return True
            
            # 计算校正量
            # delt_corrh = np.dot(self.ORMx_n, np.array(x_err))
            # delt_corrv = np.dot(self.ORMy_n, np.array(y_err))
            delt_corrh = np.dot(self.pseudo_inverse_x, np.array(x_err))
            delt_corrv = np.dot(self.pseudo_inverse_y, np.array(y_err))


            # 应用校正
            hcor_vals = caget_many(pvname_corx)
            vcor_vals = caget_many(pvname_cory)
            new_hcor = np.clip(
                np.array(hcor_vals) + delt_corrh,
                -1*self.max_value, 1*self.max_value
            )
            new_vcor = np.clip(
                np.array(vcor_vals) + delt_corrv,
                -1*self.max_value, 1*self.max_value
            )
            
            caput_many(pvname_corx, new_hcor)
            caput_many(pvname_cory, new_vcor)


            # 检查是否达到限值
            # if (np.abs(new_hcor) >= self.max_value*0.99).all() and \
            #    (np.abs(new_vcor) >= self.max_value*0.99).all():
            #     logger.warning("水平和垂直校正铁均达到调节限值")
            #     return False
        
        logger.info(f"reach the max iteration: {max_iter}")
        return False
    
    # def reset_cor(self) -> None:
    #     """重置所有校正铁为零"""
    #     values = [0.0] * len(self.pvCORx)
    #     caput_many([pv.pvname for pv in self.pvCORx], values)
    #     caput_many([pv.pvname for pv in self.pvCORy], values)
    #     logger.info("所有校正铁已重置为零")


    def reset_cor(self) -> None:
        """Reset the selected correctors, or all of them if no selection is provided."""
        self._require_write_allowed("Corrector reset")
        cor_x_list = self.cor_x_list_target or self.cor_x_list_all
        cor_y_list = self.cor_y_list_target or self.cor_y_list_all

        pv_corx = [self._cor_pv(name) for name in cor_x_list]
        pv_cory = [self._cor_pv(name) for name in cor_y_list]
        values = [0.0] * len(pv_corx)
        caput_many(pv_corx, values)
        caput_many(pv_cory, values) 

    def cor_recover(self) -> None:
        """recvoer all corrector to the value before cor"""
        self._require_write_allowed("Corrector recover")
        cor_path = self.corrector_state_path
        if not cor_path.exists():
            raise FileNotFoundError(f"Corrector backup file not found: {cor_path}")

        cor = np.loadtxt(cor_path, ndmin=2)
        if cor.shape[1] != 2:
            raise ValueError(f"Unexpected corrector backup shape: {cor.shape}")

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
        caput_many(self.pvCORx, corx)
        caput_many(self.pvCORy, cory) 

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
            one_to_one_gain = _optional_float_arg(sys.argv, 12)
            one_to_one_max_step_fraction = _optional_float_arg(sys.argv, 13)
            response_kick = _optional_float_arg(sys.argv, 14)
            global_xcor_list = _optional_csv_arg(sys.argv, 15)
            global_ycor_list = _optional_csv_arg(sys.argv, 16)
            
            corrector = OrbitCorrector(
                samp_interval, cor_accuracy, samples_perstep,
                target_BPMlist, target_BPMx_values, target_BPMy_values,
                corrector_limit=corrector_limit,
                global_max_iter=global_max_iter,
                one_to_one_max_iter=one_to_one_max_iter,
                one_to_one_gain=one_to_one_gain,
                one_to_one_max_step_fraction=one_to_one_max_step_fraction,
                response_kick=response_kick,
                global_xcor_list=global_xcor_list,
                global_ycor_list=global_ycor_list,
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
