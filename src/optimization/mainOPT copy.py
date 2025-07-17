
import sys
import signal
from subprocess import Popen,run
import re
import numpy as np

from PyQt5.QtWidgets import QMainWindow, QApplication, QCheckBox, QDoubleSpinBox
from PyQt5.QtCore import QThread, Qt, QRegExp, QTimer
from OPTgui import Ui_MainWindow


import half_linac.setup as st

class myWindow(QMainWindow, Ui_MainWindow):
    def __init__(self):
        super().__init__()
        self.setupUi(self)
        self.subprocesses=[]
        # self.plot_timer = QTimer(self)
        # self.plot_timer.timeout.connect(self._update_plot)

        # connect button
        self.pushButton_7.clicked.connect(self.start_opt)
        # self.pushButton_7.clicked.connect(self.opt_plot)

        # knobs
        # quad
        self.checkBox_2.clicked.connect(lambda: self.selectall(self.checkBox_2.isChecked(), 'Q'))
        self.pushButton_5.clicked.connect(lambda: self.setall(self.doubleSpinBox_121.value(), 'Q.*.minus.*'))
        self.pushButton_6.clicked.connect(lambda: self.setall(self.doubleSpinBox_122.value(), 'Q.*.plus.*'))
        # kick
        self.checkBox.clicked.connect(lambda: self.selectall(self.checkBox.isChecked(), 'C'))
        self.pushButton_9.clicked.connect(lambda: self.setall(self.doubleSpinBox.value(), 'C.*.minus.*'))
        self.pushButton_10.clicked.connect(lambda: self.setall(self.doubleSpinBox_2.value(), 'C.*.plus.*'))


        # # initial parameters
        self.step_lineEdit.setText('0.1') # 
        self.iter_lineEdit.setText('1') # 
        self.noise_lineEdit.setText('0') # 
        self.interval_lineEdit.setText('8') # 



        
    def selectall(self, state, indexstr):
        all_checkboxes = self.findChildren(QCheckBox)
        relative_checkboxes = [cb for cb in all_checkboxes if indexstr in cb.text()]
        if state:
            for cb in relative_checkboxes:
                cb.setChecked(True)
        if not state:
            for cb in relative_checkboxes:
                cb.setChecked(False)

    def setall(self, value, indexstr):
        all_spinboxes = self.findChildren(QDoubleSpinBox, QRegExp(indexstr))
        for sb in all_spinboxes:
            sb.setValue(value)

    def find_knobs(self):
        knobs_list = []
        knobs_minus = []
        knobs_plus = []

        all_checkboxes = self.findChildren(QCheckBox)
        used_knobs = [cb.text() for cb in all_checkboxes if cb.isChecked()] # 找到被勾选的变量文本内容
        # print(used_knobs)
        all_range_spinboxes = self.findChildren(QDoubleSpinBox, QRegExp("range.*"))

        filtered_quad = [s for s in used_knobs if 'Q' in s]
        if filtered_quad != []:
            quad_minus, quad_plus = self._get_ordered_values(filtered_quad, all_range_spinboxes)
            knobs_list.extend(filtered_quad)
            knobs_minus.extend(quad_minus)
            knobs_plus.extend(quad_plus)

        filtered_kick = [s for s in used_knobs if 'C' in s]
        if filtered_kick != []:
            kick_minus, kick_plus = self._get_ordered_values(filtered_kick, all_range_spinboxes)
            knobs_list.extend(filtered_kick)
            knobs_minus.extend(kick_minus)
            knobs_plus.extend(kick_plus)
        
        # print(knobs_list, knobs_minus, knobs_plus)

        return knobs_list, knobs_minus, knobs_plus
    def get_obj(self):
        obj_pvname = [self.obj1_pvname.text(), self.obj2_pvname.text(), self.obj3_pvname.text()]
        obj_weight = [self.obj1_weight.text(), self.obj2_weight.text(), self.obj3_weight.text()]
        obj_samples = [self.obj1_samples.text(), self.obj2_samples.text(), self.obj3_samples.text()]
        obj_math = [self.obj1_math.currentText(), self.obj2_math.currentText(), self.obj3_math.currentText()]

        valid_indices = [i for i, pv in enumerate(obj_pvname) if pv != '']
        obj_pvname = [obj_pvname[i] for i in valid_indices]
        obj_weight = [obj_weight[i] for i in valid_indices]
        obj_samples = [obj_samples[i] for i in valid_indices]
        obj_math = [obj_math[i] for i in valid_indices]

        return obj_pvname, obj_weight, obj_samples, obj_math
    
    def _get_ordered_values(self, basis_list, spinboxes):
        """
        根据基准字符串列表顺序获取匹配的 QDoubleSpinBox 值
        """
        # 创建结果列表，长度与基准列表相同，初始化为 None
        values_minus = [None] * len(basis_list)
        values_plus = [None] * len(basis_list)
        
        # 遍历所有 QDoubleSpinBox
        for spinbox in spinboxes:
            obj_name = spinbox.objectName()
            
            # 检查对象名是否包含基准列表中的任何字符串
            for idx, basis_str in enumerate(basis_list):
                if basis_str in obj_name:
                    if 'minus' in obj_name:
                        values_minus[idx] = spinbox.value()
                    if 'plus' in obj_name:
                        values_plus[idx] = spinbox.value()
                    break  # 找到第一个匹配就跳出循环
        
        return values_minus, values_plus
    
        
     

    def start_opt(self):
        # prepare the knobs and obj. 
        knobs_list, knobs_minus, knobs_plus = self.find_knobs()

        knobs_minus = [str(i) for i in knobs_minus]
        knobs_plus = [str(i) for i in knobs_plus]
        obj_pvname, obj_weight, obj_samples, obj_math = self.get_obj()
        
        cmd = [
            "python3", "RCDSopt.py",                  #0
            "start_opt",                              #1
            ",".join(knobs_list),                     #2         
            ",".join(knobs_minus),               #3
            ",".join(knobs_plus),                #4
            self.step_lineEdit.text(),                #5 
            self.iter_lineEdit.text(),                #6 
            self.noise_lineEdit.text(),               #7 
            ",".join(obj_pvname),                     #8  
            ",".join(obj_weight),                     #9 
            ",".join(obj_samples),                    #10 
            ",".join(obj_math)                        #11
        ]
        # 跨平台启动进程（确保进程组独立）
        kwargs = {}
        kwargs["start_new_session"] = True  # Unix: 新会话组

        proc = Popen(
            cmd,
            cwd=st.rootpath + "/src/optimization/RCDS",
            shell=False,  # 避免 shell 进程干扰
            **kwargs
        )
        self.subprocesses.append(proc)
        proc.wait()  # 等待子进程完成
        self._optplot()  # 子进程结束后再绘制
        
        
    def _optplot(self):
        self.optplot.axes.clear()
        with open("./RCDS/template.opt","r") as f:
            data = np.loadtxt(f)
        values = data[:, -1]   
        min_values = np.minimum.accumulate(values)

        self.optplot.axes.plot(values, 'b-', linewidth=1.5, label='Current function value')
        self.optplot.axes.plot(min_values, 'r--', linewidth=1.5, label='Historical minimum value')
 
        self.optplot.axes.set_xlabel('Number of function evaluations')
        self.optplot.axes.set_ylabel('function value')
        self.optplot.axes.set_title('Convergence curve')
        self.optplot.axes.legend()
        self.optplot.axes.grid(True)
        self.optplot.canvas.draw()


    # # cor_off
    # # def cor_off(self):
    # #     Popen("python3 correct.py cor_off",cwd=st.rootpath+"/apps/orbit_correct",shell=True)
    # def cor_off(self):
    #     bpm_target_list, bpmx_target_values, bpmy_target_values = self.target_BPMs()
    #     cmd = [
    #         "python3", "correct_optimized.py",                  #0
    #         "cor_off",                                 #1
    #         ",".join(bpm_target_list)                  #2
    #     ]
    #     # 跨平台启动进程（确保进程组独立）
    #     kwargs = {}
    #     kwargs["start_new_session"] = True  # Unix: 新会话组

    #     Popen(
    #         cmd,
    #         cwd=st.rootpath + "/src/apps/orbit_correct",
    #         shell=False,  # 避免 shell 进程干扰
    #         **kwargs
    #     )

    # def cor_recover(self):
    #     # bpm_target_list, bpmx_target_values, bpmy_target_values = self.target_BPMs()
    #     cmd = [
    #         "python3", "correct_optimized.py",                  #0
    #         "cor_off",                                 #1
    #         # ",".join(bpm_target_list)                  #2
    #     ]
    #     # 跨平台启动进程（确保进程组独立）
    #     kwargs = {}
    #     kwargs["start_new_session"] = True  # Unix: 新会话组

    #     Popen(
    #         cmd,
    #         cwd=st.rootpath + "/src/apps/orbit_correct",
    #         shell=False,  # 避免 shell 进程干扰
    #         **kwargs
    #     )


    # stop_cor
    def stop_cor(self):
        for pro in self.subprocesses:
            try:
                pro.send_signal(signal.SIGTERM)
            except:
                pro.kill()
        self.subprocesses = []
    
    # 窗口关闭事件
    def closeEvent(self, event):
        self.stop_cor()  # 调用停止函数
        event.accept()




if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = myWindow()
    window.show()
    sys.exit(app.exec_())


