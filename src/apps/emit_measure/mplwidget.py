from PyQt5.QtWidgets import QSizePolicy, QWidget, QVBoxLayout

from matplotlib.backends.backend_qt5agg import FigureCanvas

from matplotlib.figure import Figure
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar


class MplWidget(QWidget):

    def __init__(self, parent=None):
        QWidget.__init__(self, parent)

        self.fig = Figure(constrained_layout=True)
        self.axes  = self.fig.add_subplot(111) 
        self.canvas = FigureCanvas(self.fig)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        toolbar = NavigationToolbar(self.canvas,self) 

        vertical_layout = QVBoxLayout()
        vertical_layout.setContentsMargins(0, 0, 0, 0)
        vertical_layout.setSpacing(2)
        vertical_layout.addWidget(toolbar, 0)
        vertical_layout.addWidget(self.canvas, 1)

        self.setLayout(vertical_layout)
