import json
import time
import sdds
import sys

import half_linac.setup as st

jsonpath     = st.rootpath+"/src/virtual_machine/half_elegant/halflinac.json"
def full_VM():
    # back to the state before simply
    with open(jsonpath,"r") as f:
        lte  = json.load(f)
    contl    = lte  ["control"]
    lattice  = lte  ["lattice"]
    line = lte["lattice"]["ALL"]["LINE"].split(',')

    if "PREW" in lattice:
        del lattice["PREW"]
    
    if "sdds_beam" in contl:
        del contl["sdds_beam"]

        bunched_beam = {
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
            "sigma_dp": "4e-3"
        }
        contl["bunched_beam"] = bunched_beam
    
    contl["run_setup"]["p_central"]= "223.4028"

    lte["control"]  = contl
    lte["lattice"]  = lattice
    lte["usedline"] = line
    with open(jsonpath,"w") as f:   
        f.write(json.dumps(lte,indent=4))

    print("full VM is back")
    return

if __name__=='__main__':
    """change the usedline to ESA line in json file"""
    full_VM()