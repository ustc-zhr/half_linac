from __future__ import annotations

from ...analysis.jitter import transform_jitter_values
from .visibility import resolve_initial_visibility

try:
    from PyQt5 import QtCore, QtWidgets
except ImportError:  # pragma: no cover - optional runtime dependency
    QtCore = None
    QtWidgets = None

try:
    import pyqtgraph as pg
except ImportError:  # pragma: no cover - optional runtime dependency
    pg = None


if QtWidgets is not None:

    DEFAULT_VISIBLE_JITTER_SERIES = 3

    class JitterPlot(QtWidgets.QWidget):
        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            self._series_rows = []
            self._series_visibility = {}
            self._focused_pv_id = None
            self._mean_lines = []

            layout = QtWidgets.QVBoxLayout(self)

            controls = QtWidgets.QHBoxLayout()
            controls.addWidget(QtWidgets.QLabel("Show"))
            self.series_button = QtWidgets.QToolButton(self)
            self.series_button.setText("Visible Variables")
            self.series_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
            self.series_menu = QtWidgets.QMenu(self.series_button)
            self.series_button.setMenu(self.series_menu)
            self.series_button.setEnabled(False)
            controls.addWidget(self.series_button, 0)
            controls.addWidget(QtWidgets.QLabel("Display"))
            self.display_mode_combo = QtWidgets.QComboBox(self)
            self.display_mode_combo.addItem("Raw", "raw")
            self.display_mode_combo.addItem("Mean-centered", "mean_centered")
            self.display_mode_combo.addItem("Z-score", "z_score")
            self.display_mode_combo.setToolTip(
                "Choose raw values, mean-centered values, or z-score normalization for comparison."
            )
            controls.addWidget(self.display_mode_combo, 0)
            controls.addStretch(1)
            layout.addLayout(controls)

            self.info_label = QtWidgets.QLabel("Select one or more read PVs to inspect jitter points.")
            self.info_label.setWordWrap(True)
            layout.addWidget(self.info_label)

            if pg is not None:
                self.plot_widget = pg.PlotWidget(title="Jitter Points")
                self.plot_widget.addLegend()
                self.plot_widget.setLabel("bottom", "Sample Index")
                self.plot_widget.setLabel("left", "Value")
                self.plot_widget.showGrid(x=True, y=True, alpha=0.2)
                layout.addWidget(self.plot_widget, 1)
            else:
                self.plot_widget = None
                layout.addWidget(QtWidgets.QLabel("pyqtgraph is not installed"))

            self.display_mode_combo.currentIndexChanged.connect(self._render_series)

        def clear_data(self, message: str) -> None:
            self._series_rows = []
            self._series_visibility = {}
            self._focused_pv_id = None
            self._mean_lines = []
            self.info_label.setText(message)
            self._rebuild_series_menu()
            if self.plot_widget is not None:
                self.plot_widget.clear()
                self.plot_widget.addLegend()
                self.plot_widget.setTitle("Jitter Points")
                self.plot_widget.setLabel("bottom", "Sample Index")
                self.plot_widget.setLabel("left", "Value")

        def current_pv_ids(self) -> list[str]:
            return [
                str(row["pv_id"])
                for row in self._series_rows
                if self._series_visibility.get(str(row["pv_id"]), True)
            ]

        def focused_pv_id(self) -> str | None:
            return str(self._focused_pv_id) if self._focused_pv_id else None

        def set_series_rows(
            self,
            rows,
            current_pv_ids: list[str] | None = None,
            focused_pv_id: str | None = None,
        ) -> None:
            previous_visibility = dict(self._series_visibility)
            self._series_rows = list(rows)
            self._series_visibility = resolve_initial_visibility(
                [str(row["pv_id"]) for row in self._series_rows],
                previous_visibility=previous_visibility,
                explicit_visible_keys=current_pv_ids,
                default_visible_count=DEFAULT_VISIBLE_JITTER_SERIES,
            )

            if not self._series_rows:
                self.clear_data("No valid jitter samples are available.")
                return

            focus_candidate = str(focused_pv_id).strip() if focused_pv_id else ""
            available_ids = {str(row["pv_id"]) for row in self._series_rows}
            if focus_candidate in available_ids:
                self._focused_pv_id = focus_candidate
            elif self._focused_pv_id not in available_ids:
                visible_ids = self.current_pv_ids()
                self._focused_pv_id = visible_ids[0] if visible_ids else str(self._series_rows[0]["pv_id"])

            self._rebuild_series_menu()
            self._render_series()

        def select_pv_id(self, pv_id: str | None) -> None:
            if not pv_id:
                return
            token = str(pv_id)
            available_ids = {str(row["pv_id"]) for row in self._series_rows}
            if token not in available_ids:
                return
            self._focused_pv_id = token
            self._series_visibility[token] = True
            self._rebuild_series_menu()
            self._render_series()

        def _rebuild_series_menu(self) -> None:
            self.series_menu.clear()
            self.series_button.setEnabled(bool(self._series_rows))
            if not self._series_rows:
                self.series_button.setText("Visible Variables")
                return

            show_all_action = self.series_menu.addAction("Show All")
            show_all_action.triggered.connect(lambda: self._set_all_series_visibility(True))
            hide_all_action = self.series_menu.addAction("Hide All")
            hide_all_action.triggered.connect(lambda: self._set_all_series_visibility(False))
            self.series_menu.addSeparator()

            visible_count = 0
            total_count = len(self._series_rows)
            for row in self._series_rows:
                pv_id = str(row["pv_id"])
                label = str(row["label"])
                visible = bool(self._series_visibility.get(pv_id, True))
                if visible:
                    visible_count += 1
                action = self.series_menu.addAction(label)
                action.setCheckable(True)
                action.setChecked(visible)
                action.toggled.connect(lambda checked, target_pv_id=pv_id: self._set_series_visibility(target_pv_id, checked))

            self.series_button.setText(f"Visible Variables ({visible_count}/{total_count})")

        def _set_series_visibility(self, pv_id: str, visible: bool) -> None:
            if pv_id not in self._series_visibility:
                return
            self._series_visibility[pv_id] = bool(visible)
            if not self._series_visibility[pv_id] and self._focused_pv_id == pv_id:
                visible_ids = self.current_pv_ids()
                self._focused_pv_id = visible_ids[0] if visible_ids else None
            elif self._focused_pv_id is None and visible:
                self._focused_pv_id = pv_id
            self._rebuild_series_menu()
            self._render_series()

        def _set_all_series_visibility(self, visible: bool) -> None:
            for pv_id in list(self._series_visibility.keys()):
                self._series_visibility[pv_id] = bool(visible)
            if visible and self._focused_pv_id is None and self._series_rows:
                self._focused_pv_id = str(self._series_rows[0]["pv_id"])
            elif not visible:
                self._focused_pv_id = None
            self._rebuild_series_menu()
            self._render_series()

        @staticmethod
        def _mode_title(mode: str) -> str:
            titles = {
                "raw": "Raw",
                "mean_centered": "Mean-Centered",
                "z_score": "Z-Score",
            }
            return titles.get(mode, mode)

        def _display_mode(self) -> str:
            return str(self.display_mode_combo.currentData() or "raw")

        def _render_series(self) -> None:
            visible_rows = [
                row for row in self._series_rows if self._series_visibility.get(str(row["pv_id"]), True)
            ]
            if not visible_rows:
                self.info_label.setText("No variable is selected for jitter plotting.")
                if self.plot_widget is not None:
                    self.plot_widget.clear()
                    self.plot_widget.addLegend()
                    self.plot_widget.setTitle("Jitter Points")
                    self.plot_widget.setLabel("bottom", "Sample Index")
                    self.plot_widget.setLabel("left", "Value")
                return

            if self._focused_pv_id not in {str(row["pv_id"]) for row in visible_rows}:
                self._focused_pv_id = str(visible_rows[0]["pv_id"])

            focused_row = next(
                (row for row in visible_rows if str(row["pv_id"]) == str(self._focused_pv_id)),
                visible_rows[0],
            )
            focused_label = str(focused_row["label"])
            requested_mode = self._display_mode()
            value_phrase = self._mode_title(requested_mode)
            fallback_note = ""
            if requested_mode == "z_score":
                fallback_count = sum(
                    1
                    for row in visible_rows
                    if transform_jitter_values(
                        row["values"],
                        requested_mode,
                        mean=float(row["mean"]),
                        std=float(row["std"]),
                    ).applied_mode != requested_mode
                )
                if fallback_count:
                    fallback_note = f" | {fallback_count} constant variable(s) shown as mean-centered."
            self.info_label.setText(
                f"Showing {len(visible_rows)}/{len(self._series_rows)} variables. "
                f"Focus: {focused_label}, count={len(focused_row['values'])}, mean={float(focused_row['mean']):.6g}, "
                f"std={float(focused_row['std']):.6g}, rms={float(focused_row['rms']):.6g}, "
                f"p2p={float(focused_row['p2p']):.6g}, plot={value_phrase}.{fallback_note}"
            )

            if self.plot_widget is None:
                return

            self.plot_widget.clear()
            self.plot_widget.addLegend()
            if requested_mode == "mean_centered":
                self.plot_widget.setTitle("Jitter Points (Mean-Centered)")
            elif requested_mode == "z_score":
                self.plot_widget.setTitle("Jitter Points (Z-Score Normalized)")
            else:
                self.plot_widget.setTitle("Jitter Points")
            self.plot_widget.setLabel("bottom", "Sample Index")
            units = {str(row.get("unit", "")).strip() for row in visible_rows if str(row.get("unit", "")).strip()}
            if requested_mode == "z_score":
                self.plot_widget.setLabel("left", "Z-score")
            elif len(units) == 1:
                unit = next(iter(units))
                if requested_mode == "mean_centered":
                    self.plot_widget.setLabel("left", f"Value - Mean [{unit}]")
                else:
                    self.plot_widget.setLabel("left", f"Value [{unit}]")
            else:
                self.plot_widget.setLabel("left", "Value - Mean" if requested_mode == "mean_centered" else "Value")

            self._mean_lines = []
            for index, row in enumerate(visible_rows):
                pv_id = str(row["pv_id"])
                sample_indices = list(row.get("sample_indices", range(len(row["values"]))))
                raw_values = list(row["values"])
                transform = transform_jitter_values(
                    raw_values,
                    requested_mode,
                    mean=float(row["mean"]),
                    std=float(row["std"]),
                )
                values = transform.values
                color = pg.intColor(index, hues=max(len(visible_rows), 1))
                focused = pv_id == str(self._focused_pv_id)
                pen_width = 2.5 if focused else 1.5
                symbol_size = 7 if focused else 5
                self.plot_widget.plot(
                    sample_indices,
                    values,
                    pen=pg.mkPen(color=color, width=pen_width),
                    symbol="o",
                    symbolSize=symbol_size,
                    symbolBrush=pg.mkBrush(color),
                    symbolPen=pg.mkPen(color, width=1),
                    name=str(row["label"]),
                )
                if focused:
                    mean_line = pg.InfiniteLine(
                        pos=0.0 if transform.applied_mode != "raw" else float(row["mean"]),
                        angle=0,
                        movable=False,
                        pen=pg.mkPen(color=color, width=1.5, style=QtCore.Qt.DashLine),
                    )
                    self.plot_widget.addItem(mean_line)
                    self._mean_lines.append(mean_line)

else:

    class JitterPlot:  # pragma: no cover - fallback for non-GUI tests
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("PyQt5 is required to create JitterPlot")
