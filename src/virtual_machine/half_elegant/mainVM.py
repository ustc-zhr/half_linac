
import sys
from subprocess import Popen
import time
import signal

from PyQt5.QtWidgets import QMainWindow, QApplication
from PyQt5.QtCore import QThread
from VMgui import Ui_MainWindow

import half_linac.setup as st

class myWindow(QMainWindow, Ui_MainWindow):
    def __init__(self):
        super().__init__()
        self.setupUi(self)
        self.subprocesses = []
        
        # connect button
        self.start_ioc.clicked.connect(self.startioc)
        self.start_vm.clicked.connect(self.startvm)
        self.shutdown_VM.clicked.connect(self.stopvm)
        self.static_err.clicked.connect(self.staticerr)
        self.err_off.clicked.connect(self.erroff)
        self.pushButton_ESAline.clicked.connect(self.ESAline)
        self.pushButton_simply_VM.clicked.connect(self.simply_VM)
        self.pushButton_FULLline.clicked.connect(self.back_FULL)

        # default value
        self.QDXDYvalue.setText('0') # um
        self.QK1JITTER.setText('0') # um

        self.start_vm.setEnabled(False) 
        self.shutdown_VM.setEnabled(False)
        self.pushButton_ESAline.setEnabled(False)
    # ---------------
    # softIOC
    # ---------------    
    def startioc(self):

        self.textEdit.append('start softIOC')
        # 启动进程（确保进程组独立）
        kwargs = {}
        kwargs["start_new_session"] = True  # Unix: 新会话组
        proc = Popen(
            ["python3", "mainIOC.py"],
            cwd=st.rootpath + "/src/softIOC",
            shell=False,  # 避免 shell 进程干扰
            **kwargs
        )
        self.subprocesses.append(proc)
        
        time.sleep(2) # wait for ini softIOC before start VM
        self.start_vm.setEnabled(True) 

    # ---------------
    # VM
    # --------------- 
    def startvm(self):
        self.textEdit.append('start vm')

        # 跨平台启动进程（确保进程组独立）
        kwargs = {}
        kwargs["start_new_session"] = True  # Unix: 新会话组
        proc = Popen(
            ["python3", "start_VM.py"],
            cwd=st.rootpath + "/src/virtual_machine/half_elegant",
            shell=False,  # 避免 shell 进程干扰
            **kwargs
        )
        self.subprocesses.append(proc)

        self.pushButton_ESAline.setEnabled(True)
        self.shutdown_VM.setEnabled(True)

    # ---------------
    # ESA line
    # --------------- 
    def ESAline(self):
        self.textEdit.append('transfer to ESA line')

        # 跨平台启动进程（确保进程组独立）
        kwargs = {}
        kwargs["start_new_session"] = True  # Unix: 新会话组
        proc = Popen(
            ["python3", "transfer_ESAline.py"],
            cwd=st.rootpath + "/src/virtual_machine/half_elegant",
            shell=False,  # 避免 shell 进程干扰
            **kwargs
        )
        self.subprocesses.append(proc)

    # ---------------
    # simply VM
    # --------------- 
    def simply_VM(self):
        self.textEdit.append('simplify the VM lattice')
        ele_start = self.comboBox_simply_start.currentText()
        ele_end   = self.comboBox_simply_end.currentText()

        # 跨平台启动进程（确保进程组独立）
        kwargs = {}
        kwargs["start_new_session"] = True  # Unix: 新会话组
        proc = Popen(
            ["python3", "simply_VM.py", ele_start, ele_end],
            cwd=st.rootpath + "/src/virtual_machine/half_elegant",
            shell=False,  # 避免 shell 进程干扰
            **kwargs
        )
        self.subprocesses.append(proc)
    def back_FULL(self):
        self.textEdit.append('back to FULL line')

        # 跨平台启动进程（确保进程组独立）
        kwargs = {}
        kwargs["start_new_session"] = True  # Unix: 新会话组
        proc = Popen(
            ["python3", "full_VM.py"],
            cwd=st.rootpath + "/src/virtual_machine/half_elegant",
            shell=False,  # 避免 shell 进程干扰
            **kwargs
        )
        self.subprocesses.append(proc)

    # ---------------
    # err
    # --------------- 
    # add err
    def staticerr(self): #generate QUAD xy random error
        self.textEdit.append('add static error \t{ Q:'+self.QDXDYvalue.text()+' um} \n'+
                             'add jitter \t{ Q:'+self.QK1JITTER.text()+' ppm}')
        # Popen("python3 err_gene_VM.py gene_err "+self.QDXDYvalue.text(),cwd=st.rootpath+"/virtual_machine/half_elegant",shell=True)
        
        kwargs = {}
        kwargs["start_new_session"] = True  # Unix: 新会话组
        Popen(
            ["python3", "err_gene_VM.py", "gene_err", self.QDXDYvalue.text(), self.QK1JITTER.text()],
            cwd=st.rootpath + "/src/virtual_machine/half_elegant",
            shell=False,  # 避免 shell 进程干扰
            **kwargs
        )
    # err off
    def erroff(self): #turn off QUAD xy random error
        self.textEdit.append('err is off')
        self.QDXDYvalue.setText('0')
        self.QK1JITTER.setText('0')
        Popen("python3 err_gene_VM.py err_off",cwd=st.rootpath+"/src/virtual_machine/half_elegant",shell=True)
        

    # --------------- 
    # shutdown the vm
    # --------------- 
    def stopvm(self):
        self.textEdit.append('shutdown vm')
        self._stop_subpro()

    # windows close event
    def closeEvent(self, event):
        self._stop_subpro()
        event.accept()

    # stop function：close the subprocesses
    def _stop_subpro(self):
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


