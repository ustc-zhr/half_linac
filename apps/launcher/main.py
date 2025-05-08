from PyQt5.QtWidgets import QMainWindow, QApplication
from PyQt5.QtCore import QThread
from gui import Ui_MainWindow
import os
import sys
from subprocess import Popen
from half_linac.apps.orbit_display import main as orbit
import half_linac.setup as st

class myWindow(QMainWindow, Ui_MainWindow):
    def __init__(self):
        super().__init__()
        self.setupUi(self)
        

        self.vmbtn.clicked.connect(self.start_vm)

        self.pushButton_3.clicked.connect(self.start_beammonitor)
        self.pushButton_2.clicked.connect(self.start_emitMeasure)

        self.pushButton.clicked.connect(self.start_orbit_display)

        self.pushButton_4.clicked.connect(self.start_bba)

        self.measure_response.clicked.connect(self.measure_res)
        self.orbit_correct.clicked.connect(self.orb_correct)
        self.cor_off.clicked.connect(self.coroff)
        self.cor_stop.clicked.connect(self.corstop)
    
    # ---------------
    # virtual machine
    # ---------------
    def start_vm(self):
        Popen("python3 mainVM.py",cwd=st.rootpath+"/virtual_machine/half_elegant",shell=True) 

    # -----------
    # beammonitor
    # -----------
    def start_beammonitor(self):
        Popen("python3 main.py vm",cwd=st.rootpath+"/apps/beam_monitor",shell=True) #"vm" for virtual machine  "real" for real machine 

    # -----------
    # emitMeasure
    # -----------
    def start_emitMeasure(self):
        Popen("python3 main.py",cwd=st.rootpath+"/apps/emit_measure",shell=True) 

    # -------------
    # orbit display
    # -------------     
    def start_orbit_display(self):
        Popen("python3 main.py",cwd=st.rootpath+"/apps/orbit_display",shell=True)

    # --------------------
    # Beam-based Alignment
    # --------------------    
    def start_bba(self):
        Popen("python3 main.py",cwd=st.rootpath+"/apps/bba",shell=True) 

    # -------------
    # orbit correct
    # -------------
    def measure_res(self): #measure response matrix
        Popen("python3 findresponse.py",cwd=st.rootpath+"/apps/orbit_correct",shell=True)
    # def correct_x(self):
    #     Popen("python3 correct.py",cwd=st.rootpath+"/apps/orbit_correct",shell=True)
    def coroff(self):
        Popen("python3 cor_off.py",cwd=st.rootpath+"/apps/orbit_correct",shell=True)
    def corstop(self):
        with open(st.rootpath+"/apps/orbit_correct/clicked.txt", 'w') as f:  
            f.write('clicked')

    def orb_correct(self):
        Popen("python3 mainOrbCor.py",cwd=st.rootpath+"/apps/orbit_correct",shell=True)    

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = myWindow()
    window.show()
    sys.exit(app.exec_())


