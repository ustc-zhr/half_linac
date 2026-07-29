from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from half_linac.src.apps.dispersion_correction.machine.epics import EpicsMachine
from half_linac.src.apps.dispersion_correction.preflight import run_preflight
from half_linac.src.apps.dispersion_correction import profile_runtime
from half_linac.src.apps.dispersion_correction.profile_runtime import (
    apply_profile_selection,
    load_profile_run_config,
    profile_section_choices,
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


def test_irfel_profile_separates_monitor_and_correction_bpms() -> None:
    context = load_app_context(
        "dispersion_correction",
        machine_id="irfel",
        control_backend="real",
    )
    _, config = load_profile_run_config(context)

    assert config.monitor_bpms == ("BPM07", "BPM08")
    assert config.target_bpms == ("BPM09", "BPM10")
    assert config.measurement_bpms == (
        "BPM07",
        "BPM08",
        "BPM09",
        "BPM10",
    )
    assert set(config.backend.options["pv_map"]["bpms"]) == set(
        config.measurement_bpms
    )


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
    assert config.section.id == "MIR-dogleg"
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
    assert config.backend.mode == "write_enabled"
    assert config.backend.options["readback_timeout"] == pytest.approx(10.0)
    assert context.model_backend is not None
    assert config.section.id == "MIR-dogleg"
    assert config.section.display_name == "MIR Dogleg"
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
    assert pv_map["quadrupoles"]["QM13"] == {
        "control": "k1",
        "K1": "IRFEL:AP:QUAD:MQ3:K1:ao",
    }
    assert pv_map["energy_knob"] == {
        "set": "IRFEL:modulator1:HV_set:ao",
        "readback": "IRFEL:modulator1:HV:ai",
    }
    preflight = run_preflight(config)
    assert not preflight.ok
    assert preflight.level == "blocked"
    assert not preflight.checks["energy_calibration_available"]
    assert any(
        "calibration.actuator_per_delta" in blocker
        for blocker in preflight.blockers
    )
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
    assert selected.backend.options["pv_map"]["quadrupoles"]["QM11"] == {
        "control": "k1",
        "K1": "IRFEL:AP:QUAD:MQ1:K1:ao",
    }


def test_half_profile_allows_bl01_writes_but_requires_an_energy_pv() -> None:
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
    assert config.backend.type == "epics"
    assert config.backend.mode == "write_enabled"
    assert config.section.id == "bl01"
    assert not config.section.model_only
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
    assert tuple(knob.name for knob in config.knobs) == (
        "QL01_QL06_sym",
        "QL02_QL05_sym",
        "QL03_QL04_sym",
    )
    assert config.knobs[2].devices == {"QL03": 1.0, "QL04": 1.0}
    preflight = run_preflight(config)
    assert not preflight.ok
    assert not preflight.checks["response_dimensions_sufficient"]
    assert any(
        "Underdetermined response: 3 correction knobs and 2 target BPMs"
        in warning
        for warning in preflight.warnings
    )
    assert "Energy knob PV is not configured" in preflight.blockers
    assert "write_enabled requires an energy set PV" in preflight.blockers


def test_half_real_uses_k1_for_correction_and_model_snapshot() -> None:
    context = load_app_context(
        "dispersion_correction",
        machine_id="half",
        control_backend="real",
    )
    _, config = load_profile_run_config(context)

    assert config.backend.mode == "write_enabled"
    assert config.backend.options["pv_map"]["quadrupoles"]["QL01"] == {
        "control": "k1",
        "K1": "IN:MG:L001:QUAD:QL01:K1",
    }
    k1_pv = resolve_channel(context, "QL01", "K1")
    snapshot = build_model_snapshot(
        context,
        (("QL01", "K1"),),
        pv_reader=lambda pv: 6.25 if pv == k1_pv else None,
    )
    assert snapshot.lattice_overrides == {"QL01": {"K1": 6.25}}


def test_half_bh01_bh03_section_uses_symmetric_k1_knobs() -> None:
    context = load_app_context(
        "dispersion_correction",
        machine_id="half",
        control_backend="vm",
    )
    assert profile_section_choices(context) == (
        ("bl01", "BL01 Dogleg"),
        ("bh01_bh03", "BH01–BH03 Horizontal Achromat"),
        ("bv01_bv02", "BV01–BV02 Vertical Achromat"),
        ("bh04_sep_diagnostics", "BH04–SEP Diagnostics"),
    )

    _, config = load_profile_run_config(context, section_id="bh01_bh03")

    assert config.section.model_entrance == "BPM21"
    assert config.section.model_exit == "BPM27"
    assert config.target_bpms == ("BPM26", "BPM27")
    assert config.monitor_bpms == (
        "BPM21",
        "BPM22",
        "BPM23",
        "BPM24",
        "BPM25",
    )
    assert tuple(item.element for item in config.section.model_observables) == (
        "BPM26",
        "BPM26",
    )
    assert tuple(item.component for item in config.section.model_observables) == (
        "dx",
        "dxp",
    )
    assert tuple(knob.name for knob in config.knobs) == (
        "QT05_QT12_sym",
        "QT06_QT11_sym",
        "QT07_QT10_sym",
        "QT08_QT09_sym",
    )
    assert config.backend.options["pv_map"]["quadrupoles"]["QT05"] == {
        "control": "k1",
        "K1": "HALF:IN:AP:QUAD:QT05:K1:ao",
    }
    assert config.backend.options["pv_map"]["quadrupoles"]["QT12"] == {
        "control": "k1",
        "K1": "HALF:IN:AP:QUAD:QT12:K1:ao",
    }
    snapshot = build_model_snapshot(
        context,
        (("QT05", "K1"), ("QT12", "K1")),
        pv_reader=lambda pv: {
            "HALF:IN:AP:QUAD:QT05:K1:ao": 2.3,
            "HALF:IN:AP:QUAD:QT12:K1:ao": 2.4,
        }[pv],
    )
    assert snapshot.lattice_overrides == {
        "QT05": {"K1": 2.3},
        "QT12": {"K1": 2.4},
    }

    real_context = load_app_context(
        "dispersion_correction",
        machine_id="half",
        control_backend="real",
    )
    _, real_config = load_profile_run_config(
        real_context,
        section_id="bh01_bh03",
    )
    assert real_config.backend.options["pv_map"]["quadrupoles"]["QT05"] == {
        "control": "k1",
        "K1": "IN:MG:L002:QUAD:QT05:K1",
    }
    assert real_config.backend.options["pv_map"]["quadrupoles"]["QT12"] == {
        "control": "k1",
        "K1": "IN:MG:L002:QUAD:QT12:K1",
    }
    preflight = run_preflight(config)
    assert not preflight.ok
    assert not preflight.checks["response_dimensions_sufficient"]
    assert any(
        "Underdetermined response: 4 correction knobs and 2 target BPMs"
        in warning
        for warning in preflight.warnings
    )
    assert "Energy knob PV is not configured" in preflight.blockers


def test_half_bv01_bv02_section_uses_vertical_bpms_and_real_hv_draft() -> None:
    vm_context = load_app_context(
        "dispersion_correction",
        machine_id="half",
        control_backend="vm",
    )
    _, vm_config = load_profile_run_config(
        vm_context,
        section_id="bv01_bv02",
    )

    assert vm_config.measurement.plane == "y"
    assert vm_config.section.model_entrance == "BPM36"
    assert vm_config.section.model_exit == "BPM43"
    assert vm_config.target_bpms == ("BPM42", "BPM43")
    assert vm_config.monitor_bpms == (
        "BPM36",
        "BPM37",
        "BPM38",
        "BPM39",
        "BPM40",
        "BPM41",
    )
    assert tuple(item.component for item in vm_config.section.model_observables) == (
        "dy",
        "dyp",
    )
    assert tuple(knob.name for knob in vm_config.knobs) == (
        "QT30_QT35_sym",
        "QT31_QT34_sym",
        "QT32_QT33_sym",
    )
    assert "y" in vm_config.backend.options["pv_map"]["bpms"]["BPM42"]
    assert vm_config.backend.options["pv_map"]["energy_knob"] == {}
    assert "Energy knob PV is not configured" in run_preflight(
        vm_config
    ).blockers
    vm_snapshot = build_model_snapshot(
        vm_context,
        (("QT29", "K1"), ("QT36", "K1")),
        pv_reader=lambda pv: {
            "HALF:IN:AP:QUAD:QT29:K1:ao": 1.1,
            "HALF:IN:AP:QUAD:QT36:K1:ao": 1.2,
        }[pv],
    )
    assert vm_snapshot.lattice_overrides == {
        "QT29": {"K1": 1.1},
        "QT36": {"K1": 1.2},
    }

    real_context = load_app_context(
        "dispersion_correction",
        machine_id="half",
        control_backend="real",
    )
    _, real_config = load_profile_run_config(
        real_context,
        section_id="bv01_bv02",
    )

    assert real_config.energy_knob.name == "MODULATOR_HV1"
    assert real_config.energy_knob.actuator_unit == "kV"
    assert real_config.energy_knob.delta == pytest.approx(0.004)
    assert real_config.backend.options["readback_timeout"] == 10.0
    assert real_config.backend.options["pv_map"]["energy_knob"] == {
        "set": "HALF:modulator1:HV_set:ao",
        "readback": "HALF:modulator1:HV:ai",
    }
    quadrupole_map = real_config.backend.options["pv_map"]["quadrupoles"]
    assert "QT29" not in quadrupole_map
    assert "QT36" not in quadrupole_map
    assert quadrupole_map["QT30"] == {
        "control": "k1",
        "K1": "IN:MG:L002:QUAD:QT30:K1",
    }
    assert quadrupole_map["QT35"] == {
        "control": "k1",
        "K1": "IN:MG:L002:QUAD:QT35:K1",
    }
    bpm_map = real_config.backend.options["pv_map"]["bpms"]
    machine = EpicsMachine(
        replace(
            real_config,
            backend=replace(real_config.backend, mode="read_only"),
        ),
        epics_client=FakeEpics(
            {
                bpm_map["BPM42"]["x"]: 100.0,
                bpm_map["BPM42"]["y"]: 1.25,
                bpm_map["BPM43"]["x"]: 200.0,
                bpm_map["BPM43"]["y"]: -0.5,
            }
        ),
    )
    reading = machine.read_bpm(("BPM42", "BPM43"))
    assert reading.y_mm.tolist() == pytest.approx([1.25, -0.5])
    assert reading.valid.tolist() == [True, True]


def test_half_bh04_sep_section_is_measurement_only() -> None:
    context = load_app_context(
        "dispersion_correction",
        machine_id="half",
        control_backend="vm",
    )
    _, config = load_profile_run_config(
        context,
        section_id="bh04_sep_diagnostics",
    )

    assert config.section.diagnostic_only
    assert not config.section.model_only
    assert config.section.model_entrance == "BPM36"
    assert config.section.model_exit == "WSEP1"
    assert config.target_bpms == ()
    assert config.knobs == ()
    assert config.monitor_bpms == tuple(
        f"BPM{index}" for index in range(36, 44)
    )
    assert tuple(item.element for item in config.section.model_observables) == (
        "WSEP1",
        "WSEP1",
    )
    assert config.backend.options["pv_map"]["quadrupoles"] == {}
    assert set(config.backend.options["pv_map"]["bpms"]) == set(
        config.monitor_bpms
    )

    offline_config = replace(
        config,
        backend=replace(
            config.backend,
            type="offline",
            mode="write_enabled",
            options={},
        ),
        energy_knob=replace(
            config.energy_knob,
            actuator="MODEL_DELTA",
            calibration=None,
        ),
        measurement=replace(
            config.measurement,
            samples_per_step=1,
            final_samples=1,
            sample_interval_s=0.0,
            settle_time_s=0.0,
        ),
    )
    workflow = AchromatWorkflow(offline_config)
    measurement = workflow.measure_dispersion()

    assert measurement.bpm_names == config.monitor_bpms
    assert not np.any(measurement.target_mask)
    assert np.isnan(measurement.rms_mm)
    assert np.isfinite(measurement.measured_rms_mm)
    with pytest.raises(PermissionError, match="measurement-only"):
        workflow.build_response_matrix()
    with pytest.raises(PermissionError, match="measurement-only"):
        workflow.run()


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
