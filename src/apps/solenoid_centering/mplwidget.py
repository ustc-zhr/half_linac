from __future__ import annotations

import numpy as np

from PyQt5.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from matplotlib import cm
from matplotlib.backends.backend_qt5agg import FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure


class MplWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.summary_fig = Figure(figsize=(7, 5), tight_layout=True)
        self.score_axes = self.summary_fig.add_subplot(211)
        self.bpm_axes = self.summary_fig.add_subplot(212)
        self.summary_canvas = FigureCanvas(self.summary_fig)

        self.xy_fig = Figure(figsize=(7, 5), tight_layout=True)
        self.xy_axes = self.xy_fig.add_subplot(111)
        self.xy_canvas = FigureCanvas(self.xy_fig)

        tabs = QTabWidget(self)
        tabs.addTab(self._plot_page(self.summary_canvas), "Summary")
        tabs.addTab(self._plot_page(self.xy_canvas), "XY Trajectories")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(tabs)
        self.clear()

    def _plot_page(self, canvas):
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(NavigationToolbar(canvas, page))
        layout.addWidget(canvas)
        return page

    def clear(self):
        self.score_axes.clear()
        self.bpm_axes.clear()
        self.xy_axes.clear()
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
        self.summary_canvas.draw_idle()
        self.xy_canvas.draw_idle()

    def plot_result(self, result):
        self.clear()
        axis_scans = result.axis_scans
        if not axis_scans:
            return
        candidates = [candidate for scan in axis_scans for candidate in scan.candidates]

        for scan in axis_scans:
            x_values = [candidate.corrector_value for candidate in scan.candidates]
            scores = [candidate.score.score for candidate in scan.candidates]
            label = f"{scan.axis.upper()} round {scan.round_index + 1}"
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
        self.score_axes.legend(loc="best")
        self.bpm_axes.legend(loc="best")
        self.summary_canvas.draw_idle()
        self.xy_canvas.draw_idle()

    def _plot_xy_trajectories(self, candidates, best):
        if not candidates:
            return

        colors = self._trajectory_colors(len(candidates))
        show_all_labels = len(candidates) <= 12
        for index, candidate in enumerate(candidates):
            label = None
            if show_all_labels:
                label = self._candidate_label(candidate)
            self.xy_axes.plot(
                candidate.bpm_x_means,
                candidate.bpm_y_means,
                marker="o",
                linewidth=1.2,
                alpha=0.55,
                color=colors[index],
                label=label,
            )

        self.xy_axes.plot(
            best.bpm_x_means,
            best.bpm_y_means,
            marker="o",
            linewidth=2.6,
            color="black",
            label=f"Best {self._candidate_label(best)}",
        )
        self.xy_axes.scatter(
            [best.bpm_x_means[0]],
            [best.bpm_y_means[0]],
            marker="s",
            s=52,
            color="black",
            label="Best start",
        )
        self.xy_axes.scatter(
            [best.bpm_x_means[-1]],
            [best.bpm_y_means[-1]],
            marker="^",
            s=58,
            color="black",
            label="Best end",
        )
        self.xy_axes.set_title(f"BPM XY Trajectories ({len(candidates)} candidates)")
        self.xy_axes.legend(loc="best", fontsize="small")
        self.xy_axes.axis("equal")

    @staticmethod
    def _trajectory_colors(count):
        if count <= 1:
            return ["tab:blue"]
        color_map = cm.get_cmap("tab20" if count <= 20 else "viridis")
        return [color_map(value) for value in np.linspace(0.0, 1.0, count)]

    @staticmethod
    def _candidate_label(candidate):
        return (
            f"{candidate.axis.upper()} r{candidate.round_index + 1} "
            f"c={candidate.corrector_value:.4g}, score={candidate.score.score:.3g}"
        )
