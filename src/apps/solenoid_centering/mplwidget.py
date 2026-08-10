from __future__ import annotations

import numpy as np

from PyQt5.QtWidgets import QTabWidget, QVBoxLayout, QWidget

try:
    from matplotlib import colormaps
except ImportError:  # Matplotlib < 3.5
    colormaps = None
    from matplotlib import cm
from matplotlib.backends.backend_qt5agg import FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure

from half_linac.src.apps.solenoid_centering.gui.theme import LIGHT_THEME


class MplWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.palette = dict(LIGHT_THEME)
        self.summary_fig = Figure(
            figsize=(7, 5),
            tight_layout=True,
            facecolor=self.palette["plot_card_bg"],
        )
        self.score_axes = self.summary_fig.add_subplot(211)
        self.bpm_axes = self.summary_fig.add_subplot(212)
        self.summary_canvas = FigureCanvas(self.summary_fig)

        self.xy_fig = Figure(
            figsize=(7, 5),
            tight_layout=True,
            facecolor=self.palette["plot_card_bg"],
        )
        self.xy_axes = self.xy_fig.add_subplot(111)
        self.xy_canvas = FigureCanvas(self.xy_fig)

        self.all_fig = Figure(
            figsize=(7, 5),
            tight_layout=True,
            facecolor=self.palette["plot_card_bg"],
        )
        self.all_x_axes = self.all_fig.add_subplot(211)
        self.all_y_axes = self.all_fig.add_subplot(212)
        self.all_canvas = FigureCanvas(self.all_fig)
        self._live_candidates = []

        self.tabs = QTabWidget(self)
        self.summary_page = self._plot_page(self.summary_canvas)
        self.xy_page = self._plot_page(self.xy_canvas)
        self.all_page = self._plot_page(self.all_canvas)
        self.tabs.addTab(self.xy_page, "XY Paths")
        self.tabs.addTab(self.summary_page, "Best")
        self.tabs.addTab(self.all_page, "All Scans")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tabs)
        self.clear()

    def set_theme(self, palette: dict[str, str]) -> None:
        self.palette = dict(palette)
        for figure in (self.summary_fig, self.xy_fig, self.all_fig):
            figure.set_facecolor(self.palette["plot_card_bg"])
        for axes in (
            self.score_axes,
            self.bpm_axes,
            self.xy_axes,
            self.all_x_axes,
            self.all_y_axes,
        ):
            self._style_axes(axes)
        self.summary_canvas.draw_idle()
        self.xy_canvas.draw_idle()
        self.all_canvas.draw_idle()

    def _plot_page(self, canvas):
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(NavigationToolbar(canvas, page))
        layout.addWidget(canvas)
        return page

    def clear(self):
        self._live_candidates = []
        self.score_axes.clear()
        self.bpm_axes.clear()
        self.xy_axes.clear()
        self.all_x_axes.clear()
        self.all_y_axes.clear()
        for axes in (
            self.score_axes,
            self.bpm_axes,
            self.xy_axes,
            self.all_x_axes,
            self.all_y_axes,
        ):
            self._style_axes(axes)
        self.score_axes.set_title("Score vs Corrector")
        self.score_axes.set_xlabel("Corrector setpoint")
        self.score_axes.set_ylabel("Score")
        self.bpm_axes.set_title("Best Candidate BPM vs Solenoid")
        self.bpm_axes.set_xlabel("Solenoid setpoint")
        self.bpm_axes.set_ylabel("BPM position")
        self.xy_axes.set_title("BPM XY Trajectories")
        self.xy_axes.set_xlabel("BPM X")
        self.xy_axes.set_ylabel("BPM Y")
        self.xy_axes.grid(True, alpha=0.25)
        self._reset_all_scan_axes()
        self.tabs.setCurrentWidget(self.xy_page)
        self.summary_canvas.draw_idle()
        self.xy_canvas.draw_idle()
        self.all_canvas.draw_idle()

    def start_live(self):
        self._live_candidates = []
        self.xy_axes.clear()
        self.all_x_axes.clear()
        self.all_y_axes.clear()
        for axes in (self.xy_axes, self.all_x_axes, self.all_y_axes):
            self._style_axes(axes)
        self.xy_axes.set_title("BPM XY Trajectories (live)")
        self.xy_axes.set_xlabel("BPM X")
        self.xy_axes.set_ylabel("BPM Y")
        self.xy_axes.grid(True, alpha=0.25)
        self._reset_all_scan_axes()
        self.tabs.setCurrentWidget(self.xy_page)
        self.xy_canvas.draw_idle()
        self.all_canvas.draw_idle()

    def add_live_candidate(self, candidate):
        self._live_candidates.append(candidate)
        self.xy_axes.clear()
        self._style_axes(self.xy_axes)
        self.xy_axes.set_xlabel("BPM X")
        self.xy_axes.set_ylabel("BPM Y")
        self.xy_axes.grid(True, alpha=0.25)
        best = min(self._live_candidates, key=lambda item: item.score.score)
        self._plot_xy_trajectories(self._live_candidates, best, live=True)
        self._plot_all_scan_data(self._live_candidates, live=True)
        self.xy_canvas.draw_idle()
        self.all_canvas.draw_idle()

    def plot_result(self, result):
        self.clear()
        axis_scans = result.axis_scans
        if not axis_scans:
            return
        candidates = [candidate for scan in axis_scans for candidate in scan.candidates]

        for scan in axis_scans:
            x_values = [candidate.corrector_value for candidate in scan.candidates]
            scores = [candidate.score.score for candidate in scan.candidates]
            label = f"{scan.axis.upper()} iteration {scan.round_index + 1}"
            self.score_axes.plot(x_values, scores, marker="o", label=label)
            self.score_axes.axvline(scan.best.corrector_value, linestyle="--", alpha=0.35)

        best = min((scan.best for scan in axis_scans), key=lambda item: item.score.score)
        self.bpm_axes.plot(
            best.solenoid_values,
            best.bpm_x_means,
            marker="o",
            label="BPM X",
        )
        self.bpm_axes.plot(
            best.solenoid_values,
            best.bpm_y_means,
            marker="o",
            label="BPM Y",
        )
        self._plot_xy_trajectories(candidates, best)
        self._plot_all_scan_data(candidates)
        self.score_axes.legend(loc="best")
        self.bpm_axes.legend(loc="best")
        self.summary_canvas.draw_idle()
        self.xy_canvas.draw_idle()
        self.all_canvas.draw_idle()

    def _reset_all_scan_axes(self):
        self._style_axes(self.all_x_axes)
        self._style_axes(self.all_y_axes)
        self.all_x_axes.set_title("All BPM X vs Solenoid")
        self.all_x_axes.set_xlabel("Solenoid setpoint")
        self.all_x_axes.set_ylabel("BPM X")
        self.all_x_axes.grid(True, alpha=0.25)
        self.all_y_axes.set_title("All BPM Y vs Solenoid")
        self.all_y_axes.set_xlabel("Solenoid setpoint")
        self.all_y_axes.set_ylabel("BPM Y")
        self.all_y_axes.grid(True, alpha=0.25)

    def _plot_all_scan_data(self, candidates, *, live=False):
        self.all_x_axes.clear()
        self.all_y_axes.clear()
        self._reset_all_scan_axes()
        if not candidates:
            return

        colors = self._trajectory_colors(len(candidates))
        show_all_labels = len(candidates) <= 12
        best = min(candidates, key=lambda item: item.score.score)
        for index, candidate in enumerate(candidates):
            label = self._candidate_label(candidate) if show_all_labels else None
            line_width = 2.2 if candidate is best else 1.1
            alpha = 0.9 if candidate is best else 0.45
            color = self.palette["plot_best"] if candidate is best else colors[index]
            self.all_x_axes.plot(
                candidate.solenoid_values,
                candidate.bpm_x_means,
                marker="o",
                linewidth=line_width,
                alpha=alpha,
                color=color,
                label=label,
            )
            self.all_y_axes.plot(
                candidate.solenoid_values,
                candidate.bpm_y_means,
                marker="o",
                linewidth=line_width,
                alpha=alpha,
                color=color,
                label=label,
            )

        prefix = "All scan data live" if live else "All scan data"
        self.all_x_axes.set_title(f"{prefix}: BPM X ({len(candidates)} candidates)")
        self.all_y_axes.set_title(f"{prefix}: BPM Y ({len(candidates)} candidates)")
        if show_all_labels:
            self.all_x_axes.legend(loc="best", fontsize="small")
            self.all_y_axes.legend(loc="best", fontsize="small")

    def _plot_xy_trajectories(self, candidates, best, *, live=False):
        if not candidates:
            return

        colors = self._trajectory_colors(len(candidates))
        show_all_labels = len(candidates) <= 12
        for index, candidate in enumerate(candidates):
            label = None
            if show_all_labels:
                label = self._candidate_label(candidate)
            line_width = 1.8 if candidate is candidates[-1] else 1.2
            alpha = 0.85 if candidate is candidates[-1] else 0.45
            self.xy_axes.plot(
                candidate.bpm_x_means,
                candidate.bpm_y_means,
                marker="o",
                linewidth=line_width,
                alpha=alpha,
                color=colors[index],
                label=label,
            )

        self.xy_axes.plot(
            best.bpm_x_means,
            best.bpm_y_means,
            marker="o",
            linewidth=2.6,
            color=self.palette["plot_best"],
            label=(
                f"Best so far {self._candidate_label(best)}"
                if live
                else f"Best {self._candidate_label(best)}"
            ),
        )
        self.xy_axes.scatter(
            [best.bpm_x_means[0]],
            [best.bpm_y_means[0]],
            marker="s",
            s=52,
            color=self.palette["plot_best"],
            label="Best start",
        )
        self.xy_axes.scatter(
            [best.bpm_x_means[-1]],
            [best.bpm_y_means[-1]],
            marker="^",
            s=58,
            color=self.palette["plot_best"],
            label="Best end",
        )
        prefix = "BPM XY Trajectories live" if live else "BPM XY Trajectories"
        self.xy_axes.set_title(f"{prefix} ({len(candidates)} candidates)")
        self.xy_axes.legend(loc="best", fontsize="small")
        self.xy_axes.axis("equal")

    def _style_axes(self, axes):
        axes.set_facecolor(self.palette["plot_bg"])
        axes.tick_params(colors=self.palette["plot_text"])
        axes.xaxis.label.set_color(self.palette["plot_text"])
        axes.yaxis.label.set_color(self.palette["plot_text"])
        axes.title.set_color(self.palette["plot_text"])
        for spine in axes.spines.values():
            spine.set_color(self.palette["plot_spine"])

    @staticmethod
    def _trajectory_colors(count):
        if count <= 1:
            return ["tab:blue"]
        name = "tab20" if count <= 20 else "viridis"
        color_map = colormaps.get_cmap(name) if colormaps is not None else cm.get_cmap(name)
        return [color_map(value) for value in np.linspace(0.0, 1.0, count)]

    @staticmethod
    def _candidate_label(candidate):
        return (
            f"{candidate.axis.upper()} i{candidate.round_index + 1} "
            f"c={candidate.corrector_value:.4g}, score={candidate.score.score:.3g}"
        )
