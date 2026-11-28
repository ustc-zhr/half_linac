"""ICT1 stable windows and scan-level statistics for the InfoBAX workflow."""

import json
import math
import re
import time

import numpy as np

from half_linac.src.optimization.infobax import ICT1Config

from .measurement_service import ScanChargeMonitor, trim_charge_values
from .optimization import ChargeConstraint


def monitor_settings(paras, constraint):
    from half_linac.src.shared.machine_profile import (
        load_app_context, resolve_channel, resolve_ct_monitor_workflow,
    )

    constraint.validate()
    if (constraint.element_id, constraint.channel) != ("ICT01", "charge"):
        raise ValueError("InfoBAX supports the ICT1 charge channel.")
    context = paras.app_context
    backend = context.control_backend.name
    ct_context = load_app_context("ct_monitor", machine_id=context.profile.machine.id, control_backend=backend)
    workflow = resolve_ct_monitor_workflow(ct_context.profile)
    return {
        **constraint.to_dict(),
        "pv": resolve_channel(context, constraint.element_id, constraint.channel),
        "unit": workflow["measurement_unit"],
        "scale": float(workflow["scale_to_display_unit"][backend]),
        "stale_timeout_s": workflow["stale_timeout_s"][backend],
        "sample_count": paras.samples,
        "sample_interval_s": max(paras.sample_interval, workflow["refresh_interval_ms"] / 1000),
        "minimum_samples": 3,
        "minimum_valid_fraction": 0.8,
    }


def charge_summary(monitor):
    summary = monitor.summary()
    summary.pop("samples")
    if summary["status"] == "valid":
        valid = [sample["value"] for sample in monitor.samples if sample["status"] == "valid"]
        _, used, _ = trim_charge_values(valid, monitor.settings["trim_fraction"])
        scatter = float(np.std(used, ddof=1)) if len(used) > 1 else 0.0
        summary.update(trimmed_standard_deviation_nc=scatter, standard_error_nc=scatter / math.sqrt(len(used)))
    return summary


def read_ict1_window(directory, window_id):
    if not isinstance(window_id, str) or re.fullmatch(r"[a-f0-9]{32}", window_id) is None:
        raise ValueError("Malformed ICT1 window ID")
    window = json.loads((directory / f"{window_id}.json").read_text())
    if window["window_id"] != window_id:
        raise ValueError("ICT1 window ID does not match its file")
    return window


def scan_charge_summary(settings, windows, *, connection_error=None):
    monitor = ScanChargeMonitor(settings)
    monitor.samples = [sample for window in windows for sample in window["samples"]]
    monitor.attempts = len(monitor.samples)
    monitor.connection_error = connection_error
    return charge_summary(monitor)


def numerical_ict1_config(settings, baseline):
    count = settings["sample_count"]
    used = count - 2 * math.floor(count * settings["trim_fraction"])
    noise = baseline["trimmed_standard_deviation_nc"] / (baseline["value"] * math.sqrt(used))
    constraint = ChargeConstraint.from_settings(settings)
    return ICT1Config(baseline["value"], constraint.retention, max(0.001, noise),
                      absolute_minimum_nc=constraint.minimum_nc)


class ICT1WindowSampler:
    def __init__(self, settings):
        self.settings = settings
        self.pv = None

    def capture(self, window, check):
        monitor = ScanChargeMonitor(self.settings)
        window["samples"] = monitor.samples
        check()
        if self.pv is None:
            import epics

            monitor.prepare(epics.PV)
            self.pv = monitor.pv
        monitor.pv = self.pv
        window["opened_at"] = time.time()
        previous_timestamp = None
        for _ in range(self.settings["sample_count"]):
            # Wait for the first fresh update too. An immediate get can only
            # reproduce the value held in the IOC before the window opened.
            end = time.monotonic() + self.settings["sample_interval_s"]
            while (remaining := end - time.monotonic()) > 0:
                check()
                time.sleep(min(0.05, remaining))
            check()
            sample = monitor.read(window_opened_at=window["opened_at"], after_timestamp=previous_timestamp)
            if sample["status"] == "valid":
                previous_timestamp = sample["source_timestamp"]
        window["summary"] = charge_summary(monitor)
