# HALF Q 铁能量参考

仅管理 `IN:L02:ENG`、`IN:L04:ENG` 至 `IN:L18:ENG` 九个 PV。
电流换算与有效 K1 保持由现有控制系统完成。

设备在 `configs/machines/half/machine.json` 中以 `kind: energy_reference` 定义，
PV 映射位于 `configs/machines/half/control_backends/real.json` 的对应 `energy` 通道。
应用通过共享 machine-profile 加载器按类型读取；默认仍为离线演示。

从仓库根目录启动离线演示：

Control Room 的 **Machine & Tools → Q Energy Reference** 在 HALF / real 模式可用，
连接实际能量 PV，箭头直接写入；VM 模式禁用该入口。
写权限由 `configs/machines/half/apps/energy_buttons.json` 管理。
独立脚本不带参数时仍使用离线演示：

```sh
bash scripts/start_energy_buttons.sh
```

确认现场 EPICS 环境后，显式连接实际 PV：

```sh
bash scripts/start_energy_buttons.sh --epics
```

选择范围，增减或缩放生成目标，也可直接编辑目标列，然后应用修改。
微调步长默认为 1 MeV，可自行修改。上方 ◀/▶ 立即写入选中项，每行 ◀/▶ 只立即写入该项。
箭头基于当前值增减，不使用或提交目标列中尚未应用的编辑。支持连续点击，按住 400 ms 后每 150 ms 重复调节。
后台串行执行，同一范围的连续待执行步长合并；失败时清空待执行调节并停止按住重复，不自动重试。
手动编辑、生成目标、加载方案和恢复仍需点击 Apply。离线演示中的箭头只修改内存数据。
计算基于最近读取的当前值；重复生成不会累积缩放。
方案保存全部九项的目标值（未编辑项使用当前值），加载不写 PV。
恢复上次设置仅载入本次会话上一批操作前的能量，需点击应用；不保证恢复原磁铁电流。

写入前检查全批次连接、权限、有效正数、PV 提供的控制范围和基准值。
逐项写入失败时停止，不自动回滚或重试。确认仅针对能量 PV，不代表电流到位。
跨 PV 操作不具备原子性，检查与写入之间无法排除其他控制端并发修改。
不使用未提供的磁铁限流数据；现场需验证原系统的联动和保护。
记录保存到仓库 `logs/energy_buttons/operations.jsonl`，包括离线操作。

依赖 PyQt5；EPICS 模式额外需要 pyepics。后台线程处理 PV 通信。
