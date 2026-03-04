import time
import numpy as np
# from epics import caget, caput

class EnergyFeedbackController:
    """
    电子 Linac 能量脉冲间 (Pulse-to-Pulse) 反馈控制器。
    集成特性：
    1. PI 控制 (Proportional-Integral) 消除静差。
    2. 一阶低通滤波 (LPF) 抑制 BPM 噪声。
    3. 积分抗饱和 (Anti-windup) 保护。
    4. 四种模式：HV, IQ (幅调), PHASE_PAIR (相位对冲), HYBRID (混合)。
    """

    def __init__(
        self,
        mode="HV",
        rep_rate=1.0,

        # ---- BPM 配置 ----
        pv_bpm_x="BPM:DSP:X",
        pv_bpm_valid="BPM:DSP:VALID",
        use_two_bpm=False,
        pv_bpm2_x=None,

        # ---- 光学参数 (用于计算 delta = dE/E) ----
        D1=1.2,          # [m] 
        D2=None,         # [m]
        alpha=1.0,      # 传输矩阵因子

        # ---- 执行器 PV ----
        pv_hv_set="MOD:HV:SET",
        pv_hv_rb="MOD:HV:RB",
        pv_i_set="LLRF:IQ:I_SET",
        pv_q_set="LLRF:IQ:Q_SET",
        pv_phi1_set="LLRF:LAST1:PHASE_SET",
        pv_phi2_set="LLRF:LAST2:PHASE_SET",

        # ---- PI 控制器与滤波器参数 ----
        kp=0.8,               # 比例权重
        ki=0.2,               # 积分权重
        lpf_alpha=0.3,        # 滤波系数 (0-1), 越小越平滑
        i_limit=0.5,          # 积分幅度限制 (Anti-windup)
        deadband=2e-4,        # 死区 (不触发反馈的微小误差范围)

        # ---- 物理转换增益 (物理单位 / delta) ----
        gain_hv=2.0,          # kV / delta
        gain_iq_amp=0.15,     # 相对幅度 / delta
        gain_phi_hybrid=0.02, # rad / delta (用于 Hybrid 模式相位微调)
        gain_phase_deg=5.0,   # deg / delta (用于 Phase-Pair 模式)

        # ---- 安全步进限制 (每脉冲最大改变量) ----
        max_hv_step=0.5,      # kV
        max_iq_step=0.02,     # unit
        max_phase_step=0.5    # deg
    ):
        # 基础设置
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

        # 光学参数
        self.D1, self.D2, self.alpha = D1, D2, alpha

        # 控制器参数
        self.kp, self.ki = kp, ki
        self.lpf_alpha = lpf_alpha
        self.i_limit = i_limit
        self.deadband = deadband

        # 增益与限制映射
        self.gain_map = {
            "HV": gain_hv,
            "IQ": gain_iq_amp,
            "HYBRID": gain_phi_hybrid,
            "PHASE_PAIR": gain_phase_deg
        }
        self.step_limit_map = {
            "HV": max_hv_step,
            "IQ": max_iq_step,
            "PHASE_PAIR": max_phase_step,
            "HYBRID": max_hv_step
        }

        # 内部状态变量
        self.integral_error = 0.0
        self.last_delta_filtered = 0.0
        self.last_run_time = time.time()

        self._validate_config()

    def _validate_config(self):
        if self.mode not in ("IQ", "HV", "HYBRID", "PHASE_PAIR"):
            raise ValueError(f"Unknown feedback mode: {self.mode}")
        if self.use_two_bpm and (self.pv_bpm2_x is None or self.D2 is None):
            raise ValueError("Two-BPM mode configuration incomplete.")

    @staticmethod
    def _clamp(val, lim):
        return max(min(val, lim), -lim)

    # -------------------------------
    # 数据采集与滤波
    # -------------------------------
    def compute_delta(self):
        """获取原始数据并应用一阶低通滤波"""
        x1 = caget(self.pv_bpm_x)
        if x1 is None: return None

        if self.use_two_bpm:
            x2 = caget(self.pv_bpm2_x)
            if x2 is None: return None
            delta_raw = (x2 - self.alpha * x1) / (self.D2 - self.alpha * self.D1)
        else:
            delta_raw = x1 / self.D1

        # LPF: y(n) = alpha*x(n) + (1-alpha)*y(n-1)
        self.last_delta_filtered = (self.lpf_alpha * delta_raw) + \
                                   (1 - self.lpf_alpha) * self.last_delta_filtered
        return self.last_delta_filtered

    # -------------------------------
    # 具体执行器更新逻辑
    # -------------------------------
    def _apply_hv_logic(self, total_adj):
        hv_rb = caget(self.pv_hv_rb)
        dhv = self._clamp(total_adj, self.step_limit_map["HV"])
        caput(self.pv_hv_set, hv_rb + dhv)
        return f"dHV={dhv:+.3f} kV"

    def _apply_iq_logic(self, total_adj):
        """保持相位不变，仅缩放幅度"""
        I, Q = caget(self.pv_i_set), caget(self.pv_q_set)
        if I is None or Q is None: return "IQ_Read_Error"
        
        A_curr = np.hypot(I, Q)
        if A_curr < 1e-6: return "A_Zero"
        
        dA = self._clamp(total_adj, self.step_limit_map["IQ"])
        scale = (A_curr + dA) / A_curr
        
        caput(self.pv_i_set, I * scale)
        caput(self.pv_q_set, Q * scale)
        return f"dA={dA:+.3e} (scale={scale:.4f})"

    def _apply_phase_pair_logic(self, total_adj):
        """对称调节最后两个结构的相位"""
        dphi = self._clamp(total_adj, self.step_limit_map["PHASE_PAIR"])
        phi1, phi2 = caget(self.pv_phi1_set), caget(self.pv_phi2_set)
        
        caput(self.pv_phi1_set, phi1 + dphi)
        caput(self.pv_phi2_set, phi2 - dphi)
        return f"dPhi1={dphi:+.3f}, dPhi2={-dphi:+.3f} deg"

    def _apply_hybrid_logic(self, total_adj_hv, delta_filt):
        """HV 调幅度 (PI), IQ 调相位 (P)"""
        # 1. HV PI 控制
        hv_msg = self._apply_hv_logic(total_adj_hv)
        
        # 2. IQ 相位 P 控制 (辅助阻尼)
        dphi_rad = -self.gain_map["HYBRID"] * delta_filt
        I, Q = caget(self.pv_i_set), caget(self.pv_q_set)
        A, phi = np.hypot(I, Q), np.arctan2(Q, I)
        
        new_phi = phi + dphi_rad
        caput(self.pv_i_set, A * np.cos(new_phi))
        caput(self.pv_q_set, A * np.sin(new_phi))
        
        return f"{hv_msg}, dPhi_IQ={np.degrees(dphi_rad):+.4f}deg"

    # -------------------------------
    # 反馈主循环步骤
    # -------------------------------
    def step(self):
        # 1. 束流合法性检查
        if caget(self.pv_bpm_valid) != 1:
            # 如果束流丢失，通常建议重置积分项，防止恢复时过冲
            # self.integral_error = 0.0 
            return None

        # 2. 获取滤波后的误差
        delta = self.compute_delta()
        if delta is None: return None

        # 3. 死区检查
        if abs(delta) < self.deadband:
            return None

        # 4. PI 计算与抗饱和
        self.integral_error += delta
        self.integral_error = self._clamp(self.integral_error, self.i_limit)

        # 组合 PI 输出: u = Kp * error + Ki * integral
        pi_output = (self.kp * delta) + (self.ki * self.integral_error)
        
        # 计算控制输出 (负反馈方向)
        phys_gain = self.gain_map.get(self.mode, 1.0)
        total_adjustment = -pi_output * phys_gain

        # 5. 分模式执行
        if self.mode == "HV":
            action = self._apply_hv_logic(total_adjustment)
        elif self.mode == "IQ":
            action = self._apply_iq_logic(total_adjustment)
        elif self.mode == "PHASE_PAIR":
            action = self._apply_phase_pair_logic(total_adjustment)
        elif self.mode == "HYBRID":
            action = self._apply_hybrid_logic(total_adjustment, delta)
        else:
            action = "N/A"

        # 6. 日志与时序
        print(f"[P2P-PI] Mode: {self.mode:10s} | Delta_F: {delta:+.2e} | Int: {self.integral_error:+.2e} | {action}")
        
        elapsed = time.time() - self.last_run_time
        time.sleep(max(0, (1.0 / self.rep_rate) - elapsed))
        self.last_run_time = time.time()
        
        return delta

# ---------------------------------------------------------
# 启动入口
# ---------------------------------------------------------
if __name__ == "__main__":
    fb = EnergyFeedbackController(
        mode="HYBRID", 
        rep_rate=2.0,       # 2Hz
        kp=0.5, ki=0.1,     # PI 参数
        lpf_alpha=0.4       # 适中滤波
    )

    print(f"Starting Energy Feedback Loop [Mode: {fb.mode}]...")
    try:
        while True:
            fb.step()
    except KeyboardInterrupt:
        print("Feedback stopped by user.")