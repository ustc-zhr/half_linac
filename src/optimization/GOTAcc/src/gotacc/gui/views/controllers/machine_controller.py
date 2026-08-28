from __future__ import annotations

import copy
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidgetItem,
    QTabWidget,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from gotacc.interfaces.policies import POLICY_REGISTRY

if TYPE_CHECKING:  # pragma: no cover
    from ..main_window import MainWindow

try:
    from ...services.machine_profile import (
        MACHINE_PROFILE_VERSION,
        MachineProfile,
        load_machine_profile,
        save_machine_profile,
    )
    from ...services.pv_library import PVLibraryDocument, PVLibraryItem, load_pv_library_file
    from ...services.task_service import TaskService
    from ..tool_dialogs import PVLibrarySelectorDialog, PVMappingSelectorDialog
except ImportError:  # pragma: no cover - local script fallback
    CURRENT_DIR = Path(__file__).resolve().parent
    GUI_ROOT = CURRENT_DIR.parents[1]
    for path in (GUI_ROOT, GUI_ROOT / "services", GUI_ROOT / "views"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    from machine_profile import (
        MACHINE_PROFILE_VERSION,
        MachineProfile,
        load_machine_profile,
        save_machine_profile,
    )
    from pv_library import PVLibraryDocument, PVLibraryItem, load_pv_library_file
    from task_service import TaskService
    from tool_dialogs import PVLibrarySelectorDialog, PVMappingSelectorDialog


class MachineController:
    def __init__(self, window: "MainWindow") -> None:
        self.window = window
        self.view = window.view_adapter
        self._loaded_pv_library: PVLibraryDocument | None = None
        self._last_sync_snapshot: dict[str, list[dict[str, str]]] | None = None

    @staticmethod
    def _default_config_directory() -> Path:
        pv_library_dir = Path(__file__).resolve().parents[5] / "config" / "pv_libraries"
        return pv_library_dir if pv_library_dir.exists() else Path.cwd()

    def init_machine_page(self) -> None:
        self._configure_simplified_machine_page()
        self.refresh_selected_library_tables()
        self.refresh_machine_summary()

    def _configure_simplified_machine_page(self) -> None:
        ui = self.window.machine_ui
        if hasattr(ui, "tab_advancedMachine"):
            return

        self._configure_machine_profile_bar()
        self._configure_simple_connection_panel()
        self._configure_pv_mapping_actions()
        self._configure_pv_mapping_master_detail()
        self._configure_policy_options()
        self._move_advanced_machine_controls()

    @staticmethod
    def _machine_profile_directory() -> Path:
        return Path(__file__).resolve().parents[5] / "config" / "machine_profiles"

    def _configure_machine_profile_bar(self) -> None:
        ui = self.window.machine_ui
        frame = QFrame(self.window.machine_page)
        frame.setObjectName("machineProfileBar")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)
        title = QLabel("Machine Profile", frame)
        summary = QFrame(frame)
        summary_layout = QVBoxLayout(summary)
        summary_layout.setContentsMargins(8, 0, 8, 0)
        summary_layout.setSpacing(1)
        name_label = QLabel("Embedded Machine · v1", summary)
        name_label.setObjectName("machineProfileName")
        name_label.setProperty("role", "value")
        source_label = QLabel("Built-in", summary)
        source_label.setObjectName("machineProfileSource")
        source_label.setProperty("role", "title")
        summary_layout.addWidget(name_label)
        summary_layout.addWidget(source_label)
        open_button = QPushButton("Open", frame)
        save_button = QPushButton("Save As", frame)
        for button in (open_button, save_button):
            button.setProperty("inlineAction", True)
            button.setFixedSize(88, 28)
        layout.addWidget(title)
        layout.addWidget(summary)
        layout.addStretch(1)
        layout.addWidget(open_button)
        layout.addWidget(save_button)
        frame.setMaximumHeight(54)
        ui.verticalLayout_main.insertWidget(1, frame)
        ui.frame_machineProfile = frame
        ui.label_machineProfileName = name_label
        ui.label_machineProfileSource = source_label
        ui.label_machineProfileStatus = name_label
        ui.pushButton_openMachineProfile = open_button
        ui.pushButton_saveMachineProfile = save_button
        open_button.clicked.connect(self.open_machine_profile)
        save_button.clicked.connect(self.save_machine_profile_as)
        self.refresh_machine_profile_bar()

    def refresh_machine_profile_bar(self) -> None:
        ui = self.window.machine_ui
        if not hasattr(ui, "label_machineProfileName"):
            return
        current = getattr(ui, "machine_profile", {}) or {}
        current_source = str(current.get("source", ""))
        name = str(current.get("name", "Embedded Machine"))
        version = int(current.get("version", MACHINE_PROFILE_VERSION) or MACHINE_PROFILE_VERSION)
        source_text = Path(current_source).name if current_source else "Built-in"
        ui.label_machineProfileName.setText(f"{name} · v{version}")
        ui.label_machineProfileName.setToolTip(
            f"Machine Profile: {name}, version {version}"
        )
        ui.label_machineProfileSource.setText(source_text)
        ui.label_machineProfileSource.setToolTip(current_source or "Built-in Machine Profile")

    def _selected_machine_profile_path(self) -> str:
        path, _ = QFileDialog.getOpenFileName(
            self.window,
            "Open Machine Profile",
            str(self._machine_profile_directory()),
            "GOTAcc Machine Profile (*.json);;All Files (*)",
        )
        return path

    def open_machine_profile(self) -> None:
        path = self._selected_machine_profile_path()
        if not path:
            return
        try:
            profile = load_machine_profile(path)
            machine = copy.deepcopy(profile.machine)
            machine["profile"] = {
                "profile_id": profile.profile_id,
                "name": profile.name,
                "version": profile.version,
                "source": str(Path(path).resolve()),
            }
            old_suppress = self.window._suppress_autofill
            self.window._suppress_autofill = True
            try:
                self.window.task_builder_controller.apply_machine_payload(
                    machine,
                    refresh=False,
                )
            finally:
                self.window._suppress_autofill = old_suppress
        except Exception as exc:
            QMessageBox.critical(self.window, "Open Machine Profile Failed", str(exc))
            return
        self.refresh_selected_library_tables()
        self.view.refresh_task_preview()
        self.view.log_console(
            f"Loaded Machine Profile {profile.name!r}; Task Builder remains unchanged until Sync To Task."
        )
        self.view.status_message(f"Machine Profile loaded: {profile.name}", 5000)

    def save_machine_profile_as(self) -> None:
        current = getattr(self.window.machine_ui, "machine_profile", {}) or {}
        default_name = str(current.get("name", "")).strip()
        if not default_name or current.get("profile_id") == "embedded":
            default_name = "New Machine"
        name, accepted = QInputDialog.getText(
            self.window,
            "Save Machine Profile",
            "Profile name:",
            text=default_name,
        )
        name = name.strip()
        if not accepted or not name:
            return
        machine = copy.deepcopy(self.view.current_task().get("machine", {}) or {})
        machine.pop("profile", None)
        try:
            profile = MachineProfile.create(name, machine)
        except ValueError as exc:
            QMessageBox.warning(self.window, "Save Machine Profile", str(exc))
            return
        profile_dir = self._machine_profile_directory()
        profile_dir.mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(
            self.window,
            "Save Machine Profile",
            str(profile_dir / f"{profile.profile_id}.json"),
            "GOTAcc Machine Profile (*.json);;All Files (*)",
        )
        if not path:
            return
        if not path.lower().endswith(".json"):
            path = f"{path}.json"
        try:
            save_machine_profile(profile, path)
        except Exception as exc:
            QMessageBox.critical(self.window, "Save Machine Profile Failed", str(exc))
            return
        self.window.machine_ui.machine_profile = {
            "profile_id": profile.profile_id,
            "name": profile.name,
            "version": profile.version,
            "source": str(Path(path).resolve()),
        }
        self.refresh_machine_profile_bar()
        self.view.refresh_task_preview()
        self.view.log_console(f"Machine Profile saved to: {path}")
        self.view.status_message(f"Machine Profile saved: {Path(path).name}", 5000)

    def _configure_policy_options(self) -> None:
        combo = self.window.machine_ui.comboBox_policy
        current = combo.currentText().strip().lower()
        options = ["none", *POLICY_REGISTRY.names("write", gui_only=True)]
        combo.clear()
        combo.addItems(options)
        combo.setCurrentText(current if current in options else "none")

    def _configure_simple_connection_panel(self) -> None:
        ui = self.window.machine_ui
        ui.groupBox_connection.setTitle("EPICS")
        ui.label_status.setText("Status")
        ui.label_caAddress.setVisible(False)
        ui.lineEdit_caAddress.setVisible(False)
        ui.checkBox_autoConnect.setVisible(False)
        ui.pushButton_connect.setVisible(False)
        ui.pushButton_disconnect.setVisible(False)
        ui.pushButton_test.setText("Check")
        ui.pushButton_test.setToolTip("Read the first configured EPICS PV for the current online task.")
        ui.pushButton_test.setProperty("inlineAction", True)
        ui.pushButton_test.setFixedHeight(24)
        ui.pushButton_test.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        ui.label_statusValue.setProperty("role", "statusPill")
        ui.label_statusValue.setMinimumWidth(104)
        ui.label_statusValue.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        ui.label_timeout.setToolTip("Maximum wait time for GUI EPICS PV reads.")
        ui.doubleSpinBox_timeout.setToolTip(
            "Used by Check, current-knob reads and PV Monitor; it does not control PV writes."
        )
        ui.doubleSpinBox_timeout.setMaximumWidth(110)

        ui.verticalLayout_connectionBox.removeItem(ui.formLayout_connection)
        connection_row = QHBoxLayout()
        connection_row.setObjectName("horizontalLayout_connectionSummary")
        ui.horizontalLayout_connectionSummary = connection_row
        connection_row.setContentsMargins(0, 0, 0, 0)
        connection_row.setSpacing(8)
        ui.label_status.setParent(ui.groupBox_connection)
        ui.label_statusValue.setParent(ui.groupBox_connection)
        ui.label_timeout.setParent(ui.groupBox_connection)
        ui.doubleSpinBox_timeout.setParent(ui.groupBox_connection)
        ui.pushButton_test.setParent(ui.groupBox_connection)
        connection_row.addWidget(ui.label_status)
        connection_row.addWidget(ui.label_statusValue)
        connection_row.addStretch(1)
        connection_row.addWidget(ui.label_timeout)
        connection_row.addWidget(ui.doubleSpinBox_timeout)
        connection_row.addWidget(ui.pushButton_test)
        ui.verticalLayout_connectionBox.addLayout(connection_row)
        ui.horizontalLayout_buttons.setContentsMargins(0, 0, 0, 0)
        ui.horizontalLayout_buttons.setSpacing(6)
        ui.groupBox_connection.setMaximumHeight(82)
        ui.pushButton_test.style().unpolish(ui.pushButton_test)
        ui.pushButton_test.style().polish(ui.pushButton_test)

    def _configure_pv_mapping_actions(self) -> None:
        ui = self.window.machine_ui
        select_button = QPushButton("Select PVs", ui.frame_pvPresetLibrary)
        select_button.setObjectName("pushButton_selectPvs")
        select_button.setToolTip("Load a PV library if needed, then choose knobs, objectives and constraints in one flow.")
        select_button.clicked.connect(self.open_pv_mapping_dialog)
        ui.horizontalLayout_pvLibraryControls.insertWidget(0, select_button)
        ui.pushButton_selectPvs = select_button

        ui.pushButton_loadPvLibrary.setVisible(False)
        ui.pushButton_applySelectedPvLibrary.setText("Sync To Task")
        ui.pushButton_applySelectedPvLibrary.setToolTip(
            "Merge PV Mapping into Task Builder by role and name. Existing task parameters are preserved."
        )
        ui.horizontalLayout_pvLibraryControls.removeWidget(ui.pushButton_applySelectedPvLibrary)
        ui.horizontalLayout_pvLibraryControls.insertWidget(1, ui.pushButton_applySelectedPvLibrary)
        undo_button = QPushButton("Undo Sync", ui.frame_pvPresetLibrary)
        undo_button.setObjectName("pushButton_undoMappingSync")
        undo_button.setToolTip("Restore Task Builder rows from before the most recent mapping sync.")
        undo_button.setEnabled(False)
        undo_button.clicked.connect(self.undo_last_mapping_sync)
        ui.horizontalLayout_pvLibraryControls.insertWidget(2, undo_button)
        ui.pushButton_undoMappingSync = undo_button
        issues_button = QPushButton("Review Issues", ui.frame_pvPresetLibrary)
        issues_button.setObjectName("mappingIssuesButton")
        issues_button.setProperty("danger", True)
        issues_button.setVisible(False)
        issues_button.clicked.connect(self.review_mapping_issues)
        ui.horizontalLayout_pvLibraryControls.insertWidget(3, issues_button)
        ui.pushButton_reviewMappingIssues = issues_button
        ui.horizontalLayout_pvLibraryControls.setContentsMargins(0, 0, 0, 0)
        ui.horizontalLayout_pvLibraryControls.setSpacing(6)
        ui.verticalLayout_pvPresetLibrary.setContentsMargins(8, 5, 8, 5)
        ui.verticalLayout_pvPresetLibrary.setSpacing(0)
        ui.verticalLayout_pvPresetLibrary.removeWidget(ui.label_pvLibrarySummary)
        ui.label_pvLibrarySummary.setVisible(True)
        ui.label_pvLibrarySummary.setProperty("role", "mappingStatus")
        ui.label_pvLibrarySummary.setWordWrap(False)
        ui.label_pvLibrarySummary.setMinimumHeight(24)
        ui.label_pvLibrarySummary.setSizePolicy(
            QSizePolicy.Preferred, QSizePolicy.Fixed
        )
        ui.label_pvLibrarySummary.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        ui.horizontalLayout_pvLibraryControls.addWidget(
            ui.label_pvLibrarySummary, 0, Qt.AlignRight | Qt.AlignVCenter
        )
        ui.frame_pvPresetLibrary.setMaximumHeight(40)
        for button in (
            ui.pushButton_selectPvs,
            ui.pushButton_applySelectedPvLibrary,
            ui.pushButton_undoMappingSync,
            ui.pushButton_reviewMappingIssues,
        ):
            button.setProperty("inlineAction", True)
            button.setFixedHeight(24)
            button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
            button.style().unpolish(button)
            button.style().polish(button)
        ui.label_statusValue.style().unpolish(ui.label_statusValue)
        ui.label_statusValue.style().polish(ui.label_statusValue)

        for widget in (
            ui.pushButton_pickKnobsFromLibrary,
            ui.pushButton_clearSelectedKnobs,
            ui.pushButton_pickObjectivesFromLibrary,
            ui.pushButton_clearSelectedObjectives,
            ui.pushButton_pickConstraintsFromLibrary,
            ui.pushButton_clearSelectedConstraints,
            ui.label_pvLibrarySource,
            ui.frame_selectedLibrarySummary,
        ):
            widget.setVisible(False)

    def _configure_pv_mapping_master_detail(self) -> None:
        ui = self.window.machine_ui
        table = ui.tableWidget_mapping
        layout = ui.verticalLayout_mapping
        layout.removeWidget(table)

        splitter = QSplitter(Qt.Horizontal, ui.tab_mapping)
        splitter.setObjectName("splitter_pvMapping")
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(6)
        table.setParent(splitter)
        splitter.addWidget(table)

        detail_scroll = QScrollArea(splitter)
        detail_scroll.setObjectName("scrollArea_mappingDetail")
        detail_scroll.setWidgetResizable(True)
        detail_scroll.setFrameShape(QFrame.NoFrame)
        detail_scroll.setMinimumWidth(300)
        detail_scroll.setMaximumWidth(420)
        detail = QFrame()
        detail.setObjectName("mappingDetailPanel")
        detail.setMinimumWidth(280)
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(16, 14, 16, 14)
        detail_layout.setSpacing(12)

        header = QLabel("Select a machine signal", detail)
        header.setObjectName("mappingDetailTitle")
        header.setWordWrap(True)
        detail_layout.addWidget(header)
        subtitle = QLabel(
            "Signal definitions come from the PV library. Assign policies here; "
            "Sync To Task preserves task-side settings.",
            detail,
        )
        subtitle.setObjectName("mappingDetailSubtitle")
        subtitle.setWordWrap(True)
        detail_layout.addWidget(subtitle)

        signal_group = QGroupBox("Signal", detail)
        signal_group.setMinimumHeight(245)
        signal_form = QFormLayout(signal_group)
        signal_form.setContentsMargins(10, 12, 10, 10)
        signal_form.setHorizontalSpacing(10)
        signal_form.setVerticalSpacing(8)
        role_combo = QComboBox(signal_group)
        role_combo.addItems(["knob", "objective", "constraint"])
        name_edit = QLineEdit(signal_group)
        pv_edit = QLineEdit(signal_group)
        readback_edit = QLineEdit(signal_group)
        group_edit = QLineEdit(signal_group)
        note_edit = QLineEdit(signal_group)
        role_combo.setEnabled(False)
        role_combo.setToolTip("Defined by the selected PV library entry.")
        for editor in (
            name_edit,
            pv_edit,
            readback_edit,
            group_edit,
            note_edit,
        ):
            editor.setReadOnly(True)
            editor.setToolTip("Defined by the selected PV library entry.")
        signal_form.addRow("Role", role_combo)
        signal_form.addRow("Name", name_edit)
        signal_form.addRow("PV Name", pv_edit)
        signal_form.addRow("Readback", readback_edit)
        signal_form.addRow("Group", group_edit)
        signal_form.addRow("Note", note_edit)
        policy_group = QGroupBox("Policies", detail)
        policy_group.setMinimumHeight(105)
        policy_layout = QVBoxLayout(policy_group)
        policy_summary = QLabel("Select an objective or constraint signal.", policy_group)
        policy_summary.setWordWrap(True)
        policy_layout.addWidget(policy_summary)
        manage_button = QPushButton("Add Policy", policy_group)
        manage_button.setProperty("inlineAction", True)
        manage_button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        policy_layout.addWidget(manage_button, 0, Qt.AlignLeft)
        detail_layout.addWidget(policy_group)
        detail_layout.addWidget(signal_group)
        detail_layout.addStretch(1)

        detail_scroll.setWidget(detail)
        splitter.addWidget(detail_scroll)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([850, 340])
        layout.addWidget(splitter, 1)

        ui.splitter_pvMapping = splitter
        ui.scrollArea_mappingDetail = detail_scroll
        ui.frame_mappingDetail = detail
        ui.label_mappingDetailTitle = header
        ui.label_mappingDetailSubtitle = subtitle
        ui.groupBox_mappingSignal = signal_group
        ui.comboBox_mappingDetailRole = role_combo
        ui.lineEdit_mappingDetailName = name_edit
        ui.lineEdit_mappingDetailPv = pv_edit
        ui.lineEdit_mappingDetailReadback = readback_edit
        ui.lineEdit_mappingDetailGroup = group_edit
        ui.lineEdit_mappingDetailNote = note_edit
        ui.groupBox_mappingPolicies = policy_group
        ui.label_mappingPolicySummary = policy_summary
        ui.pushButton_manageMappingPolicies = manage_button

        headers = self.window.task_builder_controller.table_headers(table)
        for field in ("Readback", "Group", "Note", "Policy Action"):
            table.setColumnHidden(headers.index(field), True)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setAlternatingRowColors(False)
        table.setShowGrid(False)
        table.verticalHeader().setDefaultSectionSize(36)
        header_view = table.horizontalHeader()
        role_col = headers.index("Role")
        name_col = headers.index("Name")
        pv_col = headers.index("PV Name")
        policy_col = headers.index("Policies")
        header_view.setSectionResizeMode(role_col, QHeaderView.Fixed)
        header_view.setSectionResizeMode(name_col, QHeaderView.Fixed)
        header_view.setSectionResizeMode(pv_col, QHeaderView.Stretch)
        header_view.setSectionResizeMode(policy_col, QHeaderView.Fixed)
        table.setColumnWidth(role_col, 96)
        table.setColumnWidth(name_col, 160)
        table.setColumnWidth(policy_col, 230)

        table.currentCellChanged.connect(
            lambda current_row, _current_col, _previous_row, _previous_col: (
                self.refresh_mapping_detail(current_row)
            )
        )
        manage_button.clicked.connect(self._manage_selected_mapping_policies)

        if table.rowCount():
            table.setCurrentCell(0, name_col)
        else:
            self.refresh_mapping_detail(-1)

    def _set_mapping_detail_enabled(self, enabled: bool) -> None:
        ui = self.window.machine_ui
        ui.comboBox_mappingDetailRole.setEnabled(False)
        for widget in (
            ui.lineEdit_mappingDetailName,
            ui.lineEdit_mappingDetailPv,
            ui.lineEdit_mappingDetailReadback,
            ui.lineEdit_mappingDetailGroup,
            ui.lineEdit_mappingDetailNote,
        ):
            widget.setEnabled(enabled)

    def refresh_mapping_detail(self, row: int | None = None) -> None:
        ui = self.window.machine_ui
        if not hasattr(ui, "frame_mappingDetail"):
            return
        table = ui.tableWidget_mapping
        current_row = table.currentRow() if row is None else row
        headers = self.window.task_builder_controller.table_headers(table)
        if current_row < 0 or current_row >= table.rowCount():
            self._set_mapping_detail_enabled(False)
            ui.label_mappingDetailTitle.setText("Select a machine signal")
            ui.label_mappingPolicySummary.setText(
                "Select an objective or constraint signal."
            )
            ui.pushButton_manageMappingPolicies.setEnabled(False)
            return

        def value(field: str) -> str:
            item = table.item(current_row, headers.index(field))
            return item.text().strip() if item is not None else ""

        role = value("Role").lower()
        name = value("Name")
        self._set_mapping_detail_enabled(True)
        ui.comboBox_mappingDetailRole.setCurrentText(role or "objective")
        ui.lineEdit_mappingDetailName.setText(name)
        ui.lineEdit_mappingDetailPv.setText(value("PV Name"))
        ui.lineEdit_mappingDetailReadback.setText(value("Readback"))
        ui.lineEdit_mappingDetailGroup.setText(value("Group"))
        ui.lineEdit_mappingDetailNote.setText(value("Note"))

        role_label = role.title() if role else "Unassigned"
        ui.label_mappingDetailTitle.setText(f"{role_label} · {name or 'Unnamed signal'}")
        policy_enabled = role in {"objective", "constraint"} and bool(name)
        ui.groupBox_mappingPolicies.setVisible(role in {"objective", "constraint"})
        ui.pushButton_manageMappingPolicies.setEnabled(policy_enabled)
        if not policy_enabled:
            ui.label_mappingPolicySummary.setText("Policies do not apply to knob rows.")
            return
        bound = self.window._bound_policy_rows(role, name)
        if not bound:
            ui.label_mappingPolicySummary.setText("No policies assigned.")
            ui.pushButton_manageMappingPolicies.setText("Add Policy")
        else:
            enabled = sum(bool(policy["enabled"]) for policy in bound)
            lines = [
                f"• {policy['preset']} · {policy['status']} — "
                f"{policy['issue'] or policy['summary']}"
                for policy in bound
            ]
            ui.label_mappingPolicySummary.setText("\n".join(lines))
            ui.pushButton_manageMappingPolicies.setText(
                f"Manage {len(bound)} " + ("Policy" if len(bound) == 1 else "Policies")
            )
            ui.pushButton_manageMappingPolicies.setToolTip(
                f"{enabled} of {len(bound)} policies enabled."
            )

    def _manage_selected_mapping_policies(self) -> None:
        row = self.window.machine_ui.tableWidget_mapping.currentRow()
        if row >= 0:
            self.window._manage_mapping_policies(row)

    def review_mapping_issues(self) -> None:
        errors = self._mapping_sync_errors()
        if not errors:
            QMessageBox.information(self.window, "PV Mapping", "No mapping issues found.")
            return
        QMessageBox.warning(self.window, "PV Mapping Issues", "\n".join(errors))
        table = self.window.machine_ui.tableWidget_mapping
        fields = self.window.task_builder_controller.table_headers(table)
        focused = False
        for row in range(table.rowCount()):
            role_item = table.item(row, fields.index("Role"))
            name_item = table.item(row, fields.index("Name"))
            pv_item = table.item(row, fields.index("PV Name"))
            role = role_item.text().strip().lower() if role_item is not None else ""
            name = name_item.text().strip() if name_item is not None else ""
            pv_name = pv_item.text().strip() if pv_item is not None else ""
            if role not in {"knob", "objective", "constraint"} or not name or not pv_name:
                table.selectRow(row)
                table.setCurrentCell(row, fields.index("Name" if not name else "PV Name"))
                self.refresh_mapping_detail(row)
                focused = True
                break
        if focused:
            return
        policy_issues = TaskService.policy_binding_issues(self.view.current_task())
        if not policy_issues:
            return
        first = policy_issues[0]
        issue_kind = str(first.get("kind", ""))
        issue_target = str(first.get("target", ""))
        for row in range(table.rowCount()):
            role_item = table.item(row, fields.index("Role"))
            name_item = table.item(row, fields.index("Name"))
            role = role_item.text().strip().lower() if role_item is not None else ""
            name = name_item.text().strip() if name_item is not None else ""
            if role == issue_kind and name == issue_target:
                table.selectRow(row)
                table.setCurrentCell(row, fields.index("Policies"))
                self.refresh_mapping_detail(row)
                break

    def _move_advanced_machine_controls(self) -> None:
        ui = self.window.machine_ui
        main_tabs = ui.tabWidget_machine

        for page in (ui.tab_writePolicy,):
            index = main_tabs.indexOf(page)
            if index >= 0:
                main_tabs.removeTab(index)

        ui.groupBox_guard.setParent(None)

        advanced_page = QWidget(main_tabs)
        advanced_page.setObjectName("tab_advancedMachine")
        advanced_layout = QVBoxLayout(advanced_page)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.setSpacing(0)
        advanced_tabs = QTabWidget(advanced_page)
        advanced_tabs.setObjectName("tabWidget_machineAdvanced")
        advanced_tabs.setDocumentMode(True)
        advanced_layout.addWidget(advanced_tabs)

        safeguards_page = QWidget(main_tabs)
        safeguards_page.setObjectName("tab_runSafeguards")
        safeguards_layout = QVBoxLayout(safeguards_page)
        safeguards_layout.setContentsMargins(10, 12, 10, 10)
        safeguards_layout.setSpacing(0)
        ui.groupBox_guard.setTitle("")
        ui.groupBox_guard.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        ui.checkBox_readbackCheck.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        ui.formLayout_guard.setHorizontalSpacing(10)
        ui.formLayout_guard.setVerticalSpacing(8)
        safeguards_layout.addWidget(ui.groupBox_guard, 0, Qt.AlignTop)
        safeguards_layout.addStretch(1)
        ui.groupBox_guard.show()

        advanced_tabs.addTab(ui.tab_writePolicy, "Write Policy")
        preset_page = QWidget(advanced_tabs)
        preset_layout = QVBoxLayout(preset_page)
        preset_layout.setContentsMargins(12, 12, 12, 12)
        preset_layout.setSpacing(10)
        intro = QLabel(
            "Reusable starting points for common machine-specific behavior. "
            "Apply and customize them from a matching objective or constraint "
            "row in PV Mapping.",
            preset_page,
        )
        intro.setWordWrap(True)
        preset_layout.addWidget(intro)
        preset_table = QTableWidget(0, 4, preset_page)
        preset_table.setHorizontalHeaderLabels(
            ["Kind", "Policy Template", "Source", "Description"]
        )
        preset_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        preset_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        preset_table.setSelectionMode(QAbstractItemView.SingleSelection)
        preset_table.setShowGrid(False)
        preset_table.verticalHeader().setVisible(False)
        preset_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        preset_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        preset_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        preset_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        preset_layout.addWidget(preset_table, 1)
        preset_actions = QHBoxLayout()
        preset_hint = QLabel(
            "To create one, open a PV Mapping policy and choose Save as Template.",
            preset_page,
        )
        rename_button = QPushButton("Rename", preset_page)
        delete_button = QPushButton("Delete", preset_page)
        rename_button.setEnabled(False)
        delete_button.setEnabled(False)
        preset_actions.addWidget(preset_hint, 1)
        preset_actions.addWidget(rename_button)
        preset_actions.addWidget(delete_button)
        preset_layout.addLayout(preset_actions)
        advanced_tabs.addTab(preset_page, "Templates")
        main_tabs.addTab(safeguards_page, "Run Safeguards")
        main_tabs.addTab(advanced_page, "Policies")
        main_tabs.setCurrentWidget(ui.tab_mapping)

        ui.tab_runSafeguards = safeguards_page
        ui.tab_advancedMachine = advanced_page
        ui.tabWidget_machineAdvanced = advanced_tabs
        ui.tab_policyPresetBrowser = preset_page
        ui.tableWidget_policyPresets = preset_table
        ui.pushButton_renamePolicyPreset = rename_button
        ui.pushButton_deletePolicyPreset = delete_button
        ui.tab_safeguardsAdvanced = safeguards_page
        preset_table.itemSelectionChanged.connect(self._update_policy_preset_actions)
        rename_button.clicked.connect(self._rename_selected_policy_preset)
        delete_button.clicked.connect(self._delete_selected_policy_preset)
        self.refresh_policy_preset_browser()

    def refresh_policy_preset_browser(self) -> None:
        ui = self.window.machine_ui
        if not hasattr(ui, "tableWidget_policyPresets"):
            return
        table = ui.tableWidget_policyPresets
        table.setRowCount(0)
        rows: list[tuple[str, str, str, str, str]] = []
        for kind in ("objective", "constraint"):
            for preset_name in POLICY_REGISTRY.preset_names(kind, gui_only=True):
                preset = POLICY_REGISTRY.resolve_preset(kind, preset_name)
                rows.append(
                    (kind.title(), preset.display_name, "Built-in", preset.description, "")
                )
        for preset in getattr(ui, "policy_presets", []):
            rows.append(
                (
                    str(preset.get("kind", "")).title(),
                    str(preset.get("name", "")),
                    "Machine",
                    str(preset.get("description", "")),
                    str(preset.get("id", "")),
                )
            )
        for values in rows:
            row = table.rowCount()
            table.insertRow(row)
            for column, value in enumerate(values[:4]):
                item = QTableWidgetItem(value)
                if column == 1:
                    item.setData(Qt.UserRole, values[4])
                table.setItem(row, column, item)
        self._update_policy_preset_actions()

    def _selected_custom_policy_preset_id(self) -> str:
        table = self.window.machine_ui.tableWidget_policyPresets
        row = table.currentRow()
        if row < 0:
            return ""
        item = table.item(row, 1)
        return str(item.data(Qt.UserRole) or "") if item is not None else ""

    def _update_policy_preset_actions(self) -> None:
        preset_id = self._selected_custom_policy_preset_id()
        self.window.machine_ui.pushButton_renamePolicyPreset.setEnabled(bool(preset_id))
        self.window.machine_ui.pushButton_deletePolicyPreset.setEnabled(bool(preset_id))

    def _rename_selected_policy_preset(self) -> None:
        preset_id = self._selected_custom_policy_preset_id()
        if preset_id:
            self.window._rename_custom_policy_preset(preset_id)

    def _delete_selected_policy_preset(self) -> None:
        preset_id = self._selected_custom_policy_preset_id()
        if preset_id:
            self.window._delete_custom_policy_preset(preset_id)

    @staticmethod
    def _mapping_row_value(row: dict, key: str, default: str = "") -> str:
        return str(row.get(key, default)).strip()

    def is_online_task(self, task: dict | None = None) -> bool:
        current = task if task is not None else self.view.current_task()
        return str(current.get("mode", "")).strip().lower() == "online epics"

    @staticmethod
    def machine_check_identity(task: dict) -> dict:
        machine = task.get("machine", {}) or {}

        def enabled_names(field: str) -> list[str]:
            return [
                str(row.get("Name", "")).strip()
                for row in TaskService._enabled_rows(task.get(field, []))
            ]

        mapping = [
            {
                "role": str(row.get("Role", "")).strip().lower(),
                "name": str(row.get("Name", "")).strip(),
                "pv": str(row.get("PV Name", "")).strip(),
                "readback": str(row.get("Readback", "")).strip(),
            }
            for row in machine.get("mapping", []) or []
            if any(str(value).strip() for value in row.values())
        ]
        mapping.sort(key=lambda row: (row["role"], row["name"], row["pv"], row["readback"]))
        write_links = [
            {
                "source": str(row.get("Source Index", "")).strip(),
                "target": str(row.get("Target PV", "")).strip(),
            }
            for row in machine.get("write_links", []) or []
            if not str(row.get("Enabled", "")).strip()
            or TaskService._is_enabled(row.get("Enabled", ""))
        ]
        write_links.sort(key=lambda row: (row["source"], row["target"]))
        return {
            "mode": str(task.get("mode", "")).strip(),
            "algorithm": str(task.get("algorithm", "")).strip(),
            "variables": enabled_names("variables"),
            "objectives": enabled_names("objectives"),
            "constraints": enabled_names("constraints"),
            "mapping": mapping,
            "write_links": write_links,
            "write_policy": str(machine.get("write_policy", "none")).strip().lower(),
            "readback_check": bool(machine.get("readback_check", False)),
            "ca_address": str(machine.get("ca_address", "")).strip(),
        }

    def invalidate_machine_check_if_stale(self, task: dict | None = None) -> bool:
        saved_identity = self.window.state.machine_check_identity
        if not saved_identity:
            return False
        current = task if task is not None else self.view.current_task()
        if saved_identity == self.machine_check_identity(current):
            return False

        self.window.state.machine_check_identity.clear()
        self.window.state.last_test_read_status = "Stale"
        self.window.state.last_test_read_detail = (
            "PV configuration changed after the most recent check. Run PV Check again."
        )
        self.window.machine_ui.label_statusValue.setText("Stale")
        self.window.ui.label_statusConnectionValue.setText("Stale")
        return True

    def _enabled_task_rows(self, task: dict | None = None) -> tuple[list[dict], list[dict]]:
        current = task if task is not None else self.view.current_task()
        return (
            TaskService._enabled_rows(current.get("variables", [])),
            TaskService._enabled_rows(current.get("objectives", [])),
        )

    def _table_records(self, table) -> list[dict[str, str]]:
        return TaskService.table_to_records(table)

    @staticmethod
    def _coalesce(*values: str, default: str = "") -> str:
        for value in values:
            text = str(value).strip()
            if text:
                return text
        return default

    @staticmethod
    def _normalize_mapping_role(value: str) -> str:
        role = str(value).strip().lower()
        if role == "knob":
            return "knob"
        if role == "objective":
            return "objective"
        if role == "constraint":
            return "constraint"
        return role

    @staticmethod
    def _default_group_for_role(role: str) -> str:
        if role == "knob":
            return "main"
        if role == "constraint":
            return "guard"
        if role == "objective":
            return "metric"
        return ""

    def _mapping_items_for_role(self, role: str) -> list[PVLibraryItem]:
        target_role = self._normalize_mapping_role(role)
        rows = TaskService.table_to_records(self.window.machine_ui.tableWidget_mapping)
        items: list[PVLibraryItem] = []
        for row in rows:
            row_role = self._normalize_mapping_role(self._mapping_row_value(row, "Role"))
            if row_role != target_role:
                continue
            pv_name = self._mapping_row_value(row, "PV Name")
            name = self._mapping_row_value(row, "Name")
            if not pv_name and not name:
                continue
            items.append(
                PVLibraryItem(
                    name=name,
                    pv_name=pv_name,
                    readback=self._mapping_row_value(row, "Readback", default=pv_name),
                    group=self._mapping_row_value(row, "Group", default=self._default_group_for_role(target_role)),
                    note=self._mapping_row_value(row, "Note"),
                )
            )
        return items

    def _mapping_records(self) -> list[dict[str, str]]:
        rows = TaskService.table_to_records(self.window.machine_ui.tableWidget_mapping)
        fields = ("Role", "Name", "PV Name", "Readback", "Group", "Note")
        return [
            {field: row.get(field, "") for field in fields}
            for row in rows
            if any(str(row.get(field, "")).strip() for field in fields)
        ]

    def _mapping_sync_errors(self) -> list[str]:
        errors: list[str] = []
        seen_keys: set[tuple[str, str]] = set()
        names_to_roles: dict[str, str] = {}
        knob_pvs: dict[str, str] = {}

        for index, row in enumerate(self._mapping_records(), start=1):
            role = self._normalize_mapping_role(self._mapping_row_value(row, "Role"))
            name = self._mapping_row_value(row, "Name")
            pv_name = self._mapping_row_value(row, "PV Name")
            if role not in {"knob", "objective", "constraint"}:
                errors.append(f"Mapping row {index} has an invalid Role.")
                continue
            if not name:
                errors.append(f"Mapping row {index} has no Name.")
                continue
            if not pv_name:
                errors.append(f"Mapping row {index} ({name}) has no PV Name.")

            key = (role, name)
            if key in seen_keys:
                errors.append(f"Duplicate mapping for {role} {name!r}.")
            seen_keys.add(key)

            previous_role = names_to_roles.get(name)
            if previous_role is not None and previous_role != role:
                errors.append(
                    f"Mapping name {name!r} is used as both {previous_role} and {role}."
                )
            names_to_roles[name] = role

            if role == "knob" and pv_name:
                previous_name = knob_pvs.get(pv_name)
                if previous_name is not None and previous_name != name:
                    errors.append(
                        f"Knobs {previous_name!r} and {name!r} share Setpoint PV {pv_name!r}."
                    )
                knob_pvs[pv_name] = name

        errors.extend(
            issue["message"]
            for issue in TaskService.policy_binding_issues(self.view.current_task())
        )
        return list(dict.fromkeys(errors))

    def _task_row_names(self, role: str) -> list[str]:
        task = self.view.current_task()
        field = {
            "knob": "variables",
            "objective": "objectives",
            "constraint": "constraints",
        }[role]
        return [
            str(row.get("Name", "")).strip()
            for row in task.get(field, []) or []
            if isinstance(row, dict)
            if str(row.get("Name", "")).strip()
        ]

    def _mapping_names(self, role: str) -> list[str]:
        return [entry.name for entry in self._mapping_items_for_role(role) if entry.name]

    def _selection_matches_task_builder(self) -> bool:
        return all(
            set(self._task_row_names(role)) == set(self._mapping_names(role))
            for role in ("knob", "objective", "constraint")
        )

    def _task_rows_needing_setup(self) -> int:
        count = 0
        for row in TaskService._enabled_rows(self.view.current_task().get("variables", [])):
            try:
                lower = float(row.get("Lower", ""))
                upper = float(row.get("Upper", ""))
                initial = float(row.get("Initial", ""))
            except (TypeError, ValueError):
                count += 1
                continue
            if lower >= upper or not lower <= initial <= upper:
                count += 1
        return count

    @staticmethod
    def _entry_summary(entries: list[PVLibraryItem], *, empty_label: str) -> str:
        if not entries:
            return empty_label
        labels = [entry.name or entry.pv_name for entry in entries]
        if len(labels) <= 6:
            return ", ".join(labels)
        preview = ", ".join(labels[:6])
        return f"{preview}, ... (+{len(labels) - 6} more)"

    def refresh_selected_library_tables(self) -> None:
        mapped_knobs = self._mapping_items_for_role("knob")
        mapped_objectives = self._mapping_items_for_role("objective")
        mapped_constraints = self._mapping_items_for_role("constraint")
        knob_label = getattr(self.window.machine_ui, "label_selectedKnobsSummary", None)
        objective_label = getattr(self.window.machine_ui, "label_selectedObjectivesSummary", None)
        constraint_label = getattr(self.window.machine_ui, "label_selectedConstraintsSummary", None)
        if knob_label is not None:
            knob_label.setText(
                "Mapped Knobs: "
                + self._entry_summary(mapped_knobs, empty_label="none")
            )
        if objective_label is not None:
            objective_label.setText(
                "Mapped Objectives: "
                + self._entry_summary(mapped_objectives, empty_label="none")
            )
        if constraint_label is not None:
            constraint_label.setText(
                "Mapped Constraints: "
                + self._entry_summary(mapped_constraints, empty_label="none")
            )
        self.update_pv_library_summary()

    def update_pv_library_summary(self) -> None:
        source_label = getattr(self.window.machine_ui, "label_pvLibrarySource", None)
        summary_label = getattr(self.window.machine_ui, "label_pvLibrarySummary", None)
        apply_button = getattr(self.window.machine_ui, "pushButton_applySelectedPvLibrary", None)
        if source_label is None or summary_label is None:
            return

        mapped_knobs = self._mapping_items_for_role("knob")
        mapped_objectives = self._mapping_items_for_role("objective")
        mapped_constraints = self._mapping_items_for_role("constraint")
        has_signals = bool(mapped_knobs or mapped_objectives or mapped_constraints)
        selection_matches = self._selection_matches_task_builder()
        online_task = self.is_online_task(self.view.current_task())
        active_run = self.window.state.run.phase in {
            "Running",
            "Stopping",
            "Abort Requested",
            "Restoring",
        }
        undo_button = getattr(self.window.machine_ui, "pushButton_undoMappingSync", None)
        if undo_button is not None:
            undo_button.setEnabled(self._last_sync_snapshot is not None and not active_run)
        errors = self._mapping_sync_errors()
        issues_button = getattr(
            self.window.machine_ui, "pushButton_reviewMappingIssues", None
        )
        if issues_button is not None:
            issues_button.setVisible(bool(errors))
            issues_button.setText(
                f"Review {len(errors)} Issue" + ("" if len(errors) == 1 else "s")
            )
            issues_button.setToolTip("\n".join(errors))
        if errors:
            issue_count = len(errors)
            sync_state = f"{issue_count} Issue" + ("" if issue_count == 1 else "s")
        elif not has_signals:
            sync_state = "No Signals Selected"
        elif selection_matches:
            needs_setup = self._task_rows_needing_setup()
            if not needs_setup:
                sync_state = "Synced To Task"
            elif needs_setup == 1:
                sync_state = "Synced · 1 Knob Needs Setup"
            else:
                sync_state = f"Synced · {needs_setup} Knobs Need Setup"
        else:
            sync_state = "Selection Changed · Sync Needed"

        can_sync = (
            has_signals
            and not selection_matches
            and not errors
            and online_task
            and not active_run
        )
        if apply_button is not None:
            apply_button.setEnabled(can_sync)
            if not has_signals:
                apply_button.setToolTip("Select one or more machine signals first.")
            elif errors:
                apply_button.setToolTip("Review Mapping issues before syncing to Task Builder.")
            elif not online_task:
                apply_button.setToolTip("Switch to an Online EPICS task before syncing.")
            elif selection_matches:
                apply_button.setToolTip("The selected signal rows already match Task Builder.")
            else:
                apply_button.setToolTip(
                    "Update Task Builder to match the selected knob, objective and constraint signals."
                )

        if self._loaded_pv_library is None:
            source_label.setText("Library: none")
            summary_label.setText(
                f"Mapping {len(mapped_knobs)} knob · {len(mapped_objectives)} objective · "
                f"{len(mapped_constraints)} constraint | {sync_state}"
            )
            summary_label.setToolTip("No PV library is currently loaded.")
            return

        source_label.setText(f"Library: {self._loaded_pv_library.source}")
        summary_label.setText(
            f"{self._loaded_pv_library.machine} · "
            f"Mapping {len(mapped_knobs)} knob · {len(mapped_objectives)} objective · {len(mapped_constraints)} constraint"
            f" | {sync_state}"
        )
        summary_label.setToolTip(
            f"Library: {self._loaded_pv_library.source}\n"
            f"Available: {len(self._loaded_pv_library.knobs)} knob, "
            f"{len(self._loaded_pv_library.objectives)} objective"
        )

    def load_external_pv_library(self) -> bool:
        path, _ = QFileDialog.getOpenFileName(
            self.window,
            "Load Machine PV Library",
            str(self._default_config_directory()),
            "PV libraries (*.json *.yaml *.yml)",
        )
        if not path:
            return False
        try:
            document = load_pv_library_file(path)
        except Exception as exc:
            QMessageBox.critical(self.window, "Load Machine PV Library", str(exc))
            self.view.log_warning(f"Failed to load PV library {Path(path).name}: {exc}")
            return False

        self._loaded_pv_library = document
        self.refresh_selected_library_tables()
        self.view.log_console(f"Loaded machine PV library from {path}.")
        self.view.append_overview_activity("Machine", status=f"Loaded PV library {Path(path).name}.")
        self.view.status_message(f"Loaded PV library: {Path(path).name}", 4000)
        return True

    def open_pv_mapping_dialog(self) -> None:
        if self._loaded_pv_library is None and not self.load_external_pv_library():
            return
        if self._loaded_pv_library is None:
            return

        dialog = PVMappingSelectorDialog(
            knob_entries=list(self._loaded_pv_library.knobs),
            objective_entries=list(self._loaded_pv_library.objectives),
            constraint_entries=list(self._loaded_pv_library.objectives),
            current_keys=self._current_mapping_keys_by_role(),
            source_label=str(self._loaded_pv_library.source),
            parent=self.window,
        )
        if dialog.exec_() != dialog.Accepted:
            return

        selected = dialog.selected_entries_by_role()
        self._rewrite_mapping_rows(
            knob_rows=self._entries_to_mapping_rows(selected["knob"], role="knob"),
            objective_rows=self._entries_to_mapping_rows(selected["objective"], role="objective"),
            constraint_rows=self._entries_to_mapping_rows(selected["constraint"], role="constraint"),
        )
        self.view.log_console(
            "Updated PV Mapping from selector: "
            f"{len(selected['knob'])} knob(s), "
            f"{len(selected['objective'])} objective(s), "
            f"{len(selected['constraint'])} constraint(s)."
        )
        self.view.append_overview_activity(
            "Machine",
            status=(
                f"Selected {len(selected['knob'])} knob, "
                f"{len(selected['objective'])} objective, "
                f"{len(selected['constraint'])} constraint PV row(s)."
            ),
        )

    def _current_mapping_keys_by_role(self) -> dict[str, set[str]]:
        keys = {"knob": set(), "objective": set(), "constraint": set()}
        for row in TaskService.table_to_records(self.window.machine_ui.tableWidget_mapping):
            role = self._normalize_mapping_role(self._mapping_row_value(row, "Role"))
            if role not in keys:
                continue
            for field in ("Name", "PV Name"):
                value = self._mapping_row_value(row, field).lower()
                if value:
                    keys[role].add(value)
        return keys

    def _open_library_dialog(
        self,
        entries: list[PVLibraryItem],
        *,
        title: str,
        intro_text: str,
    ) -> list[PVLibraryItem] | None:
        if self._loaded_pv_library is None:
            QMessageBox.information(
                self.window,
                title,
                "Load a machine PV library file first.",
            )
            return None
        if not entries:
            QMessageBox.information(
                self.window,
                title,
                "The loaded machine library does not contain entries of this type.",
            )
            return None

        dialog = PVLibrarySelectorDialog(
            entries,
            title=title,
            intro_text=intro_text,
            parent=self.window,
        )
        if dialog.exec_() != dialog.Accepted:
            return None
        return dialog.selected_entries()

    @staticmethod
    def _entries_to_mapping_rows(entries: list[PVLibraryItem], *, role: str) -> list[dict[str, str]]:
        normalized_role = role if role in {"knob", "objective", "constraint"} else "objective"
        default_group = MachineController._default_group_for_role(normalized_role)
        return [
            {
                "Role": normalized_role,
                "Name": entry.name,
                "PV Name": entry.pv_name,
                "Readback": entry.readback,
                "Group": entry.group or default_group,
                "Note": entry.note,
            }
            for entry in entries
        ]

    def _rewrite_mapping_rows(
        self,
        *,
        knob_rows: list[dict[str, str]] | None = None,
        objective_rows: list[dict[str, str]] | None = None,
        constraint_rows: list[dict[str, str]] | None = None,
    ) -> None:
        current_rows = TaskService.table_to_records(self.window.machine_ui.tableWidget_mapping)
        existing_knob_rows: list[dict[str, str]] = []
        existing_objective_rows: list[dict[str, str]] = []
        existing_constraint_rows: list[dict[str, str]] = []
        other_rows: list[dict[str, str]] = []
        for row in current_rows:
            role = self._normalize_mapping_role(self._mapping_row_value(row, "Role"))
            normalized = {
                "Role": role or self._mapping_row_value(row, "Role"),
                "Name": self._mapping_row_value(row, "Name"),
                "PV Name": self._mapping_row_value(row, "PV Name"),
                "Readback": self._mapping_row_value(
                    row,
                    "Readback",
                    default=self._mapping_row_value(row, "PV Name"),
                ),
                "Group": self._mapping_row_value(
                    row,
                    "Group",
                    default=self._default_group_for_role(role),
                ),
                "Note": self._mapping_row_value(row, "Note"),
            }
            if role == "knob":
                existing_knob_rows.append(normalized)
            elif role == "objective":
                existing_objective_rows.append(normalized)
            elif role == "constraint":
                existing_constraint_rows.append(normalized)
            else:
                other_rows.append(normalized)

        desired_rows = [
            *(knob_rows if knob_rows is not None else existing_knob_rows),
            *(objective_rows if objective_rows is not None else existing_objective_rows),
            *(constraint_rows if constraint_rows is not None else existing_constraint_rows),
            *other_rows,
        ]

        table = self.window.machine_ui.tableWidget_mapping
        old_state = table.blockSignals(True)
        try:
            self.window._fill_table_from_records(table, desired_rows)
        finally:
            table.blockSignals(old_state)

        self.refresh_selected_library_tables()
        self.view.refresh_task_preview()

    def open_knob_library_dialog(self) -> None:
        if self._loaded_pv_library is None:
            self._open_library_dialog([], title="Select Knobs From Library", intro_text="")
            return
        selected = self._open_library_dialog(
            list(self._loaded_pv_library.knobs),
            title="Select Knobs From Library",
            intro_text="Choose one or more knob PVs from the loaded machine library.",
        )
        if selected is None:
            return
        self._rewrite_mapping_rows(
            knob_rows=self._entries_to_mapping_rows(selected, role="knob"),
        )
        self.view.log_console(f"Loaded {len(selected)} knob PV row(s) into PV Mapping.")
        self.view.append_overview_activity("Machine", status=f"Mapped {len(selected)} knob PV row(s) from library.")

    def open_objective_library_dialog(self) -> None:
        if self._loaded_pv_library is None:
            self._open_library_dialog([], title="Select Objectives From Library", intro_text="")
            return
        selected = self._open_library_dialog(
            list(self._loaded_pv_library.objectives),
            title="Select Objectives From Library",
            intro_text="Choose one or more objective PVs from the loaded machine library.",
        )
        if selected is None:
            return
        self._rewrite_mapping_rows(
            objective_rows=self._entries_to_mapping_rows(selected, role="objective"),
        )
        self.view.log_console(f"Loaded {len(selected)} objective PV row(s) into PV Mapping.")
        self.view.append_overview_activity(
            "Machine",
            status=f"Mapped {len(selected)} objective PV row(s) from library.",
        )

    def open_constraint_library_dialog(self) -> None:
        if self._loaded_pv_library is None:
            self._open_library_dialog([], title="Select Constraints From Objectives", intro_text="")
            return
        selected = self._open_library_dialog(
            list(self._loaded_pv_library.objectives),
            title="Select Constraints From Objectives",
            intro_text="Choose one or more objective/diagnostic PVs to use as output constraints.",
        )
        if selected is None:
            return
        self._rewrite_mapping_rows(
            constraint_rows=self._entries_to_mapping_rows(selected, role="constraint"),
        )
        self.view.log_console(f"Loaded {len(selected)} constraint PV row(s) into PV Mapping.")
        self.view.append_overview_activity(
            "Machine",
            status=f"Mapped {len(selected)} constraint PV row(s) from objective library.",
        )

    def clear_selected_knobs(self) -> None:
        self._rewrite_mapping_rows(knob_rows=[])
        self.view.log_console("Cleared knob rows from PV Mapping.")

    def clear_selected_objectives(self) -> None:
        self._rewrite_mapping_rows(objective_rows=[])
        self.view.log_console("Cleared objective rows from PV Mapping.")

    def clear_selected_constraints(self) -> None:
        self._rewrite_mapping_rows(constraint_rows=[])
        self.view.log_console("Cleared constraint rows from PV Mapping.")

    def _align_task_builder_rows_to_mapping(
        self,
        mapped_knobs: list[PVLibraryItem],
        mapped_objectives: list[PVLibraryItem],
        mapped_constraints: list[PVLibraryItem],
    ) -> tuple[dict[str, list[dict[str, str]]], dict[str, int]]:
        table_specs = {
            "variables": (
                self.window.task_ui.tableWidget_variables,
                mapped_knobs,
                {
                    "Enable": "Y",
                    "Name": "",
                    "Lower": "",
                    "Upper": "",
                    "Initial": "",
                    "Group": "main",
                },
            ),
            "objectives": (
                self.window.task_ui.tableWidget_objectives,
                mapped_objectives,
                {
                    "Enable": "Y",
                    "Name": "",
                    "Direction": "maximize",
                    "Weight": "1.0",
                    "Samples": "1",
                    "Math": "mean",
                },
            ),
            "constraints": (
                self.window.task_ui.tableWidget_constraints,
                mapped_constraints,
                {
                    "Enable": "Y",
                    "Name": "",
                    "Lower": "",
                    "Upper": "",
                    "Math": "mean",
                },
            ),
        }
        merged: dict[str, list[dict[str, str]]] = {}
        counts = {"added": 0, "preserved": 0, "removed": 0}

        for field, (table, entries, defaults) in table_specs.items():
            existing_rows = self._table_records(table)
            existing_by_name: dict[str, dict[str, str]] = {}
            for row in existing_rows:
                name = str(row.get("Name", "")).strip()
                if not name:
                    continue
                if name in existing_by_name:
                    raise ValueError(f"Task Builder contains duplicate {field} name {name!r}.")
                existing_by_name[name] = row

            selected_names: set[str] = set()
            desired_rows: list[dict[str, str]] = []
            for entry in entries:
                name = str(entry.name).strip()
                if not name:
                    raise ValueError(f"A mapped {field} row has no name.")
                selected_names.add(name)
                existing = existing_by_name.get(name)
                if existing is None:
                    record = copy.deepcopy(defaults)
                    counts["added"] += 1
                else:
                    record = copy.deepcopy(existing)
                    counts["preserved"] += 1
                record["Enable"] = "Y"
                record["Name"] = name
                if field == "variables" and not str(record.get("Group", "")).strip():
                    record["Group"] = entry.group or "main"
                desired_rows.append(record)

            for row in existing_rows:
                name = str(row.get("Name", "")).strip()
                if not name or name in selected_names:
                    continue
                counts["removed"] += 1
            merged[field] = desired_rows

        return merged, counts

    def _first_task_setup_target(self) -> tuple[int, object, int] | None:
        task = self.view.current_task()
        variable_rows = TaskService.table_to_records(self.window.task_ui.tableWidget_variables)
        for row_index, row in enumerate(variable_rows):
            if not TaskService._is_enabled(row.get("Enable", "")):
                continue
            try:
                lower = float(row.get("Lower", ""))
                upper = float(row.get("Upper", ""))
                initial = float(row.get("Initial", ""))
            except (TypeError, ValueError):
                return 0, self.window.task_ui.tableWidget_variables, row_index
            if lower >= upper or not lower <= initial <= upper:
                return 0, self.window.task_ui.tableWidget_variables, row_index

        objective_rows = TaskService.table_to_records(self.window.task_ui.tableWidget_objectives)
        for row_index, row in enumerate(objective_rows):
            if not TaskService._is_enabled(row.get("Enable", "")):
                continue
            try:
                weight = float(row.get("Weight", ""))
                samples = int(float(row.get("Samples", "")))
            except (TypeError, ValueError):
                return 1, self.window.task_ui.tableWidget_objectives, row_index
            direction = str(row.get("Direction", "")).strip().lower()
            math_op = str(row.get("Math", "")).strip().lower()
            if direction not in {"maximize", "minimize"} or samples < 1 or not np.isfinite(weight):
                return 1, self.window.task_ui.tableWidget_objectives, row_index
            if math_op not in {"mean", "std"}:
                return 1, self.window.task_ui.tableWidget_objectives, row_index

        algorithm = TaskService._optimizer_name_from_gui(task.get("algorithm", "BO"))
        if algorithm in {"consbo", "consmobo", "consmggpo", "consmggpo_so"}:
            constraint_rows = TaskService.table_to_records(self.window.task_ui.tableWidget_constraints)
            for row_index, row in enumerate(constraint_rows):
                if not TaskService._is_enabled(row.get("Enable", "")):
                    continue
                try:
                    TaskService._constraint_bounds_from_rows([row])
                except Exception:
                    return 2, self.window.task_ui.tableWidget_constraints, row_index
        return None

    def _first_added_task_target(
        self,
        snapshot: dict[str, list[dict[str, str]]],
    ) -> tuple[int, object, int] | None:
        specs = (
            ("variables", self.window.task_ui.tableWidget_variables),
            ("objectives", self.window.task_ui.tableWidget_objectives),
            ("constraints", self.window.task_ui.tableWidget_constraints),
        )
        for tab_index, (field, table) in enumerate(specs):
            previous_names = {
                str(row.get("Name", "")).strip()
                for row in snapshot[field]
                if str(row.get("Name", "")).strip()
            }
            for row_index, row in enumerate(TaskService.table_to_records(table)):
                name = str(row.get("Name", "")).strip()
                if name and name not in previous_names:
                    return tab_index, table, row_index
        return None

    def _focus_task_builder_target(self, target: tuple[int, object, int]) -> None:
        tab_index, table, row_index = target
        self.view.go_to_page(self.window.PAGE_TASK_BUILDER)
        self.window.task_ui.tabWidget_tables.setCurrentIndex(tab_index)
        table.selectRow(row_index)
        table.setCurrentCell(row_index, 1)
        name_item = table.item(row_index, 1)
        if name_item is not None:
            table.scrollToItem(name_item)
        table.setFocus()

    @staticmethod
    def _mapping_sync_preview(
        snapshot: dict[str, list[dict[str, str]]],
        merged: dict[str, list[dict[str, str]]],
        counts: dict[str, int],
    ) -> str:
        labels = {
            "variables": "Knobs",
            "objectives": "Objectives",
            "constraints": "Constraints",
        }
        lines = [
            "Review the Task Builder changes before applying:",
            "",
            f"{counts['added']} added · {counts['preserved']} preserved · "
            f"{counts['removed']} removed",
        ]
        for field in ("variables", "objectives", "constraints"):
            before = [
                str(row.get("Name", "")).strip()
                for row in snapshot[field]
                if str(row.get("Name", "")).strip()
            ]
            after = [
                str(row.get("Name", "")).strip()
                for row in merged[field]
                if str(row.get("Name", "")).strip()
            ]
            added = [name for name in after if name not in before]
            removed = [name for name in before if name not in after]
            details = []
            if added:
                details.append("add " + ", ".join(added))
            if removed:
                details.append("remove " + ", ".join(removed))
            if not details:
                details.append("no name changes")
            lines.append(f"{labels[field]}: {'; '.join(details)}")
        lines.extend(
            [
                "",
                "Existing bounds, initial values, directions, sampling and math "
                "settings are preserved for matching names.",
            ]
        )
        return "\n".join(lines)

    def apply_selected_pv_library_entries(self) -> None:
        task = self.view.current_task()
        if not self.is_online_task(task):
            QMessageBox.information(
                self.window,
                "Sync PV Mapping To Task",
                "Switch the task to Online EPICS before syncing PV Mapping into Task Builder.",
            )
            return
        mapped_knobs = self._mapping_items_for_role("knob")
        mapped_objectives = self._mapping_items_for_role("objective")
        mapped_constraints = self._mapping_items_for_role("constraint")
        errors = self._mapping_sync_errors()
        if errors:
            QMessageBox.warning(self.window, "Sync PV Mapping To Task", "\n".join(errors))
            self.view.log_warning("PV Mapping sync blocked: " + "; ".join(errors))
            return
        if not mapped_knobs and not mapped_objectives and not mapped_constraints:
            QMessageBox.information(
                self.window,
                "Sync PV Mapping To Task",
                "Add at least one knob, objective, or constraint row to PV Mapping first.",
            )
            return

        tables = {
            "variables": self.window.task_ui.tableWidget_variables,
            "objectives": self.window.task_ui.tableWidget_objectives,
            "constraints": self.window.task_ui.tableWidget_constraints,
        }
        snapshot = {
            field: copy.deepcopy(self._table_records(table))
            for field, table in tables.items()
        }
        try:
            merged, counts = self._align_task_builder_rows_to_mapping(
                mapped_knobs,
                mapped_objectives,
                mapped_constraints,
            )
            preview = self._mapping_sync_preview(snapshot, merged, counts)
            answer = QMessageBox.question(
                self.window,
                "Confirm Sync To Task",
                preview,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                self.view.status_message("Sync To Task cancelled.", 3000)
                return
            for field, table in tables.items():
                self.window.task_builder_controller.fill_table_from_records(
                    table,
                    merged[field],
                )
        except Exception as exc:
            for field, table in tables.items():
                self.window.task_builder_controller.fill_table_from_records(
                    table,
                    snapshot[field],
                )
            QMessageBox.critical(self.window, "Sync PV Mapping To Task", str(exc))
            self.view.log_warning(f"PV Mapping sync failed: {exc}")
            return

        self._last_sync_snapshot = snapshot
        self.window.machine_ui.pushButton_undoMappingSync.setEnabled(True)
        summary = (
            f"Mapping synchronized: {counts['added']} added, "
            f"{counts['preserved']} preserved, {counts['removed']} removed."
        )
        self.view.log_console(summary)
        self.view.refresh_task_preview()
        self.view.append_overview_activity(
            "Machine",
            status=summary,
        )
        self.refresh_selected_library_tables()
        target = self._first_task_setup_target()
        if target is None and counts["added"]:
            target = self._first_added_task_target(snapshot)
        if target is not None:
            self._focus_task_builder_target(target)
        self.view.status_message(summary, 5000)

    def undo_last_mapping_sync(self) -> None:
        if self._last_sync_snapshot is None:
            return
        tables = {
            "variables": self.window.task_ui.tableWidget_variables,
            "objectives": self.window.task_ui.tableWidget_objectives,
            "constraints": self.window.task_ui.tableWidget_constraints,
        }
        snapshot = self._last_sync_snapshot
        for field, table in tables.items():
            self.window.task_builder_controller.fill_table_from_records(
                table,
                copy.deepcopy(snapshot[field]),
            )
        self._last_sync_snapshot = None
        self.window.machine_ui.pushButton_undoMappingSync.setEnabled(False)
        self.view.refresh_task_preview()
        self.view.log_console("Undid the most recent PV Mapping sync.")
        self.view.status_message("PV Mapping sync undone.", 4000)

    def set_machine_status(self, text: str) -> None:
        self.window.machine_ui.label_statusValue.setText(text)
        self.window.ui.label_statusConnectionValue.setText(text)
        self.refresh_machine_summary()
        self.view.refresh_overview_readiness()

    def refresh_machine_summary(self) -> None:
        if not hasattr(self.window.machine_ui, "label_machineSummary"):
            return
        if hasattr(self.window.machine_ui, "frame_machineProfile"):
            self.window.machine_ui.frame_machineProfile.setVisible(
                self.is_online_task(self.view.current_task())
            )
        write_policy = self.window.machine_ui.comboBox_policy.currentText().strip()
        bindings = getattr(self.window.machine_ui, "policy_bindings", [])
        enabled_objective_policies = [
            binding
            for binding in bindings
            if binding.get("kind") == "objective"
            and bool(binding.get("enabled", True))
        ]
        enabled_constraint_policies = [
            binding
            for binding in bindings
            if binding.get("kind") == "constraint"
            and bool(binding.get("enabled", True))
        ]
        objective_policy_summary = (
            f"{len(enabled_objective_policies)} enabled"
            if enabled_objective_policies
            else "none"
        )
        constraint_policy_summary = (
            f"{len(enabled_constraint_policies)} enabled"
            if enabled_constraint_policies
            else "none"
        )
        restore = "restore-on-abort on" if self.window.machine_ui.checkBox_restore.isChecked() else "restore-on-abort off"
        readback = (
            f"readback on (tol {self.window.machine_ui.doubleSpinBox_readbackTol.value():g})"
            if self.window.machine_ui.checkBox_readbackCheck.isChecked()
            else "readback off"
        )
        set_interval = f"set {self.window.machine_ui.doubleSpinBox_setInterval.value():g}s"
        sample_interval = f"sample {self.window.machine_ui.doubleSpinBox_sampleInterval.value():g}s"
        status = self.window.machine_ui.label_statusValue.text().strip() or "Disconnected"
        self.window.machine_ui.label_machineSummary.setText(
            f"PV status {status} · write policy {write_policy} · objective policies {objective_policy_summary} · "
            f"constraint policies {constraint_policy_summary} · "
            f"{restore} · {readback} · {set_interval} · {sample_interval}"
        )

    def resolve_epics_read_pv(self, task: dict) -> str:
        task_cfg = TaskService.build_task_config(task)
        kwargs = task_cfg.backend.kwargs
        for field in ("knob_readback_pvnames", "knobs_pvnames", "obj_pvnames"):
            pvnames = kwargs.get(field, [])
            if pvnames:
                return str(pvnames[0])
        raise ValueError("No EPICS PV is configured for the current task.")

    def ensure_machine_ready_for_online(self, task: dict) -> bool:
        if not self.is_online_task(task):
            return True
        status = self.window.machine_ui.label_statusValue.text().strip().lower()
        current_identity = self.machine_check_identity(task)
        if (
            status == "pv check passed"
            and self.window.state.machine_check_identity == current_identity
        ):
            return True
        if self.window.machine_ui.checkBox_autoConnect.isChecked():
            return self.check_machine_pv(show_dialog=False)
        return False

    def _prepare_epics_caget(self):
        try:
            from epics import caget
        except ImportError as exc:
            raise RuntimeError(f"EPICS backend is unavailable: {exc}") from exc

        inherited_ca = os.environ.get("EPICS_CA_ADDR_LIST", "").strip()
        auto_discovery = os.environ.get("EPICS_CA_AUTO_ADDR_LIST", "").strip()
        if inherited_ca:
            self.view.log_pv(f"Using inherited EPICS CA address list: {inherited_ca}")
        elif auto_discovery:
            self.view.log_pv(
                f"Using inherited EPICS auto-discovery setting: EPICS_CA_AUTO_ADDR_LIST={auto_discovery}"
            )
        else:
            self.view.log_pv("Using EPICS defaults or network auto-discovery.")
        return caget

    def connect_machine(self) -> None:
        try:
            self._prepare_epics_caget()
        except Exception as exc:
            self.set_machine_status("Unavailable")
            self.view.log_warning(str(exc))
            QMessageBox.critical(self.window, "EPICS Unavailable", str(exc))
            return
        self.set_machine_status("Ready")
        self.view.log_console("EPICS module is available. Use Check PV to verify a configured PV.")
        self.view.append_overview_activity("Machine", status="EPICS backend available.")

    def disconnect_machine(self) -> None:
        self.set_machine_status("Disconnected")
        self.view.log_pv("Disconnected from machine backend.")
        self.view.log_console("Machine disconnected.")
        self.view.append_overview_activity("Machine", status="Disconnected.")

    def test_machine_read(self) -> None:
        self.check_machine_pv(show_dialog=True)

    def check_machine_pv(self, *, show_dialog: bool = True) -> bool:
        task = self.view.current_task()
        if not self.is_online_task(task):
            if show_dialog:
                QMessageBox.information(
                    self.window,
                    "Check PV",
                    "Current task is not an online EPICS task.",
                )
            return False

        try:
            caget = self._prepare_epics_caget()
            task_cfg = TaskService.build_task_config(task)
            kwargs = task_cfg.backend.kwargs
            pvnames: list[str] = []
            for field in (
                "knobs_pvnames",
                "knob_readback_pvnames",
                "obj_pvnames",
                "constraint_pvnames",
            ):
                pvnames.extend(str(value).strip() for value in kwargs.get(field, []) if str(value).strip())
            for _source, target in kwargs.get("write_policy_kwargs", {}).get("pvlinks", []):
                target_text = str(target).strip()
                if target_text:
                    pvnames.append(target_text)
            pvnames = list(dict.fromkeys(pvnames))
            if not pvnames:
                raise ValueError("No EPICS PV is configured for the current task.")
            self.set_machine_status("Checking")
            checked_values: list[tuple[str, object]] = []
            timeout = float(self.window.machine_ui.doubleSpinBox_timeout.value())
            for pvname in pvnames:
                value = caget(pvname, timeout=timeout)
                if value is None:
                    raise RuntimeError(f"{pvname} returned None")
                checked_values.append((pvname, value))
        except Exception as exc:
            self.set_machine_status("Failed")
            self.window.state.machine_check_identity.clear()
            self.window.state.last_test_read_status = "Failed"
            self.window.state.last_test_read_detail = f"Last PV check failed: {exc}"
            self.view.refresh_overview_readiness()
            self.view.append_overview_activity("Machine", status="PV check failed.")
            self.view.log_warning(f"EPICS PV check failed: {exc}")
            if show_dialog:
                QMessageBox.critical(self.window, "PV Check Failed", str(exc))
            return False

        self.window.state.machine_check_identity = self.machine_check_identity(task)
        self.set_machine_status("PV Check Passed")
        self.window.state.last_test_read_status = "Passed"
        preview = ", ".join(f"{pv} = {value}" for pv, value in checked_values[:3])
        if len(checked_values) > 3:
            preview += f", ... (+{len(checked_values) - 3} more)"
        self.window.state.last_test_read_detail = (
            f"Checked {len(checked_values)} required PV(s): {preview}"
        )
        self.view.refresh_overview_readiness()
        self.view.append_overview_activity(
            "Machine", status=f"PV check passed for {len(checked_values)} required PV(s)."
        )
        for pvname, value in checked_values:
            self.view.log_pv(f"PV check: {pvname} -> {value}")
        self.view.log_console("EPICS PV check completed.")
        if show_dialog:
            QMessageBox.information(
                self.window,
                "Check PV",
                f"PV read succeeded for {len(checked_values)} required PV(s).",
            )
        return True

    def read_current_knob_values(self, task: dict, variables: list[dict]) -> list[float]:
        if not self.is_online_task(task):
            raise ValueError("Current machine readback is only available for Online EPICS tasks.")
        if not variables:
            return []
        if not self.ensure_machine_ready_for_online(task):
            raise ValueError("Connect the machine before reading current knob values.")

        try:
            from epics import caget
        except ImportError as exc:
            raise RuntimeError(f"EPICS backend is unavailable: {exc}") from exc

        pvnames = TaskService._resolve_online_knob_readback_pvs(task, variables)
        timeout = float(self.window.machine_ui.doubleSpinBox_timeout.value())
        values: list[float] = []
        for row, pvname in zip(variables, pvnames):
            value = caget(pvname, timeout=timeout)
            if value is None:
                raise RuntimeError(f"{pvname} returned None")
            try:
                scalar = float(np.asarray(value, dtype=float).reshape(-1)[0])
            except Exception as exc:
                name = str(row.get("Name", pvname)).strip() or pvname
                raise RuntimeError(
                    f"{pvname} for knob {name!r} did not return a scalar numeric value."
                ) from exc
            values.append(scalar)
        return values

    def log_machine_policy_change(self, text: str) -> None:
        self.view.log_console(f"Write policy changed to: {text}")
        self.refresh_machine_summary()

    def log_objective_policy_change(self, text: str) -> None:
        self.view.log_console(f"Objective policy changed to: {text}")
        self.refresh_machine_summary()
