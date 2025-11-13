import half_linac.setup as st
import json

if __name__=='__main__':
    """change the usedline to ESA line in json file"""

    jsonpath = st.rootpath+"/src/virtual_machine/half_elegant/halflinac.json"

    with open(jsonpath,"r") as f:
        lte  = json.load(f)
    EASline = lte["lattice"]["ESA"]["LINE"].split(',')
    lte["usedline"] = EASline

    with open(jsonpath,"w") as f:
        f.write(json.dumps(lte,indent=4))