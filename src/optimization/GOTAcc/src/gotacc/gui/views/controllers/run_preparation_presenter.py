from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from ..main_window import MainWindow


class RunPreparationPresenter:
    def __init__(self, window: "MainWindow") -> None:
        self.window = window
        self.view = window.view_adapter

    def prepare_for_start(self, task: dict[str, Any]) -> None:
        self.view.reset_plot_data()
        self.view.redraw_plots()
        self.view.clear_recent_evaluations()
        self.view.clear_run_events()
        self.view.update_results_after_start(task)
