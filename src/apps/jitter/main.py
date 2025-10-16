
import sys
import numpy as np
import datetime
import time
from subprocess import Popen,run
from epics import caget, PV, caput_many, caget_many

from PyQt5.QtWidgets import QMainWindow, QApplication, QCheckBox, QDoubleSpinBox
from PyQt5.QtCore import QThread, Qt, QRegExp, QTimer
from Jittergui import Ui_MainWindow


import half_linac.setup as st

class myWindow(QMainWindow, Ui_MainWindow):
    def __init__(self):
        super().__init__()
        self.setupUi(self)
        self.subprocesses=[]
        self.plot_timer = QTimer(self)


        # connect button
        self.pushButton.clicked.connect(self.start)


        # # initial parameters
        self.points=int(self.lineEdit.text()) # 
        self.interval=float(self.lineEdit_2.text()) # 

    
    def _get_obj(self):
        obj_pvnames = [self.obj1_pvname.text(), self.obj2_pvname.text(), self.obj3_pvname.text()]

        valid_indices = [i for i, pv in enumerate(obj_pvnames) if pv != '']
        obj_pvnames = [obj_pvnames[i] for i in valid_indices]

        return obj_pvnames

   

    def start(self):
        # 
        obj_pvnames = self._get_obj()
        
        vall = []
        for j in range(self.points):
            print(j)
            vall.append(caget_many(obj_pvnames))
            time.sleep(self.interval)

        now = datetime.datetime.now()
        timestamp = now.strftime("%Y%m%d_%H%M%S") 
        filename = f"jitterfile_{timestamp}.txt"
        np.savetxt(filename,vall,fmt="%.6e")

        # jitter
        val = np.array(vall)
        jitter1 = []
        jitter2 = []
        for j in range(len(obj_pvnames)):
            data = val[:,j]

            tmp1 = np.mean(data)
            tmp2 = np.std(data)
            
            jitter1.append(round(tmp1,2))
            jitter2.append(round(tmp2,2))
        
        self.obj1_mean.setText(str(jitter1[0])) # 
        self.obj2_mean.setText(str(jitter1[1])) # 
        self.obj3_mean.setText(str(jitter1[2])) # 
        self.obj1_std.setText(str(jitter2[0])) # 
        self.obj2_std.setText(str(jitter2[1])) # 
        self.obj3_std.setText(str(jitter2[2])) # 
    


    
    # 窗口关闭事件
    def closeEvent(self, event):
        self.stop_cor()  # 调用停止函数
        event.accept()




if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = myWindow()
    window.show()
    sys.exit(app.exec_())


