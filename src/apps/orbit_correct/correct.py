import time
import logging
import numpy as np
import sys

_REPO_BOOTSTRAP_ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / "repo_bootstrap.py").is_file()
)
if str(_REPO_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_BOOTSTRAP_ROOT))

from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

from typing import List, Optional, Tuple, Union
from epics import caput_many, PV, caget_many
from scipy.linalg import svd
from half_linac.src.shared.machine_profile import (
    load_app_context,
    resolve_channel,
)
from half_linac.src.apps.orbit_correct.profile_runtime import (
    CORRECTOR_STATE_PATH,
    CORRECT_LOG_PATH,
    RESPONSE_MATRIX_PATH,
    load_orbit_runtime_settings,
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


def _parse_target_arg(arg: str, scale: float = 1.0) -> List[float]:
    values = [item for item in arg.split(',') if item]
    return [float(value) * scale for value in values]

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
                target_BPMy_values: Optional[List[float]] = None):
        self.app_context = load_app_context("orbit_correct")
        self.machine_profile = self.app_context.profile
        self.orbit_workflow = self.app_context.orbit_workflow
        self.machine_mode = self.app_context.control_backend.name
        self.orbit_runtime = load_orbit_runtime_settings(self.app_context)

        # constant definition
        self.RESPM_FILE = str(RESPONSE_MATRIX_PATH)
        self.d_value = 0.02 * 0.001  # step for corrector
        self.max_value = self.orbit_runtime["corrector_upperlimit_rad"]
        
        # all cor and bpm lists
        if self.orbit_workflow is None:
            raise ValueError("Orbit workflow is not available in the current app context.")
        self.cor_x_list_all = list(self.orbit_workflow.xcors)
        self.cor_y_list_all = list(self.orbit_workflow.ycors)
        self.bpm_list_all = list(self.orbit_workflow.bpms)
        self.N_BPM = len(self.bpm_list_all)
        self.N_COR = len(self.cor_x_list_all)
        
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
            self.bpm_list_target = list(target_BPMlist)
            self.cor_x_list_target = [self.cor_x_list_all[index_map[name]] for name in target_BPMlist]
            self.cor_y_list_target = [self.cor_y_list_all[index_map[name]] for name in target_BPMlist]
            self.target_BPMx_values = target_BPMx_values
            self.target_BPMy_values = target_BPMy_values
        else:
            self.bpm_list_target = []
            self.cor_x_list_target = []
            self.cor_y_list_target = []
            self.target_BPMx_values = []
            self.target_BPMy_values = []
        
        
        
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
        
        with CORRECTOR_STATE_PATH.open('w', encoding='utf-8') as file:
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

    def correct_one_to_one(self) -> None:
        """one-to-one method"""
        self._require_targets()
        for j in range(len(self.bpm_list_target)):
            logger.info(f"开始校正: {self.bpm_list_target[j]}")
            
            # 同时获取初始值
            xbpm_val0, ybpm_val0, hcorrVal, vcorrVal = self._get_avg_readings([
                self.pvBPMx[j],
                self.pvBPMy[j],
                self.pvCORx[j],
                self.pvCORy[j]
            ])
            
            # 微调并测量响应
            self.pvCORx[j].put(hcorrVal + self.d_value)
            self.pvCORy[j].put(vcorrVal + self.d_value)
            xbpm_val1, ybpm_val1 = self._get_avg_readings([
                self.pvBPMx[j],
                self.pvBPMy[j]
            ])
            
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
                continue
            
            # 计算新校正值
            hcorrVal += (self.target_BPMx_values[j] - xbpm_val0) / Rx
            vcorrVal += (self.target_BPMy_values[j] - ybpm_val0) / Ry
            
            # 限幅处理
            hcorrVal = np.clip(hcorrVal, -self.max_value, self.max_value)
            vcorrVal = np.clip(vcorrVal, -self.max_value, self.max_value)
            
            # 应用校正
            self.pvCORx[j].put(hcorrVal)
            self.pvCORy[j].put(vcorrVal)

            # 验证校正结果并进行迭代校正
            loop = 0
            while True:
                # xbpm_val1 = self.pvBPMx[j].get()
                # ybpm_val1 = self.pvBPMy[j].get()
                xbpm_val1, ybpm_val1 = self._get_avg_readings([
                    self.pvBPMx[j],
                    self.pvBPMy[j]
                ])
                
                # 检查是否满足精度要求
                x_err = abs(self.target_BPMx_values[j] - xbpm_val1)
                y_err = abs(self.target_BPMy_values[j] - ybpm_val1)
                
                if x_err < self.cor_accuracy and y_err < self.cor_accuracy:
                    break
                    
                # 检查校正铁是否达到限值
                if abs(hcorrVal) >= self.max_value and abs(vcorrVal) >= self.max_value:
                    logger.warning(f"{self.bpm_list_target[j]} 校正铁都达到限值")
                    break
                    
                loop += 1
                logger.info(f"校正迭代 {loop} 次: {self.bpm_list_target[j]}")
                
                if  abs(hcorrVal) < self.max_value and abs(vcorrVal) < self.max_value:
                    hcorrVal += (self.target_BPMx_values[j] - xbpm_val1) / Rx
                    vcorrVal += (self.target_BPMy_values[j] - ybpm_val1) / Ry
                    hcorrVal = np.clip(hcorrVal, -self.max_value, self.max_value)
                    vcorrVal = np.clip(vcorrVal, -self.max_value, self.max_value)
                    self.pvCORx[j].put(hcorrVal)
                    self.pvCORy[j].put(vcorrVal)
                    time.sleep(self.sample_interval)
                
                if  (abs(hcorrVal) < self.max_value and abs(vcorrVal) >= self.max_value):
                    if  x_err < self.cor_accuracy and y_err >= self.cor_accuracy:
                        logger.warning(f"{self.bpm_list_target[j]} V校正铁达到限值")
                        break
                    
                    else:
                        vcorrVal += (self.target_BPMy_values[j] - ybpm_val1) / Ry
                        vcorrVal = np.clip(vcorrVal, -self.max_value, self.max_value)
                        self.pvCORy[j].put(vcorrVal)
                        
                if  (abs(hcorrVal) >= self.max_value and abs(vcorrVal) < self.max_value):
                    if  x_err >= self.cor_accuracy and y_err < self.cor_accuracy:
                        logger.warning(f"{self.bpm_list_target[j]} H校正铁达到限值")
                        break
                    else:
                        hcorrVal += (self.target_BPMx_values[j] - xbpm_val1) / Rx
                        hcorrVal = np.clip(hcorrVal, -self.max_value, self.max_value)
                        self.pvCORx[j].put(hcorrVal)
                
                time.sleep(self.sample_interval)
            
            logger.info(f"correction finished: {self.bpm_list_target[j]}")
            logger.info(f"corrector: ({hcorrVal:.3f}, {vcorrVal:.3f})")
            logger.info(f"BPM: ({xbpm_val1:.3e}, {ybpm_val1:.3e})")


    def _load_response_matrix(self) -> Tuple[np.ndarray, np.ndarray]:
        """加载并计算响应矩阵的逆矩阵"""
        try:
            RM = np.loadtxt(self.RESPM_FILE)
            print(RM)
            ORM_x = RM[0:self.N_BPM, 0:self.N_COR]
            ORM_y = RM[self.N_BPM:self.N_BPM * 2, self.N_COR:self.N_COR * 2]
            return np.linalg.inv(ORM_x), np.linalg.inv(ORM_y)
        except Exception as e:
            logger.error(f"加载响应矩阵失败: {e}")
            raise

    def _compute_svd(self, min_singular_value=0.01):
        """计算响应矩阵的SVD分解"""
        RM = np.loadtxt(self.RESPM_FILE)
        ORM_x = RM[0:self.N_BPM, 0:self.N_COR]
        ORM_y = RM[self.N_BPM:self.N_BPM * 2, self.N_COR:self.N_COR * 2]
            
        U_x, s_x, Vt_x = svd(ORM_x, full_matrices=False)
        # self.svd_components_x = (U_x, s_x, Vt_x)
        
        # 创建截断的伪逆矩阵
        s_inv_x = np.zeros_like(s_x)
        for i, val in enumerate(s_x):
            if abs(val) > min_singular_value:
                s_inv_x[i] = 1 / val
        
        self.pseudo_inverse_x = Vt_x.T @ np.diag(s_inv_x) @ U_x.T

        U_y, s_y, Vt_y = svd(ORM_y, full_matrices=False)
        # self.svd_components_y = (U_y, s_y, Vt_y)
        
        # 创建截断的伪逆矩阵
        s_inv_y = np.zeros_like(s_y)
        for i, val in enumerate(s_y):
            if abs(val) > min_singular_value:
                s_inv_y[i] = 1 / val
        
        self.pseudo_inverse_y = Vt_y.T @ np.diag(s_inv_y) @ U_y.T

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
     
    def correct_global(self, max_iter: int = 20) -> bool:
        """全局校正方法"""
        self._require_targets()

        # 加载响应矩阵
        # self.ORMx_n, self.ORMy_n = self._load_response_matrix()
        print('svd method')
        self._compute_svd()

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
            hcor_vals = caget_many(self.pvnameCORx)
            vcor_vals = caget_many(self.pvnameCORy)
            new_hcor = np.clip(
                np.array(hcor_vals) + delt_corrh,
                -1*self.max_value, 1*self.max_value
            )
            new_vcor = np.clip(
                np.array(vcor_vals) + delt_corrv,
                -1*self.max_value, 1*self.max_value
            )
            
            caput_many(self.pvnameCORx, new_hcor)
            caput_many(self.pvnameCORy, new_vcor)


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
        cor_x_list = self.cor_x_list_target or self.cor_x_list_all
        cor_y_list = self.cor_y_list_target or self.cor_y_list_all

        pv_corx = [self._cor_pv(name) for name in cor_x_list]
        pv_cory = [self._cor_pv(name) for name in cor_y_list]
        values = [0.0] * len(pv_corx)
        caput_many(pv_corx, values)
        caput_many(pv_cory, values) 

    def cor_recover(self) -> None:
        """recvoer all corrector to the value before cor"""
        cor_path = CORRECTOR_STATE_PATH
        if not cor_path.exists():
            raise FileNotFoundError(f"Corrector backup file not found: {cor_path}")

        cor = np.loadtxt(cor_path, ndmin=2)
        if cor.shape[1] != 2:
            raise ValueError(f"Unexpected corrector backup shape: {cor.shape}")

        corx = cor[:, 0]
        cory = cor[:, 1]
        print(corx, cory)

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
            
            corrector = OrbitCorrector(
                samp_interval, cor_accuracy, samples_perstep,
                target_BPMlist, target_BPMx_values, target_BPMy_values
            )
            corrector._require_targets()
            corrector.init_BPM_pv()
            corrector.init_COR_pv()
            corrector.save_origin_cor()

            
            if method == "one-to-one":
                corrector.correct_one_to_one()
            elif method == "global":
                corrector.correct_global()
        
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
