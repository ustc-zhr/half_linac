from PyQt5.QtWidgets import QMainWindow, QApplication
from PyQt5.QtCore import QThread
import sys
import signal
from subprocess import Popen

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
        self.pushButton.clicked.connect(self.start_orbit_display)

        self.pushButton_2.clicked.connect(self.start_emitMeasure)
        self.pushButton_4.clicked.connect(self.start_bba)
        self.orbit_correct.clicked.connect(self.orb_correct)

    

    def start_vm(self):
        '''
        virtual machine
        '''

        kwargs = {}
        kwargs["start_new_session"] = True  # Unix: 新会话组
        proc = Popen(
            ["python3", "mainVM.py"],
            cwd=st.rootpath + "/src/virtual_machine/half_elegant",
            shell=False,  # 避免 shell 进程干扰
            **kwargs
        )
        self.subprocesses.append(proc)

    # -----------
    # 
    # -----------
    def start_beammonitor(self):
        '''
        beammonitor
        '''
        kwargs = {}
        kwargs["start_new_session"] = True  # Unix: 新会话组
        proc = Popen(
            ["python3", "main.py", "vm"],
            cwd=st.rootpath + "/src/apps/beam_monitor",
            shell=False,  # 避免 shell 进程干扰
            **kwargs
        )
        self.subprocesses.append(proc)

    
    def start_emitMeasure(self):
        '''
        emitMeasure
        '''

        kwargs = {}
        kwargs["start_new_session"] = True  # Unix: 新会话组
        proc = Popen(
            ["python3", "main.py"],
            cwd=st.rootpath + "/src/apps/emit_measure",
            shell=False,  # 避免 shell 进程干扰
            **kwargs
        )
        self.subprocesses.append(proc)        

    
    def start_orbit_display(self):
        '''
        orbit display
        '''
        
        kwargs = {}
        kwargs["start_new_session"] = True  # Unix: 新会话组
        proc = Popen(
            ["python3", "main.py"],
            cwd=st.rootpath + "/src/apps/orbit_display",
            shell=False,  # 避免 shell 进程干扰
            **kwargs
        )
        self.subprocesses.append(proc)  


    def start_bba(self):
        '''
        Beam-based Alignment
        '''
        kwargs = {}
        kwargs["start_new_session"] = True  # Unix: 新会话组
        proc = Popen(
            ["python3", "main.py"],
            cwd=st.rootpath + "/src/apps/bba",
            shell=False,  # 避免 shell 进程干扰
            **kwargs
        )
        self.subprocesses.append(proc)  


    def orb_correct(self):
        '''
        orbit correct
        '''   
        kwargs = {}
        kwargs["start_new_session"] = True  # Unix: 新会话组
        proc = Popen(
            ["python3", "mainOrbCor.py"],
            cwd=st.rootpath + "/src/apps/orbit_correct",
            shell=False,  # 避免 shell 进程干扰
            **kwargs
        )
        self.subprocesses.append(proc)  


    
    def closeEvent(self, event):
        '''
        windows close event
        '''
        self.stop_subpro()  # 调用停止函数
        event.accept()


    def stop_subpro(self):
        '''
        close the subprocesses
        '''
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


