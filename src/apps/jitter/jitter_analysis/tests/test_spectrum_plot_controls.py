import os

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from jitter_analysis.gui.plots import spectrum_plot as spectrum_plot_module


pytestmark = pytest.mark.skipif(
    spectrum_plot_module.QtWidgets is None,
    reason="PyQt5 is required for SpectrumPlot tests",
)


@pytest.fixture(scope="module")
def qt_app():
    app = spectrum_plot_module.QtWidgets.QApplication.instance()
    return app or spectrum_plot_module.QtWidgets.QApplication([])


def test_spectrum_plot_supports_independent_linear_and_log_axes(qt_app):
    plot = spectrum_plot_module.SpectrumPlot()

    assert plot.x_scale_combo.currentData() == "linear"
    assert plot.y_scale_combo.currentData() == "linear"
    assert plot._x_axis_label() == "Frequency [Hz]"
    assert plot._y_axis_label("amplitude") == "Amplitude"
    assert plot._y_axis_label("welch_psd") == "PSD"

    plot.x_scale_combo.setCurrentIndex(plot.x_scale_combo.findData("log10"))
    plot.y_scale_combo.setCurrentIndex(plot.y_scale_combo.findData("log10"))

    assert plot._is_log_x()
    assert plot._is_log_y()
    assert plot._x_axis_label() == "Frequency [Hz] (log scale)"
    assert plot._y_axis_label("amplitude") == "Amplitude (log scale)"
    assert plot._y_axis_label("welch_psd") == "PSD (log scale)"
    if plot.plot_widget is not None:
        plot_item = plot.plot_widget.getPlotItem()
        assert plot_item.ctrl.logXCheck.isChecked()
        assert plot_item.ctrl.logYCheck.isChecked()
        for axis in (plot._frequency_axis, plot._amplitude_axis):
            assert axis.autoSIPrefix is False
            sparse_labels = axis.logTickStrings([-4.0, -3.9, -3.7, -3.3, -3.0], 1.0, None)
            assert sparse_labels[0]
            assert sparse_labels[-1]
            assert sparse_labels[1:-1] == ["", "", ""]
            zoomed_labels = axis.logTickStrings(
                [-0.6989700043360187, -0.3010299956639812], 1.0, None
            )
            assert all(zoomed_labels)
            assert all(axis.logTickStrings([-4.0, -3.0, -2.0], 1.0, 1.0))
        assert "(x" not in plot._frequency_axis.labelString()
        assert "(x" not in plot._amplitude_axis.labelString()

    plot.close()
    plot.deleteLater()
    qt_app.processEvents()
