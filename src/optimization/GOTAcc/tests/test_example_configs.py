from pathlib import Path

import pytest

from gotacc.configs.loader import load_task_config


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_load_python_task_config_from_config_directory():
    cfg = load_task_config(REPO_ROOT / "config" / "task_configs" / "python" / "para_half.py")

    assert cfg.meta.machine == "HALF"
    assert cfg.backend.type == "epics"
    assert cfg.optimizer.name == "bo"


def test_load_yaml_task_config_from_config_directory():
    pytest.importorskip("yaml")

    cfg = load_task_config(REPO_ROOT / "config" / "task_configs" / "yaml" / "irfel_bo.yaml")

    assert cfg.meta.name == "irfel_bo_weighted_fel"
    assert cfg.backend.type == "epics"
    assert cfg.optimizer.name == "bo"
