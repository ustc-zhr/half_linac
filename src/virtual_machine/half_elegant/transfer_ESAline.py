import sys
from pathlib import Path

_REPO_BOOTSTRAP_ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / "repo_bootstrap.py").is_file()
)
if str(_REPO_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_BOOTSTRAP_ROOT))

from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

from half_linac.src.shared.machine_profile import resolve_machine_runtime
from half_linac.src.virtual_machine.half_elegant.runtime_state import update_runtime_state

if __name__=='__main__':
    """change the usedline to ESA line in json file"""

    jsonpath = resolve_machine_runtime().vm.runtime_json

    def use_esa_line(lte):
        lte["usedline"] = lte["lattice"]["ESA"]["LINE"].split(',')
        return True

    update_runtime_state(jsonpath, use_esa_line)
