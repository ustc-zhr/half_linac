from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _bootstrap_gotacc_src() -> None:
    """Allow this wrapper to be loaded by file path without installing GOTAcc."""
    gotacc_src = Path(__file__).resolve().parents[1] / "GOTAcc" / "src"
    if str(gotacc_src) not in sys.path:
        sys.path.insert(0, str(gotacc_src))


_bootstrap_gotacc_src()

_UPSTREAM_CONFIG = (
    Path(__file__).resolve().parents[1]
    / "GOTAcc"
    / "config"
    / "task_configs"
    / "python"
    / "para_half.py"
)


def _load_upstream_config():
    if not _UPSTREAM_CONFIG.is_file():
        raise FileNotFoundError(f"GOTAcc HALF config not found: {_UPSTREAM_CONFIG}")
    spec = importlib.util.spec_from_file_location("_gotacc_half_para", _UPSTREAM_CONFIG)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load GOTAcc HALF config: {_UPSTREAM_CONFIG}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_upstream = _load_upstream_config()

DESCRIPTION = _upstream.DESCRIPTION
MACHINE_NAME = _upstream.MACHINE_NAME
TASK_NAME = _upstream.TASK_NAME
knob_para = _upstream.knob_para
obj_para = _upstream.obj_para
task_config = _upstream.task_config

__all__ = [
    "DESCRIPTION",
    "MACHINE_NAME",
    "TASK_NAME",
    "knob_para",
    "obj_para",
    "task_config",
]
