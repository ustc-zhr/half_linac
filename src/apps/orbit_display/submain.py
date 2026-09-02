import sys
from pathlib import Path

_REPO_BOOTSTRAP_ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / "repo_bootstrap.py").is_file()
)
if str(_REPO_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_BOOTSTRAP_ROOT))

from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

import numpy as np
from epics import caget_many
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from half_linac.src.shared.machine_profile import (
    get_workflow,
    list_elements,
    load_app_context,
    resolve_channel,
)
from half_linac.src.shared.window_activation import install_qt_window_raise_handler


DEFAULT_DETAIL_PALETTE = {
    "window_bg": "#0f1519",
    "window_fg": "#e6edf2",
    "panel_bg": "#172027",
    "panel_border": "#24333d",
    "summary_bg": "#1b262d",
    "summary_border": "#2b3a45",
    "summary_title_fg": "#f3efe3",
    "muted_fg": "#90a1ad",
    "input_bg": "#10171c",
    "input_border": "#31424d",
    "input_fg": "#edf3f7",
    "orbit_x": "#6cb6ff",
    "orbit_y": "#f4c46a",
    "status_bg": "#11191f",
    "status_fg": "#c9d5dc",
    "metric_active_fg": "#45d0bc",
    "metric_warning_fg": "#e4b86f",
}


def build_bpm_detail_theme(palette):
    return """
QMainWindow, QWidget#bpmDetailCentral {{
    background-color: {window_bg};
    color: {window_fg};
    font-family: "IBM Plex Sans", "Source Han Sans SC", "Segoe UI", sans-serif;
}}

QFrame#detailHeader {{
    background-color: {summary_bg};
    border: 1px solid {summary_border};
    border-radius: 14px;
}}

QFrame#detailCard {{
    background-color: {panel_bg};
    border: 1px solid {panel_border};
    border-radius: 14px;
}}

QLabel#detailTitle {{
    background: transparent;
    color: {summary_title_fg};
    border: none;
    font-size: 20px;
    font-weight: 700;
}}

QLabel#detailCardTitle {{
    background: transparent;
    color: {summary_title_fg};
    border: none;
    font-size: 15px;
    font-weight: 700;
}}

QLabel[role="meta"] {{
    background: transparent;
    color: {muted_fg};
    border: none;
    font-size: 11px;
    font-weight: 600;
}}

QLabel#detailConnection {{
    background: transparent;
    border: none;
    font-size: 12px;
    font-weight: 700;
}}

QLabel#detailConnection[status="live"] {{
    color: {metric_active_fg};
}}

QLabel#detailConnection[status="warning"] {{
    color: {metric_warning_fg};
}}

QTableWidget#bpmTable {{
    background-color: {input_bg};
    alternate-background-color: {panel_bg};
    color: {input_fg};
    border: 1px solid {input_border};
    border-radius: 10px;
    gridline-color: {panel_border};
    selection-background-color: {summary_bg};
    selection-color: {input_fg};
    outline: none;
    padding: 3px;
}}

QTableWidget#bpmTable::item {{
    border: none;
    padding: 6px 10px;
}}

QHeaderView::section {{
    background-color: {summary_bg};
    color: {muted_fg};
    border: none;
    border-bottom: 1px solid {input_border};
    padding: 8px 10px;
    font-size: 11px;
    font-weight: 700;
}}

QStatusBar {{
    background-color: {status_bg};
    color: {status_fg};
}}
""".format_map(palette)


class myWindow(QMainWindow):
    def __init__(self, refresh_interval_ms=1000, palette=None, parent=None):
        super().__init__(parent)
        install_qt_window_raise_handler(self)
        self.app_context = load_app_context("orbit_display")
        self.machine_profile = self.app_context.profile
        self.control_backend = self.app_context.control_backend.name
        self.refresh_interval_ms = max(100, int(refresh_interval_ms))
        self.bpm_position_scale_to_mm = self._resolve_bpm_position_scale_to_mm()
        self.bpm_elements = list_elements(self.app_context, kind="bpm")
        self.bpm_ids = [element.id for element in self.bpm_elements]
        self.bpm_x_pvs = [
            resolve_channel(self.app_context, bpm_id, "x") for bpm_id in self.bpm_ids
        ]
        self.bpm_y_pvs = [
            resolve_channel(self.app_context, bpm_id, "y") for bpm_id in self.bpm_ids
        ]
        self.pvlx_val = [None] * len(self.bpm_x_pvs)
        self.pvly_val = [None] * len(self.bpm_y_pvs)
        self._current_palette = dict(palette or DEFAULT_DETAIL_PALETTE)

        self._build_ui()
        self._configure_bpm_table()
        self.apply_theme(self._current_palette)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.bpmvalue_dis)
        self.timer.start(self.refresh_interval_ms)
        self.bpmvalue_dis()

    def _build_ui(self):
        self.setWindowTitle(f"{self.machine_profile.machine.display_name} BPM Detail")
        self.resize(760, 720)
        self.setMinimumSize(620, 460)

        central = QWidget(self)
        central.setObjectName("bpmDetailCentral")
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(12)

        header = QFrame(central)
        header.setObjectName("detailHeader")
        header.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 12, 14, 12)
        header_layout.setSpacing(14)

        title = QLabel(f"{self.machine_profile.machine.display_name} BPM Detail", header)
        title.setObjectName("detailTitle")
        header_layout.addWidget(title)
        header_layout.addStretch(1)

        self.machine_label = QLabel(
            f"Machine: {self.machine_profile.machine.display_name}",
            header,
        )
        self.backend_label = QLabel(f"Backend: {self.control_backend.upper()}", header)
        self.refresh_label = QLabel(header)
        for label in (self.machine_label, self.backend_label, self.refresh_label):
            label.setProperty("role", "meta")
            header_layout.addWidget(label)
        outer.addWidget(header)

        card = QFrame(central)
        card.setObjectName("detailCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 12, 12, 12)
        card_layout.setSpacing(10)

        card_header = QHBoxLayout()
        card_header.setContentsMargins(0, 0, 0, 0)
        card_title = QLabel("Beam Position Readback", card)
        card_title.setObjectName("detailCardTitle")
        self.connection_label = QLabel("Waiting", card)
        self.connection_label.setObjectName("detailConnection")
        self.connection_label.setProperty("status", "warning")
        card_header.addWidget(card_title)
        card_header.addStretch(1)
        card_header.addWidget(self.connection_label)
        card_layout.addLayout(card_header)

        self.bpm_table = QTableWidget(card)
        self.bpm_table.setObjectName("bpmTable")
        self.bpm_table.setColumnCount(3)
        self.bpm_table.setHorizontalHeaderLabels(
            ("BPM", "Horizontal X (mm)", "Vertical Y (mm)")
        )
        self.bpm_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.bpm_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.bpm_table.setAlternatingRowColors(True)
        self.bpm_table.setShowGrid(False)
        self.bpm_table.verticalHeader().setVisible(False)
        self.bpm_table.verticalHeader().setDefaultSectionSize(34)
        header_view = self.bpm_table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.Stretch)
        header_view.setSectionResizeMode(2, QHeaderView.Stretch)
        card_layout.addWidget(self.bpm_table)
        outer.addWidget(card, 1)

        self.statusBar().setSizeGripEnabled(False)
        self._update_refresh_label()

    def _configure_bpm_table(self):
        self.bpm_table.setRowCount(len(self.bpm_ids))
        self.x_value_widgets = []
        self.y_value_widgets = []
        for row, bpm_id in enumerate(self.bpm_ids):
            bpm_item = QTableWidgetItem(bpm_id)
            x_item = QTableWidgetItem("--")
            y_item = QTableWidgetItem("--")
            bpm_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            x_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            y_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.bpm_table.setItem(row, 0, bpm_item)
            self.bpm_table.setItem(row, 1, x_item)
            self.bpm_table.setItem(row, 2, y_item)
            self.x_value_widgets.append(x_item)
            self.y_value_widgets.append(y_item)

    def apply_theme(self, palette):
        self._current_palette = dict(palette)
        self.setStyleSheet(build_bpm_detail_theme(self._current_palette))
        self._apply_table_chrome_theme()
        x_color = QColor(self._current_palette["orbit_x"])
        y_color = QColor(self._current_palette["orbit_y"])
        text_color = QColor(self._current_palette["window_fg"])
        for row in range(self.bpm_table.rowCount()):
            self.bpm_table.item(row, 0).setForeground(text_color)
            self.bpm_table.item(row, 1).setForeground(x_color)
            self.bpm_table.item(row, 2).setForeground(y_color)
        self._refresh_connection_style()

    def _apply_table_chrome_theme(self):
        palette = self._current_palette
        self.bpm_table.horizontalHeader().setStyleSheet(
            """
QHeaderView {
    background: %(summary_bg)s;
    border: none;
}
QHeaderView::section {
    background: %(summary_bg)s;
    color: %(muted_fg)s;
    border: none;
    border-bottom: 1px solid %(input_border)s;
    padding: 8px 10px;
    font-size: 11px;
    font-weight: 700;
}
""" % palette
        )
        self.bpm_table.verticalScrollBar().setStyleSheet(
            """
QScrollBar:vertical {
    background: %(input_bg)s;
    border: none;
    width: 12px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: %(input_border)s;
    border: none;
    border-radius: 4px;
    min-height: 28px;
    margin: 2px;
}
QScrollBar::handle:vertical:hover {
    background: %(muted_fg)s;
}
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {
    border: none;
    height: 0;
}
QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {
    background: transparent;
}
""" % palette
        )

    def set_refresh_interval_ms(self, refresh_interval_ms):
        self.refresh_interval_ms = max(100, int(refresh_interval_ms))
        self.timer.start(self.refresh_interval_ms)
        self._update_refresh_label()

    def _update_refresh_label(self):
        self.refresh_label.setText(f"Refresh: {self.refresh_interval_ms / 1000:g} s")

    def _resolve_bpm_position_scale_to_mm(self):
        workflow = get_workflow(self.machine_profile, "orbit")
        scale_by_backend = workflow.get("bpm_position_scale_to_mm", {})
        if isinstance(scale_by_backend, dict):
            try:
                return float(scale_by_backend.get(self.control_backend, 1000.0))
            except (TypeError, ValueError):
                pass
        return 1000.0

    def _format_bpm_value(self, value):
        if value is None:
            return "--"
        try:
            scaled_value = float(value) * self.bpm_position_scale_to_mm
        except (TypeError, ValueError):
            return "--"
        if not np.isfinite(scaled_value):
            return "--"
        return f"{scaled_value:.3f}"

    @staticmethod
    def _normalize_values(values, count):
        normalized = ([] if values is None else list(values))[:count]
        normalized.extend([None] * (count - len(normalized)))
        return normalized

    def bpmvalue_dis(self):
        self.init_pv()
        for index in range(len(self.bpm_ids)):
            self.x_value_widgets[index].setText(self._format_bpm_value(self.pvlx_val[index]))
            self.y_value_widgets[index].setText(self._format_bpm_value(self.pvly_val[index]))

    def init_pv(self):
        try:
            self.pvlx_val = self._normalize_values(
                caget_many(self.bpm_x_pvs),
                len(self.bpm_x_pvs),
            )
            self.pvly_val = self._normalize_values(
                caget_many(self.bpm_y_pvs),
                len(self.bpm_y_pvs),
            )
        except Exception as exc:
            self.pvlx_val = [None] * len(self.bpm_x_pvs)
            self.pvly_val = [None] * len(self.bpm_y_pvs)
            self._set_connection_status("PV unavailable", "warning")
            self.statusBar().showMessage(f"PV connection unavailable: {exc}", 5000)
            return

        has_data = any(value is not None for value in self.pvlx_val + self.pvly_val)
        if has_data:
            self._set_connection_status("Live", "live")
            self.statusBar().clearMessage()
        else:
            self._set_connection_status("No data", "warning")
            self.statusBar().showMessage("BPM PVs returned no data.", 5000)

    def _set_connection_status(self, text, status):
        self.connection_label.setText(text)
        self.connection_label.setProperty("status", status)
        self._refresh_connection_style()

    def _refresh_connection_style(self):
        self.connection_label.style().unpolish(self.connection_label)
        self.connection_label.style().polish(self.connection_label)
        self.connection_label.update()

    def closeEvent(self, event):
        self.timer.stop()
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = myWindow()
    window.show()
    sys.exit(app.exec_())
