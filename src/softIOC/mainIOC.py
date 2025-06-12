import time
import os
from subprocess import Popen

from pv_server import pv_server
import half_linac.setup as st


if __name__=='__main__':
    # Half-linac
    jsonpath  = st.rootpath+"/src/virtual_machine/half_elegant/halflinac.json" 
    iocpath   = st.rootpath+"/src/softIOC/halflinac"
    
    myserver = pv_server(jsonpath, iocpath)
    #1. halflinac.json => ./softIOC/db/halflinac.substitutions 
    myserver.gen_substitution_file()
 
    #2. start softIOC
    Popen("./runMe",cwd=iocpath,shell=True)

    #3. initialize epics PV with the values from *.json
    myserver.init_lattice_pv()

    #4. Now, we can monitor the lattice pv, in case they have changes
    myserver.monitor_json()

    # 让程序保持运行 且为了避免cpu过分占用 以一定的时间间隔
    print('Now wait for changes')
    while True:
        time.sleep(1.e-3)
    
