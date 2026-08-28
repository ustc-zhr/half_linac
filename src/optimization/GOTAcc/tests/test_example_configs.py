from pathlib import Path

import pytest

from gotacc.configs.loader import load_task_config
from gotacc.gui.services.pv_library import load_pv_library_file


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


@pytest.mark.parametrize(
    "path",
    sorted((REPO_ROOT / "config" / "pv_libraries").glob("*.json"))
    + sorted((REPO_ROOT / "config" / "pv_libraries").glob("*.yaml"))
    + sorted((REPO_ROOT / "config" / "pv_libraries").glob("*.yml")),
)
def test_load_bundled_pv_libraries(path):
    doc = load_pv_library_file(path)

    assert doc.machine
    assert doc.knobs or doc.objectives
    for items in (doc.knobs, doc.objectives):
        names = [item.name for item in items]
        pv_names = [item.pv_name for item in items]
        assert len(names) == len(set(names)), f"duplicate names in {path.name}"
        assert len(pv_names) == len(set(pv_names)), f"duplicate PVs in {path.name}"
