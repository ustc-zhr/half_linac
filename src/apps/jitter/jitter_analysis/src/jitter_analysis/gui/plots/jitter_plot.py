from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
import math

from ...analysis.jitter import transform_jitter_values
from .visibility import downsample_series_min_max, padded_finite_range, resolve_initial_visibility
from .trend_plot import TrendAxisItem
from .theme import style_plot_widget

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

    DEFAULT_VISIBLE_JITTER_SERIES = 1
    MAX_SYMBOL_POINTS_PER_SERIES = 5000
    DEFAULT_JITTER_DISPLAY_POINTS = 6000


    class SeriesVisibilityDialog(QtWidgets.QDialog):
        def __init__(self, rows, visible_pv_ids, parent=None) -> None:
            super().__init__(parent)
            self.setWindowTitle("Choose Visible Variables")
            self.resize(760, 620)
            self._rows = list(rows)
            self._items_by_pv_id = {}

            layout = QtWidgets.QVBoxLayout(self)
            filters = QtWidgets.QHBoxLayout()
            self.search_edit = QtWidgets.QLineEdit(self)
            self.search_edit.setPlaceholderText("Search name, PV, group, or tags")
            self.search_edit.setClearButtonEnabled(True)
            self.group_combo = QtWidgets.QComboBox(self)
            self.group_combo.addItem("All Groups", "")
            filters.addWidget(self.search_edit, 1)
            filters.addWidget(self.group_combo)
            layout.addLayout(filters)

            self.tree = QtWidgets.QTreeWidget(self)
            self.tree.setHeaderLabels(["Variable", "Read PV"])
            self.tree.setAlternatingRowColors(True)
            self.tree.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
            self.tree.header().setStretchLastSection(False)
            self.tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
            self.tree.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
            layout.addWidget(self.tree, 1)

            actions = QtWidgets.QHBoxLayout()
            self.select_filtered_button = QtWidgets.QPushButton("Select Results", self)
            self.clear_button = QtWidgets.QPushButton("Clear", self)
            self.status_label = QtWidgets.QLabel(self)
            actions.addWidget(self.select_filtered_button)
            actions.addWidget(self.clear_button)
            actions.addStretch(1)
            actions.addWidget(self.status_label)
            layout.addLayout(actions)

            button_box = QtWidgets.QDialogButtonBox(self)
            self.apply_button = button_box.addButton("Apply", QtWidgets.QDialogButtonBox.AcceptRole)
            self.cancel_button = button_box.addButton(QtWidgets.QDialogButtonBox.Cancel)
            button_box.accepted.connect(self.accept)
            button_box.rejected.connect(self.reject)
            layout.addWidget(button_box)

            self._populate_tree(set(visible_pv_ids or []))
            self.search_edit.textChanged.connect(self._apply_filters)
            self.group_combo.currentIndexChanged.connect(self._apply_filters)
            self.tree.itemChanged.connect(self._update_status)
            self.select_filtered_button.clicked.connect(self._select_filtered)
            self.clear_button.clicked.connect(self._clear_selection)
            self._apply_filters()

        def _populate_tree(self, visible_pv_ids: set[str]) -> None:
            groups = {}
            group_order = []
            for row in self._rows:
                group_id = str(row.get("group_id", "") or "ungrouped")
                group_label = str(row.get("group_label", "") or group_id)
                if group_id not in groups:
                    parent_item = QtWidgets.QTreeWidgetItem([group_label, ""])
                    parent_item.setData(0, QtCore.Qt.UserRole, group_id)
                    parent_item.setFlags(
                        parent_item.flags()
                        | QtCore.Qt.ItemIsUserCheckable
                        | QtCore.Qt.ItemIsAutoTristate
                    )
                    groups[group_id] = parent_item
                    group_order.append(group_id)
                    self.group_combo.addItem(group_label, group_id)

                pv_id = str(row["pv_id"])
                label = str(row.get("label", pv_id))
                read_pv = str(row.get("read_pv", ""))
                tags = " ".join(str(tag) for tag in row.get("tags", []))
                search_text = " ".join(
                    part.casefold()
                    for part in (label, pv_id, read_pv, group_id, group_label, tags)
                    if part
                )
                child = QtWidgets.QTreeWidgetItem([label, read_pv])
                child.setFlags(child.flags() | QtCore.Qt.ItemIsUserCheckable)
                child.setCheckState(
                    0,
                    QtCore.Qt.Checked if pv_id in visible_pv_ids else QtCore.Qt.Unchecked,
                )
                child.setData(0, QtCore.Qt.UserRole, pv_id)
                child.setData(0, QtCore.Qt.UserRole + 1, search_text)
                groups[group_id].addChild(child)
                self._items_by_pv_id[pv_id] = child

            for group_id in group_order:
                self.tree.addTopLevelItem(groups[group_id])
            self.tree.expandAll()

        def _apply_filters(self, *_args) -> None:
            query_tokens = [token for token in self.search_edit.text().casefold().split() if token]
            selected_group = str(self.group_combo.currentData() or "")
            for parent_index in range(self.tree.topLevelItemCount()):
                parent = self.tree.topLevelItem(parent_index)
                group_id = str(parent.data(0, QtCore.Qt.UserRole) or "")
                visible_children = 0
                for child_index in range(parent.childCount()):
                    child = parent.child(child_index)
                    search_text = str(child.data(0, QtCore.Qt.UserRole + 1) or "")
                    matches_search = all(token in search_text for token in query_tokens)
                    matches_group = not selected_group or group_id == selected_group
                    child.setHidden(not (matches_search and matches_group))
                    if not child.isHidden():
                        visible_children += 1
                parent.setHidden(visible_children == 0)
                parent.setExpanded(visible_children > 0)
            self._update_status()

        def _visible_child_items(self):
            for parent_index in range(self.tree.topLevelItemCount()):
                parent = self.tree.topLevelItem(parent_index)
                if parent.isHidden():
                    continue
                for child_index in range(parent.childCount()):
                    child = parent.child(child_index)
                    if not child.isHidden():
                        yield child

        def _select_filtered(self, *_args) -> None:
            blocker = QtCore.QSignalBlocker(self.tree)
            try:
                for child in self._visible_child_items():
                    child.setCheckState(0, QtCore.Qt.Checked)
            finally:
                del blocker
            self._update_status()

        def _clear_selection(self, *_args) -> None:
            blocker = QtCore.QSignalBlocker(self.tree)
            try:
                for child in self._items_by_pv_id.values():
                    child.setCheckState(0, QtCore.Qt.Unchecked)
            finally:
                del blocker
            self._update_status()

        def _update_status(self, *_args) -> None:
            visible_count = sum(1 for _item in self._visible_child_items())
            selected_count = len(self.selected_pv_ids())
            self.status_label.setText(
                f"Results: {visible_count}/{len(self._rows)}    Selected: {selected_count}"
            )

        def selected_pv_ids(self) -> list[str]:
            return [
                str(row["pv_id"])
                for row in self._rows
                if self._items_by_pv_id[str(row["pv_id"])].checkState(0) == QtCore.Qt.Checked
            ]

    class JitterPlot(QtWidgets.QWidget):
        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            self._series_rows = []
            self._series_visibility = {}
            self._focused_pv_id = None
            self._mean_lines = []
            self._sample_elapsed_seconds = []
            self._time_origin = None
            self._time_origin_epoch_seconds = None

            layout = QtWidgets.QVBoxLayout(self)

            controls = QtWidgets.QHBoxLayout()
            controls.addWidget(QtWidgets.QLabel("Show"))
            self.series_button = QtWidgets.QToolButton(self)
            self.series_button.setText("Visible Variables")
            self.series_button.setProperty("plotToolbarControl", "true")
            self.series_button.setFixedHeight(28)
            self.series_button.setEnabled(False)
            controls.addWidget(self.series_button, 0)
            controls.addWidget(QtWidgets.QLabel("Display"))
            self.display_mode_combo = QtWidgets.QComboBox(self)
            self.display_mode_combo.addItem("Raw", "raw")
            self.display_mode_combo.addItem("Mean-centered", "mean_centered")
            self.display_mode_combo.addItem("Z-score", "z_score")
            self.display_mode_combo.setFixedHeight(28)
            self.display_mode_combo.setToolTip(
                "Choose raw values, mean-centered values, or z-score normalization for comparison."
            )
            controls.addWidget(self.display_mode_combo, 0)
            controls.addWidget(QtWidgets.QLabel("X Axis"))
            self.axis_combo = QtWidgets.QComboBox(self)
            self.axis_combo.addItem("Sample Index", "sample_index")
            self.axis_combo.addItem("Elapsed Time", "elapsed_time")
            self.axis_combo.addItem("Clock Time", "clock_time")
            self.axis_combo.setFixedHeight(28)
            controls.addWidget(self.axis_combo, 0)
            self.downsample_check = QtWidgets.QCheckBox("Downsample")
            self.downsample_check.setChecked(True)
            self.downsample_check.setFixedHeight(28)
            self.downsample_check.setToolTip("Reduce rendered jitter points with min/max sampling while preserving extrema.")
            controls.addWidget(self.downsample_check, 0)
            self.display_points_spin = QtWidgets.QSpinBox(self)
            self.display_points_spin.setRange(500, 1_000_000)
            self.display_points_spin.setSingleStep(500)
            self.display_points_spin.setValue(DEFAULT_JITTER_DISPLAY_POINTS)
            self.display_points_spin.setSuffix(" pts")
            self.display_points_spin.setToolTip("Maximum rendered points per jitter series when downsampling is enabled.")
            self.display_points_spin.setFixedHeight(28)
            controls.addWidget(self.display_points_spin, 0)
            controls.addStretch(1)
            self.controls_layout = controls
            layout.addLayout(controls)

            self.info_label = QtWidgets.QLabel("Select one or more read PVs to inspect jitter points.")
            self.info_label.setWordWrap(True)
            layout.addWidget(self.info_label)

            if pg is not None:
                self._bottom_axis = TrendAxisItem(orientation="bottom") if TrendAxisItem is not None else None
                if self._bottom_axis is not None:
                    self.plot_widget = pg.PlotWidget(title="Jitter Points", axisItems={"bottom": self._bottom_axis})
                else:
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
            self.axis_combo.currentIndexChanged.connect(self._render_series)
            self.downsample_check.toggled.connect(self._handle_downsample_changed)
            self.display_points_spin.valueChanged.connect(self._render_series)
            self.series_button.clicked.connect(self._open_series_selector)
            self._update_downsample_control_state()

        def add_trailing_control_widget(self, widget) -> None:
            self.controls_layout.addWidget(widget, 0)

        def clear_data(self, message: str) -> None:
            self._series_rows = []
            self._series_visibility = {}
            self._focused_pv_id = None
            self._mean_lines = []
            self._sample_elapsed_seconds = []
            self._time_origin = None
            self._time_origin_epoch_seconds = None
            self.info_label.setText(message)
            self._rebuild_series_menu()
            if self.plot_widget is not None:
                self.plot_widget.clear()
                self.plot_widget.addLegend()
                style_plot_widget(self.plot_widget)
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
            sample_timestamps: Sequence[datetime | None] | None = None,
        ) -> None:
            previous_visibility = dict(self._series_visibility)
            self._series_rows = list(rows)
            self._set_sample_timestamps(sample_timestamps)
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
            self.series_button.setEnabled(bool(self._series_rows))
            if not self._series_rows:
                self.series_button.setText("Visible Variables")
                return
            self.series_button.setText(
                f"Visible Variables ({len(self.current_pv_ids())}/{len(self._series_rows)})"
            )

        def _open_series_selector(self) -> None:
            if not self._series_rows:
                return
            dialog = SeriesVisibilityDialog(
                self._series_rows,
                self.current_pv_ids(),
                parent=self,
            )
            if dialog.exec_() == QtWidgets.QDialog.Accepted:
                self.set_visible_pv_ids(dialog.selected_pv_ids())

        def set_visible_pv_ids(
            self,
            pv_ids,
            *,
            focused_pv_id: str | None = None,
        ) -> None:
            visible_ids = {str(pv_id) for pv_id in pv_ids}
            available_ids = {str(row["pv_id"]) for row in self._series_rows}
            visible_ids &= available_ids
            for pv_id in self._series_visibility:
                self._series_visibility[pv_id] = pv_id in visible_ids

            focus_candidate = str(focused_pv_id or "")
            if focus_candidate in visible_ids:
                self._focused_pv_id = focus_candidate
            elif self._focused_pv_id not in visible_ids:
                self._focused_pv_id = next(
                    (
                        str(row["pv_id"])
                        for row in self._series_rows
                        if str(row["pv_id"]) in visible_ids
                    ),
                    None,
                )
            self._rebuild_series_menu()
            self._render_series()

        def _set_series_visibility(self, pv_id: str, visible: bool) -> None:
            if pv_id not in self._series_visibility:
                return
            visible_ids = set(self.current_pv_ids())
            if visible:
                visible_ids.add(pv_id)
            else:
                visible_ids.discard(pv_id)
            self.set_visible_pv_ids(visible_ids, focused_pv_id=pv_id if visible else None)

        def _set_all_series_visibility(self, visible: bool) -> None:
            self.set_visible_pv_ids(
                [row["pv_id"] for row in self._series_rows] if visible else []
            )

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

        def _axis_mode(self) -> str:
            return str(self.axis_combo.currentData() or "sample_index")

        def _downsample_enabled(self) -> bool:
            return bool(self.downsample_check.isChecked())

        def _handle_downsample_changed(self, *_args) -> None:
            self._update_downsample_control_state()
            self._render_series()

        def _update_downsample_control_state(self) -> None:
            self.display_points_spin.setEnabled(self._downsample_enabled())

        def _display_series_data(self, sample_indices, values) -> tuple[list[float], list[float], bool]:
            length = min(len(sample_indices), len(values))
            plot_sample_indices = list(sample_indices[:length])
            plot_values = list(values[:length])
            downsampled = False
            if self._downsample_enabled():
                plot_sample_indices, plot_values, downsampled = downsample_series_min_max(
                    plot_sample_indices,
                    plot_values,
                    max_points=int(self.display_points_spin.value()),
                )
            return self._x_values_for_sample_indices(plot_sample_indices), plot_values, downsampled

        def _set_sample_timestamps(self, sample_timestamps: Sequence[datetime | None] | None) -> None:
            self._sample_elapsed_seconds = []
            self._time_origin = None
            self._time_origin_epoch_seconds = None
            if sample_timestamps is None:
                return
            for sample_index, timestamp in enumerate(sample_timestamps):
                self._record_sample_time(sample_index, timestamp)

        def _record_sample_time(self, sample_index: int, timestamp: datetime | None) -> None:
            if timestamp is None or sample_index < 0:
                return
            if self._time_origin is None:
                self._time_origin = timestamp
                self._time_origin_epoch_seconds = float(timestamp.timestamp())
            elapsed_seconds = (timestamp - self._time_origin).total_seconds()
            if sample_index >= len(self._sample_elapsed_seconds):
                self._sample_elapsed_seconds.extend([None] * (sample_index + 1 - len(self._sample_elapsed_seconds)))
            if self._sample_elapsed_seconds[sample_index] is None:
                self._sample_elapsed_seconds[sample_index] = float(elapsed_seconds)

        def _axis_value_for_index(self, sample_index: int, axis_mode: str | None = None) -> float:
            mode = str(axis_mode or self._axis_mode())
            if mode == "sample_index":
                return float(sample_index)
            if 0 <= sample_index < len(self._sample_elapsed_seconds):
                elapsed_seconds = self._sample_elapsed_seconds[sample_index]
                if elapsed_seconds is not None:
                    if mode == "clock_time" and self._time_origin_epoch_seconds is not None:
                        return float(self._time_origin_epoch_seconds + elapsed_seconds)
                    return float(elapsed_seconds)
            return float(sample_index)

        def _x_values_for_sample_indices(self, sample_indices) -> list[float]:
            axis_mode = self._axis_mode()
            if self._bottom_axis is not None:
                self._bottom_axis.set_axis_mode(axis_mode)
            if axis_mode == "elapsed_time":
                if self.plot_widget is not None:
                    self.plot_widget.setLabel("bottom", "Elapsed Time [s]")
            elif axis_mode == "clock_time":
                if self.plot_widget is not None:
                    self.plot_widget.setLabel("bottom", "Clock Time")
            else:
                if self.plot_widget is not None:
                    self.plot_widget.setLabel("bottom", "Sample Index")
            return [self._axis_value_for_index(int(item), axis_mode=axis_mode) for item in sample_indices]

        def _render_series(self) -> None:
            visible_rows = [
                row for row in self._series_rows if self._series_visibility.get(str(row["pv_id"]), True)
            ]
            if not visible_rows:
                self.info_label.setText("No variable is selected for jitter plotting.")
                if self.plot_widget is not None:
                    self.plot_widget.clear()
                    self.plot_widget.addLegend()
                    style_plot_widget(self.plot_widget)
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
            kept_count = len(focused_row["values"])
            raw_count = int(focused_row.get("raw_count", kept_count))
            removed_count = int(focused_row.get("removed_count", max(raw_count - kept_count, 0)))
            if raw_count > kept_count or removed_count > 0:
                count_phrase = f"count={kept_count}/{raw_count} kept"
                filter_note = f", filtered={removed_count}"
            else:
                count_phrase = f"count={kept_count}"
                filter_note = ""
            downsample_enabled = self._downsample_enabled()
            downsample_limit = int(self.display_points_spin.value())
            symbol_disabled_count = sum(
                1
                for row in visible_rows
                if (
                    min(len(row.get("values", [])), downsample_limit)
                    if downsample_enabled
                    else len(row.get("values", []))
                )
                > MAX_SYMBOL_POINTS_PER_SERIES
            )
            symbol_note = (
                f" Symbols disabled for {symbol_disabled_count} large series."
                if symbol_disabled_count
                else ""
            )
            downsample_candidate_count = (
                sum(1 for row in visible_rows if len(row.get("values", [])) > downsample_limit)
                if downsample_enabled
                else 0
            )
            if downsample_enabled and downsample_candidate_count:
                downsample_note = (
                    f" Min/max downsampling enabled: {downsample_candidate_count} large series "
                    f"limited to {downsample_limit} rendered points."
                )
            elif downsample_enabled:
                downsample_note = f" Min/max downsampling enabled: limit={downsample_limit} rendered points."
            else:
                downsample_note = " Downsampling disabled."
            relative_jitter_percent = float(focused_row.get("relative_jitter_percent", math.nan))
            relative_jitter_phrase = (
                f"{relative_jitter_percent:.6g}%"
                if math.isfinite(relative_jitter_percent)
                else "N/A"
            )
            self.info_label.setText(
                f"Showing {len(visible_rows)}/{len(self._series_rows)} variables. "
                f"Focus: {focused_label}, {count_phrase}{filter_note}, mean={float(focused_row['mean']):.6g}, "
                f"std={float(focused_row['std']):.6g}, jitter={relative_jitter_phrase}, "
                f"p2p={float(focused_row['p2p']):.6g}, plot={value_phrase}.{fallback_note}"
                f"{downsample_note}{symbol_note}"
            )

            if self.plot_widget is None:
                return

            self.plot_widget.clear()
            self.plot_widget.addLegend()
            style_plot_widget(self.plot_widget)
            if requested_mode == "mean_centered":
                self.plot_widget.setTitle("Jitter Points (Mean-Centered)")
            elif requested_mode == "z_score":
                self.plot_widget.setTitle("Jitter Points (Z-Score Normalized)")
            else:
                self.plot_widget.setTitle("Jitter Points")
            self._x_values_for_sample_indices([0])
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
            visible_y_values = []
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
                visible_y_values.extend(values)
                plot_x_values, plot_y_values, _ = self._display_series_data(sample_indices, values)
                color = pg.intColor(index, hues=max(len(visible_rows), 1))
                focused = pv_id == str(self._focused_pv_id)
                pen_width = 2.5 if focused else 1.5
                use_symbols = len(plot_y_values) <= MAX_SYMBOL_POINTS_PER_SERIES
                plot_kwargs = {
                    "pen": pg.mkPen(color=color, width=pen_width),
                    "name": str(row["label"]),
                }
                if use_symbols:
                    plot_kwargs.update(
                        {
                            "symbol": "o",
                            "symbolSize": 7 if focused else 5,
                            "symbolBrush": pg.mkBrush(color),
                            "symbolPen": pg.mkPen(color, width=1),
                        }
                    )
                self.plot_widget.plot(
                    plot_x_values,
                    plot_y_values,
                    **plot_kwargs,
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

            style_plot_widget(self.plot_widget)
            y_range = padded_finite_range(visible_y_values)
            if y_range is not None:
                self.plot_widget.setYRange(y_range[0], y_range[1], padding=0.0)

else:

    class JitterPlot:  # pragma: no cover - fallback for non-GUI tests
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("PyQt5 is required to create JitterPlot")
