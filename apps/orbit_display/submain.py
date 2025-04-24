import time
import sys
from subprocess import Popen
import numpy as np

from subgui import Ui_Form 
from PyQt5.QtWidgets import QMainWindow, QApplication, QLabel, QHBoxLayout
from PyQt5.QtCore import QTimer

from epics import caget, caget_many

import half_linac.setup as st

class myWindow(QMainWindow, Ui_Form):
    def __init__(self):
        super().__init__()
        self.setupUi(self)
        
        # init pv
        # self.init_pv()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.bpmvalue_dis)
        self.timer.start(1000) #every 1s
        
    def bpmvalue_dis(self):
        # init pv
        self.init_pv()

        self.pvlx_val = [round(num, 3) for num in self.pvlx_val]
        self.pvly_val = [round(num, 3) for num in self.pvly_val]
        # BPMx 1-10
        self.bPMx01LineEdit.setText(str(self.pvlx_val[0]))
        self.bPMx02LineEdit.setText(str(self.pvlx_val[1]))
        self.bPMx03LineEdit.setText(str(self.pvlx_val[2]))
        self.bPMx04LineEdit.setText(str(self.pvlx_val[3]))
        self.bPMx05LineEdit.setText(str(self.pvlx_val[4]))
        self.bPMx06LineEdit.setText(str(self.pvlx_val[5]))
        self.bPMx07LineEdit.setText(str(self.pvlx_val[6]))
        self.bPMx08LineEdit.setText(str(self.pvlx_val[7]))
        self.bPMx09LineEdit.setText(str(self.pvlx_val[8]))
        self.bPMx10LineEdit.setText(str(self.pvlx_val[9]))
        # BPMx 11-20
        self.bPMx11LineEdit.setText(str(self.pvlx_val[10]))
        self.bPMx12LineEdit.setText(str(self.pvlx_val[11]))
        self.bPMx13LineEdit.setText(str(self.pvlx_val[12]))
        self.bPMx14LineEdit.setText(str(self.pvlx_val[13]))
        self.bPMx15LineEdit.setText(str(self.pvlx_val[14]))
        self.bPMx16LineEdit.setText(str(self.pvlx_val[15]))
        self.bPMx17LineEdit.setText(str(self.pvlx_val[16]))
        self.bPMx18LineEdit.setText(str(self.pvlx_val[17]))
        self.bPMx19LineEdit.setText(str(self.pvlx_val[18]))
        self.bPMx20LineEdit.setText(str(self.pvlx_val[19]))
        # BPMx 21-30
        self.bPMx21LineEdit.setText(str(self.pvlx_val[20]))
        self.bPMx22LineEdit.setText(str(self.pvlx_val[21]))
        self.bPMx23LineEdit.setText(str(self.pvlx_val[22]))
        self.bPMx24LineEdit.setText(str(self.pvlx_val[23]))
        self.bPMx25LineEdit.setText(str(self.pvlx_val[24]))
        self.bPMx26LineEdit.setText(str(self.pvlx_val[25]))
        self.bPMx27LineEdit.setText(str(self.pvlx_val[26]))
        self.bPMx28LineEdit.setText(str(self.pvlx_val[27]))
        self.bPMx29LineEdit.setText(str(self.pvlx_val[28]))
        self.bPMx30LineEdit.setText(str(self.pvlx_val[29]))
        # BPMx 31-40
        self.bPMx31LineEdit.setText(str(self.pvlx_val[30]))
        self.bPMx32LineEdit.setText(str(self.pvlx_val[31]))
        self.bPMx33LineEdit.setText(str(self.pvlx_val[32]))
        self.bPMx34LineEdit.setText(str(self.pvlx_val[33]))
        self.bPMx35LineEdit.setText(str(self.pvlx_val[34]))
        self.bPMx36LineEdit.setText(str(self.pvlx_val[35]))
        self.bPMx37LineEdit.setText(str(self.pvlx_val[36]))
        self.bPMx38LineEdit.setText(str(self.pvlx_val[37]))
        self.bPMx39LineEdit.setText(str(self.pvlx_val[38]))
        self.bPMx40LineEdit.setText(str(self.pvlx_val[39]))

        # BPMx 41-43
        self.bPMx41LineEdit.setText(str(self.pvlx_val[40]))
        self.bPMx42LineEdit.setText(str(self.pvlx_val[41]))
        self.bPMx43LineEdit.setText(str(self.pvlx_val[42]))


        # BPMy 1-10
        self.bPMy01LineEdit.setText(str(self.pvly_val[0]))
        self.bPMy02LineEdit.setText(str(self.pvly_val[1]))
        self.bPMy03LineEdit.setText(str(self.pvly_val[2]))
        self.bPMy04LineEdit.setText(str(self.pvly_val[3]))
        self.bPMy05LineEdit.setText(str(self.pvly_val[4]))
        self.bPMy06LineEdit.setText(str(self.pvly_val[5]))
        self.bPMy07LineEdit.setText(str(self.pvly_val[6]))
        self.bPMy08LineEdit.setText(str(self.pvly_val[7]))
        self.bPMy09LineEdit.setText(str(self.pvly_val[8]))
        self.bPMy10LineEdit.setText(str(self.pvly_val[9]))
        # BPMy 11-20
        self.bPMy11LineEdit.setText(str(self.pvly_val[10]))
        self.bPMy12LineEdit.setText(str(self.pvly_val[11]))
        self.bPMy13LineEdit.setText(str(self.pvly_val[12]))
        self.bPMy14LineEdit.setText(str(self.pvly_val[13]))
        self.bPMy15LineEdit.setText(str(self.pvly_val[14]))
        self.bPMy16LineEdit.setText(str(self.pvly_val[15]))
        self.bPMy17LineEdit.setText(str(self.pvly_val[16]))
        self.bPMy18LineEdit.setText(str(self.pvly_val[17]))
        self.bPMy19LineEdit.setText(str(self.pvly_val[18]))
        self.bPMy20LineEdit.setText(str(self.pvly_val[19]))
        # BPMy 21-30
        self.bPMy21LineEdit.setText(str(self.pvly_val[20]))
        self.bPMy22LineEdit.setText(str(self.pvly_val[21]))
        self.bPMy23LineEdit.setText(str(self.pvly_val[22]))
        self.bPMy24LineEdit.setText(str(self.pvly_val[23]))
        self.bPMy25LineEdit.setText(str(self.pvly_val[24]))
        self.bPMy26LineEdit.setText(str(self.pvly_val[25]))
        self.bPMy27LineEdit.setText(str(self.pvly_val[26]))
        self.bPMy28LineEdit.setText(str(self.pvly_val[27]))
        self.bPMy29LineEdit.setText(str(self.pvly_val[28]))
        self.bPMy30LineEdit.setText(str(self.pvly_val[29]))
        # BPMy 31-40
        self.bPMy31LineEdit.setText(str(self.pvly_val[30]))
        self.bPMy32LineEdit.setText(str(self.pvly_val[31]))
        self.bPMy33LineEdit.setText(str(self.pvly_val[32]))
        self.bPMy34LineEdit.setText(str(self.pvly_val[33]))
        self.bPMy35LineEdit.setText(str(self.pvly_val[34]))
        self.bPMy36LineEdit.setText(str(self.pvly_val[35]))
        self.bPMy37LineEdit.setText(str(self.pvly_val[36]))
        self.bPMy38LineEdit.setText(str(self.pvly_val[37]))
        self.bPMy39LineEdit.setText(str(self.pvly_val[38]))
        self.bPMy40LineEdit.setText(str(self.pvly_val[39]))

        # BPMy 41-43
        self.bPMy41LineEdit.setText(str(self.pvly_val[40]))
        self.bPMy42LineEdit.setText(str(self.pvly_val[41]))
        self.bPMy43LineEdit.setText(str(self.pvly_val[42]))


    def init_pv(self):
        pvlx = []
        pvly = []
        for j in range(43):
            if j+1 < 10:
                pvx = "HALF:IN:BPM:BPM0"+str(j+1)+":X:ao"
                pvy = "HALF:IN:BPM:BPM0"+str(j+1)+":Y:ao"
            else:
                pvx = "HALF:IN:BPM:BPM"+str(j+1)+":X:ao"
                pvy = "HALF:IN:BPM:BPM"+str(j+1)+":Y:ao"
            #print(pvx,pvlx)
            pvlx.append(pvx)
            pvly.append(pvy)

        # get the values
        self.pvlx_val = caget_many(pvlx) 
        self.pvly_val = caget_many(pvly) 



if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = myWindow()
    window.show()
    sys.exit(app.exec_())





