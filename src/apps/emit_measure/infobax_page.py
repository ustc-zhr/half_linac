"""InfoBAX algorithm page hosted by the existing optimization dialog."""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from queue import Queue, Empty
from threading import Event
from uuid import uuid4

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QSpinBox, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QFileDialog, QAbstractItemView)
from matplotlib.figure import Figure
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg

from half_linac.src.shared.machine_profile import resolve_app_runtime_paths
from .control import emittance_lock
from .infobax_gui import WorkflowWorker
from .infobax_framework import archived_variables, build_framework_workflow, load_framework_recovery
from .infobax_workflow import (
    DEFAULT_CURRENT_CANDIDATES, DEFAULT_INITIAL_POINTS_PER_END, InfoBAXWorkflow, WorkflowConfig,
    minimum_initial_points_per_end,
)


class IntegratedInfoBAXWorker(WorkflowWorker):
    scan_requested = pyqtSignal(object)
    cancel_scan = pyqtSignal()
    frame = pyqtSignal(dict)

    def __init__(self, paras, variables, run_dir, *, plane, model, active_queries,
                 max_minutes, solenoid_settle, current_candidate_count=DEFAULT_CURRENT_CANDIDATES,
                 initial_points_per_end=DEFAULT_INITIAL_POINTS_PER_END,
                 resume=False, workflow=None, action="run", parent=None,recovery_context=None):
        super().__init__(run_dir=run_dir,workflow=workflow,resume=resume,action=action,parent=parent)
        self.paras,self.variables=paras,variables
        self.recovery_context=recovery_context
        self.arguments=dict(plane=plane,model=model,active_queries=active_queries,
                            current_candidate_count=current_candidate_count,initial_points_per_end=initial_points_per_end,
                            max_minutes=max_minutes,solenoid_settle=solenoid_settle)

    def full_scan(self,phase,completed,on_point):
        points=Queue()
        paras=self.workflow.machine.scan_parameters(self.workflow.run_dir/phase,completed,
                                                     points.put,self.workflow.control_lease)
        request={"paras":paras,"done":Event(),"result":None}
        self.scan_requested.emit(request)
        pending=None
        def drain():
            nonlocal pending
            while True:
                try: point=points.get_nowait()
                except Empty: break
                try: on_point(point)
                except Exception as exc:
                    if pending is None: pending=exc; self.cancel_scan.emit()
        while not request["done"].wait(.05):
            drain()
            if pending is None:
                try: self.workflow.check()
                except Exception as exc: pending=exc; self.cancel_scan.emit()
        drain()
        result=request["result"]
        if pending is not None and result.get("restored"):
            result.update(error=str(pending),fatal=True)
        return result

    def run(self):
        try:
            if self.action=="restore_archive":
                self.workflow=load_framework_recovery(self.recovery_context,self.run_dir)
                self.workflow.progress=self.progress.emit
                self.ready.emit(self.workflow)
                self.workflow.manual_move()
                return
            if self.action in {"apply","restore"}:
                self.workflow.manual_move(apply_verified=self.action=="apply")
                return
            with emittance_lock(self.paras.app_context.profile.machine.id,
                                self.paras.app_context.control_backend.name) as lease:
                self.workflow=build_framework_workflow(self.paras,self.variables,self.run_dir,
                    **self.arguments,automatic_measurements=True,progress=self.progress.emit,
                    full_scan=self.full_scan,resume=self.resume,lease=lease)
                self.workflow.machine.frame_progress=self.frame.emit
                if self.abort.is_set():
                    self.workflow.request_pause() if self.pause else self.workflow.cancelled.set()
                self.ready.emit(self.workflow)
                self.workflow.run()
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if self.workflow is not None:
                self.workflow.control_lease = None


class InfoBAXPage(QWidget):
    def __init__(self,dialog):
        super().__init__(dialog)
        self.dialog=dialog
        self.worker=self.workflow=self.scan=self.request=None
        self.saved=None
        self.read_only=False
        self.error=None
        box=QVBoxLayout(self)
        row=QHBoxLayout()
        row.addWidget(QLabel("Posterior"))
        self.model = QComboBox()
        self.model.addItem("transport_moments", "transport_moments")
        self.model.addItem("Direct variance GP (M0)", "M0")
        self.model.setItemData(self.model.findData("M0"),
                               "Gaussian process for beam-size variance (σ²) over solenoid currents and K1.",
                               Qt.ToolTipRole)
        row.addWidget(self.model)
        row.addWidget(QLabel("Active query limit"))
        self.active = QSpinBox()
        self.active.setRange(0, 2**31 - 1)
        self.active.setValue(3)
        self.active.setToolTip("Maximum additional single-point measurements after initialization, within the remaining query pool and run time budget.")
        row.addWidget(self.active)
        self.clear_button=QPushButton("Clear run / archive")
        self.clear_button.clicked.connect(self.clear_archive)
        row.addWidget(self.clear_button)
        box.addLayout(row)
        design_row = QHBoxLayout()
        self.current_candidate_count = QSpinBox()
        self.current_candidate_count.setRange(2, 99)
        self.current_candidate_count.setValue(DEFAULT_CURRENT_CANDIDATES)
        self.current_candidate_count.setToolTip("Number of candidate current settings, including the starting settings for all selected solenoids.")
        self.initial_points_per_end = QSpinBox()
        self.initial_points_per_end.setRange(minimum_initial_points_per_end(self.model.currentData()), 2**31 - 1)
        self.initial_points_per_end.setValue(DEFAULT_INITIAL_POINTS_PER_END)
        self.initial_points_per_end.setToolTip(
            "K1 points at each of the two most separated current candidates. "
            "Minimum: 1 for M0 or 3 for transport_moments. Maximum: the main-window K1 grid size."
        )
        for label, control in (("Current candidates", self.current_candidate_count),
                               ("Initial K1 points per end", self.initial_points_per_end)):
            caption = QLabel(label)
            caption.setBuddy(control)
            design_row.addWidget(caption)
            design_row.addWidget(control)
        design_row.addStretch()
        box.addLayout(design_row)
        self.note=QLabel();self.note.setWordWrap(True);box.addWidget(self.note)
        self.proposal=QLabel("Baseline: full scan → endpoint initialization → model-selected queries → full verification")
        self.proposal.setWordWrap(True);box.addWidget(self.proposal)
        self.posterior=QLabel("Best-workpoint probabilities: —");self.posterior.setWordWrap(True);box.addWidget(self.posterior)
        self.figure=Figure(figsize=(7,2.5),tight_layout=True)
        self.canvas=FigureCanvasQTAgg(self.figure);self.axes=self.figure.add_subplot(111)
        self.canvas.setMinimumHeight(140);box.addWidget(self.canvas)
        self.table=QTableWidget(0,6)
        self.table.setHorizontalHeaderLabels(["Stage","Current (A)","K1","Variance (mm²)","Repeatability SEM","State"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.cellDoubleClicked.connect(self.open_diagnostics)
        box.addWidget(self.table,1)
        self.model.currentIndexChanged.connect(self.update_initial_points_minimum)
        for signal in (self.model.currentIndexChanged,self.active.valueChanged,
                       self.current_candidate_count.valueChanged,self.initial_points_per_end.valueChanged):
            signal.connect(self.settings_changed)
        self.style_plot()
        self.refresh_note()

    def update_initial_points_minimum(self):
        self.initial_points_per_end.setMinimum(minimum_initial_points_per_end(self.model.currentData()))

    def style_plot(self):
        palette=self.dialog.host._palette()
        self.figure.set_facecolor(palette['plot_bg']);self.axes.set_facecolor(palette['plot_bg'])
        self.axes.tick_params(colors=palette['plot_text'],labelsize=8)
        for spine in self.axes.spines.values():spine.set_color(palette['plot_spine'])
        self.axes.xaxis.label.set_color(palette['plot_text']);self.axes.yaxis.label.set_color(palette['plot_text'])
        self.axes.grid(alpha=.25,color=palette['plot_grid'])

    def state(self):
        return self.workflow.payload() if self.workflow is not None else self.saved

    def busy(self):
        return self.worker is not None and self.worker.isRunning()

    def refresh_note(self):
        p=self.dialog.host.get_setting(show_warning=False)
        if self.dialog.info_active():
            self.dialog.measurement_note.setText("Main-window width method, energy, ROI/background and sampling are frozen at Start. InfoBAX uses its fixed candidate grid for baseline/verification.")
        if p is None:
            self.note.setText("Check the main-window measurement settings.")
        else:
            bounds=(p.adaptive_config.k1_min,p.adaptive_config.k1_max) if p.scan_strategy in {"adaptive","adaptive_quality"} else (p.k1_from,p.k1_end)
            self.note.setText(f"Main measurement: {p.quad_name} → {p.flag_name} · {p.beam_width_method} · {p.samples} frames/point · "
                f"{p.sample_interval:g} s interval · {p.k1_steps} K1 candidates over [{bounds[0]:g}, {bounds[1]:g}]. "
                "Timed sampling; independent new frames are unverified. At least 3 frames/point are required. "
                "Baseline and verification run as full scans.")

    def settings_changed(self,*args):
        if self.workflow is not None and not self.busy(): self.workflow.confirmed=False
        self.dialog.update_buttons()

    def start(self):
        d=self.dialog
        try:
            d.check_idle()
            if self.read_only: raise ValueError("Prototype v1 archives are read-only; start a new run after clearing the archive")
            if not d.selected_names():
                raise ValueError("Select at least one solenoid for InfoBAX")
            variables=d.selected_variables()
            paras=d.host.optimization_parameters(variables,defer_io=True)
            if paras.samples<3: raise ValueError("Set main-window Samples per point to at least 3 for InfoBAX")
            if self.initial_points_per_end.value()>paras.k1_steps:
                raise ValueError("Initial K1 points per end cannot exceed the number of K1 candidates")
            state=self.state()
            resume=bool(state and state["status"] in {"paused","recovery_required","restore_failed"})
            if state and state.get("restoration_status")=="failed" and self.workflow is not None:
                raise ValueError("Restore Initial before starting another run")
            root=resolve_app_runtime_paths(Path(__file__).parent,d.host.app_context)["runs_dir"]
            run_dir=(self.workflow.run_dir if self.workflow else Path(self.saved["run_dir"])) if resume else root/(
                "infobax_"+datetime.now().strftime("%Y%m%d_%H%M%S")+"_"+uuid4().hex[:8])
            if not resume: self.workflow=self.saved=None;self.table.setRowCount(0)
            self.paras=paras
            self.launch(IntegratedInfoBAXWorker(paras,variables,run_dir,plane=d.plane.currentData(),
                model=self.model.currentData(),active_queries=self.active.value(),max_minutes=d.minutes.value(),
                current_candidate_count=self.current_candidate_count.value(),initial_points_per_end=self.initial_points_per_end.value(),
                solenoid_settle=d.solenoid_settle.value(),resume=resume,parent=self))
        except Exception as exc: d.error(str(exc))

    def launch(self,worker):
        self.error=None;self.worker=worker
        self.dialog.lock_host()
        worker.ready.connect(self.ready);worker.progress.connect(self.show_progress)
        worker.scan_requested.connect(self.start_scan)
        worker.cancel_scan.connect(self.cancel_scan);worker.frame.connect(self.show_frame)
        worker.failed.connect(self.failed);worker.finished.connect(self.finished)
        worker.start();self.dialog.update_buttons()

    def ready(self,workflow):
        self.workflow=workflow;self.saved=None
        self.dialog.status.setText(f"InfoBAX · {workflow.run_dir}")

    def start_scan(self,request):
        self.request=request
        try:
            from .main import scanThread
            if self.worker.workflow.cancelled.is_set() or self.worker.workflow.pause_requested.is_set():
                request["result"]={"restored":True,"error":"Stopped before scan"}
                request["done"].set();self.request=None;return
            self.dialog.host.display({"clear":True,"preserve_beam_image":True})
            self.dialog.host._begin_scan_progress(request["paras"])
            self.scan=scanThread(request["paras"])
            self.scan.trigger.connect(self.show_frame)
            self.scan.finished.connect(self.scan_finished)
            self.scan.start()
        except Exception as exc:
            request["result"]={"restored":True,"error":str(exc),"fatal":True}
            request["done"].set();self.scan=self.request=None

    def show_frame(self, payload):
        if payload.get("error"):
            self.dialog.status.setText(f"InfoBAX measurement: {payload['error']}")
            return
        self.dialog.host.display(payload)

    def scan_finished(self):
        self.request["result"]=deepcopy(self.scan.terminal_result)
        self.request["done"].set()
        self.scan.deleteLater();self.scan=self.request=None

    def cancel_scan(self):
        if self.scan is not None: self.scan.stop()

    def stop(self,pause=False):
        if self.busy(): self.worker.stop(pause=pause);self.cancel_scan()

    def format_current(self, current, names):
        if isinstance(current, (list, tuple)):
            return self.dialog.format_currents(dict(zip(names, current, strict=True)))
        return f"{current:g} A"

    def show_progress(self,payload):
        self.render()
        request = payload.get("request")
        if request:
            names = json.loads(self.workflow.config.settings_json).get("variables", ())
            current = self.format_current(request["current_a"], names)
            details = (f"K1={request['scan_k1']:g}" if "scan_k1" in request
                       else f"{request['remaining_points']} K1 points")
            self.proposal.setText(f"{request['phase']} · {current} · {details}")
        else:
            self.proposal.setText(f"InfoBAX · {payload.get('status')} · {payload.get('record_count',0)} point records")

    def render(self):
        state=self.state()
        if not state:return
        names = json.loads(state["config"]["settings_json"]).get("variables", ())
        records=list(state["records"])
        self.table.setRowCount(len(records));x=[];y=[]
        for i,r in enumerate(records):
            m=r.get("measurement",{})
            current = self.format_current(r["current_a"], names) if names else r["current_a"]
            values=(r["phase"],current,r["scan_k1"],m.get("variance_mm2","—"),m.get("noise_std_mm2","—"),r["state"])
            for j,v in enumerate(values):self.table.setItem(i,j,QTableWidgetItem(f"{v:.6g}" if isinstance(v,float) else str(v)))
            if names:
                self.table.item(i, 1).setToolTip(current)
            if m:x.append(i);y.append(m["variance_mm2"])
        self.axes.clear();self.axes.plot(x,y,".-");self.axes.set(xlabel="Measurement",ylabel="Variance (mm²)")
        self.style_plot();self.canvas.draw_idle()
        r=state.get("recommendation")
        if r:
            items=[f"{self.format_current(current, names)}: {prob:.1%}" for current,prob in zip(state['candidates'],r['best_probabilities'])]
            self.posterior.setText("P(best) · "+" | ".join(items)+f" · INVALID: {r['invalid_output_probability']:.1%}")
        else:self.posterior.setText("Best-workpoint probabilities: —")
        baseline=state.get("baseline_fit") or {};verify=state.get("verification_fit") or {}
        self.dialog.progress_bar.setRange(0,len(state.get("candidates",[]))*len(state["config"]["scan_values"]))
        self.dialog.progress_bar.setValue(sum(r["state"]=="observed" for r in records))
        self.dialog.progress_bar.setFormat("%v point observations · grid %m")
        self.dialog.status.setText(f"InfoBAX · {state['status']} · baseline εn={baseline.get('normalized_emittance_mm_mrad','—')} · "
            f"verified εn={verify.get('normalized_emittance_mm_mrad','—')} · restore={state['restoration_status']} · {state.get('error') or ''}")

    def failed(self,message):
        self.error=message
        if self.workflow:self.workflow.confirmed=False

    def finished(self):
        self.render()
        state=self.state()
        self.dialog.fault=bool(state and state["restoration_status"] in {"failed","pending"})
        if not self.dialog.fault:self.dialog.unlock_host()
        if self.error:self.dialog.status.setText(self.error)
        self.dialog.update_buttons()
        if self.dialog.close_pending and not self.dialog.fault:self.dialog.close()

    def manual(self,action):
        try:
            self.dialog.check_idle()
            if self.workflow is None:
                if action=="initial" and self.saved is not None and not self.read_only:
                    self.launch(IntegratedInfoBAXWorker(None,None,Path(self.saved["run_dir"]),
                        plane="x",model="transport_moments",active_queries=0,max_minutes=120,solenoid_settle=0,
                        action="restore_archive",parent=self,recovery_context=self.dialog.host.app_context))
                    return
                raise ValueError("No executable session is loaded")
            if self.workflow.machine.context is not self.dialog.host.app_context:raise ValueError("Runtime context changed")
            if action=="best" and QMessageBox.question(self,"Apply verified result","Apply the independently verified improvement?")!=QMessageBox.Yes:return
            self.launch(IntegratedInfoBAXWorker(None,None,self.workflow.run_dir,plane=self.workflow.config.plane,
                model=self.workflow.config.model,active_queries=0,max_minutes=120,solenoid_settle=0,
                workflow=self.workflow,action="apply" if action=="best" else "restore",parent=self))
        except Exception as exc:self.dialog.error(str(exc))

    def load_run(self):
        root=resolve_app_runtime_paths(Path(__file__).parent,self.dialog.host.app_context)["runs_dir"]
        path,_=QFileDialog.getOpenFileName(self,"Open InfoBAX run",str(root),"InfoBAX (infobax_workflow.json);;JSON (*.json)")
        if not path:return
        try:
            data=json.loads(Path(path).read_text())
            if data.get("schema") not in {"half_infobax_workflow_v1","half_infobax_workflow_v2"}:raise ValueError("Unsupported InfoBAX archive")
            self.workflow=None;self.saved=dict(data,run_dir=str(Path(path).parent))
            self.read_only=data["schema"]!="half_infobax_workflow_v2"
            if not self.read_only and (data["restoration_status"] in {"pending","failed"}
                    or any(a["state"]=="pending" for a in data.get("actions",[]))):
                self.saved["status"]="recovery_required"
            if not self.read_only:
                cfg=data["config"];settings=json.loads(cfg["settings_json"])
                if "measurement_contract" not in settings:self.read_only=True
                else:
                    d=self.dialog
                    variables = {variable.element_id: variable for variable in archived_variables(WorkflowConfig(**cfg), settings)}
                    for name,row in d.variable_rows.items():
                        item=d.variables.item(row,0)
                        item.setCheckState(2 if name in variables else 0)
                        if name in variables:
                            d.variables.item(row,3).setText(str(variables[name].low))
                            d.variables.item(row,4).setText(str(variables[name].high))
                    d.plane.setCurrentIndex(d.plane.findData(cfg["plane"]));d.minutes.setValue(cfg["max_minutes"])
                    d.solenoid_settle.setValue(settings["solenoid_settle"])
                    self.model.setCurrentIndex(self.model.findData(cfg["model"]))
                    self.active.setValue(cfg["active_queries"])
                    self.current_candidate_count.setValue(cfg.get("current_candidate_count", DEFAULT_CURRENT_CANDIDATES))
                    self.initial_points_per_end.setValue(cfg.get("initial_points_per_end", DEFAULT_INITIAL_POINTS_PER_END))
            self.render()
            if self.read_only:self.proposal.setText("Read-only prototype archive. Clear it to start a new framework run.")
            elif self.saved["status"]=="recovery_required":self.proposal.setText("Restore Initial before resuming; recovery does not require a working camera.")
            else:self.proposal.setText("Match the main-window measurement settings before resuming this run.")
            self.dialog.update_buttons()
        except Exception as exc:self.dialog.error(str(exc))

    def clear_archive(self):
        if not self.busy() and not self.dialog.fault:
            self.workflow=self.saved=None;self.read_only=False;self.table.setRowCount(0)
            self.axes.clear();self.style_plot();self.canvas.draw_idle()
            self.posterior.setText("Best-workpoint probabilities: —")
            self.proposal.setText("Baseline → initialization → model-selected queries → full verification")
            self.dialog.status.setText("InfoBAX ready.")
            self.dialog.progress_bar.setRange(0,1);self.dialog.progress_bar.setValue(0)
            self.dialog.progress_bar.setFormat("0 point observations")
            self.dialog.update_buttons()

    def open_diagnostics(self,row,column):
        state=self.state()
        if not state or not 0<=row<len(state["records"]):return
        record=state["records"][row]
        metadata=(record.get("measurement") or {}).get("metadata",{})
        QMessageBox.information(self,"InfoBAX measurement",json.dumps(metadata,ensure_ascii=False,indent=2))
