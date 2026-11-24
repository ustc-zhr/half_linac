# HALF 数字影子下一窗口交接

整理日期：2026-11-18。现有模型链接口评审及六维接口首版合成验收已完成。L01B 历史粒子导出面和 ELEGANT 模型入口已溯源，正式绑定等待**指定出口法兰端面、ELEGANT 零点和末端场归属**。三屏重建继续延期。

## 工作位置与版本

- 专用工作区：`/home/zhanghaoran/gitproj/half_linac-dt`。
- 分支：`feature/digital-shadow-poc`。继续沿用，不因换窗口新建分支或重做实现。
- 交接整理前 HEAD：`e40deca`；最近源码修复为 `ba85ab3`。本交接另有文档提交，开始时以 git log 为准，不 checkout/reset 到旧哈希。
- 原工作区 `/home/zhanghaoran/gitproj/half_linac` 尚未合并这些实现，其中 `docs/DT/` 有用户本地附件；保持原样，不清理或覆盖。
- 开始先核对 `git status --short`、`git branch --show-current`、`git log -5 --oneline`，保留任何新增的用户改动。

## 当前已经完成

初始架构审查、单次快照驱动局部 ELEGANT、real 读取器的离线演练、VM 一次采集与版本核验、GUI/Launcher、归档重放、单帧无损图片池、背景复用、独立图像 ROI RMS 比较均已交付。用户已多次报告手动测试，最近一次对应下拉框交互修复。

Digital Shadow 新增 **Offline particle model** 和 CLI `run-model`。首版仅下游，支持模板束团、原生 SDDS 和匹配 `ALL_MAIN / C#1 entrance` 的六维粒子归档。三条入口已由独立 ELEGANT 跟踪验证，每次产出 13 屏横纵 RMS。运行使用设计参数，或离线加载已保存快照并覆盖四极铁 K1。预注入器和串联继续等待各自验收条件，VM 接入在 IMPACT-T 对照完成后另评估。 [使用与验收](MODEL_PIPELINE_CONTRACT_DRAFT.md#9-digital-shadow-首版调用与验收)

原 SIZE 与图像比较来源独立。图像残差只对通过工作点、发布和图像条件检查的 VM 生成；real/合成演练为 unverified，残差留空。没有进行真实机器采集或物理精度验收。

最近下拉框修复有 121 项定向回归及静态检查；图像比较阶段有 9 项 ELEGANT/GUI/私有 loopback IOC 集成。它们不是全仓库测试通过或实机验收的声明。既有验收不必为纯文档设计重复执行。

源码入口：

| 职责 | 当前接口 |
| --- | --- |
| 请求 | `src/shared/digital_shadow/models.py` / `ShadowRequest` |
| 一次会话 | `session.py` / `run_once()` |
| 当前模型链 | `pipeline.py` / `prepare_model()`、`predict()` |
| 离线粒子跟踪 | `particle_model.py` / `ParticleModelRequest`、`run_particle_model()` |
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
- 已将 `HALF_pre_1A_all.dat` 和 `HALF_pre_1A_main.dat` 逐粒子溯源到历史 PARMELA L01B 后 10 cm 导出面。当前 ELEGANT 模型从 `C#1 entrance` 开始，`ICT01` 和 `BPM02` 同在模型 `s=0.25 m`。2025-07-02 设备总图的热阴极方案为两台设备分别标注引线，法兰到模型零点的物理距离仍待确认，见草案第 2 节。

## 下一步范围与交付

初始评审以源码核对和文档为范围。经授权实施后，六维载荷读写、边界解析和等权粒子 SDDS 适配已通过定向测试及独立 ELEGANT 恒等、漂移和准直损失验收。正式绑定等待物理证据。现场/IOC/VM 运行、其他引擎和三屏重建仍独立安排。

设计评审见 [模型链与 L01B 出口六维束流接口草案](MODEL_PIPELINE_CONTRACT_DRAFT.md)。审阅范围包括：

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
