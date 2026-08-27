from __future__ import annotations

import math
import sys
import time
from pathlib import Path

_REPO_BOOTSTRAP_ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / "repo_bootstrap.py").is_file()
)
if str(_REPO_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_BOOTSTRAP_ROOT))

from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

from epics import PV
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PyQt5.QtCore import QPointF, Qt, QTimer
from PyQt5.QtGui import QIntValidator, QPainter, QPalette, QPen
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from model import (
    MonitorStore,
    ShotPairer,
    SignalSample,
    TransmissionMapPairer,
    TransmissionMapSample,
    TransmissionSample,
    downsample_transmission_samples,
    parse_bounded_integer_input,
    rolling_statistics,
)
from half_linac.src.shared.app_theme import resolve_initial_theme
from half_linac.src.shared.machine_profile import (
    RuntimeContextWidget,
    list_elements,
    load_app_context,
    resolve_channel,
    resolve_ct_monitor_workflow,
)
from half_linac.src.shared.window_activation import install_qt_window_raise_handler


DARK = {
    "window": "#0f1519",
    "panel": "#172027",
    "border": "#2a3943",
    "text": "#e6edf2",
    "muted": "#91a2ad",
    "input": "#10171c",
    "accent": "#45d0bc",
    "upstream": "#63b3ed",
    "downstream": "#f0b45a",
    "efficiency": "#45d0bc",
    "warning": "#e37878",
    "grid": "#2a3943",
}

LIGHT = {
    "window": "#f2ede5",
    "panel": "#fffdf9",
    "border": "#d7cec1",
    "text": "#2c3942",
    "muted": "#7c7368",
    "input": "#fffdf9",
    "accent": "#2d7f6d",
    "upstream": "#2878a8",
    "downstream": "#a96d13",
    "efficiency": "#2d7f6d",
    "warning": "#b44141",
    "grid": "#ddd4c7",
}

THEME_BUTTON_SIZE = 32


def _stylesheet(palette: dict[str, str]) -> str:
    return f"""
QWidget {{ background: {palette['window']}; color: {palette['text']};
  font-family: \"IBM Plex Sans\", \"Source Han Sans SC\", \"Segoe UI\", sans-serif; }}
QFrame#panel, QFrame#metricCard {{ background: {palette['panel']};
  border: 1px solid {palette['border']}; border-radius: 12px; }}
QLabel#title {{ font-size: 22px; font-weight: 700; background: transparent; }}
QLabel#sectionTitle {{ font-size: 14px; font-weight: 700; background: transparent; }}
QLabel#pairArrow {{ color: {palette['muted']}; font-size: 22px; font-weight: 700;
  background: transparent; padding: 12px 4px 0px 4px; }}
QLabel[role="field"] {{ color: {palette['muted']}; font-size: 11px; font-weight: 600;
  background: transparent; }}
QLabel#metricValue {{ font-size: 24px; font-weight: 700; color: {palette['accent']};
  background: transparent; }}
QLabel#metricValue[emphasis="true"] {{ font-size: 34px; }}
QLabel#metricValue[warning="true"] {{ color: {palette['warning']}; }}
QLabel[role="statusBadge"] {{ background: transparent; border: none;
  padding: 0px; font-size: 11px; font-weight: 700; }}
QLabel[role="statusBadge"][tone="success"] {{ color: {palette['accent']}; }}
QLabel[role="statusBadge"][tone="danger"] {{ color: {palette['warning']}; }}
QLabel[role="statusBadge"][tone="neutral"] {{ color: {palette['muted']}; }}
QPushButton, QToolButton {{ background: {palette['input']}; color: {palette['text']};
  border: 1px solid {palette['border']}; border-radius: 8px; min-height: 30px;
  padding: 3px 10px; font-weight: 700; }}
QPushButton#pauseButton[paused="true"] {{ color: {palette['accent']};
  border-color: {palette['accent']}; }}
QPushButton#clearHistoryButton {{ color: {palette['muted']}; }}
QToolButton#themeToggleButton {{ padding: 0px; border-radius: 9px; font-size: 14px; }}
QToolButton#themeToggleButton:hover {{ border-color: {palette['accent']}; }}
QComboBox {{ background: {palette['input']}; color: {palette['text']};
  border: 1px solid {palette['border']}; border-radius: 9px; min-height: 30px;
  padding: 3px 8px 3px 11px; selection-background-color: {palette['accent']}; }}
QComboBox:hover, QComboBox:focus {{ border-color: {palette['accent']}; }}
QComboBox#deviceCombo {{ font-size: 17px; font-weight: 700; min-height: 40px;
  border-radius: 10px; }}
QComboBox::drop-down {{ border: none; width: 30px; }}
QComboBox::down-arrow {{ image: none; width: 0px; height: 0px; }}
QComboBox QAbstractItemView {{ background: {palette['input']}; color: {palette['text']};
  border: 1px solid {palette['border']}; border-radius: 8px; padding: 4px;
  outline: 0; selection-background-color: {palette['accent']}; }}
QTableWidget {{ background: {palette['panel']}; alternate-background-color: {palette['input']};
  color: {palette['text']}; border: none; gridline-color: {palette['border']};
  selection-background-color: {palette['accent']}; selection-color: {palette['window']}; }}
QHeaderView::section {{ background: {palette['input']}; color: {palette['muted']};
  border: none; border-bottom: 1px solid {palette['border']}; padding: 5px;
  font-size: 11px; font-weight: 700; }}
QStatusBar {{ background: {palette['panel']}; color: {palette['muted']}; }}
"""


class CleanComboBox(QComboBox):
    """Combo box with a theme-aware chevron and no native separator slot."""

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        color = self.palette().color(QPalette.Text)
        color.setAlpha(165 if self.isEnabled() else 80)
        painter.setPen(QPen(color, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        center_x = float(self.width() - 16)
        center_y = float(self.height()) / 2.0
        painter.drawLine(QPointF(center_x - 4.0, center_y - 2.0), QPointF(center_x, center_y + 2.0))
        painter.drawLine(QPointF(center_x, center_y + 2.0), QPointF(center_x + 4.0, center_y - 2.0))


class MetricCard(QFrame):
    def __init__(
        self,
        title: str,
        parent: QWidget | None = None,
        *,
        emphasis: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("metricCard")
        # Grid allocation should remain stable even when a changing detail string
        # has a much wider size hint (for example, "waiting for paired update").
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(2)
        self.title_label = QLabel(title, self)
        self.title_label.setProperty("role", "field")
        self.value_label = QLabel("—", self)
        self.value_label.setObjectName("metricValue")
        self.value_label.setProperty("emphasis", emphasis)
        self.detail_label = QLabel("Waiting for data", self)
        self.detail_label.setProperty("role", "field")
        self.detail_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        layout.addWidget(self.detail_label)

    def set_value(self, value: str, detail: str = "", warning: bool = False) -> None:
        self.value_label.setText(value)
        self.detail_label.setText(detail)
        self.detail_label.setToolTip(detail)
        self.value_label.setProperty("warning", warning)
        self.value_label.style().unpolish(self.value_label)
        self.value_label.style().polish(self.value_label)

    def set_title(self, title: str) -> None:
        self.title_label.setText(title)


class StatusStrip(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items: dict[str, tuple[QLabel, QLabel]] = {}
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(14)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)

    def add_item(self, key: str, title: str, value: str = "—") -> None:
        title_label = QLabel(title, self)
        title_label.hide()
        value_label = QLabel(value, self)
        value_label.setProperty("role", "statusBadge")
        value_label.setProperty("tone", "neutral")
        self._layout.addWidget(value_label)
        self._items[key] = (title_label, value_label)

    def set_item(self, key: str, value: str, tone: str = "neutral") -> None:
        label = self._items[key][1]
        label.setText(value)
        label.setProperty("tone", tone)
        label.style().unpolish(label)
        label.style().polish(label)


class CTMonitorWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        install_qt_window_raise_handler(self)
        self.app_context = load_app_context("ct_monitor")
        self.profile = self.app_context.profile
        self.backend = self.app_context.control_backend.name
        self.workflow = resolve_ct_monitor_workflow(self.profile)
        self.current_theme = resolve_initial_theme()

        self.refresh_interval_ms = int(self.workflow["refresh_interval_ms"])
        self.event_queue_size = int(self.workflow["event_queue_size"])
        self.pair_tolerance_s = float(self.workflow["pair_tolerance_s"])
        self.measurement_channel = str(self.workflow["measurement_channel"])
        self.measurement_label = str(self.workflow["measurement_label"])
        self.measurement_unit = str(self.workflow["measurement_unit"])
        self.minimum_upstream_value = float(self.workflow["minimum_upstream_value"])
        self.rolling_window = int(self.workflow["rolling_window"])
        self.rolling_window_options = tuple(
            int(value) for value in self.workflow["rolling_window_options"]
        )
        self.rolling_window_input_min, self.rolling_window_input_max = (
            int(value) for value in self.workflow["rolling_window_input_range"]
        )
        self.trend_window_s = float(self.workflow["trend_window_s"])
        self.trend_window_options_s = tuple(
            float(value) for value in self.workflow["trend_window_options_s"]
        )
        trend_input_range = self.workflow["trend_window_input_range_s"]
        self.trend_window_input_min = int(trend_input_range[0])
        self.trend_window_input_max = int(trend_input_range[1])
        self.max_trend_window_s = float(self.trend_window_input_max)
        self.history_size = int(self.workflow["history_size"])
        self.max_plot_points = int(self.workflow["max_plot_points"])
        raw_gap = self.workflow["trend_gap_s"][self.backend]
        self.trend_gap_s = None if raw_gap is None else float(raw_gap)
        self.efficiency_axis_default_max = float(
            self.workflow["efficiency_axis_default_max_percent"]
        )
        self.scale_to_display_unit = float(
            self.workflow["scale_to_display_unit"][self.backend]
        )
        raw_stale = self.workflow["stale_timeout_s"][self.backend]
        self.stale_timeout_s = None if raw_stale is None else float(raw_stale)

        self.measurement_elements = list_elements(
            self.app_context,
            kind="ct",
            logical_channel=self.measurement_channel,
            control_backend=self.backend,
        )
        self.sample_noun = "samples" if self.backend == "vm" else "shots"
        self.store = MonitorStore(queue_size=self.event_queue_size)
        self.pairer = ShotPairer()
        self.map_pairer = TransmissionMapPairer()
        self.transmission_history: list[TransmissionSample] = []
        self._last_map_sample: TransmissionMapSample | None = None
        self._pvs: dict[str, PV] = {}
        self._pv_error: str | None = None
        self._paused = False
        self._plots_dirty = True
        self._last_plot_draw = 0.0
        self._last_valid_sample: TransmissionSample | None = None
        self._measurement_order = {
            element.id: element.order for element in self.measurement_elements
        }
        self._measurement_display_names = {
            element.id: element.display_name for element in self.measurement_elements
        }
        self._map_element_ids = tuple(
            element.id for element in sorted(self.measurement_elements, key=lambda item: item.order)
        )

        self._build_ui()
        self._apply_theme()
        self._connect_pvs()
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._refresh)
        self.refresh_timer.start(self.refresh_interval_ms)
        self._refresh()

    def _build_ui(self) -> None:
        self.setWindowTitle(f"{self.profile.machine.display_name} ICT Monitor")
        self.resize(1280, 900)
        self.setMinimumSize(980, 720)
        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        header = QFrame(central)
        header.setObjectName("panel")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 10, 14, 10)
        title = QLabel("ICT Transmission Monitor", header)
        title.setObjectName("title")
        header_layout.addWidget(title)
        header_layout.addStretch(1)
        header_layout.addWidget(
            RuntimeContextWidget(
                machine_id=self.profile.machine.id,
                machine_display_name=self.profile.machine.display_name,
                control_backend=self.backend,
                parent=header,
            )
        )
        self.theme_button = QToolButton(header)
        self.theme_button.setObjectName("themeToggleButton")
        self.theme_button.setFixedSize(THEME_BUTTON_SIZE, THEME_BUTTON_SIZE)
        self.theme_button.clicked.connect(self._toggle_theme)
        header_layout.addWidget(self.theme_button)
        root.addWidget(header)

        controls = QFrame(central)
        controls.setObjectName("panel")
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(14, 10, 14, 10)
        controls_layout.setSpacing(10)

        upstream_layout = QVBoxLayout()
        upstream_layout.setSpacing(3)
        upstream_layout.addWidget(self._field_label("UPSTREAM", controls))
        self.upstream_combo = CleanComboBox(controls)
        self.upstream_combo.setObjectName("deviceCombo")
        self.upstream_combo.setMinimumWidth(150)
        upstream_layout.addWidget(self.upstream_combo)
        controls_layout.addLayout(upstream_layout)

        pair_arrow = QLabel("→", controls)
        pair_arrow.setObjectName("pairArrow")
        controls_layout.addWidget(pair_arrow)

        downstream_layout = QVBoxLayout()
        downstream_layout.setSpacing(3)
        downstream_layout.addWidget(self._field_label("DOWNSTREAM", controls))
        self.downstream_combo = CleanComboBox(controls)
        self.downstream_combo.setObjectName("deviceCombo")
        self.downstream_combo.setMinimumWidth(150)
        downstream_layout.addWidget(self.downstream_combo)
        controls_layout.addLayout(downstream_layout)

        self.swap_button = QPushButton("⇄", controls)
        self.swap_button.setToolTip("Swap upstream and downstream CTs")
        self.swap_button.clicked.connect(self._swap_selection)
        controls_layout.addWidget(self.swap_button)
        controls_layout.addStretch(1)
        self.selection_policy_label = self._field_label("", controls)
        self.selection_policy_label.hide()
        self.status_panel = StatusStrip(controls)
        self.status_panel.add_item("monitor", "MONITOR")
        controls_layout.addWidget(self.status_panel)
        controls_layout.addSpacing(6)
        self.pause_button = QPushButton("Pause", controls)
        self.pause_button.setObjectName("pauseButton")
        self.pause_button.setProperty("paused", False)
        self.pause_button.setToolTip("Pause chart acquisition; incoming samples will be discarded")
        self.pause_button.clicked.connect(self._toggle_pause)
        self.clear_button = QPushButton("Clear", controls)
        self.clear_button.setObjectName("clearHistoryButton")
        self.clear_button.setToolTip("Clear in-memory CT history")
        self.clear_button.clicked.connect(self._clear_history)
        controls_layout.addWidget(self.pause_button)
        controls_layout.addWidget(self.clear_button)
        root.addWidget(controls)

        for element in self.measurement_elements:
            self.upstream_combo.addItem(element.display_name, element.id)
            self.downstream_combo.addItem(element.display_name, element.id)
        self._select_combo_data(self.upstream_combo, str(self.workflow["default_upstream"]))
        self._select_combo_data(self.downstream_combo, str(self.workflow["default_downstream"]))
        self.upstream_combo.currentIndexChanged.connect(self._selection_changed)
        self.downstream_combo.currentIndexChanged.connect(self._selection_changed)
        self._update_selection_policy()

        map_panel = QFrame(central)
        map_panel.setObjectName("panel")
        map_layout = QVBoxLayout(map_panel)
        map_layout.setContentsMargins(12, 8, 12, 10)
        map_layout.setSpacing(6)
        map_title = QLabel("Transmission map", map_panel)
        map_title.setObjectName("sectionTitle")
        map_layout.addWidget(map_title)
        self.map_table = QTableWidget(max(0, len(self._map_element_ids) - 1), 6, map_panel)
        self.map_table.setHorizontalHeaderLabels(
            ("UPSTREAM", self.measurement_unit, "TRANSMISSION", "DOWNSTREAM", self.measurement_unit, "STATUS")
        )
        self.map_table.verticalHeader().hide()
        self.map_table.setAlternatingRowColors(True)
        self.map_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.map_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.map_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.map_table.setShowGrid(False)
        self.map_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.map_table.setFixedHeight(
            self.map_table.horizontalHeader().height() + self.map_table.rowCount() * 31 + 4
        )
        for row, (upstream, downstream) in enumerate(
            zip(self._map_element_ids, self._map_element_ids[1:])
        ):
            values = (
                self._measurement_display_names.get(upstream, upstream),
                "—",
                "—",
                self._measurement_display_names.get(downstream, downstream),
                "—",
                "Waiting for data",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, (upstream, downstream))
                self.map_table.setItem(row, column, item)
        self.map_table.cellClicked.connect(self._select_map_segment)
        map_layout.addWidget(self.map_table)
        root.addWidget(map_panel)

        self.trend_window_combo = CleanComboBox(central)
        self.trend_window_combo.setEditable(True)
        self.trend_window_combo.setInsertPolicy(QComboBox.NoInsert)
        self.trend_window_combo.lineEdit().setValidator(
            QIntValidator(
                self.trend_window_input_min,
                self.trend_window_input_max,
                self.trend_window_combo,
            )
        )
        self.trend_window_combo.setMinimumWidth(96)
        self.trend_window_combo.setToolTip(
            f"Choose a preset or enter {self.trend_window_input_min}–"
            f"{self.trend_window_input_max} seconds"
        )
        for seconds in self.trend_window_options_s:
            self.trend_window_combo.addItem(f"{seconds:g}", int(seconds))
        self._select_combo_data(self.trend_window_combo, self.trend_window_s)
        self.rolling_window_combo = CleanComboBox(central)
        self.rolling_window_combo.setEditable(True)
        self.rolling_window_combo.setInsertPolicy(QComboBox.NoInsert)
        self.rolling_window_combo.lineEdit().setValidator(
            QIntValidator(
                self.rolling_window_input_min,
                self.rolling_window_input_max,
                self.rolling_window_combo,
            )
        )
        self.rolling_window_combo.setMinimumWidth(96)
        self.rolling_window_combo.setToolTip(
            f"Choose a preset or enter {self.rolling_window_input_min}–"
            f"{self.rolling_window_input_max} {self.sample_noun}"
        )
        for count in self.rolling_window_options:
            self.rolling_window_combo.addItem(str(count), count)
        self._select_combo_data(self.rolling_window_combo, self.rolling_window)

        metrics = QGridLayout()
        metrics.setHorizontalSpacing(10)
        self.upstream_card = MetricCard(f"Upstream {self.measurement_label}", central)
        self.downstream_card = MetricCard(f"Downstream {self.measurement_label}", central)
        self.efficiency_card = MetricCard(
            "Transmission efficiency",
            central,
            emphasis=True,
        )
        self.trend_window_combo.activated.connect(
            lambda _index: self._apply_trend_window_input()
        )
        self.trend_window_combo.lineEdit().editingFinished.connect(
            self._apply_trend_window_input
        )
        self.rolling_window_combo.activated.connect(
            lambda _index: self._apply_rolling_window_input()
        )
        self.rolling_window_combo.lineEdit().editingFinished.connect(
            self._apply_rolling_window_input
        )
        metrics.addWidget(self.upstream_card, 0, 0)
        metrics.addWidget(self.efficiency_card, 0, 1)
        metrics.addWidget(self.downstream_card, 0, 2)
        metrics.setColumnStretch(0, 3)
        metrics.setColumnStretch(1, 4)
        metrics.setColumnStretch(2, 3)
        root.addLayout(metrics)

        plot_panel = QFrame(central)
        plot_panel.setObjectName("panel")
        plot_panel.setToolTip(
            f"Pair tolerance: {self.pair_tolerance_s:.1f} s\n"
            f"Upstream threshold: {self.minimum_upstream_value:g} "
            f"{self.measurement_unit}"
        )
        plot_layout = QVBoxLayout(plot_panel)
        plot_layout.setContentsMargins(8, 8, 8, 8)
        plot_toolbar = QHBoxLayout()
        plot_toolbar.setContentsMargins(6, 0, 6, 0)
        plot_toolbar.setSpacing(7)
        self.plot_title_label = QLabel("CT trends", plot_panel)
        self.plot_title_label.setObjectName("sectionTitle")
        plot_toolbar.addWidget(self.plot_title_label)
        plot_toolbar.addStretch(1)
        plot_toolbar.addWidget(self._field_label("ROLLING", plot_panel))
        plot_toolbar.addWidget(self.rolling_window_combo)
        plot_toolbar.addWidget(self._field_label(self.sample_noun, plot_panel))
        plot_toolbar.addSpacing(8)
        plot_toolbar.addWidget(self._field_label("SPAN", plot_panel))
        plot_toolbar.addWidget(self.trend_window_combo)
        plot_toolbar.addWidget(self._field_label("s", plot_panel))
        plot_layout.addLayout(plot_toolbar)
        self.figure = Figure(figsize=(11, 7), tight_layout=True)
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.measurement_axis = self.figure.add_subplot(2, 1, 1)
        self.efficiency_axis = self.figure.add_subplot(2, 1, 2)
        plot_layout.addWidget(self.canvas)
        root.addWidget(plot_panel, 1)
        self._update_selection_labels()

    @staticmethod
    def _field_label(text: str, parent: QWidget) -> QLabel:
        label = QLabel(text, parent)
        label.setProperty("role", "field")
        return label

    @staticmethod
    def _select_combo_data(combo: QComboBox, value: object) -> None:
        for index in range(combo.count()):
            if combo.itemData(index) == value:
                combo.setCurrentIndex(index)
                return

    def _apply_trend_window_input(self) -> None:
        value = parse_bounded_integer_input(
            self.trend_window_combo.currentText(),
            self.trend_window_input_min,
            self.trend_window_input_max,
        )
        if value is None:
            self.trend_window_combo.setEditText(f"{self.trend_window_s:g}")
            return
        self.trend_window_s = float(value)
        self.trend_window_combo.setEditText(str(value))
        self._plots_dirty = True

    def _apply_rolling_window_input(self) -> None:
        value = parse_bounded_integer_input(
            self.rolling_window_combo.currentText(),
            self.rolling_window_input_min,
            self.rolling_window_input_max,
        )
        if value is None:
            self.rolling_window_combo.setEditText(str(self.rolling_window))
            return
        self.rolling_window = value
        self.rolling_window_combo.setEditText(str(value))

    def _connect_pvs(self) -> None:
        channel_elements = [
            (element, self.measurement_channel)
            for element in self.measurement_elements
        ]
        for element, channel in channel_elements:
            key = element.id
            pv_name = resolve_channel(self.app_context, key, channel)
            self.store.set_connected(key, False)
            try:
                pv = PV(
                    pv_name,
                    form="time" if channel == self.measurement_channel else "ctrl",
                    connection_callback=self._connection_callback(key),
                    auto_monitor=True,
                )
                self._pvs[key] = pv
                pv.add_callback(
                    self._value_callback(key, channel),
                    with_ctrlvars=False,
                )
            except Exception as exc:
                self._pv_error = str(exc)

    def _connection_callback(self, key: str):
        def callback(conn=False, **_kwargs):
            self.store.set_connected(key, bool(conn))
            if not conn:
                self.store.clear_queues(key)

        return callback

    def _value_callback(self, key: str, channel: str):
        def callback(value=None, timestamp=None, status=None, severity=None, **kwargs):
            fallback_units = (
                self.measurement_unit
                if channel == self.measurement_channel
                else "A"
            )
            units = str(kwargs.get("units") or fallback_units)
            self.store.update(
                key,
                value=value,
                timestamp=time.time() if timestamp is None else timestamp,
                connected=True,
                units=units,
                status=status,
                severity=severity,
            )

        return callback

    def _selected_ids(self) -> tuple[str, str]:
        return str(self.upstream_combo.currentData()), str(self.downstream_combo.currentData())

    def _selection_changed(self) -> None:
        upstream, downstream = self._selected_ids()
        if upstream == downstream and self.downstream_combo.count() > 1:
            next_index = (self.downstream_combo.currentIndex() + 1) % self.downstream_combo.count()
            self.downstream_combo.blockSignals(True)
            self.downstream_combo.setCurrentIndex(next_index)
            self.downstream_combo.blockSignals(False)
            upstream, downstream = self._selected_ids()
        self.transmission_history.clear()
        self._last_valid_sample = None
        self.pairer.reset()
        self.store.clear_queues(upstream, downstream)
        self._update_selection_policy()
        self._update_selection_labels()
        self._plots_dirty = True

    def _select_map_segment(self, row: int, _column: int) -> None:
        if row < 0 or row >= self.map_table.rowCount():
            return
        pair = self.map_table.item(row, 0).data(Qt.UserRole)
        if not pair:
            return
        upstream, downstream = pair
        self.upstream_combo.blockSignals(True)
        self.downstream_combo.blockSignals(True)
        self._select_combo_data(self.upstream_combo, upstream)
        self._select_combo_data(self.downstream_combo, downstream)
        self.upstream_combo.blockSignals(False)
        self.downstream_combo.blockSignals(False)
        self._selection_changed()

    def _update_transmission_map(self, batch, now: float) -> None:
        if batch.samples and not self._paused:
            self._last_map_sample = batch.samples[-1]
        sample = self._last_map_sample
        values = dict(sample.station_values) if sample is not None else {}
        global_error = batch.status if batch.status in {
            "PV disconnected",
            "PV alarm",
            "invalid value",
            "missing timestamp",
            "stale data",
            "timestamp mismatch",
        } else None
        for row, (upstream, downstream) in enumerate(
            zip(self._map_element_ids, self._map_element_ids[1:])
        ):
            segment = (
                sample.segments[row]
                if sample is not None and row < len(sample.segments)
                else None
            )
            upstream_value = values.get(upstream)
            downstream_value = values.get(downstream)
            status = "Paused" if self._paused else (
                global_error or (segment.status if segment is not None else batch.status)
            )
            cells = (
                self._measurement_display_names.get(upstream, upstream),
                f"{upstream_value:.4g} {self.measurement_unit}"
                if upstream_value is not None else "—",
                f"{segment.efficiency_percent:.2f}%"
                if segment is not None and segment.efficiency_percent is not None
                else "N/A",
                self._measurement_display_names.get(downstream, downstream),
                f"{downstream_value:.4g} {self.measurement_unit}"
                if downstream_value is not None else "—",
                status,
            )
            for column, value in enumerate(cells):
                self.map_table.item(row, column).setText(value)
        self.map_table.setToolTip(
            f"{batch.status}; {self._age_text(sample, now) if sample is not None else 'waiting for data'}"
        )

    def _update_selection_labels(self) -> None:
        upstream_id, downstream_id = self._selected_ids()
        upstream_name = self._measurement_display_names.get(upstream_id, upstream_id)
        downstream_name = self._measurement_display_names.get(downstream_id, downstream_id)
        measurement = self.measurement_label.capitalize()
        self.upstream_card.set_title(f"{upstream_name} · upstream {measurement}")
        self.downstream_card.set_title(f"{downstream_name} · downstream {measurement}")
        self.efficiency_card.set_title(
            f"{upstream_name} → {downstream_name} transmission"
        )
        self.plot_title_label.setText(f"{upstream_name} / {downstream_name} trends")

    def _is_reverse_order(self) -> bool:
        upstream, downstream = self._selected_ids()
        return self._measurement_order[upstream] > self._measurement_order[downstream]

    def _update_selection_policy(self) -> None:
        if self._is_reverse_order():
            palette = DARK if self.current_theme == "dark" else LIGHT
            self.selection_policy_label.setText("Reverse order")
            self.selection_policy_label.setStyleSheet(
                f"color: {palette['warning']}; background: transparent;"
            )
        else:
            self.selection_policy_label.setText("")
            self.selection_policy_label.setStyleSheet("background: transparent;")

    def _swap_selection(self) -> None:
        up_index = self.upstream_combo.currentIndex()
        down_index = self.downstream_combo.currentIndex()
        self.upstream_combo.blockSignals(True)
        self.downstream_combo.blockSignals(True)
        self.upstream_combo.setCurrentIndex(down_index)
        self.downstream_combo.setCurrentIndex(up_index)
        self.upstream_combo.blockSignals(False)
        self.downstream_combo.blockSignals(False)
        self._selection_changed()

    def _toggle_pause(self) -> None:
        self._paused = not self._paused
        self.pause_button.setText("Resume" if self._paused else "Pause")
        self.pause_button.setProperty("paused", self._paused)
        self.pause_button.style().unpolish(self.pause_button)
        self.pause_button.style().polish(self.pause_button)
        self.store.clear_queues(*self._map_element_ids)
        self.pairer.reset()
        self.map_pairer.reset()
        self._last_map_sample = None
        message = (
            "Acquisition paused; incoming samples are discarded."
            if self._paused
            else "Acquisition resumed; waiting for new samples."
        )
        self.statusBar().showMessage(message, 3000)

    def _clear_history(self) -> None:
        self.transmission_history.clear()
        self._last_valid_sample = None
        self.pairer.reset()
        self.map_pairer.reset()
        self._last_map_sample = None
        self.store.clear_queues(*self._map_element_ids)
        self._plots_dirty = True

    def _refresh(self) -> None:
        now = time.time()
        snapshot = self.store.snapshot()
        queued = self.store.queued_snapshot()
        upstream_id, downstream_id = self._selected_ids()
        result = self.pairer.pair_queued(
            queued,
            snapshot,
            upstream_id,
            downstream_id,
            now=now,
            scale_to_display_unit=self.scale_to_display_unit,
            tolerance_s=self.pair_tolerance_s,
            stale_timeout_s=self.stale_timeout_s,
            minimum_upstream_value=self.minimum_upstream_value,
        )
        map_result = self.map_pairer.pair_queued(
            queued,
            snapshot,
            self._map_element_ids,
            now=now,
            scale_to_display_unit=self.scale_to_display_unit,
            tolerance_s=self.pair_tolerance_s,
            stale_timeout_s=self.stale_timeout_s,
            minimum_upstream_value=self.minimum_upstream_value,
        )

        if not self._paused and result.samples:
            self.transmission_history.extend(result.samples)
            overflow = len(self.transmission_history) - self.history_size
            if overflow > 0:
                del self.transmission_history[:overflow]
            self._last_valid_sample = result.samples[-1]
            self._plots_dirty = True
        if self.transmission_history and now - self._last_plot_draw >= 1.0:
            self._plots_dirty = True
        pairing_status = self._pairing_display_status(
            result.status,
            len(result.samples),
            result.mismatched_samples,
        )
        self._update_measurement_cards(snapshot, upstream_id, downstream_id, now)
        self._update_transmission_map(map_result, now)
        self._update_efficiency_cards(pairing_status, now)
        self._update_status(snapshot, pairing_status)
        if self._plots_dirty:
            self._draw_plots(now)
            self._last_plot_draw = now
            self._plots_dirty = False

    def _pairing_display_status(
        self,
        status: str,
        new_samples: int,
        mismatched_samples: int,
    ) -> str:
        if self._paused:
            return "Paused · incoming samples discarded"
        if new_samples and status == "valid":
            noun = self.sample_noun[:-1] if new_samples == 1 else self.sample_noun
            mismatch = (
                f" · {mismatched_samples} unmatched discarded"
                if mismatched_samples
                else ""
            )
            return f"Paired · {new_samples} new {noun}{mismatch}"
        if status == "waiting for paired update" and self._last_valid_sample is not None:
            return "Paired · waiting for next update"
        return status

    @staticmethod
    def _age_text(sample: SignalSample | TransmissionSample | None, now: float) -> str:
        if sample is None or sample.timestamp is None or not math.isfinite(sample.timestamp):
            return "age unknown"
        age = max(0.0, now - sample.timestamp)
        if age < 1.0:
            return f"updated {age * 1000:.0f} ms ago"
        if age < 60.0:
            return f"updated {age:.1f} s ago"
        return f"updated {age / 60.0:.1f} min ago"

    def _measurement_card_state(
        self,
        sample: SignalSample | None,
        element_id: str,
        now: float,
    ) -> tuple[str, str, bool]:
        if sample is None:
            return "—", f"{element_id} · waiting for data", True
        age = self._age_text(sample, now)
        if not sample.connected:
            return "N/A", f"{element_id} · disconnected", True
        if (sample.severity or 0) >= 2:
            return "N/A", f"{element_id} · PV alarm · {age}", True
        if sample.value is None or not math.isfinite(sample.value):
            return "N/A", f"{element_id} · invalid value", True
        if sample.timestamp is None or not math.isfinite(sample.timestamp):
            return "N/A", f"{element_id} · missing timestamp", True
        if (
            self.stale_timeout_s is not None
            and sample.timestamp is not None
            and now - sample.timestamp > self.stale_timeout_s
        ):
            return "N/A", f"{element_id} · stale · {age}", True
        return (
            f"{sample.value * self.scale_to_display_unit:.4g} {self.measurement_unit}",
            f"{element_id} · {age}",
            False,
        )

    def _update_measurement_cards(
        self,
        snapshot: dict[str, SignalSample],
        upstream_id: str,
        downstream_id: str,
        now: float,
    ) -> None:
        self.upstream_card.set_value(
            *self._measurement_card_state(snapshot.get(upstream_id), upstream_id, now)
        )
        self.downstream_card.set_value(
            *self._measurement_card_state(snapshot.get(downstream_id), downstream_id, now)
        )

    def _update_efficiency_cards(self, pairing_status: str, now: float) -> None:
        sample = self._last_valid_sample
        if pairing_status in {
            "PV disconnected",
            "PV alarm",
            "invalid value",
            "missing timestamp",
            "stale data",
            "timestamp mismatch",
            "upstream below threshold",
        }:
            self.efficiency_card.set_value("N/A", pairing_status, True)
        elif sample is None:
            self.efficiency_card.set_value("—", pairing_status)
        else:
            warning = sample.efficiency_percent > 100.0 or self._is_reverse_order()
            mean, stddev = rolling_statistics(
                self.transmission_history,
                self.rolling_window,
            )
            if mean is None or stddev is None:
                detail = self._age_text(sample, now)
            else:
                count = min(len(self.transmission_history), self.rolling_window)
                detail = (
                    f"Rolling {count}: {mean:.2f}% ± {stddev:.2f}% · "
                    f"{self._age_text(sample, now)}"
                )
            self.efficiency_card.set_value(
                f"{sample.efficiency_percent:.2f}%",
                detail,
                warning,
            )

    def _update_status(
        self,
        snapshot: dict[str, SignalSample],
        pairing_status: str,
    ) -> None:
        upstream_id, downstream_id = self._selected_ids()
        active_scalar_ids = [upstream_id, downstream_id]
        connected = sum(
            bool(snapshot.get(element_id) and snapshot[element_id].connected)
            for element_id in active_scalar_ids
        )
        total = len(active_scalar_ids)
        if self._paused:
            tone = "neutral"
            status = "Paused"
        elif connected < total:
            tone = "danger" if connected == 0 else "neutral"
            status = f"{connected}/{total} channels"
        elif self._is_reverse_order():
            tone = "danger"
            status = "Reverse order"
        else:
            status, tone = self._compact_pairing_status(pairing_status)
        self.status_panel.set_item("monitor", f"● {status}", tone)
        if self._pv_error:
            self.statusBar().showMessage(f"EPICS unavailable: {self._pv_error}")

    @staticmethod
    def _compact_pairing_status(pairing_status: str) -> tuple[str, str]:
        if pairing_status.startswith("Paired"):
            return "Data ready", "success"
        if pairing_status.startswith("Paused"):
            return "Paused", "neutral"
        if pairing_status == "waiting for paired update":
            return "Waiting for pair", "neutral"
        if pairing_status == "waiting for data":
            return "Waiting for data", "neutral"
        labels = {
            "PV disconnected": "PV disconnected",
            "PV alarm": "PV alarm",
            "invalid value": "Invalid value",
            "missing timestamp": "No timestamp",
            "stale data": "Stale data",
            "timestamp mismatch": "Time mismatch",
            "upstream below threshold": "Low upstream",
        }
        return labels.get(pairing_status, pairing_status), "danger"

    def _transmission_plot_series(
        self,
        samples: list[TransmissionSample],
        now: float,
    ) -> tuple[list[float], list[float], list[float], list[float]]:
        x: list[float] = []
        upstream: list[float] = []
        downstream: list[float] = []
        efficiency: list[float] = []
        previous_timestamp: float | None = None
        for sample in samples:
            if (
                previous_timestamp is not None
                and self.trend_gap_s is not None
                and sample.timestamp - previous_timestamp > self.trend_gap_s
            ):
                x.append((previous_timestamp + sample.timestamp) / 2.0 - now)
                upstream.append(float("nan"))
                downstream.append(float("nan"))
                efficiency.append(float("nan"))
            x.append(sample.timestamp - now)
            upstream.append(sample.upstream_value)
            downstream.append(sample.downstream_value)
            efficiency.append(sample.efficiency_percent)
            previous_timestamp = sample.timestamp
        return x, upstream, downstream, efficiency

    @staticmethod
    def _set_zero_inclusive_ylim(axis, values: list[float]) -> None:
        finite = [value for value in values if math.isfinite(value)]
        if not finite:
            return
        low = min(finite)
        high = max(finite)
        span = high - low
        margin = span * 0.1 if span > 0 else max(abs(high) * 0.1, 1e-3)
        bottom = min(0.0, low - margin)
        top = max(0.0, high + margin)
        if bottom == top:
            top = bottom + 1.0
        axis.set_ylim(bottom, top)

    def _draw_plots(self, now: float) -> None:
        palette = DARK if self.current_theme == "dark" else LIGHT
        axes = [self.measurement_axis, self.efficiency_axis]
        for axis in axes:
            axis.clear()
            axis.set_facecolor(palette["panel"])
            axis.grid(True, color=palette["grid"], linewidth=0.7, alpha=0.8)
            axis.tick_params(colors=palette["muted"])
            for spine in axis.spines.values():
                spine.set_color(palette["border"])

        cutoff = now - self.trend_window_s
        recent_samples = [
            sample for sample in self.transmission_history if sample.timestamp >= cutoff
        ]
        plot_samples = downsample_transmission_samples(
            recent_samples,
            self.max_plot_points,
        )
        x, upstream, downstream, efficiency = self._transmission_plot_series(
            plot_samples,
            now,
        )
        up_id, down_id = self._selected_ids()
        up_name = self._measurement_display_names.get(up_id, up_id)
        down_name = self._measurement_display_names.get(down_id, down_id)
        self.measurement_axis.plot(
            x,
            upstream,
            color=palette["upstream"],
            label=up_name,
            linewidth=1.7,
        )
        self.measurement_axis.plot(
            x,
            downstream,
            color=palette["downstream"],
            label=down_name,
            linewidth=1.7,
        )
        self.measurement_axis.legend(
            loc="upper left",
            frameon=False,
            labelcolor=palette["text"],
        )
        self.efficiency_axis.plot(
            x,
            efficiency,
            color=palette["efficiency"],
            linewidth=1.8,
        )
        self.efficiency_axis.axhline(
            100.0,
            color=palette["warning"],
            linestyle="--",
            linewidth=1.0,
        )
        self._set_zero_inclusive_ylim(self.measurement_axis, [*upstream, *downstream])
        finite_efficiency = [value for value in efficiency if math.isfinite(value)]
        efficiency_upper = self.efficiency_axis_default_max
        if finite_efficiency:
            efficiency_upper = max(
                efficiency_upper,
                max(finite_efficiency) * 1.05,
            )
        self.efficiency_axis.set_ylim(0.0, efficiency_upper)
        self.measurement_axis.set_ylabel(
            f"{self.measurement_label.capitalize()} ({self.measurement_unit})",
            color=palette["text"],
        )
        plot_count = len(plot_samples)
        source_count = len(recent_samples)
        point_note = f" · {plot_count}/{source_count} plotted" if source_count > plot_count else ""
        self.measurement_axis.set_title(
            f"{up_name} / {down_name} {self.measurement_label}{point_note}",
            color=palette["text"],
            loc="left",
            fontweight="bold",
        )
        self.efficiency_axis.set_ylabel("Efficiency (%)", color=palette["text"])
        self.efficiency_axis.set_title("Transmission efficiency", color=palette["text"], loc="left", fontweight="bold")
        self.efficiency_axis.set_xlabel("Time from now (s)", color=palette["text"])
        self.measurement_axis.set_xlim(-self.trend_window_s, 0.0)
        self.efficiency_axis.set_xlim(-self.trend_window_s, 0.0)

        self.figure.patch.set_facecolor(palette["panel"])
        self.canvas.draw_idle()

    def _apply_theme(self) -> None:
        palette = DARK if self.current_theme == "dark" else LIGHT
        self.setStyleSheet(_stylesheet(palette))
        self.theme_button.setText("☀" if self.current_theme == "dark" else "☾")
        self.theme_button.setToolTip(
            "Switch to light theme" if self.current_theme == "dark" else "Switch to dark theme"
        )
        if hasattr(self, "selection_policy_label"):
            self._update_selection_policy()
        self._plots_dirty = True
        if hasattr(self, "figure"):
            self._draw_plots(time.time())
            self._last_plot_draw = time.time()
            self._plots_dirty = False

    def _toggle_theme(self) -> None:
        self.current_theme = "light" if self.current_theme == "dark" else "dark"
        self._apply_theme()

    def closeEvent(self, event) -> None:
        self.refresh_timer.stop()
        for pv in self._pvs.values():
            pv.clear_callbacks()
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    window = CTMonitorWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
    QHeaderView,
