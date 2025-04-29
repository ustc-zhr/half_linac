
import os
import sys
from subprocess import Popen
import os
import time

from PyQt5.QtWidgets import QMainWindow, QApplication
from PyQt5.QtCore import QThread
from VMgui import Ui_MainWindow


import half_linac.setup as st

class myWindow(QMainWindow, Ui_MainWindow):
    def __init__(self):
        super().__init__()
        self.setupUi(self)

        self.start_ioc.clicked.connect(self.startioc)
        self.start_vm.clicked.connect(self.startvm)
        self.static_err.clicked.connect(self.staticerr)
        self.err_off.clicked.connect(self.erroff)

        self.QDXDYvalue.setText('0') # um
        
        self.textEdit.append('zzz')
 
    # softIOC
    def startioc(self):
        Popen("python3 main.py",cwd=st.rootpath+"/softIOC",shell=True) 
    # VM
    def startvm(self):
        Popen("python3 start_VM.py",cwd=st.rootpath+"/virtual_machine/half_elegant",shell=True) 

    # add_err
    def staticerr(self): #generate QUAD xy random error
        Popen("python3 err_gene_VM.py gene_err "+self.QDXDYvalue.text(),cwd=st.rootpath+"/virtual_machine/half_elegant",shell=True)
    #
    def erroff(self): #turn off QUAD xy random error
        self.QDXDYvalue.setText('0')
        Popen("python3 err_gene_VM.py err_off",cwd=st.rootpath+"/virtual_machine/half_elegant",shell=True)




if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = myWindow()
    window.show()
    sys.exit(app.exec_())


