import time
import sdds
import sys
from pathlib import Path

_REPO_BOOTSTRAP_ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / "repo_bootstrap.py").is_file()
)
if str(_REPO_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_BOOTSTRAP_ROOT))

from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

import half_linac.runtime_config as st
from half_linac.src.shared.machine_profile import resolve_machine_runtime
from half_linac.src.virtual_machine.half_elegant.runtime_state import (
    read_runtime_state,
    update_runtime_state,
)

runtime = resolve_machine_runtime()
jsonpath = runtime.vm.runtime_json
elegant_dir = runtime.vm.bootstrap_lattice.parent
def simply_VM(elem_start, elem_end):
        """to simplify the lattice in VM for accelerate the testing process (only considering from Q to flag )"""
        
        quad = elem_start
        flag = elem_end

        lte = read_runtime_state(jsonpath)
        usedline = lte["lattice"]["ALL"]["LINE"].split(',')
        current_usedline = lte["usedline"]

        # pre the input beam before entrance of quad
        # ------------------------------------------
        def prepare_pre_bunch(runtime_state):
            prewatch = {
                "NAME": "PREW",
                "TYPE": "WATCH",
                "FILENAME": "pre.bun",
                "MODE": "COORD",
                "DISABLE": "0",
            }

            lattice = runtime_state["lattice"]
            lattice["PREW"] = prewatch

            preline = usedline[0:usedline.index(quad)]
            preline.append(prewatch["NAME"])
            runtime_state["usedline"] = preline
            return True

        update_runtime_state(jsonpath, prepare_pre_bunch)
        
        # wait the vm run
        time.sleep(st.runtime_vmmachine)
        print("pre.bun before ",quad," is ready")

        # get the energy before quad
        tmp = sdds.SDDS(0)
        tmp.load(str(elegant_dir / "one.cen"))
        tmppCentral = tmp.columnData[11][0][-1]

        # simply VM
        # ---------
        id1 = current_usedline.index(quad)
        id2 = current_usedline.index(flag)
        scanline = current_usedline[id1:id2+1]

        def apply_simple_mode(runtime_state):
            contl = runtime_state["control"]
            contl.pop("bunched_beam", None)

            contl["run_setup"]["p_central"] = str(tmppCentral)
            contl["sdds_beam"] = {
                "input": "pre.bun",
                "center_arrival_time": "1",
                "reuse_bunch": "1",
            }

            runtime_state["usedline"] = scanline
            return True

        update_runtime_state(jsonpath, apply_simple_mode)
        
        time.sleep(5)
        print("simply VM: (",quad,"-to-",flag,") is ready")

        return

if __name__=='__main__':
    """change the usedline to ESA line in json file"""
    simply_VM(sys.argv[1], sys.argv[2])
