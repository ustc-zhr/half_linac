import numpy as np
import matplotlib.pyplot as plt
from collections import deque
from main_pi import EnergyFeedbackController
# ---------------------------------------------------------
# Mock EPICS Environment
# ---------------------------------------------------------
class MockEpics:
    def __init__(self):
        # 初始物理状态
        self.pvs = {
            "BPM:DSP:X": 0.0,
            "BPM:DSP:VALID": 1,
            "MOD:HV:SET": 40.0,  # 初始高压 40kV
            "MOD:HV:RB": 40.0,
            "LLRF:IQ:I_SET": 1.0,
            "LLRF:IQ:Q_SET": 0.0
        }
        self.true_energy_offset = 0.0  # 外部漂移
        self.sensitivity = 0.05        # HV 每改变 1kV，delta 改变多少

    def caget(self, pv):
        if pv == "BPM:DSP:X":
            # 模拟物理：delta = (HV误差 * 灵敏度) + 外部漂移 + 噪声
            # 假设 D1 = 1.0，所以 delta 就是 BPM:X
            noise = np.random.normal(0, 0.0001)
            delta = (self.pvs["MOD:HV:RB"] - 40.0) * self.sensitivity + self.true_energy_offset + noise
            return delta
        return self.pvs.get(pv, 0.0)

    def caput(self, pv, val):
        self.pvs[pv] = val
        # 模拟回读延迟（简单处理）
        if "SET" in pv:
            rb_pv = pv.replace("SET", "RB")
            if rb_pv in self.pvs:
                self.pvs[rb_pv] = val

mock = MockEpics()

# 覆盖原有的 caget/caput 函数，注入到控制器中
def caget(pv): return mock.caget(pv)
def caput(pv, val): return mock.caput(pv, val)

# ---------------------------------------------------------
# 导入我们之前写的控制器 (此处简写，确保逻辑一致)
# ---------------------------------------------------------
# 注意：在实际运行前，请确保 EnergyFeedbackController 在同一文件中或已正确导入
# 这里直接使用上面定义的逻辑

# ---------------------------------------------------------
# 模拟运行与绘图
# ---------------------------------------------------------
def run_simulation(steps=200):
    # 初始化控制器 (HV模式)
    ctrl = EnergyFeedbackController(
        mode="HV", rep_rate=100, # 模拟器跑快点
        kp=0.6, ki=0.15, lpf_alpha=0.3, 
        gain_hv=20.0, # 这里的增益需要根据 mock.sensitivity 调整
        i_limit=0.1
    )

    history = {"delta": [], "hv": [], "integral": [], "drift": []}
    
    print("Step | Delta | HV | Drift")
    print("-" * 30)

    for i in range(steps):
        # 1. 模拟环境中的能量漂移
        # 前 50 步稳定，50-150 步线性漂移，之后保持
        if i > 50:
            mock.true_energy_offset += 0.0002
        
        # 2. 运行控制器一步
        delta = ctrl.step()
        
        # 3. 记录数据
        history["delta"].append(delta if delta else 0.0)
        history["hv"].append(mock.pvs["MOD:HV:RB"])
        history["integral"].append(ctrl.integral_error)
        history["drift"].append(mock.true_energy_offset)

        if i % 20 == 0:
            print(f"{i:4d} | {history['delta'][-1]:+.2e} | {history['hv'][-1]:.3f} | {history['drift'][-1]:.4f}")

    # 4. 绘图
    plt.figure(figsize=(12, 8))
    
    
    
    plt.subplot(3, 1, 1)
    plt.plot(history["delta"], label="Filtered Delta (BPM Error)", color='red')
    plt.axhline(0, color='black', linestyle='--')
    plt.title("Energy Error (Delta)")
    plt.legend()
    plt.grid(True)

    plt.subplot(3, 1, 2)
    plt.plot(history["hv"], label="Actuator (HV)", color='blue')
    plt.title("Actuator Output (Modulator HV)")
    plt.legend()
    plt.grid(True)

    plt.subplot(3, 1, 3)
    plt.plot(history["integral"], label="Integral Term", color='green')
    plt.plot(history["drift"], label="Simulated External Drift", color='orange', linestyle=':')
    plt.title("Integral Error & External Drift")
    plt.legend()
    plt.grid(True)

    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    run_simulation()