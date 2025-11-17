import json
import time
import sdds
import sys

import half_linac.setup as st

jsonpath     = st.rootpath+"/src/virtual_machine/half_elegant/halflinac.json"
def simply_VM(elem_start, elem_end):
        """to simplify the lattice in VM for accelerate the testing process (only considering from Q to flag )"""
        
        quad = elem_start
        flag = elem_end

        with open(jsonpath,"r") as f:
            lte  = json.load(f)
        contl    = lte  ["control"]
        lattice  = lte  ["lattice"]
        # usedline = lte  ["usedline"]
        usedline = lte["lattice"]["ALL"]["LINE"].split(',')

        # pre the input beam before entrance of quad
        # ------------------------------------------
        # add a watch
        prewatch = {}
        prewatch["NAME"] = "PREW"
        prewatch["TYPE"] = "WATCH"
        prewatch["FILENAME"] = "pre.bun"
        prewatch["MODE"] = "COORD"
        prewatch["DISABLE"] = "0"

        lattice["PREW"]={}
        lattice["PREW"]=prewatch
        
        id = usedline.index(quad)
        preline = usedline[0:id]
        preline.append(prewatch["NAME"])

        ltepre = {}
        ltepre["control"]  = contl
        ltepre["lattice"]  = lattice
        ltepre["usedline"] = preline
        with open(jsonpath,"w") as f:
            f.write(json.dumps(ltepre,indent=4))
        
        # wait the vm run
        time.sleep(st.runtime_vmmachine)
        print("pre.bun before ",quad," is ready")

        # get the energy before quad
        tmp = sdds.SDDS(0)
        tmp.load(st.rootpath+"/src/virtual_machine/half_elegant/elegant/one.cen")
        tmppCentral = tmp.columnData[11][0][-1]

        # simply VM
        # ---------
        contl    = lte  ["control"]
        lattice  = lte  ["lattice"]
        usedline = lte  ["usedline"]

        id1 = usedline.index(quad)
        id2 = usedline.index(flag)
        scanline = usedline[id1:id2+1]

        del contl["bunched_beam"]

        contl["run_setup"]["p_central"]= str(tmppCentral)

        contl["sdds_beam"] = {}
        contl["sdds_beam"]["input"]="pre.bun"
        contl["sdds_beam"]["center_arrival_time"]="1"
        contl["sdds_beam"]["reuse_bunch"]="1"

        lte["control"]  = contl
        lte["lattice"]  = lattice
        lte["usedline"] = scanline

        with open(jsonpath,"w") as f:
            f.write(json.dumps(lte,indent=4))
        
        time.sleep(5)
        print("simply VM: (",quad,"-to-",flag,") is ready")

        return

if __name__=='__main__':
    """change the usedline to ESA line in json file"""
    simply_VM(sys.argv[1], sys.argv[2])