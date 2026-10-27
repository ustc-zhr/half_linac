import os

import numpy as np
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


def _series_source(
    *,
    contiguous=True,
    pv_id="test_phase",
    name="Test phase",
    group_id="llrf_channel_phase",
    group_label="LLRF Channel Phase",
    read_pv="TEST:LLRF:PHASE",
    tags=None,
    amplitude=0.4,
):
    sample_count = 128
    sample_indices = list(range(sample_count))
    if not contiguous:
        sample_indices = list(range(64)) + list(range(65, 129))
    times = np.arange(sample_count, dtype=float)
    values = 2.0 + amplitude * np.sin(2.0 * np.pi * 0.125 * times)
    return {
        "pv_id": pv_id,
        "name": name,
        "unit": "deg",
        "group_id": group_id,
        "group_label": group_label,
        "read_pv": read_pv,
        "tags": list(tags or ["phase"]),
        "sample_count": sample_count,
        "sample_indices": sample_indices,
        "sample_times_sec": times.tolist(),
        "series_sample_interval_sec": 1.0,
        "values": values.tolist(),
        "source_count": sample_count,
        "range_count": sample_count,
        "removed_count": 0,
    }


def _select_mode(plot, mode):
    index = plot.mode_combo.findData(mode)
    assert index >= 0
    plot.mode_combo.setCurrentIndex(index)


def test_spectrum_show_uses_jitter_variable_selector(qt_app, monkeypatch):
    first = _series_source()
    second = _series_source(
        pv_id="test_amplitude",
        name="Test amplitude",
        group_id="llrf_channel_amplitude",
        group_label="LLRF Channel Amplitude",
        read_pv="TEST:LLRF:AMPLITUDE",
        tags=["amplitude"],
    )
    captured = {}

    class FakeVisibilityDialog:
        def __init__(self, rows, visible_pv_ids, parent=None):
            captured["rows"] = rows
            captured["visible_pv_ids"] = list(visible_pv_ids)
            captured["parent"] = parent

        def exec_(self):
            return spectrum_plot_module.QtWidgets.QDialog.Accepted

        def selected_pv_ids(self):
            return ["test_amplitude"]

    monkeypatch.setattr(
        spectrum_plot_module,
        "SeriesVisibilityDialog",
        FakeVisibilityDialog,
    )
    plot = spectrum_plot_module.SpectrumPlot()
    plot.set_series_sources([first, second])

    assert plot.series_button.text() == "Visible Variables (1/2)"
    assert plot.series_button.property("plotToolbarControl") == "true"
    assert plot.series_button.height() == 28
    assert plot.current_pv_ids() == ["test_phase"]

    plot.series_button.click()

    assert captured["visible_pv_ids"] == ["test_phase"]
    assert captured["parent"] is plot
    assert captured["rows"][1] == {
        "pv_id": "test_amplitude",
        "label": "Test amplitude [test_amplitude]",
        "group_id": "llrf_channel_amplitude",
        "group_label": "LLRF Channel Amplitude",
        "read_pv": "TEST:LLRF:AMPLITUDE",
        "tags": ["amplitude"],
    }
    assert plot.current_pv_ids() == ["test_amplitude"]
    assert plot.series_button.text() == "Visible Variables (1/2)"
    assert plot.summary_table.item(0, 0).text() == (
        "Test amplitude [test_amplitude]"
    )

    plot.close()
    plot.deleteLater()
    qt_app.processEvents()


def test_relative_db_compares_spectral_shape_without_changing_absolute_table(
    qt_app,
):
    plot = spectrum_plot_module.SpectrumPlot()
    plot.set_series_sources(
        [
            _series_source(
                pv_id="small_phase",
                name="Small phase",
                amplitude=0.4,
            ),
            _series_source(
                pv_id="large_phase",
                name="Large phase",
                amplitude=400.0,
            ),
        ]
    )
    plot.set_visible_pv_ids(["small_phase", "large_phase"])

    assert plot.y_display_combo.currentData() == "absolute"
    assert plot.y_scale_combo.isEnabled()
    assert float(plot.summary_table.item(0, 8).text()) == pytest.approx(
        0.4, rel=1.0e-5
    )
    assert float(plot.summary_table.item(1, 8).text()) == pytest.approx(
        400.0, rel=1.0e-5
    )

    relative_index = plot.y_display_combo.findData("relative_db")
    assert relative_index >= 0
    plot.y_display_combo.setCurrentIndex(relative_index)

    assert plot._is_relative_db()
    assert not plot._is_log_y()
    assert not plot.y_scale_combo.isEnabled()
    assert plot.y_scale_combo.currentData() == "linear"
    assert plot._y_axis_label("amplitude") == (
        "Relative Amplitude [dB; each PV non-DC peak = 0 dB]"
    )
    assert float(plot.summary_table.item(0, 8).text()) == pytest.approx(
        0.4, rel=1.0e-5
    )
    assert float(plot.summary_table.item(1, 8).text()) == pytest.approx(
        400.0, rel=1.0e-5
    )
    assert "y=relative dB (each PV non-DC peak=0 dB)" in (
        plot.info_label.text()
    )

    amplitude_db, amplitude_reference = plot._relative_db_values(
        [0.0, 1.0, 2.0],
        [0.0, 10.0, 1.0],
        power=False,
    )
    psd_db, psd_reference = plot._relative_db_values(
        [0.0, 1.0, 2.0],
        [0.0, 10.0, 1.0],
        power=True,
    )
    assert amplitude_reference == pytest.approx(10.0)
    assert psd_reference == pytest.approx(10.0)
    assert amplitude_db == pytest.approx([-120.0, 0.0, -20.0])
    assert psd_db == pytest.approx([-120.0, 0.0, -10.0])

    if plot.plot_widget is not None:
        curves = plot.plot_widget.listDataItems()
        assert len(curves) == 2
        normalized_spectra = []
        for curve in curves:
            _x_values, y_values = curve.getData()
            assert max(y_values) == pytest.approx(0.0, abs=1.0e-9)
            assert min(y_values) >= -120.0
            normalized_spectra.append(y_values)
            assert "ref " in curve.name()
        np.testing.assert_allclose(
            normalized_spectra[0],
            normalized_spectra[1],
            atol=1.0e-7,
        )

    _select_mode(plot, "welch_psd")
    assert plot._y_axis_label("welch_psd") == (
        "Relative PSD [dB; each PV non-DC peak = 0 dB]"
    )
    if plot.plot_widget is not None:
        curves = plot.plot_widget.listDataItems()
        assert len(curves) == 2
        for curve in curves:
            _x_values, y_values = curve.getData()
            assert max(y_values) == pytest.approx(0.0, abs=1.0e-9)
            assert min(y_values) >= -120.0
            assert "deg²/Hz" in curve.name()

    plot.close()
    plot.deleteLater()
    qt_app.processEvents()


def test_spectrum_plot_supports_independent_linear_and_log_axes(qt_app):
    plot = spectrum_plot_module.SpectrumPlot()

    assert plot.x_scale_combo.currentData() == "linear"
    assert plot.y_scale_combo.currentData() == "linear"
    assert plot._x_axis_label() == "Frequency [Hz]"
    assert (
        plot._y_axis_label("amplitude")
        == "One-sided peak amplitude"
    )
    assert plot._y_axis_label("welch_psd") == "PSD"

    plot.x_scale_combo.setCurrentIndex(
        plot.x_scale_combo.findData("log10")
    )
    plot.y_scale_combo.setCurrentIndex(
        plot.y_scale_combo.findData("log10")
    )

    assert plot._is_log_x()
    assert plot._is_log_y()
    assert plot._x_axis_label() == "Frequency [Hz] (log scale)"
    assert (
        plot._y_axis_label("amplitude")
        == "One-sided peak amplitude (log scale)"
    )
    assert plot._y_axis_label("welch_psd") == "PSD (log scale)"
    if plot.plot_widget is not None:
        plot_item = plot.plot_widget.getPlotItem()
        assert plot_item.ctrl.logXCheck.isChecked()
        assert plot_item.ctrl.logYCheck.isChecked()
        for axis in (plot._frequency_axis, plot._amplitude_axis):
            assert axis.autoSIPrefix is False
            sparse_labels = axis.logTickStrings(
                [-4.0, -3.9, -3.7, -3.3, -3.0],
                1.0,
                None,
            )
            assert sparse_labels[0]
            assert sparse_labels[-1]
            assert sparse_labels[1:-1] == ["", "", ""]
            zoomed_labels = axis.logTickStrings(
                [-0.6989700043360187, -0.3010299956639812],
                1.0,
                None,
            )
            assert all(zoomed_labels)
            assert all(
                axis.logTickStrings([-4.0, -3.0, -2.0], 1.0, 1.0)
            )
        assert "(x" not in plot._frequency_axis.labelString()
        assert "(x" not in plot._amplitude_axis.labelString()

    plot.close()
    plot.deleteLater()
    qt_app.processEvents()


def test_spectrum_modes_expose_reproducible_controls_and_units(qt_app):
    plot = spectrum_plot_module.SpectrumPlot()
    plot.set_context_summary("Selected samples 1–128 | 127 s")
    plot.set_series_sources([_series_source()])

    assert plot.mode_combo.currentData() == "amplitude"
    assert plot._selected_window_name("amplitude") == "hann"
    assert plot.detrend_combo.currentData() == "mean"
    assert plot.window_combo.isEnabled()
    assert plot.detrend_combo.isEnabled()
    assert not plot.nperseg_spin.isEnabled()
    assert not plot.fit_frequency_spin.isEnabled()
    assert plot.summary_table.item(0, 0).text() == (
        "Test phase [test_phase]"
    )
    assert plot.summary_table.item(0, 5).text() == "Hann"
    assert float(plot.summary_table.item(0, 8).text()) == pytest.approx(
        0.4,
        rel=1.0e-5,
    )
    assert "one-sided peak amplitude" in plot.info_label.text()
    assert "Selected samples 1–128 | 127 s" in plot.info_label.text()

    _select_mode(plot, "welch_psd")
    assert plot.window_combo.isEnabled()
    assert plot.detrend_combo.isEnabled()
    assert plot.nperseg_spin.isEnabled()
    assert not plot.fit_frequency_spin.isEnabled()
    assert plot.summary_table.horizontalHeaderItem(9).text() == "Peak PSD"
    assert plot.summary_table.item(0, 10).text() == "deg²/Hz"
    assert plot.summary_table.item(0, 6).text() == "128"
    assert "Selected samples 1–128 | 127 s" in plot.info_label.text()

    _select_mode(plot, "periodic_fit")
    assert not plot.window_combo.isEnabled()
    assert not plot.detrend_combo.isEnabled()
    assert not plot.nperseg_spin.isEnabled()
    assert plot.fit_frequency_spin.isEnabled()
    assert plot.summary_table.horizontalHeaderItem(5).text() == "Peak Amp"
    assert plot.summary_table.item(0, 2).text() == "Auto"
    assert float(plot.summary_table.item(0, 3).text()) == pytest.approx(
        0.125,
        abs=1.0e-7,
    )
    assert float(plot.summary_table.item(0, 5).text()) == pytest.approx(
        0.4,
        rel=1.0e-5,
    )
    if plot.plot_widget is not None:
        assert any(
            isinstance(item, spectrum_plot_module.pg.InfiniteLine)
            for item in plot.plot_widget.items()
        )

    plot.fit_frequency_spin.setValue(0.125)
    assert plot.summary_table.item(0, 2).text() == "Locked"
    assert "0.125 Hz locked" in plot.info_label.text()
    assert "Selected samples 1–128 | 127 s" in plot.info_label.text()

    relative_index = plot.y_display_combo.findData("relative_db")
    plot.y_display_combo.setCurrentIndex(relative_index)
    assert float(plot.summary_table.item(0, 5).text()) == pytest.approx(
        0.4,
        rel=1.0e-5,
    )
    if plot.plot_widget is not None:
        data_items = plot.plot_widget.listDataItems()
        spectrum_curve = next(item for item in data_items if item.name())
        _x_values, spectrum_db = spectrum_curve.getData()
        assert max(spectrum_db) <= 1.0e-9
        marker = next(
            item
            for item in data_items
            if not item.name() and len(item.getData()[0]) == 1
        )
        _marker_x, marker_db = marker.getData()
        assert -0.01 < marker_db[0] <= 0.0

    plot.close()
    plot.deleteLater()
    qt_app.processEvents()


def test_fft_rejects_gapped_indices_and_periodic_fit_accepts_them(qt_app):
    plot = spectrum_plot_module.SpectrumPlot()
    plot.set_series_sources([_series_source(contiguous=False)])

    assert plot.summary_table.rowCount() == 0
    assert "use Periodic Fit" in plot.info_label.text()

    _select_mode(plot, "periodic_fit")

    assert plot.summary_table.rowCount() == 1
    assert plot.summary_table.item(0, 2).text() == "Auto"
    assert float(plot.summary_table.item(0, 3).text()) == pytest.approx(
        0.125,
        abs=1.0e-7,
    )

    plot.close()
    plot.deleteLater()
    qt_app.processEvents()


def test_periodic_fit_reports_missing_timestamp_data(qt_app):
    plot = spectrum_plot_module.SpectrumPlot()
    source = _series_source()
    source["sample_times_sec"] = []
    plot.set_series_sources([source])

    _select_mode(plot, "periodic_fit")

    assert plot.summary_table.rowCount() == 0
    assert "values and sample_times_sec must have the same shape" in (
        plot.info_label.text()
    )

    plot.close()
    plot.deleteLater()
    qt_app.processEvents()
