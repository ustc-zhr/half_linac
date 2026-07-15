# Accelerator HLA Platform Positioning

## 产品定位

本仓库是一个配置驱动的多机器电子加速器高层应用平台。它为不同装置提供统一的设备抽象、控制后端选择、虚拟机与模型集成、物理应用运行和实机写入治理能力。

推荐的英文描述是：

> A configurable, multi-machine high-level application platform for electron accelerators.

推荐的中文简称是“加速器高层应用平台”。在需要强调当前验证范围时，使用“多机器电子直线加速器高层应用平台”。

## 为什么称为平台

平台的复用单元不再是某台机器的 GUI 副本，而是稳定的运行契约：

- machine profile 描述机器元素、顺序、物理限制和逻辑通道
- control backend 将逻辑通道映射到 real 或 vm PV
- app workflow 只保存不能从机器元素推导的扫描、图像和推荐默认值
- model backend 描述 elegant 等分析或模拟资源
- shared runtime 负责配置加载、能力判断、通道解析、运行状态和写入策略
- application suite 根据机器能力加载同一套诊断、测量和校正应用

HALF 和 IRFEL 是目前的平台实例。它们具有不同的设备清单、PV 命名、模型资源和默认后端，但使用共同的应用和运行时基础设施。

## 架构分层

### Platform Core

平台核心主要位于 `src/shared/`，负责：

- machine profile 加载与校验
- 元素发现与逻辑通道解析
- control/model backend 选择
- 应用能力和 commissioning 状态判断
- 实机写入控制
- VM、softIOC 和模型运行路径解析
- 共享运行状态、进程和窗口生命周期辅助

### Application Suite

`src/apps/` 和 `src/optimization/` 提供面向加速器运行的高层功能。目前包括轨道显示与校正、束流图像、BBA、发射度、能谱、螺线管中心测量、色散校正、抖动分析和优化工作流。

并非所有应用都已经完全纳入 machine-profile 契约。`GOTAcc` 和 jitter analysis 仍按外部维护集成代码管理，后续只有在确有需要时再逐步接入平台核心。

### Machine Profiles

`configs/machines/<machine_id>/` 描述具体机器。机器 profile 是平台实例配置，不是应用代码的分支。

当前实例包括：

- `half`：HALF Linac
- `irfel`：IRFEL

新机器应优先通过配置接入。只有当现有元素、通道或后端契约确实无法表达物理需求时，才扩展平台核心。

## 平台边界

“通用”指加速器高层应用领域内的多机器复用，不表示平台已经与所有设备类型和基础设施无关。

当前边界包括：

- 已由两台电子直线加速器验证
- 控制系统以 EPICS Channel Access 为主
- VM 和模型计算以 elegant 为主
- 操作界面以 PyQt 桌面应用为主
- 设备语义围绕 BPM、corrector、quadrupole、bend、flag、solenoid 和 RF 等加速器元素

因此现阶段不宣称已经普遍支持储存环、质子或重离子装置、非 EPICS 控制系统和任意模型引擎。这些能力需要由实际机器接入和稳定接口继续验证。

## 运行与安全定位

平台覆盖从离线开发到实机 commissioning 的连续路径：

```text
offline -> VM/softIOC -> real read-only -> write smoke test -> commissioned
```

应用能否在特定机器和后端运行，不只取决于配置文件是否存在，还取决于所需设备、逻辑通道、模型资源和实机 commissioning 状态。写入控制是平台契约的一部分，不应由单个 GUI 临时决定。

## 历史命名策略

`half_linac` 当前仍是仓库、Python 导入路径、conda 环境和部分环境变量使用的历史名称。第一阶段只改变产品叙述，不执行破坏性重命名：

- 不修改 `half_linac` Python 导入路径
- 不修改 `HALF_LINAC_*` 环境变量
- 不修改现有启动脚本和部署目录
- 不修改 HALF 专属的 `half_elegant`、`halflinac` 和 machine id

文档中应区分两种含义：

- Accelerator HLA Platform：整个软件平台
- HALF / IRFEL：平台支持的具体机器实例

未来若迁移仓库名或 Python 包名，应提供兼容导入和环境变量过渡期；HALF 专属资源仍应保留 HALF 命名。

## 演进原则

- 新机器优先新增 machine profile，不复制应用代码
- 应用优先依赖元素类型、逻辑通道和能力，不依赖机器名称
- machine profile 保持机器原生且易维护，避免细粒度应用角色膨胀
- control backend 保持物理量和单位语义明确，不混用实机电流与 VM kick/angle
- app workflow 只保存无法从机器元素推导的事实
- 平台能力以实际机器和运行验证为依据，不以抽象层数量为依据
