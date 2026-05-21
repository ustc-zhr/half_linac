import half_linac.runtime_config as st
from pathlib import Path

from half_linac.src.virtual_machine.half_elegant.runtime_state import update_runtime_state

if __name__=='__main__':
    """change the usedline to ESA line in json file"""

    jsonpath = Path(st.rootpath) / "src/virtual_machine/half_elegant/halflinac.json"

    def use_esa_line(lte):
        lte["usedline"] = lte["lattice"]["ESA"]["LINE"].split(',')
        return True

    update_runtime_state(jsonpath, use_esa_line)
