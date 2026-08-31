import os
from datetime import datetime
from types import SimpleNamespace

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from jitter_analysis.domain.types import MultiKnobStepRecord, SampleRecord
from jitter_analysis.gui.plots import response_map_plot as response_map_module


pytestmark = pytest.mark.skipif(
    response_map_module.QtWidgets is None,
    reason="PyQt5 is required for ResponseMapPlot tests",
)


@pytest.fixture(scope="module")
def qt_app():
    global _QT_APP
    app = response_map_module.QtWidgets.QApplication.instance()
    _QT_APP = app or response_map_module.QtWidgets.QApplication([])
    return _QT_APP


def test_response_map_selects_response_and_uses_grid_point_means(qt_app):
    now = datetime.now()
    steps = [
        MultiKnobStepRecord(
            step_index=0,
            target_values={"k1": -1.0, "k2": 2.0},
            readback_values={"k1": -0.9, "k2": 2.1},
            started_at=now,
            samples=[
                SampleRecord("x", 1.0, now),
                SampleRecord("x", 3.0, now),
                SampleRecord("y", 8.0, now),
            ],
        ),
        MultiKnobStepRecord(
            step_index=1,
            target_values={"k1": 1.0, "k2": 2.0},
            readback_values={"k1": 0.9, "k2": 1.9},
            started_at=now,
            samples=[SampleRecord("x", 5.0, now), SampleRecord("y", 10.0, now)],
        ),
    ]
    plot = response_map_module.ResponseMapPlot()
    plot.set_data(
        steps,
        x_knob_id="k1",
        y_knob_id="k2",
        x_name="K1",
        y_name="K2",
        x_unit="A",
        y_unit="A",
        objects=[
            SimpleNamespace(id="x", name="BPM X"),
            SimpleNamespace(id="y", name="BPM Y"),
        ],
    )

    assert plot.channel_combo.count() == 2
    assert plot._rows_by_pv["x"] == [(-1.0, 2.0, 2.0), (1.0, 2.0, 5.0)]
    assert "2 grid points" in plot.summary_label.text()
    plot.channel_combo.setCurrentIndex(1)
    assert "BPM Y" in plot.summary_label.text()
