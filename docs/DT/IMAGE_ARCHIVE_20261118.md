# HALF 数字影子单帧原图归档

2026-11-18，按任务保存每屏一帧，保留收到的数组类型并用 NPZ 无损压缩。该功能为可选项，默认关闭，没有连续录像、自动删除或自动屏幕操作。用户此前报告已测试上一阶段的离线演练；本次新增图像功能另行完成下述自动验收，未连接实机。

## GUI 操作

在 `half_linac-dt` 工作区重新打开 Launcher → Digital Shadow：

1. 选择 **Offline real capture demo · NO PV**，点击 **Load real demo**。
2. 勾选 **Save one original frame per screen**，保留默认 **10 GiB limit** 或设置适用上限。
3. 点击 **Run offline real demo**。无需启动 VM 或 IOC，图像为明确标记的合成 uint16 数组。
4. 结果提示保存数量，展开 **Saved images · ROI RMS [mm]** 查看原始数组类型、压缩大小和两平面 ROI RMS。原 SIZE 比较继续独立显示。
5. History → **Use archived inputs for replay** 使用同一份原图引用，不再次读取相机或复制图片。

VM 一次采集模式也支持同一选项，需要已有虚拟 IOC/VM。真实采集仍须选择原有显式 real 模式并点击读取按钮；本轮没有执行。

界面显示图片目录占用、容量上限和磁盘余量。占用达到上限的 80% 时提示；新增对象会超过上限时拒绝保存该对象，结果标为 partial 并保留原因，不删除旧数据。已有对象可继续复用。输出目录仍可写时，图像失败不阻止已有标量快照和局部预测归档；磁盘本身完全不可写时，无法保证 JSON 结果仍能落盘。

## 存储及重放

GUI 在选定输出目录共享 `image_store/<sha256>.npz`，每个 run 保存小型 `image_index.json`。对象身份包含 dtype、shape 和完整像素字节；相同原图或背景只存一份，已有对象不覆盖。多个任务写同一池时使用文件锁，按实际文件占用检查上限，包括中断时可能遗留的 `.image_*.tmp`，避免临时文件绕过占用统计。

索引绑定 MachineStateSnapshot 的 ID、内容哈希和后端，逐屏记录来源 PV、源时间、接收时间、报警、返回元素数、连接时元素数、标定/ROI、原始 dtype/shape、字节数及对象校验值。数组保持在独立文件中，不改变标量快照 v1 的排除数组约定。

图片是配置 image PV 实际返回的完整数组，在数字影子做翻转、背景扣除和数值转换之前保存。当前 VM IOC 的 flag.template 返回固定宽度字符串数组，归档保留该类型，派生计算另行记录 numeric_string_conversion；没有修改 IOC 模板或 PV 映射。现场相机的实际类型仍未验证。

重放校验索引及所有对象，缺失或损坏即拒绝使用；input_hash 绑定索引及引用。图片池路径目前为绝对路径。备份时必须同时保存索引和共享图片池，并保留可解析的池路径；只复制单个 job/run 目录不是完整图片备份。本阶段没有自动搬迁、清理或保留期任务。

## 背景与派生 RMS

背景可选，通过 **Backgrounds JSON** 或 CLI 指定逐屏映射，路径相对于该 JSON；必须提供来源，不会把一张背景隐式用于其他屏：

```json
{
  "PRF06": {
    "path": "backgrounds/prf06.npy",
    "provenance": "说明实际采集条件、时间和来源"
  }
}
```

背景使用原生相机方向的二维 `.npy`，或仅含 `image` 数组的 `.npz`；尺寸必须匹配该屏。背景原类型及文件哈希保留，通过同一图片池去重。来源文字不自动证明曝光相同或与束流帧同期。

派生处理复用 `subtract_background()` 与 `fit_beam_image(method="RMS moments")`：扣除可选背景，再应用 profile 的 flip_y、像素标定和 default_roi。完整原图保留，供未来重算。索引记录 ROI、背景、计算类型和处理规则，结果标为 **image_roi_projection_rms**，不将 Gaussian/unknown SIZE 改标为 RMS。

新图像表是独立诊断展示，尚未用图像 RMS 自动替代原比较残差，也没有自动入口重建或校准。相机饱和、曝光/增益、有限视场及图像标定误差尚未验收；ROI RMS 不作为全分布粒子 RMS 的精度认证。

## 调用与时间范围

`image_capture.ImageCapture` 在连接前按所选诊断去重屏幕，解析对应后端的 image PV 和几何配置。real 采集先读必要回读和标量诊断，再尝试图像及其他全机状态；VM 在同一完成计算的前后版本检查内读图。每屏至多一次 get，不重复采样，不延长原窗口。原图压缩、背景存储和 RMS 计算在读取结束后执行。

连接失败、尺寸不符、坏类型、报警、过期帧、无信号或容量不足有逐屏原因。源时间和接收时间与快照绑定，但不声明四屏同束团或多屏可同时插入。

`session.run_once(..., image_bundle=...)` 只消费已保存的索引及对象，不访问 PV。共享采样器、调束状态机、写入安全机制、IOC/VM 发布器及机器配置保持原样。

CLI 示例：

```bash
python3 scripts/run_digital_shadow.py example --real-demo --output-dir runs/image_demo_inputs
python3 scripts/run_digital_shadow.py demo-real \
  --request runs/image_demo_inputs/request.json --output-root runs/image_demo \
  --save-images --image-limit-gib 10
```

默认池为 `<output-root>/image_store`，可用 `--image-store` 复用其他池；`--image-backgrounds` 传背景映射。replay 自动读取 snapshot 旁的 image_index.json，也可显式给 `--image-index`。real 演练因 SIZE 定义不兼容仍返回 2（partial），不代表图片保存失败。

## 验收

- 102 项定向回归通过：原类型逐字节恢复、去重、每屏一帧、背景逐屏绑定、文件损坏、容量不足不删旧图、读取窗口、跨后端拒绝、已知解析 RMS 数值及 GUI 可选操作等。
- 9 项 ELEGANT/GUI 集成通过：带图/不带图的 real 演练及重放、私有 loopback IOC 的实际 VM 四屏图像归档、原 VM 采集及进程清理。
- 静态检查通过；实机 PV 访问为 0，现有 VM/IOC 运行文件哈希不变。

结果、截图、归档和日志保存于 [runs/digital_shadow_images/20261118](../../runs/digital_shadow_images/20261118/)，由既有忽略规则排除，不加入源码。
