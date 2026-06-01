from __future__ import annotations

try:
    from PyQt5 import QtCore, QtWidgets
except ImportError:  # pragma: no cover - optional runtime dependency
    QtCore = None
    QtWidgets = None


if QtWidgets is not None:

    class RandomKnobConfigDialog(QtWidgets.QDialog):
        def __init__(
            self,
            *,
            knobs,
            group_labels: dict[str, str] | None = None,
            current_state: dict[str, dict[str, object]] | None = None,
            epics_client=None,
            parent=None,
        ) -> None:
            super().__init__(parent)
            self.setWindowTitle("Configure Ranges")
            self.resize(980, 620)

            self._knobs = list(knobs)
            self._group_labels = dict(group_labels or {})
            self._current_state = dict(current_state or {})
            self._epics_client = epics_client
            self._knob_row_ids = [knob.id for knob in self._knobs]
            self._knob_specs_by_id = {knob.id: knob for knob in self._knobs}

            layout = QtWidgets.QVBoxLayout(self)
            intro = QtWidgets.QLabel(
                "Choose which control PVs participate in random sampling, then set Low/High for each enabled row."
            )
            intro.setWordWrap(True)
            layout.addWidget(intro)

            helper_row = QtWidgets.QHBoxLayout()
            self.fetch_current_button = QtWidgets.QPushButton("Fetch Current Values")
            self.use_limits_button = QtWidgets.QPushButton("Use Limits For Enabled")
            self.apply_step_hint_button = QtWidgets.QPushButton("Apply +/- Step Hint x")
            self.step_hint_factor_spin = QtWidgets.QDoubleSpinBox()
            self.step_hint_factor_spin.setRange(0.0, 1.0e6)
            self.step_hint_factor_spin.setDecimals(3)
            self.step_hint_factor_spin.setValue(3.0)
            helper_row.addWidget(self.fetch_current_button)
            helper_row.addWidget(self.use_limits_button)
            helper_row.addWidget(self.apply_step_hint_button)
            helper_row.addWidget(self.step_hint_factor_spin)
            helper_row.addStretch(1)
            layout.addLayout(helper_row)

            self.table = QtWidgets.QTableWidget(0, 8, self)
            self.table.setHorizontalHeaderLabels(
                ["Use", "Name", "Group", "Current", "Low", "High", "Step Hint", "Limits"]
            )
            self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
            self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
            self.table.setEditTriggers(
                QtWidgets.QAbstractItemView.DoubleClicked
                | QtWidgets.QAbstractItemView.EditKeyPressed
                | QtWidgets.QAbstractItemView.SelectedClicked
            )
            self.table.setAlternatingRowColors(True)
            self.table.verticalHeader().setVisible(False)
            header = self.table.horizontalHeader()
            header.setStretchLastSection(False)
            header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
            header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
            header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
            header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
            header.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeToContents)
            header.setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeToContents)
            header.setSectionResizeMode(6, QtWidgets.QHeaderView.ResizeToContents)
            header.setSectionResizeMode(7, QtWidgets.QHeaderView.ResizeToContents)
            layout.addWidget(self.table, 1)

            self.status_label = QtWidgets.QLabel()
            self.status_label.setWordWrap(True)
            layout.addWidget(self.status_label)

            buttons = QtWidgets.QDialogButtonBox(
                QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel,
                self,
            )
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)

            self.fetch_current_button.clicked.connect(self.fetch_current_values)
            self.use_limits_button.clicked.connect(self.apply_ranges_from_limits)
            self.apply_step_hint_button.clicked.connect(self.apply_ranges_from_step_hint)
            self.table.itemChanged.connect(self._update_status)

            self._populate_table()

        def _populate_table(self) -> None:
            blocker = QtCore.QSignalBlocker(self.table)
            self.table.clearContents()
            self.table.setRowCount(len(self._knobs))

            for row, knob in enumerate(self._knobs):
                group_label = self._group_labels.get(knob.group, knob.group)
                limits = f"{float(knob.limits.low):.6g} .. {float(knob.limits.high):.6g}"
                row_state = self._current_state.get(knob.id, {})
                current_text = str(row_state.get("current_text", "--"))
                low_text = str(row_state.get("low_text", f"{float(knob.limits.low):.6g}"))
                high_text = str(row_state.get("high_text", f"{float(knob.limits.high):.6g}"))

                enabled_item = QtWidgets.QTableWidgetItem()
                enabled_item.setFlags(
                    QtCore.Qt.ItemIsSelectable
                    | QtCore.Qt.ItemIsEnabled
                    | QtCore.Qt.ItemIsUserCheckable
                )
                enabled_item.setCheckState(
                    QtCore.Qt.Checked if row_state.get("enabled", True) else QtCore.Qt.Unchecked
                )
                self.table.setItem(row, 0, enabled_item)

                for col, value in (
                    (1, knob.name),
                    (2, group_label),
                    (3, current_text),
                    (4, low_text),
                    (5, high_text),
                    (6, f"{float(knob.step_hint):.6g}"),
                    (7, limits),
                ):
                    item = QtWidgets.QTableWidgetItem(str(value))
                    if col in {1, 2, 3, 6, 7}:
                        item.setFlags(QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsEnabled)
                    self.table.setItem(row, col, item)
            del blocker
            self._update_status()

        def selected_state(self) -> dict[str, dict[str, object]]:
            state = {}
            for row, knob_id in enumerate(self._knob_row_ids):
                enabled_item = self.table.item(row, 0)
                current_item = self.table.item(row, 3)
                low_item = self.table.item(row, 4)
                high_item = self.table.item(row, 5)
                state[knob_id] = {
                    "enabled": enabled_item.checkState() == QtCore.Qt.Checked if enabled_item else True,
                    "current_text": current_item.text().strip() if current_item else "",
                    "low_text": low_item.text().strip() if low_item else "",
                    "high_text": high_item.text().strip() if high_item else "",
                }
            return state

        def fetch_current_values(self) -> None:
            if self._epics_client is None:
                self.status_label.setText("EPICS client is not available.")
                return

            connected = 0
            blocker = QtCore.QSignalBlocker(self.table)
            for row, knob in enumerate(self._knobs):
                try:
                    result = self._epics_client.read(knob.readback_pv or knob.write_pv)
                except Exception as exc:
                    self.status_label.setText(str(exc))
                    del blocker
                    return

                value_text = "--"
                if result.connected and result.value is not None:
                    try:
                        value_text = f"{float(result.value):.6g}"
                        connected += 1
                    except (TypeError, ValueError):
                        value_text = "--"
                self.table.item(row, 3).setText(value_text)
            del blocker
            self.status_label.setText(
                f"Fetched current values for {connected}/{len(self._knobs)} knob(s)."
            )

        def apply_ranges_from_limits(self) -> None:
            blocker = QtCore.QSignalBlocker(self.table)
            for row, knob in enumerate(self._knobs):
                enabled_item = self.table.item(row, 0)
                if enabled_item is not None and enabled_item.checkState() != QtCore.Qt.Checked:
                    continue
                self.table.item(row, 4).setText(f"{float(knob.limits.low):.6g}")
                self.table.item(row, 5).setText(f"{float(knob.limits.high):.6g}")
            del blocker
            self.status_label.setText("Applied configured knob limits to enabled rows.")

        def apply_ranges_from_step_hint(self) -> None:
            factor = float(self.step_hint_factor_spin.value())
            applied = 0
            skipped = 0
            blocker = QtCore.QSignalBlocker(self.table)
            for row, knob in enumerate(self._knobs):
                enabled_item = self.table.item(row, 0)
                if enabled_item is not None and enabled_item.checkState() != QtCore.Qt.Checked:
                    continue
                current_text = self.table.item(row, 3).text().strip()
                try:
                    center = float(current_text)
                except (TypeError, ValueError):
                    skipped += 1
                    continue
                delta = abs(float(knob.step_hint)) * max(factor, 0.0)
                low = max(float(knob.limits.low), center - delta)
                high = min(float(knob.limits.high), center + delta)
                self.table.item(row, 4).setText(f"{low:.6g}")
                self.table.item(row, 5).setText(f"{high:.6g}")
                applied += 1
            del blocker
            self.status_label.setText(
                f"Applied +/- step-hint x {factor:.3g} for {applied} knob(s)."
                + (f" Skipped {skipped} knob(s) without current values." if skipped else "")
            )

        def _update_status(self) -> None:
            state = self.selected_state()
            enabled = sum(1 for row in state.values() if row["enabled"])
            self.status_label.setText(
                f"{enabled}/{len(self._knobs)} knob(s) enabled for random sampling."
            )

else:

    class RandomKnobConfigDialog:  # pragma: no cover - fallback for non-GUI tests
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("PyQt5 is required to create RandomKnobConfigDialog")
