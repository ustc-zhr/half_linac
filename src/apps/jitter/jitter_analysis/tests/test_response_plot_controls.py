import os
from types import SimpleNamespace

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from jitter_analysis.gui.plots import response_plot as response_plot_module


pytestmark = pytest.mark.skipif(
    response_plot_module.QtWidgets is None,
    reason="PyQt5 is required for ResponsePlot tests",
)


@pytest.fixture(scope="module")
def qt_app():
    app = response_plot_module.QtWidgets.QApplication.instance()
    return app or response_plot_module.QtWidgets.QApplication([])


def test_response_plot_selects_one_read_pv_and_tracks_point_spread(qt_app):
    plot = response_plot_module.ResponsePlot(show_channel_selector=True)
    objects = [
        SimpleNamespace(id="x", name="BPM X", unit="mm"),
        SimpleNamespace(id="y", name="BPM Y", unit="mm"),
    ]
    plot.reset_channels("Random Point Index", "", objects)

    assert plot.channel_combo.count() == 2
    assert plot.channel_combo.currentData() == "x"
    if plot.plot_widget is None:
        return
    assert plot._curves["x"].isVisible()
    assert not plot._curves["y"].isVisible()

    plot.append_step(
        1.0,
        [
            SimpleNamespace(pv_id="x", value=1.0),
            SimpleNamespace(pv_id="x", value=3.0),
            SimpleNamespace(pv_id="y", value=4.0),
            SimpleNamespace(pv_id="y", value=6.0),
        ],
    )
    assert plot._grouped_data["x"][1.0]["values"] == [1.0, 3.0]
    assert plot._sample_std([1.0, 3.0]) == pytest.approx(2.0 ** 0.5)

    plot.channel_combo.setCurrentIndex(1)
    assert not plot._curves["x"].isVisible()
    assert plot._curves["y"].isVisible()
