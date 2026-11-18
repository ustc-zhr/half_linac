# HALF 图像 ROI RMS 与模型的独立比较

2026-11-18，已在数字影子中接入保存原图驱动的独立图像 RMS 比较，先限于可核验的 VM 工作点。原 SIZE 标量比较、原图、背景和索引保持各自来源；没有新增 PV、控制操作、调束状态机或物理模型。用户此前报告已测试单帧归档，本次新增比较功能另行自动验收。

## 使用

在 `half_linac-dt` 重新打开 Digital Shadow，选择 **VM · capture once and predict**，勾选 **Save one original frame per screen**，正常采集后，在结果表上方切换 **Image ROI RMS**。模型预测、图像 RMS 和差值同时显示，图形也随所选来源切换；**SIZE / scalar diagnostics** 保留原比较。

有已归档图片的历史工作点可直接重放；不补读 PV、不复制原图。没有 VM 或真机时，可在 History → Open archive 打开本轮验收的 [vm_example](../../runs/digital_shadow_image_comparison/20261118/vm_example)，直接查看四屏比较，无需启动服务。

**Offline real capture demo · NO PV** 继续支持合成图像演练，新增八项图像结果为 unverified，残差留空。真实快照同样尚未取得同工作点图像诊断的现场验收，本阶段不给 real 图像生成有效残差。

## 数据和调用

`session.run_once()` 继续生成原 `comparisons`，另调用 `image_comparison.compare_image_diagnostics()`，输出独立的 `image_comparisons` 和 `image_comparison_status`，并保存 `image_comparisons.json`。不把 image PV 冒用为 SIZE PV。

每行使用 `PRFxx/image_rms_x` 或 `image_rms_y`，通过 prediction_key 对应原模型的 PRFxx/sigx 或 sigy；保存 image PV、图像和背景哈希、源时间、接收时间、冻结的几何/ROI、处理方法、算法文件哈希和分析标识。

比较从对象池重新读取原图/背景，复用既有 `_image_rms()` 和 `fit_beam_image(method="RMS moments")`，核对已有派生值及方法、测宽定义、背景标记。改变原派生数值或定义不能得到有效残差。image_fit.py、background_store.py 也纳入本次 input_hash 的实现依赖。原图索引保持原样，重放时新比较单独生成。

有效 VM 对应关系复用已有 `verify_vm_evidence()`：输入/结果版本、会话、Ready 状态、标量与计算结果一致，并要求已有 `publication.screens=True`、图像源时间处于该完成计算窗口、接收时间处于快照窗口。没有改 VM 发布器或同步流程。缺少该发布记录的旧档案仍可查看/重放，但不能自动认定图像工作点已核验。

## 比较条件与限制

| 状态 | 条件与行为 |
| --- | --- |
| compared | VM 版本及图像发布已核验，原图和处理有效，以下图像条件满足；显示图像减模型的工程差值 |
| limited | ROI 裁剪、边缘信号、像素分辨率或未处理负像素触发限制；保留测量与指标，残差留空 |
| unverified | real、合成演练，或缺少有效 VM 图像发布对应；保留有效测量，残差留空 |
| unavailable | 原图缺失、报警/时间/几何/处理定义或派生值不符等；说明原因，不生成残差 |

工程筛选规则为：ROI 保留至少 99.9% 的已记录正信号；整帧与 ROI 各自最外一圈像素的信号比例不超过 0.1%；该平面 RMS 至少跨两像素；没有未扣背景的负像素。这些是可追溯的筛选规则，不是现场精度验收阈值。

逐项记录 ROI 保留比例、整帧/ROI 边缘比例、背景扣除的信号比例、RMS 对应像素数、整帧 RMS，以及 ROI RMS 与整帧 RMS 的差。仅有视场内的图像不能证明视场外不存在束流；像素化、曝光、饱和、相机展宽和背景适用性仍影响比较。

compared 表示工程比较条件满足，不表示模型校准或真实机器物理精度通过。`physics_precision_validated` 始终为 false，不声明四屏同束团。任一被请求的图像比较不可用或受限时，总结果为 partial，同时单独保留预测完成和原 SIZE 状态。

## 验收

120 项定向回归、9 项 ELEGANT/GUI/私有 VM 集成及仓库静态检查通过。定向测试覆盖来源分离、VM 发布证据、ROI 裁剪、背景扣除、低像素分辨率、坏元数据和派生值篡改、单位、GUI 切换及归档重放。

私有 loopback IOC/ELEGANT 集成使用同一组粒子生成图像、全粒子标量及模型入口，四屏共八项图像比较成立。最大绝对图像差值为 0.0007023 mm，逐项满足像素半宽加已知有限视场偏差的界限，不要求图像残差为零。该界限只适用于本次已知粒子与一阶模型一致的 VM 样本，不作为现场精度容差。

最终测试日志、截图、像素检查数值、快照和可直接打开的 VM 示例保存在 [验收目录](../../runs/digital_shadow_image_comparison/20261118/)。未连接实机，现有 VM/IOC 运行文件保持原哈希，原图存储仍沿用已实现的去重和容量限制。
