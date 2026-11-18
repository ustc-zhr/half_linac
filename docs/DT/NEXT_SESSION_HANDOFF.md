# HALF 数字影子下一窗口交接

整理日期：2026-11-18。下一步为**现有模型链输入输出契约与 L01B 出口六维束流接口的设计评审**，先交付文档，审阅后再实施。三屏重建明确延期，不作为前置条件。

## 工作位置与版本

- 专用工作区：`/home/zhanghaoran/gitproj/half_linac-dt`。
- 分支：`feature/digital-shadow-poc`。继续沿用，不因换窗口新建分支或重做实现。
- 交接整理前 HEAD：`e40deca`；最近源码修复为 `ba85ab3`。本交接另有文档提交，开始时以 git log 为准，不 checkout/reset 到旧哈希。
- 原工作区 `/home/zhanghaoran/gitproj/half_linac` 尚未合并这些实现，其中 `docs/DT/` 有用户本地附件；保持原样，不清理或覆盖。
- 开始先核对 `git status --short`、`git branch --show-current`、`git log -5 --oneline`，保留任何新增的用户改动。

## 当前已经完成

初始架构审查、单次快照驱动局部 ELEGANT、real 读取器的离线演练、VM 一次采集与版本核验、GUI/Launcher、归档重放、单帧无损图片池、背景复用、独立图像 ROI RMS 比较均已交付。用户已多次报告手动测试，最近一次对应下拉框交互修复。

原 SIZE 与图像比较来源独立。图像残差只对通过工作点、发布和图像条件检查的 VM 生成；real/合成演练为 unverified，残差留空。没有进行真实机器采集或物理精度验收。

最近下拉框修复有 121 项定向回归及静态检查；图像比较阶段有 9 项 ELEGANT/GUI/私有 loopback IOC 集成。它们不是全仓库测试通过或实机验收的声明。既有验收不必为纯文档设计重复执行。

源码入口：

| 职责 | 当前接口 |
| --- | --- |
| 请求 | `src/shared/digital_shadow/models.py` / `ShadowRequest` |
| 一次会话 | `session.py` / `run_once()` |
| 当前模型链 | `pipeline.py` / `prepare_model()`、`predict()` |
| 原始到模型输入 | `src/shared/machine_profile/model_snapshot.py` / `build_model_snapshot_from_machine_state()` |
| 模型后端 | `src/shared/machine_profile/model_backend.py` / `ElegantModelBackend`、`build_model_backend()` |
| 采集与核验 | `real_capture.py`、`vm_capture.py` |
| 图像与比较 | `image_capture.py`、`image_store.py`、`image_comparison.py` |
| 现有 SDDS 束流源 | `src/virtual_machine/beam_source.py` |

表内未给全路径的文件属于 `src/shared/digital_shadow/`。没有统一 ModelPipeline/TwinSession 类；已有功能不因缺少该类名而需要重建。

## 已确认事实与延期项

- K1 是四极铁归一化强度设置，K1_total 是归一化强度回读，单位 `1/m^2`；real 模型使用回读，不用设置值或原始电流兜底。
- 68 块四极铁中 66 块有有效 K1/K1_total 映射。QDU01/02 缺有效归一化输入；QHL1～4 是当前未使用的格架定义，不是四块待新增的实机设备。
- PRF06/08 SIZE 为 mm、高斯拟合 σ；PRF07/09 单位已确认 mm，算法定义仍按 unknown 保留。不重复询问已确认的单位和含义。
- 没有独立 QT01 入口测量，也没有此前四屏原图及同期机器状态；不再要求补交这些不存在的历史资料。
- 三屏重建 PRF06/07/08 → PRF06 exit、留出 PRF09 仅记录为可选后续功能，**本轮不实现**。
- 当前物理路线为 `IMPACT-T / NN → L01B 出口 → ELEGANT，未来 Cheetah`。旧 HALF_DT_HANDOFF 中的 Cheetah 优先顺序为历史讨论。
- 当前 ShadowRequest 仅含动能和独立横向二阶矩。pipeline 拒绝 RF/弯转段和耦合/色散，6×6 传输矩阵不等于六维束流接口。
- 尚未明确 L01B 加速管出口在当前格架中的唯一位置。`BL01B` 是弯铁，不能代替该边界；L1 等 RF 定义重复，需核对 occurrence/edge。

## 下一步范围与交付

允许读源码和更新评审文档；本轮不改源码/机器配置，不连接 real PV，不启动 IOC/VM，不安装或实现 IMPACT-T/NN/Cheetah，不做三屏重建或调束闭环。

先交付一份契约草案，建议文件 `docs/DT/MODEL_PIPELINE_CONTRACT_DRAFT.md`（目前尚未创建），包括：

1. 现有 ShadowRequest、MachineStateSnapshot、ModelSnapshot、session/result/manifest 的接口对照与复用清单，避免平行数据体系。
2. L01B 边界位置的代码证据、未定事项，以及六维统计量/粒子表示的坐标、单位、参考粒子、时间、RF 相位、电荷、权重和损失约定。候选定义标明待评审，缺失信息不猜测。
3. ELEGANT 光学、未来 tracking 与 IMPACT-T/NN/Cheetah 适配器的能力边界和统一输入输出；拟新增 API 与现有 API 清楚区分。
4. 快照、束流、模型/标定版本、引擎与外部资源的绑定；最小局部重算和计算缓存失效规则。当前 input_hash/图片去重不是完整计算缓存。
5. 最小变更文件与接口清单、兼容策略、分阶段离线验收项及未决问题。先形成可审阅结果，再进入实现。

无需先新建通用 MachineStateManager、Task Engine 或改调束状态机。保持 Real/Virtual Backend 独立，复用 machine_profile、现有采样、模型后端、运行器和 worker。不要把历史 real 开发入口或 API 中的显式开关视为现场操作授权。

## 阅读顺序与环境

先读根 AGENTS.md、本文和 [目标差距回查](GOAL_GAP_REVIEW_20261118.md)。按涉及目录读取更具体 AGENTS.md，再看 [初始架构审查](ARCHITECTURE_REVIEW.md)第 6、7 节及上述核心代码。旧长交接 [HALF_DT_HANDOFF.md](HALF_DT_HANDOFF.md) 仅在需要物理背景时查阅，不必重读全部历史聊天或重做已经完成的审查。

HALF 模型配置为 `configs/machines/half/model_backends/simulation.elegant.json`，格架证据位于 `src/virtual_machine/half_elegant/elegant/lattice_ini.lte`。不要修改生成的 runtime JSON/LTE/ELE 或正在使用的 VM/IOC 文件。

后续确需验证时，沿用 conda 环境 half_linac；已有 Python 为 `/home/zhanghaoran/anaconda3/envs/half_linac/bin/python3`。常用入口见根 AGENTS.md 的 scripts；不要自动启动运行服务。验收产物位于被忽略的 runs 下，模型链设计不依赖重新采集。现成 GUI 示例为 `runs/digital_shadow_image_comparison/20261118/vm_example`，可离线查看。
