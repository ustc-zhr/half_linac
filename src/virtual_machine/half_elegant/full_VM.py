from pathlib import Path

import half_linac.runtime_config as st
from half_linac.src.virtual_machine.half_elegant.runtime_state import update_runtime_state

jsonpath = Path(st.rootpath) / "src/virtual_machine/half_elegant/halflinac.json"
def full_VM():
    # back to the state before simply
    def restore_full_line(lte):
        contl = lte["control"]
        lattice = lte["lattice"]
        line = lattice["ALL"]["LINE"].split(',')

        lattice.pop("PREW", None)

        if "sdds_beam" in contl:
            del contl["sdds_beam"]

            contl["bunched_beam"] = {
                "n_particles_per_bunch": "10000",
                "emit_nx": "10e-6",
                "emit_ny": "10e-6",
                "use_twiss_command_values": "10000",
                "distribution_type[0]": "\"gaussian\"",
                "distribution_type[1]": "\"gaussian\"",
                "distribution_type[2]": "\"gaussian\"",
                "distribution_cutoff[0]": "5",
                "distribution_cutoff[1]": "5",
                "distribution_cutoff[2]": "5",
                "sigma_s": "1.11e-3",
                "sigma_dp": "4e-3",
            }

        contl["run_setup"]["p_central"] = "223.4028"
        lte["usedline"] = line
        return True

    update_runtime_state(jsonpath, restore_full_line)

    print("full VM is back")
    return

if __name__=='__main__':
    """change the usedline to ESA line in json file"""
    full_VM()
