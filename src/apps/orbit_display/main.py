import time
import sys
from subprocess import Popen
import numpy as np

from gui import Ui_MainWindow 
from PyQt5.QtWidgets import QMainWindow, QApplication, QLabel, QHBoxLayout
from PyQt5.QtCore import QTimer

from epics import caget, caget_many

import half_linac.setup as st

class myWindow(QMainWindow, Ui_MainWindow):
    def __init__(self):
        super().__init__()
        self.setupUi(self)


        # init pv
        self.init_pv()

        self.cxmin = None
        self.cxmax = None
        self.cymin = None
        self.cymax = None

        self.BPMxstart = None
        self.BPMxend   = None
        self.BPMystart = None
        self.BPMyend   = None

        self.start_1.clicked.connect(self.start1_btn)
        self.stop_1.clicked.connect(self.stop1_btn)

        self.start_2.clicked.connect(self.start2_btn)
        self.stop_2.clicked.connect(self.stop2_btn)

        # self.detail.clicked.connect(self.start_bpmvalue_btn)

    def init_pv(self):
        pvlx = []
        pvly = []
        for j in range(43):
            if j+1 < 10:
                pvx = "HALF:IN:BPM:BPM0"+str(j+1)+":X:ao"
                pvy = "HALF:IN:BPM:BPM0"+str(j+1)+":Y:ao"
            else:
                pvx = "HALF:IN:BPM:BPM"+str(j+1)+":X:ao"
                pvy = "HALF:IN:BPM:BPM"+str(j+1)+":Y:ao"
            #print(pvx,pvlx)
            pvlx.append(pvx)
            pvly.append(pvy)

        # get the values
        self.pvlx_val = caget_many(pvlx) 
        self.pvly_val = caget_many(pvly) 

    def start1_btn(self):
        self.timer_1 = QTimer(self)
        self.timer_1.timeout.connect(self.plotorbit_x)
        self.timer_1.start(1000) #every 1s
    def stop1_btn(self):
        self.timer_1.stop()

    def start2_btn(self):
        self.timer_2 = QTimer(self)
        self.timer_2.timeout.connect(self.plotorbit_y)
        self.timer_2.start(1000) #every 1s
    def stop2_btn(self):
        self.timer_2.stop()

    # def plotorbit_x(self):
    #     self.init_pv()
    #     pvl_val = [num*1000 for num in self.pvlx_val]#mm

    #     if self.hold_1.isChecked()==True:
    #         pass
    #     else:
    #         self.graphWidget_1.canvas.axes.clear()

    #     def setcxmin():
    #         try:
    #             self.cxmin = float(self.QL_cxmin.text())
    #         except:
    #             pass
    #     def setcxmax():
    #         try:
    #             self.cxmax = float(self.QL_cxmax.text())
    #         except:
    #             pass

    #     def setBPMxstart():
    #         try:
    #             self.BPMxstart = int(self.bPMSLineEdit.text())
    #         except:
    #             pass
    #     def setBPMxend():
    #         try:
    #             self.BPMxend = int(self.bPMELineEdit.text())
    #         except:
    #             pass

    #     self.QL_cxmin.returnPressed.connect(setcxmin)
    #     self.QL_cxmax.returnPressed.connect(setcxmax)
    #     if self.cxmin != None:
    #         self.graphWidget_1.canvas.axes.set_ylim(bottom=self.cxmin)
    #     if self.cxmax != None:
    #         self.graphWidget_1.canvas.axes.set_ylim(top=self.cxmax)

    #     self.bPMSLineEdit.returnPressed.connect(setBPMxstart)
    #     self.bPMELineEdit.returnPressed.connect(setBPMxend)
    #     if self.BPMxstart != None:
    #         self.graphWidget_1.canvas.axes.set_xlim(left=self.BPMxstart)
    #     if self.BPMxend != None:
    #         self.graphWidget_1.canvas.axes.set_xlim(right=self.BPMxend)

    #     x = np.linspace(1,len(pvl_val),len(pvl_val))
    #     self.graphWidget_1.canvas.axes.plot(x, pvl_val,'-o')
    #     self.graphWidget_1.canvas.axes.set_xlabel("BPM #")
    #     self.graphWidget_1.canvas.axes.set_ylabel("Cx (mm)")
    #     self.graphWidget_1.canvas.draw()

    # def plotorbit_y(self):
    #     self.init_pv()
    #     pvl_val = [num*1000 for num in self.pvly_val]#mm

    #     if self.hold_2.isChecked() == True:
    #         pass
    #     else:
    #         self.graphWidget_2.canvas.axes.clear()

    #     def setcymin():
    #         try:
    #             self.cymin = float(self.QL_cymin.text())
    #         except:
    #             pass
    #     def setcymax():
    #         try:
    #             self.cymax = float(self.QL_cymax.text())
    #         except:
    #             pass

    #     def setBPMystart():
    #         try:
    #             self.BPMystart = int(self.bPMSLineEdit_2.text())
    #         except:
    #             pass
    #     def setBPMyend():
    #         try:
    #             self.BPMyend = int(self.bPMYLineEdit.text())
    #         except:
    #             pass
                
    #     self.QL_cymin.returnPressed.connect(setcymin)
    #     self.QL_cymax.returnPressed.connect(setcymax)
    #     if self.cxmin != None:
    #         self.graphWidget_2.canvas.axes.set_ylim(bottom=self.cymin)
    #     if self.cxmax != None:
    #         self.graphWidget_2.canvas.axes.set_ylim(top=self.cymax)

    #     self.bPMSLineEdit_2.returnPressed.connect(setBPMystart)
    #     self.bPMYLineEdit.returnPressed.connect(setBPMyend)
    #     if self.BPMystart != None:
    #         self.graphWidget_2.canvas.axes.set_xlim(left=self.BPMystart)
    #     if self.BPMyend != None:
    #         self.graphWidget_2.canvas.axes.set_xlim(right=self.BPMyend)        

    #     x = np.linspace(1,len(pvl_val),len(pvl_val))
    #     self.graphWidget_2.canvas.axes.plot(x, pvl_val, '-o')
    #     self.graphWidget_2.canvas.axes.set_xlabel("BPM #")
    #     self.graphWidget_2.canvas.axes.set_ylabel("Cy (mm)")
    #     self.graphWidget_2.canvas.draw()

    def plotorbit_x(self):
        self.init_pv()
        pvl_val = [num*1000 for num in self.pvlx_val] # mm

        # 1. 设置全局绘图风格（建议放在 __init__ 中，也可以放这里）
        ax = self.graphWidget_1.canvas.axes
        fig = self.graphWidget_1.canvas.figure
        
        # 设置背景颜色
        fig.patch.set_facecolor('#1e1e2e') # 整个画布背景
        # ax.set_facecolor('#000000')       # 绘图区背景设为全黑，突出曲线
        ax.set_facecolor('#1e1e2e') 

        if self.hold_1.isChecked():
            pass
        else:
            ax.clear()

        # --- 范围设置逻辑 ---
        # 注意：建议将 returnPressed.connect 移到 __init__ 中，
        # 这里仅保留 set_ylim 和 set_xlim 逻辑，防止重复连接信号导致卡顿
        try:
            if self.QL_cxmin.text():
                self.cxmin = float(self.QL_cxmin.text())
                ax.set_ylim(bottom=self.cxmin)
            if self.QL_cxmax.text():
                self.cxmax = float(self.QL_cxmax.text())
                ax.set_ylim(top=self.cymax)
            
            if self.bPMSLineEdit.text():
                self.BPMxstart = int(self.bPMSLineEdit.text())
                ax.set_xlim(left=self.BPMxstart)
            if self.bPMELineEdit.text():
                self.BPMxend = int(self.bPMELineEdit.text())
                ax.set_xlim(right=self.BPMxend)
        except ValueError:
            pass # 忽略格式错误的输入

        # 2. 优化坐标轴和网格颜色
        ax.tick_params(colors='#cdd6f4', which='both') # 刻度线颜色
        ax.xaxis.label.set_color('#cdd6f4')            # X轴标签颜色
        ax.yaxis.label.set_color('#cdd6f4')            # Y轴标签颜色
        
        for spine in ax.spines.values():
            spine.set_edgecolor('#45475a')             # 坐标轴边框颜色

        # 添加深色网格
        ax.grid(True, color='#313244', linestyle='--', linewidth=0.5)

        # 3. 绘图：使用高亮度色彩
        x = np.linspace(1, len(pvl_val), len(pvl_val))
        # 使用电光蓝 (#89b4fa)，点用白色边框包裹
        # ax.plot(x, pvl_val, '-o', color='#89b4fa', 
        #         markerfacecolor='#89b4fa', markeredgecolor='white', 
        #         markersize=4, linewidth=1.5, label="Horizontal Orbit")
        ax.plot(x, pvl_val,'-o')
        ax.set_xlabel("BPM #", fontweight='bold')
        ax.set_ylabel("Cx (mm)", fontweight='bold')
        
        # 刷新画布
        # fig.tight_layout()
        self.graphWidget_1.canvas.draw()

    def plotorbit_y(self):
            self.init_pv()
            pvl_val = [num*1000 for num in self.pvly_val]  # mm

            # 获取 axes 和 figure 句柄
            ax = self.graphWidget_2.canvas.axes
            fig = self.graphWidget_2.canvas.figure

            # 1. 设置深色配色基调
            fig.patch.set_facecolor('#1e1e2e')  # 画布背景（与主界面一致）
            ax.set_facecolor('#1e1e2e')        # 绘图区背景（全黑，对比度最高）

            if self.hold_2.isChecked():
                pass
            else:
                ax.clear()

            # 2. 坐标轴与文字颜色设置 (提升科技感)
            color_text = '#cdd6f4'  # 浅蓝色文字
            color_grid = '#313244'  # 深灰色网格
            
            ax.tick_params(colors=color_text, which='both', labelsize=9)
            ax.xaxis.label.set_color(color_text)
            ax.yaxis.label.set_color(color_text)
            
            # 隐藏上方和右方的边框线，或改变其颜色
            for spine in ax.spines.values():
                spine.set_edgecolor('#45475a')

            # 添加网格线
            ax.grid(True, color=color_grid, linestyle=':', linewidth=0.5)

            # --- 范围设置逻辑 ---
            # 注意：建议将 returnPressed.connect 移到 __init__ 中，
            # 这里仅保留 set_ylim 和 set_xlim 逻辑，防止重复连接信号导致卡顿
            try:
                if self.QL_cymin.text():
                    self.cymin = float(self.QL_cymin.text())
                    ax.set_ylim(bottom=self.cymin)
                if self.QL_cymax.text():
                    self.cymax = float(self.QL_cymax.text())
                    ax.set_ylim(top=self.cymax)
                
                if self.bPMSLineEdit_2.text():
                    self.BPMystart = int(self.bPMSLineEdit_2.text())
                    ax.set_xlim(left=self.BPMystart)
                if self.bPMYLineEdit.text():
                    self.BPMyend = int(self.bPMYLineEdit.text())
                    ax.set_xlim(right=self.BPMyend)
            except ValueError:
                pass # 忽略格式错误的输入

            # 3. 绘图：使用荧光黄 (#f9e2af) 区分 Y 轨道
            x = np.linspace(1, len(pvl_val), len(pvl_val))
            # ax.plot(x, pvl_val, '-o', 
            #         color='#f9e2af',           # 曲线颜色：荧光黄
            #         markerfacecolor='#f9e2af', # 点填充色
            #         markeredgecolor='white',   # 点边缘色（增加亮点效果）
            #         markersize=4, 
            #         linewidth=1.5,
            #         label="Vertical Orbit")
            ax.plot(x, pvl_val, '-o')
            ax.set_xlabel("BPM #", fontweight='bold')
            ax.set_ylabel("Cy (mm)", fontweight='bold')
            
            # 紧凑布局并刷新
            # fig.tight_layout()
            self.graphWidget_2.canvas.draw()

    def start_bpmvalue_btn(self):
        Popen("python3 submain.py",cwd=st.rootpath+"/src/apps/orbit_display",shell=True) 


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = myWindow()
    window.show()
    sys.exit(app.exec_())





