import os

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from jitter_analysis.gui.plots import sensitivity_plot as sensitivity_plot_module


pytestmark = pytest.mark.skipif(
    sensitivity_plot_module.QtWidgets is None,
    reason="PyQt5 is required for SensitivityPlot tests",
)


@pytest.fixture(scope="module")
def qt_app():
    app = sensitivity_plot_module.QtWidgets.QApplication.instance()
    return app or sensitivity_plot_module.QtWidgets.QApplication([])


def test_sensitivity_table_keeps_primary_scan_metrics_only(qt_app):
    plot = sensitivity_plot_module.SensitivityPlot()

    assert plot.table.columnCount() == 5
    assert [plot.table.horizontalHeaderItem(index).text() for index in range(5)] == [
        "PV",
        "Slope",
        "R^2",
        "Resp Span",
        "Unit",
    ]
