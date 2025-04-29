# Author: Biaobin Li
# Date: 2024-01-25
# 2024-08-29 changed by Shancai Zhang: run elegant when json file changed

import os
import time
from elegant_parser import elegant_parser
import half_linac.setup as st
#import pv_server

if __name__=='__main__':
    lattice_file = './elegant/lattice_ini.lte'
    line_name    = 'ALL'
    ele_file     = './elegant/one_ini.ele'  
    jsonpath     = st.rootpath+"/virtual_machine/half_elegant/halflinac.json"
    top_path     = os.getcwd()

    lte = elegant_parser(lattice_file, ele_file, line_name)
    
    ## -------------------------------------------------
    # initialization process: 
    lte.dump2json()
    last_modified = os.path.getmtime(jsonpath) #ini time stamp of json file
    lte.json2lte_ele()
    # first run elegant
    print("initial Elegant is running ...\n")
    os.chdir("./elegant")
    os.system("./one")
    os.chdir(top_path)
    # first update bpm/flag PV data 
    print("initial update bpm data ...\n")
    lte.broadcast_bpm()
    # lte.set_bpmPV()
    print("initial update flag data ...\n")
    lte.broadcast_flag()    
    print("VM(Elegant) is waiting changes")


    period = 1  #1s    
    while True:
        time.sleep(period)
        current_modified = os.path.getmtime(jsonpath) #time stamp of json file
        

        if current_modified != last_modified:
            print("json is changed")
            lte.json2lte_ele()
            last_modified = current_modified
            # run elegant
            print("update the Elegant ...\n")
            os.chdir("./elegant")
            os.system("./one")
            os.chdir(top_path)
            print("update bpm data ...\n")
            # update bpm/flag PV data
            lte.broadcast_bpm()
            print("update flag data ...\n")
            lte.broadcast_flag()
            print("VM(Elegant) is waiting changes")


