from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from PyQt5.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:  # pragma: no cover
    from ..main_window import MainWindow

try:
    from ...services.pv_library import PVLibraryDocument, PVLibraryItem, load_pv_library_file
    from ...services.task_service import TaskService
    from ..tool_dialogs import PVLibrarySelectorDialog, PVMappingSelectorDialog
except ImportError:  # pragma: no cover - local script fallback
    CURRENT_DIR = Path(__file__).resolve().parent
    GUI_ROOT = CURRENT_DIR.parents[1]
    for path in (GUI_ROOT, GUI_ROOT / "services", GUI_ROOT / "views"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    from pv_library import PVLibraryDocument, PVLibraryItem, load_pv_library_file
    from task_service import TaskService
    from tool_dialogs import PVLibrarySelectorDialog, PVMappingSelectorDialog


class MachineController:
    def __init__(self, window: "MainWindow") -> None:
        self.window = window
        self.view = window.view_adapter
        self._loaded_pv_library: PVLibraryDocument | None = None

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

        self._configure_simple_connection_panel()
        self._configure_pv_mapping_actions()
        self._move_advanced_machine_controls()

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

        ui.verticalLayout_connectionBox.removeItem(ui.formLayout_connection)
        connection_row = QHBoxLayout()
        connection_row.setContentsMargins(0, 0, 0, 0)
        connection_row.setSpacing(8)
        ui.label_status.setParent(ui.groupBox_connection)
        ui.label_statusValue.setParent(ui.groupBox_connection)
        ui.pushButton_test.setParent(ui.groupBox_connection)
        connection_row.addWidget(ui.label_status)
        connection_row.addWidget(ui.label_statusValue)
        connection_row.addStretch(1)
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
        ui.pushButton_applySelectedPvLibrary.setToolTip("Sync the PV Mapping table into Task Builder.")
        ui.horizontalLayout_pvLibraryControls.removeWidget(ui.pushButton_applySelectedPvLibrary)
        ui.horizontalLayout_pvLibraryControls.insertWidget(1, ui.pushButton_applySelectedPvLibrary)
        ui.horizontalLayout_pvLibraryControls.setContentsMargins(0, 0, 0, 0)
        ui.horizontalLayout_pvLibraryControls.setSpacing(6)
        ui.verticalLayout_pvPresetLibrary.setContentsMargins(8, 4, 8, 4)
        ui.verticalLayout_pvPresetLibrary.setSpacing(4)
        ui.frame_pvPresetLibrary.setMaximumHeight(34)
        for button in (ui.pushButton_selectPvs, ui.pushButton_applySelectedPvLibrary):
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
            ui.label_pvLibrarySummary,
            ui.frame_selectedLibrarySummary,
        ):
            widget.setVisible(False)

    def _move_advanced_machine_controls(self) -> None:
        ui = self.window.machine_ui
        main_tabs = ui.tabWidget_machine

        for page in (ui.tab_writePolicy, ui.tab_objectivePolicy, ui.tab_constraintPolicy):
            index = main_tabs.indexOf(page)
            if index >= 0:
                main_tabs.removeTab(index)

        ui.groupBox_guard.setParent(None)

        advanced_page = QWidget(main_tabs)
        advanced_page.setObjectName("tab_advancedMachine")
        advanced_layout = QVBoxLayout(advanced_page)
        advanced_tabs = QTabWidget(advanced_page)
        advanced_tabs.setObjectName("tabWidget_machineAdvanced")
        advanced_layout.addWidget(advanced_tabs)

        safeguards_page = QWidget(advanced_tabs)
        safeguards_page.setObjectName("tab_safeguardsAdvanced")
        safeguards_layout = QVBoxLayout(safeguards_page)
        safeguards_layout.addWidget(ui.groupBox_guard)
        ui.groupBox_guard.show()

        advanced_tabs.addTab(safeguards_page, "Safeguards")
        advanced_tabs.addTab(ui.tab_writePolicy, "Write Links")
        advanced_tabs.addTab(ui.tab_objectivePolicy, "Objective Policy")
        advanced_tabs.addTab(ui.tab_constraintPolicy, "Constraint Policy")
        main_tabs.addTab(advanced_page, "Advanced")
        main_tabs.setCurrentWidget(ui.tab_mapping)

        ui.tab_advancedMachine = advanced_page
        ui.tabWidget_machineAdvanced = advanced_tabs
        ui.tab_safeguardsAdvanced = safeguards_page

    @staticmethod
    def _mapping_row_value(row: dict, key: str, default: str = "") -> str:
        return str(row.get(key, default)).strip()

    def is_online_task(self, task: dict | None = None) -> bool:
        current = task if task is not None else self.view.current_task()
        return str(current.get("mode", "")).strip().lower() == "online epics"

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

    def _mapping_matches_task_builder(self) -> bool:
        task = self.view.current_task()
        if not self.is_online_task(task):
            return False

        variables, objectives = self._enabled_task_rows(task)
        constraints = TaskService._enabled_rows(task.get("constraints", []))
        mapped_knobs = self._mapping_items_for_role("knob")
        mapped_objectives = self._mapping_items_for_role("objective")
        mapped_constraints = self._mapping_items_for_role("constraint")

        if len(variables) != len(mapped_knobs):
            return False
        if len(objectives) != len(mapped_objectives):
            return False
        if len(constraints) != len(mapped_constraints):
            return False

        for index, entry in enumerate(mapped_knobs):
            variable_name = str(variables[index].get("Name", "")).strip()
            if variable_name != entry.name:
                return False

        for index, entry in enumerate(mapped_objectives):
            objective_name = str(objectives[index].get("Name", "")).strip()
            if objective_name != entry.name:
                return False

        for index, entry in enumerate(mapped_constraints):
            constraint_name = str(constraints[index].get("Name", "")).strip()
            if constraint_name != entry.name:
                return False

        return True

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

        variables, objectives = self._enabled_task_rows()
        constraints = TaskService._enabled_rows(self.view.current_task().get("constraints", []))
        mapped_knobs = self._mapping_items_for_role("knob")
        mapped_objectives = self._mapping_items_for_role("objective")
        mapped_constraints = self._mapping_items_for_role("constraint")
        sync_state = "Synced" if self._mapping_matches_task_builder() else "Not synced"

        if self._loaded_pv_library is None:
            source_label.setText("Library: none")
            summary_label.setText(
                f"Mapping: {len(mapped_knobs)} knob, {len(mapped_objectives)} objective, {len(mapped_constraints)} constraint"
                f" | Task: {len(variables)} knob, {len(objectives)} objective, {len(constraints)} constraint"
                f" | {sync_state}"
            )
            if apply_button is not None:
                apply_button.setEnabled(bool(mapped_knobs or mapped_objectives or mapped_constraints))
            return

        source_label.setText(f"Library: {self._loaded_pv_library.source}")
        summary_label.setText(
            f"{self._loaded_pv_library.machine}"
            f" | Library: {len(self._loaded_pv_library.knobs)} knob, {len(self._loaded_pv_library.objectives)} objective"
            f" | Mapping: {len(mapped_knobs)} knob, {len(mapped_objectives)} objective, {len(mapped_constraints)} constraint"
            f" | Task: {len(variables)} knob, {len(objectives)} objective, {len(constraints)} constraint"
            f" | {sync_state}"
        )
        if apply_button is not None:
            apply_button.setEnabled(bool(mapped_knobs or mapped_objectives or mapped_constraints))

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
    ) -> None:
        task_builder = self.window.task_builder_controller
        variable_table = self.window.task_ui.tableWidget_variables
        objective_table = self.window.task_ui.tableWidget_objectives
        constraint_table = self.window.task_ui.tableWidget_constraints

        existing_variable_rows = self._table_records(variable_table)
        variable_records: list[dict[str, str]] = []
        for index, entry in enumerate(mapped_knobs):
            existing = existing_variable_rows[index] if index < len(existing_variable_rows) else {}
            variable_records.append(
                {
                    "Enable": "Y",
                    "Name": str(entry.name).strip() or f"x{index}",
                    "Lower": self._coalesce(str(existing.get("Lower", "")), default="-1.0"),
                    "Upper": self._coalesce(str(existing.get("Upper", "")), default="1.0"),
                    "Initial": self._coalesce(str(existing.get("Initial", "")), default="0.0"),
                    "Group": self._coalesce(entry.group, str(existing.get("Group", "")), default="main"),
                }
            )
        task_builder.fill_table_from_records(variable_table, variable_records)

        existing_objective_rows = self._table_records(objective_table)
        objective_records: list[dict[str, str]] = []
        for index, entry in enumerate(mapped_objectives):
            existing = existing_objective_rows[index] if index < len(existing_objective_rows) else {}
            objective_records.append(
                {
                    "Enable": "Y",
                    "Name": str(entry.name).strip() or f"obj{index}",
                    "Direction": self._coalesce(str(existing.get("Direction", "")), default="maximize"),
                    "Weight": self._coalesce(str(existing.get("Weight", "")), default="1.0"),
                    "Samples": self._coalesce(str(existing.get("Samples", "")), default="1"),
                    "Math": self._coalesce(str(existing.get("Math", "")), default="mean"),
                }
            )
        task_builder.fill_table_from_records(objective_table, objective_records)

        existing_constraint_rows = self._table_records(constraint_table)
        existing_constraints_by_name = {
            str(row.get("Name", "")).strip(): row
            for row in existing_constraint_rows
            if str(row.get("Name", "")).strip()
        }
        constraint_records: list[dict[str, str]] = []
        for index, entry in enumerate(mapped_constraints):
            name = str(entry.name).strip() or f"cons{index}"
            existing = existing_constraints_by_name.get(name, {})
            constraint_records.append(
                {
                    "Enable": "Y",
                    "Name": name,
                    "Lower": self._coalesce(str(existing.get("Lower", "")), default=""),
                    "Upper": self._coalesce(str(existing.get("Upper", "")), default=""),
                    "Math": self._coalesce(str(existing.get("Math", "")), default="mean"),
                }
            )
        task_builder.fill_table_from_records(constraint_table, constraint_records)

        self.view.log_console(
            f"Synced Task Builder from PV Mapping: {len(mapped_knobs)} knob(s), "
            f"{len(mapped_objectives)} objective(s), {len(mapped_constraints)} constraint(s)."
        )
        self.view.refresh_task_preview()

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
        if not mapped_knobs and not mapped_objectives and not mapped_constraints:
            QMessageBox.information(
                self.window,
                "Sync PV Mapping To Task",
                "Add at least one knob, objective, or constraint row to PV Mapping first.",
            )
            return

        self._align_task_builder_rows_to_mapping(
            mapped_knobs,
            mapped_objectives,
            mapped_constraints,
        )
        self.view.append_overview_activity(
            "Machine",
            status=(
                f"Synced {len(mapped_knobs)} knob, {len(mapped_objectives)} objective, "
                f"and {len(mapped_constraints)} constraint mapping row(s) to Task Builder."
            ),
        )
        self.refresh_selected_library_tables()

    def set_machine_status(self, text: str) -> None:
        self.window.machine_ui.label_statusValue.setText(text)
        self.window.ui.label_statusConnectionValue.setText(text)
        self.refresh_machine_summary()
        self.view.refresh_overview_readiness()

    def refresh_machine_summary(self) -> None:
        if not hasattr(self.window.machine_ui, "label_machineSummary"):
            return
        write_policy = self.window.machine_ui.comboBox_policy.currentText().strip()
        objective_policy_rows = TaskService.table_to_records(self.window.machine_ui.tableWidget_objectivePolicies)
        constraint_policy_rows = TaskService.table_to_records(self.window.machine_ui.tableWidget_constraintPolicies)
        enabled_objective_policies = [
            row
            for row in objective_policy_rows
            if TaskService._is_enabled(row.get("Enabled", ""))
            and str(row.get("Policy Name", "")).strip()
        ]
        enabled_constraint_policies = [
            row
            for row in constraint_policy_rows
            if TaskService._is_enabled(row.get("Enabled", ""))
            and str(row.get("Policy Name", "")).strip()
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
        if status in {"ready", "connected"}:
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
            pvname = self.resolve_epics_read_pv(task)
            self.set_machine_status("Checking")
            value = caget(
                pvname,
                timeout=float(self.window.machine_ui.doubleSpinBox_timeout.value()),
            )
            if value is None:
                raise RuntimeError(f"{pvname} returned None")
        except Exception as exc:
            self.set_machine_status("Failed")
            self.window.state.last_test_read_status = "Failed"
            self.window.state.last_test_read_detail = f"Last PV check failed: {exc}"
            self.view.refresh_overview_readiness()
            self.view.append_overview_activity("Machine", status="PV check failed.")
            self.view.log_warning(f"EPICS PV check failed: {exc}")
            if show_dialog:
                QMessageBox.critical(self.window, "PV Check Failed", str(exc))
            return False

        self.set_machine_status("Connected")
        self.window.state.last_test_read_status = "Passed"
        self.window.state.last_test_read_detail = f"{pvname} = {value}"
        self.view.refresh_overview_readiness()
        self.view.append_overview_activity("Machine", status=f"PV check passed for {pvname}.")
        self.view.log_pv(f"PV check: {pvname} -> {value}")
        self.view.log_console("EPICS PV check completed.")
        if show_dialog:
            QMessageBox.information(self.window, "Check PV", f"PV read succeeded:\n{pvname} = {value}")
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
