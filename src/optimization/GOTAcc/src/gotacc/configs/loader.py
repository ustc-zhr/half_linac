from __future__ import annotations

"""
统一配置加载器（精简版）

支持的配置来源
--------------
1. YAML 文件
   例如：
       configs/half.yaml
       configs/irfel.yaml

2. Python 文件
   例如：
       /path/to/para_half.py
       /path/to/para_irfel.py

3. Python 模块路径
   例如：
       gotacc.configs.para_half
       gotacc.configs.para_irfel

支持的配置入口
--------------
只支持以下两种现代写法：

A. 模块级常量
   TASK_CONFIG = TaskConfig(...)

B. 模块级函数
   def task_config() -> TaskConfig:
       return TaskConfig(...)

不再支持旧接口：
   - machine_para()
   - optimizer_para()
   - run_para()

设计目标
--------
1. 简化配置逻辑，避免新旧接口长期并存
2. 统一所有配置最终都返回 TaskConfig
3. 对错误给出清晰提示，方便快速定位
"""

import importlib
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Mapping

from gotacc.configs.schema import TaskConfig, task_config_from_dict, validate_task_config
from gotacc.configs.validators import validate_task_config_strict


def load_task_config(spec: str | Path) -> TaskConfig:
    """
    统一入口。

    参数
    ----
    spec:
        可以是：
        1. YAML 文件路径
        2. Python 文件路径
        3. Python 模块路径

    返回
    ----
    TaskConfig
        经过基础结构校验 + 严格语义校验后的统一配置对象
    """

    # 确保输入为path
    if isinstance(spec, Path):
        spec = str(spec)

    path = Path(spec)

    # ------------------------------------------------------------
    # 情况 1：spec 是一个实际存在的文件路径
    # ------------------------------------------------------------
    if path.exists():
        suffix = path.suffix.lower()

        if suffix in {".yaml", ".yml"}:
            cfg = load_yaml_config(path)

        elif suffix == ".py":
            cfg = load_python_file_config(path)

        else:
            raise ValueError(
                f"Unsupported config file suffix: {path.suffix!r}. "
                "Only .yaml / .yml / .py are supported."
            )

    # ------------------------------------------------------------
    # 情况 2：spec 不是文件路径，则按 Python 模块路径处理
    # ------------------------------------------------------------
    else:
        cfg = load_python_module_config(spec)

    # 两层校验：
    # 1) schema.py: 基础结构校验
    # 2) validators.py: 更细的语义校验
    cfg = validate_task_config(cfg)
    cfg = validate_task_config_strict(cfg)
    return cfg


def load_yaml_config(path: str | Path) -> TaskConfig:
    """
    从 YAML 文件读取 TaskConfig。

    YAML 文件应当能被 safe_load 成一个字典，
    然后再由 task_config_from_dict() 转成 TaskConfig。
    """
    try:
        import yaml
    except ImportError as exc:
        raise ImportError(
            "YAML config requested but PyYAML is not installed. "
            "Install it with: pip install pyyaml"
        ) from exc

    path = Path(path)

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, Mapping):
        raise TypeError(
            f"YAML config must load as a mapping/dict, got {type(data).__name__}"
        )

    return task_config_from_dict(data)


def load_python_file_config(path: str | Path) -> TaskConfig:
    """
    从 Python 文件动态加载模块，再提取 TaskConfig。
    """
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    module_name = f"_gotacc_cfg_{path.stem}"

    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load Python config from file: {path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return _task_config_from_module(module)


def load_python_module_config(module_name: str) -> TaskConfig:
    """
    按模块路径导入配置模块，再提取 TaskConfig。
    """
    module = importlib.import_module(module_name)
    return _task_config_from_module(module)


def _task_config_from_module(module: ModuleType) -> TaskConfig:
    """
    从一个已导入模块中提取 TaskConfig。

    仅支持两种形式：
    1. TASK_CONFIG = TaskConfig(...)
    2. def task_config() -> TaskConfig

    若两者都没有，则报错。
    """
    # ------------------------------------------------------------
    # 方式 1：模块级常量 TASK_CONFIG
    # ------------------------------------------------------------
    if hasattr(module, "TASK_CONFIG"):
        raw = getattr(module, "TASK_CONFIG")

        if isinstance(raw, TaskConfig):
            return raw

        if isinstance(raw, Mapping):
            return task_config_from_dict(raw)

        raise TypeError(
            f"In module {module.__name__!r}, TASK_CONFIG must be either "
            f"TaskConfig or mapping/dict, got {type(raw).__name__}"
        )

    # ------------------------------------------------------------
    # 方式 2：模块级函数 task_config()
    # ------------------------------------------------------------
    if hasattr(module, "task_config"):
        fn = getattr(module, "task_config")

        if not callable(fn):
            raise TypeError(
                f"In module {module.__name__!r}, task_config exists but is not callable."
            )

        raw = fn()

        if isinstance(raw, TaskConfig):
            return raw

        if isinstance(raw, Mapping):
            return task_config_from_dict(raw)

        raise TypeError(
            f"In module {module.__name__!r}, task_config() must return "
            f"TaskConfig or mapping/dict, got {type(raw).__name__}"
        )

    raise AttributeError(
        f"Config module {module.__name__!r} must provide either:\n"
        f"  1. TASK_CONFIG = TaskConfig(...)\n"
        f"  2. def task_config() -> TaskConfig"
    )