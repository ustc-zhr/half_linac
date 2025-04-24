import time
import sys
from subprocess import Popen
import numpy as np

from gui import Ui_MainWindow 
from PyQt5.QtWidgets import QMainWindow, QApplication, QLabel, QHBoxLayout
from PyQt5.QtCore import QTimer

from epics import caget, caget_many

import half_linac.setup as st

class myWindow(QMainWindow, Ui_MainWindow):
    def __init__(self):
        super().__init__()
        self.setupUi(self)
       
        # init pv
        self.init_pv()

        self.cxmin = None
        self.cxmax = None
        self.cymin = None
        self.cymax = None

        self.BPMxstart = None
        self.BPMxend   = None
        self.BPMystart = None
        self.BPMyend   = None

        self.start_1.clicked.connect(self.start1_btn)
        self.stop_1.clicked.connect(self.stop1_btn)

        self.start_2.clicked.connect(self.start2_btn)
        self.stop_2.clicked.connect(self.stop2_btn)

        self.detail.clicked.connect(self.start_bpmvalue_btn)

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

    def start1_btn(self):
        self.timer_1 = QTimer(self)
        self.timer_1.timeout.connect(self.plotorbit_x)
        self.timer_1.start(1000) #every 1s
    def stop1_btn(self):
        self.timer_1.stop()

    def start2_btn(self):
        self.timer_2 = QTimer(self)
        self.timer_2.timeout.connect(self.plotorbit_y)
        self.timer_2.start(1000) #every 1s
    def stop2_btn(self):
        self.timer_2.stop()

    def plotorbit_x(self):
        self.init_pv()
        pvl_val = self.pvlx_val

        if self.hold_1.isChecked()==True:
            pass
        else:
            self.graphWidget_1.canvas.axes.clear()

        def setcxmin():
            try:
                self.cxmin = float(self.QL_cxmin.text())
            except:
                pass
        def setcxmax():
            try:
                self.cxmax = float(self.QL_cxmax.text())
            except:
                pass

        def setBPMxstart():
            try:
                self.BPMxstart = int(self.bPMSLineEdit.text())
            except:
                pass
        def setBPMxend():
            try:
                self.BPMxend = int(self.bPMELineEdit.text())
            except:
                pass

        self.QL_cxmin.returnPressed.connect(setcxmin)
        self.QL_cxmax.returnPressed.connect(setcxmax)
        if self.cxmin != None:
            self.graphWidget_1.canvas.axes.set_ylim(bottom=self.cxmin)
        if self.cxmax != None:
            self.graphWidget_1.canvas.axes.set_ylim(top=self.cxmax)

        self.bPMSLineEdit.returnPressed.connect(setBPMxstart)
        self.bPMELineEdit.returnPressed.connect(setBPMxend)
        if self.BPMxstart != None:
            self.graphWidget_1.canvas.axes.set_xlim(left=self.BPMxstart)
        if self.BPMxend != None:
            self.graphWidget_1.canvas.axes.set_xlim(right=self.BPMxend)

        x = np.linspace(1,len(pvl_val),len(pvl_val))
        self.graphWidget_1.canvas.axes.plot(x, pvl_val,'-o')
        self.graphWidget_1.canvas.axes.set_xlabel("BPM #")
        self.graphWidget_1.canvas.axes.set_ylabel("Cx (mm)")
        self.graphWidget_1.canvas.draw()

    def plotorbit_y(self):
        self.init_pv()
        pvl_val = self.pvly_val

        if self.hold_2.isChecked() == True:
            pass
        else:
            self.graphWidget_2.canvas.axes.clear()

        def setcymin():
            try:
                self.cymin = float(self.QL_cymin.text())
            except:
                pass
        def setcymax():
            try:
                self.cymax = float(self.QL_cymax.text())
            except:
                pass

        def setBPMystart():
            try:
                self.BPMystart = int(self.bPMSLineEdit_2.text())
            except:
                pass
        def setBPMyend():
            try:
                self.BPMyend = int(self.bPMYLineEdit.text())
            except:
                pass
                
        self.QL_cymin.returnPressed.connect(setcymin)
        self.QL_cymax.returnPressed.connect(setcymax)
        if self.cxmin != None:
            self.graphWidget_2.canvas.axes.set_ylim(bottom=self.cymin)
        if self.cxmax != None:
            self.graphWidget_2.canvas.axes.set_ylim(top=self.cymax)

        self.bPMSLineEdit_2.returnPressed.connect(setBPMystart)
        self.bPMYLineEdit.returnPressed.connect(setBPMyend)
        if self.BPMystart != None:
            self.graphWidget_1.canvas.axes.set_xlim(left=self.BPMystart)
        if self.BPMyend != None:
            self.graphWidget_1.canvas.axes.set_xlim(right=self.BPMyend)        

        x = np.linspace(1,len(pvl_val),len(pvl_val))
        self.graphWidget_2.canvas.axes.plot(x, pvl_val, '-o')
        self.graphWidget_2.canvas.axes.set_xlabel("BPM #")
        self.graphWidget_2.canvas.axes.set_ylabel("Cy (mm)")
        self.graphWidget_2.canvas.draw()


    def start_bpmvalue_btn(self):
        Popen("python3 submain.py",cwd=st.rootpath+"/apps/orbit_display",shell=True) 


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = myWindow()
    window.show()
    sys.exit(app.exec_())





