from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
    QLabel, QProgressBar, QSplitter, QToolBar, QVBoxLayout, QWidget,
)

from .mplwidget import MplWidget


def scan_page_layout(page, title):
    root = QVBoxLayout(page)
    root.setContentsMargins(12, 10, 12, 12)
    root.setSpacing(8)
    card = QFrame(page)
    card.setObjectName("summaryPanel")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(12, 8, 12, 8)
    heading = QLabel(title, card)
    heading.setObjectName("summaryTitle")
    layout.addWidget(heading)
    summary = QLabel(card)
    summary.setProperty("role", "muted")
    summary.setWordWrap(True)
    layout.addWidget(summary)
    root.addWidget(card)
    return root, summary


def parameter_card(page, fields):
    card = QFrame(page)
    card.setObjectName("parameterCard")
    layout = QGridLayout(card)
    layout.setContentsMargins(12, 8, 12, 8)
    layout.setHorizontalSpacing(14)
    layout.setVerticalSpacing(4)
    for column, (label, widget) in enumerate(fields):
        caption = QLabel(label, card)
        caption.setBuddy(widget)
        layout.addWidget(caption, 0, column)
        layout.addWidget(widget, 1, column)
    layout.setColumnStretch(len(fields), 1)
    return card


def scan_workspace(page, table, table_title, column_widths, selection_buttons, plot_caption):
    splitter = QSplitter(Qt.Horizontal, page)
    splitter.setObjectName("scanSplitter")
    splitter.setChildrenCollapsible(False)
    splitter.setHandleWidth(6)

    table_panel = QFrame(splitter)
    table_panel.setObjectName("plotCard")
    table_panel.setMinimumWidth(730)
    layout = QVBoxLayout(table_panel)
    layout.setContentsMargins(10, 8, 10, 10)
    layout.setSpacing(6)
    heading = QHBoxLayout()
    title = QLabel(table_title, table_panel)
    title.setObjectName("cardTitle")
    heading.addWidget(title)
    heading.addStretch(1)
    for button in selection_buttons:
        button.setProperty("compact", True)
        heading.addWidget(button)
    layout.addLayout(heading)

    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    table.setMinimumWidth(710)
    table.verticalHeader().hide()
    table.verticalHeader().setDefaultSectionSize(28)
    header = table.horizontalHeader()
    header.setObjectName("scanTableHeader")
    for column, width in enumerate(column_widths):
        header.setSectionResizeMode(column, QHeaderView.Fixed)
        table.setColumnWidth(column, width)
    header.setStretchLastSection(True)
    layout.addWidget(table, 1)

    plot_panel = QFrame(splitter)
    plot_panel.setObjectName("plotCard")
    plot_panel.setMinimumWidth(400)
    layout = QVBoxLayout(plot_panel)
    layout.setContentsMargins(12, 10, 12, 10)
    plot_title = QLabel(plot_caption, plot_panel)
    plot_title.setProperty("role", "field")
    layout.addWidget(plot_title)
    plot = MplWidget(plot_panel)
    plot.findChild(QToolBar).hide()
    layout.addWidget(plot, 1)
    splitter.addWidget(table_panel)
    splitter.addWidget(plot_panel)
    splitter.setStretchFactor(0, 1)
    splitter.setStretchFactor(1, 1)
    splitter.setSizes([760, 700])
    return splitter, plot, plot_title


def station_selector(table, row, *, checked=False):
    cell = QWidget(table)
    layout = QHBoxLayout(cell)
    layout.setContentsMargins(0, 0, 0, 0)
    selector = QCheckBox(cell)
    selector.setProperty("stageSelector", True)
    selector.setChecked(checked)
    selector.setFocusPolicy(Qt.NoFocus)
    layout.addWidget(selector, 0, Qt.AlignCenter)
    table.setCellWidget(row, 0, cell)
    return selector


def scan_actions(page, buttons, total):
    actions = QHBoxLayout()
    actions.setSpacing(8)
    for button in buttons:
        actions.addWidget(button)
    actions.addSpacing(10)
    progress = QProgressBar(page)
    progress.setRange(0, max(1, total))
    progress.setValue(0)
    progress.setMinimumWidth(320)
    actions.addWidget(progress, 1)
    return actions, progress


def prepare_scan_axes(plot, palette, *, has_data):
    axes = plot.axes
    axes.clear()
    plot.fig.patch.set_facecolor(palette["plot"])
    axes.set_facecolor(palette["plot"])
    axes.grid(True, color=palette["grid"], alpha=0.6)
    axes.tick_params(colors=palette["muted"])
    for spine in axes.spines.values():
        spine.set_color(palette["border"])
    if has_data:
        axes.set_axis_on()
    else:
        axes.set_axis_off()
        axes.text(
            0.5, 0.5, "Waiting for scan data",
            ha="center", va="center", transform=axes.transAxes, color=palette["muted"],
        )
    return axes
