from gui import Ui_Form
import sys
from PyQt5.QtWidgets import QMainWindow, QApplication, QLabel, QHBoxLayout, QWidget
from PyQt5.QtCore import QTimer
import epics
import sdds
import time
import numpy as np
from PyQt5.QtCore import QThread,pyqtSignal
import matplotlib as mpl
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit
import half_linac.setup as st
import json
import copy
from half_linac.virtual_machine.half_elegant.elegant_parser import elegant_parser
import os
import math
from collections import defaultdict

nest_dict = lambda: defaultdict(nest_dict)
jsonpath = st.rootpath+"/virtual_machine/half_elegant/halflinac.json"
lattice_file = st.rootpath+'/virtual_machine/half_elegant/elegant/lattice_ini.lte'
line_name    = 'ALL' #'use_beamline'
ele_file     = st.rootpath+'/virtual_machine/half_elegant/elegant/emit_ini.ele'  
filepath     = st.rootpath+"/virtual_machine/half_elegant/halflinac.json"
lfile = st.rootpath+'/virtual_machine/half_elegant/elegant/emit.lte'
#line_name    = 'ALL' #'use_beamline'
efile     = st.rootpath+'/virtual_machine/half_elegant/elegant/emit.ele'  
jpath     = st.rootpath+"/virtual_machine/half_elegant/emit.json"
    

tmp = sdds.SDDS(0)
tmp.load(st.rootpath+'/virtual_machine/half_elegant/elegant/emit.mat')
list_R = [tmp.columnData[i][0][0] for i in range(12, 48)]
print(list_R)
Rj = np.array(list_R).reshape(6,6)
print(Rj)
qf_map = np.transpose(Rj)
# get the final phase 



