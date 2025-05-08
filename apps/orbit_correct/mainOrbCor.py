
import os
import sys
from subprocess import Popen,run
import os
import time

from PyQt5.QtWidgets import QMainWindow, QApplication
from PyQt5.QtCore import QThread
from OrbCorgui import Ui_MainWindow


import half_linac.setup as st

class myWindow(QMainWindow, Ui_MainWindow):
    def __init__(self):
        super().__init__()
        self.setupUi(self)
        self.subprocesses=[]

        # 按钮绑定
        self.pushButton_4.clicked.connect(self.start_cor)
        self.pushButton_2.clicked.connect(self.cor_off)
        self.pushButton_3.clicked.connect(self.stop_cor)

        # 初始化参数
        self.samplingIntervalSLineEdit.setText('6') # s
        self.correctorAccuracyUmLineEdit.setText('10') # um
        self.sampPerStepLineEdit.setText('2') # 
        
    
    # start cor
    def start_cor(self):
        proc1=Popen("python3 correct.py start_cor "+self.comboBox.currentText()+' '+self.samplingIntervalSLineEdit.text()+' '+self.correctorAccuracyUmLineEdit.text()+' '+self.sampPerStepLineEdit.text(),cwd=st.rootpath+"/apps/orbit_correct",shell=True) 
        self.subprocesses.append(proc1)




    # cor_off
    def cor_off(self):
        Popen("python3 correct.py cor_off",cwd=st.rootpath+"/apps/orbit_correct",shell=True)




    # stop_cor
    def stop_cor(self):
        print('proc:',self.subprocesses)
        for pro in self.subprocesses:
            print(pro)
            pro.kill()
            pro.wait()





if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = myWindow()
    window.show()
    sys.exit(app.exec_())


