from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QFileDialog, QInputDialog, QMessageBox, QTreeWidgetItem

if TYPE_CHECKING:  # pragma: no cover
    from ..main_window import MainWindow

try:
    from ...services.task_service import TaskService
    from ...services.template_library import (
        clone_template_task,
        grouped_templates,
        list_templates,
        template_detail_text,
    )
    from ..tool_dialogs import PVMonitorDialog
except ImportError:  # pragma: no cover - local script fallback
    CURRENT_DIR = Path(__file__).resolve().parent
    GUI_ROOT = CURRENT_DIR.parents[1]
    for path in (GUI_ROOT, GUI_ROOT / "services", GUI_ROOT / "workers"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    from task_service import TaskService
    from template_library import clone_template_task, grouped_templates, list_templates, template_detail_text
    from tool_dialogs import PVMonitorDialog


class TemplatesController:
    def __init__(self, window: "MainWindow") -> None:
        self.window = window
        self.view = window.view_adapter

    def init_templates_page(self) -> None:
        tree = self.window.ui.treeWidget_templates
        tree.setColumnCount(1)
        tree.setHeaderLabels(["Template"])
        tree.setHeaderHidden(True)
        tree.setAlternatingRowColors(True)
        tree.setUniformRowHeights(True)
        tree.setIndentation(14)
        tree.clear()
        for category, templates in grouped_templates().items():
            root = QTreeWidgetItem([f"{category} ({len(templates)})"])
            root.setData(0, Qt.UserRole, {"kind": "category", "category": category})
            for template in templates:
                item = QTreeWidgetItem([template.title])
                item.setData(0, Qt.UserRole, {"kind": "template", "template": template})
                root.addChild(item)
            tree.addTopLevelItem(root)
        tree.expandAll()
        self.window.ui.plainTextEdit_templateDetails.setReadOnly(True)
        self.window.ui.plainTextEdit_templateDetails.setPlainText(
            "Select a template on the left to preview and apply it."
        )
        self.window.ui.label_selectedTemplateSummary.setText(
            "No template selected yet. Choose one from the list to preview its setup and apply it to the current task."
        )
        self.window.ui.pushButton_applyTemplate.setEnabled(False)
        self.window.ui.pushButton_cloneTemplate.setEnabled(False)
        self.window.ui.pushButton_exportTemplate.setEnabled(False)
        if tree.topLevelItemCount() > 0 and tree.topLevelItem(0).childCount() > 0:
            tree.setCurrentItem(tree.topLevelItem(0).child(0))
            self.update_template_details()

    def template_count(self) -> int:
        return len(list_templates())

    def selected_template_summary_text(self, template) -> str:
        if template is None:
            return (
                "No template selected yet. Choose one from the list to preview its setup "
                "and apply it to the current task."
            )
        return (
            f"{template.category} starter\n"
            f"{template.title}\n"
            f"{template.description}"
        )

    def sidebar_meta_text(self, template) -> str:
        count = self.template_count()
        if template is None:
            return f"{count} starter templates ready."
        return f"{count} starter templates. Selected: {template.title}"

    def selected_template_definition(self):
        items = self.window.ui.treeWidget_templates.selectedItems()
        if not items:
            return None
        data = items[0].data(0, Qt.UserRole) or {}
        if isinstance(data, dict) and data.get("kind") == "template":
            return data.get("template")
        return None

    def update_template_details(self) -> None:
        template = self.selected_template_definition()
        if template is None:
            self.window.ui.plainTextEdit_templateDetails.setPlainText(
                "Select a concrete template to preview it."
            )
            self.window.ui.label_selectedTemplateSummary.setText(
                self.selected_template_summary_text(None)
            )
            self.window.ui.pushButton_applyTemplate.setEnabled(False)
            self.window.ui.pushButton_cloneTemplate.setEnabled(False)
            self.window.ui.pushButton_exportTemplate.setEnabled(False)
            return
        self.window.ui.plainTextEdit_templateDetails.setPlainText(template_detail_text(template))
        self.window.ui.label_selectedTemplateSummary.setText(
            self.selected_template_summary_text(template)
        )
        self.window.ui.pushButton_applyTemplate.setEnabled(True)
        self.window.ui.pushButton_cloneTemplate.setEnabled(True)
        self.window.ui.pushButton_exportTemplate.setEnabled(True)

    def apply_selected_template(self) -> None:
        template = self.selected_template_definition()
        if template is None:
            QMessageBox.information(self.window, "Apply Template", "Please select a template first.")
            return
        self.apply_template_definition(template)

    def open_template_library(self) -> None:
        self.view.go_to_page(self.window.PAGE_TASK_BUILDER)
        self.window.template_library_dialog.show()
        self.window.template_library_dialog.raise_()
        self.window.template_library_dialog.activateWindow()

    def apply_template_definition(self, template) -> None:
        task = clone_template_task(template)
        self.view.apply_task_payload(
            task,
            source_label=f"Applied template: {template.title}",
            goto_builder=True,
        )

    def clone_template(self) -> None:
        template = self.selected_template_definition()
        if template is None:
            QMessageBox.information(self.window, "Clone Template", "Please select a template first.")
            return
        suggested = f"{template.task.get('task_name', template.key)}_copy"
        new_name, ok = QInputDialog.getText(
            self.window,
            "Clone Template",
            "Task name for the cloned template:",
            text=suggested,
        )
        if not ok or not new_name.strip():
            return
        task = clone_template_task(template, new_task_name=new_name.strip())
        self.view.apply_task_payload(
            task,
            source_label=f"Cloned template into task draft: {new_name.strip()}",
            goto_builder=True,
        )

    def export_template(self) -> None:
        template = self.selected_template_definition()
        if template is None:
            QMessageBox.information(self.window, "Export Template", "Please select a template first.")
            return
        default_name = f"{template.task.get('task_name', template.key)}_template.json"
        path, _ = QFileDialog.getSaveFileName(
            self.window,
            "Export Template Draft",
            str(Path.cwd() / default_name),
            "JSON Draft Files (*.json);;All Files (*)",
        )
        if not path:
            return
        if not path.lower().endswith(".json"):
            path = f"{path}.json"
        TaskService.export_task_json(clone_template_task(template), path)
        self.view.log_console(f"Template exported to: {path}")
        QMessageBox.information(self.window, "Export Template", f"Template draft exported to:\n{path}")

    def check_environment(self) -> None:
        self.view.refresh_overview_readiness()
        summary = (
            f"Python: {self.window.ui.label_readinessPythonValue.text()}\n"
            f"GUI: {self.window.ui.label_readinessGuiValue.text()}\n"
            f"pyepics: {self.window.ui.label_readinessEpicsValue.text()}\n"
            f"Machine: {self.window.ui.label_readinessMachineValue.text()}\n"
            f"Last Test Read: {self.window.ui.label_readinessTestReadValue.text()}"
        )
        self.view.append_overview_activity("Check", status="Refreshed run readiness.")
        self.view.log_console("Run readiness refreshed.")
        QMessageBox.information(
            self.window,
            "Run Readiness",
            summary,
        )

    def show_pv_monitor(self) -> None:
        dialog = PVMonitorDialog(
            self.view.current_task,
            timeout_provider=lambda: float(self.window.machine_ui.doubleSpinBox_timeout.value()),
            parent=self.window,
        )
        dialog.exec_()

    def show_policy_editor(self) -> None:
        self.window.ui.tabWidget_configure.setCurrentIndex(self.window.CONFIGURE_TAB_MACHINE)
        if hasattr(self.window.machine_ui, "tab_advancedMachine"):
            self.window.machine_ui.tabWidget_machine.setCurrentWidget(self.window.machine_ui.tab_advancedMachine)
            self.window.machine_ui.tabWidget_machineAdvanced.setCurrentWidget(self.window.machine_ui.tab_objectivePolicy)
            location = "Machine Setup -> Advanced -> Objective Policy"
        else:
            self.window.machine_ui.tabWidget_machine.setCurrentWidget(self.window.machine_ui.tab_objectivePolicy)
            location = "Machine Setup -> Objective Policy"
        QMessageBox.information(
            self.window,
            "Policy Editor",
            f"Objective policies are now edited directly in {location}.",
        )
        self.view.go_to_page(self.window.PAGE_MACHINE)
        self.view.log_console(f"Opened {location}.")
