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
        MainWindow.resize(570, 673)
        self.centralwidget = QtWidgets.QWidget(MainWindow)
        sizePolicy = QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Preferred)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(self.centralwidget.sizePolicy().hasHeightForWidth())
        self.centralwidget.setSizePolicy(sizePolicy)
        self.centralwidget.setObjectName("centralwidget")
        self.verticalLayout_2 = QtWidgets.QVBoxLayout(self.centralwidget)
        self.verticalLayout_2.setObjectName("verticalLayout_2")
        self.gridLayout_3 = QtWidgets.QGridLayout()
        self.gridLayout_3.setObjectName("gridLayout_3")
        spacerItem = QtWidgets.QSpacerItem(20, 40, QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Expanding)
        self.gridLayout_3.addItem(spacerItem, 1, 0, 1, 1)
        self.textEdit = QtWidgets.QTextEdit(self.centralwidget)
        self.textEdit.setMaximumSize(QtCore.QSize(16777215, 200))
        self.textEdit.setObjectName("textEdit")
        self.gridLayout_3.addWidget(self.textEdit, 0, 0, 1, 1)
        self.gridLayout = QtWidgets.QGridLayout()
        self.gridLayout.setVerticalSpacing(20)
        self.gridLayout.setObjectName("gridLayout")
        self.pushButton_2 = QtWidgets.QPushButton(self.centralwidget)
        self.pushButton_2.setMinimumSize(QtCore.QSize(0, 40))
        self.pushButton_2.setStyleSheet("QPushButton{\n"
"\n"
"color: rgb(170, 0, 0);\n"
"\n"
"font: bold 12pt;\n"
"}")
        self.pushButton_2.setObjectName("pushButton_2")
        self.gridLayout.addWidget(self.pushButton_2, 2, 0, 1, 1)
        self.pushButton = QtWidgets.QPushButton(self.centralwidget)
        self.pushButton.setMinimumSize(QtCore.QSize(0, 40))
        self.pushButton.setStyleSheet("QPushButton{\n"
"\n"
"color: rgb(170, 0, 0);\n"
"\n"
"font: bold 12pt;\n"
"}")
        self.pushButton.setDefault(False)
        self.pushButton.setFlat(False)
        self.pushButton.setObjectName("pushButton")
        self.gridLayout.addWidget(self.pushButton, 0, 0, 1, 1)
        spacerItem1 = QtWidgets.QSpacerItem(35, 20, QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Minimum)
        self.gridLayout.addItem(spacerItem1, 4, 1, 1, 1)
        self.measure_response = QtWidgets.QPushButton(self.centralwidget)
        self.measure_response.setMinimumSize(QtCore.QSize(0, 40))
        self.measure_response.setStyleSheet("QPushButton{\n"
"\n"
"color: rgb(170, 0, 0);\n"
"\n"
"font: bold 12pt;\n"
"}")
        self.measure_response.setObjectName("measure_response")
        self.gridLayout.addWidget(self.measure_response, 4, 0, 1, 1)
        self.orbit_correct = QtWidgets.QPushButton(self.centralwidget)
        self.orbit_correct.setMinimumSize(QtCore.QSize(0, 40))
        self.orbit_correct.setStyleSheet("QPushButton{\n"
"\n"
"color: rgb(170, 0, 0);\n"
"\n"
"font: bold 12pt;\n"
"}")
        self.orbit_correct.setObjectName("orbit_correct")
        self.gridLayout.addWidget(self.orbit_correct, 5, 0, 1, 1)
        self.pushButton_4 = QtWidgets.QPushButton(self.centralwidget)
        self.pushButton_4.setMinimumSize(QtCore.QSize(0, 40))
        self.pushButton_4.setStyleSheet("QPushButton{\n"
"\n"
"color: rgb(170, 0, 0);\n"
"\n"
"font: bold 12pt;\n"
"}")
        self.pushButton_4.setObjectName("pushButton_4")
        self.gridLayout.addWidget(self.pushButton_4, 3, 0, 1, 1)
        self.pushButton_3 = QtWidgets.QPushButton(self.centralwidget)
        self.pushButton_3.setMinimumSize(QtCore.QSize(0, 40))
        self.pushButton_3.setStyleSheet("QPushButton{\n"
"\n"
"color: rgb(170, 0, 0);\n"
"\n"
"font: bold 12pt;\n"
"}")
        self.pushButton_3.setObjectName("pushButton_3")
        self.gridLayout.addWidget(self.pushButton_3, 1, 0, 1, 1)
        self.cor_off = QtWidgets.QPushButton(self.centralwidget)
        self.cor_off.setObjectName("cor_off")
        self.gridLayout.addWidget(self.cor_off, 4, 2, 1, 1)
        self.cor_stop = QtWidgets.QPushButton(self.centralwidget)
        self.cor_stop.setObjectName("cor_stop")
        self.gridLayout.addWidget(self.cor_stop, 5, 2, 1, 1)
        self.gridLayout.setColumnStretch(0, 20)
        self.gridLayout_3.addLayout(self.gridLayout, 4, 0, 1, 1)
        self.vmbtn = QtWidgets.QPushButton(self.centralwidget)
        self.vmbtn.setMinimumSize(QtCore.QSize(0, 40))
        self.vmbtn.setStyleSheet("QPushButton{\n"
"\n"
"color: rgb(170, 0, 0);\n"
"\n"
"font: bold 14pt;\n"
"}")
        self.vmbtn.setObjectName("vmbtn")
        self.gridLayout_3.addWidget(self.vmbtn, 2, 0, 1, 1)
        spacerItem2 = QtWidgets.QSpacerItem(20, 40, QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Expanding)
        self.gridLayout_3.addItem(spacerItem2, 3, 0, 1, 1)
        self.verticalLayout_2.addLayout(self.gridLayout_3)
        MainWindow.setCentralWidget(self.centralwidget)
        self.menubar = QtWidgets.QMenuBar(MainWindow)
        self.menubar.setGeometry(QtCore.QRect(0, 0, 570, 20))
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
        self.textEdit.setHtml(_translate("MainWindow", "<!DOCTYPE HTML PUBLIC \"-//W3C//DTD HTML 4.0//EN\" \"http://www.w3.org/TR/REC-html40/strict.dtd\">\n"
"<html><head><meta name=\"qrichtext\" content=\"1\" /><style type=\"text/css\">\n"
"p, li { white-space: pre-wrap; }\n"
"</style></head><body style=\" font-family:\'Sans Serif\'; font-size:9pt; font-weight:400; font-style:normal;\">\n"
"<p style=\" margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px;\"><span style=\" font-size:10pt; font-weight:600; color:#ff0000;\">Pay Attention</span><span style=\" font-size:10pt; color:#ff0000;\">:</span></p>\n"
"<p style=\"-qt-paragraph-type:empty; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px; font-size:10pt; color:#ff0000;\"><br /></p>\n"
"<p style=\" margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px;\"><span style=\" font-size:10pt; color:#ff0000;\">(1) [Virtual Machine] is for virtual machine development, where you need to click [start VM] &amp; [start IOC] and you can also add error. </span></p></body></html>"))
        self.pushButton_2.setText(_translate("MainWindow", "emit measure"))
        self.pushButton.setToolTip(_translate("MainWindow", "<html><head/><body><p><br/></p></body></html>"))
        self.pushButton.setWhatsThis(_translate("MainWindow", "<html><head/><body><p><br/></p></body></html>"))
        self.pushButton.setText(_translate("MainWindow", "orbit display"))
        self.measure_response.setText(_translate("MainWindow", "measure response"))
        self.orbit_correct.setText(_translate("MainWindow", "orbit correct"))
        self.pushButton_4.setText(_translate("MainWindow", "BBA"))
        self.pushButton_3.setText(_translate("MainWindow", "beam monitor"))
        self.cor_off.setText(_translate("MainWindow", "cor zero"))
        self.cor_stop.setText(_translate("MainWindow", "cor stop"))
        self.vmbtn.setText(_translate("MainWindow", "Virtual Machine"))
