import pytest


def test_pv_mapping_search_filters_rows_without_losing_selection(monkeypatch):
    pytest.importorskip("PyQt5")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from PyQt5.QtWidgets import QApplication, QTableWidgetSelectionRange

    import gotacc.gui.main  # noqa: F401 - configures Qt runtime paths
    from gotacc.gui.services.pv_library import PVLibraryItem
    from gotacc.gui.views.tool_dialogs import PVMappingSelectorDialog

    app = QApplication.instance() or QApplication([])
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
    table = dialog._tables["knob"]
    table.setRangeSelected(
        QTableWidgetSelectionRange(0, 0, 0, table.columnCount() - 1),
        True,
    )

    dialog.lineEdit_filter.setText("orbit bpm")
    app.processEvents()

    assert table.isRowHidden(0)
    assert not table.isRowHidden(1)
    assert dialog.selected_entries("knob") == [entries[0]]

    table.setRangeSelected(
        QTableWidgetSelectionRange(1, 0, 1, table.columnCount() - 1),
        True,
    )
    assert dialog.selected_entries("knob") == entries

    dialog.lineEdit_filter.clear()
    app.processEvents()
    assert not table.isRowHidden(0)
    assert not table.isRowHidden(1)
    dialog.close()
