import sys
from collections import deque
from math import isfinite
from pathlib import Path

_REPO_BOOTSTRAP_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "repo_bootstrap.py").is_file())
if str(_REPO_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_BOOTSTRAP_ROOT))
from repo_bootstrap import ensure_repo_import_path
ensure_repo_import_path(__file__)

from epics import caget_many
from matplotlib.backends.backend_qt5agg import FigureCanvas
from matplotlib.figure import Figure
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QPalette
from PyQt5.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QMainWindow, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget)
from half_linac.src.shared.machine_profile import list_elements, load_app_context, resolve_channel
from half_linac.src.shared.window_activation import install_qt_window_raise_handler

MAX_HISTORY = 150
DEFAULT_DETAIL_PALETTE = {
    "window_bg":"#0f1519","window_fg":"#e6edf2","panel_bg":"#172027","panel_border":"#24333d",
    "summary_bg":"#1b262d","summary_border":"#2b3a45","summary_title_fg":"#f3efe3","muted_fg":"#90a1ad",
    "input_bg":"#10171c","input_border":"#31424d","input_fg":"#edf3f7","plot_card_bg":"#121a20",
    "plot_bg":"#11181e","plot_grid":"#2a3943","plot_spine":"#445764","plot_text":"#d7e2ea",
    "status_bg":"#11191f","status_fg":"#c9d5dc","metric_active_fg":"#45d0bc","metric_warning_fg":"#e4b86f","orbit_x":"#6cb6ff"}

def build_bpm_detail_theme(palette):
    return """QMainWindow, QWidget#bpmDetailCentral {{ background-color:{window_bg}; color:{window_fg}; font-family:"IBM Plex Sans","Source Han Sans SC","Segoe UI",sans-serif; }}
QFrame#detailHeader,QFrame#detailCard {{ background-color:{summary_bg}; border:1px solid {summary_border}; border-radius:12px; }}
QFrame#detailCard {{ background-color:{panel_bg}; border-color:{panel_border}; }}
QLabel#detailTitle {{ background:transparent; color:{summary_title_fg}; border:none; font-size:20px; font-weight:700; }}
QLabel#detailCardTitle {{ background:transparent; color:{summary_title_fg}; border:none; font-size:15px; font-weight:700; }}
QLabel[role="meta"] {{ background:transparent; color:{muted_fg}; border:none; font-size:11px; font-weight:600; }}
QLabel#detailConnection {{ background:transparent; border:none; font-size:12px; font-weight:700; }}
QLabel#detailConnection[status="live"] {{ color:{metric_active_fg}; }} QLabel#detailConnection[status="warning"] {{ color:{metric_warning_fg}; }}
QTableWidget#bpmTable {{ background-color:{input_bg}; alternate-background-color:{panel_bg}; color:{input_fg}; border:1px solid {input_border}; border-radius:8px; gridline-color:{panel_border}; selection-background-color:{summary_bg}; selection-color:{input_fg}; outline:none; padding:3px; }}
QTableWidget#bpmTable::item {{ border:none; padding:6px 10px; }}
QHeaderView::section {{ background-color:{summary_bg}; color:{muted_fg}; border:none; border-bottom:1px solid {input_border}; padding:8px 10px; font-size:11px; font-weight:700; }}
QPushButton,QComboBox {{ background:{input_bg}; color:{input_fg}; border:1px solid {input_border}; border-radius:6px; padding:4px 8px; }}
QPushButton:hover,QComboBox:hover {{ background:{summary_bg}; }}
QComboBox QAbstractItemView {{ background:{input_bg}; color:{input_fg}; selection-background-color:{summary_bg}; selection-color:{input_fg}; border:1px solid {input_border}; }}
QStatusBar {{ background-color:{status_bg}; color:{status_fg}; }}""".format_map(palette)

class myWindow(QMainWindow):
    def __init__(self, refresh_interval_ms=1000, palette=None, parent=None):
        super().__init__(parent); install_qt_window_raise_handler(self)
        self.app_context=load_app_context("orbit_display"); self.machine_profile=self.app_context.profile
        self.control_backend=self.app_context.control_backend.name; self.refresh_interval_ms=max(100,int(refresh_interval_ms))
        self.bpm_elements=list_elements(self.app_context,kind="bpm"); self.bpm_ids=[e.id for e in self.bpm_elements]
        self.bpm_position_scale_to_mm=self.machine_profile.machine.bpm_scale_to_mm(self.control_backend)
        self.bpm_x_pvs=[resolve_channel(self.app_context,i,"x") for i in self.bpm_ids]
        self.bpm_y_pvs=[resolve_channel(self.app_context,i,"y") for i in self.bpm_ids]
        self.bpm_sum_pvs=[self._resolve_optional_channel(self.app_context,i,"sum") for i in self.bpm_ids]
        self.pvlx_val=[None]*len(self.bpm_ids); self.pvly_val=[None]*len(self.bpm_ids); self.pvsum_val=[None]*len(self.bpm_ids)
        self.baselines=[None]*len(self.bpm_ids); self.history=[deque(maxlen=MAX_HISTORY) for _ in self.bpm_ids]; self.selected_index=0
        self._current_palette=dict(palette or DEFAULT_DETAIL_PALETTE); self._build_ui(); self._configure_bpm_table(); self.apply_theme(self._current_palette)
        self.timer=QTimer(self); self.timer.timeout.connect(self.bpmvalue_dis); self.timer.start(self.refresh_interval_ms); self.bpmvalue_dis()

    def _build_ui(self):
        self.setWindowTitle(f"{self.machine_profile.machine.display_name} BPM Signal Monitor"); self.resize(850,760); self.setMinimumSize(650,560)
        central=QWidget(self); central.setObjectName("bpmDetailCentral"); self.setCentralWidget(central); outer=QVBoxLayout(central); outer.setContentsMargins(10,10,10,10); outer.setSpacing(10)
        header=QFrame(central); header.setObjectName("detailHeader"); hl=QHBoxLayout(header); hl.setContentsMargins(14,10,14,10)
        title=QLabel(f"{self.machine_profile.machine.display_name} BPM Signal Monitor",header); title.setObjectName("detailTitle"); hl.addWidget(title); hl.addStretch(1)
        self.backend_label=QLabel(f"Backend: {self.control_backend.upper()}",header); self.refresh_label=QLabel(header)
        for label in (self.backend_label,self.refresh_label): label.setProperty("role","meta"); hl.addWidget(label)
        outer.addWidget(header)
        controls=QFrame(central); controls.setObjectName("detailHeader"); cl=QHBoxLayout(controls); cl.setContentsMargins(10,6,10,6); cl.addWidget(QLabel("Display",controls))
        self.mode_combo=QComboBox(controls); self.mode_combo.addItems(("Raw","Normalized")); self.mode_combo.currentIndexChanged.connect(self._redraw_history); cl.addWidget(self.mode_combo)
        self.set_baseline_button=QPushButton("Set All Baselines",controls); self.set_baseline_button.clicked.connect(self._set_all_baselines); cl.addWidget(self.set_baseline_button)
        self.clear_baseline_button=QPushButton("Clear All Baselines",controls); self.clear_baseline_button.clicked.connect(self._clear_all_baselines); cl.addWidget(self.clear_baseline_button)
        self.clear_history_button=QPushButton("Clear History",controls); self.clear_history_button.clicked.connect(self._clear_history); cl.addWidget(self.clear_history_button); cl.addStretch(1); outer.addWidget(controls)
        card=QFrame(central); card.setObjectName("detailCard"); cardl=QVBoxLayout(card); cardl.setContentsMargins(10,10,10,10); cardl.setSpacing(8); ch=QHBoxLayout()
        ct=QLabel("BPM Signals",card); ct.setObjectName("detailCardTitle"); self.connection_label=QLabel("Waiting",card); self.connection_label.setObjectName("detailConnection"); self.connection_label.setProperty("status","warning"); ch.addWidget(ct); ch.addStretch(1); ch.addWidget(self.connection_label); cardl.addLayout(ch)
        self.bpm_table=QTableWidget(card); self.bpm_table.setObjectName("bpmTable"); self.bpm_table.setColumnCount(6); self.bpm_table.setHorizontalHeaderLabels(("BPM","Horizontal X (mm)","Vertical Y (mm)","Current Sum","Baseline","Relative")); self.bpm_table.setEditTriggers(QAbstractItemView.NoEditTriggers); self.bpm_table.setSelectionBehavior(QAbstractItemView.SelectRows); self.bpm_table.setSelectionMode(QAbstractItemView.SingleSelection); self.bpm_table.setAlternatingRowColors(True); self.bpm_table.setShowGrid(False); self.bpm_table.verticalHeader().setVisible(False); self.bpm_table.verticalHeader().setDefaultSectionSize(32)
        hv=self.bpm_table.horizontalHeader(); hv.setSectionResizeMode(0,QHeaderView.ResizeToContents)
        for c in (1,2,3,4,5): hv.setSectionResizeMode(c,QHeaderView.Stretch)
        self.bpm_table.itemSelectionChanged.connect(self._select_bpm); cardl.addWidget(self.bpm_table,3); outer.addWidget(card,2)
        self.history_canvas=FigureCanvas(Figure(figsize=(8,2.8))); self.history_ax=self.history_canvas.figure.add_subplot(111); outer.addWidget(self.history_canvas,2); self.statusBar().setSizeGripEnabled(False); self._update_refresh_label()

    def _configure_bpm_table(self):
        self.bpm_table.setRowCount(len(self.bpm_ids)); self.x_value_widgets=[]; self.y_value_widgets=[]; self.sum_value_widgets=[]; self.baseline_widgets=[]; self.relative_widgets=[]
        for row,bpm_id in enumerate(self.bpm_ids):
            items=[QTableWidgetItem(bpm_id),QTableWidgetItem("--"),QTableWidgetItem("--"),QTableWidgetItem("--"),QTableWidgetItem("--"),QTableWidgetItem("--")]
            items[0].setTextAlignment(Qt.AlignLeft|Qt.AlignVCenter)
            for item in items[1:]: item.setTextAlignment(Qt.AlignRight|Qt.AlignVCenter)
            for col,item in enumerate(items): self.bpm_table.setItem(row,col,item)
            self.x_value_widgets.append(items[1]); self.y_value_widgets.append(items[2]); self.sum_value_widgets.append(items[3]); self.baseline_widgets.append(items[4]); self.relative_widgets.append(items[5])
        if self.bpm_ids: self.bpm_table.selectRow(0)

    def apply_theme(self,palette):
        self._current_palette=dict(palette); self.setStyleSheet(build_bpm_detail_theme(self._current_palette))
        base=QColor(self._current_palette["input_bg"]); alternate=QColor(self._current_palette["panel_bg"]); text_color=QColor(self._current_palette["input_fg"]); selected=QColor(self._current_palette["summary_bg"])
        table_palette=self.bpm_table.palette(); table_palette.setColor(QPalette.Base,base); table_palette.setColor(QPalette.AlternateBase,alternate); table_palette.setColor(QPalette.Window,base); table_palette.setColor(QPalette.Text,text_color); table_palette.setColor(QPalette.Highlight,selected); table_palette.setColor(QPalette.HighlightedText,text_color); self.bpm_table.setPalette(table_palette); self.bpm_table.setAutoFillBackground(True)
        header_palette=self.bpm_table.horizontalHeader().palette(); header_palette.setColor(QPalette.Button,selected); header_palette.setColor(QPalette.ButtonText,QColor(self._current_palette["muted_fg"])); header_palette.setColor(QPalette.Window,selected); self.bpm_table.horizontalHeader().setPalette(header_palette)
        combo_palette=self.mode_combo.palette(); combo_palette.setColor(QPalette.Base,base); combo_palette.setColor(QPalette.Button,base); combo_palette.setColor(QPalette.Text,text_color); combo_palette.setColor(QPalette.ButtonText,text_color); combo_palette.setColor(QPalette.Highlight,selected); combo_palette.setColor(QPalette.HighlightedText,text_color); self.mode_combo.setPalette(combo_palette)
        popup_palette=self.mode_combo.view().palette(); popup_palette.setColor(QPalette.Base,base); popup_palette.setColor(QPalette.Window,base); popup_palette.setColor(QPalette.Text,text_color); popup_palette.setColor(QPalette.Highlight,selected); popup_palette.setColor(QPalette.HighlightedText,text_color); self.mode_combo.view().setPalette(popup_palette)
        text=QColor(self._current_palette["window_fg"]); accent=QColor(self._current_palette.get("orbit_x","#6cb6ff"))
        for row in range(self.bpm_table.rowCount()):
            row_background=alternate if row % 2 else base
            for col in range(self.bpm_table.columnCount()):
                item=self.bpm_table.item(row,col)
                item.setBackground(row_background)
                item.setForeground(text if col == 0 else accent)
        self._redraw_history(); self._refresh_connection_style()

    def set_refresh_interval_ms(self,value): self.refresh_interval_ms=max(100,int(value)); self.timer.start(self.refresh_interval_ms); self._update_refresh_label()
    def _update_refresh_label(self): self.refresh_label.setText(f"Refresh: {self.refresh_interval_ms/1000:g} s")
    @staticmethod
    def _resolve_optional_channel(ctx,bpm_id,logical):
        try: return resolve_channel(ctx,bpm_id,logical)
        except (KeyError,ValueError): return None
    @staticmethod
    def _number(value):
        try: value=float(value)
        except (TypeError,ValueError,OverflowError): return None
        return value if isfinite(value) else None
    @classmethod
    def _format_value(cls,value):
        value=cls._number(value); return "--" if value is None else f"{value:.6g}"
    def _select_bpm(self):
        rows=self.bpm_table.selectionModel().selectedRows()
        if rows: self.selected_index=rows[0].row(); self._redraw_history()
    def _set_all_baselines(self): self.baselines=[self._number(v) for v in self.pvsum_val]; self._update_table(); self._redraw_history()
    def _clear_all_baselines(self): self.baselines=[None]*len(self.bpm_ids); self._update_table(); self._redraw_history()
    def _clear_history(self):
        for values in self.history: values.clear()
        self._redraw_history()
    def _format_position(self, value):
        value=self._number(value)
        if value is None: return "--"
        scaled=value*self.bpm_position_scale_to_mm
        return f"{scaled:.3f}" if isfinite(scaled) else "--"

    def _update_table(self):
        for i in range(len(self.bpm_ids)):
            current=self._number(self.pvsum_val[i]); baseline=self.baselines[i]; relative=current/baseline if current is not None and baseline not in (None,0) else None
            self.x_value_widgets[i].setText(self._format_position(self.pvlx_val[i]))
            self.y_value_widgets[i].setText(self._format_position(self.pvly_val[i]))
            self.sum_value_widgets[i].setText(self._format_value(current)); self.baseline_widgets[i].setText(self._format_value(baseline)); self.relative_widgets[i].setText(self._format_value(relative))

    @staticmethod
    def _read_values(pvs):
        if not pvs: return []
        try:
            values=caget_many(pvs)
            values=[] if values is None else list(values)
        except Exception:
            values=[]
        return values[:len(pvs)]+[None]*max(0,len(pvs)-len(values))

    def bpmvalue_dis(self):
        self.pvlx_val=self._read_values(self.bpm_x_pvs)
        self.pvly_val=self._read_values(self.bpm_y_pvs)
        sum_values=self._read_values([pv for pv in self.bpm_sum_pvs if pv is not None])
        cursor=0; self.pvsum_val=[]
        for pv in self.bpm_sum_pvs:
            self.pvsum_val.append(sum_values[cursor] if pv is not None and cursor < len(sum_values) else None)
            if pv is not None: cursor+=1
        for i,value in enumerate(self.pvsum_val): self.history[i].append(self._number(value))
        xy_live=any(self._number(v) is not None for v in self.pvlx_val+self.pvly_val)
        sum_live=any(self._number(v) is not None for v in self.pvsum_val)
        if sum_live:
            connection_text, connection_status = "Live", "live"
        elif xy_live:
            connection_text, connection_status = "X/Y Live · Sum unavailable", "warning"
        else:
            connection_text, connection_status = "No data", "warning"
        self._set_connection_status(connection_text, connection_status)
        if xy_live or sum_live: self.statusBar().clearMessage()
        self._update_table(); self._redraw_history()
    def _redraw_history(self):
        if not hasattr(self,"history_ax"): return
        p=self._current_palette; ax=self.history_ax; ax.clear(); ax.set_facecolor(p.get("plot_bg",p["input_bg"])); ax.tick_params(colors=p.get("plot_text",p["window_fg"])); ax.grid(True,color=p.get("plot_grid",p["panel_border"]),linewidth=.6)
        for spine in ax.spines.values(): spine.set_edgecolor(p.get("plot_spine",p["input_border"]))
        i=self.selected_index if self.bpm_ids else -1; values=list(self.history[i]) if i>=0 else []
        if self.mode_combo.currentText()=="Normalized" and i>=0:
            baseline=self.baselines[i]; values=[v/baseline if v is not None and baseline not in (None,0) else None for v in values]
        valid=[(n,v) for n,v in enumerate(values) if v is not None]
        if valid: ax.plot([n for n,_ in valid],[v for _,v in valid],color=p.get("orbit_x","#6cb6ff"),linewidth=1.6,marker="o",markersize=3)
        name=self.bpm_ids[i] if i>=0 else "--"; unit="relative" if self.mode_combo.currentText()=="Normalized" else "a.u."; color=p.get("plot_text",p["window_fg"])
        ax.set_title(f"{name} Sum history",color=color,loc="left",fontweight="bold"); ax.set_xlabel("Sample",color=color); ax.set_ylabel(unit,color=color); self.history_canvas.figure.set_facecolor(p.get("plot_card_bg",p["panel_bg"])); self.history_canvas.draw_idle()
    def _set_connection_status(self,text,status): self.connection_label.setText(text); self.connection_label.setProperty("status",status); self._refresh_connection_style()
    def _refresh_connection_style(self): self.connection_label.style().unpolish(self.connection_label); self.connection_label.style().polish(self.connection_label); self.connection_label.update()
    def closeEvent(self,event): self.timer.stop(); super().closeEvent(event)

if __name__=="__main__":
    app=QApplication(sys.argv); window=myWindow(); window.show(); sys.exit(app.exec_())
