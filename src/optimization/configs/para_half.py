from __future__ import annotations

import sys
from pathlib import Path


def _bootstrap_gotacc_src() -> None:
    """Allow this wrapper to be loaded by file path without installing GOTAcc."""
    gotacc_src = Path(__file__).resolve().parents[1] / "GOTAcc" / "src"
    if str(gotacc_src) not in sys.path:
        sys.path.insert(0, str(gotacc_src))


_bootstrap_gotacc_src()

from gotacc.configs.py_cfg.para_half import (  # noqa: E402
    DESCRIPTION,
    MACHINE_NAME,
    TASK_NAME,
    knob_para,
    obj_para,
    task_config,
)

__all__ = [
    "DESCRIPTION",
    "MACHINE_NAME",
    "TASK_NAME",
    "knob_para",
    "obj_para",
    "task_config",
]
