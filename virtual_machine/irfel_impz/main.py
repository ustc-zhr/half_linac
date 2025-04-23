# Author: Biaobin Li
# Date: 2024-01-25

import os
import time
from impactz_parser import impactz_parser
from simu_results_process import vm_results

if __name__=='__main__':
    
    file_name = './impz/lte.impz'
    line_name = 'flag4line'

    lte = impactz_parser(file_name,line_name)

    # initialization process: generate lte.json from lte.impz
    lte.dump2json()

    period = 1  #1s 
    top_path = os.getcwd()    
    while True:       
        #load lte.json to generate ImpactZ.in in every loop
        lte.json2impzin()      
        
        # run impact-z
        print("IMPACT-Z is running ...\n")
        os.chdir("./impz")
        os.system("./runMe")
        os.chdir(top_path)

        # broadcast simu results to epics channel
        vm = vm_results()
        vm.broadcast_flag()
        vm.broadcast_bpm()
        
        time.sleep(period)

