# Magnet Cycle

从当前 machine profile 的 `kind` 自动加载二极铁、四极铁、校正磁铁和螺线管。
使用 `limits.current_set` 的完整上下限，单位为 A；缺少完整 limits 的磁铁不可选。
双极范围会完整跨零往返，包括 HALF BL01A/B 的 −100～100 A。

从仓库根目录启动离线模拟（不导入 EPICS、不连接 PV）：

```bash
bash scripts/start_magnet_cycle.sh --machine half
```

Control Room → Machine & Tools → Magnet Cycle 使用 EPICS 模式，跟随主界面的机器与 VM/real 选择。
命令行不加 `--epics` 仍为离线模拟。
模拟电流从范围内的零值开始，若零不在范围内则取最近端点；模拟不验证磁滞、
电源反向能力或实际励磁效果。模拟各逻辑磁铁独立，实际模式按设定 PV 去重。

显式连接 VM 电流通道：

```bash
bash scripts/start_magnet_cycle.sh --machine irfel --backend vm --epics
```

实际 PV 模式始终使用 `current_set` / `current_readback`，不降级为 K1、angle 或 kick。
HALF VM 当前没有磁铁电流映射，因此其 EPICS 模式会显示不可用原因；离线模拟可用。
IRFEL 四极铁、二极铁当前缺少电流 limits，需要在 machine.json 补充后使用。
实机写入已启用（`write_control.real: allowed`），Control Room 使用 `WRITE` 访问标识。

```bash
bash scripts/start_magnet_cycle.sh --machine half --backend real --epics
```

EPICS 模式省略 `--backend` 时跟随 Control Room 环境变量，未设置时使用机器默认模式。
打开窗口不会开始 cycle；选择磁铁、设置参数并点击开始后才进行读写。

## 流程

保存所有选中电源的初始设定，并检查范围和读回一致性，然后：

1. 斜坡到下限，连续稳定后保持。
2. 斜坡到上限、稳定保持，再回下限、稳定保持，重复 N 次。
3. 从下限斜坡回初始设定，连续稳定后完成（无需额外保持）。

每个电源独立推进。软件最多每 0.2 秒推进一个 `rate × 0.2` 的小步，延迟后
不会追赶大步写入；实际速率可以更慢。读回超过跟踪容差时暂停推进并计时，
端点读回离开容差后重新累计稳定与保持时间。任何读写失败、非有限数值、
外部设定变化或等待超时，整组停止后续写入，不自动恢复或归零。
已经完成的磁铁不再读取或写入。

停止不能撤回正在通信的命令，也不能替代电源硬件停止；电源可能继续到最后
下发的值。窗口关闭时先请求停止，线程退出后方可关闭。
共用设定 PV 只执行一次；共用 PV 的限值或读回映射不一致时拒绝开始。
同一机器、后端的 EPICS cycle 实例用进程锁互斥；该锁不约束其他应用，
其他应用修改设定会在下一次读回检查时触发停止。

初始参数为 3 次、1 A/s、端点保持 2 s、容差 0.05 A、稳定 1 s、超时 30 s。
这些是工具初值，并非经过磁铁或电源标定的运行参数。不同电源应分组选取合适
参数运行；完整电流范围不意味着已经验证磁饱和或重复性。

每次运行在本目录 `runtime/<machine>/<backend>/runs/` 下记录 JSONL 日志，
包含模式、参数、初始设定、每次成功写入及完成/异常原因。
