"""Real-compatible capture and replay with injected readers; no live CA."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from half_linac.src.shared.digital_shadow import run_once
from half_linac.src.shared.digital_shadow.real_capture import (
    build_real_capture_plan, capture_real_once, verify_real_evidence,
)
from half_linac.src.shared.digital_shadow.real_demo import demo_request, SimulatedRealReader
from half_linac.src.shared.machine_profile.loader import load_model_context
from half_linac.src.shared.machine_profile.models import ControlBackendConfig
from half_linac.src.shared.machine_profile.resolver import resolve_channel
from half_linac.src.shared.machine_state import snapshot_to_dict


@pytest.fixture
def data(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No live PV access during offline real verification")
    for name in ("PV", "get_pv", "caget", "caput", "caget_many", "caput_many"):
        monkeypatch.setattr("epics." + name, forbidden)
    monkeypatch.setattr("epics.ca.create_channel", forbidden)
    monkeypatch.setattr("epics.ca.poll", forbidden)
    context = replace(load_model_context("half"), control_backend=ControlBackendConfig("real"))
    request = demo_request()
    reader = SimulatedRealReader(context, request)
    return SimpleNamespace(context=context, request=request, reader=reader)


def capture(data, factory=None):
    return capture_real_once(data.context, data.request, pv_factory=factory or data.reader,
                             poll_function=lambda interval: None, synthetic=True)


@pytest.fixture
def model(monkeypatch):
    calls = []
    def backend(*args, **kwargs):
        def get_map(*args, **kwargs):
            calls.append(kwargs)
            return np.eye(6)
        return SimpleNamespace(get_map=get_map)
    monkeypatch.setattr("half_linac.src.shared.digital_shadow.pipeline.build_model_backend", backend)
    return calls


def test_all_quadrupoles_are_inspected_without_current_fallback(data):
    points, scope = build_real_capture_plan(data.context, data.request)
    assert len(points) == 140
    assert scope["normalized_quadrupole_count"] == 66
    assert {item["element"] for item in scope["missing_normalized_quadrupoles"]} == {"QDU01", "QDU02"}
    assert not any("current" in point.endpoint.logical_channel for point in points)
    snapshot, evidence = capture(data)
    assert len(data.reader.reads) == len(set(data.reader.reads)) == 140
    assert snapshot.backend == "real" and snapshot.snapshot_id.startswith("synthetic_")
    assert evidence["reader_kind"] == "synthetic" and not evidence["same_bunch_confirmed"]
    assert {e.unit for e in snapshot.entries} == {"m^-2", "mm"}


def test_cli_generates_a_four_screen_real_demo_request_without_reading_pvs(data, tmp_path):
    import runpy
    root = Path(__file__).resolve().parents[1]
    main = runpy.run_path(str(root / "scripts/run_digital_shadow.py"))["main"]
    directory = tmp_path / "example"
    assert main(["example", "--real-demo", "--output-dir", str(directory)]) == 0
    request = json.loads((directory / "request.json").read_text())
    assert len(request["diagnostics"]) == 8 and "SYNTHETIC" in request["beam"]["provenance"]
    assert not (directory / "machine_snapshot.json").exists()
    assert not data.reader.reads


def test_live_real_requires_explicit_permission_and_demo_cannot_go_live(data):
    with pytest.raises(ValueError, match="explicit allow_live"):
        capture_real_once(data.context, data.request)
    with pytest.raises(ValueError, match="synthetic demo"):
        capture_real_once(data.context, data.request, allow_live=True)
    with pytest.raises(ValueError, match="injected poll_function"):
        capture_real_once(data.context, data.request, pv_factory=data.reader)
    assert not data.reader.reads


def test_wrong_backend_is_refused_before_reader(data):
    with pytest.raises(ValueError, match="explicit HALF real"):
        capture_real_once(replace(data.context, control_backend=ControlBackendConfig("vm")),
                           data.request, pv_factory=data.reader, poll_function=lambda interval: None)
    assert not data.reader.reads


@pytest.mark.parametrize("fault", ["vm_alias", "missing_readback"])
def test_required_mapping_fault_is_refused_before_sampling(data, fault):
    quad = data.context.profile.get_element("QT01")
    channels = deepcopy(quad.channels)
    if fault == "vm_alias": channels["K1"]["real"] = channels["K1"]["vm"]
    else: channels["K1_total"].pop("real")
    changed = replace(quad, channels=channels)
    profile = replace(data.context.profile,
        elements=tuple(changed if e.id == quad.id else e for e in data.context.profile.elements),
        _elements_by_id={**data.context.profile._elements_by_id, quad.id: changed})
    with pytest.raises(ValueError):
        capture_real_once(replace(data.context, profile=profile), data.request,
                           pv_factory=data.reader, poll_function=lambda interval: None)
    assert not data.reader.reads


def test_raw_units_and_readbacks_survive_model_derivation(data, model, tmp_path):
    snapshot, evidence = capture(data)
    raw = snapshot_to_dict(snapshot)
    result = run_once(data.context, snapshot, data.request, tmp_path, real_evidence=evidence)
    assert result["status"] == "partial" and result["prediction_status"] == "complete"
    assert result["synthetic"] and len(result["comparisons"]) == 8
    assert all(row["status"] == "incompatible" and row["measured"] is not None and row["residual"] is None for row in result["comparisons"])
    assert snapshot_to_dict(snapshot) == raw
    assert len(model) == 4
    recorded = {entry.key: entry for entry in snapshot.entries}
    for quad in ("QT01", "QT02", "QT03", "QT04"):
        assert model[0]["lattice_overrides"][quad]["K1"] == recorded[quad + "/K1_total"].value
        assert model[0]["lattice_overrides"][quad]["K1"] != recorded[quad + "/K1"].value
    directory = Path(result["run_dir"])
    assert json.loads((directory / "machine_snapshot.json").read_text()) == json.loads(json.dumps(raw))
    manifest = json.loads((directory / "manifest.json").read_text())
    assert len(manifest["unit_normalizations"]) == 4
    assert "model_input_snapshot.json" in manifest["artifacts"]
    model_metadata = json.loads((directory / "model_snapshot.json").read_text())["extra_metadata"]
    assert model_metadata["synthetic"] and model_metadata["reader_kind"] == "synthetic"
    again = run_once(data.context, snapshot, data.request, tmp_path, real_evidence=evidence)
    assert again["input_hash"] == result["input_hash"]


@pytest.mark.parametrize("fault", ["unit", "alarm", "disconnected", "future", "array"])
def test_required_readback_fault_prevents_any_model_run(data, model, tmp_path, fault):
    target = resolve_channel(data.context, "QT01", "K1_total", "real")
    def factory(name):
        pv = data.reader(name)
        original = pv.get_with_metadata
        def read(**kwargs):
            value = original(**kwargs)
            if name == target:
                if fault == "unit": value["units"] = "A"
                elif fault == "alarm": value["severity"] = 2
                elif fault == "future": value["timestamp"] += 100
                elif fault == "array": value.update(value=[1, 2], count=2)
            return value
        pv.get_with_metadata = read
        if name == target and fault == "disconnected": pv.connected = False
        return pv
    request = replace(data.request, max_capture_duration_s=0.02) if fault == "disconnected" else data.request
    data.request = request
    snapshot, evidence = capture(data, factory)
    with pytest.raises(RuntimeError):
        run_once(data.context, snapshot, request, tmp_path, real_evidence=evidence)
    assert not model
    manifest = json.loads(next(tmp_path.glob("shadow_*/manifest.json")).read_text())
    assert manifest["status"] == "failed"
    assert "machine_snapshot.json" in manifest["artifacts"]


def test_unrelated_global_readback_failure_does_not_block_local_prediction(data, model, tmp_path):
    target = resolve_channel(data.context, "QL01", "K1_total", "real")
    number, _ = data.reader.values[target]
    data.reader.values[target] = number, "A"
    snapshot, evidence = capture(data)
    result = run_once(data.context, snapshot, data.request, tmp_path, real_evidence=evidence)
    assert result["capture_status"] == "partial" and result["prediction_status"] == "complete"
    assert len(model) == 4


def test_unrelated_disconnection_cannot_starve_required_local_readbacks(data, model, tmp_path):
    target = resolve_channel(data.context, "QL01", "K1_total", "real")
    def factory(name):
        pv = data.reader(name)
        if name == target: pv.connected = False
        return pv
    snapshot, evidence = capture_real_once(data.context, data.request, timeout_s=0.02,
        pv_factory=factory, poll_function=lambda interval: None, synthetic=True)
    result = run_once(data.context, snapshot, data.request, tmp_path, real_evidence=evidence)
    assert result["capture_status"] == "partial" and result["prediction_status"] == "complete"
    assert len(model) == 4


def test_real_evidence_is_bound_and_cannot_relabel_sigma(data, model, tmp_path):
    snapshot, evidence = capture(data)
    with pytest.raises(ValueError, match="does not belong"):
        verify_real_evidence(data.context, replace(snapshot, snapshot_id="different"), data.request, evidence)
    changed = deepcopy(evidence)
    changed["scope"]["diagnostics"]["PRF06/sigx"]["width_definition"] = "rms"
    with pytest.raises(ValueError):
        verify_real_evidence(data.context, snapshot, data.request, changed)
    with pytest.raises(RuntimeError, match="requires captured diagnostic"):
        run_once(data.context, snapshot, data.request, tmp_path)


def test_old_observation_has_no_numeric_accuracy_residual(data, model, tmp_path):
    target = resolve_channel(data.context, "PRF09", "sigx", "real")
    def factory(name):
        pv = data.reader(name)
        original = pv.get_with_metadata
        def read(**kwargs):
            value = original(**kwargs)
            if name == target: value["timestamp"] -= 100
            return value
        pv.get_with_metadata = read
        return pv
    snapshot, evidence = capture(data, factory)
    result = run_once(data.context, snapshot, data.request, tmp_path, real_evidence=evidence)
    row = next(row for row in result["comparisons"] if row["key"] == "PRF09/sigx")
    assert row["status"] == "unavailable" and row["residual"] is None and row["measured"] is None
