"""Schematic geometry and navigation remain separate from physical positions."""
import os
import unittest
from types import SimpleNamespace
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtWidgets import QApplication
from half_linac.src.virtual_machine.beamline_view import BeamlineView, schematic_spans
from half_linac.src.virtual_machine.workbench_data import occurrences


class BeamlineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_compression_repeated_selection_and_navigation(self):
        elements = occurrences(dict(usedline=['Q','D','Q','W'], lattice={
            'Q': dict(TYPE='QUAD', L=0), 'D': dict(TYPE='DRIF', L=20),
            'W': dict(TYPE='WATCH', L=0)}))
        spans = schematic_spans(elements)
        self.assertTrue(spans[1][2])
        self.assertEqual(elements[2]['s'], 20)
        self.assertTrue(all(spans[i][1] < spans[i+1][0] for i in range(3)))
        selected = []
        view = BeamlineView(selected.append)
        view.resize(900,180); view.show()
        view.update_line(elements, 2, True, reset=True)
        self.app.processEvents()
        self.assertEqual(view._name(2), 'Q [2/2]')
        center = sum(spans[2][:2])/2
        event = SimpleNamespace(inaxes=view.ax, xdata=center, ydata=0, x=300, button=1)
        view._down(event); view._up(event)
        self.assertEqual(selected, [2])
        full = view.ax.get_xlim()
        view._scroll(SimpleNamespace(inaxes=view.ax,xdata=center,button='up'))
        self.assertLess(view.ax.get_xlim()[1]-view.ax.get_xlim()[0],full[1]-full[0])
        view.fit_line()
        self.assertEqual(view.ax.get_xlim(), full)
        view._down(event)
        view._move(SimpleNamespace(x=340))
        view._up(event)
        self.assertEqual(selected, [2])
        view.update_line(elements,2,False)
        self.assertEqual(elements[2]['s'],20)
        view.close()
