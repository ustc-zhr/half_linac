import pytest


def test_secondary_dialog_buttons_use_compact_height(monkeypatch):
    pytest.importorskip("PyQt5")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from PyQt5.QtWidgets import (
        QApplication,
        QDialog,
        QDialogButtonBox,
        QMessageBox,
        QPushButton,
        QVBoxLayout,
    )

    import gotacc.gui.main  # noqa: F401 - configures Qt runtime paths
    from gotacc.gui.theme import apply_theme

    app = QApplication.instance() or QApplication([])
    apply_theme(app, "dark")
    dialog = QDialog()
    layout = QVBoxLayout(dialog)
    action = QPushButton("Action", dialog)
    inline_action = QPushButton("Inline Action", dialog)
    inline_action.setProperty("inlineAction", True)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dialog)
    layout.addWidget(action)
    layout.addWidget(inline_action)
    layout.addWidget(buttons)
    dialog.show()
    message = QMessageBox(
        QMessageBox.Information,
        "Status",
        "Message",
        QMessageBox.Ok | QMessageBox.Cancel,
    )
    message.show()
    app.processEvents()

    dialog_buttons = [action, inline_action, *buttons.buttons(), *message.buttons()]
    assert all(button.height() == 30 for button in dialog_buttons)

    dialog.close()
    message.close()
    app.processEvents()


def test_pv_mapping_search_and_group_filters_without_losing_selection(monkeypatch):
    pytest.importorskip("PyQt5")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from PyQt5.QtWidgets import QApplication, QPushButton, QTableWidgetSelectionRange

    import gotacc.gui.main  # noqa: F401 - configures Qt runtime paths
    from gotacc.gui.services.pv_library import PVLibraryItem
    from gotacc.gui.theme import apply_theme
    from gotacc.gui.views.tool_dialogs import PVMappingSelectorDialog

    app = QApplication.instance() or QApplication([])
    apply_theme(app, "dark")
    entries = [
        PVLibraryItem(
            name="Q1",
            pv_name="IRFEL:PS:Q1:ao",
            readback="IRFEL:PS:Q1:ai",
            group="matching",
            note="main quadrupole",
        ),
        PVLibraryItem(
            name="BPM-X",
            pv_name="IRFEL:BPM:01:X",
            readback="IRFEL:BPM:01:X",
            group="diagnostics",
            note="orbit monitor",
        ),
    ]
    dialog = PVMappingSelectorDialog(
        knob_entries=entries,
        objective_entries=[],
        constraint_entries=[],
    )
    dialog.show()
    app.processEvents()
    compact_buttons = dialog.findChildren(QPushButton)
    assert {button.text() for button in compact_buttons} >= {
        "Select Visible",
        "Clear Selection",
        "OK",
        "Cancel",
    }
    assert all(button.height() == 30 for button in compact_buttons)
    table = dialog._tables["knob"]
    table.setRangeSelected(
        QTableWidgetSelectionRange(0, 0, 0, table.columnCount() - 1),
        True,
    )

    search = dialog._search_boxes["knob"]
    group_box = dialog._group_boxes["knob"]

    search.setText("orbit bpm")
    app.processEvents()

    assert table.isRowHidden(0)
    assert not table.isRowHidden(1)
    assert dialog.selected_entries("knob") == [entries[0]]

    table.setRangeSelected(
        QTableWidgetSelectionRange(1, 0, 1, table.columnCount() - 1),
        True,
    )
    assert dialog.selected_entries("knob") == entries

    search.clear()
    app.processEvents()
    assert not table.isRowHidden(0)
    assert not table.isRowHidden(1)

    group_box.setCurrentText("matching")
    app.processEvents()
    assert not table.isRowHidden(0)
    assert table.isRowHidden(1)
    assert dialog.selected_entries("knob") == entries
    assert dialog._status_labels["knob"].text() == "Visible: 1/2    Selected: 2"
    dialog.close()
