import sys
import unittest
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QTableWidget, QTableWidgetItem

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from half_linac.src.apps.emit_measure.sample_selection import configure_sample_selection


class SampleSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.table = QTableWidget(3, 2)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)
        configure_sample_selection(self.table)
        for row in range(3):
            for column in range(2):
                item = QTableWidgetItem("")
                self.table.setItem(row, column, item)
                if column == 0:
                    item.setCheckState(Qt.Checked)
        self.table.show()
        self.app.processEvents()
        self.addCleanup(self.table.close)

    def click(self, row, column, modifiers=Qt.NoModifier):
        QTest.mouseClick(self.table.viewport(), Qt.LeftButton, modifiers,
                         self.table.visualItemRect(self.table.item(row, column)).center())

    def selected(self):
        return {index.row() for index in self.table.selectionModel().selectedRows()}

    def test_use_retargets_bulk_exclusion_to_clicked_row(self):
        self.click(0, 1)
        self.click(1, 1, Qt.ControlModifier)
        self.assertEqual(self.selected(), {0, 1})
        self.click(2, 0)
        self.assertEqual(self.selected(), {2})
        self.assertEqual(self.table.item(2, 0).checkState(), Qt.Unchecked)
        # Exclude Selected must not change the previously selected rows.
        for row in self.selected():
            self.table.item(row, 0).setCheckState(Qt.Unchecked)
        self.assertEqual(self.table.item(0, 0).checkState(), Qt.Checked)
        self.assertEqual(self.table.item(1, 0).checkState(), Qt.Checked)

    def test_modifier_clicks_select_without_toggling_use(self):
        self.click(0, 1)
        self.click(2, 0, Qt.ShiftModifier)
        self.assertEqual(self.selected(), {0, 1, 2})
        self.click(1, 0, Qt.ControlModifier)
        self.assertEqual(self.selected(), {0, 2})
        self.assertTrue(all(self.table.item(r, 0).checkState() == Qt.Checked for r in range(3)))
