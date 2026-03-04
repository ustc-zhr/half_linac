
import sys
import time
import numpy as np
import os
import sdds
import math
import json

from subprocess import Popen
import matplotlib as mpl
import matplotlib.pyplot as plt
from scipy.interpolate import UnivariateSpline
from scipy.optimize import curve_fit
from epics import caget, caget_many, caput, caput_many, PV

from PyQt5.QtWidgets import QMainWindow, QApplication, QLabel, QHBoxLayout, QWidget, QFileDialog
from PyQt5.QtCore import QTimer
from PyQt5.QtCore import QThread

from gui import Ui_MainWindow
import half_linac.setup as st
from half_linac.src.apps.energy_spectrum.get_energy0 import get_energy0
from half_linac.src.apps.energy_spectrum.esa_auto_tuner import ESA_AutoTuner

from half_linac.src.virtual_machine.half_elegant.elegant_parser import elegant_parser

# 会使用到VM计算η和twiss (不具有一般性)

class EnergySpectrumApp(QMainWindow,Ui_MainWindow):
    """
    a gui window for energ spectrum analysis
    """

    def __init__(self):
        super().__init__()
        self.setupUi(self)

        # machine_type
        self.machine_type = st.machine_type

        # initialize flag PV according to real machine or VM
        self.init_ESAflag()

        # refresh plot with timer (the default frequency: 1Hz)
        self.setup_timer()
        
        #function: fig setup
        self.lineEdit_expotime.returnPressed.connect(self.set_expotime)
        self.lineEdit_refresh.returnPressed.connect(self.set_refresh)

        #function: eta and twiss calculation 
        self.checkBox_emit.clicked.connect(lambda: self.emit_withornot(self.checkBox_emit.isChecked()))
        self.pushButton_cal_disp.clicked.connect(self.cal_disp)
        self.pushButton_cal_twiss_disp.clicked.connect(self.cal_twiss_disp)

        #function: background image
        self.pushButton_sapmles.clicked.connect(self.background_samples)
        self.pushButton_save.clicked.connect(self.save_bgfile)
        self.pushButton_load.clicked.connect(self.load_bgfile)
        self.checkBox_bg.clicked.connect(lambda: self.bg_removeornot(self.checkBox_bg.isChecked()))

        #function: tune energy
        self.slider_energy.valueChanged.connect(self.set_bend_quad)
        self.pushButton_autoFind.clicked.connect(self.run_esa_auto_tune)

        
        # 
        self.colorbar = None
        self.sigx = None
        self.sigy = None
        
        # ESA 入口处束团参数
        self.with_emit = False # 默认不考虑发射度
        self.remove_bg = False # 默认不去背景

        self.beta_flag = 0
        self.emi_flag = 0
        self.eta_flag = 0
        # 
        self.cal_disp()
        self.fit_method = "direct"



    def background_samples(self):
        """sample background image and subtract later"""
        n_samples = int(self.lineEdit_samples.text())
        print(f"sampling {n_samples} background images...")
        bg_images = []
        for i in range(n_samples):
            time.sleep(1) # wait for PV update
            tmp = self.flag_pv_obj.get()
            data_ini = list(map(float, tmp))
            data = np.reshape(data_ini,(self.flag_pixel[1],self.flag_pixel[0])) # 注意shape顺序，先y后x
            bg_images.append(data)
        self.bg_image = np.mean(bg_images, axis=0)
        print("background sampling done.")

        colormap = self.comboBox_colormap.currentText() 

        self.background_plot.axes.clear()
        self.background_plot.axes.imshow(self.bg_image,cmap=colormap,origin="lower",extent=self.extent,aspect="auto")
        self.background_plot.axes.set_xlabel("x (mm)")
        self.background_plot.axes.set_ylabel("y (mm)")
        self.background_plot.axes.set_xlim(self.xlim)
        self.background_plot.axes.set_ylim(self.ylim)

        self.background_plot.canvas.draw()
    
    def save_bgfile(self):
        """save the background image to a file"""
        options = QFileDialog.Options()
        filePath, _ = QFileDialog.getSaveFileName(self,"Save Background Image","","NumPy Files (*.npy);;All Files (*)", options=options)
        if self.bg_image is None:
            print("No background image to save!")
            return
        if filePath:
            np.save(filePath, self.bg_image)
            print(f"background image saved to {filePath}")
    
    def load_bgfile(self):
        """load the background image from a file"""
        options = QFileDialog.Options()
        filePath, _ = QFileDialog.getOpenFileName(self,"Load Background Image","","NumPy Files (*.npy);;All Files (*)", options=options)
        if filePath:
            self.bg_image = np.load(filePath)
            print(f"background image loaded from {filePath}")

            self.background_plot.axes.clear()
            colormap = self.comboBox_colormap.currentText() 
            self.background_plot.axes.imshow(self.bg_image,cmap=colormap,origin="lower",extent=self.extent,aspect="auto")
            self.background_plot.axes.set_xlabel("x (mm)")
            self.background_plot.axes.set_ylabel("y (mm)")
            self.background_plot.axes.set_xlim(self.xlim)
            self.background_plot.axes.set_ylim(self.ylim)

            self.background_plot.canvas.draw()

    def bg_removeornot(self, state):
        """decide whether to remove background or not"""
        if state:
            self.remove_bg = True
            print("background removal is ON")
        else:
            self.remove_bg = False
            print("background removal is OFF")


    def init_ESAflag(self):
        """init the flag PV and pixel size according to real machine or VM"""
        if self.machine_type == "real":
            self.flag_pv = st.ESAflag_pv
            self.flag_pixel = st.ESAflag_pixel
            self.flag_expotime_pv = st.ESAflag_expotime_pv

            flag_pixel_width=st.ESAflag_pixel_width

            expotime = caget(self.flag_expotime_pv)
            self.lineEdit_expotime.setText(str(expotime))

        elif self.machine_type == "vm":
            self.flag_pv = st.ESAflag_pv_vm
            self.flag_pixel = st.ESAflag_pixel_vm

            flag_pixel_width=st.ESAflag_pixel_width_vm

        self.flag_pv_obj = PV(self.flag_pv)

        self.width  = self.flag_pixel[0]*flag_pixel_width # mm
        self.height = self.flag_pixel[1]*flag_pixel_width # mm
        self.xlim = (-0.5*self.width , 0.5*self.width  )
        self.ylim = (-0.5*self.height, 0.5*self.height ) 
        self.extent = self.xlim +self.ylim
  
    def setup_timer(self):
        # refreah the figure at 1 Hz
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.ESA_running)
        self.is_timer_running = True  # 定时器状态
        self.timer.start(1000)# 每过1s timer.timeout触发一次

    def set_refresh(self):  
        # 停止并重新启动定时器以更改时间间隔  
        self.timer.stop()  
        interval = round(float(self.lineEdit_refresh.text()))*1000
        self.timer.start(interval) 

    def set_expotime(self):
        if sys.argv[1] == "real":
            expoTime = float(self.lineEdit_expotime.text())
            caput(self.expoTimePV,expoTime)

        elif sys.argv[1] == "vm":
            self.lineEdit_expotime.setText("VM,NO expoTime")


    def ESA_running(self):
        # clear previous image
        self.ESAflag_image.axes.clear()
        # get colormap
        colormap = self.comboBox_colormap.currentText()  
        fit_method = self.comboBox_fitmethod.currentText()  
        # get flag image data from PV
        tmp = self.flag_pv_obj.get()
        data_ini = list(map(float, tmp))
        data = np.reshape(data_ini,(self.flag_pixel[1],self.flag_pixel[0])) # 注意shape顺序，先y后x
        # subtract background if needed
        if self.remove_bg == True:
            data = data - self.bg_image
            data[data<0] = 0  # 防止负值出现

        # plot the image
        self.ESAflag_image.axes.imshow(data,cmap=colormap,origin="lower",extent=self.extent,aspect="auto")

        self.ESAflag_image.axes.set_xlabel("x (mm)")
        self.ESAflag_image.axes.set_ylabel("y (mm)")
        self.ESAflag_image.axes.set_xlim(self.xlim)
        self.ESAflag_image.axes.set_ylim(self.ylim)

        #  density stat 
        #------------------------

        # sample out only the selected region data
        x = np.linspace(self.extent[0],self.extent[1],self.flag_pixel[0])
        y = np.linspace(self.extent[2],self.extent[3],self.flag_pixel[1])
        idx = np.logical_and(x>self.xlim[0], x<self.xlim[1])
        idy = np.logical_and(y>self.ylim[0], y<self.ylim[1])
        x = x[idx] #numpy布尔索引
        y = y[idy]
        data = data[idy,:][:,idx]    
        
        # projection density
        denx0 = np.sum(data,axis=0) #-2e4
        deny0 = np.sum(data,axis=1) #-6e4

        # add density profile line
        #-------------------------
        norm_denx = denx0/np.max(denx0)
        norm_deny = deny0/np.max(deny0)
        denx = norm_denx *self.height *0.3  +self.ylim[0]*0.98
        deny = norm_deny *self.width  *0.3  +self.xlim[0]*0.98
        self.ESAflag_image.axes.plot(x,denx,'--c',label="origin projection")
        # self.ESAflag_image.axes.plot(deny,y,'--c')

        
        
        # add gauss-fitting lines
        #------------------------
        def Gauss_func(x,a,x0,sigma):    
                return a*np.exp(-(x-x0)**2/(2*sigma**2))
        def gauss_fit(x,amp):
            max_amp = np.max(amp)  # 最大值
            max_index = np.argmax(amp)  # 最大值对应的索引
            x0_initial = x[max_index]  # 对应的x坐标作为x0初始值
            initial_guess = [max_amp, x0_initial, 1.0] # 对应高斯函数参数 [A, μ, σ, C] 的初始值
            popt,pcov = curve_fit(Gauss_func, x, amp, p0=initial_guess) 
            return popt
        if fit_method == "Gauss":
            # x方向的高斯拟合
            popt = gauss_fit(x, norm_denx)
            # print(popt)
            fit_norm_denx = Gauss_func(x,popt[0],popt[1],popt[2])
            fit_denx = fit_norm_denx *self.height*0.3 +self.ylim[0]*0.98
            self.ESAflag_image.axes.plot(x,fit_denx,'--r')
            self.meanx =abs(round(popt[1],3)) 
            self.sigx =abs(round(popt[2],3)) 
            # print(self.sigx)
            # self.lineEdit_5.setText(str(abs(round(popt[2],3))))

            # y方向的高斯拟合
            # popt = gauss_fit(y, norm_deny)
            # fit_norm_deny = Gauss_func(y,popt[0],popt[1],popt[2])
            # fit_deny = fit_norm_deny *self.width*0.3 +self.xlim[0]*0.98
            # self.ESAflag_image.axes.plot(fit_deny,y,'--r',label="Gauss fit")

        # 不拟合直接计算投影分布的方差
        elif fit_method == "direct":
            # 直接计算投影分布的方差
            total_denx = np.sum(denx0)
            probabilities = denx0 / total_denx
            mean_direct = np.sum(x * probabilities)
            variance_direct = np.sum(probabilities * (x - mean_direct)**2)
            std_direct = np.sqrt(variance_direct)
            # gauss_direct = Gauss_func(x, np.max(norm_denx), mean_direct, std_direct)
            # fit_denx_direct = gauss_direct * self.height * 0.3 + self.ylim[0] * 0.98
            # self.ESAflag_image.axes.plot(x, fit_denx_direct, '--g', label="direct")
            self.meanx = mean_direct
            self.sigx = std_direct

            # 使用样条插值
            try:
                spline = UnivariateSpline(x, norm_denx, s=0.1)  # s是平滑参数
                # x_dense = np.linspace(x[0], x[-1], 200)  # 更密集的点
                fit_norm_denx = spline(x)
                fit_denx = fit_norm_denx * self.height * 0.3 + self.ylim[0] * 0.98
                self.ESAflag_image.axes.plot(x, fit_denx, '--r', label="spline fit", alpha=0.7)
            except:
                print("样条拟合失败")
        self.ESAflag_image.axes.grid(alpha=0.8,linestyle='--',color='b')
        self.ESAflag_image.canvas.draw()


        # -----------------
        # energy0 calculation (coresponding to the x=0)
        if self.machine_type == "vm":
            energy0 = 2200 # MeV  
        else:
            # 1. 若提供了相关的能量物理量在ioc中 可以直接caget获取
            # 2. 根据磁铁(电流)强度给出energy0
            # current_ES_Bend = caget("HALF:IN:ESA:PRF01:CurrentSet") # A
            # current_ES_Bend = 100
            # energy0 = get_energy0(current_ES_Bend) # MeV
            pass

        # dispersion calculation and display 
        # self.cal_disp()

        # energy_center and energy_spread calculation and display 
        energy_center = energy0 * (self.meanx - 0)*1e-3 / self.eta_flag + energy0 # MeV

        if self.with_emit == True: # 不考虑发射度贡献
            energy_spread = math.sqrt(((self.sigx*1e-3)**2 - self.beta_flag * self.emi_flag) / self.eta_flag ** 2) * energy0 / energy_center
        elif self.with_emit == False: # 考虑发射度贡献 
            energy_spread = math.sqrt(((self.sigx*1e-3)**2 - 0 * 0) / self.eta_flag ** 2) * energy0 / energy_center
        
        self.label_energy.setText("{:.4f}".format(energy_center)) # MeV
        self.label_energyspread.setText("{:.4f}".format(energy_spread*1e2)) # %

        # plot energy profile in another figure
        enregy_all = [energy0 * (xi - 0)*1e-3 / self.eta_flag + energy0 for xi in x]
        self.energy_plot.axes.clear()
        self.energy_plot.axes.plot(enregy_all,norm_denx,'--c',label="origin projection")
        if fit_method == "direct":
            self.energy_plot.axes.plot(enregy_all,fit_norm_denx,'--r',label="spline fit")
        elif fit_method == "Gauss":
            self.energy_plot.axes.plot(enregy_all,fit_norm_denx,'--r',label="Gauss fit")
        self.energy_plot.axes.set_xlabel("E (MeV)")
        self.energy_plot.axes.set_ylabel("Spectrum (arb. units)")
        self.energy_plot.axes.legend()
        self.energy_plot.axes.grid(alpha=0.5,linestyle='--')  # 可选网格
        self.energy_plot.canvas.draw()  # 强制刷新

    def cal_disp(self):
        try:
            # 根据ESA的弯铁SM(L, angle)和Q铁QE01 QE02 QE03(k,L) 漂移段(L)参数计算eta    变量仅为Q_k
            # 采用elegant计算

            # 获取当前ESA三块Q铁强度 这里假设获得的是强度k
            QE01_k = caget(st.pv_prefix_quad + "QE01" + st.pv_suffix_quad)
            QE02_k = caget(st.pv_prefix_quad + "QE02" + st.pv_suffix_quad)
            QE03_k = caget(st.pv_prefix_quad + "QE03" + st.pv_suffix_quad)

            #
            lattice_file = st.rootpath+'/src/virtual_machine/half_elegant/elegant/lattice_ini.lte'
            esa_ini_ele_file    = st.rootpath+'/src/virtual_machine/half_elegant/elegant/esa_ini.ele' 
            line_name    = 'ESAlocal' #'use_beamline'

            esajson_path        = st.rootpath+"/src/virtual_machine/half_elegant/esa.json"

            esa_lte_file        = st.rootpath+'/src/virtual_machine/half_elegant/elegant/esa.lte'
            esa_ele_file        = st.rootpath+'/src/virtual_machine/half_elegant/elegant/esa.ele'  
            

            lte1 = elegant_parser(lattice_file, esa_ini_ele_file, line_name)
            lte1.dump2json(esajson_path)
            with open(esajson_path,"r") as f:
                lte = json.load(f)
            contl = lte["control"]
            lattice  = lte["lattice"]

            contl['run_setup']['lattice'] = 'esa.lte'
            lattice['QE01']['K1'] = str(QE01_k)
            lattice['QE02']['K1'] = str(QE02_k)
            lattice['QE03']['K1'] = str(QE03_k)

            lte["control"]  = contl
            lte["lattice"]  = lattice

            with open(esajson_path,"w") as f:
                f.write(json.dumps(lte,indent=4))
            
            lte1.json2lte_ele(esa_lte_file,esa_ele_file,esajson_path)   

            # run elegant 
            # ==========================
            cupath = os.getcwd()
            os.chdir(st.rootpath+"/src/virtual_machine/half_elegant/elegant")
            os.system("elegant esa.ele > esa.log")
            os.chdir(cupath)
            time.sleep(1)
            
            tmp = sdds.SDDS(0)
            tmp.load(st.rootpath+'/src/virtual_machine/half_elegant/elegant/esa.mat')
            list_R = [tmp.columnData[i][0][0] for i in range(12, 48)]
            Rj = np.array(list_R).reshape(6,6)

            self.eta_flag = Rj[0, -1] 
            print('dispersion of ESA updates: ',self.eta_flag, 'm')
        
        except Exception as e:
            print(f"Error in cal_disp: {e}")
            self.eta_flag = 0.7484210850804714  # 理论设计值
            print('default dispersion: ',self.eta_flag, 'm')
            
        self.lineEdit_eta_ESAflag.setText(str(round(self.eta_flag,5)))

    def cal_twiss_disp(self):
        """calculate the twiss @ ESA flag according the twiss @ in"""
        # get twiss @ in
        alpha_in = self.doubleSpinBox_alpha_in.value() #    -16.2@QT02
        beta_in = self.doubleSpinBox_beta_in.value() # m     88.6@QT02
        emi_in = self.doubleSpinBox_emi_in.value()*1e-9 # m  ~43nm@QT02
        start_element = self.comboBox_start_element.currentText() 

        QE01_k = caget(st.pv_prefix_quad + "QE01" + st.pv_suffix_quad)
        QE02_k = caget(st.pv_prefix_quad + "QE02" + st.pv_suffix_quad)
        QE03_k = caget(st.pv_prefix_quad + "QE03" + st.pv_suffix_quad)

        if beta_in <= 0:
            print("wrong beta in")
            return

        # run ESA lattice 
        #
        lattice_file        = st.rootpath+'/src/virtual_machine/half_elegant/elegant/lattice_ini.lte'
        esa_ini_ele_file    = st.rootpath+'/src/virtual_machine/half_elegant/elegant/esa_ini.ele' 
        line_name           = 'ESA' #'use_beamline'

        esajson_path        = st.rootpath+"/src/virtual_machine/half_elegant/esa.json"

        esa_lte_file        = st.rootpath+'/src/virtual_machine/half_elegant/elegant/esa.lte'
        esa_ele_file        = st.rootpath+'/src/virtual_machine/half_elegant/elegant/esa.ele'

        lte1 = elegant_parser(lattice_file, esa_ini_ele_file, line_name)
        lte1.dump2json(esajson_path)
        with open(esajson_path,"r") as f:
            lte = json.load(f)
        contl = lte["control"]
        lattice  = lte["lattice"]
        usedline = lte["usedline"]

        lattice['QE01']['K1'] = str(QE01_k)
        lattice['QE02']['K1'] = str(QE02_k)
        lattice['QE03']['K1'] = str(QE03_k)

        contl['run_setup']['lattice'] = 'esa.lte'
        contl['twiss_output']['beta_x'] = str(beta_in)
        contl['twiss_output']['alpha_x'] = str(alpha_in)

        # map of entrance of elem1 => end
        id1 = usedline.index(start_element)
        scanline = usedline[id1:-1]

        # update json with new lte and new control
        lte["control"]  = contl
        lte["lattice"]  = lattice
        lte["usedline"] = scanline

        with open(esajson_path,"w") as f:
            f.write(json.dumps(lte,indent=4))
        
        lte1.json2lte_ele(esa_lte_file,esa_ele_file,esajson_path)   

        # run elegant 
        # ==========================
        cupath = os.getcwd()
        os.chdir(st.rootpath+"/src/virtual_machine/half_elegant/elegant")
        os.system("elegant esa.ele > esa.log")
        os.chdir(cupath)
        time.sleep(1)
        
        tmp = sdds.SDDS(0)
        tmp.load(st.rootpath+'/src/virtual_machine/half_elegant/elegant/esa.twi')
        betax   = tmp.columnData[1][0][-1]
        alphax  = tmp.columnData[2][0][-1]
        eta     = tmp.columnData[4][0][-1]

        # results
        self.alpha_flag = alphax
        self.beta_flag = betax
        self.emi_flag = emi_in # m

        self.eta_flag = eta # m.

        print('cal results: beta=',self.beta_flag, 'm, alpha=',self.alpha_flag, 'eta=',self.eta_flag, ' m')

        self.lineEdit_alpha_ESAflag.setText(str(round(self.alpha_flag,5)))
        self.lineEdit_beta_ESAflag.setText(str(round(self.beta_flag,5)))
        # self.lineEdit_emi_ESAflag.setText(str(self.emi_flag*1e9))
        self.lineEdit_eta_ESAflag.setText(str(round(self.eta_flag,5)))

    def emit_withornot(self, state):
        if state:
            self.with_emit = True
            
        if not state:
            self.with_emit = False


    def set_bend_quad(self):
        """
        update the energy0 value according to slider position
        这里energy0是由ESA的弯铁强度决定的
        """
        slider_value = self.slider_energy.value()
        self.label_sliderenergy.setText(str(slider_value))

        # # get current bend Q
        # BEND_energy = caget("HALF:IN:PRFESA:EnergySet") # MeV
        # QE01_k = caget(st.pv_prefix_quad + "QE01" + st.pv_suffix_quad)
        # QE02_k = caget(st.pv_prefix_quad + "QE02" + st.pv_suffix_quad)
        # QE03_k = caget(st.pv_prefix_quad + "QE03" + st.pv_suffix_quad)

        # # update bend Q

    def run_esa_auto_tune(self):
        # 暂停定时刷新，防止抢 PV
        self.timer.stop()

        best_I = ESA_AutoTuner(
            B_min=0,
            B_max=200,
            coarse_steps=40,
            fine_steps=15
        )

        if best_I is not None:
            print(f"[GUI] ESA auto-tuned to {best_I:.3f} A")

        # 恢复正常显示
        self.timer.start()
        # self.ESA_running()




if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = EnergySpectrumApp()
    window.show()
    sys.exit(app.exec_())
    
    
    





