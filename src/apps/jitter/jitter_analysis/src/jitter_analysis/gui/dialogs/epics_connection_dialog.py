from __future__ import annotations

from dataclasses import dataclass

try:
    from PyQt5 import QtWidgets
except ImportError:  # pragma: no cover - optional runtime dependency
    QtWidgets = None


@dataclass(frozen=True, slots=True)
class ConnectionFailure:
    endpoint_type: str
    name: str
    pv_name: str
    group: str


if QtWidgets is not None:

    class EpicsConnectionResultsDialog(QtWidgets.QDialog):
        def __init__(
            self,
            *,
            connected: int,
            total: int,
            failures: list[ConnectionFailure],
            parent=None,
        ) -> None:
            super().__init__(parent)
            self.failures = list(failures)
            self.setWindowTitle("Check EPICS Results")
            self.resize(900, 460)

            layout = QtWidgets.QVBoxLayout(self)

            self.summary_label = QtWidgets.QLabel(
                f"{connected}/{total} endpoint(s) connected. "
                f"{len(self.failures)} disconnected.",
                self,
            )
            self.summary_label.setProperty("role", "statusPill")
            self.summary_label.setProperty("tone", "danger")
            layout.addWidget(self.summary_label)

            description = QtWidgets.QLabel(
                "The endpoints below did not connect. Copy their EPICS PV names "
                "for command-line diagnosis.",
                self,
            )
            description.setWordWrap(True)
            layout.addWidget(description)

            self.table = QtWidgets.QTableWidget(len(self.failures), 4, self)
            self.table.setHorizontalHeaderLabels(["Type", "Name", "EPICS PV", "Group"])
            self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
            self.table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
            self.table.setAlternatingRowColors(True)
            self.table.verticalHeader().setVisible(False)
            for row, failure in enumerate(self.failures):
                values = [
                    failure.endpoint_type,
                    failure.name,
                    failure.pv_name,
                    failure.group,
                ]
                for column, value in enumerate(values):
                    item = QtWidgets.QTableWidgetItem(value)
                    item.setToolTip(value)
                    self.table.setItem(row, column, item)
            self.table.resizeColumnsToContents()
            self.table.horizontalHeader().setStretchLastSection(False)
            self.table.horizontalHeader().setSectionResizeMode(
                2, QtWidgets.QHeaderView.Stretch
            )
            layout.addWidget(self.table, 1)

            self.copy_status_label = QtWidgets.QLabel("", self)
            layout.addWidget(self.copy_status_label)

            button_box = QtWidgets.QDialogButtonBox(self)
            self.copy_button = button_box.addButton(
                "Copy Failed PVs", QtWidgets.QDialogButtonBox.ActionRole
            )
            self.close_button = button_box.addButton(QtWidgets.QDialogButtonBox.Close)
            self.copy_button.setProperty("role", "diagnostic")
            self.close_button.setProperty("role", "subtle")
            self.copy_button.setMinimumHeight(40)
            self.close_button.setMinimumHeight(40)
            self.copy_button.clicked.connect(self._copy_failed_pvs)
            self.close_button.clicked.connect(self.accept)
            layout.addWidget(button_box)

        def failed_pv_names(self) -> list[str]:
            return list(dict.fromkeys(failure.pv_name for failure in self.failures))

        def failed_pv_text(self) -> str:
            return "\n".join(self.failed_pv_names())

        def _copy_failed_pvs(self) -> None:
            pv_names = self.failed_pv_names()
            clipboard = QtWidgets.QApplication.clipboard()
            clipboard.setText("\n".join(pv_names))
            self.copy_status_label.setText(
                f"Copied {len(pv_names)} unique failed PV name(s)."
            )

else:

    class EpicsConnectionResultsDialog:  # pragma: no cover - fallback for non-GUI tests
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("PyQt5 is required to create EpicsConnectionResultsDialog")
