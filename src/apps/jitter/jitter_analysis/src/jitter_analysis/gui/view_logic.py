from __future__ import annotations

from datetime import datetime
from typing import Iterable

from ..domain.types import RunMode, RunStatus, ScanStepRecord


def mode_key_from_run_mode(mode: RunMode | None) -> str:
    mapping = {
        RunMode.TIMED_ACQUISITION: "timed_acquisition",
        RunMode.KNOB_SCAN: "single_knob_scan",
        RunMode.MULTI_KNOB_RANDOM: "multi_knob_random",
    }
    return mapping.get(mode, "timed_acquisition")


def analysis_mode_key(fallback_mode: str, has_analysis_data: bool, current_run_mode: RunMode | None) -> str:
    if has_analysis_data and current_run_mode is not None:
        return mode_key_from_run_mode(current_run_mode)
    return fallback_mode


def mode_display_name(mode: str | None) -> str:
    labels = {
        "timed_acquisition": "Monitor",
        "single_knob_scan": "Single Knob",
        "multi_knob_random": "Random Multi-Knob",
    }
    return labels.get(mode, mode.replace("_", " ").title() if mode else "Idle")


def mode_help_text(mode: str) -> str:
    messages = {
        "timed_acquisition": "Monitor samples the selected read PVs at a fixed interval.",
        "single_knob_scan": (
            "Single Knob changes one active control PV while all other selected control PVs stay fixed."
        ),
        "multi_knob_random": (
            "Random Multi-Knob changes all enabled control PVs together at each random point."
        ),
    }
    return messages.get(mode, "")


def progress_tone(completed: int, total: int) -> str:
    if total <= 0 or completed <= 0:
        return "subtle"
    if completed >= total:
        return "success"
    return "info"


def run_status_tone(status: RunStatus) -> str:
    if status == RunStatus.COMPLETED:
        return "success"
    if status == RunStatus.STOPPED:
        return "warning"
    if status == RunStatus.FAILED:
        return "danger"
    if status == RunStatus.RUNNING:
        return "info"
    return "subtle"


def connection_summary(connected: int, total: int) -> tuple[str, str]:
    if total <= 0:
        return "Not checked", "subtle"
    label = f"{connected}/{total} connected"
    if connected == total:
        return f"Connected ({label})", "success"
    if connected <= 0:
        return label, "danger"
    return label, "warning"


def jitter_filter_status_text(
    *,
    available: bool,
    enabled: bool,
    threshold: float,
    total_removed: int | None = None,
    affected_variables: int | None = None,
) -> str:
    if not available:
        return "Outlier filtering is available in Monitor mode only and applies to Jitter, Correlation, and Spectrum."
    if not enabled:
        return "Outlier filter is off. Monitor Jitter, Correlation, and Spectrum use raw valid samples."

    message = (
        f"Outlier filter is on. Method: robust z-score using median and MAD. "
        f"Threshold: {threshold:.1f} sigma."
    )
    if total_removed is None:
        message += " It will apply to Jitter, Correlation, and Spectrum when analysis data is available."
    else:
        message += f" Removed {total_removed} point(s)"
        if affected_variables is not None:
            message += f" across {affected_variables} variable(s)."
    return message


def mode_ready_state(
    mode: str,
    *,
    run_status: RunStatus,
    config_loaded: bool,
    selected_object_count: int,
    selected_knob_count: int,
    active_knob_available: bool,
    random_ranges_valid: bool,
) -> tuple[bool, str]:
    if run_status == RunStatus.RUNNING:
        return False, "Run in progress. Stop the current run to change PVs or mode."

    if not config_loaded:
        return False, "Next: load a PV library."

    if selected_object_count <= 0 and selected_knob_count <= 0:
        return False, "Next: choose read PVs and control PVs with 'Choose PVs...'."

    if mode == "timed_acquisition":
        if selected_object_count <= 0:
            return False, "Next: choose at least one read PV for Monitor."
        return True, "Ready: click Start to run Monitor."

    if mode == "single_knob_scan":
        if selected_knob_count <= 0:
            return False, "Next: choose at least one control PV to enable Single Knob."
        if not active_knob_available:
            return False, "Next: choose the active control PV for Single Knob."
        if selected_object_count <= 0:
            return False, "Next: choose at least one read PV to sample during Single Knob."
        return True, "Ready: click Start to run Single Knob."

    if selected_knob_count <= 0:
        return False, "Next: choose control PVs to enable Random Multi-Knob."
    if selected_object_count <= 0:
        return False, "Next: choose at least one read PV to sample during Random Multi-Knob."
    if not random_ranges_valid:
        return False, "Next: open 'Configure Ranges...' and enable at least one valid control PV range."
    return True, "Ready: click Start to run Random Multi-Knob."


def single_knob_axis_name(axis_source: str, knob_name: str = "") -> str:
    base_name = knob_name.strip() or "Knob Value"
    if axis_source == "target":
        return f"{base_name} Target"
    return f"{base_name} Readback"


def single_knob_axis_summary_text(axis_source: str) -> str:
    if axis_source == "target":
        return "Sensitivity uses target knob values on the x-axis."
    return "Sensitivity uses knob readback on the x-axis, with target fallback if readback is unavailable."


def single_knob_step_axis_value(axis_source: str, step: ScanStepRecord) -> float | None:
    if axis_source == "target":
        return step.target_value
    return step.readback_value if step.readback_value is not None else step.target_value


def estimate_series_sample_interval(timestamps: Iterable[datetime]) -> float:
    rows = list(timestamps)
    if len(rows) < 2:
        raise ValueError("At least two timestamps are required.")
    deltas = []
    for left, right in zip(rows, rows[1:]):
        delta = (right - left).total_seconds()
        if delta > 0.0:
            deltas.append(delta)
    if not deltas:
        raise ValueError("No positive timestamp deltas are available.")
    deltas.sort()
    mid = len(deltas) // 2
    if len(deltas) % 2 == 1:
        return float(deltas[mid])
    return float((deltas[mid - 1] + deltas[mid]) / 2.0)


def estimate_series_sample_interval_from_sample_indices(
    sample_indices,
    sample_timestamps: list[datetime | None],
) -> float:
    timestamps = []
    for sample_index in sample_indices:
        if sample_index is None:
            continue
        index = int(sample_index)
        if 0 <= index < len(sample_timestamps):
            timestamp = sample_timestamps[index]
            if timestamp is not None:
                timestamps.append(timestamp)
    return estimate_series_sample_interval(timestamps)
