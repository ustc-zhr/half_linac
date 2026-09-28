from PyQt5.QtGui import QColor, QPalette
from PyQt5.QtWidgets import QWidget, QVBoxLayout

from matplotlib.backends.backend_qt5agg import FigureCanvas

from matplotlib.figure import Figure
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar


class MplWidget(QWidget):

    def __init__(self, parent=None):
        QWidget.__init__(self, parent)

        self.fig = Figure(constrained_layout=True)
        self.axes  = self.fig.add_subplot(111) 
        self.canvas = FigureCanvas(self.fig)  
        self.toolbar = NavigationToolbar(self.canvas, self)

        vertical_layout = QVBoxLayout()
        vertical_layout.addWidget(self.toolbar)
        vertical_layout.addWidget(self.canvas)

        self.setLayout(vertical_layout)

    def set_toolbar_colors(self, background, foreground):
        palette = self.toolbar.palette()
        palette.setColor(QPalette.Window, QColor(background))
        palette.setColor(QPalette.WindowText, QColor(foreground))
        palette.setColor(QPalette.Button, QColor(background))
        palette.setColor(QPalette.ButtonText, QColor(foreground))
        self.toolbar.setPalette(palette)
        for _text, _tooltip, image_name, callback in self.toolbar.toolitems:
            if image_name is None or callback is None:
                continue
            action = self.toolbar._actions.get(callback)
            if action is not None:
                action.setIcon(self.toolbar._icon(f"{image_name}.png"))
