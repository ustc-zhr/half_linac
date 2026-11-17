"""Compare recorded scalar diagnostics with predictions at the recorded point."""
import math

from half_linac.src.shared.machine_profile.resolver import resolve_channel
from half_linac.src.shared.machine_state import SampleQuality

from .models import timestamp


def compare_diagnostics(context, snapshot, request, predictions_m):
    entries = {entry.key: entry for entry in snapshot.entries}
    started = timestamp(snapshot.capture_started_at)
    finished = timestamp(snapshot.capture_finished_at)
    rows = []
    for diagnostic in request.diagnostics:
        row = {"key": diagnostic.key, "unit": diagnostic.unit, "width_definition": "rms",
               "predicted": predictions_m[diagnostic.key] * (1000 if diagnostic.unit == "mm" else 1),
               "measured": None, "residual": None, "status": "unavailable", "detail": "",
               "source_timestamp": None, "received_at": None}
        entry = entries.get(diagnostic.key)
        try:
            if entry is None:
                raise ValueError("Diagnostic absent from the recorded snapshot")
            row.update(source_timestamp=entry.source_timestamp, received_at=entry.received_at)
            if entry.pv_name != resolve_channel(context, diagnostic.element, diagnostic.channel):
                raise ValueError("Diagnostic PV mapping differs from the current profile")
            if entry.quality != SampleQuality.OK or (entry.alarm_severity or 0) > 0:
                raise ValueError(f"Diagnostic quality is {entry.quality.value}")
            if entry.unit != diagnostic.unit:
                raise ValueError("Diagnostic unit differs from the explicitly declared unit")
            if entry.native_count != 1 or entry.value_type != "number" or isinstance(entry.value, bool):
                raise ValueError("Diagnostic is not a scalar number")
            value = float(entry.value)
            if not math.isfinite(value) or value <= 0:
                raise ValueError("RMS width must be positive and finite")
            if entry.source_timestamp is None or not math.isfinite(entry.source_timestamp):
                raise ValueError("Diagnostic source timestamp is missing")
            if not started - request.diagnostic_max_age_s <= entry.source_timestamp <= finished:
                raise ValueError("Diagnostic timestamp lies outside the capture age window")
            if entry.received_at is None or not started <= timestamp(entry.received_at) <= finished:
                raise ValueError("Diagnostic receipt timestamp lies outside the capture window")
            row.update(measured=value, residual=value - row["predicted"], status="compared")
        except (ValueError, TypeError) as exc:
            row["detail"] = str(exc)
        rows.append(row)
    return rows
