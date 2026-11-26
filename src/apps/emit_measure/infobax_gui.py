"""Independent InfoBAX operator window; EPICS work runs only in its worker."""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from threading import Event
from uuid import uuid4

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton,
    QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView,
)
from matplotlib.figure import Figure
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
import numpy as np

from half_linac.src.shared.machine_profile import load_app_context, get_emit_preset
from .infobax_launcher import build_workflow


class WorkflowWorker(QThread):
    ready = pyqtSignal(object)
    progress = pyqtSignal(dict)
    requested = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, *, options=None, run_dir=None, workflow=None, resume=False, action="run", parent=None):
        super().__init__(parent)
        self.options, self.run_dir, self.workflow = options, run_dir, workflow
        self.resume, self.action = resume, action
        self.answer = Event()
        self.accepted = False
        self.abort = Event()
        self.pause = False

    def approve(self, request, check):
        self.answer.clear()
        self.accepted = False
        self.requested.emit(request)
        while not self.answer.wait(0.05):
            check()
            if self.abort.is_set():
                self.workflow.request_pause() if self.pause else self.workflow.cancelled.set()
        check()
        return self.accepted

    def decide(self):
        self.accepted = True
        self.answer.set()

    def stop(self, *, pause=False):
        self.pause = pause
        self.abort.set()
        if self.workflow is not None:
            self.workflow.request_pause() if pause else self.workflow.cancelled.set()

    def run(self):
        try:
            if self.workflow is None:
                self.workflow = build_workflow(self.options, self.run_dir, resume=self.resume,
                    approve=self.approve, progress=self.progress.emit)
            self.workflow.approve, self.workflow.progress = self.approve, self.progress.emit
            if self.abort.is_set():
                self.workflow.request_pause() if self.pause else self.workflow.cancelled.set()
            self.ready.emit(self.workflow)
            if self.action == "run":
                self.workflow.run()
            elif self.action == "apply":
                self.workflow.manual_move(apply_verified=True)
            elif self.action == "restore":
                self.workflow.manual_move()
            elif self.action != "load":
                raise ValueError("Unknown worker action")
        except Exception as exc:
            self.failed.emit(str(exc))


class InfoBAXWindow(QMainWindow):
    def __init__(self, backend="fake", run_root=None):
        super().__init__()
        self.setWindowTitle("HALF Linac · InfoBAX 发射度优化")
        self.resize(1180, 900)
        self.worker = self.workflow = None
        self.loaded_dir = None
        self._closing = False
        self.worker_error = None
        self.run_root = Path(run_root or Path.cwd()/"runtime"/"emit_infobax")
        self.context = load_app_context("emit_measure", machine_id="half", control_backend="real")
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        self.settings = QGroupBox("测量与优化设置")
        form = QFormLayout(self.settings)
        self.backend = QComboBox()
        self.backend.addItems(["fake", "real"])
        self.backend.setCurrentText(backend)
        self.preset = QComboBox()
        self.preset.addItems([p.id for p in self.context.emit_measure_workflow.presets])
        self.preset.setCurrentText(self.context.emit_measure_workflow.default_preset)
        self.variable = QComboBox()
        self.variable.addItems([e.id for e in self.context.profile.elements if e.kind == "solenoid"])
        self.plane, self.model = QComboBox(), QComboBox()
        self.plane.addItems(["x", "y"])
        self.model.addItems(["transport_moments", "M0"])
        self.low, self.high = self.number(-1), self.number(1)
        self.scan_low, self.scan_high = self.number(-1), self.number(5)
        self.energy, self.settle, self.interval = self.number(2200, positive=True), self.number(8, positive=True), self.number(0, positive=True)
        self.scan_points, self.samples, self.active, self.minutes = QSpinBox(), QSpinBox(), QSpinBox(), QSpinBox()
        for widget, minimum, maximum, value in ((self.scan_points,3,101,15),(self.samples,3,100,5),
                                               (self.active,0,2**31 - 1,3),(self.minutes,1,1440,120)):
            widget.setRange(minimum, maximum)
            widget.setValue(value)
        self.roi = QLineEdit()
        self.roi.setPlaceholderText("留空使用 profile ROI；或 left,right,bottom,top 像素边界")
        self.background = QLineEdit()
        self.background.setPlaceholderText("留空：不扣背景；或选择与翻转后图像一致的 .npy")
        background_row = QWidget()
        row = QHBoxLayout(background_row)
        row.setContentsMargins(0,0,0,0)
        row.addWidget(self.background)
        choose = QPushButton("选择背景")
        choose.clicked.connect(self.choose_background)
        row.addWidget(choose)
        self.upstream = QCheckBox("已确认螺线管位于扫描段上游，并核对能量、电流/K1 范围和背景")
        for label, widgets in (("后端 / 测量预置",(self.backend,self.preset)),
                ("螺线管 / 目标平面 / 后验",(self.variable,self.plane,self.model)),
                ("电流范围 [A]",(self.low,self.high)),
                ("K1 范围 / 点数",(self.scan_low,self.scan_high,self.scan_points)),
                ("能量 [MeV] / 稳定时间 [s]",(self.energy,self.settle)),
                ("每点帧数 / 帧间隔 [s]",(self.samples,self.interval)),
                ("主动查询上限 / 会话时限 [min]",(self.active,self.minutes))):
            holder=QWidget(); h=QHBoxLayout(holder); h.setContentsMargins(0,0,0,0)
            for widget in widgets:
                h.addWidget(widget)
            form.addRow(label,holder)
        form.addRow("ROI",self.roi)
        form.addRow("背景",background_row)
        form.addRow(self.upstream)
        layout.addWidget(self.settings)
        self.note=QLabel(); self.note.setWordWrap(True); layout.addWidget(self.note)
        actions=QHBoxLayout()
        self.start_button=QPushButton("开始")
        self.load_button=QPushButton("载入运行")
        self.approve_button=QPushButton("确认执行当前建议")
        self.pause_button=QPushButton("暂停并还原")
        self.stop_button=QPushButton("停止并还原")
        self.apply_button=QPushButton("应用验证结果")
        self.restore_button=QPushButton("恢复初始")
        for button in (self.start_button,self.load_button,self.approve_button,self.pause_button,
                       self.stop_button,self.apply_button,self.restore_button):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.status=QLabel("就绪；默认 Fake，不访问设备。")
        self.status.setWordWrap(True); layout.addWidget(self.status)
        self.summary=QLabel("基线：—    复核：—    还原：—")
        layout.addWidget(self.summary)
        self.figure=Figure(figsize=(8,2.6), constrained_layout=True)
        self.canvas=FigureCanvasQTAgg(self.figure)
        self.canvas.setMinimumHeight(200)
        self.axes,self.image_axes=self.figure.subplots(1,2)
        self.axes.set(xlabel="Measurement",ylabel="RMS variance [mm²]")
        self.image_axes.set_title("Latest raw frame")
        layout.addWidget(self.canvas,1)
        self.table=QTableWidget(0,6)
        self.table.setHorizontalHeaderLabels(["阶段","电流 [A]","K1","方差 [mm²]","标准误","状态"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.table,1)
        self.start_button.clicked.connect(self.start_run)
        self.load_button.clicked.connect(self.load_run)
        self.approve_button.clicked.connect(self.approve_current)
        self.pause_button.clicked.connect(lambda: self.stop_run(True))
        self.stop_button.clicked.connect(lambda: self.stop_run(False))
        self.apply_button.clicked.connect(lambda: self.manual(True))
        self.restore_button.clicked.connect(lambda: self.manual(False))
        self.preset.currentTextChanged.connect(self.preset_changed)
        self.backend.currentTextChanged.connect(self.refresh_buttons)
        self.upstream.toggled.connect(self.refresh_buttons)
        self.preset_changed()

    @staticmethod
    def number(value, positive=False):
        w=QDoubleSpinBox(); w.setDecimals(6); w.setRange(0 if positive else -1e6,1e6); w.setValue(value)
        return w

    def choose_background(self):
        path,_=QFileDialog.getOpenFileName(self,"选择背景",str(Path.cwd()),"NumPy (*.npy)")
        if path: self.background.setText(path)

    def preset_changed(self):
        p=get_emit_preset(self.context,self.preset.currentText())
        self.energy.setValue(p.energy_mev)
        self.scan_low.setValue(p.scan.k1_from); self.scan_high.setValue(p.scan.k1_end)
        self.scan_points.setValue(p.scan.k1_steps)
        self.settle.setValue(p.scan.settle_time or 0); self.interval.setValue(p.scan.sample_interval or 0)
        self.refresh_buttons()

    def options(self):
        roi=None if not self.roi.text().strip() else tuple(int(v.strip()) for v in self.roi.text().split(","))
        if roi is not None and len(roi)!=4: raise ValueError("ROI 需要四个像素边界")
        return {"backend":self.backend.currentText(),"preset":self.preset.currentText(),
            "variable":self.variable.currentText(),"plane":self.plane.currentText(),"model":self.model.currentText(),
            "current_low":self.low.value(),"current_high":self.high.value(),"energy_mev":self.energy.value(),
            "scan_low":self.scan_low.value(),"scan_high":self.scan_high.value(),"scan_points":self.scan_points.value(),
            "samples":self.samples.value(),"active_queries":self.active.value(),"max_minutes":self.minutes.value(),
            "settle_time":self.settle.value(),"sample_interval":self.interval.value(),"roi":roi,
            "background":self.background.text().strip() or None,"upstream_confirmed":self.upstream.isChecked()}

    def busy(self):
        return self.worker is not None and self.worker.isRunning()

    def refresh_buttons(self):
        busy=self.busy()
        available=True
        if self.backend.currentText()=="real":
            available=False
            note="真机操作统一从主应用的发射度优化 / InfoBAX 标签页进入；可使用 --backend real 打开。"
        else:
            note="Fake：合成束矩观测用于验证流程，不进行相机采集；真机从主应用共享测量。"
        self.note.setText(note)
        resumable=self.workflow is not None and self.workflow.status=="paused"
        self.settings.setEnabled(not busy and self.workflow is None)
        self.start_button.setText("继续" if resumable else "开始新运行")
        self.start_button.setEnabled(not busy and available and (self.backend.currentText()=="fake" or self.upstream.isChecked() or resumable))
        self.load_button.setEnabled(not busy)
        self.pause_button.setEnabled(busy); self.stop_button.setEnabled(busy)
        if not busy: self.approve_button.setEnabled(False)
        self.apply_button.setEnabled(not busy and self.workflow is not None and self.workflow.confirmed
                                     and self.workflow.restoration_status=="succeeded")
        self.restore_button.setEnabled(not busy and self.workflow is not None and self.workflow.initial is not None)

    def launch(self, **kwargs):
        self.worker_error = None
        self.approve_button.setEnabled(False)
        self.worker=WorkflowWorker(parent=self,**kwargs)
        self.worker.ready.connect(self.ready)
        self.worker.progress.connect(self.show_progress)
        self.worker.requested.connect(self.request)
        self.worker.failed.connect(self.failure)
        self.worker.finished.connect(self.finished)
        self.worker.start(); self.refresh_buttons()

    def start_run(self):
        try:
            if self.workflow is not None and self.workflow.status=="paused":
                self.workflow.cancelled.clear()
                self.launch(workflow=self.workflow)
                return
            if self.workflow is not None:
                self.workflow=None; self.loaded_dir=None; self.settings.setEnabled(True)
                self.status.setText("请调整设置，再点击开始新运行。")
                self.refresh_buttons(); return
            options=self.options()
            run_dir=self.run_root/(datetime.now().strftime("%Y%m%d_%H%M%S")+"_"+uuid4().hex[:8])
            self.status.setText(f"正在准备：{run_dir}")
            self.launch(options=options,run_dir=run_dir)
        except Exception as exc: self.failure(str(exc))

    def ready(self, workflow):
        self.workflow=workflow
        self.status.setText(f"运行目录：{workflow.run_dir}")

    def request(self, request):
        detail=(f"K1={request['scan_k1']:.6g}" if 'scan_k1' in request else
                f"完整扫描 {request['remaining_points']} 个剩余点，K1={request['scan_values']}")
        self.status.setText(f"等待确认 · {request['phase']} · 电流 {request['current_a']:.6g} A · {detail}")
        self.approve_button.setEnabled(not self.worker.abort.is_set())

    def approve_current(self):
        self.approve_button.setEnabled(False)
        if self.busy(): self.worker.decide()

    def stop_run(self, pause):
        self.approve_button.setEnabled(False)
        if self.busy(): self.worker.stop(pause=pause)

    def show_progress(self, payload):
        if payload.get("status")!="awaiting_approval":
            self.status.setText(f"{payload.get('status','')} · {payload.get('record_count',0)} 条记录 · "
                                f"{payload.get('elapsed_seconds',0):.1f} s · {payload.get('error') or ''}")
        if self.workflow is not None:
            self.render()

    def render(self):
        w=self.workflow
        records=list(w.records)
        self.table.setRowCount(len(records))
        x,y=[],[]
        for i,r in enumerate(records):
            m=r.get("measurement") or {}
            values=(r["phase"],r["current_a"],r["scan_k1"],m.get("variance_mm2","—"),m.get("noise_std_mm2","—"),r["state"])
            for j,value in enumerate(values):
                self.table.setItem(i,j,QTableWidgetItem(f"{value:.6g}" if isinstance(value,float) else str(value)))
            if m:
                x.append(i); y.append(m["variance_mm2"])
        self.table.scrollToBottom()
        self.axes.clear(); self.axes.plot(x,y,".-"); self.axes.set(xlabel="Measurement",ylabel="RMS variance [mm²]")
        observed=[r for r in records if r.get("measurement")]
        if observed:
            frames=observed[-1]["measurement"]["metadata"].get("frames",[])
            if frames and frames[-1].get("raw_path"):
                try:
                    with np.load(frames[-1]["raw_path"],allow_pickle=False) as data:
                        shape=w.machine.camera.settings.shape
                        image=data["raw"].reshape(shape[1],shape[0])
                    self.image_axes.clear(); self.image_axes.imshow(image,origin="lower")
                    self.image_axes.set_title(f"Raw frame {frames[-1]['frame_id']}")
                except (OSError,ValueError): pass
        def metric(f):
            if f is None: return "—"
            error=f.get("standard_error_mm_mrad")
            return f"{f['normalized_emittance_mm_mrad']:.5g}" + ("" if error is None else f" ± {error:.3g}") + " mm·mrad"
        self.summary.setText(f"基线：{metric(w.baseline_fit)}    复核：{metric(w.verification_fit)}    "
                             f"还原：{w.restoration_status}    目录：{w.run_dir}")
        self.canvas.draw_idle()

    def load_run(self):
        directory=QFileDialog.getExistingDirectory(self,"载入 InfoBAX 运行",str(self.run_root))
        if not directory: return
        try:
            data=json.loads((Path(directory)/"infobax_workflow.json").read_text())
            settings=json.loads(data["config"]["settings_json"])
            self.backend.setCurrentText(settings["launch"]["backend"])
            self.preset.setCurrentText(settings["launch"]["preset"])
            for key,widget in (("variable",self.variable),("plane",self.plane),("model",self.model)):
                widget.setCurrentText(settings["launch"][key])
            for key,widget in (("current_low",self.low),("current_high",self.high),("energy_mev",self.energy),
                ("scan_low",self.scan_low),("scan_high",self.scan_high),("scan_points",self.scan_points),
                ("samples",self.samples),("active_queries",self.active),("max_minutes",self.minutes),
                ("settle_time",self.settle),("sample_interval",self.interval)):
                widget.setValue(settings["launch"][key])
            self.upstream.setChecked(settings["launch"].get("upstream_confirmed",False))
            self.roi.setText("" if not settings["launch"].get("roi") else ",".join(map(str,settings["launch"]["roi"])))
            self.background.setText(settings["launch"].get("background") or "")
            self.workflow = None
            self.launch(options={"backend":self.backend.currentText()},run_dir=directory,resume=True,action="load")
        except Exception as exc: self.failure(str(exc))

    def manual(self, apply):
        if apply and QMessageBox.question(self,"应用验证结果","仅改变选定螺线管电流。确认应用完整复核通过的改善结果？")!=QMessageBox.Yes:
            return
        self.launch(workflow=self.workflow,action="apply" if apply else "restore")

    def failure(self,message):
        self.worker_error = message
        if self.workflow is not None:
            self.workflow.confirmed = False
        self.status.setText(message)

    def finished(self):
        if self.workflow is not None:
            self.render()
            self.status.setText(f"{self.workflow.status} · 还原 {self.workflow.restoration_status} · {self.workflow.error or ''}")
        if self.worker_error: self.status.setText(self.worker_error)
        self.refresh_buttons()
        if self._closing: self.close()

    def closeEvent(self,event):
        if self.busy():
            self._closing=True; self.stop_run(False); event.ignore()
        else: event.accept()


def show_window(backend="fake", run_root=None):
    import sys
    app=QApplication.instance() or QApplication(sys.argv[:1])
    window=InfoBAXWindow(backend,run_root)
    window.show()
    return app.exec_()
