# Orbit Correct 响应矩阵

右侧常驻 BPM Selection 面板供 Run Correct 与 Response Matrix 共用。勾选 BPM 后，在 Response Matrix 点击 Measure Selected BPMs；Measurement Scope 显示数量和矩阵尺寸，悬停可查看设备名单。
测量页隐藏 X/Y 目标值及目标预设按钮，切回校正页保留原值。测量或校正运行期间锁定 BPM 选择。

每个 BPM 使用机器配置中同一位置的 X、Y 配对校正子；Global Correctors 的选择只控制轨道校正，不改变测量范围。
选择 N 个 BPM 时，扫描 N 个 X 校正子和 N 个 Y 校正子，保存 2N × 2N 矩阵。支持单个及非连续 BPM；HALF 默认仍全选。

矩阵文件旁的 JSON 保存实际设备名单与顺序。完整矩阵与局部矩阵均可加载，校正按设备名称取子矩阵。
启动时读取已有活动矩阵、测量完成或加载矩阵后，Global Correctors 自动选中矩阵覆盖的校正子。可手动缩小范围，普通刷新不会重置手动选择；再次点击加载会恢复该矩阵的全部校正子。超出覆盖范围会在启动前提示。
此联动只修改 Global Correctors，不改变 Target BPMs、目标值或 one-to-one 配对及响应来源。
矩阵列表悬停可查看覆盖名单。机器、后端、BPM 单位及矩阵质量检查继续生效。

命令行测量的第五个参数可指定逗号分隔的 BPM 名单（前四个参数依次为 kick、平均次数、等待时间、采样间隔）。省略名单仍测量全部配置设备。

校正页显示当前矩阵的尺寸、BPM 数量、时间及覆盖状态；完整文件名和设备名单见悬停提示。Manage Matrices 定位当前使用项。矩阵列表选择只用于浏览，点击 Use This Matrix 才启用并联动 Global Correctors；Return to Correction 返回校正页。运行期间不能切换活动矩阵。one-to-one 的 Measure Live 不要求矩阵，Active Matrix 显示配对范围覆盖状态。

Select Matrix Devices 按当前有效矩阵一次选中其全部 BPM 和 Global Correctors，保留所有目标值、校正方法及响应来源。加载矩阵本身仍不修改 BPM 勾选；无有效矩阵或任务运行时禁用此按钮。
