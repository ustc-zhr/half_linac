from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from half_linac.src.shared.digital_shadow.example import write_example
from half_linac.src.shared.digital_shadow.models import ShadowRequest
from half_linac.src.shared.digital_shadow.pipeline import prepare_model
from half_linac.src.shared.digital_shadow.session import run_once
from half_linac.src.shared.digital_shadow.vm_capture import (
    build_vm_capture_plan, capture_vm_once, verify_vm_evidence,
)
from half_linac.src.shared.elegant_backend import VmPublisher, VmPublishPlan, VmWatchRmsPublishSpec, build_vm_publish_plan
from half_linac.src.shared.elegant_backend.parser import _load_watch_rms_from_sdds
from half_linac.src.shared.machine_profile.loader import load_model_context
from half_linac.src.shared.machine_profile.models import ControlBackendConfig
from half_linac.src.shared.runtime_state import read_runtime_state, write_runtime_state
from half_linac.src.virtual_machine.workbench_data import input_version, observation_dir


@pytest.fixture
def vm_inputs(tmp_path, monkeypatch):
    directory = write_example(tmp_path / "example", vm=True)
    request = ShadowRequest.from_dict(json.loads((directory / "request.json").read_text()))
    context = load_model_context("half")
    state, _ = prepare_model(context, request)
    runtime = tmp_path / "runtime.json"
    write_runtime_state(runtime, state)
    completed = datetime.now(timezone.utc) - timedelta(seconds=60)
    version = input_version(state)
    result = {"input_version": version, "input_state": state, "session": "vm-test", "calculation": 7,
              "screens": {"2": {"name": "PRF06", "sx": 1.2, "sy": 1.3}}}
    status = {"phase": "Ready", "session": "vm-test", "calculation": 7,
              "input_version": version, "result_version": version,
              "publication": {"rms": True}, "completed_at": completed.isoformat(), "elapsed": 2.0}
    write_runtime_state(observation_dir(runtime) / "result.json", result)
    write_runtime_state(observation_dir(runtime) / "status.json", status)
    points, _ = build_vm_capture_plan(context, request)
    values = {points[0].endpoint.pv_name: float(state["lattice"]["QT02"]["K1"]),
              points[1].endpoint.pv_name: 1.2, points[2].endpoint.pv_name: 1.3}
    created = []

    class Pv:
        connected = True
        count = 1
        units = ""
        def __init__(self, name):
            self.name = name
            self.read_count = 0
            created.append(self)
        def get_with_metadata(self, **kwargs):
            assert kwargs["use_monitor"] is False
            self.read_count += 1
            return {"value": values[self.name], "timestamp": completed.timestamp(), "units": self.units,
                    "status": 0, "severity": 0, "count": 1}
        def put(self, *args, **kwargs):
            raise AssertionError("VM capture must never write PVs")
        def clear_callbacks(self):
            pass
        def disconnect(self):
            pass

    def no_write(*args, **kwargs):
        raise AssertionError("VM capture must never write PVs")
    monkeypatch.setattr("epics.caput", no_write)
    monkeypatch.setattr("epics.caput_many", no_write)
    return SimpleNamespace(context=context, request=request, runtime=runtime, state=state,
                           status=status, result=result, factory=Pv, values=values, created=created)


def capture(data, **kwargs):
    return capture_vm_once(data.context, data.request, runtime_json=data.runtime,
                           pv_factory=kwargs.pop("pv_factory", data.factory),
                           poll_function=lambda interval: None, ready_timeout_s=0.01, **kwargs)


def test_vm_capture_reads_once_and_binds_existing_result(vm_inputs, tmp_path):
    snapshot, evidence = capture(vm_inputs)
    assert snapshot.backend == "vm" and snapshot.consistency == "vm_result_matched"
    assert len(vm_inputs.created) == 3 and all(pv.read_count == 1 for pv in vm_inputs.created)
    assert {e.unit for e in snapshot.entries} == {"1/m^2", "mm"}
    assert all("VM publication contract" in e.detail for e in snapshot.entries)
    assert evidence["status"]["input_version"] == input_version(vm_inputs.state)
    backend = SimpleNamespace(get_map=lambda *a, **k: np.eye(6))
    with patch("half_linac.src.shared.digital_shadow.pipeline.build_model_backend", return_value=backend):
        result = run_once(vm_inputs.context, snapshot, vm_inputs.request, tmp_path / "runs", vm_evidence=evidence)
        assert result["status"] == "complete"  # Old publication is valid for the unchanged input version.
        ordinary = run_once(vm_inputs.context, snapshot, vm_inputs.request, tmp_path / "runs")
        assert ordinary["status"] == "partial"  # No relaxation without explicit evidence.
    manifest = read_runtime_state(Path(result["run_dir"]) / "manifest.json")
    assert manifest["mode"] == "vm_capture_replay"
    assert "vm_capture_evidence.json" in manifest["artifacts"]
    assert len(vm_inputs.created) == 3  # Neither calculation nor replay reconnects.


def test_real_context_is_refused_before_connection(vm_inputs):
    context = replace(vm_inputs.context, control_backend=ControlBackendConfig("real"))
    with pytest.raises(ValueError, match="real capture is not available"):
        capture_vm_once(context, vm_inputs.request, pv_factory=vm_inputs.factory)
    assert not vm_inputs.created


def test_real_alias_is_refused_before_connection(vm_inputs):
    context = vm_inputs.context
    quad = context.profile.get_element("QT02")
    channels = deepcopy(quad.channels)
    channels["K1"]["vm"] = channels["K1"]["real"]
    changed = replace(quad, channels=channels)
    profile = replace(context.profile, elements=tuple(changed if e.id == quad.id else e for e in context.profile.elements),
                      _elements_by_id={**context.profile._elements_by_id, quad.id: changed})
    with pytest.raises(ValueError, match="also mapped to real"):
        build_vm_capture_plan(replace(context, profile=profile), vm_inputs.request)
    assert not vm_inputs.created


@pytest.mark.parametrize("change", ["calculating", "missing_rms", "wrong_version", "wrong_session"])
def test_unready_vm_never_opens_a_pv(vm_inputs, change):
    status = vm_inputs.status.copy()
    if change == "calculating":
        status["phase"] = "Calculating"
    elif change == "missing_rms":
        status["publication"] = {}
    elif change == "wrong_version":
        status["result_version"] = "old"
    else:
        status["session"] = "another-vm"
    write_runtime_state(observation_dir(vm_inputs.runtime) / "status.json", status)
    with pytest.raises(RuntimeError, match="did not become ready"):
        capture(vm_inputs)
    assert not vm_inputs.created


def test_vm_change_during_capture_is_refused(vm_inputs):
    def factory(name):
        state = deepcopy(vm_inputs.state)
        state["lattice"]["QT02"]["K1"] = "99"
        write_runtime_state(vm_inputs.runtime, state)
        return vm_inputs.factory(name)
    with pytest.raises(RuntimeError, match="current input version"):
        capture(vm_inputs, pv_factory=factory)


def test_capture_and_completed_vm_values_must_match(vm_inputs):
    first = next(iter(vm_inputs.values))
    vm_inputs.values[first] += 0.1
    with pytest.raises(ValueError, match="does not match"):
        capture(vm_inputs)


def test_vm_unit_conflict_is_not_silently_overwritten(vm_inputs):
    vm_inputs.factory.units = "A"
    with pytest.raises(ValueError, match="unusable VM sample"):
        capture(vm_inputs)


def test_vm_evidence_cannot_be_used_for_another_snapshot(vm_inputs):
    snapshot, evidence = capture(vm_inputs)
    with pytest.raises(ValueError, match="does not belong"):
        verify_vm_evidence(vm_inputs.context, replace(snapshot, snapshot_id="another"), vm_inputs.request, evidence)


def test_diagnostic_with_old_publication_timestamp_is_rejected(vm_inputs):
    original = vm_inputs.factory.get_with_metadata
    def wrong_time(self, **kwargs):
        metadata = original(self, **kwargs)
        metadata["timestamp"] -= 100
        return metadata
    with patch.object(vm_inputs.factory, "get_with_metadata", wrong_time):
        with pytest.raises(ValueError, match="not published during"):
            capture(vm_inputs)


def test_rms_reader_uses_named_columns_last_page_and_mm():
    dataset = SimpleNamespace(columnName=["y", "x"], columnData=[[[9], [-2e-3, 2e-3]], [[9], [-1e-3, 1e-3]]], load=lambda path: None)
    with patch("half_linac.src.shared.elegant_backend.parser._new_legacy_sdds_dataset", return_value=dataset):
        assert _load_watch_rms_from_sdds(Path("unused.out")) == pytest.approx((1.0, 2.0))


@pytest.mark.parametrize("values", [[], [1.0], [float("nan"), 1.0], [1.0, 1.0]])
def test_rms_reader_rejects_invalid_particles(values):
    dataset = SimpleNamespace(columnName=["x", "y"], columnData=[[values], [values]], load=lambda path: None)
    with patch("half_linac.src.shared.elegant_backend.parser._new_legacy_sdds_dataset", return_value=dataset):
        with pytest.raises(RuntimeError):
            _load_watch_rms_from_sdds(Path("unused.out"))


def test_rms_publisher_waits_and_invalidates_disabled_output():
    spec = VmWatchRmsPublishSpec("PRF06", "VM:X", "VM:Y")
    plan = VmPublishPlan(watch_rms_specs=(spec,))
    publisher = VmPublisher()
    element = {"TYPE": "WATCH", "MODE": "coord", "DISABLE": "0"}
    with patch("half_linac.src.shared.elegant_backend.publisher._load_watch_rms_from_sdds", return_value=(1.0, 2.0)), \
         patch("half_linac.src.shared.elegant_backend.publisher.caput", return_value=1) as put:
        assert publisher.publish_watch_rms(plan, lattice={"PRF06": element}, usedline=["PRF06"], elegant_dir=".")
        assert all(call.kwargs["wait"] is True for call in put.call_args_list)
        assert [call.args[1] for call in put.call_args_list] == [1, 2]
        put.reset_mock()
        element["DISABLE"] = "1"
        assert not publisher.publish_watch_rms(plan, lattice={"PRF06": element}, usedline=["PRF06"], elegant_dir=".")
        assert all(np.isnan(call.kwargs["value"]) if "value" in call.kwargs else np.isnan(call.args[1]) for call in put.call_args_list)


def test_publish_plan_uses_only_existing_vm_width_channels():
    context = load_model_context("half")
    specs = {spec.source_watch_id: spec for spec in build_vm_publish_plan(context.profile).watch_rms_specs}
    assert {"PRF06", "PRF07", "PRF08", "PRF09"}.issubset(specs)
    assert "PRF03" not in specs  # Image-only VM screen must not acquire invented scalar PVs.
    assert specs["PRF06"].x_pv == context.profile.get_element("PRF06").channels["sigx"]["vm"]
    real = {mapping["real"] for element in context.profile.elements
            for mapping in element.channels.values() if "real" in mapping}
    assert all(name not in real for spec in specs.values() for name in (spec.x_pv, spec.y_pv) if name)


def test_vm_width_publish_aliases_are_generated_by_the_ioc(tmp_path, monkeypatch):
    from half_linac.src.softIOC.pv_server import pv_server

    def forbidden(*args, **kwargs):
        raise AssertionError("Generating IOC configuration must not access PVs")

    for name in ("PV", "caget", "caput", "caget_many", "caput_many"):
        monkeypatch.setattr("epics." + name, forbidden)
    server = pv_server(tmp_path / "unused.json", tmp_path / "ioc", machine_id="half")
    server.substitutions_path = tmp_path / "db/runtime.substitutions"
    server.gen_substitution_file()
    text = server.substitutions_path.read_text()
    specs = {spec.source_watch_id: spec for spec in build_vm_publish_plan(server.machine_profile).watch_rms_specs}
    for screen in ("PRF06", "PRF07", "PRF08", "PRF09"):
        for channel, alias in (("sigx", specs[screen].x_pv), ("sigy", specs[screen].y_pv)):
            assert f'{{ "{screen}", "VMIOC:FLAG:{screen}:{channel.upper()}", "{alias}" }}' in text


def test_vm_update_records_rms_publication_for_the_frozen_input(tmp_path):
    from half_linac.src.virtual_machine.common import start_VM

    frozen = tmp_path / "input.json"
    write_runtime_state(frozen, {"usedline": ["QT02", "PRF06"]})
    parser = SimpleNamespace(lattice={"PRF06": {"TYPE": "WATCH"}})
    plan = VmPublishPlan(watch_rms_specs=(VmWatchRmsPublishSpec("PRF06", "VM:X", "VM:Y"),))
    with patch.object(start_VM, "_run_elegant"), \
         patch.object(VmPublisher, "publish_watch_rms", return_value=True) as publish:
        parser.json_to_lte_ele = lambda *args: None
        result = start_VM._update_vm_outputs(parser, VmPublisher(), plan, tmp_path, frozen)
    assert result == {"rms": True}
    assert publish.call_args.kwargs["usedline"] == ["QT02", "PRF06"]
    assert publish.call_args.kwargs["lattice"] == parser.lattice
