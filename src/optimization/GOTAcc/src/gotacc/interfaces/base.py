from __future__ import annotations

"""
Base backend interface for GOTAcc.

当前统一接口约定
----------------
所有 backend 都应围绕以下主接口组织：

1. init_knob_value() -> np.ndarray
   返回当前初始点（绝对坐标）。
   主要用于：
   - relative bounds -> absolute bounds 的解析
   - 运行前记录机器/仿真的初始状态

2. evaluate(x) -> scalar or vector
   在给定 x 处评估目标函数。
   这是 optimizer 唯一依赖的核心接口。

3. restore_initial() -> None   [可选但推荐]
   将系统恢复到初始化时的状态。
   主要用于：
   - 异常退出恢复
   - 用户中断恢复

4. set_best(optimizer=None) -> None   [可选]
   将 backend 认为的最优点写回系统。
   当前统一约定签名为：
       set_best(optimizer=None)

5. close() -> None   [可选]
   释放 backend 持有的资源。

设计说明
--------
- 不再围绕旧接口 evaluate_func()/set_init()/set_initial() 组织
- backend 对 runner 的正式承诺只有上面这几个方法
- 若某些具体 backend 想额外保留旧别名，也仅作为内部兼容，不再是主接口
"""

from abc import ABC, abstractmethod
from typing import Any

import numpy as np


class ObjectiveBackend(ABC):
    """
    GOTAcc backend 抽象基类。

    backend 的职责是把“外部世界”包装成统一目标函数接口。
    外部世界可以是：
    - EPICS 在线装置
    - 仿真器
    - benchmark function
    - 其他离线评估函数
    """

    @abstractmethod
    def init_knob_value(self) -> np.ndarray:
        """
        返回当前初始点（绝对坐标）。

        返回值要求
        ----------
        - 一维数值数组
        - 长度应与优化变量维度一致
        """
        raise NotImplementedError

    @abstractmethod
    def evaluate(self, x: np.ndarray) -> Any:
        """
        在给定 x 处评估目标函数。

        参数
        ----
        x:
            一维数值数组，表示当前待评估点

        返回
        ----
        Any:
            - 单目标时通常为标量 float
            - 多目标时通常为一维 ndarray
        """
        raise NotImplementedError

    def restore_initial(self) -> None:
        """
        将 backend 恢复到初始化时的状态。

        默认行为
        --------
        默认什么都不做。
        对纯离线 benchmark backend，这通常是合理的；
        对在线 EPICS backend，强烈建议覆盖实现。
        """
        return None

    def set_best(self, optimizer: Any | None = None) -> None:
        """
        将最优点写回 backend。

        统一签名
        --------
        set_best(optimizer=None)

        参数
        ----
        optimizer:
            当前优化器对象。
            backend 可以选择：
            - 使用 optimizer 中的信息
            - 完全忽略该参数，转而从自身记录的数据中选 best
        """
        return None

    def close(self) -> None:
        """
        释放 backend 资源。

        默认行为
        --------
        默认什么都不做。
        若 backend 持有文件句柄、socket、订阅器、监视器等资源，
        建议覆盖实现。
        """
        return None