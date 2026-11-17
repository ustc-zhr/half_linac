from dataclasses import replace
import json
from pathlib import Path
import subprocess
from unittest.mock import patch

import numpy as np
import pytest

from half_linac.src.shared.digital_shadow import ShadowRequest, run_once
from half_linac.src.shared.digital_shadow.example import write_example
from half_linac.src.shared.digital_shadow.pipeline import prepare_model
from half_linac.src.shared.elegant_runtime import run_elegant_input
from half_linac.src.shared.machine_profile.loader import load_model_context
from half_linac.src.shared.machine_profile.models import ControlBackendConfig, MachineProfileError
from half_linac.src.shared.machine_profile.model_snapshot import build_model_snapshot_from_machine_state
from half_linac.src.shared.machine_state import SampleQuality, build_profile_signature, load_snapshot
from half_linac.src.shared.machine_profile.resolver import resolve_channel


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Offline shadow must not access PVs")
    for name in ("caget", "caput", "PV", "caget_many", "caput_many"):
        monkeypatch.setattr(f"epics.{name}", forbidden)
    directory = write_example(tmp_path / "inputs")
    snapshot = load_snapshot(directory / "machine_snapshot.json")
    request = ShadowRequest.from_dict(json.loads((directory / "request.json").read_text()))
    context = replace(load_model_context("half"), control_backend=ControlBackendConfig("real"))
    return context, snapshot, request


def change_entry(snapshot, key, **updates):
    return replace(snapshot, entries=tuple(replace(entry, **updates) if entry.key == key else entry for entry in snapshot.entries))


@pytest.fixture
def fake_model(monkeypatch):
    calls = []
    class Backend:
        def get_map(self, source, target, **kwargs):
            calls.append((source, target, kwargs))
            matrix = np.eye(6)
            matrix[0, 1] = 3 + kwargs["lattice_overrides"]["QL09"]["K1"]
            matrix[2, 3] = 2
            return matrix
    monkeypatch.setattr("half_linac.src.shared.digital_shadow.pipeline.build_model_backend", lambda *a, **k: Backend())
    return calls


def test_saved_input_uses_readback_and_preserves_setpoint(inputs):
    context, snapshot, request = inputs
    _, fields = prepare_model(context, request)
    model = build_model_snapshot_from_machine_state(context, snapshot, fields,
        logical_channels={field: "K1_total" for field in fields})
    recorded = {entry.key: entry for entry in snapshot.entries}
    assert model.source == "saved" and model.origin_source == "live_from_real"
    for field in model.fields:
        assert field.value == recorded[f"{field.element_id}/K1_total"].value
        assert field.value != recorded[f"{field.element_id}/K1"].value
        assert field.source_pv.endswith(":TOTAL")
        assert field.source_unit == field.model_unit == "1/m^2"


@pytest.mark.parametrize("change", [
    {"unit": "A"}, {"quality": SampleQuality.ALARM}, {"value": True},
    {"pv_name": "WRONG:PV"}, {"native_count": 2}, {"alarm_severity": 2},
])
def test_bad_model_input_fails_without_pv_fallback(inputs, change):
    context, snapshot, _ = inputs
    snapshot = change_entry(snapshot, "QL09/K1_total", **change)
    with pytest.raises(MachineProfileError):
        build_model_snapshot_from_machine_state(context, snapshot, (("QL09", "K1"),),
            logical_channels={("QL09", "K1"): "K1_total"})


@pytest.mark.parametrize("changes", [{"machine_id": "irfel"}, {"backend": "vm"},
                                      {"profile_signature": "changed"}, {"capture_status": "cancelled"}])
def test_capture_identity_and_status_are_required(inputs, changes):
    context, snapshot, _ = inputs
    with pytest.raises(MachineProfileError):
        build_model_snapshot_from_machine_state(context, replace(snapshot, **changes), (("QL09", "K1"),))


def test_missing_readback_never_falls_back_to_setting(inputs):
    context, snapshot, _ = inputs
    snapshot = replace(snapshot, entries=tuple(e for e in snapshot.entries if e.logical_channel != "K1_total"))
    with pytest.raises(MachineProfileError, match="missing QL09/K1_total"):
        build_model_snapshot_from_machine_state(context, snapshot, (("QL09", "K1"),),
            logical_channels={("QL09", "K1"): "K1_total"})


def test_vm_snapshot_uses_its_own_k1_channel(inputs):
    context, snapshot, _ = inputs
    context = replace(context, control_backend=ControlBackendConfig("vm"))
    entries = tuple(replace(e, pv_name=resolve_channel(context, e.element_id, "K1"))
                    for e in snapshot.entries if e.logical_channel == "K1")
    snapshot = replace(snapshot, backend="vm", profile_signature=build_profile_signature(context.profile, "vm"),
                       entries=entries, requested_count=len(entries))
    model = build_model_snapshot_from_machine_state(context, snapshot, (("QL09", "K1"),))
    assert model.origin_source == "live_from_vm"
    assert model.fields[0].value == next(e.value for e in entries if e.key == "QL09/K1")


def test_request_rejects_string_fitted_diagnostics(inputs):
    _, _, request = inputs
    payload = request.as_metadata()
    payload["beam"]["fitted_diagnostics"] = "PRF03/sigx"
    with pytest.raises(ValueError, match="array of diagnostic keys"):
        ShadowRequest.from_dict(payload)


def test_replay_isolated_and_bound_to_recorded_input(inputs, fake_model, tmp_path):
    context, snapshot, request = inputs
    a = run_once(context, snapshot, request, tmp_path / "runs")
    b = run_once(context, snapshot, request, tmp_path / "runs")
    assert a["status"] == "complete"
    assert a["run_dir"] != b["run_dir"] and a["input_hash"] == b["input_hash"]
    assert len(fake_model) == 2  # One target, shared by the two planes in each run.
    row = a["comparisons"][0]
    k1 = next(e.value for e in snapshot.entries if e.key == "QL09/K1_total")
    expected = np.sqrt(request.x_moments[0] + (3 + k1)**2 * request.x_moments[2]) * 1000
    assert row["predicted"] == pytest.approx(expected)
    assert row["residual"] == pytest.approx(row["measured"] - expected)
    manifest = json.loads((Path(a["run_dir"]) / "manifest.json").read_text())
    assert manifest["snapshot_id"] == snapshot.snapshot_id
    assert manifest["beam_basis"] == "assumed" and manifest["mode"] == "offline_replay"
    assert "machine_snapshot.json" in manifest["artifacts"]
    assert "inputs/evaluated_state.json" in manifest["artifacts"]
    emitted = json.loads((Path(a["run_dir"]) / "inputs/evaluated_state.json").read_text())
    expected_p = np.sqrt(request.energy_mev * (request.energy_mev + 2 * 0.51099895)) / 0.51099895
    assert float(emitted["control"]["run_setup"]["p_central"]) == pytest.approx(expected_p)
    changed = change_entry(snapshot, "QL09/K1_total", value=k1 + 0.1)
    c = run_once(context, changed, request, tmp_path / "runs")
    assert c["input_hash"] != a["input_hash"]
    assert c["comparisons"][0]["predicted"] != row["predicted"]


@pytest.mark.parametrize("change", [{"unit": "pixel"}, {"quality": SampleQuality.DISCONNECTED},
                                    {"source_timestamp": 0}, {"received_at": None}])
def test_unavailable_diagnostics_have_no_numeric_residual(inputs, fake_model, tmp_path, change):
    context, snapshot, request = inputs
    result = run_once(context, change_entry(snapshot, "PRF03/sigx", **change), request, tmp_path)
    row = result["comparisons"][0]
    assert result["status"] == "partial" and row["status"] == "unavailable"
    assert row["residual"] is None and row["measured"] is None and row["detail"]


def test_failed_run_has_manifest_and_no_stale_success(inputs, tmp_path):
    context, snapshot, request = inputs
    with patch("half_linac.src.shared.digital_shadow.pipeline.build_model_backend") as factory:
        factory.return_value.get_map.side_effect = RuntimeError("calculation failed")
        with pytest.raises(RuntimeError, match="calculation failed"):
            run_once(context, snapshot, request, tmp_path)
    directory = next(tmp_path.glob("shadow_*"))
    assert not (directory / "result.json").exists()
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["status"] == "failed" and manifest["error"]["message"] == "calculation failed"


def test_coupled_matrix_is_rejected(inputs, tmp_path):
    context, snapshot, request = inputs
    matrix = np.eye(6)
    matrix[0, 2] = 0.1
    with patch("half_linac.src.shared.digital_shadow.pipeline.build_model_backend") as factory:
        factory.return_value.get_map.return_value = matrix
        with pytest.raises(RuntimeError, match="six-dimensional beam input"):
            run_once(context, snapshot, request, tmp_path)


def test_capture_window_and_in_sample_validation(inputs, fake_model, tmp_path):
    context, snapshot, request = inputs
    with pytest.raises(RuntimeError, match="Capture duration"):
        run_once(context, snapshot, replace(request, max_capture_duration_s=0.1), tmp_path)
    with pytest.raises(ValueError, match="independent diagnostic"):
        replace(request, fitted_diagnostics=("PRF03/sigx",))
    with pytest.raises(ValueError, match="positive definite"):
        replace(request, x_moments=(1, 2, 1))


def test_unsupported_segment_rejected_before_calculation(inputs):
    context, _, request = inputs
    with pytest.raises(ValueError, match="Unsupported shadow element"):
        prepare_model(context, replace(request, model_line="ALL_MAIN",
            diagnostics=(replace(request.diagnostics[0], element="PRF04"),)))


def test_elegant_timeout_is_explicit_and_backward_compatible(tmp_path):
    with patch("half_linac.src.shared.elegant_runtime.subprocess.run") as run:
        run_elegant_input("one.ele", "one.log", workdir=tmp_path)
        assert "timeout" not in run.call_args.kwargs
        run.side_effect = subprocess.TimeoutExpired("elegant", 0.1)
        with pytest.raises(RuntimeError, match="timed out"):
            run_elegant_input("one.ele", "one.log", workdir=tmp_path, timeout_s=0.1)
        assert run.call_args.kwargs["timeout"] == 0.1
