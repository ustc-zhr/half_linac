from PyQt5.QtWidgets import QVBoxLayout, QWidget

from matplotlib.backends.backend_qt5agg import FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure


class MplWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.fig = Figure()
        self.axes = self.fig.add_subplot(111)
        self.canvas = FigureCanvas(self.fig)
        layout = QVBoxLayout(self)
        layout.addWidget(NavigationToolbar(self.canvas, self))
        layout.addWidget(self.canvas)
