
import sys
import signal
from subprocess import Popen,run

from PyQt5.QtWidgets import QMainWindow, QApplication
from PyQt5.QtCore import QThread, Qt
from OrbCorgui import Ui_MainWindow


import half_linac.setup as st

class myWindow(QMainWindow, Ui_MainWindow):
    def __init__(self):
        super().__init__()
        self.setupUi(self)
        self.subprocesses=[]

        # connect button
        self.pushButton.clicked.connect(self.measure_res)
        self.pushButton_4.clicked.connect(self.start_cor)
        self.pushButton_2.clicked.connect(self.cor_off)
        self.pushButton_3.clicked.connect(self.stop_cor)
        # self.pushButton.clicked.connect(self.printzz)

        # initial parameters
        self.samplingIntervalSLineEdit.setText('6') # s
        self.correctorAccuracyUmLineEdit.setText('10') # um
        self.sampPerStepLineEdit.setText('2') # 
        
    def target_BPMs(self):
        checked_items = []
        for index in range(self.listWidget.count()):
            item = self.listWidget.item(index)
            if item.checkState() == Qt.Checked:
                checked_items.append(item.text())
        return checked_items
        
    def measure_res(self): #measure response matrix
        cmd = [
            "python3", "findresponse.py",                  #0
        ]
        kwargs = {}
        kwargs["start_new_session"] = True  # Unix: 新会话组

        proc = Popen(
            cmd,
            cwd=st.rootpath + "/apps/orbit_correct",
            shell=False,  # 避免 shell 进程干扰
            **kwargs
        )
        self.subprocesses.append(proc)        

    def start_cor(self):
        target_BPMlist = self.target_BPMs()
        cmd = [
            "python3", "correct.py",                  #0
            "start_cor",                              #1
            self.comboBox.currentText(),              #2
            self.samplingIntervalSLineEdit.text(),    #3
            self.correctorAccuracyUmLineEdit.text(),  #4
            self.sampPerStepLineEdit.text(),          #5
            ",".join(target_BPMlist)                  #6
        ]
        # 跨平台启动进程（确保进程组独立）
        kwargs = {}
        kwargs["start_new_session"] = True  # Unix: 新会话组

        proc = Popen(
            cmd,
            cwd=st.rootpath + "/apps/orbit_correct",
            shell=False,  # 避免 shell 进程干扰
            **kwargs
        )
        # 获取输出
        # stdout, stderr = proc.communicate()
        # print("输出:", stdout.decode())
        self.subprocesses.append(proc)
        # print(f"启动子进程 PID: {proc.pid}")    



    # cor_off
    # def cor_off(self):
    #     Popen("python3 correct.py cor_off",cwd=st.rootpath+"/apps/orbit_correct",shell=True)
    def cor_off(self):
        target_BPMlist = self.target_BPMs()
        cmd = [
            "python3", "correct.py",                  #0
            "cor_off",                                #1
            ",".join(target_BPMlist)                  #2
        ]
        # 跨平台启动进程（确保进程组独立）
        kwargs = {}
        kwargs["start_new_session"] = True  # Unix: 新会话组

        Popen(
            cmd,
            cwd=st.rootpath + "/apps/orbit_correct",
            shell=False,  # 避免 shell 进程干扰
            **kwargs
        )


    # stop_cor
    def stop_cor(self):
        for pro in self.subprocesses:
            try:
                pro.send_signal(signal.SIGTERM)
            except:
                pro.kill()
        self.subprocesses = []
    
    # 窗口关闭事件
    def closeEvent(self, event):
        self.stop_cor()  # 调用停止函数
        event.accept()




if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = myWindow()
    window.show()
    sys.exit(app.exec_())


