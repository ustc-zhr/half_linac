# -*- coding: utf-8 -*-

# Form implementation generated from reading ui file 'OrbCorgui.ui'
#
# Created by: PyQt5 UI code generator 5.12.3
#
# WARNING! All changes made in this file will be lost!


from PyQt5 import QtCore, QtGui, QtWidgets


class Ui_MainWindow(object):
    def setupUi(self, MainWindow):
        MainWindow.setObjectName("MainWindow")
        MainWindow.resize(567, 477)
        self.centralwidget = QtWidgets.QWidget(MainWindow)
        self.centralwidget.setObjectName("centralwidget")
        self.verticalLayout_2 = QtWidgets.QVBoxLayout(self.centralwidget)
        self.verticalLayout_2.setObjectName("verticalLayout_2")
        self.gridLayout = QtWidgets.QGridLayout()
        self.gridLayout.setObjectName("gridLayout")
        self.pushButton_4 = QtWidgets.QPushButton(self.centralwidget)
        self.pushButton_4.setObjectName("pushButton_4")
        self.gridLayout.addWidget(self.pushButton_4, 0, 0, 1, 1)
        self.formLayout_3 = QtWidgets.QFormLayout()
        self.formLayout_3.setObjectName("formLayout_3")
        self.label_6 = QtWidgets.QLabel(self.centralwidget)
        self.label_6.setObjectName("label_6")
        self.formLayout_3.setWidget(0, QtWidgets.QFormLayout.LabelRole, self.label_6)
        self.comboBox = QtWidgets.QComboBox(self.centralwidget)
        self.comboBox.setObjectName("comboBox")
        self.comboBox.addItem("")
        self.comboBox.addItem("")
        self.formLayout_3.setWidget(0, QtWidgets.QFormLayout.FieldRole, self.comboBox)
        self.samplingIntervalSLabel = QtWidgets.QLabel(self.centralwidget)
        self.samplingIntervalSLabel.setObjectName("samplingIntervalSLabel")
        self.formLayout_3.setWidget(1, QtWidgets.QFormLayout.LabelRole, self.samplingIntervalSLabel)
        self.samplingIntervalSLineEdit = QtWidgets.QLineEdit(self.centralwidget)
        self.samplingIntervalSLineEdit.setObjectName("samplingIntervalSLineEdit")
        self.formLayout_3.setWidget(1, QtWidgets.QFormLayout.FieldRole, self.samplingIntervalSLineEdit)
        self.correctorAccuracyUmLabel = QtWidgets.QLabel(self.centralwidget)
        self.correctorAccuracyUmLabel.setObjectName("correctorAccuracyUmLabel")
        self.formLayout_3.setWidget(2, QtWidgets.QFormLayout.LabelRole, self.correctorAccuracyUmLabel)
        self.correctorAccuracyUmLineEdit = QtWidgets.QLineEdit(self.centralwidget)
        self.correctorAccuracyUmLineEdit.setObjectName("correctorAccuracyUmLineEdit")
        self.formLayout_3.setWidget(2, QtWidgets.QFormLayout.FieldRole, self.correctorAccuracyUmLineEdit)
        self.sampPerStepLineEdit = QtWidgets.QLineEdit(self.centralwidget)
        self.sampPerStepLineEdit.setObjectName("sampPerStepLineEdit")
        self.formLayout_3.setWidget(3, QtWidgets.QFormLayout.FieldRole, self.sampPerStepLineEdit)
        self.sampPerStepLabel = QtWidgets.QLabel(self.centralwidget)
        self.sampPerStepLabel.setObjectName("sampPerStepLabel")
        self.formLayout_3.setWidget(3, QtWidgets.QFormLayout.LabelRole, self.sampPerStepLabel)
        self.gridLayout.addLayout(self.formLayout_3, 3, 0, 1, 1)
        self.pushButton_2 = QtWidgets.QPushButton(self.centralwidget)
        self.pushButton_2.setObjectName("pushButton_2")
        self.gridLayout.addWidget(self.pushButton_2, 4, 1, 1, 1)
        self.pushButton_3 = QtWidgets.QPushButton(self.centralwidget)
        self.pushButton_3.setObjectName("pushButton_3")
        self.gridLayout.addWidget(self.pushButton_3, 0, 1, 1, 1)
        self.gridLayout.setColumnStretch(0, 1)
        self.gridLayout.setColumnStretch(1, 1)
        self.verticalLayout_2.addLayout(self.gridLayout)
        MainWindow.setCentralWidget(self.centralwidget)
        self.menubar = QtWidgets.QMenuBar(MainWindow)
        self.menubar.setGeometry(QtCore.QRect(0, 0, 567, 20))
        self.menubar.setObjectName("menubar")
        MainWindow.setMenuBar(self.menubar)
        self.statusbar = QtWidgets.QStatusBar(MainWindow)
        self.statusbar.setObjectName("statusbar")
        MainWindow.setStatusBar(self.statusbar)

        self.retranslateUi(MainWindow)
        QtCore.QMetaObject.connectSlotsByName(MainWindow)

    def retranslateUi(self, MainWindow):
        _translate = QtCore.QCoreApplication.translate
        MainWindow.setWindowTitle(_translate("MainWindow", "Orbit Correct"))
        self.pushButton_4.setText(_translate("MainWindow", "start"))
        self.label_6.setText(_translate("MainWindow", "Method"))
        self.comboBox.setItemText(0, _translate("MainWindow", "one-to-one"))
        self.comboBox.setItemText(1, _translate("MainWindow", "global"))
        self.samplingIntervalSLabel.setText(_translate("MainWindow", "sampling interval(s)"))
        self.correctorAccuracyUmLabel.setText(_translate("MainWindow", "corrector accuracy(um)"))
        self.sampPerStepLabel.setText(_translate("MainWindow", "samp per step"))
        self.pushButton_2.setText(_translate("MainWindow", "cor zero"))
        self.pushButton_3.setText(_translate("MainWindow", "stop"))
