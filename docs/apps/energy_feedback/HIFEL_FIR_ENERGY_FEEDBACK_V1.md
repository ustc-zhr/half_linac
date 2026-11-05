# HiFEL FIR 束线能量反馈第一版实施说明

## 1. 文档目的

本文档用于指导 `half_linac` 仓库中 HiFEL FIR 束线能量稳定功能的第一版实现。

第一版只实现：

- 基于 FIR 色散区 BPM 的束流能量诊断；
- 以 `MODULATOR_HV1` 高压为执行量的经典 I/PI 能量反馈；
- RF 幅度、相位和高压状态的安全监视；
- 完整的逐样本数据记录，为后续 NN 训练准备数据。

第一版不实现 NN 训练、NN 影子预测或 NN 前馈。后续 NN 在线推理应作为同一个 `energy_feedback` 应用中的控制模式接入，不能创建另一个拥有高压写权限的在线控制应用。

仓库地址：<https://github.com/ustc-zhr/half_linac>

## 2. 装置与试验范围

HiFEL 注入器及两条束线的设备顺序如下：

```text
Gun
 → SS01
 → SHB
 → SS02
 → FCT
 → Buncher（MS01）
 → BPM01
 → ACC1（LS01）
 → BPM02
 → BPM03
 → 分束

FIR：
DM5 → QM5 → QM6 → BPM04 → QM7 → QM8 → DM6
    → QM9 → QM10 → BPM05 → FIR波荡器

MIR：
ACC2 → QM11 → QM12 → BPM07
    → DM8 → QM13 → QM14 → BPM08 → QM15 → QM16 → DM9
    → QM17 → QM18 → BPM09 → MIR波荡器
```

当前只有 FIR 束线正常运行，因此第一版仅覆盖：

```text
BPM02、BPM03、BPM04、BPM05
MODULATOR_HV1
KLY1/ACC1、Buncher相关RF幅度和相位
必要的束流存在信号
```

MIR、ACC2及其dogleg不在第一版范围内。

## 3. 总体实现决策

### 3.1 不新增顶层在线控制应用

在现有 `src/apps/energy_feedback/` 上扩展，不新建另一个 FIR Energy Feedback GUI。

原因：

- `energy_feedback` 已经是配置驱动、可支持多机器的通用能量反馈应用；
- 已具备测量质量判断、控制限幅、独立读回验证、失败回滚、运行日志和能量控制互斥锁；
- 新建应用会重复安全与写入逻辑；
- 多个应用同时拥有 `MODULATOR_HV1` 写权限会带来控制冲突。

### 3.2 保留现有 `hv_feedback`

`src/apps/hv_feedback/` 继续作为独立的“RF幅度—高压关系调试及设备状态验证工具”。它不再作为新能量环的内环，也不能与新的能量反馈同时闭环运行。

需要从 `hv_feedback` 复用或抽取：

- 高压范围、单步限制和会话累计限制；
- 高压设定值与读回值偏差检查；
- ACC1与Buncher的RF幅度、相位及幅度比检查；
- 已在 HiFEL 实机验证过的采样、平均和动作节拍。

`hv_feedback` 也应接入与 `energy_feedback` 相同的控制互斥机制，避免两个应用同时写入高压PV。

### 3.3 在线控制链

```text
BPM02/03/04
  → FIR能量诊断provider
  → I/PI控制器
  → 通用限幅与会话限制
  → RF/相位/高压安全门
  → MODULATOR_HV1.voltage_set
  → 独立高压读回验证
  → ACC1场幅及束流能量变化
```

BPM05只用于监视dogleg出口和波荡器入口前的轨道，不进入第一版反馈误差。

低电平系统的相位反馈是高压能量反馈运行的外部前提。仓库当前的 `llrf_control` 是幅相操作界面，不包含软件自动相位补偿。第一版需要监视相位读回；如果相位漂移超过限制，能量反馈必须进入 `HOLD`，不能假设软件会自动纠正相位。

## 4. 现有代码结论

### 4.1 `energy_feedback`

现有模块具备：

- `DirectEnergyProvider`；
- 基于三BPM传输矩阵的 `BpmTransportEnergyProvider`；
- 通用 `ShadowControlGroup` 与一个或多个执行器；
- 每步限幅、会话累计限幅、设备绝对范围；
- setpoint写入、独立readback验证和失败回滚；
- BPM时间戳、陈旧数据、时间偏斜及四极铁变化的HOLD保护；
- CSV日志与运行元数据；
- 能量控制互斥锁。

需要注意：当前控制算法为增量式积分控制，而不是完整PI：

```text
delta_u = -loop_gain * error / response
u_next  = u_current + delta_u
```

这等价于离散I控制。第一版应明确选择：

- 保留现有I控制作为最初的安全commissioning模式；
- 同时增加真正的PI控制器，支持 `kp`、`ki`、采样周期、输出限幅和anti-windup；
- 设置 `kp = 0` 时应退化为与现有I控制相容的模式。

建议上机顺序为：先I控制，再逐步启用P项，不要直接使用未经响应标定的PI参数。

### 4.2 `hv_feedback`

现有控制对象是选定RF通道的幅度，执行器是 `MODULATOR_HV1`：

```text
relative_error = (amplitude_reference - amplitude) / amplitude_reference
delta_hv       = gain_kv_per_relerr * relative_error
```

HiFEL现有配置参数：

- 采样周期：1 s；
- 平均窗口：10 s；
- 控制周期：10 s；
- 高压单步限制：0.02 kV；
- 单次会话累计限制：0.2 kV；
- 高压安全范围：38.5～39.5 kV；
- ACC1和Buncher相位漂移限制：1°；
- ACC1幅度相对范围：0.95～1.05；
- Buncher/ACC1幅度比漂移限制：2%。

这些值应作为新能量反馈第一轮测试的默认安全上限，而不是自动认定为最终最佳控制参数。

### 4.3 已有高压能量knob

`configs/machines/irfel/apps/dispersion_correction.json` 已将 `MODULATOR_HV1` 配置为能量knob，并已用于实机流程：

- 小扫描步长：0.004 kV；
- 高压读回容差：0.015 kV。

第一版应尽量复用其写入和读回语义，用于标定高压到FIR能量观测量的响应。

### 4.4 当前FIR模型缺口

当前 `src/virtual_machine/irfel_elegant/elegant/lattice_ini.lte` 的 `ALL_MAIN` 描述MIR路径，包含BPM02、BPM03和BPM07～BPM10。

BPM04、BPM05虽然在lattice文件中声明，但尚未进入一条完整的FIR line；DM5、DM6及FIR的QM5～QM10也没有形成可供现有三BPMprovider自动发现的传输路径。

因此第一版不能直接将现有模型型 `bpm_transport` provider 配置为BPM02/BPM03/BPM04。可以选择：

1. 先增加实测标定型 `bpm_dispersion` provider，尽快完成实机反馈测试；
2. 后续补齐 `ALL_FIR` Elegant line，再切换或增加模型型三BPM重建。

第一版推荐方案1。

## 5. 第一版FIR能量诊断

### 5.1 实测型诊断量

使用BPM02、BPM03估算进入dogleg前的横向轨道分量，从BPM04水平位置中扣除该分量：

```text
q_energy = x04 - a*x02 - b*x03 - q_reference
```

其中：

- `x02`、`x03`、`x04`均使用水平位置，单位建议统一为mm；
- `a`、`b`通过固定高压时的上游水平轨道小扫描标定；
- `q_reference`在开始采集后由操作员确认或由一段稳定数据建立；
- BPM04是色散区主测量点；
- BPM05只作下游轨道保护和性能评价。

如果已经有可信的BPM04色散值，可以进一步换算为：

```text
delta = q_energy / eta_x_BPM04
delta_E = E_reference * delta
```

但第一版闭环不应依赖未经实机验证的绝对色散。可以先直接使用 `q_energy` 和实测的 `q_energy/kV` 响应完成稳定性测试。

### 5.2 标定步骤

1. 保持高压、RF幅度和相位稳定。
2. 使用两个独立的上游水平轨道扰动，采集BPM02、BPM03、BPM04响应。
3. 拟合 `a`、`b`，使纯轨道扰动在 `q_energy` 中最小。
4. 恢复轨道工作点。
5. 保持低电平相位反馈开启，使用0.004 kV量级的小高压扫描。
6. 计算：

   ```text
   response_q_per_kv = d(q_energy) / d(HV)
   ```

7. 验证响应符号、线性区间、延迟和重复性。
8. 将拟合参数、采样区间、设备读回、工作点及时间写入运行元数据。

### 5.3 测量质量门槛

下列任一情况发生时，provider应返回无效测量并触发HOLD：

- BPM02、BPM03或BPM04断连；
- 任一BPM位置非有限数；
- BPM时间戳缺失或陈旧；
- 三个BPM时间戳偏差超过配置值；
- 束流不存在或强度低于阈值；
- BPM04位置或 `q_energy` 超出合理范围；
- 用于诊断的固定磁铁设置发生超限变化；
- 校准参数缺失、非有限或版本不匹配。

当前HiFEL machine profile未配置FCT元素，只配置了ICT02～ICT04和BPM的`s`通道。第一版需要明确选择一个beam-present信号：

- 优先增加并使用FCT PV；或
- 暂时使用可靠的BPM `s`/ICT信号并配置阈值。

不能仅通过“BPM位置是否为零”判断束流存在。

## 6. 第一版控制器

### 6.1 控制变量与执行器

```text
controlled variable: q_energy 或 delta_E
actuator: MODULATOR_HV1.voltage_set
readback: MODULATOR_HV1.voltage_readback
```

执行器不得改成LLRF相位。RF相位在第一版中是必须保持稳定的受监视量。

### 6.2 建议的PI形式

推荐采用带anti-windup的离散位置式PI：

```text
e[k] = target - measurement[k]
I[k] = clamp(I[k-1] + ki*T*e[k], I_min, I_max)
u_raw[k] = u_bias + kp*e[k] + I[k]
u[k] = apply_step_and_session_limits(u_raw[k])
```

也可以采用等价的增量式PI，但必须具备：

- 显式 `kp` 与 `ki`；
- 输出饱和时的anti-windup；
- 启动、停止和模式切换时的bumpless transfer；
- 测量无效、写入失败或HOLD期间冻结积分；
- 控制器重启时不得自动恢复旧积分状态；
- 控制方向由标定响应决定，不能硬编码符号。

### 6.3 控制节拍

第一轮建议保留HiFEL已验证的慢反馈节拍：

```text
采样：1 s
统计窗口：10 s
控制动作：10 s一次
```

采样与动作周期必须解耦：日志仍逐秒记录，但控制器只在新统计窗口准备好且全部安全条件满足时动作。

统计量建议使用中值或截尾平均，以降低偶发BPM坏点影响。不得在每一个1 s样本到达时都写高压。

### 6.4 初始安全限制

```text
absolute HV range: 38.5 ... 39.5 kV
maximum step: 0.02 kV
maximum session delta: ±0.2 kV from session origin
HV readback tolerance: 0.015 ... 0.02 kV
phase drift limit: 1 deg for ACC1 and Buncher
ACC1 amplitude relative range: 0.95 ... 1.05
Buncher/ACC1 ratio drift limit: 2%
```

配置应同时与machine profile中 `MODULATOR_HV1.voltage_set` 的绝对设备限值求交集。

## 7. 安全与并发要求

### 7.1 唯一写入者

以下应用或流程可能写入同一个高压PV：

- 新的HiFEL能量反馈；
- 原有 `hv_feedback`；
- `dispersion_correction` 的能量knob扫描；
- 其他人工高压控制界面。

至少 `energy_feedback`、`hv_feedback` 和自动扫描流程必须使用统一的设备控制锁或协调控制事务。反馈启动前需要确认高压写入权；失去锁时立即停止写入并进入HOLD。

### 7.2 初始发布状态

新增HiFEL `energy_feedback` profile时，第一提交应默认为：

```json
{
  "real_status": "read_only",
  "write_control": {"default": "blocked", "real": "blocked"}
}
```

完成离线测试、实机监视和shadow计算后，再单独提交或明确修改为写入允许状态。不要在第一次代码合并时默认开放实机高压写权限。

### 7.3 HOLD条件

除测量质量问题外，以下情况必须HOLD并停止自动写入：

- 高压设定与读回不一致；
- 建议值超出绝对、单步或会话限制；
- ACC1/Buncher相位漂移超限；
- ACC1幅度或Buncher/ACC1幅度比超限；
- BPM05出口轨道偏离参考值超过配置阈值；
- 束流丢失；
- 写入失败、验证超时或回滚失败；
- 相同BPM样本被重复用于多次控制动作；
- 控制锁失效或被其他应用占用。

写入失败后不得自动继续闭环，需要操作员重新确认。

## 8. 数据记录与后续NN准备

虽然第一版不实现NN，但必须一次性记录足够的训练数据，避免后续重新上机补采。

每个1 s样本至少记录：

- 本地采样时间和各PV源时间戳；
- BPM01～BPM05的原始 `x`、`y`、`s`；
- `q_energy`、其原始值、滤波值和参考值；
- `a`、`b`及响应标定版本；
- BPM时间戳偏差和质量状态；
- 束流存在信号及其原始值；
- `MODULATOR_HV1`设定值、读回值和会话起点；
- ACC1幅度、相位；
- Buncher幅度、相位；
- RF幅相安全检查结果；
- 控制器模式、`kp`、`ki`、P项、I项；
- 原始控制建议、限幅后建议、实际写入值；
- 写入验证、回滚和HOLD原因；
- 相关磁铁读回或至少其稳定性检查结果；
- 低电平相位反馈是否开启（如果存在可读状态PV）。

日志中必须区分：

- `MONITOR`：只采集，不计算写入；
- `SHADOW`：计算控制建议，但不写入；
- `APPLIED`：写入并验证成功；
- `HOLD`：安全条件不满足；
- `WRITE_FAILED`/`ROLLBACK`：写入或恢复异常。

## 9. 操作界面

继续使用现有 `Energy Feedback` 应用，但将硬编码的phase术语改成通用术语：

- `Actuator`或`Control Knob`；
- 当前执行器名称和单位由profile提供；
- HALF可以显示相位执行器；
- HiFEL显示 `Modulator HV1`，单位kV。

HiFEL第一版建议提供以下模式：

1. `Monitor`：采集BPM和RF/HV数据；
2. `Shadow I/PI`：计算控制建议但不写入；
3. `I/PI Feedback`：在获得控制锁和操作员确认后写入高压。

界面必须清楚显示：

- 当前机器和束线：HiFEL / FIR；
- 测量源：BPM02/03/04；
- 执行器：MODULATOR_HV1；
- 相位反馈前提状态；
- 当前高压安全余量和会话累计变化；
- 能量观测量、BPM05轨道和HOLD原因；
- 当前是只读、shadow还是实机写入状态。

一旦 `configs/machines/irfel/apps/energy_feedback.json` 被profile加载，现有Control Room launcher应复用同一个 `Energy Feedback` 入口，不需要增加新的启动按钮。

## 10. 建议代码改动位置

### 必需改动

- 新增：`configs/machines/irfel/apps/energy_feedback.json`
- 修改：`src/shared/machine_profile/energy_feedback.py`
  - 支持实测型 `bpm_dispersion` source；
  - 校验BPM列表、单位、系数、beam-present信号和安全门配置。
- 修改：`src/apps/energy_feedback/measurement.py`
  - 增加 `BpmDispersionEnergyProvider` 或更通用命名；
  - 实现时间戳、束流存在、范围和滤波检查。
- 修改：`src/apps/energy_feedback/profile_runtime.py`
  - 解析BPM02/03/04、BPM05、HV和RF保护信号；
  - 支持非模型型BPM诊断；
  - 解析通用执行器显示信息。
- 修改：`src/apps/energy_feedback/engine.py`
  - 保留现有I模式；
  - 增加状态明确、可测试的PI控制器；
  - 支持独立动作周期和anti-windup。
- 修改：`src/apps/energy_feedback/main.py`
  - 增加10 s控制节拍；
  - 在写入前执行RF/HV安全检查；
  - 将phase专用UI文字改为通用执行器文字；
  - 完整记录特征和控制分量。
- 修改：`src/apps/energy_feedback/writer.py`
  - 将错误信息中的 `phase write` 改成通用 `actuator write`；
  - 保留独立读回验证和回滚。
- 修改：`src/apps/hv_feedback/`
  - 接入统一的高压控制锁；
  - 不改变已commissioned的原控制含义。

### 建议新增测试

- `tests/test_irfel_fir_energy_feedback.py`
  - BPM轨道扣除公式；
  - BPM时间戳偏差和陈旧数据；
  - 束流丢失；
  - 高压响应符号；
  - 相位、幅度及比例安全门；
  - 1 s采样和10 s动作节拍；
  - 高压单步、累计和绝对限值；
  - 控制锁冲突；
  - 写入失败、验证失败和回滚；
  - PI anti-windup和无扰切换；
  - read-only、shadow和write-enabled状态。

继续运行并保持通过：

- `tests/test_energy_feedback.py`
- `tests/test_bpm_energy_feedback.py`
- `tests/test_hv_feedback.py`
- `tests/test_energy_control_lock.py`
- `tests/test_machine_profile.py`

## 11. 分阶段实施与验收

### 阶段A：离线实现

- 增加HiFEL profile和实测型BPM provider；
- 增加PI与RF/HV安全门；
- 默认read-only；
- 单元测试全部通过；
- 不连接实机、不启动长时间IOC/GUI进程。

### 阶段B：实机监视

- 只运行 `Monitor`；
- 验证PV映射、单位、时间戳、束流存在信号；
- 验证BPM02/03/04同步；
- 验证RF幅相与高压安全门不会误报；
- 连续采集一段稳定运行数据。

### 阶段C：标定与shadow

- 标定轨道扣除系数 `a`、`b`；
- 标定 `q_energy/kV` 响应、延迟和符号；
- 在shadow模式运行I/PI；
- 检查建议高压步长、累计漂移和HOLD逻辑；
- 不进行自动高压写入。

### 阶段D：I反馈实机测试

- 经操作员确认后开放写入；
- 从现有I控制和较小增益开始；
- 保持10 s动作周期；
- 验证能量观测量收敛、BPM05轨道和RF相位稳定；
- 验证停止、HOLD和写入失败后的行为。

### 阶段E：PI测试

- 在I反馈已稳定的基础上逐步增加P项；
- 对比I与PI的收敛速度、超调和噪声放大；
- 固化最终参数和适用工作点；
- 形成可供后续NN训练的数据集。

### 验收指标

至少比较反馈开启前后：

- `q_energy`或换算能量的RMS；
- 峰峰值和长期漂移；
- 收敛时间与超调；
- BPM05水平位置RMS；
- ACC1/Buncher相位RMS；
- FCT/ICT/BPM强度有效脉冲比例；
- 高压动作次数、饱和次数和会话累计变化；
- HOLD及写入失败次数。

具体数值目标应在首次monitor和标定数据获得后确定，不应在缺少实机基线时硬编码。

## 12. 第一版明确不做的内容

- NN模型训练；
- NN影子预测；
- NN前馈写入；
- MIR束线能量反馈；
- ACC2控制；
- 自动优化四极铁、螺线管或校正铁；
- 自动启停外部低电平相位反馈；
- 未经实测验证的绝对能量精度声明。

## 13. 后续扩展方向

第一版稳定后再考虑：

1. 补齐HiFEL FIR Elegant line，包括DM5、QM5～QM10、DM6、BPM04和BPM05；
2. 将实测型诊断与模型型三BPM诊断交叉验证；
3. 从现有日志生成按运行时段划分的训练集；
4. 离线训练能量扰动预测模型；
5. 在同一应用中增加 `NN Shadow`；
6. 最后增加有严格限幅、异常回退和PI残差校正的 `NN Feedforward + PI`。

在线NN不应绕过现有PI、安全门、设备限制、写入验证或控制锁。

## 14. 提交给Codex的简要任务描述

> 在 `half_linac` 仓库中扩展现有 `energy_feedback`，为HiFEL FIR束线增加基于BPM02/BPM03/BPM04的实测型色散能量诊断，并使用 `MODULATOR_HV1` 高压作为执行器。复用现有 `hv_feedback` 的RF幅相及高压安全限制，增加1 s采样/10 s动作节拍、I/PI控制、anti-windup、通用执行器UI、完整数据记录和统一控制锁。BPM05仅作出口轨道保护。第一版默认read-only，先完成monitor与shadow，不实现任何NN功能，也不要默认开放实机写权限。保持HALF现有能量反馈及HiFEL现有高压反馈行为和测试兼容。
