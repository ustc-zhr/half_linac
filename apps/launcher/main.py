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
        
        self.iocbtn.clicked.connect(self.start_ioc)
        self.vmbtn.clicked.connect(self.start_vm)
        self.pushButton.clicked.connect(self.start_orbit_display)
        self.pushButton_2.clicked.connect(self.start_emitMeasure)
        self.pushButton_3.clicked.connect(self.start_beammonitor)
        self.pushButton_4.clicked.connect(self.start_bba)
        self.generate_posi_err.clicked.connect(self.geni_err)
        self.err_off.clicked.connect(self.erroff)
        self.measure_response.clicked.connect(self.measure_res)
        self.orbit_correct.clicked.connect(self.correct_x)
        self.cor_off.clicked.connect(self.coroff)
        self.cor_stop.clicked.connect(self.corstop)
    
    # softIOC+VM
    def start_ioc(self):
        #Popen("./runMe",cwd=st.rootpath+"/softIOC/halflinac",shell=True) 
        Popen("python3 main.py",cwd=st.rootpath+"/softIOC",shell=True) 
    def start_vm(self):
        Popen("python3 main.py",cwd=st.rootpath+"/virtual_machine/half_elegant",shell=True) 

    # beammonitor+emitMeasure
    def start_beammonitor(self):
        Popen("python3 main.py vm",cwd=st.rootpath+"/apps/beam_monitor",shell=True) #"vm" for virtual machine  "real" for real machine 
    def start_emitMeasure(self):
        Popen("python3 main.py",cwd=st.rootpath+"/apps/emit_measure",shell=True) 


    def start_orbit_display(self):
        # Popen("./runMe",cwd=st.rootpath+"/apps/orbit_display",shell=True)
        Popen("python3 main.py",cwd=st.rootpath+"/apps/orbit_display",shell=True)

    def start_bba(self):
        Popen("python3 main.py",cwd=st.rootpath+"/apps/bba",shell=True) 

    def geni_err(self): #generate QUAD xy random error
        Popen("python3 geni_err.py",cwd=st.rootpath+"/softIOC",shell=True)

    def erroff(self): #turn off QUAD xy random error
        Popen("python3 err_off.py",cwd=st.rootpath+"/softIOC",shell=True)

    def measure_res(self): #measure response matrix
        Popen("python3 findresponse.py",cwd=st.rootpath+"/apps/orbit_correct",shell=True)

    def correct_x(self):
        Popen("python3 correct.py",cwd=st.rootpath+"/apps/orbit_correct",shell=True)

    def coroff(self):
        Popen("python3 cor_off.py",cwd=st.rootpath+"/apps/orbit_correct",shell=True)
 
    def corstop(self):
        with open(st.rootpath+"/apps/orbit_correct/clicked.txt", 'w') as f:  
            f.write('clicked')            

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = myWindow()
    window.show()
    sys.exit(app.exec_())


