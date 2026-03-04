import time
import numpy as np
from epics import caget, caput

class EnergyFeedbackController:
    """
    改进型 PI 能量反馈控制器 (带低通滤波与抗饱和)
    """

    def __init__(
        self,
        mode="HV",
        rep_rate=1.0,

        # ---- BPM configuration ----
        pv_bpm_x="BPM:DSP:X",
        pv_bpm_valid="BPM:DSP:VALID",
        use_two_bpm=False,
        pv_bpm2_x=None,

        # ---- Optics ----
        D1=1.2, D2=None, alpha=None,

        # ---- Actuators ----
        pv_hv_set="MOD:HV:SET", pv_hv_rb="MOD:HV:RB",
        pv_i_set="LLRF:IQ:I_SET", pv_q_set="LLRF:IQ:Q_SET",
        pv_phi1_set="LLRF:LAST1:PHASE_SET", pv_phi2_set="LLRF:LAST2:PHASE_SET",

        # ---- PI Gains & Filter ----
        kp=1.0,               # 比例增益 (相对于总增益的权重)
        ki=0.2,               # 积分增益
        lpf_alpha=0.3,        # 低通滤波系数 (0~1, 越小越平滑但响应越慢)
        
        # 针对各模式的具体物理转换系数 (Scaling factors)
        gain_hv=2.0,          # kV / delta
        gain_iq=0.15,
        gain_phi=0.02,
        gain_phase=5.0,

        # ---- Limits & Safety ----
        max_hv_step=0.5,
        max_iq_step=0.02,
        max_phase_step=0.2,
        i_limit=0.5,          # 积分幅度限制 (防止积分饱和 Windup)
        deadband=2e-4         # 死区
    ):
        self.mode = mode.upper()
        self.rep_rate = rep_rate

        # PVs
        self.pv_bpm_x = pv_bpm_x
        self.pv_bpm_valid = pv_bpm_valid
        self.use_two_bpm = use_two_bpm
        self.pv_bpm2_x = pv_bpm2_x
        self.pv_hv_set = pv_hv_set
        self.pv_hv_rb = pv_hv_rb
        self.pv_i_set = pv_i_set
        self.pv_q_set = pv_q_set
        self.pv_phi1_set = pv_phi1_set
        self.pv_phi2_set = pv_phi2_set

        # Optics
        self.D1, self.D2, self.alpha = D1, D2, alpha

        # PI 参数
        self.kp = kp
        self.ki = ki
        self.lpf_alpha = lpf_alpha
        self.i_limit = i_limit
        self.deadband = deadband

        # 物理增益
        self.gain_map = {
            "HV": gain_hv,
            "IQ": gain_iq,
            "HYBRID": gain_phi,
            "PHASE_PAIR": gain_phase
        }

        # 步进限制
        self.step_limit_map = {
            "HV": max_hv_step,
            "IQ": max_iq_step,
            "PHASE_PAIR": max_phase_step,
            "HYBRID": max_hv_step  # Hybrid 主要看 HV 步进
        }

        # 控制器内部状态
        self.last_delta_filtered = 0.0
        self.integral_error = 0.0
        self.last_run_time = time.time()

        self._validate_config()

    def _validate_config(self):
        if self.mode not in ("IQ", "HV", "HYBRID", "PHASE_PAIR"):
            raise ValueError(f"Unknown feedback mode: {self.mode}")
        if self.use_two_bpm and (self.pv_bpm2_x is None or self.D2 is None):
            raise ValueError("Two-BPM mode requires BPM2 PV and D2/alpha")

    @staticmethod
    def _clamp(val, lim):
        return max(min(val, lim), -lim)

    def compute_delta(self):
        """获取原始 delta 并进行低通滤波"""
        try:
            x1 = caget(self.pv_bpm_x)
            if x1 is None: return None
            
            if self.use_two_bpm:
                x2 = caget(self.pv_bpm2_x)
                if x2 is None: return None
                delta_raw = (x2 - self.alpha * x1) / (self.D2 - self.alpha * self.D1)
            else:
                delta_raw = x1 / self.D1

            # 一阶低通滤波: y(n) = a*x(n) + (1-a)*y(n-1)
            self.last_delta_filtered = (self.lpf_alpha * delta_raw) + \
                                       (1 - self.lpf_alpha) * self.last_delta_filtered
            return self.last_delta_filtered
        except Exception as e:
            print(f"Error computing delta: {e}")
            return None

    def step(self):
        # 1. 检查束流有效性
        if caget(self.pv_bpm_valid) != 1:
            return None

        # 2. 计算滤波后的误差
        delta = self.compute_delta()
        if delta is None: return None

        # 3. 死区逻辑 (死区内不累积积分，防止微小噪声导致漂移)
        if abs(delta) < self.deadband:
            return None

        # 4. PI 控制核心逻辑
        # 计算积分项并进行抗饱和限制 (Anti-windup)
        self.integral_error += delta
        self.integral_error = self._clamp(self.integral_error, self.i_limit)

        # 组合 PI 输出: u = Kp * error + Ki * integral
        pi_output = (self.kp * delta) + (self.ki * self.integral_error)
        
        # 5. 执行器映射
        action_msg = ""
        phys_gain = self.gain_map.get(self.mode, 1.0)
        step_lim = self.step_limit_map.get(self.mode, 0.1)

        # 计算总调整量 (负反馈)
        total_adjustment = -pi_output * phys_gain

        if self.mode == "HV":
            hv_curr = caget(self.pv_hv_rb)
            dhv = self._clamp(total_adjustment, step_lim)
            caput(self.pv_hv_set, hv_curr + dhv)
            action_msg = f"dHV={dhv:+.3f} kV"

        elif self.mode == "PHASE_PAIR":
            dphi = self._clamp(total_adjustment, step_lim)
            phi1 = caget(self.pv_phi1_set)
            phi2 = caget(self.pv_phi2_set)
            caput(self.pv_phi1_set, phi1 + dphi)
            caput(self.pv_phi2_set, phi2 - dphi)
            action_msg = f"dPhi_pair={dphi:+.3f} deg"

        # ... (IQ 和 HYBRID 模式可按此逻辑类推)

        print(f"[PI-FB] Mode={self.mode:10s} Delta_filt={delta:+.2e} Int={self.integral_error:+.2e} {action_msg}")

        # 6. 精确的时序控制(间隔里考虑程序运行时间)
        elapsed = time.time() - self.last_run_time
        wait_time = max(0, (1.0 / self.rep_rate) - elapsed)
        time.sleep(wait_time)
        self.last_run_time = time.time()
        
        return delta