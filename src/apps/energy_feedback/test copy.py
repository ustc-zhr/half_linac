import numpy as np
import matplotlib.pyplot as plt
import time

# =========================================================
# 1. 模拟环境 (Mock Engine)
# =========================================================
class MockAccelerator:
    def __init__(self):
        # 内部 PV 寄存器
        self.pvs = {
            "BPM:DSP:X": 0.0,
            "BPM:DSP:VALID": 1,
            "MOD:HV:SET": 40.0,
            "MOD:HV:RB": 40.0,
            "LLRF:IQ:I_SET": 1.0,
            "LLRF:IQ:Q_SET": 0.0,
            "LLRF:LAST1:PHASE_SET": 0.0,
            "LLRF:LAST2:PHASE_SET": 0.0
        }
        self.drift_value = 0.0  # 模拟外部环境漂移 (如温度)
        self.hv_sensitivity = 0.04 # 1kV 改变 0.04 的 delta

    def get_pv(self, pv_name):
        if pv_name == "BPM:DSP:X":
            # 物理公式: delta = (当前高压 - 基准高压) * 灵敏度 + 漂移 + 噪声
            noise = np.random.normal(0, 0.00005)
            delta = (self.pvs["MOD:HV:RB"] - 40.0) * self.hv_sensitivity + self.drift_value + noise
            return delta
        return self.pvs.get(pv_name, 0.0)

    def set_pv(self, pv_name, value):
        self.pvs[pv_name] = value
        # 模拟硬件响应回读 (Readback)
        if "SET" in pv_name:
            rb_name = pv_name.replace("SET", "RB")
            self.pvs[rb_name] = value

# 实例化模拟器
sim_env = MockAccelerator()

# =========================================================
# 2. 定义 Mock 的 caget/caput (不调用真正的 epics 库)
# =========================================================
def mock_caget(pvname):
    return sim_env.get_pv(pvname)

def mock_caput(pvname, value):
    sim_env.set_pv(pvname, value)

# =========================================================
# 3. 能量反馈控制器类 (为了演示，直接放在这里)
# =========================================================
class EnergyFeedbackController:
    # ... (这里放你之前完善后的完整代码，但要把 caget/caput 替换掉) ...
    # 为了测试方便，我在这里简单改写 step 方法内部调用的函数名
    def __init__(self, mode="HV", rep_rate=1.0, kp=0.5, ki=0.1, lpf_alpha=0.3):
        self.mode = mode
        self.rep_rate = rep_rate
        self.kp, self.ki = kp, ki
        self.lpf_alpha = lpf_alpha
        self.integral_error = 0.0
        self.last_delta_filtered = 0.0
        self.last_run_time = time.time()
        
        # 模式增益
        self.gain_hv = 20.0 # 增大增益以适应模拟环境
        self.i_limit = 0.2
        self.deadband = 1e-5

    def compute_delta(self):
        # 使用我们的 mock_caget
        x1 = mock_caget("BPM:DSP:X") 
        self.last_delta_filtered = (self.lpf_alpha * x1) + (1 - self.lpf_alpha) * self.last_delta_filtered
        return self.last_delta_filtered

    def step(self):
        if mock_caget("BPM:DSP:VALID") != 1: return None
        
        delta = self.compute_delta()
        if abs(delta) < self.deadband: return delta
        
        self.integral_error = np.clip(self.integral_error + delta, -self.i_limit, self.i_limit)
        
        # PI 计算
        total_adj = -(self.kp * delta + self.ki * self.integral_error) * self.gain_hv
        
        # 执行器
        hv_curr = mock_caget("MOD:HV:RB")
        mock_caput("MOD:HV:SET", hv_curr + np.clip(total_adj, -0.5, 0.5))
        
        return delta

# =========================================================
# 4. 执行模拟与绘图
# =========================================================
def run_test():
    ctrl = EnergyFeedbackController(kp=0.6, ki=0.2, lpf_alpha=0.4)
    
    steps = 300
    data_delta = []
    data_hv = []
    data_drift = []

    print("Running Simulation... (Press Ctrl+C to stop)")
    
    for i in range(steps):
        # 在第 100 步时模拟一个突然的能量跌落 (Drift)
        if i == 100:
            sim_env.drift_value = 0.005 
        # 在第 200 步时模拟一个持续的缓慢漂移
        if i > 200:
            sim_env.drift_value += 0.00005

        delta = ctrl.step()
        
        data_delta.append(delta)
        data_hv.append(mock_caget("MOD:HV:RB"))
        data_drift.append(sim_env.drift_value)

    # 绘图
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    
    ax1.plot(data_delta, color='red', label='Energy Error (Delta)')
    ax1.axhline(0, color='black', linestyle='--')
    ax1.set_ylabel("Delta")
    ax1.legend()
    ax1.grid(True)
    ax1.set_title("PI Controller Performance Simulation")

    ax2.plot(data_hv, color='blue', label='Modulator HV [kV]')
    ax2.plot(np.array(data_drift)*500 + 40, color='orange', alpha=0.3, label='Scaled Drift (Visible)')
    ax2.set_ylabel("HV [kV]")
    ax2.set_xlabel("Pulses")
    ax2.legend()
    ax2.grid(True)

    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    run_test()