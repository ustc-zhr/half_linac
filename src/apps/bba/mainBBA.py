import sys

from PyQt5.QtWidgets import QApplication

try:
    from .main import myWindow as BBAWindow
except ImportError:
    from main import myWindow as BBAWindow


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = BBAWindow()
    window.show()
    sys.exit(app.exec_())
