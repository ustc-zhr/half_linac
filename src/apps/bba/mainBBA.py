import sys
from PyQt5.QtWidgets import QMainWindow, QApplication, QLabel, QHBoxLayout, QWidget
from PyQt5.QtCore import QThread, pyqtSignal, QTimer
import epics
import time
import numpy as np
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit
from collections import defaultdict
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass

from gui import Ui_Form
import half_linac.setup as st
from half_linac.virtual_machine.half_elegant.elegant_parser import elegant_parser


@dataclass
class ScanParameters:
    """存储扫描参数的类"""
    corr: str = ""
    quad: str = ""
    bpm1: str = ""
    bpm2: str = ""
    plane: str = "X"
    corrPV: str = ""
    quadPV: str = ""
    bpm1PV: str = ""
    bpm2PV: str = ""
    corr_from: float = 0.0
    corr_end: float = 0.0
    corr_steps: int = 0
    quad_from: float = 0.0
    quad_end: float = 0.0
    quad_steps: int = 0
    samples: int = 0
    sleeptime: float = 0.0
    recal: bool = False
    EnergyMeV: float = 0.0
    bpm1sampleNum: int = 0
    By: str = ""
    Bx: str = ""
    Leff_By: float = 0.0
    Leff_Bx: float = 0.0
    realorVM: str = ""

class BBAWindow(QWidget, Ui_Form):
    def __init__(self):
        super().__init__()
        self.setupUi(self)
        self.scan: Optional[QThread] = None
        self.clear: Optional[QThread] = None
        
        self._setup_default_values()
        self._connect_buttons()

    def _setup_default_values(self):
        """设置默认值"""
        # BBA-1 默认设置
        self.lineEdit_3.setText("5")  # corrector steps
        self.lineEdit_5.setText("5")  # quad steps
        self.lineEdit_7.setText("6")  # frequency time (s)
        self.lineEdit_8.setText("1")  # samples/step
        
        # BBA-1 调试值
        self.lineEdit_6.setText("45")
        self.lineEdit_4.setText("60")
        self.lineEdit.setText("-0.001")
        self.lineEdit_2.setText("0.001")

        # BBA-2 默认设置
        self.lineEdit_12.setText("5")  # corrector steps
        self.lineEdit_16.setText("5")  # quad steps
        self.lineEdit_15.setText("8")   # frequency time (s)
        self.lineEdit_9.setText("1")    # samples/step
        self.lineEdit_20.setText("2200")  # Energy (MeV)
        self.lineEdit_22.setText("1")   # BPM1 sample number

        # BBA-2 公式和参数
        self.lineEdit_23.setText("-0.4813*current-0.7747")
        self.lineEdit_25.setText("0.058287")
        self.lineEdit_24.setText("-0.4968*current-0.3153")
        self.lineEdit_26.setText("0.052513")
        
        # BBA-2 调试值
        self.lineEdit_14.setText("-5")
        self.lineEdit_17.setText("2")
        self.lineEdit_11.setText("-0.001")
        self.lineEdit_13.setText("0.001")

    def _connect_buttons(self):
        """连接按钮信号和槽函数"""
        # BBA-1 按钮
        self.pushButton.clicked.connect(self.start_scan)
        self.pushButton_2.clicked.connect(self.clear_plot)
        self.pushButton_3.clicked.connect(self.recalculate)
        self.pushButton_4.clicked.connect(self.stop_scan)

        # BBA-2 按钮
        self.pushButton_5.clicked.connect(self.start_scan_bba2)
        self.pushButton_6.clicked.connect(self.stop_scan)
        self.pushButton_8.clicked.connect(self.clear_plot_bba2)
        self.pushButton_7.clicked.connect(self.recalculate_bba2)

    def get_settings(self) -> ScanParameters:
        """获取BBA-1的扫描参数"""
        params = ScanParameters()
        
        params.corr = self.comboBox.currentText()
        params.quad = self.comboBox_2.currentText()
        params.bpm1 = self.comboBox_3.currentText()
        params.bpm2 = self.comboBox_4.currentText()

        params.corrPV = f"HALF:IN:COR:{params.corr}:ao"
        params.quadPV = f"HALF:IN:QUAD:{params.quad}:K1"
        
        plane = self.comboBox_5.currentText()
        params.plane = "X" if plane == "X-Plane" else "Y"
        params.bpm1PV = f"HALF:IN:BPM:{params.bpm1}:{params.plane}:ao"
        params.bpm2PV = f"HALF:IN:BPM:{params.bpm2}:{params.plane}:ao"

        params.corr_from = float(self.lineEdit.text())
        params.corr_end = float(self.lineEdit_2.text())
        params.corr_steps = int(self.lineEdit_3.text())
        params.quad_from = float(self.lineEdit_6.text())
        params.quad_end = float(self.lineEdit_4.text())
        params.quad_steps = int(self.lineEdit_5.text())

        params.samples = int(self.lineEdit_8.text())
        params.sleeptime = float(self.lineEdit_7.text())

        return params

    def get_settings_bba2(self) -> ScanParameters:
        """获取BBA-2的扫描参数"""
        params = ScanParameters()
        
        params.quad = self.comboBox_7.currentText()
        params.corr = self.comboBox_9.currentText()
        params.bpm1 = self.comboBox_8.currentText()
        params.bpm2 = self.comboBox_6.currentText()

        params.quadPV = f"HALF:IN:QUAD:{params.quad}:K1"
        params.corrPV = f"HALF:IN:COR:{params.corr}:ao"
        
        plane = self.comboBox_10.currentText()
        params.plane = "X" if plane == "X-Plane" else "Y"
        params.bpm1PV = f"HALF:IN:BPM:{params.bpm1}:{params.plane}:ao"
        params.bpm2PV = f"HALF:IN:BPM:{params.bpm2}:{params.plane}:ao"

        params.quad_from = float(self.lineEdit_14.text())
        params.quad_end = float(self.lineEdit_17.text())
        params.quad_steps = int(self.lineEdit_16.text())
        params.corr_from = float(self.lineEdit_11.text())
        params.corr_end = float(self.lineEdit_13.text())
        params.corr_steps = int(self.lineEdit_12.text())

        params.samples = int(self.lineEdit_9.text())
        params.sleeptime = float(self.lineEdit_15.text())

        params.EnergyMeV = float(self.lineEdit_20.text())
        params.bpm1sampleNum = int(self.lineEdit_22.text())

        params.By = self.lineEdit_23.text()
        params.Bx = self.lineEdit_24.text()
        params.Leff_By = float(self.lineEdit_25.text())
        params.Leff_Bx = float(self.lineEdit_26.text())

        params.realorVM = self.comboBox_11.currentText()

        return params

    def start_scan(self):
        """启动BBA-1扫描"""
        self.clear_plot()
        params = self.get_settings()
        params.recal = False
        self.scan = BBAScanThread(params)
        self.scan.start()
        self.scan.trigger.connect(self.display)

    def stop_scan(self):
        """停止当前扫描"""
        if self.scan is not None:
            self.scan.stop()
            print("Scan thread stopped.")

    def recalculate(self):
        """重新计算BBA-1结果"""
        params = self.get_settings()
        params.recal = True
        self.scan = BBAScanThread(params)
        self.scan.start()
        self.scan.trigger.connect(self.display)

    def clear_plot(self):
        """清除BBA-1图表"""
        self.clear = ClearThread()
        self.clear.start()
        self.clear.trigger.connect(self.display)
        
    def display(self, data: Dict[str, Any]):
        """显示BBA-1数据"""
        if "clear" in data:
            self._clear_plots()
            return

        show_type = data["show"]
        
        if show_type == "k1m2":
            self._plot_k1m2(data["K1Lq"], data["m2"])
        elif show_type == "fit_k1m2":
            self._plot_fit_k1m2(data["x"], data["y"], data["m1"], data["S"], data["mm1"])
        elif show_type == "m1S":
            self._plot_m1s(data["m1"], data["S"], data["yvals"], data["offset"])

    def _clear_plots(self):
        """清除所有图表"""
        self.widget.axes.clear()
        self.widget_2.axes.clear()
        self.widget.canvas.draw()
        self.widget_2.canvas.draw()
        self.lineEdit_10.setText("")

    def _plot_k1m2(self, k1: np.ndarray, m2: np.ndarray):
        """绘制K1Lq vs m2图表"""
        self.widget.axes.plot(k1, m2, "xr")
        self.widget.axes.set_xlabel("$K_1L_q$")
        self.widget.axes.set_ylabel("BPM2 (mm)")
        self.widget.canvas.draw()

    def _plot_fit_k1m2(self, x: np.ndarray, y: np.ndarray, m1: float, S: float, mm1: np.ndarray):
        """绘制拟合的K1Lq vs m2图表"""
        self.widget.axes.plot(x, y, "g--", label="fitting-curve")
        self.widget.canvas.draw()

        SS = np.ones(len(mm1)) * S
        self.widget_2.axes.plot(mm1, SS, "xg")
        self.widget_2.axes.plot(m1, S, "ro")
        self.widget_2.axes.set_xlabel("BPM1 (mm)")
        self.widget_2.axes.set_ylabel("S")
        self.widget_2.canvas.draw()

    def _plot_m1s(self, x: np.ndarray, y: np.ndarray, yvals: np.ndarray, offset: float):
        """绘制m1 vs S图表"""
        self.widget_2.axes.plot(x, y, "ro")
        self.widget_2.axes.set_xlabel("BPM1 (mm)")
        self.widget_2.axes.set_ylabel("S")
        self.widget_2.axes.plot(x, yvals, "g-")
        self.widget_2.canvas.draw()
        self.lineEdit_10.setText(str(round(offset, 1)))

    # BBA-2 相关方法
    def start_scan_bba2(self):
        """启动BBA-2扫描"""
        self.clear_plot_bba2()
        params = self.get_settings_bba2()
        params.recal = False
        self.scan = BBAScanThreadBBA2(params)
        self.scan.start()
        self.scan.trigger.connect(self.display_bba2)

    def recalculate_bba2(self):
        """重新计算BBA-2结果"""
        self.clear_plot_bba2()
        params = self.get_settings_bba2()
        params.recal = True
        self.scan = BBAScanThreadBBA2(params)
        self.scan.start()
        self.scan.trigger.connect(self.display_bba2)

    def clear_plot_bba2(self):
        """清除BBA-2图表"""
        self.clear = ClearThread()
        self.clear.start()
        self.clear.trigger.connect(self.display_bba2)

    def display_bba2(self, data: Dict[str, Any]):
        """显示BBA-2数据"""
        if "clear" in data:
            self._clear_plots_bba2()
            return

        show_type = data["show"]
        
        if show_type == "k1m2":
            self._plot_k1m2_bba2(data["K1Lq"], data["m2"])
        elif show_type == "fit_k1m2":
            self._plot_fit_k1m2_bba2(data["x"], data["y"])
        elif show_type == "thetam2":
            self._plot_thetam2(data["theta"], data["m2"])
        elif show_type == "fit_thetam2":
            self._plot_fit_thetam2(data["x"], data["y"], data["m1_ave"], 
                                 data["R12"], data["b1q1"])

    def _clear_plots_bba2(self):
        """清除BBA-2所有图表"""
        self.widget_3.axes.clear()
        self.widget_4.axes.clear()
        self.widget_3.canvas.draw()
        self.widget_4.canvas.draw()
        self.lineEdit_18.setText("")
        self.lineEdit_19.setText("")
        self.lineEdit_21.setText("")

    def _plot_k1m2_bba2(self, k1: np.ndarray, m2: np.ndarray):
        """绘制BBA-2的K1Lq vs m2图表"""
        self.widget_3.axes.plot(k1, m2 * 1e3, "xr")
        self.widget_3.axes.set_xlabel("$K_1L_q$")
        self.widget_3.axes.set_ylabel("BPM2 (mm)")
        self.widget_3.canvas.draw()

    def _plot_fit_k1m2_bba2(self, x: np.ndarray, y: np.ndarray):
        """绘制BBA-2拟合的K1Lq vs m2图表"""
        self.widget_3.axes.plot(x, y * 1e3, "g--", label="fitting-curve")
        self.widget_3.canvas.draw()

    def _plot_thetam2(self, theta: np.ndarray, m2: np.ndarray):
        """绘制theta vs m2图表"""
        self.widget_4.axes.plot(theta * 1e3, m2 * 1e3, "rx")
        self.widget_4.axes.set_xlabel("corrector kick (mrad)")
        self.widget_4.axes.set_ylabel("BPM2 (mm)")
        self.widget_4.canvas.draw()

    def _plot_fit_thetam2(self, x: np.ndarray, y: np.ndarray, m1_ave: float, 
                         R12: float, b1q1: float):
        """绘制拟合的theta vs m2图表"""
        self.widget_4.axes.plot(x * 1e3, y * 1e3, "g--", label="fitting-curve")
        self.widget_4.canvas.draw()

        self.lineEdit_21.setText(str(round(m1_ave * 1e3, 1)))
        self.lineEdit_19.setText(str(round(R12, 2)))
        self.lineEdit_18.setText(str(round(b1q1 * 1e3, 1)))

class BBAScanThread(QThread):
    """BBA-1扫描线程"""
    trigger = pyqtSignal(dict)
    
    def __init__(self, params: ScanParameters):
        super().__init__()
        self.params = params
        self.is_running = True
        
    def run(self):
        try:
            info = {}
            if not self.params.recal:
                self._perform_scan(info)
            else:
                self._recalculate(info)
                
            self._final_calculation(info)
            self.trigger.emit(info)
            
        except Exception as e:
            print(f"Error in scan thread: {str(e)}")
            self.trigger.emit({"error": str(e)})

    def _perform_scan(self, info: Dict[str, Any]):
        """执行实际扫描"""
        cor = epics.PV(self.params.corrPV)
        quad = epics.PV(self.params.quadPV)
        bpm1 = epics.PV(self.params.bpm1PV)
        bpm2 = epics.PV(self.params.bpm2PV)
        
        sign = 1 if self.params.plane == "X" else -1

        k1l = np.linspace(self.params.quad_from, self.params.quad_end, self.params.quad_steps)
        kickl = np.linspace(self.params.corr_from, self.params.corr_end, self.params.corr_steps)

        m1, S = [], []
        inival_quad = quad.get()
        inival_kick = cor.get()
        
        print(f"Initial values: quad={inival_quad}, kick={inival_kick}")

        for kick in kickl:
            cor.put(kick)
            m2, mm1 = [], []
            
            for k1 in k1l:
                quad.put(k1)
                
                for _ in range(self.params.samples):
                    if not self.is_running:
                        self._restore_initial_values(quad, cor, inival_quad, inival_kick)
                        return
                        
                    time.sleep(self.params.sleeptime)
                    mm2 = bpm2.get()
                    m2.append(mm2)
                    mm1.append(bpm1.get())

                    info.update({
                        "show": "k1m2",
                        "K1Lq": k1 * sign * 0.05,
                        "m2": mm2
                    })
                    self.trigger.emit(info)
                    time.sleep(1)

            mm1_ave = np.mean(mm1)
            m2_mat = np.reshape(m2, (self.params.quad_steps, self.params.samples))
            m2_ave = np.mean(m2_mat, 1)

            x = sign * k1l * 0.05
            y = m2_ave

            z1 = np.polyfit(x, y, deg=1)
            p1 = np.poly1d(z1)
            yvals = p1(x)

            info.update({
                "show": "fit_k1m2",
                "x": x,
                "y": yvals,
                "m1": mm1_ave,
                "S": z1[0],
                "mm1": mm1
            })
            self.trigger.emit(info)
            time.sleep(1)

            m1.append(mm1_ave)
            S.append(z1[0])

        self._restore_initial_values(quad, cor, inival_quad, inival_kick)
        self._save_data(m1, S)

    def _recalculate(self, info: Dict[str, Any]):
        """重新计算数据"""
        try:
            with open("m1S.txt", "r") as f:
                data = np.loadtxt(f)
            info.update({
                "m1": data[:, 0],
                "S": data[:, 1]
            })
        except FileNotFoundError:
            raise Exception("Data file not found for recalculation")

    def _final_calculation(self, info: Dict[str, Any]):
        """执行最终计算"""
        x = info["m1"]
        y = info["S"]
        
        z1 = np.polyfit(x, y, deg=1)
        p1 = np.poly1d(z1)
        yvals = p1(x)
        offset = z1[1] / z1[0]

        info.update({
            "show": "m1S",
            "yvals": yvals,
            "offset": offset
        })

    def _restore_initial_values(self, quad, cor, quad_val, cor_val):
        """恢复初始值"""
        quad.put(quad_val)
        cor.put(cor_val)
        print("Restored initial values")

    def _save_data(self, m1: List[float], S: List[float]):
        """保存数据到文件"""
        txt = np.matrix([m1, S]).transpose()
        np.savetxt("m1S.txt", txt, fmt="%.6e")

    def stop(self):
        """停止扫描"""
        self.is_running = False

class BBAScanThreadBBA2(QThread):
    """BBA-2扫描线程"""
    trigger = pyqtSignal(dict)
    
    def __init__(self, params: ScanParameters):
        super().__init__()
        self.params = params
        self.is_running = True
        self.S = None
        self.m1_ave = None
        self.R12 = None
        
    def run(self):
        try:
            info = {}
            if not self.params.recal:
                self._perform_quad_scan(info)
            else:
                self._load_quad_data(info)
                
            self._fit_quad_data(info)
            
            if not self.params.recal:
                self._perform_bpm1_measurement()
            else:
                self._load_bpm1_data()
                
            self._perform_corrector_scan(info)
            self._final_calculation(info)
            
            self.trigger.emit(info)
            
        except Exception as e:
            print(f"Error in BBA2 scan thread: {str(e)}")
            self.trigger.emit({"error": str(e)})

    def _perform_quad_scan(self, info: Dict[str, Any]):
        """执行四极磁铁扫描"""
        quad = epics.PV(self.params.quadPV)
        bpm2 = epics.PV(self.params.bpm2PV)
        
        sign = 1 if self.params.plane == "X" else -1
        inival_quad = quad.get()
        k1l = np.linspace(self.params.quad_from, self.params.quad_end, self.params.quad_steps)

        kk1, m2 = [], []
        
        for k1 in k1l:
            quad.put(k1)
            
            for _ in range(self.params.samples):
                if not self.is_running:
                    quad.put(inival_quad)
                    return
                    
                time.sleep(self.params.sleeptime)
                mm2 = bpm2.get() * 1e-3
                m2.append(mm2)
                kk1.append(k1)

                info.update({
                    "show": "k1m2",
                    "K1Lq": k1 * sign * 0.05,
                    "m2": mm2
                })
                self.trigger.emit(info)
                time.sleep(1)

        k1Lq = sign * np.array(kk1) * 0.05
        self._save_quad_data(k1Lq, m2)
        quad.put(inival_quad)

    def _load_quad_data(self, info: Dict[str, Any]):
        """加载四极磁铁数据"""
        try:
            with open("bba2_k1Lqm2.txt", "r") as f:
                data = np.loadtxt(f)
            info.update({
                "K1Lq": data[:, 0],
                "m2": data[:, 1]
            })
        except FileNotFoundError:
            raise Exception("Quad scan data file not found")

    def _fit_quad_data(self, info: Dict[str, Any]):
        """拟合四极磁铁数据"""
        k1Lq = info["K1Lq"]
        m2 = info["m2"]
        
        k1Lq_mat = np.reshape(k1Lq, (int(len(k1Lq) / self.params.samples), self.params.samples))
        k1Lq_ave = np.mean(k1Lq_mat, 1)
        
        m2_mat = np.reshape(m2, (int(len(m2) / self.params.samples), self.params.samples))
        m2_ave = np.mean(m2_mat, 1)

        z1 = np.polyfit(k1Lq_ave, m2_ave, deg=1)
        p1 = np.poly1d(z1)
        yvals = p1(k1Lq_ave)

        self.S = z1[0]
        
        info.update({
            "show": "fit_k1m2",
            "x": k1Lq_ave,
            "y": yvals
        })
        self.trigger.emit(info)

    def _perform_bpm1_measurement(self):
        """执行BPM1测量"""
        bpm1 = epics.PV(self.params.bpm1PV)
        m1 = []
        
        for _ in range(self.params.bpm1sampleNum):
            if not self.is_running:
                return
                
            mm1 = bpm1.get() * 1e-3
            m1.append(mm1)
            time.sleep(2)
            
        np.savetxt("bba2_m1.txt", m1, fmt="%.6e")
        self.m1_ave = np.mean(m1)

    def _load_bpm1_data(self):
        """加载BPM1数据"""
        try:
            with open("bba2_m1.txt", "r") as f:
                m1 = np.loadtxt(f)
            self.m1_ave = np.mean(m1)
        except FileNotFoundError:
            raise Exception("BPM1 data file not found")

    def _perform_corrector_scan(self, info: Dict[str, Any]):
        """执行校正器扫描"""
        cor = epics.PV(self.params.corrPV)
        bpm2 = epics.PV(self.params.bpm2PV)
        
        kickl = np.linspace(self.params.corr_from, self.params.corr_end, self.params.corr_steps)
        anglel = self._calculate_kick_angles(kickl)
        
        if not self.params.recal:
            inival_kick = cor.get()
            theta, m2 = [], []
            
            for i, kick in enumerate(kickl):
                cor.put(kick)
                
                for _ in range(self.params.samples):
                    if not self.is_running:
                        cor.put(inival_kick)
                        return
                        
                    time.sleep(self.params.sleeptime)
                    mm2 = bpm2.get() * 1e-3
                    m2.append(mm2)
                    theta.append(anglel[i])

                    info.update({
                        "show": "thetam2",
                        "theta": anglel[i],
                        "m2": mm2
                    })
                    self.trigger.emit(info)
                    time.sleep(1)
                    
            self._save_corrector_data(theta, m2)
            cor.put(inival_kick)
        else:
            self._load_corrector_data(info)

    def _calculate_kick_angles(self, currents: np.ndarray) -> np.ndarray:
        """计算踢角"""
        if self.params.plane == "X":
            By = eval(self.params.By) * 1e-4
            Leff = self.params.Leff_By
        elif self.params.plane == "Y":
            Bx = eval(self.params.Bx) * 1e-4
            Leff = self.params.Leff_Bx
        else:
            raise ValueError("Invalid plane specified")
            
        if self.params.realorVM == "Virtual Machine":
            return currents
        else:
            return 299.8 / self.params.EnergyMeV * (By if self.params.plane == "X" else Bx) * Leff

    def _save_corrector_data(self, theta: List[float], m2: List[float]):
        """保存校正器数据"""
        txt = np.matrix([theta, m2]).transpose()
        np.savetxt("bba2_thetam2.txt", txt, fmt="%.6e")

    def _load_corrector_data(self, info: Dict[str, Any]):
        """加载校正器数据"""
        try:
            with open("bba2_thetam2.txt", "r") as f:
                data = np.loadtxt(f)
            info.update({
                "theta": data[:, 0],
                "m2": data[:, 1]
            })
        except FileNotFoundError:
            raise Exception("Corrector scan data file not found")

    def _final_calculation(self, info: Dict[str, Any]):
        """执行最终计算"""
        theta = info["theta"]
        m2 = info["m2"]
        
        theta_mat = np.reshape(theta, (int(len(theta) / self.params.samples), self.params.samples))
        theta_ave = np.mean(theta_mat, 1)
        
        m2_mat = np.reshape(m2, (int(len(m2) / self.params.samples), self.params.samples))
        m2_ave = np.mean(m2_mat, 1)

        z1 = np.polyfit(theta_ave, m2_ave, deg=1)
        self.R12 = z1[0]
        
        b1mq1 = self.S / self.R12 - self.m1_ave
        
        info.update({
            "show": "fit_thetam2",
            "x": theta_ave,
            "y": np.poly1d(z1)(theta_ave),
            "m1_ave": self.m1_ave,
            "R12": self.R12,
            "b1q1": b1mq1
        })

    def stop(self):
        """停止扫描"""
        self.is_running = False

class ClearThread(QThread):
    """清除图表线程"""
    trigger = pyqtSignal(dict)
    
    def run(self):
        self.trigger.emit({"clear": True})

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = BBAWindow()
    window.show()
    sys.exit(app.exec_())