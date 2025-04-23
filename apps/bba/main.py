from gui import Ui_Form
import sys
from PyQt5.QtWidgets import QMainWindow, QApplication, QLabel, QHBoxLayout, QWidget
from PyQt5.QtCore import QTimer
import epics
import time
import numpy as np
from PyQt5.QtCore import QThread,pyqtSignal
import matplotlib as mpl
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit
import half_linac.setup as st
import json
import copy
from half_linac.virtual_machine.half_elegant.elegant_parser import elegant_parser
import os
import math
from collections import defaultdict

class structData:
    def __init__(self):
        pass
        
class myWindow(QWidget,Ui_Form):
    def __init__(self):
        super().__init__()
        self.setupUi(self)

        # default settings for BBA-1 
        # ---------------------------
        self.lineEdit_3.setText("5")  # steps for corrector
        self.lineEdit_5.setText("5") # steps for quad
        self.lineEdit_7.setText("6")  # freq time (s) 
        self.lineEdit_8.setText("1")  # samples/step
        
        # for debug
        self.lineEdit_6.setText("45")
        self.lineEdit_4.setText("60")
        self.lineEdit.setText("-0.001")
        self.lineEdit_2.setText("0.001")

        # default settings for BBA-2 
        # ---------------------------
        self.lineEdit_12.setText("5")  # steps for corrector
        self.lineEdit_16.setText("5") # steps for quad
        self.lineEdit_15.setText("5")  # freq time (s) 
        self.lineEdit_9.setText("3")  # samples/step
        self.lineEdit_20.setText("30.8")
        self.lineEdit_22.setText("10")

        self.lineEdit_23.setText("-0.4813*current-0.7747")
        self.lineEdit_25.setText("0.058287")
        self.lineEdit_24.setText("-0.4968*current-0.3153")
        self.lineEdit_26.setText("0.052513")
        
        # for debug
        self.lineEdit_14.setText("-33")
        self.lineEdit_17.setText("-23")
        self.lineEdit_11.setText("-8")
        self.lineEdit_13.setText("-4")

        self.scan = None

        # push button
        # --------------------------
        self.pushButton.clicked.connect(self.startScan)
        self.pushButton_2.clicked.connect(self.clearPlot)
        self.pushButton_3.clicked.connect(self.recalculate)
        self.pushButton_4.clicked.connect(self.stopScan)

        self.pushButton_5.clicked.connect(self.startScan_bba2)
        self.pushButton_6.clicked.connect(self.stopScan)
        self.pushButton_8.clicked.connect(self.clearPlot_bba2)
        self.pushButton_7.clicked.connect(self.recalculate_bba2)

    def get_setting(self):
        para = structData()
        # get scan parameters
        
        para.corr = self.comboBox.currentText()
        para.quad = self.comboBox_2.currentText()
        para.bpm1 = self.comboBox_3.currentText()
        para.bpm2 = self.comboBox_4.currentText()

        para.corrPV = "IRFEL:PS:"+para.corr+":current:ao"
        para.quadPV = "IRFEL:AP:QUAD:"+para.quad+":K1:ao"
        if self.comboBox_5.currentText() == "X-Plane":
            para.plane = "X"
            para.bpm1PV = "IRFEL:BD:"+para.bpm1+":X"
            para.bpm2PV = "IRFEL:BD:"+para.bpm2+":X"
        else:
            para.plane = "Y"
            para.bpm1PV = "IRFEL:BD:"+para.bpm1+":Y"
            para.bpm2PV = "IRFEL:BD:"+para.bpm2+":Y"

        para.corr_from  = float(self.lineEdit.text())
        para.corr_end   = float(self.lineEdit_2.text())
        para.corr_steps = int(self.lineEdit_3.text())
        para.quad_from  = float(self.lineEdit_6.text())
        para.quad_end   = float(self.lineEdit_4.text())
        para.quad_steps = int(self.lineEdit_5.text())

        para.samples   = int(self.lineEdit_8.text())
        para.sleeptime = float(self.lineEdit_7.text())

        return para
 
    def startScan(self):
        self.clearPlot()
        self.paras = self.get_setting()
        self.paras.recal = False
        self.scan = scanThread(self.paras)
        #self.scan.run()
        self.scan.start()
        self.scan.trigger.connect(self.display)

    def stopScan(self):
        if self.scan != None:
            self.scan.stop()
            print("Scan thread is stopped.")

    def recalculate(self):
        self.paras = self.get_setting()
        self.paras.recal = True 
        self.scan = scanThread(self.paras)
        self.scan.start()
        self.scan.trigger.connect(self.display)

    def clearPlot(self):
        self.clear = clearThread()
        self.clear.start()
        self.clear.trigger.connect(self.display)
        
    def display(self,dict):
        if "clear" in dict:
            self.widget.axes.clear()
            self.widget_2.axes.clear()
            self.widget.canvas.draw()
            self.widget_2.canvas.draw()
            self.lineEdit_10.setText("")
            return

        if dict["show"] == "k1m2":
            k1 = dict["K1Lq"]
            m2 = dict["m2"]
            self.widget.axes.plot(k1,m2,"xr")
            self.widget.axes.set_xlabel("$K_1L_q$")
            self.widget.axes.set_ylabel("BPM2 (mm)")
            self.widget.canvas.draw()

        elif dict["show"] == "fit_k1m2":
            x = dict["x"]
            y = dict["y"]
            self.widget.axes.plot(x,y,"g--",label="fitting-curve")
            self.widget.canvas.draw()

            m1 = dict["m1"]
            S  = dict["S"]
            mm1 = dict["mm1"]
            SS  = np.ones(len(mm1))*S

            self.widget_2.axes.plot(mm1,SS,"xg")
            self.widget_2.axes.plot(m1,S,"ro")
            self.widget_2.axes.set_xlabel("BPM1 (mm)")
            self.widget_2.axes.set_ylabel("S")
            self.widget_2.canvas.draw()

        elif dict["show"] == "m1S":
            x = dict["m1"]
            y = dict["S"]
            yvals = dict["yvals"]
            offset = dict["offset"]

            self.widget_2.axes.plot(x,y,"ro")
            self.widget_2.axes.set_xlabel("BPM1 (mm)")
            self.widget_2.axes.set_ylabel("S")
 
            self.widget_2.axes.plot(x,yvals,"g-")
            self.widget_2.canvas.draw()

            self.lineEdit_10.setText(str(round(offset,1)))

    # BBA-2
    # -----------------------------
    def get_setting_bba2(self):
        para = structData()
        # get scan parameters
        
        para.quad = self.comboBox_7.currentText()
        para.corr = self.comboBox_9.currentText()
        para.bpm1 = self.comboBox_8.currentText()
        para.bpm2 = self.comboBox_6.currentText()

        para.quadPV = "IRFEL:AP:QUAD:"+para.quad+":K1:ao"
        para.corrPV = "IRFEL:PS:"+para.corr+":current:ao"
        if self.comboBox_10.currentText() == "X-Plane":
            para.plane = "X"
            para.bpm1PV = "IRFEL:BD:"+para.bpm1+":X"
            para.bpm2PV = "IRFEL:BD:"+para.bpm2+":X"
        else:
            para.plane = "Y"
            para.bpm1PV = "IRFEL:BD:"+para.bpm1+":Y"
            para.bpm2PV = "IRFEL:BD:"+para.bpm2+":Y"

        para.quad_from  = float(self.lineEdit_14.text())
        para.quad_end   = float(self.lineEdit_17.text())
        para.quad_steps = int(self.lineEdit_16.text())
        para.corr_from  = float(self.lineEdit_11.text())
        para.corr_end   = float(self.lineEdit_13.text())
        para.corr_steps = int(self.lineEdit_12.text())

        para.samples   = int(self.lineEdit_9.text())
        para.sleeptime = float(self.lineEdit_15.text())

        para.EnergyMeV = float(self.lineEdit_20.text()) 
        para.bpm1sampleNum = int(self.lineEdit_22.text())

        para.By = self.lineEdit_23.text()
        para.Bx = self.lineEdit_24.text()
        para.Leff_By = float(self.lineEdit_25.text())
        para.Leff_Bx = float(self.lineEdit_26.text())

        para.realorVM = self.comboBox_11.currentText()
        return para

    def recalculate_bba2(self):
        self.clearPlot_bba2()
        self.paras = self.get_setting_bba2()
        self.paras.recal = True 
        self.scan = scanThread_bba2(self.paras)
        self.scan.start()
        self.scan.trigger.connect(self.display_bba2)

    def startScan_bba2(self):
        self.clearPlot_bba2()
        self.paras = self.get_setting_bba2()
        self.paras.recal = False
        self.scan = scanThread_bba2(self.paras)
        self.scan.start()
        self.scan.trigger.connect(self.display_bba2)

    def display_bba2(self,dict):
        if "clear" in dict:
            self.widget_3.axes.clear()
            self.widget_4.axes.clear()
            self.widget_3.canvas.draw()
            self.widget_4.canvas.draw()
            self.lineEdit_18.setText("")
            self.lineEdit_19.setText("")
            self.lineEdit_21.setText("")
            return

        if dict["show"] == "k1m2":
            k1 = dict["K1Lq"]
            m2 = dict["m2"] *1e3
            self.widget_3.axes.plot(k1,m2,"xr")
            self.widget_3.axes.set_xlabel("$K_1L_q$")
            self.widget_3.axes.set_ylabel("BPM2 (mm)")
            self.widget_3.canvas.draw()

        elif dict["show"] == "fit_k1m2":
            x = dict["x"]
            y = dict["y"]*1e3
            self.widget_3.axes.plot(x,y,"g--",label="fitting-curve")
            self.widget_3.canvas.draw()

        elif dict["show"] == "thetam2":
            x = dict["theta"]*1e3
            y = dict["m2"]*1e3

            self.widget_4.axes.plot(x,y,"rx")
            self.widget_4.axes.set_xlabel("corrector kick (mrad)")
            self.widget_4.axes.set_ylabel("BPM2 (mm)")
            self.widget_4.canvas.draw()

        elif dict["show"] == "fit_thetam2":
            x = dict["x"]*1e3
            y = dict["y"]*1e3
            m1_ave = dict["m1_ave"]*1e3
            R12 = dict["R12"]
            b1q1 = dict["b1q1"]*1e3
            
            self.widget_4.axes.plot(x,y,"g--",label="fitting-curve")
            self.widget_4.canvas.draw()

            self.lineEdit_21.setText(str(round(m1_ave,1)))
            self.lineEdit_19.setText(str(round(R12,2)))
            self.lineEdit_18.setText(str(round(b1q1,1)))

    def clearPlot_bba2(self):
        self.clear = clearThread()
        self.clear.start()
        self.clear.trigger.connect(self.display_bba2)

# bba-1, scan thread
# -----------------------------------------
class scanThread(QThread):
    trigger = pyqtSignal(dict)
    def __init__(self,paras):
        super().__init__()
        self.par = paras
        
        self.is_running = True
        
    def run(self):
        info = {}
        if self.par.recal == False:
            cor  = epics.PV(self.par.corrPV)
            quad = epics.PV(self.par.quadPV)
            bpm1 = epics.PV(self.par.bpm1PV)
            bpm2 = epics.PV(self.par.bpm2PV)       
            
            if self.par.plane == "X":
                sign = 1
            else:
                sign = -1

            k1l   = np.linspace(self.par.quad_from,self.par.quad_end,self.par.quad_steps)
            kickl = np.linspace(self.par.corr_from,self.par.corr_end,self.par.corr_steps)

            m1 = []
            S = []
            inival_quad = quad.get()
            inival_kick = cor.get()
            print("ini values of the quad and corrector=",inival_quad, inival_kick)
            for kick in kickl:
                cor.put(kick)
                #time.sleep(5) 
                #mm1 = bpm1.get()

                m2 = []
                mm1 = []
                for k1 in k1l:
                    quad.put(k1)

                    for j in range(self.par.samples):
                        if self.is_running == True:
                            print("cor-kick,K1=",kick,k1)
                            time.sleep(self.par.sleeptime)
                            
                            mm2 = bpm2.get()
                            m2.append(mm2)
                            mm1.append(bpm1.get())

                            info["show"] = "k1m2"
                            info["K1Lq"] = k1*sign*0.05
                            info["m2"] = mm2

                            #print("info=",info)
                            self.trigger.emit(info)
                            time.sleep(1)
                        else:
                            quad.put(inival_quad)
                            cor.put(inival_kick)
                            print("Program Stop, corrector and quad is back to initial values:",inival_quad,inival_kick)
                            return

                mm1_ave = np.mean(mm1)

                # fitting
                # ------------------
                # get the mean value
                m2_mat = np.reshape(m2,(self.par.quad_steps,self.par.samples))
                m2_ave = np.mean(m2_mat,1)

                err_m2 = np.max(m2_mat,1)-m2_ave

                x = sign*k1l*0.05
                y = m2_ave

                z1 = np.polyfit(x,y,deg=1)
                p1 = np.poly1d(z1)
                yvals = p1(x)

                info["show"] = "fit_k1m2"
                info["x"] = x
                info["y"] = yvals

                SS = z1[0]
                info["m1"] = mm1_ave
                info["S"] = SS
                info["mm1"] = mm1

                m1.append(mm1_ave)
                S.append(SS)

                #print("info=",info)
                self.trigger.emit(info)
                time.sleep(1)

            quad.put(inival_quad)
            cor.put(inival_kick)
            print("Scan finished, corrector and quad is back to initial values.")

            # save data to txt file
            txt = np.matrix([m1,S]).transpose()
            np.savetxt("m1S.txt",txt,fmt="%.6e")

            x = m1
            y = S

        elif self.par.recal == True:
            with open("m1S.txt","r") as f:
                data = np.loadtxt(f)
            x = data[:,0]
            y = data[:,1]
        else:
            print("Error for recal")
            sys.exit()

        z1 = np.polyfit(x,y,deg=1)
        p1 = np.poly1d(z1)
        yvals = p1(x)
        offset = z1[1]/z1[0]

        info["show"] = "m1S"
        info["m1"] = x
        info["S"] = y
        info["yvals"] = yvals
        info["offset"] = offset

        #print("info=",info)
        self.trigger.emit(info)

        print("offset=",offset,"mm")
        print("Program finished !")

    def stop(self):
        self.is_running = False

# bba-2, scan thread
# -----------------------------------------
class scanThread_bba2(QThread):
    trigger = pyqtSignal(dict)
    def __init__(self,paras):
        super().__init__()
        self.par = paras
        
        self.is_running = True

        self.S = None
        self.m1_ave = None
        self.R12 = None
        
    def run(self):
        info = {}
        cor  = epics.PV(self.par.corrPV)
        quad = epics.PV(self.par.quadPV)
        bpm1 = epics.PV(self.par.bpm1PV)
        bpm2 = epics.PV(self.par.bpm2PV)       
        
        if self.par.plane == "X":
            sign = 1
        else:
            sign = -1

        inival_quad = quad.get()
        print("ini values of the quad=",inival_quad)

        k1l   = np.linspace(self.par.quad_from,self.par.quad_end,self.par.quad_steps)
        kickl = np.linspace(self.par.corr_from,self.par.corr_end,self.par.corr_steps)
        
        # 1) scan the quad 
        if self.par.recal == False:
            kk1 = []
            m2  = []
            for k1 in k1l:
                quad.put(k1)

                for j in range(self.par.samples):
                    if self.is_running == True:
                        print("K1=",k1)
                        time.sleep(self.par.sleeptime)
                        
                        mm2 = bpm2.get()*1e-3
                        m2.append(mm2)
                        kk1.append(k1)

                        info["show"] = "k1m2"
                        info["K1Lq"] = k1*sign*0.05
                        info["m2"] = mm2

                        self.trigger.emit(info)
                        time.sleep(1)
                    else:
                        quad.put(inival_quad)
                        print("Program Stop, quad is back to initial values:",inival_quad)
                        return
            # save results to bba2_k1m2.txt
            k1Lq = sign*np.array(kk1)*0.05
            txt = np.matrix([k1Lq,m2]).transpose()
            np.savetxt("bba2_k1Lqm2.txt",txt,fmt="%.6e")
        else:
            # recalculate, must delete all data for one step
            with open("bba2_k1Lqm2.txt","r") as f:
                data = np.loadtxt(f)
            k1Lq = data[:,0]
            m2  = data[:,1]

            info["show"] = "k1m2"
            info["K1Lq"] = k1Lq
            info["m2"] = m2

            self.trigger.emit(info)
            time.sleep(1)

        # fitting
        # -------
        # get the mean value
        k1Lq_mat = np.reshape(k1Lq,(int(len(k1Lq)/self.par.samples),self.par.samples))
        k1Lq_ave = np.mean(k1Lq_mat,1)
 
        m2_mat = np.reshape(m2,(int(len(m2)/self.par.samples),self.par.samples))
        m2_ave = np.mean(m2_mat,1)
        err_m2 = np.max(m2_mat,1)-m2_ave

        x = k1Lq_ave
        y = m2_ave

        z1 = np.polyfit(x,y,deg=1)
        p1 = np.poly1d(z1)
        yvals = p1(x)

        info["show"] = "fit_k1m2"
        info["x"] = x
        info["y"] = yvals

        self.S = z1[0]

        #print("info=",info)
        self.trigger.emit(info)
        time.sleep(1)

        quad.put(inival_quad)
        print("First scan finished, quad is back to initial values.")
        
        # 2) take several shots to get the mean values of BPM1 <m1>
        if self.par.recal == False:
            m1 = []
            print("get average BPM1 <m1> now:")
            for j in range(self.par.bpm1sampleNum):
                if self.is_running == True:
                    mm1 = bpm1.get()*1e-3
                    m1.append(mm1)
                    
                    print("BPM1 m1=",mm1*1e3,"mm")
                    time.sleep(2)
                else:
                    print("BPM1 scan stop.")
                    return
            np.savetxt("bba2_m1.txt",m1,fmt="%.6e")
        else:
            with open("bba2_m1.txt","r") as f:
                m1 = np.loadtxt(f)

        self.m1_ave = np.mean(m1)
        print("m1_ave=",round(self.m1_ave*1e3,2),"mm")

        # 3) scan corrector now
        # current to angle[rad]
        current = kickl
        if self.par.plane == "X":
            #By = (-0.4813*current-0.7747)*1e-4 #[T]
            #Leff = 0.058287
            By = eval(self.par.By)*1e-4
            Leff = self.par.Leff_By
            anglel = 299.8/self.par.EnergyMeV*By*Leff

        elif self.par.plane == "Y":
            #Bx = (-0.4968*current-0.3153)*1e-4
            #Leff = 0.052513
            Bx = eval(self.par.Bx)*1e-4
            Leff = self.par.Leff_Bx
            anglel = 299.8/self.par.EnergyMeV*Bx*Leff
        else:
            print("Error, plane should be X or Y.")
            sys.exit(0)

        # for virtual machine, kick angle=current
        # ------------
        if self.par.realorVM == "Virtual Machine":
            print("Virtual Machine.")
            anglel = kickl
        # ------------
        
        if self.par.recal == False:
            inival_kick = cor.get()
            print("ini values of the corrector=",inival_kick)
            
            theta = []
            m2 = []
            k = 0
            for kick in kickl:
                cor.put(kick)
                for j in range(self.par.samples):
                    if self.is_running == True:
                        print("corrector I=",kick)

                        time.sleep(self.par.sleeptime)
                        mm2 = bpm2.get()*1e-3
                        m2.append(mm2)
                        theta.append(anglel[k])

                        info["show"] = "thetam2"
                        info["theta"] = anglel[k]
                        info["m2"] = mm2

                        self.trigger.emit(info)
                        time.sleep(1)
                    else:
                        cor.put(inival_kick)
                        print("Program stop, corrector is back to initial value:",inival_kick)
                        return
                k = k+1    

            txt = np.matrix([theta,m2]).transpose()
            np.savetxt("bba2_thetam2.txt",txt,fmt="%.6e")

            cor.put(inival_kick)
            print("Second scan finished, corrector is back to initial values.")

        else:
            with open("bba2_thetam2.txt","r") as f:
                data = np.loadtxt(f)
            
            theta = data[:,0]
            m2 = data[:,1]

            info["show"] = "thetam2"
            info["theta"] = theta
            info["m2"] = m2

            self.trigger.emit(info)
            time.sleep(1)

        # fitting
        theta_mat = np.reshape(theta,(int(len(theta)/self.par.samples),self.par.samples))
        theta_ave = np.mean(theta_mat,1)
 
        m2_mat = np.reshape(m2,(int(len(m2)/self.par.samples),self.par.samples))
        m2_ave = np.mean(m2_mat,1)
        
        x = theta_ave
        y = m2_ave
        
        z1 = np.polyfit(x,y,deg=1)
        p1 = np.poly1d(z1)
        yvals = p1(x)

        self.R12 = z1[0]

        # 4) final calculations
        b1mq1=self.S/self.R12-self.m1_ave #[m]

        info["show"] = "fit_thetam2"
        info["x"] = x
        info["y"] = yvals
        info["m1_ave"] = self.m1_ave
        info["R12"] = self.R12
        info["b1q1"] = b1mq1

        self.trigger.emit(info)
        time.sleep(1)

    def stop(self):
        self.is_running = False

class clearThread(QThread):
    trigger = pyqtSignal(dict)
    def __init__(self):
        super().__init__()

    def run(self):
        todisp = {}
        todisp["clear"] = True
        self.trigger.emit(todisp)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = myWindow()
    window.show()
    sys.exit(app.exec_())
