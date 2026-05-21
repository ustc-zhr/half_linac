from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from ..main_window import MainWindow


class RunCompletionPresenter:
    def __init__(self, window: "MainWindow") -> None:
        self.window = window
        self.view = window.view_adapter

    def apply_finished_payload(self, payload: dict[str, Any]) -> None:
        run_phase = self.window.run_session_presenter.apply_finished_payload(payload)
        self.view.log_event(f"Run finished with state={run_phase}.")
        if payload.get("history_path"):
            self.view.log_event(f"History saved to: {payload['history_path']}")
        if payload.get("plot_path"):
            self.view.log_event(f"Plot saved to: {payload['plot_path']}")
        self.view.update_results_after_finish(payload)
        self.view.redraw_plots()
        try:
            saved_images = self.window.results_controller.save_result_images()
        except Exception as exc:
            self.view.log_warning(f"GUI result image export failed: {exc}")
        else:
            if saved_images:
                self.view.log_event(f"GUI result images saved: {len(saved_images)} file(s).")
        if run_phase == "Finished":
            self.view.go_to_page(self.window.PAGE_RESULTS)
