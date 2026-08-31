import os

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from jitter_analysis.gui.plots import influence_plot as influence_plot_module


pytestmark = pytest.mark.skipif(
    influence_plot_module.QtWidgets is None,
    reason="PyQt5 is required for InfluencePlot tests",
)


@pytest.fixture(scope="module")
def qt_app():
    app = influence_plot_module.QtWidgets.QApplication.instance()
    return app or influence_plot_module.QtWidgets.QApplication([])


def test_influence_plot_builds_overview_and_knob_response_matrix(qt_app):
    plot = influence_plot_module.InfluencePlot()
    plot.set_rows(
        [
            {
                "pv_id": "pv1",
                "name": "BPM X",
                "point_count": 12,
                "response_span": 4.2,
                "r_squared": 0.91,
                "coefficients": {
                    "k1": {"knob_id": "k1", "raw": 2.0, "standardized": 0.8, "knob_span": 1.0, "unit": "mm/A"},
                    "k2": {"knob_id": "k2", "raw": -0.5, "standardized": -0.2, "knob_span": 2.0, "unit": "mm/A"},
                },
                "response_values": [1.0, 2.0, 3.0],
                "predicted_values": [1.1, 1.9, 3.0],
                "warnings": [],
            }
        ],
        knob_ids=["k1", "k2"],
        knob_names={"k1": "K1", "k2": "K2"},
    )

    assert plot.overview_table.rowCount() == 1
    assert plot.overview_table.item(0, 1).text() == "K1"
    assert plot.matrix_table.rowCount() == 1
    assert plot.matrix_table.columnCount() == 2
    assert plot.matrix_table.item(0, 0).text() == "+0.800"
    assert "BPM X ← K1" in plot.detail_label.text()
