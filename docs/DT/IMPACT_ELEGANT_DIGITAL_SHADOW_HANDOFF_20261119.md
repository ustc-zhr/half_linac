# HALF 注入器 Digital Shadow 接入交接

日期为 2026-11-19。后续工程接入在 VS Code 的 WSL Ubuntu 工作区 `/home/zhanghaoran/gitproj/half_linac-dt` 中进行，沿用 `feature/digital-shadow-poc` 分支。工作树已有正在进行的改动，接续时先用 `git status --short --branch` 核对并保留。物理算例和原始数据在仓库外的 `/home/zhanghaoran/half-impact-p0`。本次交接只新增本文件。

## 1. 当前进度

- **上游物理算例已验证。** IMPACT-T 3.1.5 从阴极跟踪到 L01B 下游，使用 Gauss-0.522 nC、4 万宏粒子、PIC 72×72×64、1 ps、原场和逐粒子出口孔径判定。对标使用 PARMELA V3.39、0.5°、SCHEFF 40×50。9.299 m 主时间窗内的能量、能散和归一化发射度已完成对标。横向 RMS 束斑及全束尾部仍有偏差，详见 P0 物理对标记录。
- **离线粒子串联已跑通。** 现有 Digital Shadow `run-model` 读取 IMPACT-T 六维归档，把粒子经 SDDS 接入 ELEGANT。逐粒子核对了位置、动量、到达时间和编号。ELEGANT 读入后的电荷、能量、束斑与发射度在浮点精度内一致，入口 `CCC` 时间清理保留了全部选中粒子。
- **快照驱动已验证。** 使用同一份上游束流，现有离线入口可从快照应用 `ALL_MAIN` 的 63 个四极铁 K1。合成回读的 QT02 单值探针只改变该元件，QT02 上游四屏逐项一致，下游 PRF09 水平 RMS 从 1.741 mm 变为 1.665 mm。此项验证用于确认快照参数能够传入跟踪。
- **HALF VM 同工作点工程验收已通过。** 用隔离 VM 与本机回环 IOC 运行现有 `ALL_MAIN`，采集单一 Ready 版本的 63 个 K1 和屏幕束斑。Digital Shadow 使用该轮 VM 快照独立运行。两个入口使用字节一致的 SDDS，参考动量、电荷、元件顺序和物理设置一致。13 屏、26 项横纵 RMS 的最大绝对差为 4.44×10⁻¹⁶ mm，原生 WATCH 粒子编号和坐标也逐项一致。两边均从 30,908 粒子、403.638 pC 跟踪到末端 25,263 粒子、329.918 pC，传输率 81.74%。该结果是同一 ELEGANT 格架的工程一致性验收。
- **ES01 与 RF 拼接已记录。** 验收使用设计格架的 ES01 水平半孔径 ±4 mm。IMPACT-T 在此丢 4,007 粒子，当前 PARMELA 对照丢 2,313 粒子，额外损失主要与 IMPACT-T 入射横向束宽有关。独立 ±20 mm 开度试验的 IMPACT-T 末端传输率为 90.72%，用于灵敏度对照。下游重匹配暂缓。三处主聚束器 RF 拼接台阶沿用原场，后续涉及低能尾或损失位置时复查。

## 2. 9.2994809 m 接法

| 项目 | 已验收约定 |
| --- | --- |
| IMPACT-T 末端 RF 场结束 | `z = 9.1994809 m` |
| IMPACT-T 原生过面 | `z = 9.2994809 m`，`screens.tsv` 元件索引 264。用通过孔径且纵向动量为正的首次过面记录 |
| ELEGANT 入口 | `ALL_MAIN / C#1 entrance`，即 `s = 0`。六维归档的边界 ID 为 `ELEGANT_ENTRANCE`，元件为 `C`，实例编号为 1，边为 `entrance` |
| 接续漂移 | 末端 RF 场到原生面已有 100 mm，由 IMPACT-T 计入一次。接入 ELEGANT 时的额外漂移为 0 mm，沿用 ELEGANT 的首段 `DHOTGUN0A = 0.25 m` |
| 选束 | 以 35.095529589 ns 为绝对相位零点，频率 2.856 GHz，严格取 −50° < Δφ < +10°。原生面有 32,022 个前向过面粒子，选中 30,908 个 |
| 参考动量与电荷 | 共享参考动量 114.34711644088068 MeV/c，归档电子束电荷 −4.036383508403361×10⁻¹⁰ C。ELEGANT `CHARGE.TOTAL` 使用电荷绝对值 |
| 坐标与时间 | 归档保留等权粒子及原始编号。SDDS 斜率用机械动量比 `px/pz`、`py/pz`，`p` 用总动量除以 `mc`。时间沿用 P0 绝对跟踪时钟，原离线入口的时间中心化、横向中心化及时间换号均设为 0 |

此前物理对标绘图用了回推后的 9.2990000 m 截面，两面相差 0.4809 mm。相同绝对时间窗在该对标面选中 31,043 个粒子。工程回放使用原生面上的 30,908 个粒子。

《HALF-注入器设备总图20250930.pdf》第 3 页 `IN-GA-01/02` 的俯视图支持这套名义接法。以 L01B 侧法兰配合面对应 IMPACT-T RF 场结束面为几何假设，按 BL01A 设计轨道入口切点反推，ELEGANT 零点估计比原生面下游约 0.7 mm，离线验收取零补偿。正式绑定需核实 RF 场图结束面对应的机械端面。ICT01、BPM02 和校正铁的实物定位另行核对，当前格架将它们放在同一 `s = 0.25 m` 截面。

## 3. P0 数据与可重复验收

以下路径是 WSL 本机路径。运行数据按路径引用。六维元数据 JSON 与其中声明的 NPZ 应放在同一目录，读入时校验哈希。

| 内容 | 路径 |
| --- | --- |
| IMPACT-T 原生过面记录 | [/home/zhanghaoran/half-impact-p0/final-benchmark/impact-t/reference-output/screens.tsv](/home/zhanghaoran/half-impact-p0/final-benchmark/impact-t/reference-output/screens.tsv) |
| 六维粒子归档入口 | [impact-native-entrance.json](/home/zhanghaoran/half-impact-p0/final-benchmark/comparison/impact-elegant-offline/impact-native-entrance.json) |
| 粒子载荷 | [particles-de75e3deec144947c10296a9b5e95734402e148139966b592a4416a768b40323.npz](/home/zhanghaoran/half-impact-p0/final-benchmark/comparison/impact-elegant-offline/particles-de75e3deec144947c10296a9b5e95734402e148139966b592a4416a768b40323.npz) |
| 原生面和时间窗元数据 | [handoff.json](/home/zhanghaoran/half-impact-p0/final-benchmark/comparison/impact-elegant-offline/handoff.json) |
| VM 同工作点验收摘要及完整归档路径 | [comparison.json](/home/zhanghaoran/half-impact-p0/final-benchmark/comparison/impact-elegant-offline/vm-same-working-point/comparison.json) |
| 13 屏逐项对比 CSV | [screen-comparison.csv](/home/zhanghaoran/half-impact-p0/final-benchmark/comparison/impact-elegant-offline/vm-same-working-point/screen-comparison.csv) |
| VM 工作点快照 | [vm-machine-snapshot.json](/home/zhanghaoran/half-impact-p0/final-benchmark/comparison/impact-elegant-offline/vm-same-working-point/vm-machine-snapshot.json) |
| VM Ready 版本和采集证据 | [vm-capture-evidence.json](/home/zhanghaoran/half-impact-p0/final-benchmark/comparison/impact-elegant-offline/vm-same-working-point/vm-capture-evidence.json) |
| 同工作点离线回放请求 | [shadow-request.json](/home/zhanghaoran/half-impact-p0/final-benchmark/comparison/impact-elegant-offline/vm-same-working-point/shadow-request.json) |
| 可供现有 GUI 打开的验收归档 | [shadow_4a35e4d85ae74cd68242844ec886c1c1](/home/zhanghaoran/half-impact-p0/final-benchmark/comparison/impact-elegant-offline/vm-same-working-point/shadow-runs/shadow_4a35e4d85ae74cd68242844ec886c1c1) |

验收覆盖 PRF02 至 PRF14。PRF04 和 PRF06–09 使用 VM 发布的 RMS PV，其余八屏使用同一计算版本的 VM 原生 WATCH 数据。`comparison.json` 和 CSV 分别记录了证据和每屏的数据来源。P0 同目录的 `capture_private_vm.py` 与 `compare.py` 保存了验收方法，私有 VM 与 IOC 在采集结束后停止。

在 WSL 的仓库根目录运行下面的命令，可复跑同一验收请求并逐屏比对。P0 目录可通过 `HALF_IMPACT_P0_DIR` 指定，回放归档和回归报告写入仓库的 `runs/`。

~~~bash
(
set -e
source scripts/setup.sh
export PATH="/home/zhanghaoran/gitproj/elegant/src/oag/apps/bin/linux-x86_64:/home/zhanghaoran/gitproj/elegant/src/epics/extensions/bin/linux-x86_64:$PATH"
export RPN_DEFNS=/home/zhanghaoran/gitproj/elegant/defns.rpn
p0_dir="${HALF_IMPACT_P0_DIR:-/home/zhanghaoran/half-impact-p0/final-benchmark/comparison/impact-elegant-offline}"
shadow_python="${HALF_SHADOW_PYTHON:-/home/zhanghaoran/anaconda3/envs/half_linac/bin/python}"
replay_case="$p0_dir/vm-same-working-point"
summary=$("$shadow_python" -B scripts/run_digital_shadow.py run-model \
  --request "$replay_case/shadow-request.json" \
  --beam-path "$p0_dir/impact-native-entrance.json" \
  --impact-handoff "$p0_dir/handoff.json" \
  --output-root runs/digital-shadow/p0)
run_dir=$(printf '%s\n' "$summary" | "$shadow_python" -c 'import json, sys; print(json.load(sys.stdin)["run_dir"])')
"$shadow_python" -B scripts/check_digital_shadow_p0.py \
  --run "$run_dir" --reference-dir "$replay_case" > "${run_dir}-regression.json"
printf '%s\n' "$run_dir" "${run_dir}-regression.json"
)
~~~

`run-model` 支持在请求中设置 `beam_path`、`snapshot_path` 和 `handoff_path`。请求中的相对路径以请求文件所在目录为基准。上面的 `--beam-path` 覆盖 P0 请求原有的绝对粒子路径。归档内的 `request.json` 使用归档内的粒子、快照和交接元数据，可直接传给 `run-model --request` 复跑。归档 `manifest.json` 记录原生过面和时间窗、粒子 NPZ 和转换后 SDDS 的 SHA-256、边界与源格架 SHA-256，以及 VM 快照 ID。回归报告记录 26 项 RMS 残差，检查阈值沿用 `1e-12 mm`。

在 Digital Shadow 的 **Run & Compare → Import request…** 载入同工作点的 `shadow-request.json`，在 **IMPACT-T handoff JSON** 选择本节的 `handoff.json`，然后点 **Run particle model**。请求自带 `handoff_path` 时，导入会自动回填。完成后，结果页显示名义接法 `0 mm`、实物基准待核及 VM 快照 ID。通过 **History → Open archive… → Use archived inputs for replay** 可回填归档内的粒子、快照和交接 JSON。用上面的 `check_digital_shadow_p0.py` 检查该 GUI 作业目录，可生成相同格式的逐屏报告。

## 4. 下一步工程接入

1. **一期固化冻结粒子与离线快照回放。** 复用 `Offline particle model → Downstream only` 和 `run-model`，请求使用 `scope=downstream`、`beam_source=archived`、`parameter_source=snapshot`。上游引用已验收的 IMPACT-T 六维归档，下游读取可配置路径下的 VM 快照。上游独立重算和两段串联的现行入口见第 7、8 节。
2. **复用已有数据契约和运行器。** [beam_state.py](../../src/shared/digital_shadow/beam_state.py) 负责 `BeamState6D`、边界和粒子归档读写。[beam_sdds.py](../../src/shared/elegant_backend/beam_sdds.py) 负责等权电子束的 SDDS 转换。[particle_model.py](../../src/shared/digital_shadow/particle_model.py) 中的 `ParticleModelRequest` 和 `run_particle_model()` 已负责 ELEGANT 入口校验、快照 K1、资源冻结、跟踪和屏幕预测。GUI 归档沿用 [jobs.py](../../src/apps/digital_shadow/jobs.py) 的 `read_archive(..., verify_particle_inputs=True)`。
3. **将串联来源写入可追溯的运行归档。** 一期用可配置路径指定六维输入，记录源 IMPACT-T 算例、原生截面、时间窗、粒子载荷哈希、100 mm 漂移归属、ELEGANT 格架哈希和 VM 快照 ID。`Coupled` 沿用下游归档束流入口，以本次作业生成的六维束流作为 ELEGANT 输入。
4. **用现有 13 屏验收作回归。** 核对同一个 SDDS 哈希、30,908 个粒子、403.638 pC、参考动量和 63 个 K1。对照 13 屏的横纵 RMS、原生粒子编号和末端 25,263 个粒子。归档应可由现有读取器打开。原验收的 4.44×10⁻¹⁶ mm 残差属于数值一致性，实机精度需按诊断另行验收。
5. **后续再接上游重算及实机对标。** 依据新的用户授权，补枪压、发射分布、螺线管及 RF 回读的 IMPACT-T 输入映射和版本绑定。实时机器采集和实机验证为独立阶段。当前实现范围优先沿用隔离 VM、离线归档和已验收的设计格架。

相关工程背景见 [NEXT_SESSION_HANDOFF.md](NEXT_SESSION_HANDOFF.md) 与 [MODEL_PIPELINE_CONTRACT_DRAFT.md](MODEL_PIPELINE_CONTRACT_DRAFT.md)。本文件的 P0 串联与 13 屏状态来自本轮已完成的回放及验收。

## 5. P1 离线上游重算

[名义工作点配置](../../configs/machines/half/benchmarks/digital_shadow_p1.json)固定 P0 输入清单、IMPACT-T 3.1.5 串行诊断构建和下游 VM 快照 ID。输入跟踪继续使用 40,000 个宏粒子、1 ps、PIC 72×72×64。重算以 P0 的原生过面、绝对相位窗和 ELEGANT 入口为准。

| 参数 | 离线输入与单位 | 版本和标定依据 |
| --- | --- | --- |
| DC 枪 | 起点 0 m，长 0.065 m，类型 112，`1T1.T7`，频率 0，场幅度系数 1。源图轴上电场积分约 −99.993 kV。 | 枪场图及输入卡由清单 SHA-256 固定。硬件高压读数与图积分的标定待核。 |
| 阴极发射 | `Flagdist=16` 读 `partcl.data`。坐标用 m，机械动量用 `p/(mc)`，行序为原始编号。参考动能 60 eV，`I=1.4919 A`，总电荷量 `I/f_RF ≈ 522.374 pC`。 | 同一冻结发射粒子文件。发射前等速推进的出生时刻为 `−z/v_z`。 |
| 螺线管 | 起点 −0.1 m，长 5.1 m，类型 3，`1T2.T7`，比例 1。场图两列 `Br,Bz` 用 T。 | P0 合成场图。单个线圈电流到场分量的标定待核。 |
| 预聚束器 | 0.50–0.70 m，类型 112，476 MHz，`1T10.T7`，幅度 0.19，相位约 10°。 | 采用 P0 绝对时钟和卡片幅相。 |
| 主聚束器与 L01A/L01B | 类型 112，2.856 GHz，电场图用 MV/m。场按 `cos(2πft+φ)` 取相位，阴极算例物理时刻从 0 开始。依照输入卡保留正交分量的相对相位。 | 公共 RF 源为 KLY1/LLRF01。L01A/L01B 的通道读数为相对监测量，绝对场幅和相位零点需单独标定。 |

单项扰动选 L01B 场幅度乘 1.01。卡片范围由场区 `[6.1565863, 9.1994809) m`、频率和场图编号确定，172 张卡的幅度列统一缩放，同时保留每对场分量相位及耦合器。按 P0 的 L01B 质心增能约 51.59 MeV 线性估算，动量响应应为正，量级约 +0.52 MeV/c。报告核对 L01A 出口粒子文件逐字节一致，再比较两组都入窗的同编号粒子动量、各自入口束流统计和下游 26 项 RMS。

复核了《HALF-注入器设备总图20250930.pdf》第 3 页 `IN-GA-01/02`（SHA-256 `8b97f9b4437e3dd43dd4fc61a88d0bf8ad7aadec816fb74d8c119745405af251`）。俯视图确认热阴极线的 L01B 下游布置及其与 BL01A 入口的关系。正式几何绑定还需带轴向基准的 L01B 部件图或测量记录，以及末端耦合器场图的机械坐标说明。P1 采用第 2 节的名义离线接法。

以下命令在 WSL 仓库根目录执行。两次 IMPACT-T 跟踪可并行，单次耗时参考 P0 约 85 分钟。结果写到 `runs/digital-shadow/p1/`。

~~~bash
(
set -e
source scripts/setup.sh
export PATH="/home/zhanghaoran/gitproj/elegant/src/oag/apps/bin/linux-x86_64:/home/zhanghaoran/gitproj/elegant/src/epics/extensions/bin/linux-x86_64:$PATH"
export RPN_DEFNS=/home/zhanghaoran/gitproj/elegant/defns.rpn
benchmark="${HALF_IMPACT_BENCHMARK:-/home/zhanghaoran/half-impact-p0/final-benchmark}"
impact_lib="${HALF_IMPACT_LIB_DIR:-/home/zhanghaoran/anaconda3/envs/impact/lib}"
shadow_python="${HALF_SHADOW_PYTHON:-/home/zhanghaoran/anaconda3/envs/half_linac/bin/python}"
mkdir -p runs/digital-shadow/p1
run_dir=$(mktemp -d "$PWD/runs/digital-shadow/p1/rebuild.XXXXXX")
runner=("$shadow_python" -B scripts/run_digital_shadow_p1.py --benchmark "$benchmark" --run-dir "$run_dir")
"${runner[@]}" run-impact nominal --impact-lib-dir "$impact_lib" &
nominal_job=$!
"${runner[@]}" run-impact l01b-amplitude-plus1pct --impact-lib-dir "$impact_lib" &
probe_job=$!
nominal_status=0
probe_status=0
wait "$nominal_job" || nominal_status=$?
wait "$probe_job" || probe_status=$?
test "$nominal_status" -eq 0 && test "$probe_status" -eq 0 && "${runner[@]}" compare
)
~~~

`compare` 输出 `comparison-*/report.json` 的路径。名义重算须逐粒子复现冻结六维载荷并通过 P0 的 13 屏 VM 回归。报告包含入口束流统计、两组同编号粒子的平均动量变化、末端粒子数及逐屏响应，两组 ELEGANT 归档可用已有归档读取器打开。

## 6. P2 截面绑定核对

**名义接法已核对，实物基准待确认。** 离线 `Coupled` 沿用 PRF02–PRF14 共 13 屏回归通过的接法。`z = 9.2994809 m` 原生面粒子直接注入 `ALL_MAIN / C#1 entrance`，额外漂移为 0 mm。从 RF 场结束面 `z = 9.1994809 m` 到原生面的 100 mm 已由 IMPACT‑T 跟踪一次。

| 证据 | 已核对的数值及适用范围 |
| --- | --- |
| IMPACT‑T 末端 RF | P0 `ImpactT.in` SHA-256 `a13f4500568218a9e010088ffb4e54410195d84a884dcd2548a25a03a717886c`。末端 112 卡从 9.1470172 m 起，长 0.0524637 m，终点为 9.1994809 m。 |
| 末端场图 | `COUPLE100.T7` 转换后逐字节复现 `1T28.T7`（SHA-256 `719d57491814fb2f533ed7a008026fbe968c01d8cbc82e826a35669f1404b7b4`）。场图为局部 0–5.24637 cm 网格。两份场图的轴向数据均需由部件图或测量资料绑定到机械基准。 |
| IMPACT‑T 原生记录 | 末端 RF 后的 100 mm 漂移只计一次。原生过面为 `z = 9.2994809 m`，边界编号 264。 |
| ELEGANT | 源格架 SHA-256 `1974e933b47370fe9deab7914f7195cf7b9de47f89340158360f35f58c46ee4c`。`ALL_MAIN / C#1 entrance` 为 `s = 0`，沿两段各 0.25 m 的漂移至 `BL01A#1 entrance`。 |
| 总图 | 第 5 节的 PDF，第 3 页 `IN-GA-01/02`，页对象 42，向量数据见内容流 62–65。图幅旋转后的横坐标沿束流减小。L01B 侧候选法兰配合面在 934.72 pt，BL01A 设计轨道入口曲线端点在 906.34 pt。按该视口 21.167 mm/pt，图上距离约 600.7 mm。主要图元的 0.12 pt 坐标步长折合约 2.54 mm，现场安装及端面识别的不确定度待核。 |

按 2025-09-30 总图，所选 L01B 法兰配合面到 BL01A 入口切点约为 600.7 mm。沿设计轨道从该切点向上游回退 500 mm 得到 ELEGANT 零点，约在法兰下游 100.7 mm。若 RF 场结束面与所选法兰面重合，ELEGANT 零点在 IMPACT‑T 原生导出面下游约 0.7 mm。这个数值为图纸估算，用于下述离线灵敏度试验。

设 `D` 为所选法兰配合面到 BL01A 入口切点的轴向距离，`δ_RF` 为 RF 场结束面相对该法兰的带符号轴向偏移，下游为正。ELEGANT 零点相对 IMPACT‑T 原生面的偏移为 `Δ = D − 500 mm − 100 mm − δ_RF`。正式标定需在同一轴向基准下确认法兰配合面、`D` 的安装尺寸或实测值，以及 `δ_RF`。ICT01/BPM02 的引线标注可复核设备顺序，有效测量面仍需各自的基准。

### 0 mm 与 +0.7 mm 离线灵敏度

两组使用同一批 P0 入窗粒子、同一源格架和同一份 63 个 K1 的 VM 快照。+0.7 mm 组在两平行截面间作逐粒子无场输运，保留原相位窗的粒子成员和绝对时钟，增加到达时间均值 2.335 ps。默认 0 mm 组的 26 项 RMS 对 VM 验收最大残差为 `4.44e-16 mm`。两组的入口均为 30,908 粒子、403.638 pC，末端均存活 25,263 粒子。+0.7 mm 组相对基线最大束斑变化为 PRF04 纵向 RMS 增加 `1.0362e-4 mm`。 [本机逐屏报告](../../runs/digital-shadow/p2/boundary_9a8328ebb0a54d63acca0e42b2987b37/report.json)

按第 3 节设置 ELEGANT 的 `PATH` 和 `RPN_DEFNS` 后，可从仓库根目录复跑该灵敏度试验。

~~~bash
p0_dir="${HALF_IMPACT_P0_DIR:-/home/zhanghaoran/half-impact-p0/final-benchmark/comparison/impact-elegant-offline}"
shadow_python="${HALF_SHADOW_PYTHON:-/home/zhanghaoran/anaconda3/envs/half_linac/bin/python}"
"$shadow_python" -B scripts/check_digital_shadow_boundary.py \
  --p0-dir "$p0_dir" --offset-mm 0.7 --output-root runs/digital-shadow/p2
~~~

### PB 幅相的离线证据

已用原 PARMELA 转换器逐字节复现 `SUBHC1.T7 → 1T10.T7`（目标 SHA-256 `84e158199db266d9c2450f1d9dff3ea837555c22754a0ffc6b33e09e9ba943a6`）。原 `run10dump.acc` 的 PB 卡相位为 100°、幅度系数 0.19。阴极时钟归零后的 IMPACT‑T 卡用 476 MHz、约 10°和同一幅度系数 0.19。模型相位换算为 `φ_Impact = φ_PARMELA − 90°`。

现场 `LLRFPB` 设定和 CH2 监测的信号身份可由控制配置与低电平对照表追溯。查阅的《HALF注入器能量与功率标定.xlsx》记录 L01–L20 功率源和增能，其中 L01 的 Buncher 数据属于 S 波段。PB 的幅度比例、相位极性和零点仍缺 476 MHz 采样的同点标定，需要带单位的拾取幅相、采样时刻及独立的腔场或束流相位参考。 [控制配置](../../configs/machines/half/control_backends/real.json#L2021)

## 7. 单 IMPACT‑T 离线运行

从本工作树打开 Digital Shadow，在 **Offline particle model → Pre-injector only** 中填写 **P0 benchmark directory** 和 **IMPACT-T library directory**。也可在启动 Control Room 前设置下列环境变量来预填目录。

~~~bash
export HALF_IMPACT_BENCHMARK="$HOME/half-impact-p0/final-benchmark"
export HALF_IMPACT_LIB_DIR="$HOME/anaconda3/envs/impact/lib"
HALF_LINAC_MACHINE_ID=half HALF_LINAC_CONTROL_BACKEND=vm bash scripts/runMe
~~~

**Upstream case** 默认选 **P0 nominal working point**。从全线归档打开独立上游阶段时，也可用该选择器重放 **P1 · L01B field amplitude +1%**。底部显示准备输入、跟踪步号和模拟时刻，以及导出阶段。默认跟踪超时为 7,200 秒，本机全量验收约需 67–85 分钟。**Stop** 会终止跟踪，作业历史保留 `interrupted` 状态和原生日志。

也可通过 **Import request…** 或 `python3 scripts/run_digital_shadow.py run-model --request preinjector.json --output-root runs/digital_shadow_gui/preinjector` 使用同一请求。下面是 `preinjector.json` 的示例，请求中的相对路径以文件所在目录为基准。

~~~json
{
  "schema": "digital_shadow_particle_request_v1",
  "scope": "preinjector",
  "impact_benchmark_dir": "~/half-impact-p0/final-benchmark",
  "impact_library_dir": "~/anaconda3/envs/impact/lib"
}
~~~

完成后，结果页显示 264 号原生面 `z = 9.2994809 m` 的粒子数和束流统计，以及 **0 mm 名义接法 · 实物基准待核**。归档的 `entrance.json`、同目录 NPZ 和 `handoff.json` 可供下游回放。**History → Use archived inputs for replay** 回填 P0 数据和运行库目录，新作业会重新校验输入。下游使用保存的 VM 快照与这份交接束流运行。 [下游回放入口](#3-p0-数据与可重复验收)

## 8. 全线离线运行

从 `feature/digital-shadow-poc` 工作树启动 Digital Shadow。启动前按第 3 节设置 ELEGANT 的 `PATH`、`RPN_DEFNS`，按第 7 节设置 IMPACT‑T 目录，或在表单中填写目录。

1. 选择 **Offline particle model → Coupled**，填写 **P0 benchmark directory**、**IMPACT-T library directory** 和 **VM snapshot JSON**。**Upstream case** 默认选 **P0 nominal working point**，也可选 **P1 · L01B field amplitude +1%**。回归比较时两组使用 P0 的 `comparison/impact-elegant-offline/vm-same-working-point/vm-machine-snapshot.json`。
2. 点击 **Run coupled model**。作业先校验并归档 VM 快照及所需四极铁 K1，再按所选工况运行 IMPACT‑T。P1 复用第 5 节的 172 张 L01B RF 卡幅度缩放，只写入本次作业的输入卡副本。跟踪进度以模拟时刻和步数显示。默认 IMPACT‑T 超时 7,200 秒，ELEGANT 超时 300 秒。
3. ELEGANT 自动接收本次导出的 `BeamState6D`，从 `z = 9.2994809 m` 原生过面直接注入 `ALL_MAIN / C#1 entrance`。末端 RF 后的 100 mm 已由 IMPACT‑T 跟踪，离线接续补偿为 0 mm。第 6 节记录名义接法核对结果及待确认的实物基准。全线作业只读取本地文件。
4. 完成后查看 PRF02–PRF14 的 26 项横纵 RMS、交接以及末端粒子数。**Stop** 终止当前阶段，已产生的日志和产物仍可在 **History** 打开。**Use archived inputs for replay** 回填工况、VM 快照和 P0 目录，显式重新运行会重新跟踪两段。

全线 `shadow_<id>/` 根目录包含请求、冻结快照、结果和阶段状态，`manifest.json` 记录所选工况和实际输入卡 SHA-256。`upstream/` 保存 IMPACT‑T 输入、日志和本次导出的 `entrance.json`、NPZ、`handoff.json`。`downstream/` 保存 ELEGANT 输入 SDDS、格架、63 个 K1、日志和结果。P0 逐屏验收以 `--run` 指定该 `downstream/`，用第 3 节命令和 P0 的 `screen-comparison.csv` 核对。P1 用第 5 节对比报告中的扰动组作为基准。

同一归档也可用 `python3 scripts/run_digital_shadow.py run-model --request <全线归档>/request.json --output-root runs/digital-shadow/coupled` 重算。新请求设置 `scope=coupled`、`parameter_source=snapshot`、`snapshot_path`、`impact_benchmark_dir` 和 `impact_library_dir`。`impact_variant` 支持 `nominal`（默认）和 `l01b-amplitude-plus1pct`。可分别用 `model_timeout_s` 和 `elegant_timeout_s` 调整两段超时。

2026-11-20 从离屏 GUI 实跑全线约 67 分钟。ELEGANT 使用本次导出的 30,908 个粒子，入口电荷 403.638 pC，参考动量 114.34711644 MeV/c，63 个 K1 与 VM 快照一致。PRF02–PRF14 的 26 项 RMS 均通过验收，最大绝对残差为 `4.44e-16 mm`，末端存活 25,263 个粒子。另一次 GUI 作业验证 Stop 结束 IMPACT‑T 进程、保留原生日志，ELEGANT 阶段为 `pending`。 [逐屏报告](../../runs/digital_shadow_gui/coupled/p0-acceptance-fc5wzapc/p0-screen-regression.json) · [停止验收](../../runs/digital_shadow_gui/coupled/p0-stop-8aqibp09/acceptance.json)

加入工况选择后，从离屏 GUI 并行实跑 P0 和 P1，每组约 74 分钟，使用同一份 VM 快照和 0 mm 交接。两组输入卡分别与原验收输入逐字节一致，L01A 出口粒子文件相同。P0 通过 13 屏回归，末端 25,263 粒子。P1 的六维粒子、输入 SDDS 和 26 项 RMS 复现第 5 节独立验收，参考动量比 P0 增加 0.51816 MeV/c，最大束斑变化为 PRF02 纵向 RMS 减小 0.01344 mm，末端 25,354 粒子。已核对归档的输入卡哈希和 History 工况恢复。 [P0/P1 全线验收报告](../../runs/digital_shadow_gui/coupled/p1-gui-acceptance-lfulqicn/acceptance.json)

### P0/P1 归档复验

在 `half_linac` Python 环境运行以下命令。`--p0-run` 和 `--p1-run` 均可指向 GUI `job_*` 目录或底层 `shadow_*` 目录。命令离线读取已有归档和参考数据。

~~~bash
source scripts/setup.sh
coupled_runs=runs/digital_shadow_gui/coupled/p1-gui-acceptance-lfulqicn
p0_reference="${HALF_IMPACT_BENCHMARK:-$HOME/half-impact-p0/final-benchmark}/comparison/impact-elegant-offline/vm-same-working-point"
python3 -B scripts/check_digital_shadow_coupled.py \
  --p0-run "$coupled_runs/nominal/job_20261120_133502_7dd5165210" \
  --p1-run "$coupled_runs/l01b-amplitude-plus1pct/job_20261120_133503_5659c9275b" \
  --reference-dir "$p0_reference"
~~~

复验取两组实际输入卡、L01A 出口粒子哈希、保存的 VM 快照和下游格架，核对原生面与 0 mm 名义交接。P0 沿用第 3 节的 VM 基准。P1 用第 5 节独立试验提取的仓库基准，包含原报告哈希，并从归档的 WATCH 文件重算 26 项 RMS。两组沿用 `1e-12 mm` 的绝对残差阈值。结果以 JSON 输出，可重定向保存。退出码 `0` 表示通过，`1` 表示输入或归档校验失败，`2` 表示 RMS 超出阈值。 [P1 参考数据](../../configs/machines/half/benchmarks/digital_shadow_coupled_acceptance.json) · [本次复验报告](../../runs/digital_shadow_gui/coupled/p1-gui-acceptance-lfulqicn/coupled-regression.json)

## 9. 第二个隔离 VM 工作点

2026-11-21 用生产 HALF VM worker 和本机回环的临时 IOC 计算 `ALL_MAIN`。以 P0 为基线，将 `QT02/K1` 从 `1.402247 m⁻²` 改为 `1.552247 m⁻²`。同一 Ready 版本采集了 63 个 K1 和 5 屏的横纵 RMS。新快照 `vm_capture_c924c03a8d1041c89375599ff5975b6c` 中其余 62 个 K1 与 P0 一致。其余 8 屏取同一计算版本的 WATCH 数据。采集完成后停止隔离服务。 [快照](../../runs/digital-shadow/vm-qt02-20261121.AAIkkv/vm-machine-snapshot.json) · [Ready 采集证据](../../runs/digital-shadow/vm-qt02-20261121.AAIkkv/vm-capture-evidence.json)

在离屏 GUI 的 **Downstream only** 导入新快照、P0 冻结六维束流和原生面交接记录。ELEGANT 入口 SDDS 与隔离 VM 字节一致，仍为 30,908 个粒子、403.638 pC、参考动量 114.34711644 MeV/c，额外漂移 0 mm。PRF02–PRF14 的 26 项横纵 RMS 全部通过，最大绝对残差 `4.44e-16 mm`。13 屏的 WATCH 粒子编号和六维坐标一致，两边末端均存活 25,263 个粒子。已验证从 History 打开归档并恢复回放输入。 [逐屏 CSV](../../runs/digital-shadow/vm-qt02-20261121.AAIkkv/screen-comparison.csv) · [独立验收报告](../../runs/digital-shadow/vm-qt02-20261121.AAIkkv/comparison.json)

与 P0 相比，位于 QT02 上游的 PRF02–PRF05 原生 WATCH 数据逐项一致。最大束斑响应出现在下游 PRF10 水平面，RMS 从 2.24159 mm 增至 3.62217 mm，变化 `+1.38058 mm`。固定 P0/P1 全线归档也已单独复验通过。 [新工作点回放请求](../../runs/digital-shadow/vm-qt02-20261121.AAIkkv/shadow-request.json)

## 10. 全线快照采集与 Follow 验收

2026-11-22 在私有回环 IOC 和生产 VM worker 上实测 GUI 的 **Capture snapshot**、**Run particle model** 与 **Follow**。QT02 `1.552247 m⁻²` 的一次采集使用 63 个 K1，入口 SDDS 与 P0 相同。随后跟随了三个不同的 VM Ready 版本。单次及三轮跟随的 26 项 RMS 最大绝对残差均为 `4.44e-16 mm`，末端粒子数与对应 VM 一致。第四轮跟踪期间实测 Stop 结束了 ELEGANT，前三轮归档仍可从 History 回放。 [隔离 VM 验收](../../runs/digital-shadow/particle-follow-20261122.673gmJ/vm-acceptance.json)

另用 Real 模拟读取器完成 GUI 采集、回放与跟随，相同输入只计算一次。持续变化的模拟回读配合真实 ELEGANT 发布了五轮。有待算工况的四次接续中，前一轮完成到下一轮启动的 P95 间隔为 24 ms，结果刷新间隔为 15.1–19.8 s。隔离 VM 三轮从快照采集结束到 GUI 显示的延迟为 14.8–20.8 s。 [Real 模拟 GUI 验收](../../runs/digital-shadow/particle-follow-20261122.673gmJ/demo/real-demo-acceptance.json) · [跟随节拍报告](../../runs/digital-shadow/particle-follow-20261122.673gmJ/cadence-acceptance.json)
