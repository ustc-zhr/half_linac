"""Whole-cell checkbox interaction for measurement sample tables."""

from PyQt5.QtCore import QEvent, QItemSelectionModel, Qt
from PyQt5.QtWidgets import QStyledItemDelegate


class UseSampleDelegate(QStyledItemDelegate):
    def editorEvent(self, event, model, option, index):
        flags = index.flags()
        if not (flags & Qt.ItemIsEnabled and flags & Qt.ItemIsUserCheckable):
            return False
        if event.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick):
            if event.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier):
                return False
            if (event.type() == QEvent.MouseButtonPress
                    and event.button() == Qt.LeftButton
                    and not event.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier)):
                # Consuming the checkbox press bypasses the view's usual selection
                # update. Keep subsequent bulk actions tied to the visible click.
                self.parent().selectionModel().setCurrentIndex(
                    index, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows
                )
            return event.button() == Qt.LeftButton
        if event.type() == QEvent.MouseButtonRelease:
            if event.button() != Qt.LeftButton or not option.rect.contains(event.pos()):
                return False
            # Modifier clicks remain available for selecting a range of rows.
            if event.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier):
                return False
            state = index.data(Qt.CheckStateRole)
            return model.setData(index, Qt.Unchecked if state == Qt.Checked else Qt.Checked,
                                 Qt.CheckStateRole)
        return super().editorEvent(event, model, option, index)


def configure_sample_selection(table):
    table.setItemDelegateForColumn(0, UseSampleDelegate(table))
    table.setToolTip(
        "Click anywhere in a Use cell to toggle it. "
        "Select rows with Shift/Ctrl or drag across the data columns, "
        "then click Exclude Selected to exclude them together."
    )
