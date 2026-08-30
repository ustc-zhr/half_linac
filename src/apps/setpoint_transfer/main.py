from __future__ import annotations

import math
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

_ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "repo_bootstrap.py").is_file()
)
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

import epics
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QDoubleValidator
from PyQt5.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from half_linac.src.shared.machine_profile import (
    CONTROL_BACKEND_ENV,
    LEGACY_CONTROL_BACKEND_ENV,
    MachineProfileError,
    RuntimeContextWidget,
    load_profile,
    normalize_mode,
    resolve_virtual_machine_usedline_workflow,
    resolve_machine_runtime,
)
from half_linac.src.shared.app_theme import resolve_initial_theme
from half_linac.src.shared.setpoint_transfer import (
    backend_capabilities,
    StagedSetpoint,
    TransferPlan,
    build_transfer_plan,
    extract_design_setpoints,
    load_target_workspace,
    save_target_workspace,
)
from half_linac.src.shared.twiss_preview import TwissPreviewResult, build_twiss_preview
from half_linac.src.apps.setpoint_transfer.execution import (
    RestoreExecutionError,
    TransferExecutionError,
    append_execution_log,
    execute_transfer_plan,
    execute_restore,
    find_restore_conflicts,
    save_transfer_transaction,
)


LARGE_CHANGE_THRESHOLD = 1.0


def _theme_palette(theme: str | None = None) -> dict[str, str]:
    dark = (theme or resolve_initial_theme()) == "dark"
    return {
        "window": "#0f1519" if dark else "#f2ede5",
        "panel": "#172027" if dark else "#fffdf9",
        "input": "#10171c" if dark else "#fffdf8",
        "alternate": "#131c22" if dark else "#f7f1e8",
        "border": "#2a3943" if dark else "#d7cec1",
        "button": "#22313a" if dark else "#eee5d8",
        "button_hover": "#2b3f4b" if dark else "#e4d8c8",
        "button_pressed": "#19262e" if dark else "#d8c9b6",
        "text": "#e6edf2" if dark else "#2c3942",
        "muted": "#91a2ad" if dark else "#746c62",
        "accent": "#45d0bc" if dark else "#2d7f6d",
        "warning": "#e4b86f" if dark else "#a97118",
        "plot": "#11191f" if dark else "#fffdf9",
        "grid": "#40515c" if dark else "#d7cec1",
    }


def _build_stylesheet(palette: dict[str, str]) -> str:
    return f"""
QMainWindow, QDialog, QWidget#centralRoot {{
  background: {palette['window']}; color: {palette['text']};
  font-family: "IBM Plex Sans", "Source Han Sans SC", "Segoe UI", sans-serif;
  font-size: 12px;
}}
QLabel {{ background: transparent; color: {palette['text']}; }}
QLabel#windowTitle {{ color: {palette['text']}; font-size: 20px; font-weight: 700; }}
QLabel[role="field"] {{ color: {palette['muted']}; font-size: 11px; font-weight: 600; }}
QLabel[role="meta"] {{ color: {palette['muted']}; font-size: 11px; font-weight: 600; }}
QLabel[role="status"] {{
  background: {palette['alternate']}; color: {palette['muted']};
  border: 1px solid {palette['border']}; border-radius: 7px; padding: 5px 9px;
}}
QLineEdit, QComboBox {{
  background: {palette['input']}; color: {palette['text']};
  border: 1px solid {palette['border']}; border-radius: 8px;
  min-height: 20px; padding: 4px 8px;
}}
QLineEdit:focus, QComboBox:focus {{ border-color: {palette['accent']}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{
  background: {palette['input']}; color: {palette['text']};
  border: 1px solid {palette['border']};
  selection-background-color: {palette['button_hover']};
}}
QTableWidget {{
  background: {palette['input']}; alternate-background-color: {palette['panel']};
  color: {palette['text']}; border: 1px solid {palette['border']};
  border-radius: 8px; gridline-color: {palette['border']}; outline: 0;
}}
QTableWidget::item {{ padding: 2px 5px; }}
QTableWidget::item:selected {{ background: {palette['button_hover']}; color: {palette['text']}; }}
QHeaderView::section {{
  background: {palette['button']}; color: {palette['muted']}; border: none;
  border-right: 1px solid {palette['border']};
  border-bottom: 1px solid {palette['border']}; padding: 5px 7px;
  font-size: 11px; font-weight: 700;
}}
QPushButton, QToolButton {{
  background: {palette['button']}; color: {palette['text']};
  border: 1px solid {palette['border']}; border-radius: 8px;
  min-height: 28px; padding: 2px 11px; font-size: 11px; font-weight: 700;
}}
QPushButton:hover, QToolButton:hover {{ background: {palette['button_hover']}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {palette['button_pressed']}; }}
QPushButton[role="primary"] {{
  background: {palette['accent']}; border-color: {palette['accent']};
  color: {palette['window']};
}}
QPushButton:disabled, QToolButton:disabled {{
  background: {palette['alternate']}; color: {palette['muted']};
  border-color: {palette['border']};
}}
QCheckBox {{ background: transparent; border: none; spacing: 6px; }}
QCheckBox::indicator {{
  width: 15px; height: 15px; border: 1px solid {palette['border']};
  border-radius: 4px; background: {palette['input']};
}}
QCheckBox::indicator:hover {{ border-color: {palette['accent']}; }}
QCheckBox::indicator:checked {{
  background: {palette['accent']}; border-color: {palette['accent']};
}}
QCheckBox::indicator:disabled {{ background: {palette['alternate']}; }}
QTabWidget::pane {{
  background: {palette['panel']}; border: 1px solid {palette['border']};
  border-radius: 8px; top: -1px;
}}
QTabBar::tab {{
  background: {palette['button']}; color: {palette['muted']};
  border: 1px solid {palette['border']}; padding: 6px 14px; margin-right: 4px;
  border-top-left-radius: 8px; border-top-right-radius: 8px; font-weight: 700;
}}
QTabBar::tab:selected {{
  background: {palette['panel']}; color: {palette['text']};
  border-bottom-color: {palette['panel']};
}}
QMenu {{
  background: {palette['panel']}; color: {palette['text']};
  border: 1px solid {palette['border']}; padding: 4px;
}}
QMenu::item {{ padding: 5px 18px; border-radius: 5px; }}
QMenu::item:selected {{ background: {palette['button_hover']}; }}
QToolBar {{ background: transparent; border: none; spacing: 2px; }}
QToolBar QToolButton {{ min-width: 24px; padding: 1px; }}
QToolButton#themeToggleButton {{
  background: {palette['button']}; color: {palette['text']};
  border: 1px solid {palette['border']}; border-radius: 11px;
  min-width: 32px; max-width: 32px; min-height: 32px; max-height: 32px;
  padding: 0px; font-size: 14px; font-weight: 700;
}}
QToolButton#themeToggleButton:hover {{ background: {palette['button_hover']}; }}
QToolButton#themeToggleButton:pressed {{ background: {palette['button_pressed']}; }}
QDialogButtonBox QPushButton:default {{
  background: {palette['accent']}; border-color: {palette['accent']};
  color: {palette['window']};
}}
"""


class EpicsPvClient:
    def read(self, pv_name: str) -> float:
        pv = epics.PV(pv_name)
        if not pv.wait_for_connection(timeout=2.0):
            raise RuntimeError(f"PV connection failed: {pv_name}")
        return float(pv.get(timeout=2.0))

    def write(self, pv_name: str, value: float) -> None:
        pv = epics.PV(pv_name)
        if not pv.wait_for_connection(timeout=2.0):
            raise RuntimeError(f"PV connection failed: {pv_name}")
        if not pv.put(float(value), wait=True, timeout=2.0):
            raise RuntimeError(f"PV write failed: {pv_name}")

    def read_many(self, pv_names: list[str]) -> list[float | None]:
        values = epics.caget_many(pv_names, timeout=2.0)
        result: list[float | None] = []
        for value in values:
            try:
                result.append(float(value))
            except (TypeError, ValueError):
                result.append(None)
        return result


class VmTransferWorker(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str, object, str)

    def __init__(self, plan: TransferPlan, parent=None):
        super().__init__(parent)
        self.plan = plan

    def run(self):
        try:
            result = execute_transfer_plan(self.plan, EpicsPvClient())
            self.completed.emit(result)
        except TransferExecutionError as exc:
            self.failed.emit(str(exc), exc.completed, exc.failed_element_id)
        except Exception as exc:
            self.failed.emit(str(exc), (), "")


class RestoreWorker(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str, object, str)

    def __init__(self, result, parent=None):
        super().__init__(parent)
        self.result = tuple(result)

    def run(self):
        try:
            self.completed.emit(execute_restore(self.result, EpicsPvClient()))
        except RestoreExecutionError as exc:
            self.failed.emit(str(exc), exc.completed, exc.failed_element_id)
        except Exception as exc:
            self.failed.emit(str(exc), (), "")


class RestoreCheckWorker(QThread):
    completed = pyqtSignal(object)

    def __init__(self, result, parent=None):
        super().__init__(parent)
        self.result = tuple(result)

    def run(self):
        self.completed.emit(find_restore_conflicts(self.result, EpicsPvClient()))


class VmPreviewWorker(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, profile, design_setpoints, control_backend="vm", parent=None):
        super().__init__(parent)
        self.profile = profile
        self.design_setpoints = design_setpoints
        self.control_backend = control_backend

    def run(self):
        try:
            from half_linac.src.shared.machine_profile import resolve_write_target

            client = EpicsPvClient()
            current: dict[str, float | None] = {}
            resolved: list[tuple[str, str]] = []
            for setpoint in self.design_setpoints:
                try:
                    target = resolve_write_target(
                        self.profile,
                        setpoint.element_id,
                        quantity="K1",
                        mode=self.control_backend,
                    )
                    resolved.append((setpoint.element_id, target.pv_name))
                except Exception:
                    current[setpoint.element_id] = None
            values = client.read_many([pv_name for _element_id, pv_name in resolved])
            for index, value in enumerate(values):
                if value is not None:
                    continue
                try:
                    values[index] = client.read(resolved[index][1])
                except Exception:
                    pass
            current.update(
                (element_id, value)
                for (element_id, _pv_name), value in zip(resolved, values)
            )
            self.completed.emit(current)
        except Exception as exc:
            self.failed.emit(str(exc))


def _select_twiss_line(
    element_ids: set[str],
    line_elements: dict[str, set[str]],
    default_line: str,
) -> str:
    candidates = [
        line_name
        for line_name, members in line_elements.items()
        if element_ids <= members
    ]
    if default_line in candidates:
        return default_line
    if candidates:
        return candidates[0]
    raise ValueError(
        "Staged Target values span model branches that cannot be previewed "
        "together. Preview main-line and ESA-line targets separately."
    )


class TwissPreviewWorker(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, profile, overrides, line_name, parent=None):
        super().__init__(parent)
        self.profile = profile
        self.overrides = overrides
        self.line_name = line_name

    def run(self):
        try:
            self.completed.emit(
                build_twiss_preview(
                    self.profile,
                    self.overrides,
                    line_name=self.line_name,
                )
            )
        except Exception as exc:
            self.failed.emit(str(exc))


class TwissPreviewDialog(QDialog):
    def __init__(self, result: TwissPreviewResult, parent=None):
        super().__init__(parent)
        self.palette = _theme_palette()
        self.setObjectName("twissPreviewDialog")
        self.setStyleSheet(_build_stylesheet(self.palette))
        self.setWindowTitle("Twiss Model Preview")
        self.resize(980, 620)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 10)
        layout.setSpacing(8)
        summary = QLabel(
            f"Line: {result.line_name}   Overrides: {len(result.overrides)}   "
            f"Max |dBeta x|: {result.max_delta_beta_x:.6g} m   "
            f"Max |dBeta y|: {result.max_delta_beta_y:.6g} m   "
            f"Max |dEta x|: {result.max_delta_eta_x:.6g} m   "
            f"Max |dEta y|: {result.max_delta_eta_y:.6g} m",
            self,
        )
        summary.setWordWrap(True)
        summary.setProperty("role", "meta")
        layout.addWidget(summary)

        self.tabs = QTabWidget(self)
        overview = QWidget(self.tabs)
        overview_layout = QVBoxLayout(overview)
        overview_layout.setContentsMargins(0, 6, 0, 0)
        self.figure = Figure(
            figsize=(9, 5),
            constrained_layout=True,
            facecolor=self.palette["panel"],
        )
        self.canvas = FigureCanvas(self.figure)
        self.toolbar = NavigationToolbar(self.canvas, overview)
        overview_layout.addWidget(self.toolbar)
        overview_layout.addWidget(self.canvas, 1)
        self.tabs.addTab(overview, "Overview")

        data = QWidget(self.tabs)
        data_layout = QVBoxLayout(data)
        data_layout.setContentsMargins(0, 6, 0, 0)
        self.table = QTableWidget(data)
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels(
            ("Element", "s [m]", "Design beta_x", "Target beta_x", "dBeta_x",
             "Design beta_y", "Target beta_y", "dBeta_y", "dEta x / y")
        )
        self.table.setRowCount(len(result.rows))
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(8, QHeaderView.Stretch)
        for row_index, row in enumerate(result.rows):
            values = (
                row.element_name,
                f"{row.s_m:.7g}",
                f"{row.design['beta_x_m']:.7g}",
                f"{row.target['beta_x_m']:.7g}",
                f"{row.target['beta_x_m'] - row.design['beta_x_m']:.7g}",
                f"{row.design['beta_y_m']:.7g}",
                f"{row.target['beta_y_m']:.7g}",
                f"{row.target['beta_y_m'] - row.design['beta_y_m']:.7g}",
                f"{row.target['dx_m'] - row.design['dx_m']:.7g} / "
                f"{row.target['dy_m'] - row.design['dy_m']:.7g}",
            )
            for column, value in enumerate(values):
                self.table.setItem(row_index, column, QTableWidgetItem(value))
        data_layout.addWidget(self.table)
        self.tabs.addTab(data, "Data")
        layout.addWidget(self.tabs, 1)

        self._plot_overview(result)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _plot_overview(self, result: TwissPreviewResult) -> None:
        self.beta_axis, self.eta_axis = self.figure.subplots(2, 1, sharex=True)
        positions = [row.s_m for row in result.rows]
        design_color = self.palette["muted"]
        x_color = self.palette["accent"]
        y_color = "#ef8a7e" if resolve_initial_theme() == "dark" else "#b34d3f"

        curves = (
            (self.beta_axis, "beta_x_m", design_color, "--", "Design beta x"),
            (self.beta_axis, "beta_y_m", design_color, ":", "Design beta y"),
            (self.beta_axis, "beta_x_m", x_color, "-", "Target beta x", "target"),
            (self.beta_axis, "beta_y_m", y_color, "-", "Target beta y", "target"),
            (self.eta_axis, "dx_m", design_color, "--", "Design eta x"),
            (self.eta_axis, "dy_m", design_color, ":", "Design eta y"),
            (self.eta_axis, "dx_m", x_color, "-", "Target eta x", "target"),
            (self.eta_axis, "dy_m", y_color, "-", "Target eta y", "target"),
        )
        for axis, field, color, style, label, *source in curves:
            values = [
                (row.target if source else row.design)[field]
                for row in result.rows
            ]
            axis.plot(positions, values, color=color, linestyle=style, linewidth=1.5, label=label)

        override_ids = {element_id.upper() for element_id in result.overrides}
        marker_positions = {
            row.s_m for row in result.rows if row.element_name.upper() in override_ids
        }
        for position in sorted(marker_positions):
            self.beta_axis.axvline(position, color=self.palette["warning"], linewidth=0.8, alpha=0.65)
            self.eta_axis.axvline(position, color=self.palette["warning"], linewidth=0.8, alpha=0.65)

        self.beta_axis.set_ylabel("beta [m]")
        self.eta_axis.set_ylabel("eta [m]")
        self.eta_axis.set_xlabel("s [m]")
        for axis in (self.beta_axis, self.eta_axis):
            axis.set_facecolor(self.palette["plot"])
            axis.tick_params(colors=self.palette["text"], labelsize=9)
            axis.xaxis.label.set_color(self.palette["text"])
            axis.yaxis.label.set_color(self.palette["text"])
            for spine in axis.spines.values():
                spine.set_color(self.palette["border"])
            axis.grid(True, color=self.palette["grid"], linewidth=0.5, alpha=0.5)
            legend = axis.legend(loc="best", ncol=2, fontsize="small", frameon=False)
            for text in legend.get_texts():
                text.set_color(self.palette["text"])
        self.canvas.draw_idle()


class TargetValueDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        editor = QLineEdit(parent)
        validator = QDoubleValidator(editor)
        validator.setNotation(QDoubleValidator.ScientificNotation)
        editor.setValidator(validator)
        return editor

    def setModelData(self, editor, model, index):
        text = editor.text().strip()
        if not text:
            model.setData(index, "")
            return
        try:
            value = float(text)
        except ValueError:
            return
        if math.isfinite(value):
            model.setData(index, f"{value:.12g}")


class MachineSetpointsWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.profile = load_profile()
        self.control_backend = normalize_mode(
            os.environ.get(CONTROL_BACKEND_ENV, "")
            or os.environ.get(LEGACY_CONTROL_BACKEND_ENV, "")
            or self.profile.machine.default_mode, "control_backend"
        )
        self.backend_capabilities = backend_capabilities(self.control_backend)
        self.current_theme = resolve_initial_theme()
        self.runtime = resolve_machine_runtime(self.profile)
        self.plan: TransferPlan | None = None
        self.worker: VmTransferWorker | None = None
        self.restore_worker: RestoreWorker | None = None
        self.restore_check_worker: RestoreCheckWorker | None = None
        self.preview_worker: VmPreviewWorker | None = None
        self.twiss_worker: TwissPreviewWorker | None = None
        self.active_plan: TransferPlan | None = None
        self.current_values: dict[str, float | None] = {}
        self.staged_values: dict[tuple[str, str], StagedSetpoint] = {}
        self.design_line_elements: dict[str, set[str]] = {}
        self.selection_checkboxes: dict[str, QCheckBox] = {}
        self.last_result = ()
        self.execution_states: dict[str, str] = {}
        self.target_step = 0.1
        self.log_path = _ROOT / "logs" / "setpoint_transfer" / f"{self.profile.machine.id}.jsonl"
        self.workspace_dir = _ROOT / "logs" / "setpoint_transfer" / "workspaces"
        self.transaction_dir = _ROOT / "logs" / "setpoint_transfer" / "transactions"
        self.setWindowTitle(f"{self.profile.machine.display_name} Machine Setpoints")
        self.resize(1120, 720)
        self.setMinimumSize(880, 560)
        self._apply_theme()

        central = QWidget(self)
        central.setObjectName("centralRoot")
        layout = QVBoxLayout(central)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(7)
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 3)
        title = QLabel("Machine Setpoints", central)
        title.setObjectName("windowTitle")
        header_layout.addWidget(title)
        header_layout.addStretch(1)
        self.runtime_context = RuntimeContextWidget(
            machine_id=self.profile.machine.id,
            machine_display_name=self.profile.machine.display_name,
            control_backend=self.control_backend,
            parent=central,
        )
        header_layout.addWidget(self.runtime_context)
        self.theme_toggle_button = QToolButton(central)
        self.theme_toggle_button.setObjectName("themeToggleButton")
        self.theme_toggle_button.setFixedSize(32, 32)
        self.theme_toggle_button.clicked.connect(self._toggle_theme)
        self._update_theme_toggle_button()
        header_layout.addWidget(self.theme_toggle_button)
        layout.addLayout(header_layout)

        self.source_label = QLabel(central)
        self.summary_label = QLabel(central)
        for label in (self.source_label, self.summary_label):
            label.setProperty("role", "meta")
        context_layout = QHBoxLayout()
        context_layout.setContentsMargins(0, 0, 0, 0)
        context_layout.setSpacing(12)
        context_layout.addWidget(self.source_label, 1)
        context_layout.addWidget(self.summary_label, 0, Qt.AlignRight)
        layout.addLayout(context_layout)
        filter_layout = QHBoxLayout()
        filter_layout.setSpacing(7)
        self.search_input = QLineEdit(central)
        self.search_input.setPlaceholderText("Filter elements...")
        self.status_filter = QComboBox(central)
        self.status_filter.addItems(("All", "Selected", "Changed", "Ready", "Blocked"))
        self.select_visible_button = QPushButton("Select Visible", central)
        self.clear_visible_button = QPushButton("Clear Visible", central)
        filter_layout.addWidget(self.search_input, 1)
        filter_layout.addWidget(self.status_filter)
        layout.addLayout(filter_layout)
        self.table = QTableWidget(central)
        selection_layout = QHBoxLayout()
        target_layout = QHBoxLayout()
        selection_group_label = QLabel("Selection", central)
        selection_group_label.setProperty("role", "field")
        target_group_label = QLabel("Target", central)
        target_group_label.setProperty("role", "field")
        step_group_label = QLabel("Step", central)
        step_group_label.setProperty("role", "field")
        for label in (selection_group_label, target_group_label):
            label.setFixedWidth(58)
        step_group_label.setFixedWidth(34)
        step_group_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.select_ready_button = QPushButton("Select Ready", central)
        self.clear_selection_button = QPushButton("Clear", central)
        self.load_design_button = QPushButton("Use Design", central)
        self.load_current_button = QPushButton("Use Current", central)
        self.clear_target_button = QPushButton("Clear", central)
        self.absolute_target_button = QPushButton("Abs Target", central)
        self.absolute_target_button.setToolTip(
            "Replace staged Target values on selected rows with their absolute values."
        )
        self.nudge_down_button = QPushButton(f"-{self.target_step:g}", central)
        self.nudge_up_button = QPushButton(f"+{self.target_step:g}", central)
        self.nudge_down_button.setToolTip("Decrease selected Target values by one step.")
        self.nudge_up_button.setToolTip("Increase selected Target values by one step.")
        self.workspace_button = QToolButton(central)
        self.workspace_button.setText("Workspace")
        self.workspace_button.setPopupMode(QToolButton.InstantPopup)
        workspace_menu = QMenu(self.workspace_button)
        self.save_workspace_action = workspace_menu.addAction("Save Workspace")
        self.load_workspace_action = workspace_menu.addAction("Load Workspace")
        workspace_menu.addSeparator()
        self.clear_workspace_action = workspace_menu.addAction("Clear Workspace")
        self.workspace_button.setMenu(workspace_menu)
        for button in (
            self.select_visible_button,
            self.clear_visible_button,
            self.select_ready_button,
            self.clear_selection_button,
            self.load_design_button,
            self.load_current_button,
            self.clear_target_button,
            self.absolute_target_button,
            self.workspace_button,
        ):
            button.setFixedWidth(104)
        self.nudge_down_button.setFixedWidth(54)
        self.nudge_up_button.setFixedWidth(54)
        self.selection_label = QLabel("0 selected", central)
        self.selection_label.setMinimumWidth(110)
        self.selection_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        selection_layout.addWidget(selection_group_label)
        selection_layout.addWidget(self.select_visible_button)
        selection_layout.addWidget(self.clear_visible_button)
        selection_layout.addWidget(self.select_ready_button)
        selection_layout.addWidget(self.clear_selection_button)
        selection_layout.addStretch(1)
        selection_layout.addWidget(self.selection_label)
        target_layout.addWidget(target_group_label)
        target_layout.addWidget(self.load_design_button)
        target_layout.addWidget(self.load_current_button)
        target_layout.addWidget(self.clear_target_button)
        target_layout.addWidget(self.absolute_target_button)
        target_layout.addWidget(self.workspace_button)
        target_layout.addStretch(1)
        target_layout.addWidget(step_group_label)
        target_layout.addWidget(self.nudge_down_button)
        target_layout.addWidget(self.nudge_up_button)
        layout.addLayout(selection_layout)
        layout.addLayout(target_layout)
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(
            ("Select", "Element", "Design K1", f"Current {self.control_backend.upper()}", "Target K1", "Delta", "Source", "Status")
        )
        self.table.setEditTriggers(
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.EditKeyPressed
            | QAbstractItemView.SelectedClicked
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(27)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 64)
        self.table.setColumnWidth(4, 120)
        self.table.setItemDelegateForColumn(4, TargetValueDelegate(self.table))
        layout.addWidget(self.table)
        self.status_label = QLabel("Ready", central)
        self.status_label.setProperty("role", "status")
        layout.addWidget(self.status_label)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, central)
        self.preview_button = QPushButton("Refresh Current", central)
        self.twiss_button = QPushButton("Preview Twiss", central)
        self.apply_button = QPushButton("Apply Selected", central)
        self.restore_button = QPushButton("Restore Last Apply", central)
        self.restore_button.setEnabled(False)
        for button in (
            self.apply_button,
            self.preview_button,
            self.twiss_button,
            self.restore_button,
        ):
            button.setFixedWidth(148)
        buttons.button(QDialogButtonBox.Close).setFixedWidth(72)
        self.apply_button.setProperty("role", "primary")
        buttons.addButton(self.preview_button, QDialogButtonBox.ActionRole)
        buttons.addButton(self.twiss_button, QDialogButtonBox.ActionRole)
        buttons.addButton(self.apply_button, QDialogButtonBox.AcceptRole)
        buttons.addButton(self.restore_button, QDialogButtonBox.ActionRole)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        self.setCentralWidget(central)
        self.preview_button.clicked.connect(self.preview)
        self.twiss_button.clicked.connect(self.preview_twiss)
        self.apply_button.clicked.connect(self.apply)
        self.restore_button.clicked.connect(self.restore_last_apply)
        self.select_ready_button.clicked.connect(self._select_all_ready)
        self.clear_selection_button.clicked.connect(self._clear_selection)
        self.load_design_button.clicked.connect(self._load_design)
        self.load_current_button.clicked.connect(self._load_current)
        self.clear_target_button.clicked.connect(self._clear_target)
        self.absolute_target_button.clicked.connect(self._absolute_selected_targets)
        self.save_workspace_action.triggered.connect(self._save_workspace)
        self.load_workspace_action.triggered.connect(self._load_workspace)
        self.clear_workspace_action.triggered.connect(self._clear_workspace)
        self.select_visible_button.clicked.connect(lambda: self._select_visible(True))
        self.clear_visible_button.clicked.connect(lambda: self._select_visible(False))
        self.nudge_down_button.clicked.connect(lambda: self._nudge_selected(-self.target_step))
        self.nudge_up_button.clicked.connect(lambda: self._nudge_selected(self.target_step))
        self.search_input.textChanged.connect(self._apply_filters)
        self.status_filter.currentTextChanged.connect(self._apply_filters)
        self.table.itemChanged.connect(self._selection_changed)
        self.apply_button.setEnabled(False)
        self.apply_button.setToolTip(
            f"Apply selected setpoints to the {self.control_backend.upper()} control backend."
        )
        self.twiss_button.setEnabled(False)
        self._load_source()

    def _apply_theme(self):
        self.setStyleSheet(_build_stylesheet(_theme_palette(self.current_theme)))
        if hasattr(self, "theme_toggle_button"):
            self._update_theme_toggle_button()

    def _update_theme_toggle_button(self):
        if self.current_theme == "dark":
            self.theme_toggle_button.setText("☀")
            self.theme_toggle_button.setToolTip("Switch to light theme")
        else:
            self.theme_toggle_button.setText("☾")
            self.theme_toggle_button.setToolTip("Switch to dark theme")

    def _toggle_theme(self):
        self.current_theme = "light" if self.current_theme == "dark" else "dark"
        os.environ["HALF_LINAC_INITIAL_THEME"] = self.current_theme
        self._apply_theme()

    def _load_source(self):
        source = Path(self.runtime.vm.bootstrap_lattice)
        self.source_label.setText(f"Design source: {source.name}")
        self.source_label.setToolTip(str(source))
        try:
            workflow = resolve_virtual_machine_usedline_workflow(self.profile)
            line_names = [self.runtime.vm.line_name]
            line_names.extend(
                choice.id
                for choice in workflow.predefined_usedlines
                if choice.role == "energy_spectrum" and choice.id not in line_names
            )
            merged = []
            seen = set()
            self.design_line_elements = {}
            for line_name in line_names:
                line_setpoints = extract_design_setpoints(source, line_name=line_name)
                self.design_line_elements[line_name] = {
                    item.element_id for item in line_setpoints
                }
                for item in line_setpoints:
                    key = (item.element_id, item.field)
                    if key not in seen:
                        seen.add(key)
                        merged.append(item)
            self.design_setpoints = tuple(merged)
        except Exception as exc:
            self.status_label.setText(f"Design source error: {exc}")
            self.preview_button.setEnabled(False)
            return
        self.summary_label.setText(
            f"{len(self.design_setpoints)} quadrupoles   "
            + f"{self.control_backend.upper()} write enabled"
        )
        self.preview()

    def preview(self):
        if self.preview_worker is not None and self.preview_worker.isRunning():
            return
        self.plan = None
        self.preview_button.setEnabled(False)
        self.apply_button.setEnabled(False)
        self.status_label.setText(f"Reading {self.control_backend.upper()} setpoints...")
        self.preview_worker = VmPreviewWorker(
            self.profile, self.design_setpoints, self.control_backend, self
        )
        self.preview_worker.completed.connect(self._preview_complete)
        self.preview_worker.failed.connect(self._preview_failed)
        self.preview_worker.finished.connect(lambda: self.preview_button.setEnabled(True))
        self.preview_worker.start()

    def _preview_complete(self, current_values):
        self.current_values = dict(current_values)
        self._rebuild_plan()
        self._refresh_selection_state()

    def _preview_failed(self, message):
        self.status_label.setText(f"Preview failed: {message}")

    def _rebuild_plan(self):
        self.plan = build_transfer_plan(
            self.profile,
            self.design_setpoints,
            target_backend=self.control_backend,
            current_values=self.current_values,
            staged_setpoints=tuple(self.staged_values.values()),
        )
        self._render_plan(self.plan)

    def _render_plan(self, plan: TransferPlan):
        selected_ids = self._checked_ids()
        self.table.blockSignals(True)
        self.table.setRowCount(len(plan.items))
        self.selection_checkboxes = {}
        for row, item in enumerate(plan.items):
            can_stage = item.pv_name is not None and item.current_value is not None
            checkbox = QCheckBox(self.table)
            checkbox.setObjectName(f"select_{item.element_id}")
            checkbox.setProperty("element_id", item.element_id)
            checkbox.setProperty("can_stage", can_stage)
            checkbox.setChecked(item.element_id in selected_ids)
            checkbox.setToolTip("Select for transfer" if can_stage else item.message)
            checkbox.stateChanged.connect(self._selection_changed)
            container = QWidget(self.table)
            container.setStyleSheet("background: transparent;")
            checkbox_layout = QHBoxLayout(container)
            checkbox_layout.setContentsMargins(0, 0, 0, 0)
            checkbox_layout.setSpacing(0)
            checkbox_layout.addStretch(1)
            checkbox_layout.addWidget(checkbox)
            checkbox_layout.addStretch(1)
            self.table.setCellWidget(row, 0, container)
            self.selection_checkboxes[item.element_id] = checkbox
            execution_state = self.execution_states.get(item.element_id)
            status_text = (
                execution_state.title()
                if execution_state
                else {
                    "ready": "Ready",
                    "blocked": "Blocked",
                    "not_staged": "Not staged",
                }.get(item.status, item.status.replace("_", " ").title())
            )
            values = (
                item.element_id,
                "" if item.design_value is None else f"{item.design_value:.7g}",
                "" if item.current_value is None else f"{item.current_value:.7g}",
                "" if item.target_value is None else f"{item.target_value:.12g}",
                "" if item.target_value is None or item.current_value is None else f"{item.target_value - item.current_value:.7g}",
                item.target_origin.title() if item.target_origin else "",
                status_text,
            )
            for column, value in enumerate(values, start=1):
                cell = QTableWidgetItem(value)
                if column == 7:
                    color = "#2d7f6d" if item.status == "ready" else "#b44141"
                    if execution_state == "applied":
                        color = "#2d7f6d"
                    elif execution_state == "not executed":
                        color = "#746c62"
                    elif item.status == "not_staged":
                        color = "#746c62"
                    cell.setForeground(QColor(color))
                    if item.message:
                        cell.setToolTip(item.message)
                if column == 5 and item.target_value is not None and item.current_value is not None:
                    change = abs(item.target_value - item.current_value)
                    if change > LARGE_CHANGE_THRESHOLD:
                        cell.setForeground(QColor("#d17a18"))
                if column == 4:
                    flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
                    if can_stage:
                        flags |= Qt.ItemIsEditable
                    cell.setFlags(flags)
                self.table.setItem(row, column, cell)
        self.table.blockSignals(False)
        self.status_label.setText(
            f"{len(plan.items)} quadrupoles   "
            f"{sum(item.status == 'ready' for item in plan.items)} ready   "
            f"{sum(item.status == 'blocked' for item in plan.items)} blocked   "
            f"{sum(item.status == 'not_staged' for item in plan.items)} not staged"
        )
        if plan.diagnostics:
            self.status_label.setToolTip("\n".join(plan.diagnostics))
        self._apply_filters()
        self._refresh_selection_state()

    def _checked_ids(self):
        return {
            element_id
            for element_id, checkbox in self.selection_checkboxes.items()
            if checkbox.isChecked()
        }

    def _row_for_element(self, element_id):
        for row in range(self.table.rowCount()):
            if self.table.item(row, 1) and self.table.item(row, 1).text() == element_id:
                return row
        return -1

    def _selected_plan(self) -> TransferPlan | None:
        if self.plan is None:
            return None
        selected_ids = self._checked_ids()
        items = tuple(
            item for item in self.plan.items if item.element_id in selected_ids
        )
        return TransferPlan(self.plan.target_backend, items, self.plan.diagnostics)

    def _selection_changed(self, item=None):
        if not isinstance(item, QTableWidgetItem):
            checkbox = self.sender()
            if isinstance(checkbox, QCheckBox) and not checkbox.property("can_stage") and checkbox.isChecked():
                checkbox.blockSignals(True)
                checkbox.setChecked(False)
                checkbox.blockSignals(False)
            self._apply_filters()
            return
        if item.column() != 4:
            return
        element_id = self.table.item(item.row(), 1).text()
        key = (element_id, "K1")
        text = item.text().strip()
        if not text:
            self.staged_values.pop(key, None)
        else:
            try:
                value = float(text)
            except ValueError:
                return
            if not math.isfinite(value):
                return
            self.staged_values[key] = StagedSetpoint(
                element_id, "K1", value, "manual"
            )
            checkbox = self.selection_checkboxes.get(element_id)
            if checkbox is not None and checkbox.isEnabled():
                checkbox.setChecked(True)
        self._rebuild_plan()

    def _select_all_ready(self):
        by_id = {item.element_id: item for item in self.plan.items} if self.plan else {}
        for element_id, checkbox in self.selection_checkboxes.items():
            plan_item = by_id.get(element_id)
            operational = (
                plan_item is not None
                and plan_item.pv_name is not None
                and plan_item.current_value is not None
            )
            checkbox.blockSignals(True)
            checkbox.setChecked(operational)
            checkbox.blockSignals(False)
        self._refresh_selection_state()

    def _clear_selection(self):
        for checkbox in self.selection_checkboxes.values():
            checkbox.blockSignals(True)
            checkbox.setChecked(False)
            checkbox.blockSignals(False)
        self._refresh_selection_state()

    def _select_visible(self, checked):
        for row in range(self.table.rowCount()):
            if self.table.isRowHidden(row):
                continue
            element_id = self.table.item(row, 1).text()
            checkbox = self.selection_checkboxes.get(element_id)
            if checkbox is None:
                continue
            if checked and not checkbox.property("can_stage"):
                continue
            checkbox.blockSignals(True)
            checkbox.setChecked(bool(checked))
            checkbox.blockSignals(False)
        self._refresh_selection_state()

    def _nudge_selected(self, delta):
        selected_ids = self._checked_ids()
        changed = False
        for row in range(self.table.rowCount()):
            if self.table.isRowHidden(row):
                continue
            element_id = self.table.item(row, 1).text()
            if element_id not in selected_ids:
                continue
            item = self.plan.items[row] if self.plan is not None else None
            if item is None or item.current_value is None:
                continue
            base = item.target_value if item.target_value is not None else item.current_value
            value = float(base) + float(delta)
            if not math.isfinite(value):
                continue
            self.staged_values[(element_id, item.field)] = StagedSetpoint(
                element_id, item.field, value, "manual"
            )
            changed = True
        if changed:
            self._rebuild_plan()

    def _absolute_selected_targets(self):
        selected_plan = self._selected_plan()
        if selected_plan is None:
            return
        changed = False
        for item in selected_plan.items:
            if item.target_value is None:
                continue
            self.staged_values[(item.element_id, item.field)] = StagedSetpoint(
                item.element_id,
                item.field,
                abs(float(item.target_value)),
                "manual",
            )
            changed = True
        if changed:
            self._rebuild_plan()

    def _apply_filters(self):
        query = self.search_input.text().strip().casefold()
        selected = self._checked_ids()
        mode = self.status_filter.currentText()
        for row in range(self.table.rowCount()):
            item = self.plan.items[row] if self.plan is not None else None
            if item is None:
                self.table.setRowHidden(row, False)
                continue
            element_match = not query or query in item.element_id.casefold()
            changed = (
                item.target_value is not None
                and item.current_value is not None
                and not math.isclose(
                    item.target_value,
                    item.current_value,
                    rel_tol=0.0,
                    abs_tol=1.0e-12,
                )
            )
            status_match = {
                "All": True,
                "Selected": item.element_id in selected,
                "Changed": changed,
                "Ready": item.status == "ready",
                "Blocked": item.status == "blocked",
            }.get(mode, True)
            self.table.setRowHidden(row, not (element_match and status_match))
        self._refresh_selection_state()

    def _refresh_selection_state(self):
        selected_plan = self._selected_plan()
        selected_count = len(selected_plan.items) if selected_plan is not None else 0
        staged_count = (
            sum(item.target_value is not None for item in selected_plan.items)
            if selected_plan is not None
            else 0
        )
        all_ready = bool(selected_plan) and bool(selected_plan.items) and all(
            item.status == "ready" for item in selected_plan.items
        )
        self.selection_label.setText(
            f"{selected_count} selected / {staged_count} staged"
        )
        busy = self.worker is not None and self.worker.isRunning()
        restore_busy = self.restore_worker is not None and self.restore_worker.isRunning()
        restore_check_busy = (
            self.restore_check_worker is not None
            and self.restore_check_worker.isRunning()
        )
        busy = busy or restore_busy or restore_check_busy
        self.apply_button.setEnabled(self.backend_capabilities.can_write and all_ready and not busy)
        self.restore_button.setEnabled(bool(self.last_result) and not busy)
        twiss_busy = self.twiss_worker is not None and self.twiss_worker.isRunning()
        self.twiss_button.setEnabled(bool(self.staged_values) and not busy and not twiss_busy)
        selected_operational = selected_count > 0 and all(
            item.pv_name is not None and item.current_value is not None
            for item in selected_plan.items
        )
        self.load_design_button.setEnabled(selected_operational and not busy)
        self.load_current_button.setEnabled(selected_operational and not busy)
        self.clear_target_button.setEnabled(selected_count > 0 and not busy)
        self.absolute_target_button.setEnabled(staged_count > 0 and not busy)
        visible_selected = any(
            not self.table.isRowHidden(row)
            and self.table.item(row, 1).text() in self._checked_ids()
            for row in range(self.table.rowCount())
        )
        self.select_visible_button.setEnabled(not busy)
        self.clear_visible_button.setEnabled(visible_selected and not busy)
        self.nudge_down_button.setEnabled(visible_selected and not busy)
        self.nudge_up_button.setEnabled(visible_selected and not busy)

    def _stage_selected(self, source):
        selected_plan = self._selected_plan()
        if selected_plan is None:
            return
        for item in selected_plan.items:
            if item.pv_name is None or item.current_value is None:
                continue
            value = item.design_value if source == "design" else item.current_value
            self.staged_values[(item.element_id, item.field)] = StagedSetpoint(
                item.element_id, item.field, float(value), source
            )
        self._rebuild_plan()

    def _load_design(self):
        self._stage_selected("design")

    def _load_current(self):
        self._stage_selected("current")

    def _clear_target(self):
        selected_plan = self._selected_plan()
        if selected_plan is None:
            return
        for item in selected_plan.items:
            self.staged_values.pop((item.element_id, item.field), None)
        self._rebuild_plan()

    def _save_workspace(self):
        staged = tuple(self.staged_values.values())
        if not staged:
            QMessageBox.information(
                self, "Save Workspace", "There are no staged Target values to save."
            )
            return
        timestamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S")
        default_path = self.workspace_dir / (
            f"{self.profile.machine.id}_setpoints_{timestamp}.json"
        )
        path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Save Machine Setpoints Workspace",
            str(default_path),
            "Setpoint workspaces (*.json)",
        )
        if not path:
            return
        destination = Path(path)
        if destination.suffix.lower() != ".json":
            destination = destination.with_suffix(".json")
        try:
            save_target_workspace(
                destination,
                machine_id=self.profile.machine.id,
                target_backend=self.control_backend,
                staged_setpoints=staged,
            )
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Save Workspace", str(exc))
            return
        self.status_label.setText(
            f"Saved {len(staged)} Target values to {destination}."
        )

    def _load_workspace(self):
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Load Machine Setpoints Workspace",
            str(self.workspace_dir),
            "Setpoint workspaces (*.json)",
        )
        if not path:
            return
        try:
            staged = load_target_workspace(
                path,
                expected_machine_id=self.profile.machine.id,
                expected_target_backend=self.control_backend,
            )
            design_keys = {
                (item.element_id.upper(), item.field.upper())
                for item in self.design_setpoints
            }
            unknown = [
                f"{item.element_id}.{item.field}"
                for item in staged
                if (item.element_id, item.field) not in design_keys
            ]
            if unknown:
                raise ValueError(
                    "Workspace targets are not present in the current design lattice: "
                    + ", ".join(unknown)
                )
        except ValueError as exc:
            QMessageBox.critical(self, "Load Workspace", str(exc))
            return
        self._clear_selection()
        self.staged_values = {
            (item.element_id, item.field): item for item in staged
        }
        self.execution_states = {}
        self._rebuild_plan()
        self.preview()

    def _clear_workspace(self):
        if not self.staged_values:
            return
        answer = QMessageBox.question(
            self,
            "Clear Workspace",
            "Clear all staged Target values?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._clear_selection()
        self.staged_values.clear()
        self.execution_states = {}
        self._rebuild_plan()

    def preview_twiss(self):
        if not self.staged_values:
            QMessageBox.information(
                self, "Twiss Model Preview", "Stage at least one Target value first."
            )
            return
        if self.twiss_worker is not None and self.twiss_worker.isRunning():
            return
        overrides = {
            element_id: {field: staged.target_value}
            for (element_id, field), staged in self.staged_values.items()
        }
        try:
            line_name = _select_twiss_line(
                set(overrides),
                self.design_line_elements,
                self.runtime.vm.line_name,
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Twiss Model Preview", str(exc))
            return
        self.twiss_button.setEnabled(False)
        self.status_label.setText(f"Running Twiss model preview for {line_name}...")
        self.twiss_worker = TwissPreviewWorker(
            self.profile, overrides, line_name, self
        )
        self.twiss_worker.completed.connect(self._twiss_complete)
        self.twiss_worker.failed.connect(self._twiss_failed)
        self.twiss_worker.finished.connect(self._twiss_finished)
        self.twiss_worker.start()

    def _twiss_complete(self, result):
        dialog = TwissPreviewDialog(result, self)
        dialog.exec_()
        self.status_label.setText(
            f"Twiss preview complete: {len(result.overrides)} Target overrides."
        )

    def _twiss_failed(self, message):
        self.status_label.setText(f"Twiss preview failed: {message}")
        QMessageBox.warning(self, "Twiss Model Preview", message)

    def _twiss_finished(self):
        self._refresh_selection_state()

    def _commit_active_editor(self):
        focus_widget = QApplication.focusWidget()
        if focus_widget is not None and self.table.isAncestorOf(focus_widget):
            focus_widget.clearFocus()
            QApplication.processEvents()

    @staticmethod
    def _plan_validation_error(plan: TransferPlan | None) -> str:
        if plan is None or not plan.items:
            return "Select at least one Quad before applying."
        blocked = [item for item in plan.items if item.status != "ready"]
        if not blocked:
            return ""
        details = "\n".join(
            f"{item.element_id}: {item.message or item.status}"
            for item in blocked
        )
        return f"The selected setpoints are not ready:\n{details}"

    @staticmethod
    def _write_details(plan: TransferPlan) -> str:
        return "\n".join(
            f"{item.element_id}.K1  {item.current_value:.12g} -> "
            f"{item.target_value:.12g}  [{item.pv_name}]"
            for item in plan.writable_items
        )

    def apply(self):
        self._commit_active_editor()
        self._rebuild_plan()
        selected_plan = self._selected_plan()
        validation_error = self._plan_validation_error(selected_plan)
        if validation_error:
            QMessageBox.warning(self, "Cannot apply setpoints", validation_error)
            return
        origin_counts = Counter(item.target_origin for item in selected_plan.writable_items)
        max_change = max(
            abs(item.target_value - item.current_value)
            for item in selected_plan.writable_items
        )
        prompt = QMessageBox(self)
        prompt.setIcon(QMessageBox.Warning)
        backend_label = "Real Machine" if self.control_backend == "real" else "Virtual Machine"
        prompt.setWindowTitle(f"Confirm {backend_label} write")
        prompt.setText(f"Write selected Quad K1 values to the {backend_label} backend?")
        prompt.setInformativeText(
            f"Selected: {len(selected_plan.writable_items)}\n"
            f"Design: {origin_counts.get('design', 0)}  "
            f"Current: {origin_counts.get('current', 0)}  "
            f"Manual: {origin_counts.get('manual', 0)}\n"
            f"Largest change: {max_change:.7g} 1/m^2\n\n"
            "All target PVs will be checked again before the first write."
        )
        prompt.setDetailedText(self._write_details(selected_plan))
        prompt.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        prompt.setDefaultButton(QMessageBox.No)
        if prompt.exec_() != QMessageBox.Yes:
            self.status_label.setText("Restore cancelled.")
            return
        self.preview_button.setEnabled(False)
        self.apply_button.setEnabled(False)
        self.status_label.setText("Applying...")
        self.active_plan = selected_plan
        self.worker = VmTransferWorker(selected_plan, self)
        self.worker.completed.connect(self._apply_complete)
        self.worker.failed.connect(self._apply_failed)
        self.worker.finished.connect(self._worker_finished)
        self.worker.start()

    def _apply_complete(self, result):
        self.last_result = tuple(result)
        self.execution_states = {item.element_id: "applied" for item in result}
        append_execution_log(self.log_path, self.active_plan or self.plan, result)
        self._save_transaction(result)
        self._update_current_from_result(result)
        self.status_label.setText(f"Applied and verified {len(result)} Quad K1 values.")

    def _apply_failed(self, message, completed, failed_element_id):
        self.last_result = tuple(completed)
        active = self.active_plan or self.plan
        applied_ids = {item.element_id for item in completed}
        self.execution_states = {
            item.element_id: (
                "applied"
                if item.element_id in applied_ids
                else "failed"
                if item.element_id == failed_element_id
                else "not executed"
            )
            for item in active.items
        }
        append_execution_log(
            self.log_path,
            active,
            completed,
            error=message,
            failed_element_id=failed_element_id,
        )
        if completed:
            self._save_transaction(completed)
        self._update_current_from_result(completed)
        self.status_label.setText(
            f"Apply stopped: {message} ({len(completed)} values already applied)"
        )

    def _update_current_from_result(self, result):
        for applied in result:
            self.current_values[applied.element_id] = applied.actual_value
        self._rebuild_plan()

    def _worker_finished(self):
        self.preview_button.setEnabled(True)
        self._refresh_selection_state()

    def _save_transaction(self, result):
        timestamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S")
        path = self.transaction_dir / (
            f"{self.profile.machine.id}_{self.control_backend}_{timestamp}.json"
        )
        try:
            save_transfer_transaction(
                path,
                machine_id=self.profile.machine.id,
                backend=self.control_backend,
                result=result,
            )
        except OSError as exc:
            self.status_label.setToolTip(f"Could not save transaction: {exc}")

    def restore_last_apply(self):
        if not self.last_result:
            return
        self.status_label.setText("Checking last Apply state...")
        self.restore_check_worker = RestoreCheckWorker(self.last_result, self)
        self.restore_check_worker.completed.connect(self._confirm_restore)
        self.restore_check_worker.finished.connect(self._refresh_selection_state)
        self.restore_check_worker.start()

    def _confirm_restore(self, conflicts):
        conflict_text = ""
        if conflicts:
            lines = [
                f"{element_id}: {current if current is not None else reason}"
                for element_id, current, reason in conflicts
            ]
            conflict_text = (
                "\n\nThese PVs changed or could not be verified since Apply:\n"
                + "\n".join(lines)
            )
        prompt = QMessageBox(self)
        prompt.setIcon(QMessageBox.Warning)
        prompt.setWindowTitle("Restore Last Apply")
        prompt.setText(
            f"Restore {len(self.last_result)} successfully applied Quad K1 values?"
        )
        prompt.setInformativeText(
            "Values will be restored in reverse order and verified after each write."
            + conflict_text
        )
        prompt.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        prompt.setDefaultButton(QMessageBox.No)
        if prompt.exec_() != QMessageBox.Yes:
            return
        if self.control_backend == "real":
            confirmation, accepted = QInputDialog.getText(
                self,
                "Confirm Real Machine restore",
                "This operation writes live Real Machine PVs. Type REAL to continue:",
            )
            if not accepted or confirmation.strip().upper() != "REAL":
                self.status_label.setText("Real Machine restore cancelled.")
                return
        self.status_label.setText("Restoring last Apply...")
        self.restore_worker = RestoreWorker(self.last_result, self)
        self.restore_worker.completed.connect(self._restore_complete)
        self.restore_worker.failed.connect(self._restore_failed)
        self.restore_worker.finished.connect(self._worker_finished)
        self.restore_worker.start()

    def _restore_complete(self, result):
        for item in result:
            self.current_values[item.element_id] = item.actual_value
        self.last_result = ()
        self.execution_states = {item.element_id: "restored" for item in result}
        self._rebuild_plan()
        self.status_label.setText(f"Restored and verified {len(result)} Quad K1 values.")

    def _restore_failed(self, message, completed, failed_element_id):
        for item in completed:
            self.current_values[item.element_id] = item.actual_value
        self.execution_states = {item.element_id: "restored" for item in completed}
        restored_ids = {item.element_id for item in completed}
        self.last_result = tuple(
            item for item in self.last_result if item.element_id not in restored_ids
        )
        self._rebuild_plan()
        self.status_label.setText(
            f"Restore stopped: {message} ({len(completed)} values restored)"
        )

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.warning(self, "Machine Setpoints", "Wait for the control-backend write to finish before closing.")
            event.ignore()
            return
        if self.restore_worker is not None and self.restore_worker.isRunning():
            QMessageBox.warning(self, "Machine Setpoints", "Wait for Restore to finish before closing.")
            event.ignore()
            return
        if self.restore_check_worker is not None and self.restore_check_worker.isRunning():
            event.ignore()
            return
        if self.preview_worker is not None and self.preview_worker.isRunning():
            event.ignore()
            return
        if self.twiss_worker is not None and self.twiss_worker.isRunning():
            event.ignore()
            return
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    try:
        window = MachineSetpointsWindow()
    except MachineProfileError as exc:
        QMessageBox.critical(None, "Machine Setpoints", str(exc))
        raise SystemExit(1) from exc
    window.show()
    raise SystemExit(app.exec_())
