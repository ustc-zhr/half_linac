# Author: Biaobin Li
# Date: 2024-01-25
# 2024-08-29 changed by Shancai Zhang: add watchdog

import os
import time
from elegant_parser import elegant_parser
import half_linac.setup as st
from watchdog.observers import Observer  
from watchdog.events import FileSystemEventHandler 
#import pv_server
lattice_file = './elegant/lattice_ini.lte'
line_name    = 'ALL'
ele_file     = './elegant/one_ini.ele'  
jsonpath  = st.rootpath+"/virtual_machine/half_elegant/halflinac.json"
lte = elegant_parser(lattice_file, ele_file, line_name)

class MyHandler(FileSystemEventHandler):  
    def on_modified(self, event):  
        if not event.is_directory:
            lte.json2lte_ele()
            # run elegant
            top_path = os.getcwd()
            print("Elegant is running ...\n")
            os.chdir("./elegant")
            os.system("./one")
            os.chdir(top_path)
            ## update bpm data
            lte.set_bpmPV()
             

if __name__=='__main__':

    lte.dump2json()
    event_handler = MyHandler()  
    observer = Observer()  
    observer.schedule(event_handler, jsonpath, recursive=False)  
    observer.start()  
    # initialization process: generate lte.json from lte.impz
    
    
    ## start the pv server to monitor changes on lattice
    ## -------------------------------------------------
    #myserver = pv_server.pv_server()

    ## initilize half.substitutions with lattice.json
    #myserver.gen_substitution_file()

    ## initialize epics PV with the values from lattice.lte
    #myserver.init_lattice_pv()
    #
    ## start monitoring the changes of lattice
    #myserver.update_lattice_json()

    period = 1  #1s 
    # top_path = os.getcwd()    
    # while True:       
    #     # load lte.json to generate *.ele and *.lte to the input files for Elegant
    #     lte.json2lte_ele()      
        
    #     # run elegant
    #     print("Elegant is running ...\n")
    #     os.chdir("./elegant")
    #     os.system("./one")
    #     os.chdir(top_path)
        
    #     ## update bpm data
    #     lte.set_bpmPV()
    try:
        while True:
            time.sleep(period)
    except KeyboardInterrupt:
        observer.stop()

    observer.join()    


