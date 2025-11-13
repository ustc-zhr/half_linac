#from gui import Ui_MainWindow
from gui import Ui_Form
import sys
from subprocess import Popen
from half_linac import setup as st
from PyQt5.QtWidgets import QMainWindow, QApplication, QLabel, QHBoxLayout, QWidget
from PyQt5.QtCore import QTimer
from epics import caget, caget_many, caput, caput_many,PV
import time
import numpy as np
from PyQt5.QtCore import QThread
import matplotlib as mpl
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit
import epics

class myWindow(QWidget,Ui_Form):
    """
    a gui window for beam monitor
    """

    def __init__(self):
        super().__init__()
        self.setupUi(self)

        # refreah the figure at 1 Hz
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.plot_beamprofile)
        self.is_timer_running = True  # 定时器状态
        self.timer.start(1000)# 每过1s timer.timeout触发一次
        
        # the real size of flag st.flag_pixel_vm change to st.flag_pixel_machine
        self.width  = st.flag_pixel_vm[0]*st.flag_pixel_width # mm
        self.height = st.flag_pixel_vm[1]*st.flag_pixel_width # mm
        self.xlim = (-0.5*self.width , 0.5*self.width  )
        self.ylim = (-0.5*self.height, 0.5*self.height ) 
        self.extent = self.xlim +self.ylim

        # default setting
        # ----------------------------------
        self.pushButton.setEnabled(False) # 上来禁用start
        self.flag_selec.setCurrentIndex(1)
        self.lineEdit_7.setText(str(0))# set move xaxis to 0
        self.lineEdit_8.setText(str(0))# set move yaxis to 0
        self.lineEdit_4.setText(str(0))# set vmin to 0 by default
        self.lineEdit_9.setText("1")# default refresh rate=1s
        
        #function in GUI
        self.pushButton.clicked.connect(self.start1_btn)
        self.pushButton_2.clicked.connect(self.stop1_btn)
        self.pushButton_3.clicked.connect(self.moveaxis)
        self.lineEdit.returnPressed.connect(self.setExpoTime)
        self.lineEdit_9.textChanged.connect(self.change_interval)

        self.h = None
        self.colorbar = None
        self.sigx = None
        self.sigy = None

    def moveaxis(self):
        if self.lineEdit_7.text() != '':
            offx = float(self.lineEdit_7.text())
        else:
            offx = 0

        if self.lineEdit_8.text() != '':
            offy = float(self.lineEdit_8.text())
        else:
            offy = 0

        tmp1 = tuple(np.array(self.extent)[0:2]-offx)
        tmp2 = tuple(np.array(self.extent)[2:4]-offy)

        self.extent = tmp1+tmp2

        # also update self.xlim
        if self.h != None:
            self.h.axes.set_xlim(tmp1)
            self.h.axes.set_ylim(tmp2)

        # offx and offy back to 0
        # self.lineEdit_7.setText("0")
        # self.lineEdit_8.setText("0")

    def init_realOrVM(self):
        #sys.argv[1] => real/vm, real machine or virtual machine

        #pv = "IRFEL:BD:FLAG4:image1:ArrayData"
        pv = "HALF:IN:FLAG:"+self.tmppv+":image1:ArrayData"
        self.expoTimePV = "HALF:IN:FLAG:"+self.tmppv+":cam1:AcquireTime" 

        if len(sys.argv) == 1:
            print("Error, usage: python main.py real")
            sys.exit(0)

        if sys.argv[1] == "real":
            self.pv = pv
            self.pixel=st.flag_pixel

            expoTime = caget(self.expoTimePV)
            self.lineEdit.setText(str(expoTime))

        elif sys.argv[1] == "vm":
            self.pv = pv+":vm"
            self.pixel=st.flag_pixel_vm


        else:
            print("Error, usage: python main.py real")
            sys.exit(0)

    def init_sigxy_pv(self):
        # start IOC
        # pv = epics.PV("HALF:IN:FLAG:PRF07:sigx")
        # tmp = pv.get(timeout=0.5)

        # if tmp==None:
        #     Popen("softIoc -d sigxy.db",cwd=".",shell=True)

        #self.sigPV=["IRFEL:BD:flag4:sigx", "IRFEL:BD:flag4:sigy"]
        self.sigPV=["HALF:IN:FLAG:"+self.tmppv+":sigx", "HALF:IN:FLAG:"+self.tmppv+":sigy"]


    def start1_btn(self):
        
        if not self.is_timer_running:  
            freq = round(float(self.lineEdit_9.text())) * 1000  
            self.timer.start(freq)  
            self.is_timer_running = True  
            if freq == 1000:  # 只有在默认设置时才更新输入框  
                self.lineEdit_9.setText("1")  
            self.pushButton.setEnabled(False)
            self.pushButton_2.setEnabled(True)
        
        if self.lineEdit_9.text() != '':
            freq = round(float(self.lineEdit_9.text()))*1000
        else:
            freq = 1000 # 1s
            self.lineEdit_9.setText("1")
        self.timer.start(freq) #every ? ms

    def stop1_btn(self):
        if self.is_timer_running:  
            self.timer.stop()  
            self.is_timer_running = False   
            self.pushButton.setEnabled(True) 
            self.pushButton_2.setEnabled(False)    

    def change_interval(self):  
        # 停止并重新启动定时器以更改时间间隔  
        self.timer.stop()  
        self.interval = round(float(self.lineEdit_9.text()))*1000
        self.timer.start(self.interval) 

    def setExpoTime(self):
        if sys.argv[1] == "real":
            expoTime = float(self.lineEdit.text())
            caput(self.expoTimePV,expoTime)

        elif sys.argv[1] == "vm":
            self.lineEdit.setText("VM,NO expoTime")


    def plot_beamprofile(self):
        #determine the PV name
        self.tmppv = self.flag_selec.currentText() 
        self.init_realOrVM()
        self.init_sigxy_pv()

        # start_time = time.time()
        tmppv1 = PV(self.pv) # pv of flag
        tmp = tmppv1.get()

        data_ini = list(map(float, tmp))

        #data_ini = np.loadtxt("./0323_flag4.txt")
        data = np.reshape(data_ini,(self.pixel[1],self.pixel[0])) 

        if self.h != None: 
            self.xlim = self.h.axes.get_xlim()
            self.ylim = self.h.axes.get_ylim()
        
        if self.colorbar != None:
            self.colorbar.remove()
            
        self.widget.axes.clear()

        if self.lineEdit_4.text() != '':
            vmin = self.lineEdit_4.text()
        else:
            vmin = str(np.min(data))
            self.lineEdit_4.setText(vmin)

        if self.lineEdit_3.text() != '':
            vmax = self.lineEdit_3.text()
        else:
            vmax = str(np.max(data))
            self.lineEdit_3.setText(vmax)

        vnorm = mpl.colors.Normalize(vmin=vmin, vmax=vmax) 
        colormap = self.comboBox_2.currentText()


        self.h = self.widget.axes.imshow(data,cmap=colormap,norm=vnorm,origin="lower",extent=self.extent,aspect="auto")
        self.colorbar = self.widget.fig.colorbar(self.h)

        self.widget.axes.set_xlabel("x (mm)")
        self.widget.axes.set_ylabel("y (mm)")
        self.widget.axes.set_xlim(self.xlim)
        self.widget.axes.set_ylim(self.ylim)

        #  density stat 
        #------------------------
        height = abs(self.ylim[1]-self.ylim[0])
        width  = abs(self.xlim[1]-self.xlim[0])
        
        # sample out only the selected region data
        x = np.linspace(self.extent[0],self.extent[1],self.pixel[0])
        y = np.linspace(self.extent[2],self.extent[3],self.pixel[1])
        
        idx = np.logical_and(x>self.xlim[0], x<self.xlim[1])
        idy = np.logical_and(y>self.ylim[0], y<self.ylim[1])
 
    
        x = x[idx] #numpy布尔索引
        y = y[idy]
        data = data[idy,:][:,idx]     
        
        denx0 = np.sum(data,axis=0) #-2e4
        deny0 = np.sum(data,axis=1) #-6e4

        # add density profile line
        #------------------------
        denx = denx0/np.max(denx0) *height *0.3  +self.ylim[0]*0.98
        deny = deny0/np.max(deny0) *width  *0.3  +self.xlim[0]*0.98
        self.widget.axes.plot(x,denx,'--c')
        self.widget.axes.plot(deny,y,'--c')

        # add gauss-fitting lines
        #------------------------
        def Gauss(x,a,x0,sigma,c):    
            y = a*np.exp(-(x-x0)**2/(2*sigma**2))+c
            return y
        
        try:
            norm_denx = denx0/np.max(denx0)
            norm_deny = deny0/np.max(deny0)

            max_den = np.max(norm_denx)  # 最大值
            max_index = np.argmax(norm_denx)  # 最大值对应的索引
            x0_initial = x[max_index]  # 对应的x坐标作为x0初始值
            initial_guess = [max_den, x0_initial, 1.0, np.min(norm_denx)] 
            popt,pcov = curve_fit(Gauss, x, norm_denx, p0=initial_guess) 

            fit_denx = Gauss(x,popt[0],popt[1],popt[2],popt[3]) *height*0.3 +self.ylim[0]*0.98
            self.widget.axes.plot(x,fit_denx,'--r')
            #export the sigx to gui
            self.sigx =abs(round(popt[2],3)) 
            self.lineEdit_5.setText(str(abs(round(popt[2],3))))

            max_den = np.max(norm_deny)  # 最大值
            max_index = np.argmax(norm_deny)  # 最大值对应的索引
            y0_initial = y[max_index]  # 对应的x坐标作为x0初始值

            initial_guess = [max_den, y0_initial, 1.0, np.min(norm_deny)] # 提高拟合精度
            popt,pcov = curve_fit(Gauss,y,norm_deny, p0=initial_guess)  

            fit_deny = Gauss(y,popt[0],popt[1],popt[2],popt[3]) *width*0.3 +self.xlim[0]*0.98
            self.widget.axes.plot(fit_deny,y,'--r',label="fitting curve")

            self.widget.axes.legend()
            #export the sigy to gui
            self.sigy =abs(round(popt[2],3)) 
            self.lineEdit_6.setText(str(abs(round(popt[2],3))))

            #print(self.sigx,self.sigy)
        except:
            print("Warning: please move the origin point to (0,0) for gauss fitting!")

        # x    = y
        # denx = deny
        # fit_denx = fit_deny
        # plt.figure()
        # plt.plot(x,denx,'-g')
        # plt.plot(x,fit_denx,'--r')
        # plt.show()
        
        # add grid
        self.widget.axes.grid(alpha=0.8,linestyle='--',color='b')
        self.widget.canvas.draw()


        # broadcast sigx,sigy to IRFEL:BD:flag4:sigx / sigy
        # =====================================================
        caput_many(self.sigPV,[self.sigx,self.sigy])
        end_time = time.time()
        #print(f"执行时间: {end_time - start_time} 秒")


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = myWindow()
    window.show()
    sys.exit(app.exec_())
    
    # window.plot_beamprofile()
    
    





