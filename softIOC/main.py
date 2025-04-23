import time
import os
from subprocess import Popen

from pv_server import pv_server
import half_linac.setup as st
from half_linac.virtual_machine.irfel_impz.impactz_parser import impactz_parser


if __name__=='__main__':
    # Half-linac
    jsonpath  = st.rootpath+"/virtual_machine/half_elegant/halflinac.json" 
    iocpath   = st.rootpath+"/softIOC/halflinac"

    # lte.impz => irfel.json
    #base      = st.rootpath+"/virtual_machine/irfel_impz"
    #jsonpath  = base +"/irfel.json" 
    #file_name = base +"/impz/lte.impz"
    #line_name = 'flag4line'
    #iocpath   = st.rootpath+"/softIOC/irfel"

    #lte = impactz_parser(file_name,line_name)
    #lte.dump2json()
    # move it to irfel_impz
    #os.rename("./irfel.json",jsonpath)
    
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
    
