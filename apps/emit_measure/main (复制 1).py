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

nest_dict = lambda: defaultdict(nest_dict)
jsonpath = st.rootpath+"/virtual_machine/half_elegant/halflinac.json"

class structData:
    def __init__(self):
        pass
        
class myWindow(QWidget,Ui_Form):
    def __init__(self):
        super().__init__()
        self.setupUi(self)

        # default settings 
        # ----------------
        self.lineEdit_2.setText("2200") # energy=2200MeV
        self.lineEdit_24.setText("5") # freq time=5s
        self.lineEdit_7.setText("0")  # K1-start
        self.lineEdit_8.setText("5")  # K1-end 
        self.lineEdit_9.setText("15") # steps=15
        self.lineEdit_10.setText("5") # samples=5 

        self.scan = None

        self.pushButton.clicked.connect(self.startScan)
        self.pushButton_2.clicked.connect(self.recalculate)
        self.pushButton_3.clicked.connect(self.clearPlot)
        self.pushButton_4.clicked.connect(self.start_twissCalc)
        self.pushButton_5.clicked.connect(self.stopScan)

        self.comboBox.currentIndexChanged.connect(self.updateComboBox4)

    def updateComboBox4(self, index):  
        # 根据第一个QComboBox的选择来更新第二个QComboBox的内容  
        # 这里只是一个示例，你可以根据实际需求来设置第二个QComboBox的内容  
        if index in [0,1,2]:  
            self.comboBox_4.clear()  # 清除当前选项  
            self.comboBox_4.addItems(["PRF06", "PRF07", "PRF08"])  
            print(index)
        elif index in [3,4]:  
            self.comboBox_4.clear()  
            self.comboBox_4.addItems(["PRF04"])  
            print(index)


    def get_setting(self):
        para = structData()
        # get scan parameters
        para.quad_name = self.comboBox.currentText()
        para.flag_name = self.comboBox_4.currentText()

        para.quadPV = "HALF:IN:QUAD:"+para.quad_name+":K1:ao"
        para.flagSigxPV = "HALF:IN:FLAG:"+para.flag_name+":sigx"
        para.flagSigyPV = "HALF:IN:FLAG:"+para.flag_name+":sigy"

        para.k1_from  = float(self.lineEdit_7.text())
        para.k1_end   = float(self.lineEdit_8.text())
        para.k1_steps = int(self.lineEdit_9.text())
        para.samples  = int(self.lineEdit_10.text())
        para.EnergyMeV   = float(self.lineEdit_2.text())
        para.sleeptime = float(self.lineEdit_24.text())

        return para
 
    def startScan(self):
        self.clearPlot()

        self.paras = self.get_setting()
        self.paras.recal = False
        self.paras.clear = False 
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
        self.paras.clear = False 
        self.scan = scanThread(self.paras)
        self.scan.start()
        self.scan.trigger.connect(self.display)

    def clearPlot(self):
        self.clear = clearThread()
        self.clear.start()
        self.clear.trigger.connect(self.display)

    def start_twissCalc(self):
        para = {}
        para["quad1"] = self.comboBox_2.currentText()
        para["quad2"] = self.comboBox_3.currentText()
        
        para["inverse_map"] = self.radioButton.isChecked()

        if self.radioButton_2.isChecked() == True:
            para["plane"] = "yplane"
        else:
            para["plane"] = "xplane"
        
        para["beta0"] = float(self.lineEdit.text())
        para["alpha0"] = float(self.lineEdit_3.text())
        para["gamma0"] = float(self.lineEdit_6.text())

        para["EnergyMeV"] = float(self.lineEdit_2.text())

        self.twissCal = twissCalThread(para)
        self.twissCal.start()
        self.twissCal.trigger.connect(self.showTwiss)

    def display(self,dict):
        if "clear" in dict:
            # clear all the results
            self.widget.axes.clear()
            self.widget_2.axes.clear()
            self.widget_8.axes.clear()
            self.widget_9.axes.clear()

            self.widget.canvas.draw()
            self.widget_2.canvas.draw()
            self.widget_8.canvas.draw()
            self.widget_9.canvas.draw()

            self.lineEdit_11.setText("")
            self.lineEdit_12.setText("")
            self.lineEdit_13.setText("")
            self.lineEdit_14.setText("")
            self.lineEdit_15.setText("")
            self.lineEdit_16.setText("")

            self.lineEdit_39.setText("")
            self.lineEdit_35.setText("")
            self.lineEdit_40.setText("")
            self.lineEdit_36.setText("")
            self.lineEdit_38.setText("")
            self.lineEdit_37.setText("")

            self.lineEdit_4.setText( "")
            self.lineEdit_5.setText( "")
            self.lineEdit_20.setText("")
            self.lineEdit_19.setText("")
            self.lineEdit_18.setText("")
            
            self.lineEdit_41.setText("")
            self.lineEdit_42.setText("")
            self.lineEdit_43.setText("")
            self.lineEdit_44.setText("")
            self.lineEdit_45.setText("")
            
            return

        if dict["method"] == None:
            k1 = dict["k1"]
            sigx = dict["sigx"]
            sigy = dict["sigy"]
       
            self.widget.axes.plot(k1,sigx,"xr")
            self.widget.axes.set_xlabel("$K_1 (m^{-2})$")
            self.widget.axes.set_ylabel("sigx (mm)")
            self.widget.canvas.draw()

            self.widget_8.axes.plot(k1,sigy,"xr")
            self.widget_8.axes.set_xlabel("$K_1 (m^{-2})$")
            self.widget_8.axes.set_ylabel("sigy (mm)")
            self.widget_8.canvas.draw()

        elif dict["method"] == "parabolic":
            xx     = dict["xplane"]["xx"]
            yy     = dict["xplane"]["yy"]
            err    = dict["xplane"]["err"]
            fit_yy = dict["xplane"]["fit_yy"]
            a      = dict["xplane"]["a"]
            b      = dict["xplane"]["b"]
            c      = dict["xplane"]["c"]

            self.widget_2.axes.clear()
            self.widget_2.axes.errorbar(-xx,yy,err,fmt=".r",ecolor='g',capsize=3)
            self.widget_2.axes.plot(-xx,fit_yy,"--b",label="fitting-curve")
            self.widget_2.axes.set_xlabel("$-K= K_1 L_q (m^{-1})$")
            self.widget_2.axes.set_ylabel("$sigx^2 (mm^2)$")
            self.widget_2.axes.legend()
            self.widget_2.canvas.draw()

            self.lineEdit_11.setText(str(dict["xplane"]["ex"]))
            self.lineEdit_12.setText(str(dict["xplane"]["beta"]))
            self.lineEdit_13.setText(str(dict["xplane"]["alpha"]))
            self.lineEdit_14.setText(str(dict["xplane"]["gamma"]))
            self.lineEdit_15.setText(str(dict["xplane"]["exn"]))

            curve = "sigx^2=" +str(a) +"K^2+" +str(b) +"K+" +str(c)
            self.lineEdit_16.setText(curve)

            #y-plane
            xx     = dict["yplane"]["xx"]
            yy     = dict["yplane"]["yy"]
            err    = dict["yplane"]["err"]
            fit_yy = dict["yplane"]["fit_yy"]
            a      = dict["yplane"]["a"]
            b      = dict["yplane"]["b"]
            c      = dict["yplane"]["c"]

            self.widget_9.axes.clear()
            self.widget_9.axes.errorbar(xx,yy,err,fmt=".r",ecolor='g',capsize=3)
            self.widget_9.axes.plot(xx,fit_yy,"--b",label="fitting-curve")
            self.widget_9.axes.set_xlabel("$K= K_1 L_q (m^{-1})$")
            self.widget_9.axes.set_ylabel("$sigy^2 (mm^2)$")
            self.widget_9.axes.legend()
            self.widget_9.canvas.draw()

            self.lineEdit_39.setText(str(dict["yplane"]["ex"]))
            self.lineEdit_35.setText(str(dict["yplane"]["beta"]))
            self.lineEdit_40.setText(str(dict["yplane"]["alpha"]))
            self.lineEdit_36.setText(str(dict["yplane"]["gamma"]))
            self.lineEdit_38.setText(str(dict["yplane"]["exn"]))

            curve = "sigy^2=" +str(a) +"K^2+" +str(b) +"K+" +str(c)
            self.lineEdit_37.setText(curve)

        elif dict["method"] == "leastSquares":
            self.lineEdit_4.setText(str(dict["xplane"]["ex"]))
            self.lineEdit_5.setText(str(dict["xplane"]["exn"]))
            self.lineEdit_20.setText(str(dict["xplane"]["beta"]))
            self.lineEdit_19.setText(str(dict["xplane"]["alpha"]))
            self.lineEdit_18.setText(str(dict["xplane"]["gamma"]))
            
            self.lineEdit_41.setText(str(dict["yplane"]["ex"]))
            self.lineEdit_42.setText(str(dict["yplane"]["exn"]))
            self.lineEdit_43.setText(str(dict["yplane"]["beta"]))
            self.lineEdit_44.setText(str(dict["yplane"]["alpha"]))
            self.lineEdit_45.setText(str(dict["yplane"]["gamma"]))

        else:
            print("Error, for final results.")
            sys.exit(0)

    def showTwiss(self, dict):
        beta  = round(dict["beta"], 2)
        alpha = round(dict["alpha"],2)
        gamma = round(dict["gamma"],2)

        self.lineEdit_17.setText(str(beta))
        self.lineEdit_21.setText(str(alpha))
        self.lineEdit_22.setText(str(gamma))

class clearThread(QThread):
    trigger = pyqtSignal(dict)
    def __init__(self):
        super().__init__()

    def run(self):
        todisp = {}
        todisp["clear"] = True
        self.trigger.emit(todisp)
 
class twissCalThread(QThread):
    trigger = pyqtSignal(dict)
    def __init__(self, para):
        super().__init__()

        self.input = para

    def run(self):
        quad1 = self.input["quad1"]
        quad2 = self.input["quad2"]

        twiss0={}
        twiss0["beta0"]  = self.input["beta0"]
        twiss0["alpha0"] = self.input["alpha0"]
        twiss0["gamma0"] = self.input["gamma0"]

        plane = self.input["plane"]
        inverse = self.input["inverse_map"]

        trans = transfer(self.input["EnergyMeV"])
        twiss1 = trans.getTwiss1(quad1,quad2,twiss0,plane=plane,inverse=inverse)

        self.trigger.emit(twiss1)

#class scanThread():
#    def __init__(self,paras):
class scanThread(QThread):

    trigger = pyqtSignal(dict)

    def __init__(self,paras):
        super().__init__()
        self.quad_name  = paras.quad_name.upper() 
        self.flag_name  = paras.flag_name.upper() 
        self.quadPV     = paras.quadPV    
        self.flagSigxPV = paras.flagSigxPV
        self.flagSigyPV = paras.flagSigyPV
        self.k1_from    = paras.k1_from   
        self.k1_end     = paras.k1_end    
        self.k1_steps   = paras.k1_steps  
        self.samples    = paras.samples   
        self.EnergyMeV  = paras.EnergyMeV
        self.sleeptime  = paras.sleeptime
        self.recal      = paras.recal 

        self.is_running = True

    def run(self):
        tmp = {}
        tmp["method"] = None

        if self.recal == False:
            # scan the quad
            # =======================
            k1_list = np.linspace(self.k1_from,self.k1_end,self.k1_steps)

            self.k1l =[]
            self.sigxl = []
            self.sigyl = []

            jsonfile2 = nest_dict()

            iniK1 = epics.caget(self.quadPV)
            for k1 in k1_list:
                # set quad 
                epics.caput(self.quadPV,k1)

                jsonfile2["K1="+str(k1)]["K1"] = k1
                jsonfile2["K1="+str(k1)]["sigx"] = []
                jsonfile2["K1="+str(k1)]["sigy"] = []
                for j in range(self.samples):
                    if self.is_running == True:
                        print("Quad K1=",k1)

                        # wait for quad setting and simulation/facility being finished 
                        time.sleep(self.sleeptime)
                        # get the sigx and sigy
                        tmp1 = k1
                        tmp2 = epics.caget(self.flagSigxPV)
                        tmp3 = epics.caget(self.flagSigyPV)

                        tmp["k1"]   = k1
                        tmp["sigx"] = tmp2
                        tmp["sigy"] = tmp3

                        jsonfile2["K1="+str(k1)]["sigx"].append(tmp2)
                        jsonfile2["K1="+str(k1)]["sigy"].append(tmp3)

                        self.k1l.append(tmp1)
                        self.sigxl.append(tmp2)
                        self.sigyl.append(tmp3)

                        self.trigger.emit(tmp)
                    else:
                        epics.caput(self.quadPV, iniK1)
                        print("Stop scan, quad is back to initial values, K1=",iniK1)
                        return
                       
            epics.caput(self.quadPV, iniK1)
            print("Scan finished, quad is back to initial values, K1=",iniK1)

            # dump the scan results
            #jsonfile = {}
            #jsonfile["k1l"]   = self.k1l
            #jsonfile["sigxl"] = self.sigxl
            #jsonfile["sigyl"] = self.sigyl
            #with open("scanResults.json","w") as f:
            #    f.write(json.dumps(jsonfile,indent=4))

            txt = np.matrix([self.k1l,self.sigxl,self.sigyl]).transpose()
            np.savetxt("scanResults.txt",txt,fmt="%.6e")

            #with open("scanResults2.json","w") as f:
            #    f.write(json.dumps(jsonfile2,indent=4))
        
        elif self.recal == True:
            print("Loading scanResults.json ...")
            #with open("scanResults.json","r") as f:
            #    data = json.load(f)
            #self.k1l   = np.array(data["k1l"])
            #self.sigxl = np.array(data["sigxl"])   #[mm]
            #self.sigyl = np.array(data["sigyl"])   #[mm]
            with open("scanResults.txt","r") as f:
                data = np.loadtxt(f)
            self.k1l   = data[:,0]
            self.sigxl = data[:,1]   #[mm]
            self.sigyl = data[:,2]   #[mm]

        else:
            print("Error, self.recal should be True or False.")
            sys.exit(0)
        
        # Parabolic fitting method
        # ========================
        # get the transfer matrix of (exit of quad,flag) 
        trans = transfer(self.EnergyMeV)
        mat = trans.get_map(self.quad_name,self.flag_name)
        
        m11 = mat[0,0]
        m12 = mat[0,1]
        m33 = mat[2,2]
        m34 = mat[2,3]
        try:
            dim0 = len(self.k1l)/self.samples
            k1l   = np.reshape(self.k1l,  (int(dim0),self.samples))
            sigxl = np.reshape(self.sigxl,(int(dim0),self.samples))
            sigyl = np.reshape(self.sigyl,(int(dim0),self.samples))

            tmp["method"] = "parabolic"
            tmp["xplane"] = self.parabolicfitting(k1l,sigxl,m11,m12)
            tmp["yplane"] = self.parabolicfitting(-k1l,sigyl,m33,m34)
            self.trigger.emit(tmp)
            time.sleep(2)
        except:
            print("Warning: Please delete all points for a K1 value to make every step has the same samples.")
            print("However, this would not affect least squares method.")

        # Least squares method
        # ========================
        tmpx, tmpy = self.leastSquare()
        
        # X-plane
        tmp["method"]    = "leastSquares"

        tmpxx = {}
        tmpxx["ex"]    = tmpx.ex
        tmpxx["exn"]   = tmpx.exn
        tmpxx["beta"]  = tmpx.beta
        tmpxx["alpha"] = tmpx.alpha
        tmpxx["gamma"] = tmpx.gamma
        tmp["xplane"] = tmpxx

        # Y-plane
        tmpyy = {}
        tmpyy["ex"]    = tmpy.ex
        tmpyy["exn"]   = tmpy.exn
        tmpyy["beta"]  = tmpy.beta
        tmpyy["alpha"] = tmpy.alpha
        tmpyy["gamma"] = tmpy.gamma
        tmp["yplane"] = tmpyy

        self.trigger.emit(tmp)
       
        print("Program finished !")

    # Method-1, least squares method
    # --------------------
    def leastSquare(self):
        k1l  = np.array(self.k1l)
        sigx = np.array(self.sigxl)    #[mm]
        sigy = np.array(self.sigyl)    #[mm]
        
        sigxx = sigx**2
        sigyy = sigy**2
        
        A0_x = []
        A0_y = []
        for k1 in k1l:
            # get the transfer map 
            trans = transfer(self.EnergyMeV)
            mat = trans.get_map(self.quad_name,self.flag_name,k1=k1,seq="ent2exit")
            
            # X-plane
            A11 = mat[0,0]**2
            A12 = 2*mat[0,0]*mat[0,1]
            A13 = mat[0,1]**2
            A0_x = A0_x + [A11,A12,A13]

            # Y-plane
            A11 = mat[2,2]**2
            A12 = 2*mat[2,2]*mat[2,3]
            A13 = mat[2,3]**2
            A0_y = A0_y + [A11,A12,A13]

        # for x-plane
        tmpx = self._solveMat(A0_x, k1l, sigxx)
        # for y-plane
        tmpy = self._solveMat(A0_y, k1l, sigyy)

        return tmpx,tmpy

    def _solveMat(self,A0,k1l,sigxx):
        A = np.asmatrix( np.reshape(A0,(len(k1l),3)) )
        b = np.asmatrix(sigxx).transpose()
        
        AA = A.transpose()*A
        bb = A.transpose()*b
        
        xx = np.linalg.solve(AA,bb)
        
        sig11 = xx[0,0]
        sig12 = xx[1,0]
        sig22 = xx[2,0]
        
        try:
            ex = math.sqrt(sig11*sig22-sig12**2)
            beta  = sig11/ex
            alpha = -sig12/ex
            gamma = sig22/ex
            
            gam0 = self.EnergyMeV*1e6/st.electron_mass
            exn = ex*gam0
            
            #print("exn,beta,alpha,gamma",exn,beta,alpha,gamma)
            tmp = structData()
            tmp.ex    = round(ex   ,2)
            tmp.exn   = round(exn  ,2)
            tmp.beta  = round(beta ,2)
            tmp.alpha = round(alpha,2)
            tmp.gamma = round(gamma,2)

        except:
            tmp = structData()
            tmp.ex    = None 
            tmp.exn   = None 
            tmp.beta  = None 
            tmp.alpha = None 
            tmp.gamma = None 

        return tmp

    # Method-2, parabolic fitting 
    #----------------------------------------
    def parabolicfitting(self,k1l,sigxl,m11,m12):

        k1_ave   = np.mean(k1l,1)
        sigx_ave = np.mean(sigxl,1)
        
        # get the error, for error bar plot
        err_sigx = np.max(sigxl,1)**2 - sigx_ave**2

        with open(jsonpath,"r") as f:
            lte = json.load(f)

        lattice = lte["lattice"]
        Lq = lattice[self.quad_name]["L"]

        s_quad = lattice[self.quad_name]["S"]
        s_flag = lattice[self.flag_name]["S"]
        distance = float(s_flag) - float(s_quad) 
     
        xx = -k1_ave*float(Lq)
        yy = sigx_ave**2
        
        ## resample the data
        #id1 = 0
        #id2 = steps
        #xxx =       xx[id1:id2]
        #yyy =       yy[id1:id2]
        #err = err_sigx[id1:id2]
        #plt.figure()
        #plt.errorbar(xxx,yyy,err,fmt='.r',ecolor='g',capsize=3)
        
        # fitting with 2-nd curve
        # -------------------------
        def paraFunc(x,a,b,c):
            y = a*x**2+b*x+c
            return y
        popt,pcov = curve_fit(paraFunc,xx,yy)
        fit_yy = paraFunc(xx,popt[0],popt[1],popt[2])

        tmp = {}
        tmp["xx"]     = xx
        tmp["yy"]     = yy
        tmp["err"]    = err_sigx
        tmp["fit_yy"] = fit_yy
        
        #plt.plot(xx,fit_yy,'--b',label='fitting-curve')
        #plt.legend()
        #plt.xlabel("-K1*Lq")
        #plt.ylabel("$sigx^2$")
        #plt.show()
        
        # calc to get twiss and emit 
        #=====================================
        a = popt[0]
        b = popt[1]
        c = popt[2]

        fac = np.sqrt(4*a*c-b**2)
        ex    = fac/(2*m12**2)
        alpha = (-b+2*a*m11/m12)/fac
        beta  = 2*a/fac
        gamma = (1+alpha**2)/beta
        
        gam0 = self.EnergyMeV*1e6/st.electron_mass
        exn = ex*gam0
        
        #print("exn,beta,alpha,gamma",exn,beta,alpha,gamma)

        tmp["ex"]    = round(ex   ,2) 
        tmp["exn"]   = round(exn  ,2) 
        tmp["beta"]  = round(beta ,2)
        tmp["alpha"] = round(alpha,2)
        tmp["gamma"] = round(gamma,2)

        tmp["a"] = round(a,2)
        tmp["b"] = round(b,2)
        tmp["c"] = round(c,2)

        return tmp
    
    def stop(self):
        self.is_running = False

class transfer:
    def __init__(self,EnergyMeV=None):
        if EnergyMeV == None:
            self.energy = None
        else:
           #re-set the energy at the entrance of lattice
           self.energy = EnergyMeV

    def getTwiss1(self, quad1, quad2, twiss0, plane="xplane", inverse=False):
        mat = self.get_map(quad1,quad2,seq="ent2exit")

        if inverse == False:
            pass
        else: 
            mat = np.linalg.inv(mat)

        if plane == "xplane":
            m11 = mat[0,0]
            m12 = mat[0,1]
            m21 = mat[1,0]
            m22 = mat[1,1]
        else:
            m11 = mat[2,2]
            m12 = mat[2,3]
            m21 = mat[3,2]
            m22 = mat[3,3]

        beta0   = twiss0["beta0"]
        alpha0  = twiss0["alpha0"]
        gamma0  = twiss0["gamma0"]
        beta  =  m11**2*beta0      -2*m11*m12*alpha0  +m12**2*gamma0
        alpha = -m11*m21*beta0 +(2*m12*m21+1)*alpha0 -m12*m22*gamma0
        gamma =  m21**2*beta0      -2*m21*m22*alpha0  +m22**2*gamma0

        twiss1={}
        twiss1["beta"]  = beta
        twiss1["alpha"] = alpha
        twiss1["gamma"] = gamma

        return twiss1

    def get_map(self, elem1, elem2, k1=None, seq="exit2exit"):
        # generate ImpactZ.in with chosen lattice 
        # ==========================
        # get the lattice element line within (elem1,elem2)
        with open(jsonpath,"r") as f:
            lte = json.load(f)
        contl = lte["control"]
        beam  = lte["beam"]
        lattice  = lte["lattice"]
        usedline = lte["usedline"]

        quad = elem1
        flag = elem2
        if k1!=None:
            # update lattice quad strength with k1
            lattice[quad]["K1"] = str(k1)
            ## find the index of quad--flag
            #id1 = usedline.index(quad)
            #id2 = usedline.index(flag)
            #scanline = usedline[id1:id2+1]

        if seq == "exit2exit":
            # map of exit of elem1 => exit of elem2
            id1 = usedline.index(quad)
            id2 = usedline.index(flag)
            scanline = usedline[id1+1:id2+1]

        elif seq == "ent2exit":
            # map of entrance of elem1 => exit of elem2
            id1 = usedline.index(quad)
            id2 = usedline.index(flag)
            scanline = usedline[id1:id2+1]
 
        else:
            print("Error for seq, stop!")
            sys.exit()
       
        print("usedline=",scanline)

        # beam settings
        beam["DISTRIBUTION_TYPE"] = "19"
        beam["NP"] = "7"
        beam["TOTAL_CHARGE"] = "0"
        contl["CORE_NUM_L"] = "1"
        contl["CORE_NUM_T"] = "1"
        contl["DEFAULT_ORDER"] = "1"

        # change pipe radius to big enough to avoid particle loss
        contl["PIPE_RADIUS"] = "100.0"

        # get optics transfer matrix:
        # - change all errors to 0
        # - change all correctors to 0
        for elem in usedline:
            if "DX" in lattice[elem] or "DY" in lattice[elem]:
                lattice[elem]["DX"] = "0.0"
                lattice[elem]["DY"] = "0.0"
                lattice[elem]["ROTATE_X"] = "0.0"
                lattice[elem]["ROTATE_Y"] = "0.0"
                lattice[elem]["ROTATE_Z"] = "0.0"
            
            if "KICK" in lattice[elem]:
                lattice[elem]["KICK"] = "0.0"

        # add watch elements at the final position
        lattice["finalwatch"] = {}
        lattice["finalwatch"]["FILENAME_ID"]= "1099"
        lattice["finalwatch"]["SAMPLE_FREQ"]= "0"
        lattice["finalwatch"]["COORD_CONV"]= "NORMAL"
        lattice["finalwatch"]["SLICE_INFO"]= "0"
        lattice["finalwatch"]["COORD_INFO"]= "1"
        lattice["finalwatch"]["SLICE_BIN"]= "0"
        lattice["finalwatch"]["NAME"]= "finalwatch"
        lattice["finalwatch"]["TYPE"]= "WATCH"
        scanline.append("finalwatch") 
                   
        # update json with new lte
        lte["beam"] = beam
        lte["control"] = contl
        lte["lattice"] = lattice
        lte["usedline"] = scanline
        
        with open("irfel.json","w") as f:
            f.write(json.dumps(lte,indent=4))
        
        impzpar = impactz_parser()    
        impzpar.json2impzin()   

        # get particle.in ready
        # ==========================
        val = 1e-5
        x     = val 
        px    = val  
        y     = val 
        py    = val 
        z     = val
        delta = val 
        
        # get scale freq and ref energy
        if self.energy == None:
            kin  = float(contl["KINETIC_ENERGY"])
        else:
            kin = self.energy*1e6

        freq = float(contl["FREQ_RF_SCALE"])
        
        gam0 = (kin+st.electron_mass)/st.electron_mass
        bet0 = math.sqrt(1-1/gam0**2)
        gambet0 = gam0*bet0
        scxl = st.c_light/(2*math.pi*freq)
        
        X  = x/scxl
        Px = px*gambet0
        Y  = y/scxl
        Py = py*gambet0
        T  = -z/scxl/bet0 
        Pt = -delta*gambet0*bet0
        qm = -1/st.electron_mass
        q = 0
        
        pha0 = np.zeros((7,9))
        pha0[1,0] = X
        pha0[2,1] = Px
        pha0[3,2] = Y
        pha0[4,3] = Py
        pha0[5,4] = T
        pha0[6,5] = Pt
        pha0[:,6] = qm
        pha0[:,8] = [1,2,3,4,5,6,7]
        
        #dump to particle.in
        if not os.path.exists("./impz"):
            os.makedirs("./impz")
        f = open("./impz/particle.in","w") 
        f.write("7 0 0 \n")
        for j in range(7):
            f.write("%e %e %e %e %e %e %e %e %d \n" % tuple(pha0[j,:]))
        f.close()

        # run IMPACT-Z 
        # ==========================
        cupath = os.getcwd()
        os.chdir("./impz")
        os.system("ImpactZ.exe ImpactZ.in > run_impz.log")
        os.chdir(cupath)
        
        # get the final phase 
        pha10 = np.loadtxt("./impz/fort.1099",skiprows=2)
        pha1 = np.delete(pha10,5,1) #the 6th col is dgam
        
        mj1 = pha1[0]/x
        mj2 = pha1[1]/px
        mj3 = pha1[2]/y
        mj4 = pha1[3]/py
        mj5 = pha1[4]/z
        mj6 = pha1[5]/delta
        
        qf_map = np.asmatrix( [mj1,mj2,mj3,mj4,mj5,mj6] ).transpose()
        
        #print(qf_map)        
        return qf_map

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = myWindow()
    window.show()
    sys.exit(app.exec_())
    
    # window.plot_beamprofile()
