# Documentation

这里保留当前维护仍需要的说明。文档按读者任务分层：先看入门与运行，需要改
profile 时看平台与机器，需要 app 细节时进入应用目录，历史长文只放在归档中。
历史日志、单次审查报告和临时测试投递步骤不再进入索引；需要追溯时请查 git
历史。

## 入门与运行

- [getting_started/SETUP_AND_RUN.md](getting_started/SETUP_AND_RUN.md): 完整环境准备、检查和启动方式
- [getting_started/ELEGANT_INSTALL.md](getting_started/ELEGANT_INSTALL.md): `elegant`、SDDS Toolkit 和 Python `sdds` 安装说明

## 平台与配置

- [platform/PLATFORM_POSITIONING.md](platform/PLATFORM_POSITIONING.md): 平台定位、边界和架构分层
- [platform/MACHINE_PROFILE_PRINCIPLES.md](platform/MACHINE_PROFILE_PRINCIPLES.md): machine profile 设计原则
- [platform/APP_WORKFLOW_CONFIG_PRINCIPLES.md](platform/APP_WORKFLOW_CONFIG_PRINCIPLES.md): 应用 workflow JSON 设计原则
- [platform/MODEL_SNAPSHOT_DESIGN.md](platform/MODEL_SNAPSHOT_DESIGN.md): 模型快照和 elegant model backend 契约
- [platform/gui_style.md](platform/gui_style.md): PyQt GUI 风格约定
- [../configs/machines/README.md](../configs/machines/README.md): machine profile 文件结构与配置职责

## 机器接入

- [machines/ADD_SECOND_MACHINE.md](machines/ADD_SECOND_MACHINE.md): 新机器接入最小路径
- [machines/IRFEL_MACHINE_PROFILE.md](machines/IRFEL_MACHINE_PROFILE.md): IRFEL profile 说明

## 应用说明

- [apps/BEAM_DYNAMICS_OPERATION_NOTES.md](apps/BEAM_DYNAMICS_OPERATION_NOTES.md): BBA、Twiss 等束流动力学计算的操作边界说明
- [apps/DISPERSION_CORRECTION.md](apps/DISPERSION_CORRECTION.md): 色散校正应用运行边界和 commissioning 信息
- [apps/ENERGY_TUNING_PIPELINE.md](apps/ENERGY_TUNING_PIPELINE.md): Energy Spectrum 与 RF 能量自适应扫描的测量层、亮度寻峰和中心锁定
- [apps/dispersion_correction/](apps/dispersion_correction/): 色散校正的配置、写入路径、能量约定和技术路线细节
- [apps/emit_measurement/tupost059.pdf](apps/emit_measurement/tupost059.pdf): 发射度测量参考资料

## 维护清单

- [backlog/PENDING_ISSUES.md](backlog/PENDING_ISSUES.md): 当前仍需跟进的问题和 commissioning 清单

## 归档

- [archive/PROJECT_OVERVIEW_FULL.md](archive/PROJECT_OVERVIEW_FULL.md): 原 README 详细版归档；日常入口以根目录 README 和本索引为准
