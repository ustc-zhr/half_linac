from datetime import datetime, timedelta
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jitter_analysis.domain.types import RunMode, RunStatus, ScanStepRecord
from jitter_analysis.gui.view_logic import (
    analysis_mode_key,
    connection_summary,
    estimate_series_sample_interval,
    estimate_series_sample_interval_from_sample_indices,
    jitter_filter_status_text,
    mode_display_name,
    mode_help_text,
    mode_ready_state,
    mode_key_from_run_mode,
    progress_tone,
    run_status_tone,
    single_knob_axis_name,
    single_knob_axis_summary_text,
    single_knob_step_axis_value,
)


def test_mode_and_tone_helpers():
    assert mode_key_from_run_mode(RunMode.KNOB_SCAN) == "single_knob_scan"
    assert mode_key_from_run_mode(None) == "timed_acquisition"
    assert analysis_mode_key("timed_acquisition", True, RunMode.MULTI_KNOB_RANDOM) == "multi_knob_random"
    assert analysis_mode_key("single_knob_scan", False, RunMode.TIMED_ACQUISITION) == "single_knob_scan"
    assert analysis_mode_key("single_knob_scan", True, None) == "single_knob_scan"
    assert mode_display_name("timed_acquisition") == "Monitor"
    assert mode_display_name("single_knob_scan") == "Single Knob"
    assert mode_display_name("unknown_mode") == "Unknown Mode"
    assert mode_display_name(None) == "Idle"
    assert "fixed interval" in mode_help_text("timed_acquisition")
    assert mode_help_text("unknown_mode") == ""
    assert progress_tone(0, 10) == "subtle"
    assert progress_tone(5, 10) == "info"
    assert progress_tone(10, 10) == "success"
    assert run_status_tone(RunStatus.COMPLETED) == "success"
    assert run_status_tone(RunStatus.FAILED) == "danger"


def test_connection_summary_labels_and_tones():
    assert connection_summary(0, 0) == ("Not checked", "subtle")
    assert connection_summary(3, 3) == ("Connected (3/3 connected)", "success")
    assert connection_summary(0, 3) == ("0/3 connected", "danger")
    assert connection_summary(2, 3) == ("2/3 connected", "warning")


def test_jitter_filter_status_text_variants():
    assert jitter_filter_status_text(
        available=False,
        enabled=True,
        threshold=4.0,
    ) == "Outlier filtering is available in Monitor mode only and applies to Jitter, Correlation, and Spectrum."
    assert jitter_filter_status_text(
        available=True,
        enabled=False,
        threshold=4.0,
    ) == "Outlier filter is off. Monitor Jitter, Correlation, and Spectrum use raw valid samples."
    assert jitter_filter_status_text(
        available=True,
        enabled=True,
        threshold=4.25,
    ) == (
        "Outlier filter is on. Method: robust z-score using median and MAD. "
        "Threshold: 4.2 sigma. It will apply to Jitter, Correlation, and Spectrum when analysis data is available."
    )
    assert jitter_filter_status_text(
        available=True,
        enabled=True,
        threshold=4.0,
        total_removed=3,
        affected_variables=2,
    ) == (
        "Outlier filter is on. Method: robust z-score using median and MAD. "
        "Threshold: 4.0 sigma. Removed 3 point(s) across 2 variable(s)."
    )


def test_mode_ready_state_handles_global_blockers():
    assert mode_ready_state(
        "timed_acquisition",
        run_status=RunStatus.RUNNING,
        config_loaded=True,
        selected_object_count=1,
        selected_knob_count=0,
        active_knob_available=False,
        random_ranges_valid=False,
    ) == (False, "Stop the current run before changing the setup.")
    assert mode_ready_state(
        "timed_acquisition",
        run_status=RunStatus.IDLE,
        config_loaded=False,
        selected_object_count=0,
        selected_knob_count=0,
        active_knob_available=False,
        random_ranges_valid=False,
    ) == (False, "Load a PV library.")
    assert mode_ready_state(
        "timed_acquisition",
        run_status=RunStatus.IDLE,
        config_loaded=True,
        selected_object_count=0,
        selected_knob_count=0,
        active_knob_available=False,
        random_ranges_valid=False,
    ) == (False, "Select at least one read PV.")


def test_mode_ready_state_for_monitor_and_single_knob_modes():
    assert mode_ready_state(
        "timed_acquisition",
        run_status=RunStatus.IDLE,
        config_loaded=True,
        selected_object_count=1,
        selected_knob_count=0,
        active_knob_available=False,
        random_ranges_valid=False,
    ) == (True, "Ready to start Monitor.")
    assert mode_ready_state(
        "single_knob_scan",
        run_status=RunStatus.IDLE,
        config_loaded=True,
        selected_object_count=1,
        selected_knob_count=1,
        active_knob_available=False,
        random_ranges_valid=False,
    ) == (False, "Select an active control PV.")
    assert mode_ready_state(
        "single_knob_scan",
        run_status=RunStatus.IDLE,
        config_loaded=True,
        selected_object_count=1,
        selected_knob_count=1,
        active_knob_available=True,
        random_ranges_valid=False,
    ) == (True, "Ready to start Single Knob.")


def test_mode_ready_state_for_random_multi_knob_mode():
    assert mode_ready_state(
        "multi_knob_random",
        run_status=RunStatus.IDLE,
        config_loaded=True,
        selected_object_count=1,
        selected_knob_count=0,
        active_knob_available=False,
        random_ranges_valid=False,
    ) == (False, "Select at least one control PV.")
    assert mode_ready_state(
        "multi_knob_random",
        run_status=RunStatus.IDLE,
        config_loaded=True,
        selected_object_count=0,
        selected_knob_count=1,
        active_knob_available=True,
        random_ranges_valid=False,
    ) == (False, "Select at least one read PV.")
    assert mode_ready_state(
        "multi_knob_random",
        run_status=RunStatus.IDLE,
        config_loaded=True,
        selected_object_count=1,
        selected_knob_count=1,
        active_knob_available=True,
        random_ranges_valid=False,
    ) == (False, "Configure at least one valid control PV range.")
    assert mode_ready_state(
        "multi_knob_random",
        run_status=RunStatus.IDLE,
        config_loaded=True,
        selected_object_count=1,
        selected_knob_count=1,
        active_knob_available=True,
        random_ranges_valid=True,
    ) == (True, "Ready to start Random Multi-Knob.")


def test_single_knob_axis_helpers():
    step = ScanStepRecord(
        step_index=0,
        target_value=1.5,
        readback_value=None,
        started_at=datetime.now(),
    )

    assert single_knob_axis_name("target", "K1") == "K1 Target"
    assert single_knob_axis_name("readback", "K1") == "K1 Readback"
    assert "target" in single_knob_axis_summary_text("target")
    assert single_knob_step_axis_value("target", step) == 1.5
    assert single_knob_step_axis_value("readback", step) == 1.5
    step.readback_value = 1.45
    assert single_knob_step_axis_value("readback", step) == 1.45


def test_estimate_series_sample_interval_uses_median_positive_delta():
    start = datetime.fromisoformat("2026-05-20T10:00:00")
    timestamps = [
        start,
        start + timedelta(seconds=0.2),
        start + timedelta(seconds=0.4),
        start + timedelta(seconds=2.0),
    ]

    assert estimate_series_sample_interval(timestamps) == 0.2


def test_estimate_series_sample_interval_from_sample_indices_filters_missing_timestamps():
    start = datetime.fromisoformat("2026-05-20T10:00:00")
    sample_timestamps = [
        start,
        None,
        start + timedelta(seconds=0.2),
        start + timedelta(seconds=0.4),
    ]

    assert estimate_series_sample_interval_from_sample_indices([0, 1, 2, 3], sample_timestamps) == 0.2


def test_estimate_series_sample_interval_rejects_missing_positive_deltas():
    timestamp = datetime.fromisoformat("2026-05-20T10:00:00")

    with pytest.raises(ValueError, match="positive timestamp deltas"):
        estimate_series_sample_interval([timestamp, timestamp])
