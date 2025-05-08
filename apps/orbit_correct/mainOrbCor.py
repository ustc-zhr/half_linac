
import os
import sys
from subprocess import Popen,run
import os
import time
import signal

from PyQt5.QtWidgets import QMainWindow, QApplication
from PyQt5.QtCore import QThread
from OrbCorgui import Ui_MainWindow


import half_linac.setup as st

class myWindow(QMainWindow, Ui_MainWindow):
    def __init__(self):
        super().__init__()
        self.setupUi(self)
        self.subprocesses=[]

        # connect button
        self.pushButton_4.clicked.connect(self.start_cor)
        self.pushButton_2.clicked.connect(self.cor_off)
        self.pushButton_3.clicked.connect(self.stop_cor)

        # initial parameters
        self.samplingIntervalSLineEdit.setText('6') # s
        self.correctorAccuracyUmLineEdit.setText('10') # um
        self.sampPerStepLineEdit.setText('2') # 
        
    
    # start cor
    # def start_cor(self):
    #     proc1=Popen("python3 correct.py start_cor "+self.comboBox.currentText()+' '+self.samplingIntervalSLineEdit.text()+' '+self.correctorAccuracyUmLineEdit.text()+' '+self.sampPerStepLineEdit.text(),cwd=st.rootpath+"/apps/orbit_correct",shell=True) 
    #     self.subprocesses.append(proc1)
    def start_cor(self):
        cmd = [
            "python3", "correct.py", "start_cor",
            self.comboBox.currentText(),
            self.samplingIntervalSLineEdit.text(),
            self.correctorAccuracyUmLineEdit.text(),
            self.sampPerStepLineEdit.text()
        ]
        # 跨平台启动进程（确保进程组独立）
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True  # Unix: 新会话组

        proc = Popen(
            cmd,
            cwd=st.rootpath + "/apps/orbit_correct",
            shell=False,  # 避免 shell 进程干扰
            **kwargs
        )
        self.subprocesses.append(proc)
        # print(f"启动子进程 PID: {proc.pid}")    



    # cor_off
    # def cor_off(self):
    #     Popen("python3 correct.py cor_off",cwd=st.rootpath+"/apps/orbit_correct",shell=True)
    def cor_off(self):
        Popen(
            ["python3", "correct.py", "cor_off"],
            cwd=st.rootpath + "/apps/orbit_correct",
            shell=False
        )


    # stop_cor
    def stop_cor(self):
        for pro in self.subprocesses:
            pro.send_signal(signal.SIGTERM)
            pro.wait()
    
    # 窗口关闭事件
    def closeEvent(self, event):
        self.stop_cor()  # 调用停止函数
        event.accept()




if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = myWindow()
    window.show()
    sys.exit(app.exec_())


