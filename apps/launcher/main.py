from PyQt5.QtWidgets import QMainWindow, QApplication
from PyQt5.QtCore import QThread
import os
import sys
import signal
from subprocess import Popen

from half_linac.apps.orbit_display import main as orbit
import half_linac.setup as st
from gui import Ui_MainWindow

class myWindow(QMainWindow, Ui_MainWindow):
    def __init__(self):
        super().__init__()
        self.setupUi(self)
        self.subprocesses=[]
        
        # connect button
        self.vmbtn.clicked.connect(self.start_vm)
        self.pushButton_3.clicked.connect(self.start_beammonitor)
        self.pushButton_2.clicked.connect(self.start_emitMeasure)
        self.pushButton.clicked.connect(self.start_orbit_display)
        self.pushButton_4.clicked.connect(self.start_bba)
        
        self.orbit_correct.clicked.connect(self.orb_correct)

    
    # ---------------
    # virtual machine
    # ---------------
    def start_vm(self):
        # Popen("python3 mainVM.py",cwd=st.rootpath+"/virtual_machine/half_elegant",shell=True) 
        # 跨平台启动进程（确保进程组独立）
        kwargs = {}
        kwargs["start_new_session"] = True  # Unix: 新会话组
        proc = Popen(
            ["python3", "mainVM.py"],
            cwd=st.rootpath + "/virtual_machine/half_elegant",
            shell=False,  # 避免 shell 进程干扰
            **kwargs
        )
        self.subprocesses.append(proc)

    # -----------
    # beammonitor
    # -----------
    def start_beammonitor(self):
        # Popen("python3 main.py vm",cwd=st.rootpath+"/apps/beam_monitor",shell=True) #"vm" for virtual machine  "real" for real machine 
        kwargs = {}
        kwargs["start_new_session"] = True  # Unix: 新会话组
        proc = Popen(
            ["python3", "main.py", "vm"],
            cwd=st.rootpath + "/apps/beam_monitor",
            shell=False,  # 避免 shell 进程干扰
            **kwargs
        )
        self.subprocesses.append(proc)

    # -----------
    # emitMeasure
    # -----------
    def start_emitMeasure(self):
        # Popen("python3 main.py",cwd=st.rootpath+"/apps/emit_measure",shell=True) 
        kwargs = {}
        kwargs["start_new_session"] = True  # Unix: 新会话组
        proc = Popen(
            ["python3", "main.py"],
            cwd=st.rootpath + "/apps/emit_measure",
            shell=False,  # 避免 shell 进程干扰
            **kwargs
        )
        self.subprocesses.append(proc)        

    # -------------
    # orbit display
    # -------------     
    def start_orbit_display(self):
        # Popen("python3 main.py",cwd=st.rootpath+"/apps/orbit_display",shell=True)
        kwargs = {}
        kwargs["start_new_session"] = True  # Unix: 新会话组
        proc = Popen(
            ["python3", "main.py"],
            cwd=st.rootpath + "/apps/orbit_display",
            shell=False,  # 避免 shell 进程干扰
            **kwargs
        )
        self.subprocesses.append(proc)  

    # --------------------
    # Beam-based Alignment
    # --------------------    
    def start_bba(self):
        # Popen("python3 main.py",cwd=st.rootpath+"/apps/bba",shell=True) 
        kwargs = {}
        kwargs["start_new_session"] = True  # Unix: 新会话组
        proc = Popen(
            ["python3", "main.py"],
            cwd=st.rootpath + "/apps/bba",
            shell=False,  # 避免 shell 进程干扰
            **kwargs
        )
        self.subprocesses.append(proc)  


    # -------------
    # orbit correct
    # -------------
    
    def orb_correct(self):
        # Popen("python3 mainOrbCor.py",cwd=st.rootpath+"/apps/orbit_correct",shell=True)    
        kwargs = {}
        kwargs["start_new_session"] = True  # Unix: 新会话组
        proc = Popen(
            ["python3", "mainOrbCor.py"],
            cwd=st.rootpath + "/apps/orbit_correct",
            shell=False,  # 避免 shell 进程干扰
            **kwargs
        )
        self.subprocesses.append(proc)  



    # windows close event
    def closeEvent(self, event):
        self.stop_subpro()  # 调用停止函数
        event.accept()

    # stop function：close the subprocesses
    def stop_subpro(self):
        for pro in self.subprocesses:
            try:
                pro.send_signal(signal.SIGTERM)
            except:
                pro.kill()
        self.subprocesses = []

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = myWindow()
    window.show()
    sys.exit(app.exec_())


