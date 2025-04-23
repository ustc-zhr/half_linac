# -*- coding: utf-8 -*-

# Form implementation generated from reading ui file 'gui.ui'
#
# Created by: PyQt5 UI code generator 5.12.3
#
# WARNING! All changes made in this file will be lost!


from PyQt5 import QtCore, QtGui, QtWidgets


class Ui_MainWindow(object):
    def setupUi(self, MainWindow):
        MainWindow.setObjectName("MainWindow")
        MainWindow.resize(800, 600)
        self.centralwidget = QtWidgets.QWidget(MainWindow)
        self.centralwidget.setObjectName("centralwidget")
        self.pushButton = QtWidgets.QPushButton(self.centralwidget)
        self.pushButton.setGeometry(QtCore.QRect(10, 70, 101, 31))
        self.pushButton.setObjectName("pushButton")
        self.pushButton_2 = QtWidgets.QPushButton(self.centralwidget)
        self.pushButton_2.setGeometry(QtCore.QRect(10, 170, 101, 31))
        self.pushButton_2.setObjectName("pushButton_2")
        self.iocbtn = QtWidgets.QPushButton(self.centralwidget)
        self.iocbtn.setGeometry(QtCore.QRect(120, 10, 91, 31))
        self.iocbtn.setObjectName("iocbtn")
        self.vmbtn = QtWidgets.QPushButton(self.centralwidget)
        self.vmbtn.setGeometry(QtCore.QRect(10, 10, 101, 31))
        self.vmbtn.setObjectName("vmbtn")
        self.pushButton_3 = QtWidgets.QPushButton(self.centralwidget)
        self.pushButton_3.setGeometry(QtCore.QRect(10, 120, 101, 31))
        self.pushButton_3.setObjectName("pushButton_3")
        self.textEdit = QtWidgets.QTextEdit(self.centralwidget)
        self.textEdit.setGeometry(QtCore.QRect(410, 10, 381, 101))
        self.textEdit.setObjectName("textEdit")
        self.pushButton_4 = QtWidgets.QPushButton(self.centralwidget)
        self.pushButton_4.setGeometry(QtCore.QRect(10, 220, 101, 31))
        self.pushButton_4.setObjectName("pushButton_4")
        self.generate_posi_err = QtWidgets.QPushButton(self.centralwidget)
        self.generate_posi_err.setGeometry(QtCore.QRect(120, 70, 199, 28))
        self.generate_posi_err.setObjectName("generate_posi_err")
        self.measure_response = QtWidgets.QPushButton(self.centralwidget)
        self.measure_response.setGeometry(QtCore.QRect(120, 120, 199, 28))
        self.measure_response.setObjectName("measure_response")
        self.err_off = QtWidgets.QPushButton(self.centralwidget)
        self.err_off.setGeometry(QtCore.QRect(320, 70, 51, 28))
        self.err_off.setObjectName("err_off")
        self.orbit_correct = QtWidgets.QPushButton(self.centralwidget)
        self.orbit_correct.setGeometry(QtCore.QRect(120, 170, 199, 28))
        self.orbit_correct.setObjectName("orbit_correct")
        self.cor_off = QtWidgets.QPushButton(self.centralwidget)
        self.cor_off.setGeometry(QtCore.QRect(320, 170, 51, 28))
        self.cor_off.setObjectName("cor_off")
        self.cor_stop = QtWidgets.QPushButton(self.centralwidget)
        self.cor_stop.setGeometry(QtCore.QRect(380, 170, 51, 28))
        self.cor_stop.setObjectName("cor_stop")
        MainWindow.setCentralWidget(self.centralwidget)
        self.menubar = QtWidgets.QMenuBar(MainWindow)
        self.menubar.setGeometry(QtCore.QRect(0, 0, 800, 20))
        self.menubar.setObjectName("menubar")
        MainWindow.setMenuBar(self.menubar)
        self.statusbar = QtWidgets.QStatusBar(MainWindow)
        self.statusbar.setObjectName("statusbar")
        MainWindow.setStatusBar(self.statusbar)

        self.retranslateUi(MainWindow)
        QtCore.QMetaObject.connectSlotsByName(MainWindow)

    def retranslateUi(self, MainWindow):
        _translate = QtCore.QCoreApplication.translate
        MainWindow.setWindowTitle(_translate("MainWindow", "Launcher"))
        self.pushButton.setText(_translate("MainWindow", "orbit display"))
        self.pushButton_2.setText(_translate("MainWindow", "emit measure"))
        self.iocbtn.setText(_translate("MainWindow", "start IOC"))
        self.vmbtn.setText(_translate("MainWindow", "start VM"))
        self.pushButton_3.setText(_translate("MainWindow", "beam monitor"))
        self.textEdit.setHtml(_translate("MainWindow", "<!DOCTYPE HTML PUBLIC \"-//W3C//DTD HTML 4.0//EN\" \"http://www.w3.org/TR/REC-html40/strict.dtd\">\n"
"<html><head><meta name=\"qrichtext\" content=\"1\" /><style type=\"text/css\">\n"
"p, li { white-space: pre-wrap; }\n"
"</style></head><body style=\" font-family:\'Sans Serif\'; font-size:9pt; font-weight:400; font-style:normal;\">\n"
"<p style=\" margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px;\"><span style=\" color:#ff0000;\">Pay Attention:</span></p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px;\"><br /></p>\n"
"<p style=\" margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px;\"><span style=\" color:#ff0000;\">(1) start VM &amp; start IOC is for virtual machine development, DO NOT click them in HALF-linac control room. </span></p></body></html>"))
        self.pushButton_4.setText(_translate("MainWindow", "BBA"))
        self.generate_posi_err.setText(_translate("MainWindow", "generate random position error"))
        self.measure_response.setText(_translate("MainWindow", "measure response"))
        self.err_off.setText(_translate("MainWindow", "err off"))
        self.orbit_correct.setText(_translate("MainWindow", "orbit correct"))
        self.cor_off.setText(_translate("MainWindow", "cor zero"))
        self.cor_stop.setText(_translate("MainWindow", "cor stop"))
