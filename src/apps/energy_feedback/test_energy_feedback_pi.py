import numpy as np
import matplotlib.pyplot as plt
import time

# -------------------------------
# 模拟 EPICS PV
# -------------------------------
PV = {
    "BPM:DSP:X": 0.0,
    "BPM:DSP:VALID": 1,
    "MOD:HV:SET": 0.0,
    "MOD:HV:RB": 0.0,
    "LLRF:IQ:I_SET": 1.0,
    "LLRF:IQ:Q_SET": 0.0,
}

def caget(pv):
    return PV.get(pv, None)

def caput(pv, val):
    PV[pv] = val

# 注入到控制器命名空间
import builtins
builtins.caget = caget
builtins.caput = caput

# -------------------------------
# 引入你的控制器
# -------------------------------
from main_pi import EnergyFeedbackController
# ↑ 假设你把上面的类存成这个文件名

# -------------------------------
# 虚拟 Linac 参数
# -------------------------------
K_HV_TRUE = 0.4        # true delta / kV
ENERGY_DRIFT = 3e-4    # 每 pulse 漂移
BPM_NOISE = 5e-5

# -------------------------------
# 仿真变量
# -------------------------------
delta_true = 0.0

history = {
    "delta_true": [],
    "delta_meas": [],
    "hv": [],
    "pi_int": [],
}

# -------------------------------
# 初始化控制器
# -------------------------------
# ctrl = EnergyFeedbackController(
#     mode="HV",
#     rep_rate=1000,
#     kp=1,
#     ki=0.1,
#     lpf_alpha=0.3,
#     deadband=0.0,
#     gain_hv=2.0,       # 控制器“认为”的增益
#     max_hv_step=0.1
# )
ctrl = EnergyFeedbackController(
    mode="PHASE_PAIR",
    kp=0.8,
    ki=0.0,      #
    gain_phase_deg=10.0,
    max_phase_step=1.0
)

# -------------------------------
# 主仿真循环
# -------------------------------
N = 1000

for k in range(N):

    # ---- 虚拟 Linac 漂移 ----
    # delta_true += ENERGY_DRIFT
    delta_true = 5e-4 * np.sin(2*np.pi*k/500)

    # ---- HV 反馈作用 ----
    dhv = PV["MOD:HV:SET"] - PV["MOD:HV:RB"]
    delta_true += K_HV_TRUE * dhv
    PV["MOD:HV:RB"] = PV["MOD:HV:SET"]

    # ---- BPM 观测 ----
    PV["BPM:DSP:X"] = delta_true * ctrl.D1 + np.random.randn() * BPM_NOISE

    # ---- 执行反馈 ----
    ctrl.step()

    # ---- 记录 ----
    history["delta_true"].append(delta_true)
    history["delta_meas"].append(ctrl.last_delta_filtered)
    history["hv"].append(PV["MOD:HV:RB"])
    history["pi_int"].append(ctrl.integral_error)

    # time.sleep(0.01)

# -------------------------------
# 画图
# -------------------------------
t = np.arange(N)

plt.figure(figsize=(10, 6))
plt.subplot(3, 1, 1)
plt.plot(t, history["delta_true"], label="True ΔE/E")
plt.plot(t, history["delta_meas"], "--", label="Measured (LPF)")
plt.legend()
plt.ylabel("ΔE/E")

plt.subplot(3, 1, 2)
plt.plot(t, history["hv"])
plt.ylabel("HV [kV]")
plt.title("PI Energy Feedback Response")

plt.subplot(3, 1, 3)
plt.plot(t, history["pi_int"])
plt.ylabel("Integral Term")
plt.xlabel("Pulse")

plt.tight_layout()
plt.show()
