from pv_server import pv_server
import half_linac.setup as st
import time
from subprocess import Popen
from half_linac.virtual_machine.irfel_impz.impactz_parser import impactz_parser
import os

if __name__=='__main__':

    # Half-linac
    jsonpath  = st.rootpath+"/virtual_machine/half_elegant/halflinac.json" 
    iocpath   = st.rootpath+"/softIOC/halflinac"

    myserver = pv_server(jsonpath, iocpath)
    myserver.gen_random_Q_err()

    
