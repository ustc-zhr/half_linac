"""Explicitly synthetic saved inputs for an offline plumbing demonstration."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid

from half_linac.src.shared.machine_profile.loader import load_model_context
from half_linac.src.shared.machine_profile.models import ControlBackendConfig
from half_linac.src.shared.machine_profile.resolver import resolve_channel
from half_linac.src.shared.machine_state import (
    MachineStateSnapshot, SnapshotEntry, StateClass, SampleQuality,
    build_profile_signature, save_snapshot,
)
from half_linac.src.shared.runtime_state import write_runtime_state

from .models import Diagnostic, ShadowRequest
from .pipeline import prepare_model


def write_example(output_dir):
    directory = Path(output_dir).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    context = replace(load_model_context("half"), control_backend=ControlBackendConfig("real"))
    request = ShadowRequest(
        model_line="EMIT_QL09_PRF03", reference_element="QL09", reference_edge="entrance",
        energy_mev=114.15, x_moments=(4e-6, 0, 1e-8), y_moments=(3e-6, 0, 2e-8),
        beam_basis="assumed", beam_provenance="SYNTHETIC example; not a real measurement or calibrated beam",
        diagnostics=(Diagnostic("PRF03", "sigx", "mm", "rms"), Diagnostic("PRF03", "sigy", "mm", "rms")),
    )
    state, fields = prepare_model(context, request)
    now = datetime.now(timezone.utc)
    entries = []
    for element_id, _ in fields:
        value = float(state["lattice"][element_id]["K1"])
        for channel, number, state_class in (("K1", value + 0.2, StateClass.SETTING), ("K1_total", value, StateClass.DERIVED)):
            entries.append((element_id, channel, number, "1/m^2", state_class))
    entries.extend((d.element, d.channel, 1.6, d.unit, StateClass.OBSERVATION) for d in request.diagnostics)
    samples = []
    for element_id, channel, value, unit, state_class in entries:
        element = context.profile.get_element(element_id)
        samples.append(SnapshotEntry(
            key=f"{element_id}/{channel}", element_id=element_id, element_kind=element.kind,
            element_order=element.order, display_name=element.display_name, logical_channel=channel,
            pv_name=resolve_channel(context, element_id, channel), state_class=state_class,
            value=value, value_type="number", unit=unit, source_timestamp=now.timestamp(),
            received_at=now.isoformat(), alarm_status=0, alarm_severity=0, native_count=1,
            quality=SampleQuality.OK, detail="SYNTHETIC: no PV access",
        ))
    snapshot = MachineStateSnapshot(
        snapshot_id=f"synthetic_{uuid.uuid4().hex}", name="SYNTHETIC offline example",
        operator_note="All observations are fabricated to demonstrate replay; residuals have no physical validation meaning",
        machine_id=context.machine.id, machine_display_name=context.machine.display_name, backend="real",
        profile_schema_version=context.profile.schema_version,
        profile_signature=build_profile_signature(context.profile, "real"),
        capture_started_at=(now - timedelta(seconds=1)).isoformat(), capture_finished_at=now.isoformat(),
        capture_status="complete", hostname="synthetic", consistency="synthetic", requested_count=len(samples), entries=tuple(samples),
    )
    save_snapshot(directory / "machine_snapshot.json", snapshot)
    write_runtime_state(directory / "request.json", request.as_metadata())
    return directory
