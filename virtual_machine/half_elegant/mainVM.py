
import os
import sys
from subprocess import Popen,run
import os
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

        self.start_ioc.clicked.connect(self.startioc)
        self.start_vm.clicked.connect(self.startvm)
        self.static_err.clicked.connect(self.staticerr)
        self.err_off.clicked.connect(self.erroff)

        self.QDXDYvalue.setText('0') # um
        
    
    # softIOC
    def startioc(self):
        self.textEdit.append('start softIOC')
        Popen("python3 main.py",cwd=st.rootpath+"/softIOC",shell=True) 
        
    # VM
    def startvm(self):
        self.textEdit.append('start vm')

        # 跨平台启动进程（确保进程组独立）
        kwargs = {}
        kwargs["start_new_session"] = True  # Unix: 新会话组
        proc = Popen(
            ["python3", "start_VM.py"],
            cwd=st.rootpath + "/virtual_machine/half_elegant",
            shell=False,  # 避免 shell 进程干扰
            **kwargs
        )
        self.subprocesses.append(proc)

    # add_err
    def staticerr(self): #generate QUAD xy random error
        self.textEdit.append('add static error {\t Q:'+self.QDXDYvalue.text()+' um}')
        Popen("python3 err_gene_VM.py gene_err "+self.QDXDYvalue.text(),cwd=st.rootpath+"/virtual_machine/half_elegant",shell=True)
    #
    def erroff(self): #turn off QUAD xy random error
        self.textEdit.append('err is off')
        self.QDXDYvalue.setText('0')
        Popen("python3 err_gene_VM.py err_off",cwd=st.rootpath+"/virtual_machine/half_elegant",shell=True)


    # 窗口关闭事件
    def closeEvent(self, event):
        self.stop_subpro()  # 调用停止函数
        event.accept()

    # 停止函数：关闭子进程
    def stop_subpro(self):
        for pro in self.subprocesses:
            pro.send_signal(signal.SIGTERM)
            # pro.kill()
            pro.wait()
    


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = myWindow()
    window.show()
    sys.exit(app.exec_())


