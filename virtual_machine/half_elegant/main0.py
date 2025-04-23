# Author: Biaobin Li
# Date: 2024-01-25

import os
import time
from elegant_parser import elegant_parser
#import pv_server

if __name__=='__main__':
    lattice_file = './elegant/lattice_ini.lte'
    line_name    = 'ALL'
    ele_file     = './elegant/one_ini.ele'  

    lte = elegant_parser(lattice_file, ele_file, line_name)

    # initialization process: generate lte.json from lte.impz
    lte.dump2json()
    
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

    period = 3  #1s 
    top_path = os.getcwd()    
    while True:       
        # load lte.json to generate *.ele and *.lte to the input files for Elegant
        lte.json2lte_ele()      
        
        # run elegant
        print("Elegant is running ...\n")
        os.chdir("./elegant")
        os.system("./one")
        os.chdir(top_path)
        
        ## update bpm data
        lte.set_bpmPV()
        
        time.sleep(period)

