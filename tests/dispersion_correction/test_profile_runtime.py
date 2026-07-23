from __future__ import annotations

from dataclasses import replace

import pytest

from half_linac.src.apps.dispersion_correction.machine.epics import EpicsMachine
from half_linac.src.apps.dispersion_correction.preflight import run_preflight
from half_linac.src.apps.dispersion_correction import profile_runtime
from half_linac.src.apps.dispersion_correction.profile_runtime import (
    apply_profile_selection,
    load_profile_run_config,
    selectable_profile_bpms,
    selectable_profile_quadrupoles,
)
from half_linac.src.apps.dispersion_correction.models import KnobConfig
from half_linac.src.apps.dispersion_correction.workflow import AchromatWorkflow
from half_linac.src.shared.machine_profile import (
    MachineProfileError,
    build_model_snapshot,
    describe_app_support,
    load_app_context,
    resolve_channel,
)


class FakeEpics:
    def __init__(self, values):
        self.values = dict(values)

    def caget(self, pv, *args, **kwargs):
        return self.values.get(pv)


def test_irfel_vm_profile_opens_as_model_only_without_energy_knob_pvs() -> None:
    context = load_app_context(
        "dispersion_correction",
        machine_id="irfel",
        control_backend="vm",
    )
    _, config = load_profile_run_config(context)

    assert config.backend.type == "offline"
    assert config.backend.mode == "read_only"
    assert config.backend.options["profile_backend"] == "vm"
    assert config.backend.options["pv_map"] == {}
    assert config.section.model_only
    assert config.section.id == "dogleg"
    assert context.model_backend is not None
    with pytest.raises(PermissionError, match="model-only"):
        AchromatWorkflow(config).measure_dispersion()


def test_irfel_real_profile_resolves_write_policy_and_existing_channels() -> None:
    context = load_app_context(
        "dispersion_correction",
        machine_id="irfel",
        control_backend="real",
    )
    _, config = load_profile_run_config(context)

    pv_map = config.backend.options["pv_map"]
    assert config.backend.type == "epics"
    assert config.backend.mode == "read_only"
    assert context.model_backend is not None
    assert config.section.id == "dogleg"
    assert config.section.display_name == "IRFEL Dogleg"
    assert config.section.model_entrance == "BPM07"
    assert config.section.model_exit == "BPM10"
    assert tuple(item.element for item in config.section.model_observables) == (
        "BPM09",
        "BPM10",
    )
    assert tuple(item.component for item in config.section.model_observables) == (
        "dx",
        "dx",
    )
    assert pv_map["bpms"]["BPM09"]["x"] == "IRFEL-BI:BPM09:BPM_PX2"
    assert pv_map["quadrupoles"]["QM13"]["current_set"] == "IRFEL:PS:QM13:current:ao"
    assert pv_map["energy_knob"] == {
        "set": "IRFEL:IN-MW:KLY1:SET_PHASE",
        "readback": "IRFEL:IN-MW:KLY1:GET_CH3_PHASE",
    }
    preflight = run_preflight(config)
    assert preflight.ok
    assert preflight.level == "read-only-ready"
    assert preflight.checks["energy_calibration_available"]
    assert preflight.checks["real_machine_timing_positive"]
    assert not any("no independent readback" in warning for warning in preflight.warnings)


@pytest.mark.parametrize(
    ("backend", "expected_source"),
    (("vm", "live_from_vm"), ("real", "live_from_real")),
)
def test_irfel_dogleg_current_snapshot_maps_all_section_quadrupoles(
    backend,
    expected_source,
) -> None:
    context = load_app_context(
        "dispersion_correction",
        machine_id="irfel",
        control_backend=backend,
    )
    names = tuple(f"QM{index:02d}" for index in range(12, 19))
    pv_values = {
        resolve_channel(context, name, "K1", backend): float(index)
        for index, name in enumerate(names, start=12)
    }

    snapshot = build_model_snapshot(
        context,
        tuple((name, "K1") for name in names),
        source="live",
        pv_reader=pv_values.__getitem__,
    )

    assert snapshot.source == expected_source
    assert snapshot.lattice_overrides == {
        name: {"K1": float(index)}
        for index, name in enumerate(names, start=12)
    }


def test_profile_selection_derives_bpm_and_quad_pvs_from_machine_elements() -> None:
    context = load_app_context(
        "dispersion_correction",
        machine_id="irfel",
        control_backend="real",
    )
    _, config = load_profile_run_config(context)
    knobs = (
        KnobConfig("QM11_QM12_sym", {"QM11": 1.0, "QM12": 1.0}, 0.0005, 0.012),
        KnobConfig("QM17_QM18_sym", {"QM17": 1.0, "QM18": 1.0}, 0.0005, 0.012),
    )

    selected = apply_profile_selection(
        context,
        config,
        target_bpms=("BPM08", "BPM09", "BPM10"),
        knobs=knobs,
    )

    assert selectable_profile_bpms(context) == tuple(f"BPM{index:02d}" for index in range(1, 11))
    assert "QM20" in selectable_profile_quadrupoles(context)
    assert selected.backend.options["pv_map"]["bpms"]["BPM08"]["x"] == (
        "IRFEL-BI:BPM08:BPM_PX2"
    )
    assert selected.backend.options["pv_map"]["quadrupoles"]["QM11"]["current_set"] == (
        "IRFEL:PS:QM11:current:ao"
    )


def test_half_profile_exposes_model_only_bl01_section() -> None:
    supported, reason = describe_app_support("half", "dispersion_correction")

    assert supported
    assert reason is None
    context = load_app_context(
        "dispersion_correction",
        machine_id="half",
        control_backend="vm",
    )
    _, config = load_profile_run_config(context)
    assert context.model_backend is not None
    assert config.backend.type == "offline"
    assert config.backend.mode == "read_only"
    assert config.section.id == "bl01"
    assert config.section.model_only
    assert config.section.model_entrance == "BPM02"
    assert config.target_bpms == ("BPM06", "BPM07")
    assert config.section.target_dispersion_mm == (0.0, 0.0)
    assert tuple(item.component for item in config.section.model_observables) == (
        "dx",
        "dxp",
    )
    assert tuple(item.element for item in config.section.model_observables) == (
        "BPM06",
        "BPM06",
    )
    with pytest.raises(PermissionError, match="model-only"):
        AchromatWorkflow(config).measure_dispersion()


def test_epics_bpm_values_apply_profile_unit_scale() -> None:
    context = load_app_context(
        "dispersion_correction",
        machine_id="irfel",
        control_backend="real",
    )
    _, config = load_profile_run_config(context)
    options = dict(config.backend.options)
    options["bpm_position_scale_to_mm"] = 1000.0
    scaled_config = replace(
        config,
        backend=replace(config.backend, mode="read_only", options=options),
    )
    pv_map = options["pv_map"]
    client = FakeEpics(
        {
            pv_map["bpms"]["BPM09"]["x"]: 0.001,
            pv_map["bpms"]["BPM09"]["y"]: -0.002,
            pv_map["bpms"]["BPM10"]["x"]: 0.003,
            pv_map["bpms"]["BPM10"]["y"]: -0.004,
        }
    )

    reading = EpicsMachine(scaled_config, epics_client=client).read_bpm(config.target_bpms)

    assert reading.x_mm.tolist() == pytest.approx([1.0, 3.0])
    assert reading.y_mm.tolist() == pytest.approx([-2.0, -4.0])


def test_profile_results_use_standard_latest_and_run_directories(tmp_path, monkeypatch) -> None:
    context = load_app_context(
        "dispersion_correction",
        machine_id="irfel",
        control_backend="real",
    )
    config = profile_runtime.default_offline_config()
    result = AchromatWorkflow(config).run()
    monkeypatch.setattr(profile_runtime, "APP_DIR", tmp_path)

    paths = profile_runtime.write_profile_result(context, result)

    assert paths["latest_json"].is_file()
    assert paths["run_markdown"].is_file()
    assert paths["latest_metadata"].is_file()
    assert paths["run_metadata"].is_file()
    assert paths["latest_metadata"].parent == tmp_path / "runtime" / "irfel" / "real" / "latest"


def test_profile_measurement_archive_includes_config_and_raw_samples(tmp_path, monkeypatch) -> None:
    context = load_app_context(
        "dispersion_correction",
        machine_id="irfel",
        control_backend="real",
    )
    config = profile_runtime.default_offline_config()
    measurement = AchromatWorkflow(config).measure_dispersion()
    monkeypatch.setattr(profile_runtime, "APP_DIR", tmp_path)

    paths = profile_runtime.write_profile_operation(
        context,
        "measure",
        measurement,
        config=config,
    )

    payload = paths["run_json"].read_text(encoding="utf-8")
    metadata = paths["run_metadata"].read_text(encoding="utf-8")
    assert '"plus"' in payload
    assert '"minus"' in payload
    assert '"config"' in metadata
    assert '"task": "measure"' in metadata
