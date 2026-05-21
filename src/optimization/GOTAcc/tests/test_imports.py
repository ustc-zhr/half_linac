import importlib
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


MODULES = [
    "gotacc",
    "gotacc.runners.run_cli",
    "gotacc.algorithms.single_objective.bo",
    "gotacc.algorithms.single_objective.turbo",
    "gotacc.algorithms.single_objective.rcds",
    "gotacc.algorithms.multi_objective.mobo",
    "gotacc.algorithms.multi_objective.mggpo",
    "gotacc.algorithms.multi_objective.mopso",
    "gotacc.algorithms.multi_objective.nsga2",
]


def test_import_core_modules():
    for module_name in MODULES:
        importlib.import_module(module_name)


test_import_core_modules()
