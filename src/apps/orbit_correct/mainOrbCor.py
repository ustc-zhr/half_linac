
import sys
import signal
from subprocess import Popen,run
import re

from PyQt5.QtWidgets import QMainWindow, QApplication, QCheckBox, QDoubleSpinBox
from PyQt5.QtCore import QThread, Qt, QRegExp
from OrbCorgui import Ui_MainWindow


import half_linac.setup as st

class myWindow(QMainWindow, Ui_MainWindow):
    def __init__(self):
        super().__init__()
        self.setupUi(self)
        self.subprocesses=[]
        

        self.all_checkboxes = self.findChildren(QCheckBox)

        # connect button
        self.pushButton.clicked.connect(self.measure_res)
        self.pushButton_4.clicked.connect(self.start_cor)
        self.pushButton_2.clicked.connect(self.cor_off)
        self.pushButton_3.clicked.connect(self.stop_cor)
        # self.pushButton.clicked.connect(self.printzz)

        # other button
        self.pushButton_5.clicked.connect(self.selectall)
        self.pushButton_6.clicked.connect(self.cancelall)


        # initial parameters
        self.samplingIntervalSLineEdit.setText('6') # s
        self.correctorAccuracyUmLineEdit.setText('10') # um
        self.sampPerStepLineEdit.setText('2') # 

    def _extract_number(self, s):
        # 提取字符串中的第一个连续数字并转为整数
        match = re.search(r'\d+', s)
        return int(match.group()) if match else 0


    def selectall(self):
        for cb in self.all_checkboxes:
            cb.setChecked(True)

    def cancelall(self):
        for cb in self.all_checkboxes:
            cb.setChecked(False)
    
    def all_BPM_target_value(self):
        # 将BPM的目标值按照1~43依次排列
        all_bpmx_spinboxes = self.findChildren(QDoubleSpinBox, QRegExp("bpmx_*"))
        # 组合数据（索引、名称、值）
        combined_data = []
        for sb in all_bpmx_spinboxes:
            index = self._extract_number(sb.objectName())
            combined_data.append( (index, sb.objectName(), sb.value()) )
        # 按索引排序
        combined_data.sort(key=lambda x: x[0])
        # 解包排序后的数据
        indeics, all_bpmx_spinboxes_names, all_bpmx_target_values = zip(*combined_data)
        # 转换为列表（如果后续需要修改）
        # all_bpmx_spinboxes_names = list(all_bpmx_spinboxes_names)
        all_bpmx_target_values = list(all_bpmx_target_values)

        all_bpmy_spinboxes = self.findChildren(QDoubleSpinBox, QRegExp("bpmy_*"))
        # 组合数据（索引、名称、值）
        combined_data = []
        for sb in all_bpmy_spinboxes:
            index = self._extract_number(sb.objectName())
            combined_data.append( (index, sb.objectName(), sb.value()) )
        # 按索引排序
        combined_data.sort(key=lambda x: x[0])
        # 解包排序后的数据
        indeics, all_bpmy_spinboxes_names, all_bpmy_target_values = zip(*combined_data)
        # 转换为列表（如果后续需要修改）
        # all_bpmy_spinboxes_names = list(all_bpmy_spinboxes_names)
        all_bpmy_target_values = list(all_bpmy_target_values)

        return all_bpmx_target_values, all_bpmy_target_values

    def target_BPMs(self):
        all_bpmx_target_values, all_bpmy_target_values = self.all_BPM_target_value()

        # print(all_bpmx_target_values)
        # print(all_bpmy_target_values)
        all_checkboxes = self.findChildren(QCheckBox)
        bpm_target_list = [cb.text() for cb in all_checkboxes if cb.isChecked()]
        bpm_target_list.sort(key=self._extract_number)

        indices = []
        for cb in bpm_target_list:
            indices.append(self._extract_number(cb))
        # print(indices)
        bpmx_target_values = [all_bpmx_target_values[i-1] for i in indices]
        bpmy_target_values = [all_bpmy_target_values[i-1] for i in indices]
        # print(bpmx_target_values)
        # print(bpmy_target_values)

        # target_list = list(zip(bpm_target_list, bpmx_target_values, bpmy_target_values))
        
        return bpm_target_list, bpmx_target_values, bpmy_target_values
        
    def measure_res(self): #measure response matrix
        cmd = [
            "python3", "findresponse_optimized.py",                  #0
        ]
        kwargs = {}
        kwargs["start_new_session"] = True  # Unix: 新会话组

        proc = Popen(
            cmd,
            cwd=st.rootpath + "/src/apps/orbit_correct",
            shell=False,  # 避免 shell 进程干扰
            **kwargs
        )
        self.subprocesses.append(proc)        

    def start_cor(self):
        
        # prepare the target paras. 
        bpm_target_list, bpmx_target_values, bpmy_target_values = self.target_BPMs()
        bpmx_target_values = [str(i) for i in bpmx_target_values]
        bpmy_target_values = [str(i) for i in bpmy_target_values]
        
        cmd = [
            "python3", "correct_optimized.py",                  #0
            "start_cor",                              #1
            self.comboBox.currentText(),              #2   method
            self.samplingIntervalSLineEdit.text(),    #3   samp_interval
            self.correctorAccuracyUmLineEdit.text(),  #4   cor_accuracy
            self.sampPerStepLineEdit.text(),          #5   samples_perstep
            ",".join(bpm_target_list),                     #6   target_BPMlist
            ",".join(bpmx_target_values),                     #7   target_BPMlist
            ",".join(bpmy_target_values)                     #8   target_BPMlist
        ]
        # 跨平台启动进程（确保进程组独立）
        kwargs = {}
        kwargs["start_new_session"] = True  # Unix: 新会话组

        proc = Popen(
            cmd,
            cwd=st.rootpath + "/src/apps/orbit_correct",
            shell=False,  # 避免 shell 进程干扰
            **kwargs
        )
        # 获取输出
        # stdout, stderr = proc.communicate()
        # print("输出:", stdout.decode())
        self.subprocesses.append(proc)
        # print(f"启动子进程 PID: {proc.pid}")    



    # cor_off
    # def cor_off(self):
    #     Popen("python3 correct.py cor_off",cwd=st.rootpath+"/apps/orbit_correct",shell=True)
    def cor_off(self):
        bpm_target_list, bpmx_target_values, bpmy_target_values = self.target_BPMs()
        cmd = [
            "python3", "correct_optimized.py",                  #0
            "cor_off",                                 #1
            ",".join(bpm_target_list)                  #2
        ]
        # 跨平台启动进程（确保进程组独立）
        kwargs = {}
        kwargs["start_new_session"] = True  # Unix: 新会话组

        Popen(
            cmd,
            cwd=st.rootpath + "/src/apps/orbit_correct",
            shell=False,  # 避免 shell 进程干扰
            **kwargs
        )


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


