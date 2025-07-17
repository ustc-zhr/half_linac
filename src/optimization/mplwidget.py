from PyQt5.QtWidgets import QWidget, QVBoxLayout

from matplotlib.backends.backend_qt5agg import FigureCanvas

from matplotlib.figure import Figure
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar


class MplWidget(QWidget):

    def __init__(self, parent=None):
        QWidget.__init__(self, parent)

        self.fig = Figure()      
        self.axes  = self.fig.add_subplot(111) 
        self.canvas = FigureCanvas(self.fig)  
        toolbar = NavigationToolbar(self.canvas,self) 

        vertical_layout = QVBoxLayout()
        vertical_layout.addWidget(toolbar) 
        vertical_layout.addWidget(self.canvas)

        self.setLayout(vertical_layout)
