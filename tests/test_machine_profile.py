from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
PARENT = REPO_ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from half_linac.src.shared.machine_profile import (
    AppContext,
    ElegantModelBackend,
    MachineProfile,
    MachineProfileError,
    MachineValidationReport,
    REAL_STATUS_NOT_SUPPORTED,
    REAL_STATUS_READ_ONLY,
    REAL_STATUS_WRITE_BLOCKED,
    build_model_backend,
    describe_app_model_support,
    describe_app_support,
    get_bba_preset,
    get_emit_preset,
    get_workflow,
    list_elements,
    load_app_context,
    load_profile,
    load_solenoid_centering_workflow,
    real_commissioning_status,
    require_workflow_write_allowed,
    resolve_bend_write_channel,
    resolve_channel,
    resolve_flag_pixel_geometry,
    resolve_machine_runtime,
    resolve_virtual_machine_segment_choices,
    resolve_virtual_machine_usedline_workflow,
    validate_machine_profile,
    workflow_writes_allowed,
)
from half_linac.src.shared.machine_profile.loader import (
    load_bba_workflow,
    load_emit_measure_workflow,
)
from half_linac.src.shared.machine_profile.runtime_selector import (
    default_control_backend_choices,
    list_machine_choices,
)


def _write_directory_profile_fixture(
    temp_root: Path,
    machine_id: str,
    machine_json: dict,
    *,
    backends: dict[str, dict],
    apps: dict[str, dict] | None = None,
    model_backends: dict[str, dict] | None = None,
) -> Path:
    machine_dir = temp_root / "configs" / "machines" / machine_id
    (machine_dir / "control_backends").mkdir(parents=True)
    if apps:
        (machine_dir / "apps").mkdir(parents=True)
    if model_backends:
        (machine_dir / "model_backends").mkdir(parents=True)

    (machine_dir / "machine.json").write_text(
        json.dumps(machine_json, indent=2),
        encoding="utf-8",
    )
    for backend_name, payload in backends.items():
        (machine_dir / "control_backends" / f"{backend_name}.json").write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )
    for app_name, payload in (apps or {}).items():
        (machine_dir / "apps" / f"{app_name}.json").write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )
    for backend_name, payload in (model_backends or {}).items():
        (machine_dir / "model_backends" / f"{backend_name}.json").write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )
    return machine_dir


class MachineProfileTests(unittest.TestCase):
    def test_machine_profile_managed_sources_do_not_import_runtime_config(self):
        managed_paths = [
            REPO_ROOT / "src/apps/launcher/main.py",
            REPO_ROOT / "src/apps/orbit_correct/mainOrbCor.py",
            REPO_ROOT / "src/apps/orbit_correct/correct.py",
            REPO_ROOT / "src/apps/orbit_correct/findresponse.py",
            REPO_ROOT / "src/apps/orbit_correct/profile_runtime.py",
            REPO_ROOT / "src/apps/orbit_display/main.py",
            REPO_ROOT / "src/apps/orbit_display/submain.py",
            REPO_ROOT / "src/apps/beam_monitor/main.py",
            REPO_ROOT / "src/apps/bba/main.py",
            REPO_ROOT / "src/apps/emit_measure/main.py",
            REPO_ROOT / "src/apps/emit_measure/test.py",
            REPO_ROOT / "src/apps/energy_spectrum/main.py",
            REPO_ROOT / "src/apps/energy_spectrum/get_energy0.py",
            REPO_ROOT / "src/shared/elegant_backend/parser.py",
            REPO_ROOT / "src/shared/elegant_backend/publisher.py",
            REPO_ROOT / "src/shared/elegant_runtime.py",
            REPO_ROOT / "src/shared/machine_profile/loader.py",
            REPO_ROOT / "src/shared/machine_profile/resolver.py",
        ]
        offenders = []
        for path in managed_paths:
            text = path.read_text(encoding="utf-8")
            if "runtime_config as st" in text:
                offenders.append(str(path.relative_to(REPO_ROOT)))
        self.assertEqual(
            offenders,
            [],
            f"Managed machine-profile sources must not import runtime_config directly: {offenders}",
        )

    def test_load_half_profile(self):
        profile = load_profile("half")
        self.assertEqual(profile.machine.id, "half")
        self.assertEqual(profile.machine.default_mode, "vm")
        self.assertEqual(profile.schema_version, "1")

    def test_load_orbit_correct_app_context(self):
        context = load_app_context("orbit_correct")
        self.assertIsInstance(context, AppContext)
        self.assertEqual(context.machine.id, "half")
        self.assertEqual(context.control_backend.name, "vm")
        self.assertIsNone(context.model_backend)
        self.assertIsNotNone(context.orbit_workflow)
        assert context.orbit_workflow is not None
        self.assertEqual(len(context.orbit_workflow.bpms), 41)
        self.assertEqual(context.orbit_workflow.xcors[18], "XC21")
        self.assertTrue(workflow_writes_allowed(context, "orbit"))
        require_workflow_write_allowed(context, "orbit", "test write")

    def test_load_orbit_display_app_context(self):
        context = load_app_context("orbit_display")
        self.assertIsInstance(context, AppContext)
        self.assertEqual(context.machine.id, "half")
        self.assertEqual(context.control_backend.name, "vm")
        self.assertIsNone(context.model_backend)
        self.assertIsNone(context.orbit_workflow)

    def test_load_beam_monitor_app_context(self):
        context = load_app_context("beam_monitor")
        self.assertIsInstance(context, AppContext)
        self.assertEqual(context.machine.id, "half")
        self.assertEqual(context.control_backend.name, "vm")
        self.assertIsNone(context.model_backend)
        self.assertIsNone(context.emit_measure_workflow)

    def test_load_solenoid_centering_app_context(self):
        context = load_app_context("solenoid_centering")
        self.assertIsInstance(context, AppContext)
        self.assertEqual(context.machine.id, "half")
        self.assertEqual(context.control_backend.name, "vm")
        self.assertIsNone(context.model_backend)
        self.assertIsNotNone(context.solenoid_centering_workflow)
        assert context.solenoid_centering_workflow is not None
        workflow = load_solenoid_centering_workflow(context.profile)
        self.assertEqual(workflow.default_preset, "ls_centering")
        self.assertEqual(workflow.presets_by_id["ls_centering"].hcorr, "LS:HC")
        self.assertFalse(workflow_writes_allowed(context, "solenoid_centering"))

    def test_load_irfel_solenoid_centering_app_context(self):
        context = load_app_context(
            "solenoid_centering",
            machine_id="irfel",
            control_backend="real",
        )
        self.assertEqual(context.machine.id, "irfel")
        self.assertEqual(context.control_backend.name, "real")
        self.assertIsNone(context.model_backend)
        assert context.solenoid_centering_workflow is not None
        workflow = context.solenoid_centering_workflow
        self.assertEqual(workflow.default_preset, "ms01_centering")
        self.assertEqual(workflow.presets_by_id["ms01_centering"].hcorr, "MSHC")
        self.assertEqual(workflow.presets_by_id["ls01_centering"].vcorr, "VC01")
        self.assertTrue(workflow_writes_allowed(context, "solenoid_centering"))
        self.assertFalse(workflow_writes_allowed(context, "solenoid_centering", mode="vm"))
        self.assertEqual(real_commissioning_status(context), "write_smoke_passed")

    def test_describe_app_model_support_reports_model_app_readiness(self):
        for machine_id in ("half", "irfel"):
            for app_name in ("bba", "emit_measure", "energy_spectrum"):
                supported, reason = describe_app_model_support(machine_id, app_name)
                self.assertTrue(supported, f"{machine_id} {app_name}: {reason}")
                self.assertIsNone(reason)

        supported, reason = describe_app_model_support("half", "orbit_correct")
        self.assertTrue(supported)
        self.assertIsNone(reason)

    def test_half_beam_monitor_workflow_keeps_backend_image_geometry(self):
        profile = load_profile("half")
        workflow = get_workflow(profile, "beam_monitor")
        self.assertEqual(workflow["default_flag"], "PRF06")
        self.assertEqual(workflow["flag_pixel_shape"]["vm"], [360, 270])
        self.assertEqual(workflow["flag_pixel_shape"]["real"], [1440, 1080])
        self.assertEqual(workflow["flag_pixel_width_mm"]["vm"], 0.02)
        self.assertEqual(
            resolve_flag_pixel_geometry(
                workflow,
                "workflows.beam_monitor",
                "vm",
                "PRF06",
            ).shape,
            (360, 270),
        )

    def test_beam_monitor_pixel_geometry_supports_per_flag_override(self):
        workflow = {
            "flag_pixel_geometry": {
                "default": {
                    "vm": {"shape": [360, 270], "pixel_width_mm": 0.02},
                    "real": {"shape": [360, 270], "pixel_width_mm": 0.02},
                },
                "by_flag": {
                    "PRF04": {
                        "real": {"shape": [1440, 1080], "pixel_width_mm": 0.0065},
                    },
                    "PRF05": {
                        "vm": {"shape": [720, 540]},
                    },
                },
            },
        }

        self.assertEqual(
            resolve_flag_pixel_geometry(
                workflow,
                "workflows.beam_monitor",
                "real",
                "PRF03",
            ).shape,
            (360, 270),
        )
        prf04_real = resolve_flag_pixel_geometry(
            workflow,
            "workflows.beam_monitor",
            "real",
            "PRF04",
        )
        self.assertEqual(prf04_real.shape, (1440, 1080))
        self.assertEqual(prf04_real.pixel_width_mm, 0.0065)
        prf05_vm = resolve_flag_pixel_geometry(
            workflow,
            "workflows.beam_monitor",
            "vm",
            "PRF05",
        )
        self.assertEqual(prf05_vm.shape, (720, 540))
        self.assertEqual(prf05_vm.pixel_width_mm, 0.02)

    def test_half_virtual_machine_workflow_keeps_expected_segment_choices(self):
        profile = load_profile("half")
        workflow = resolve_virtual_machine_usedline_workflow(profile)
        start_ids, end_ids, default_start, default_end = resolve_virtual_machine_segment_choices(profile)
        segment = workflow.local_segments[0]
        self.assertEqual(
            tuple(choice.id for choice in workflow.predefined_usedlines),
            ("ALL_MAIN", "ALL_ESA"),
        )
        self.assertEqual(workflow.default_usedline, "ALL_MAIN")
        self.assertEqual(workflow.segment_wait_s, 8.0)
        self.assertEqual(segment.parent_usedline, "ALL_MAIN")
        self.assertEqual(segment.start_ids, ("QL27", "QT01", "QT02", "QL15", "QL16"))
        self.assertEqual(segment.end_ids, ("PRF04", "PRF06", "PRF07", "PRF08"))
        self.assertEqual(default_start, "QL27")
        self.assertEqual(default_end, "PRF04")
        self.assertEqual(start_ids, ("QL27", "QT01", "QT02", "QL15", "QL16"))
        self.assertEqual(end_ids, ("PRF04", "PRF06", "PRF07", "PRF08"))

    def test_load_energy_spectrum_app_context(self):
        context = load_app_context("energy_spectrum")
        self.assertIsInstance(context, AppContext)
        self.assertEqual(context.machine.id, "half")
        self.assertEqual(context.control_backend.name, "vm")
        self.assertIsNotNone(context.model_backend)
        assert context.model_backend is not None
        self.assertEqual(context.model_backend.engine, "elegant")

    def test_load_bba_app_context(self):
        context = load_app_context("bba")
        self.assertIsInstance(context, AppContext)
        self.assertIsNotNone(context.bba_workflow)
        self.assertIsNotNone(context.model_backend)
        assert context.bba_workflow is not None
        self.assertEqual(context.bba_workflow.standard.default_preset, "bba1_default")
        self.assertEqual(context.bba_workflow.bba2.default_preset, "bba2_default")
        self.assertEqual(context.bba_workflow.standard.quads, ())
        self.assertEqual(context.bba_workflow.standard.correctors, ())
        self.assertEqual(context.bba_workflow.bba2.quads, ())
        self.assertEqual(context.bba_workflow.bba2.correctors, ())
        self.assertEqual(context.bba_workflow.bba2.control_backends, ())
        assert context.model_backend is not None
        self.assertEqual(context.model_backend.engine, "elegant")

    def test_bba_runtime_paths_are_machine_backend_scoped(self):
        from half_linac.src.apps.bba.profile_runtime import (
            new_bba_scan_archive_dir,
            resolve_bba_runtime_paths,
        )

        context = load_app_context("bba", machine_id="irfel", control_backend="vm")
        paths = resolve_bba_runtime_paths(context)

        self.assertTrue(str(paths["runtime_dir"]).endswith("src/apps/bba/runtime/irfel/vm"))
        self.assertEqual(paths["latest_dir"], paths["runtime_dir"] / "latest")
        self.assertEqual(paths["archive_dir"], paths["runtime_dir"] / "scans")
        self.assertEqual(paths["bba1_data_path"], paths["latest_dir"] / "m1S.txt")
        self.assertEqual(paths["bba1_quad_scan_path"], paths["latest_dir"] / "bba1_quad_scan.txt")
        self.assertEqual(paths["bba1_metadata_path"], paths["latest_dir"] / "metadata.json")
        self.assertEqual(paths["bba2_quad_scan_path"], paths["latest_dir"] / "bba2_k1Lqm2.txt")
        self.assertEqual(paths["bba2_bpm1_path"], paths["latest_dir"] / "bba2_m1.txt")
        self.assertEqual(paths["bba2_corrector_scan_path"], paths["latest_dir"] / "bba2_thetam2.txt")
        self.assertEqual(paths["bba2_metadata_path"], paths["latest_dir"] / "bba2_metadata.json")
        archive_dir = new_bba_scan_archive_dir(context, "bba2")
        self.assertEqual(archive_dir.parent, paths["archive_dir"])
        self.assertIn("_bba2", archive_dir.name)

    def test_load_emit_measure_app_context(self):
        context = load_app_context("emit_measure")
        self.assertIsInstance(context, AppContext)
        self.assertIsNotNone(context.emit_measure_workflow)
        self.assertIsNotNone(context.model_backend)
        assert context.emit_measure_workflow is not None
        self.assertEqual(context.emit_measure_workflow.default_preset, "emit_ql27_prf06")
        self.assertEqual(context.emit_measure_workflow.twiss_quads, ())
        assert context.model_backend is not None
        self.assertEqual(context.model_backend.engine, "elegant")

    def test_resolve_expected_half_channels(self):
        profile = load_profile("half")
        self.assertEqual(
            resolve_channel(profile, "BPM03", "x", "vm"),
            "HALF:IN:BPM:BPM03:X:ao",
        )
        self.assertEqual(
            resolve_channel(profile, "XC21", "setpoint", "real"),
            "IN:PS:LE16:XC21:current:ao",
        )
        self.assertEqual(
            resolve_channel(profile, "QL27", "k1", "vm"),
            "HALF:IN:AP:QUAD:QL27:K1:ao",
        )
        self.assertEqual(
            resolve_channel(profile, "PRF07", "sigx", "Virtual Machine"),
            "HALF:IN:FLAG:PRF07:sigx",
        )
        self.assertEqual(
            resolve_channel(profile, "PRF07", "image", "vm"),
            "HALF:IN:FLAG:PRF07:image1:ArrayData:vm",
        )
        self.assertEqual(
            resolve_channel(profile, "PRF07", "esa_image", "vm"),
            "HALF:IN:FLAG:PRFESA:image1:ArrayData:vm",
        )
        self.assertEqual(
            resolve_channel(profile, "PRF07", "exposure_time", "real"),
            "HALF:IN:FLAG:PRF07:cam1:AcquireTime",
        )
        self.assertEqual(
            resolve_bend_write_channel(profile, "BENY", "vm"),
            "HALF:IN:BEND:BENY:angle",
        )

    def test_real_backend_uses_raw_machine_pv_naming(self):
        profile = load_profile("half")
        self.assertEqual(
            resolve_channel(profile, "BPM01", "x", "real"),
            "IN:BD:LE05:DBPM:01:BPM_X",
        )
        self.assertEqual(
            resolve_channel(profile, "BPM20", "x", "real"),
            "IN:BD:LE14:DBPM:20:BPM_X",
        )
        self.assertEqual(
            resolve_channel(profile, "PRF04", "sigy", "real"),
            "IN:BD:PRF04:V:SIZE",
        )
        self.assertEqual(
            resolve_channel(profile, "XC00", "setpoint", "real"),
            "IN:PS:LE07:XC00:current:ao",
        )
        self.assertEqual(
            resolve_channel(profile, "MS:HC", "setpoint", "real"),
            "IN:PS:LE07:SM01-DX:current:ao",
        )
        self.assertEqual(
            resolve_channel(profile, "HC2", "setpoint", "real"),
            "IRFEL:PS:HC02:current:ao",
        )
        self.assertEqual(
            resolve_channel(profile, "QT01", "k1", "real"),
            "IN:MG:L002:QUAD:QT01:K1",
        )
        self.assertEqual(
            resolve_channel(profile, "QL03", "k1", "real"),
            "IN:PS:LE07:QL03:current:ao",
        )

    def test_vm_backend_uses_softioc_alias_naming_for_magnets(self):
        profile = load_profile("half")
        self.assertEqual(
            resolve_channel(profile, "XC00", "setpoint", "vm"),
            "HALF:IN:PS:XC00:current:ao",
        )
        self.assertEqual(
            resolve_channel(profile, "XC21", "setpoint", "vm"),
            "HALF:IN:PS:XC21:current:ao",
        )
        self.assertEqual(
            resolve_channel(profile, "HC01", "setpoint", "vm"),
            "HALF:IN:PS:HC01:current:ao",
        )
        self.assertEqual(
            resolve_channel(profile, "QL03", "k1", "vm"),
            "HALF:IN:AP:QUAD:QL03:K1:ao",
        )

    def test_resolve_channel_from_app_context(self):
        context = load_app_context("orbit_correct")
        self.assertEqual(
            resolve_channel(context, "BPM03", "x"),
            "HALF:IN:BPM:BPM03:X:ao",
        )

    def test_orbit_workflow_matches_expected_shape(self):
        profile = load_profile("half")
        workflow = get_workflow(profile, "orbit")
        from half_linac.src.apps.orbit_correct.profile_runtime import load_orbit_runtime_settings

        self.assertEqual(len(workflow["bpms"]), 41)
        self.assertEqual(workflow["bpms"][0], "BPM03")
        self.assertEqual(workflow["bpms"][-1], "BPM43")
        self.assertEqual(workflow["xcors"][18], "XC21")
        self.assertEqual(workflow["ycors"][26], "YC29")
        self.assertEqual(workflow["response_wait_s_by_backend"]["vm"], 8)
        self.assertEqual(workflow["corrector_upperlimit_rad"], 0.001)
        runtime = load_orbit_runtime_settings(load_app_context("orbit_correct", machine_id="half", control_backend="vm"))
        self.assertEqual(runtime["corrector_upperlimit"], 0.001)
        self.assertEqual(runtime["corrector_upperlimit_unit"], "rad")

    def test_bba_and_emit_defaults_exist(self):
        bba_context = load_app_context("bba")
        emit_context = load_app_context("emit_measure")

        bba_preset_ids = {preset.id for preset in bba_context.bba_workflow.presets}
        emit_preset_ids = {preset.id for preset in emit_context.emit_measure_workflow.presets}

        self.assertIn(bba_context.bba_workflow.standard.default_preset, bba_preset_ids)
        self.assertIn(bba_context.bba_workflow.bba2.default_preset, bba_preset_ids)
        self.assertIn(emit_context.emit_measure_workflow.default_preset, emit_preset_ids)

    def test_context_preset_helpers_return_default_presets(self):
        bba_context = load_app_context("bba")
        emit_context = load_app_context("emit_measure")
        bba_preset = get_bba_preset(bba_context)
        emit_preset = get_emit_preset(emit_context)

        self.assertEqual(bba_preset.id, bba_context.bba_workflow.standard.default_preset)
        self.assertEqual(bba_preset.plane, "x")
        self.assertEqual(emit_preset.id, emit_context.emit_measure_workflow.default_preset)
        self.assertGreater(emit_preset.energy_mev, 0)

    def test_context_preset_helpers_support_explicit_bba_preset(self):
        bba_context = load_app_context("bba")
        preset = get_bba_preset(bba_context, "bba2_default")
        self.assertEqual(preset.family, "bba2")
        self.assertIsNone(preset.mode)
        self.assertEqual(preset.energy_mev, 2200)
        self.assertEqual(preset.corr, "XC21")
        self.assertEqual(preset.scan["quad_from"], -3)
        self.assertEqual(preset.analysis["energy_mev"], 2200)
        self.assertEqual(preset.analysis["bpm1_samples"], 1)
        self.assertEqual(preset.analysis.leff_by, 0.058287)
        self.assertEqual(preset.analysis.quad_leff, 0.15)

    def test_context_preset_helpers_support_default_emit_preset(self):
        emit_context = load_app_context("emit_measure")
        preset = get_emit_preset(emit_context)
        self.assertEqual(preset.quad, "QL27")
        self.assertEqual(preset.flag, "PRF06")
        self.assertEqual(preset.energy_mev, 2200)
        self.assertEqual(preset.scan.k1_steps, 15)
        self.assertEqual(preset.scan["samples"], 5)
        self.assertEqual(preset.analysis.energy_mev, 2200)

    def test_build_model_backend_returns_elegant_backend_for_measurement_apps(self):
        bba_backend = build_model_backend(load_app_context("bba"), energy_mev=2200)
        emit_backend = build_model_backend(load_app_context("emit_measure"), energy_mev=2200)
        self.assertIsInstance(bba_backend, ElegantModelBackend)
        self.assertIsInstance(emit_backend, ElegantModelBackend)

    def test_list_elements_filters_by_kind(self):
        profile = load_profile("half")
        bpm_elements = list_elements(profile, "bpm")
        flag_elements = list_elements(profile, "flag")
        corr_elements = list_elements(profile, "corr")
        quad_elements = list_elements(profile, "quad")
        corr_ids = {element.id for element in corr_elements}
        bpm_ids = {element.id for element in bpm_elements}
        quad_ids = {element.id for element in quad_elements}
        self.assertTrue(any(element.id == "BPM03" for element in bpm_elements))
        self.assertIn("BPM01", bpm_ids)
        self.assertIn("XC00", corr_ids)
        self.assertIn("YC02", corr_ids)
        self.assertNotIn("HIC01", corr_ids)
        self.assertNotIn("VIC02", corr_ids)
        self.assertIn("QL03", quad_ids)
        self.assertNotIn("CQ3", quad_ids)
        self.assertEqual({element.id for element in flag_elements}, {"PRF04", "PRF06", "PRF07", "PRF08"})

    def test_list_elements_supports_role_and_plane_filters(self):
        profile = load_profile("half")
        quad_ids = {element.id for element in list_elements(profile, kind="quad")}
        bpm_ids = {element.id for element in list_elements(profile, kind="bpm")}
        flag_ids = {element.id for element in list_elements(profile, kind="flag")}
        x_corrs = {element.id for element in list_elements(profile, kind="corr", plane="x")}
        y_corrs = {element.id for element in list_elements(profile, kind="corr", plane="y")}
        image_flags = {element.id for element in list_elements(profile, kind="flag", logical_channel="image")}
        esa_flags = {element.id for element in list_elements(profile, kind="flag", logical_channel="esa_image")}

        self.assertIn("QL03", quad_ids)
        self.assertIn("QT18", quad_ids)
        self.assertIn("QE01", quad_ids)
        self.assertIn("QE03", quad_ids)
        self.assertIn("BPM01", bpm_ids)
        self.assertIn("BPM29", bpm_ids)
        self.assertEqual(flag_ids, {"PRF04", "PRF06", "PRF07", "PRF08"})
        self.assertEqual(image_flags, {"PRF04", "PRF06", "PRF07", "PRF08"})
        self.assertEqual(esa_flags, {"PRF07"})
        self.assertIn("XC00", x_corrs)
        self.assertIn("MS:HC", x_corrs)
        self.assertNotIn("YC00", x_corrs)
        self.assertIn("YC00", y_corrs)
        self.assertIn("LS:VC", y_corrs)
        self.assertNotIn("XC00", y_corrs)

    def test_half_elements_keep_simple_metadata_and_inferred_planes(self):
        profile = load_profile("half")
        ql03 = profile.get_element("QL03")
        xc21 = profile.get_element("XC21")
        yc21 = profile.get_element("YC21")

        self.assertEqual(ql03.roles, ())
        self.assertIn("bba_corr", xc21.roles)
        self.assertEqual(xc21.plane, "x")
        self.assertEqual(yc21.plane, "y")

    def test_load_profile_uses_env_machine_id_when_unspecified(self):
        with patch.dict(os.environ, {"HALF_LINAC_MACHINE_ID": "irfel"}, clear=False):
            profile = load_profile()
        self.assertEqual(profile.machine.id, "irfel")

    def test_load_profile_accepts_legacy_env_machine_id(self):
        with patch.dict(
            os.environ,
            {"HALF_LINAC_MACHINE_ID": "", "HALF_MACHINE_ID": "half"},
            clear=False,
        ):
            profile = load_profile()
        self.assertEqual(profile.machine.id, "half")

    def test_new_env_machine_id_takes_precedence_over_legacy_env(self):
        with patch.dict(
            os.environ,
            {
                "HALF_LINAC_MACHINE_ID": "irfel",
                "HALF_MACHINE_ID": "half",
            },
            clear=False,
        ):
            profile = load_profile()
        self.assertEqual(profile.machine.id, "irfel")

    def test_load_app_context_uses_env_control_backend_when_unspecified(self):
        with patch.dict(os.environ, {"HALF_LINAC_CONTROL_BACKEND": "real"}, clear=False):
            context = load_app_context("orbit_correct")
        self.assertEqual(context.control_backend.name, "real")

    def test_load_app_context_accepts_legacy_env_control_backend(self):
        with patch.dict(
            os.environ,
            {"HALF_LINAC_CONTROL_BACKEND": "", "HALF_CONTROL_BACKEND": "real"},
            clear=False,
        ):
            context = load_app_context("orbit_correct")
        self.assertEqual(context.control_backend.name, "real")

    def test_new_env_control_backend_takes_precedence_over_legacy_env(self):
        with patch.dict(
            os.environ,
            {
                "HALF_LINAC_CONTROL_BACKEND": "vm",
                "HALF_CONTROL_BACKEND": "real",
            },
            clear=False,
        ):
            context = load_app_context("orbit_correct")
        self.assertEqual(context.control_backend.name, "vm")

    def test_runtime_selector_lists_half_machine_profile(self):
        choices = list_machine_choices()
        machine_ids = {choice.machine_id for choice in choices}
        self.assertIn("half", machine_ids)
        self.assertNotIn("_template", machine_ids)
        self.assertTrue(any(choice.display_name for choice in choices if choice.machine_id == "half"))

    def test_runtime_selector_discovers_control_backends_from_directory_profile(self):
        self.assertEqual(default_control_backend_choices("half"), ("vm", "real"))

    def test_half_model_backend_paths_are_resolved_from_directory_config(self):
        context = load_app_context("emit_measure")
        assert context.model_backend is not None
        source_json = Path(context.model_backend.config["source_json"])
        source_lattice = Path(context.model_backend.config["source_lattice"])
        working_dir = Path(context.model_backend.config["working_dir"])
        self.assertTrue(source_json.is_absolute())
        self.assertTrue(source_lattice.is_absolute())
        self.assertTrue(working_dir.is_absolute())
        self.assertTrue(str(source_json).endswith("src/virtual_machine/half_elegant/halflinac.json"))
        self.assertTrue(str(source_lattice).endswith("src/virtual_machine/half_elegant/elegant/lattice_ini.lte"))
        self.assertTrue(str(working_dir).endswith("src/virtual_machine/half_elegant/elegant"))

    def test_half_runtime_paths_are_resolved_from_machine_json(self):
        runtime = resolve_machine_runtime("half")
        self.assertEqual(runtime.profile.machine.id, "half")
        self.assertTrue(str(runtime.vm.root).endswith("src/virtual_machine/half_elegant"))
        self.assertTrue(str(runtime.vm.ui_entrypoint).endswith("src/virtual_machine/half_elegant/mainVM.py"))
        self.assertTrue(str(runtime.vm.manager_entrypoint).endswith("src/virtual_machine/half_elegant/start_VM.py"))
        self.assertTrue(str(runtime.vm.runtime_json).endswith("src/virtual_machine/half_elegant/halflinac.json"))
        self.assertTrue(str(runtime.softioc.root).endswith("src/softIOC/halflinac"))
        self.assertTrue(str(runtime.softioc.substitutions_file).endswith("src/softIOC/halflinac/db/halflinac.substitutions"))

    def test_energy_spectrum_is_reported_supported_for_half(self):
        supported, reason = describe_app_support("half", "energy_spectrum")
        self.assertTrue(supported)
        self.assertIsNone(reason)

    def test_machine_acceptance_validator_passes_for_half(self):
        report = validate_machine_profile("half")
        self.assertIsInstance(report, MachineValidationReport)
        self.assertTrue(report.ok, report.format_text())
        self.assertEqual(report.failed, ())
        self.assertEqual(report.get_check("profile").status, "pass")
        self.assertEqual(report.get_check("runtime").status, "pass")
        self.assertEqual(report.get_check("vm_publish_plan").status, "pass")
        self.assertEqual(report.get_check("app:orbit_correct").status, "pass")
        self.assertEqual(report.get_check("model:energy_spectrum").status, "pass")

    def test_beam_monitor_is_reported_supported_for_half(self):
        supported, reason = describe_app_support("half", "beam_monitor")
        self.assertTrue(supported)
        self.assertIsNone(reason)

    def test_irfel_profile_supports_orbit_apps_and_vm_runtime(self):
        profile = load_profile("irfel")
        orbit_context = load_app_context("orbit_correct", machine_id="irfel")
        vm_orbit_context = load_app_context(
            "orbit_correct",
            machine_id="irfel",
            control_backend="vm",
        )
        beam_context = load_app_context(
            "beam_monitor",
            machine_id="irfel",
            control_backend="vm",
        )
        real_beam_context = load_app_context(
            "beam_monitor",
            machine_id="irfel",
            control_backend="real",
        )
        energy_context = load_app_context(
            "energy_spectrum",
            machine_id="irfel",
            control_backend="vm",
        )
        emit_context = load_app_context(
            "emit_measure",
            machine_id="irfel",
            control_backend="vm",
        )
        real_emit_context = load_app_context(
            "emit_measure",
            machine_id="irfel",
            control_backend="real",
        )
        bba_context = load_app_context(
            "bba",
            machine_id="irfel",
            control_backend="vm",
        )
        real_bba_context = load_app_context(
            "bba",
            machine_id="irfel",
            control_backend="real",
        )
        real_energy_context = load_app_context(
            "energy_spectrum",
            machine_id="irfel",
            control_backend="real",
        )
        orbit_display_context = load_app_context("orbit_display", machine_id="irfel")
        runtime = resolve_machine_runtime(profile)
        report = validate_machine_profile("irfel")
        workflow = get_workflow(profile, "orbit")
        beam_workflow = get_workflow(profile, "beam_monitor")
        energy_workflow = get_workflow(profile, "energy_spectrum")
        emit_workflow = get_workflow(profile, "emit_measure")
        bba_workflow = get_workflow(profile, "bba")
        vm_start_ids, vm_end_ids, vm_default_start, vm_default_end = (
            resolve_virtual_machine_segment_choices(profile)
        )
        vm_workflow = resolve_virtual_machine_usedline_workflow(profile)
        from half_linac.src.apps.orbit_correct import profile_runtime as orbit_runtime

        orbit_runtime_paths = orbit_runtime.resolve_orbit_runtime_paths(vm_orbit_context)

        self.assertEqual(profile.machine.id, "irfel")
        self.assertEqual(profile.control_backends, ("real", "vm"))
        self.assertTrue(report.ok, report.format_text())
        self.assertEqual(report.get_check("runtime").status, "pass")
        self.assertEqual(report.get_check("virtual_machine").status, "pass")
        self.assertEqual(report.get_check("vm_publish_plan").status, "pass")
        self.assertEqual(report.get_check("app:orbit_correct").status, "pass")
        self.assertEqual(report.get_check("app:beam_monitor").status, "pass")
        self.assertEqual(report.get_check("app:emit_measure").status, "pass")
        self.assertEqual(report.get_check("model:emit_measure").status, "pass")
        self.assertEqual(report.get_check("app:bba").status, "pass")
        self.assertEqual(report.get_check("model:bba").status, "pass")
        self.assertEqual(report.get_check("app:energy_spectrum").status, "pass")
        self.assertEqual(report.get_check("model:energy_spectrum").status, "pass")
        self.assertEqual(report.get_check("commissioning:orbit_correct").status, "pass")
        self.assertEqual(report.get_check("commissioning:orbit_display").status, "pass")
        self.assertEqual(report.get_check("commissioning:beam_monitor").status, "pass")
        self.assertEqual(report.get_check("commissioning:bba").status, "pass")
        self.assertEqual(report.get_check("commissioning:emit_measure").status, "pass")
        self.assertEqual(report.get_check("commissioning:energy_spectrum").status, "pass")
        self.assertEqual(
            real_commissioning_status(profile, "orbit_correct"),
            REAL_STATUS_WRITE_BLOCKED,
        )
        self.assertEqual(real_commissioning_status(profile, "orbit_display"), REAL_STATUS_READ_ONLY)
        self.assertEqual(real_commissioning_status(profile, "beam_monitor"), REAL_STATUS_WRITE_BLOCKED)
        self.assertEqual(real_commissioning_status(profile, "bba"), REAL_STATUS_NOT_SUPPORTED)
        self.assertEqual(real_commissioning_status(profile, "emit_measure"), REAL_STATUS_WRITE_BLOCKED)
        self.assertEqual(real_commissioning_status(profile, "energy_spectrum"), REAL_STATUS_WRITE_BLOCKED)
        self.assertFalse(workflow_writes_allowed(orbit_context, "orbit"))
        self.assertTrue(workflow_writes_allowed(vm_orbit_context, "orbit"))
        self.assertFalse(workflow_writes_allowed(real_beam_context, "beam_monitor"))
        self.assertTrue(workflow_writes_allowed(beam_context, "beam_monitor"))
        self.assertFalse(workflow_writes_allowed(real_emit_context, "emit_measure"))
        self.assertTrue(workflow_writes_allowed(emit_context, "emit_measure"))
        self.assertFalse(workflow_writes_allowed(real_energy_context, "energy_spectrum"))
        self.assertTrue(workflow_writes_allowed(energy_context, "energy_spectrum"))
        self.assertFalse(workflow_writes_allowed(real_bba_context, "bba"))
        self.assertTrue(workflow_writes_allowed(bba_context, "bba"))
        with self.assertRaisesRegex(MachineProfileError, "blocked"):
            require_workflow_write_allowed(orbit_context, "orbit", "test write")
        with self.assertRaisesRegex(MachineProfileError, "blocked"):
            require_workflow_write_allowed(real_beam_context, "beam_monitor", "test write")
        with self.assertRaisesRegex(MachineProfileError, "blocked"):
            require_workflow_write_allowed(real_bba_context, "bba", "test write")
        require_workflow_write_allowed(vm_orbit_context, "orbit", "test write")
        require_workflow_write_allowed(bba_context, "bba", "test write")
        self.assertEqual(beam_context.app_name, "beam_monitor")
        self.assertEqual(energy_context.app_name, "energy_spectrum")
        self.assertEqual(energy_context.control_backend.name, "vm")
        self.assertIsNotNone(energy_context.model_backend)
        assert energy_context.model_backend is not None
        self.assertEqual(energy_context.model_backend.name, "simulation")
        self.assertEqual(emit_context.app_name, "emit_measure")
        self.assertEqual(emit_context.control_backend.name, "vm")
        self.assertIsNotNone(emit_context.emit_measure_workflow)
        self.assertIsNotNone(emit_context.model_backend)
        assert emit_context.emit_measure_workflow is not None
        assert emit_context.model_backend is not None
        self.assertEqual(emit_context.emit_measure_workflow.default_preset, "emit_qm12_prf04")
        self.assertEqual(emit_context.emit_measure_workflow.twiss_quads, ("QM11", "QM12"))
        self.assertEqual(
            emit_context.emit_measure_workflow.presets_by_id["emit_qm12_prf04"].model_line,
            "ALL_DUMP",
        )
        qm12_scan = emit_context.emit_measure_workflow.presets_by_id["emit_qm12_prf04"].scan
        self.assertEqual(qm12_scan.k1_from, 25.0)
        self.assertEqual(qm12_scan.k1_end, 35.0)
        self.assertEqual(qm12_scan.k1_steps, 11)
        self.assertEqual(qm12_scan.samples, 1)
        self.assertEqual(
            emit_context.emit_measure_workflow.presets_by_id["emit_qm11_prf04"].model_line,
            "ALL_DUMP",
        )
        self.assertEqual(emit_context.model_backend.config["line_name"], "ALL_MAIN")
        self.assertEqual(build_model_backend(emit_context, line_name="ALL_DUMP").line_name, "ALL_DUMP")
        self.assertTrue(str(runtime.vm.root).endswith("src/virtual_machine/irfel_elegant"))
        self.assertTrue(str(runtime.softioc.root).endswith("src/softIOC/irfel"))
        assert orbit_context.orbit_workflow is not None
        assert vm_orbit_context.orbit_workflow is not None
        self.assertEqual(len(orbit_context.orbit_workflow.bpms), 5)
        self.assertEqual(len(orbit_context.orbit_workflow.xcors), 5)
        self.assertEqual(len(orbit_context.orbit_workflow.ycors), 5)
        self.assertEqual(len(vm_orbit_context.orbit_workflow.bpms), 5)
        self.assertEqual(orbit_context.orbit_workflow.bpms[0], "BPM03")
        self.assertEqual(orbit_context.orbit_workflow.bpms[-1], "BPM10")
        self.assertEqual(
            orbit_context.orbit_workflow.default_target_bpms,
            ("BPM03", "BPM07", "BPM08", "BPM09", "BPM10"),
        )
        self.assertEqual(orbit_context.orbit_workflow.xcors[-1], "HC07")
        self.assertEqual(orbit_context.orbit_workflow.ycors[-1], "VC07")
        self.assertEqual(workflow["response_wait_s_by_backend"]["real"], 1.0)
        self.assertEqual(workflow["corrector_upperlimit_by_backend"]["vm"]["value"], 0.001)
        self.assertEqual(workflow["corrector_upperlimit_by_backend"]["vm"]["unit"], "rad")
        self.assertEqual(workflow["corrector_upperlimit_by_backend"]["real"]["value"], 5.0)
        self.assertEqual(workflow["corrector_upperlimit_by_backend"]["real"]["unit"], "A")
        irfel_vm_orbit_runtime = orbit_runtime.load_orbit_runtime_settings(vm_orbit_context)
        irfel_real_orbit_runtime = orbit_runtime.load_orbit_runtime_settings(orbit_context)
        self.assertEqual(irfel_vm_orbit_runtime["corrector_upperlimit"], 0.001)
        self.assertEqual(irfel_vm_orbit_runtime["corrector_upperlimit_unit"], "rad")
        self.assertEqual(irfel_real_orbit_runtime["corrector_upperlimit"], 5.0)
        self.assertEqual(irfel_real_orbit_runtime["corrector_upperlimit_unit"], "A")
        self.assertEqual(irfel_vm_orbit_runtime["runtime_defaults"]["method"], "global")
        self.assertEqual(irfel_vm_orbit_runtime["runtime_defaults"]["sampling_interval_s"], 2.0)
        self.assertEqual(irfel_vm_orbit_runtime["runtime_defaults"]["accuracy_um"], 10.0)
        self.assertEqual(irfel_vm_orbit_runtime["runtime_defaults"]["samples_per_step"], 1)
        self.assertEqual(irfel_vm_orbit_runtime["runtime_defaults"]["global_max_iter"], 20)
        self.assertEqual(irfel_vm_orbit_runtime["runtime_defaults"]["one_to_one_gain"], 1.0)
        self.assertEqual(irfel_real_orbit_runtime["runtime_defaults"], irfel_vm_orbit_runtime["runtime_defaults"])
        self.assertEqual(beam_workflow["default_flag"], "PRF03")
        self.assertEqual(
            beam_workflow["flag_pixel_geometry"]["default"]["vm"]["shape"],
            [360, 270],
        )
        self.assertEqual(
            resolve_flag_pixel_geometry(
                beam_workflow,
                "workflows.beam_monitor",
                "vm",
                "PRF03",
            ).shape,
            (360, 270),
        )
        self.assertEqual(
            resolve_flag_pixel_geometry(
                beam_workflow,
                "workflows.beam_monitor",
                "real",
                "PRF04",
            ).pixel_width_mm,
            0.02,
        )
        self.assertEqual(
            resolve_channel(profile, "PRF03", "sigx", "vm"),
            "IRFEL:VM:FLAG:PRF03:sigx",
        )
        self.assertEqual(
            resolve_channel(profile, "PRF03", "sigy", "vm"),
            "IRFEL:VM:FLAG:PRF03:sigy",
        )
        self.assertEqual(
            resolve_channel(profile, "PRF04", "sigx", "vm"),
            "IRFEL:VM:FLAG:PRF04:sigx",
        )
        self.assertEqual(
            resolve_channel(profile, "PRF04", "sigy", "vm"),
            "IRFEL:VM:FLAG:PRF04:sigy",
        )
        self.assertEqual(energy_workflow["flag_element"], "PRFESA")
        self.assertEqual(energy_workflow["flag_image_channel"], "image")
        self.assertEqual(energy_workflow["vm_watch_element"], "PRFESA")
        self.assertEqual(energy_workflow["esa_quads"], ["QM19", "QM20"])
        self.assertEqual(energy_workflow["energy0_default_mev"], 36)
        self.assertEqual(emit_workflow["default_preset"], "emit_qm12_prf04")
        self.assertEqual(emit_workflow["presets"][0]["quad"], "QM12")
        self.assertEqual(emit_workflow["presets"][0]["flag"], "PRF04")
        self.assertEqual(emit_workflow["presets"][0]["model_line"], "ALL_DUMP")
        self.assertEqual(bba_context.app_name, "bba")
        self.assertEqual(bba_context.control_backend.name, "vm")
        self.assertIsNotNone(bba_context.bba_workflow)
        assert bba_context.bba_workflow is not None
        self.assertEqual(bba_context.bba_workflow.standard.control_backends, ("vm",))
        self.assertEqual(bba_context.bba_workflow.bba2.control_backends, ("vm",))
        self.assertEqual(bba_context.bba_workflow.standard.quads, ())
        self.assertEqual(bba_context.bba_workflow.standard.correctors, ())
        self.assertEqual(bba_context.bba_workflow.standard.bpm1, ())
        self.assertEqual(bba_context.bba_workflow.standard.bpm2, ())
        self.assertEqual(bba_workflow["standard"]["control_backends"], ["vm"])
        self.assertEqual(bba_workflow["write_control"]["real"], "blocked")
        self.assertIn("BPM02", vm_start_ids)
        self.assertEqual(vm_end_ids, ("PRF03",))
        self.assertEqual(vm_default_start, "QM13")
        self.assertEqual(vm_default_end, "PRF03")
        self.assertEqual(
            tuple(choice.id for choice in vm_workflow.predefined_usedlines),
            ("ALL_MAIN", "ALL_ESA", "ALL_DUMP"),
        )
        self.assertEqual(vm_workflow.segment_wait_s, 8.0)
        self.assertEqual(vm_workflow.local_segments[1].parent_usedline, "ALL_ESA")
        self.assertEqual(vm_workflow.local_segments[1].end_ids, ("PRFESA",))
        self.assertEqual(vm_workflow.local_segments[2].parent_usedline, "ALL_DUMP")
        self.assertEqual(vm_workflow.local_segments[2].end_ids, ("PRF04",))
        self.assertIsNone(orbit_display_context.orbit_workflow)
        self.assertTrue(
            str(orbit_runtime_paths["response_matrix_dir"]).endswith(
                "src/apps/orbit_correct/runtime/irfel/vm/matrices"
            )
        )
        self.assertTrue(
            str(orbit_runtime_paths["active_response_path"]).endswith(
                "src/apps/orbit_correct/runtime/irfel/vm/active_response.json"
            )
        )
        self.assertTrue(
            str(orbit_runtime_paths["corrector_state_path"]).endswith(
                "src/apps/orbit_correct/runtime/irfel/vm/cor_temp.txt"
            )
        )

    def test_orbit_response_matrix_snapshots_are_active_and_profile_checked(self):
        from half_linac.src.apps.orbit_correct import profile_runtime as orbit_runtime

        context = load_app_context(
            "orbit_correct",
            machine_id="irfel",
            control_backend="vm",
        )
        matrix = np.eye(10)

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(orbit_runtime, "ORBIT_RUNTIME_ROOT", Path(temp_dir)):
                record = orbit_runtime.write_response_matrix_snapshot(context, matrix)
                active_path = orbit_runtime.resolve_active_response_matrix(context)
                records = orbit_runtime.list_response_matrix_records(context)

                self.assertEqual(active_path, Path(record["matrix_path"]))
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]["bpms"][0], "BPM03")
                self.assertEqual(records[0]["shape"], [10, 10])

                # Dimension is checked when loading, not only when selecting a file.
                np.savetxt(active_path, np.eye(19))
                with self.assertRaisesRegex(ValueError, "shape"):
                    orbit_runtime.resolve_active_response_matrix(context)

    def test_orbit_response_matrix_quality_rejects_zero_response_columns(self):
        from half_linac.src.apps.orbit_correct import profile_runtime as orbit_runtime

        context = load_app_context(
            "orbit_correct",
            machine_id="irfel",
            control_backend="vm",
        )
        matrix = np.eye(10)
        matrix[0:5, 2] = 0.0
        matrix[5:10, 8] = 0.0

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(orbit_runtime, "ORBIT_RUNTIME_ROOT", Path(temp_dir)):
                paths = orbit_runtime.resolve_orbit_runtime_paths(context)
                with self.assertRaisesRegex(ValueError, "zero-response corrector column"):
                    orbit_runtime.write_response_matrix_snapshot(context, matrix)
                self.assertFalse(paths["active_response_path"].exists())
                self.assertEqual(list(paths["response_matrix_dir"].glob("response_*.json")), [])

                record = orbit_runtime.write_response_matrix_snapshot(context, np.eye(10))
                np.savetxt(record["matrix_path"], matrix)
                with self.assertRaisesRegex(ValueError, "zero-response corrector column"):
                    orbit_runtime.resolve_active_response_matrix(context)

                rank_bad_matrix = np.eye(10)
                rank_bad_matrix[0:5, 1] = rank_bad_matrix[0:5, 0]
                with self.assertRaisesRegex(ValueError, "rank deficient"):
                    orbit_runtime.validate_response_matrix_quality(context, rank_bad_matrix)

    def test_orbit_global_correction_uses_selected_bpm_rows_and_all_correctors(self):
        from half_linac.src.apps.orbit_correct import profile_runtime as orbit_runtime
        from half_linac.src.apps.orbit_correct.correct import OrbitCorrector

        context = load_app_context(
            "orbit_correct",
            machine_id="irfel",
            control_backend="vm",
        )
        matrix = np.eye(10)

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(orbit_runtime, "ORBIT_RUNTIME_ROOT", Path(temp_dir)):
                orbit_runtime.write_response_matrix_snapshot(context, matrix)
                with patch.dict(
                    os.environ,
                    {
                        "HALF_LINAC_MACHINE_ID": "irfel",
                        "HALF_LINAC_CONTROL_BACKEND": "vm",
                    },
                ):
                    corrector = OrbitCorrector(
                        target_BPMlist=["BPM03", "BPM09", "BPM10"],
                        target_BPMx_values=[0.0, 0.0, 0.0],
                        target_BPMy_values=[0.0, 0.0, 0.0],
                    )
                    corrector._compute_svd(min_singular_value=1e-12)

        self.assertEqual(corrector.target_indices, [0, 3, 4])
        self.assertEqual(corrector.cor_x_list_target, ["HC01", "HC06", "HC07"])
        self.assertEqual(corrector.pseudo_inverse_x.shape, (5, 3))
        self.assertEqual(corrector.pseudo_inverse_y.shape, (5, 3))
        np.testing.assert_allclose(
            corrector.pseudo_inverse_x @ np.array([1.0, 2.0, 3.0]),
            np.array([1.0, 0.0, 0.0, 2.0, 3.0]),
        )

    def test_orbit_global_correction_can_limit_corrector_columns(self):
        from half_linac.src.apps.orbit_correct import profile_runtime as orbit_runtime
        from half_linac.src.apps.orbit_correct.correct import OrbitCorrector

        context = load_app_context(
            "orbit_correct",
            machine_id="irfel",
            control_backend="vm",
        )
        matrix = np.eye(10)

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(orbit_runtime, "ORBIT_RUNTIME_ROOT", Path(temp_dir)):
                orbit_runtime.write_response_matrix_snapshot(context, matrix)
                with patch.dict(
                    os.environ,
                    {
                        "HALF_LINAC_MACHINE_ID": "irfel",
                        "HALF_LINAC_CONTROL_BACKEND": "vm",
                    },
                ):
                    corrector = OrbitCorrector(
                        target_BPMlist=["BPM09", "BPM10"],
                        target_BPMx_values=[0.0, 0.0],
                        target_BPMy_values=[0.0, 0.0],
                        global_xcor_list=["HC06", "HC07"],
                        global_ycor_list=["VC06", "VC07"],
                    )
                    corrector._compute_svd(min_singular_value=1e-12)

        self.assertEqual(corrector.target_indices, [3, 4])
        self.assertEqual(corrector.global_xcor_list, ["HC06", "HC07"])
        self.assertEqual(corrector.global_ycor_list, ["VC06", "VC07"])
        self.assertEqual(corrector.global_xcor_indices, [3, 4])
        self.assertEqual(corrector.global_ycor_indices, [3, 4])
        self.assertEqual(corrector.pseudo_inverse_x.shape, (2, 2))
        self.assertEqual(corrector.pseudo_inverse_y.shape, (2, 2))
        np.testing.assert_allclose(
            corrector.pseudo_inverse_x @ np.array([1.0, 2.0]),
            np.array([1.0, 2.0]),
        )

    def test_irfel_profile_exposes_imported_real_magnet_channels(self):
        profile = load_profile("irfel")

        self.assertEqual(len(list_elements(profile, "quad")), 20)
        self.assertEqual(len(list_elements(profile, "bend")), 3)
        self.assertEqual(len(list_elements(profile, "solenoid")), 4)
        self.assertEqual(len(list_elements(profile, "modulator")), 2)
        self.assertEqual(len(list_elements(profile, "flag")), 5)
        self.assertEqual(
            resolve_channel(profile, "QM01", "k1", "real"),
            "IRFEL:PS:QM01:current:ao",
        )
        self.assertEqual(
            resolve_channel(profile, "QM20", "readback", "real"),
            "IRFEL:PS:QM20:current:ai",
        )
        self.assertEqual(
            resolve_channel(profile, "BM01", "current_set", "real"),
            "IRFEL:PS:BM01:current:ao",
        )
        self.assertEqual(
            resolve_channel(profile, "MS01", "setpoint", "real"),
            "IRFEL:PS:MS01:current:ao",
        )
        self.assertEqual(
            resolve_channel(profile, "MODULATOR_HV1", "voltage_set", "real"),
            "IRFEL:modulator1:HV:set:ao",
        )
        self.assertEqual(
            resolve_channel(profile, "BPM01", "x", "vm"),
            "IRFEL:VM:BPM:BPM01:X",
        )
        self.assertEqual(
            resolve_channel(profile, "PRF01", "image", "vm"),
            "IRFEL:VM:FLAG:PRF01:image1:ArrayData",
        )
        self.assertEqual(
            resolve_channel(profile, "PRF03", "image", "vm"),
            "IRFEL:VM:FLAG:PRF03:image1:ArrayData",
        )
        self.assertEqual(
            resolve_channel(profile, "PRFESA", "image", "vm"),
            "IRFEL:VM:FLAG:PRFESA:image1:ArrayData",
        )

    def test_half_energy_spectrum_workflow_keeps_real_energy_setpoint_pv(self):
        profile = load_profile("half")
        workflow = get_workflow(profile, "energy_spectrum")
        self.assertEqual(
            workflow["energy_set_pv"]["real"],
            "HALF:IN:ESA:PRF01:EnergySet",
        )
        self.assertEqual(workflow["vm_watch_element"], "PRFESA")

    def test_virtual_machine_segment_choices_fall_back_to_quad_and_flag_inference(self):
        machine_json = {
            "schema_version": "1",
            "machine": {
                "id": "vmfallback",
                "family": "linac",
                "display_name": "VM Fallback",
                "default_mode": "real",
            },
            "elements": [
                {
                    "id": "Q01",
                    "kind": "quad",
                    "display_name": "Q01",
                    "order": 1,
                    "tags": [],
                    "limits": {},
                    "logical_channels": ["k1"],
                },
                {
                    "id": "Q02",
                    "kind": "quad",
                    "display_name": "Q02",
                    "order": 2,
                    "tags": [],
                    "limits": {},
                    "logical_channels": ["k1"],
                },
                {
                    "id": "PRF01",
                    "kind": "flag",
                    "display_name": "PRF01",
                    "order": 3,
                    "tags": [],
                    "limits": {},
                    "logical_channels": ["image"],
                },
            ],
        }
        real_channels = {
            "backend": "real",
            "channels": {
                "Q01": {"k1": "REAL:Q01:K1"},
                "Q02": {"k1": "REAL:Q02:K1"},
                "PRF01": {"image": "REAL:PRF01:IMAGE"},
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            _write_directory_profile_fixture(
                temp_root,
                "vmfallback",
                machine_json,
                backends={"real": real_channels},
            )
            with patch("half_linac.src.shared.machine_profile.loader.repo_root", return_value=temp_root):
                profile = load_profile("vmfallback")
                start_ids, end_ids, default_start, default_end = resolve_virtual_machine_segment_choices(profile)

        self.assertEqual(start_ids, ("Q01", "Q02"))
        self.assertEqual(end_ids, ("PRF01",))
        self.assertEqual(default_start, "Q01")
        self.assertEqual(default_end, "PRF01")

    def test_machine_acceptance_validator_reports_missing_runtime_paths(self):
        machine_json = {
            "schema_version": "1",
            "machine": {
                "id": "runtimebroken",
                "family": "linac",
                "display_name": "Runtime Broken",
                "default_mode": "real",
            },
            "runtime": {
                "vm": {
                    "root": "missing/vm",
                    "ui_entrypoint": "missing/vm/mainVM.py",
                    "manager_entrypoint": "missing/vm/start_VM.py",
                    "runtime_json": "missing/vm/runtime.json",
                    "bootstrap_lattice": "missing/vm/elegant/lattice_ini.lte",
                    "bootstrap_ele": "missing/vm/elegant/one_ini.ele",
                    "line_name": "ALL",
                },
                "softioc": {
                    "root": "missing/softioc",
                    "substitutions_file": "missing/softioc/db/runtime.substitutions",
                },
            },
            "elements": [
                {
                    "id": "BPM01",
                    "kind": "bpm",
                    "display_name": "BPM01",
                    "order": 1,
                    "tags": [],
                    "limits": {},
                    "logical_channels": ["x", "y"],
                },
                {
                    "id": "Q01",
                    "kind": "quad",
                    "display_name": "Q01",
                    "order": 2,
                    "tags": [],
                    "limits": {},
                    "logical_channels": ["k1"],
                },
                {
                    "id": "PRF01",
                    "kind": "flag",
                    "display_name": "PRF01",
                    "order": 3,
                    "tags": [],
                    "limits": {},
                    "logical_channels": ["image"],
                },
            ],
        }
        real_channels = {
            "backend": "real",
            "channels": {
                "BPM01": {"x": "REAL:BPM01:X", "y": "REAL:BPM01:Y"},
                "Q01": {"k1": "REAL:Q01:K1"},
                "PRF01": {"image": "REAL:PRF01:IMAGE"},
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            _write_directory_profile_fixture(
                temp_root,
                "runtimebroken",
                machine_json,
                backends={"real": real_channels},
            )
            with patch("half_linac.src.shared.machine_profile.loader.repo_root", return_value=temp_root):
                report = validate_machine_profile("runtimebroken")

        self.assertFalse(report.ok)
        runtime_check = report.get_check("runtime")
        self.assertIsNotNone(runtime_check)
        assert runtime_check is not None
        self.assertEqual(runtime_check.status, "fail")
        self.assertIn("runtime.vm.root", runtime_check.detail)

    def test_machine_acceptance_validator_skips_runtime_for_real_only_orbit_profile(self):
        machine_json = {
            "schema_version": "1",
            "machine": {
                "id": "realorbit",
                "family": "linac",
                "display_name": "Real Orbit",
                "default_mode": "real",
            },
            "elements": [
                {
                    "id": "BPM01",
                    "kind": "bpm",
                    "display_name": "BPM01",
                    "order": 1,
                    "tags": ["orbit"],
                    "limits": {},
                    "logical_channels": ["x", "y"],
                },
                {
                    "id": "XC01",
                    "kind": "corr",
                    "display_name": "XC01",
                    "order": 2,
                    "plane": "x",
                    "tags": ["orbit"],
                    "limits": {},
                    "logical_channels": ["setpoint"],
                },
                {
                    "id": "YC01",
                    "kind": "corr",
                    "display_name": "YC01",
                    "order": 3,
                    "plane": "y",
                    "tags": ["orbit"],
                    "limits": {},
                    "logical_channels": ["setpoint"],
                },
            ],
        }
        real_channels = {
            "backend": "real",
            "channels": {
                "BPM01": {"x": "REAL:BPM01:X", "y": "REAL:BPM01:Y"},
                "XC01": {"setpoint": "REAL:XC01"},
                "YC01": {"setpoint": "REAL:YC01"},
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            _write_directory_profile_fixture(
                temp_root,
                "realorbit",
                machine_json,
                backends={"real": real_channels},
            )
            with patch("half_linac.src.shared.machine_profile.loader.repo_root", return_value=temp_root):
                report = validate_machine_profile("realorbit")

        self.assertTrue(report.ok, report.format_text())
        runtime_check = report.get_check("runtime")
        self.assertIsNotNone(runtime_check)
        assert runtime_check is not None
        self.assertEqual(runtime_check.status, "skip")
        self.assertEqual(report.get_check("app:orbit_correct").status, "pass")

    def test_virtual_machine_workflow_accepts_non_quad_segment_start_ids(self):
        machine_json = {
            "schema_version": "1",
            "machine": {
                "id": "vminvalidstart",
                "family": "linac",
                "display_name": "VM Invalid Start",
                "default_mode": "real",
            },
            "elements": [
                {
                    "id": "Q01",
                    "kind": "quad",
                    "display_name": "Q01",
                    "order": 1,
                    "tags": [],
                    "limits": {},
                    "logical_channels": ["k1"],
                },
                {
                    "id": "PRF01",
                    "kind": "flag",
                    "display_name": "PRF01",
                    "order": 2,
                    "tags": [],
                    "limits": {},
                    "logical_channels": ["image"],
                },
            ],
        }
        real_channels = {
            "backend": "real",
            "channels": {
                "Q01": {"k1": "REAL:Q01:K1"},
                "PRF01": {"image": "REAL:PRF01:IMAGE"},
            },
        }
        vm_workflow = {
            "simple_segment_start_ids": ["PRF01"],
            "simple_segment_end_ids": ["PRF01"],
            "default_start_id": "PRF01",
            "default_end_id": "PRF01",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            _write_directory_profile_fixture(
                temp_root,
                "vminvalidstart",
                machine_json,
                backends={"real": real_channels},
                apps={"virtual_machine": vm_workflow},
            )
            with patch("half_linac.src.shared.machine_profile.loader.repo_root", return_value=temp_root):
                profile = load_profile("vminvalidstart")
                start_ids, end_ids, default_start, default_end = resolve_virtual_machine_segment_choices(profile)

        self.assertEqual(start_ids, ("PRF01",))
        self.assertEqual(end_ids, ("PRF01",))
        self.assertEqual(default_start, "PRF01")
        self.assertEqual(default_end, "PRF01")

    def test_virtual_machine_workflow_rejects_unknown_end_ids(self):
        machine_json = {
            "schema_version": "1",
            "machine": {
                "id": "vminvalidend",
                "family": "linac",
                "display_name": "VM Invalid End",
                "default_mode": "real",
            },
            "elements": [
                {
                    "id": "Q01",
                    "kind": "quad",
                    "display_name": "Q01",
                    "order": 1,
                    "tags": [],
                    "limits": {},
                    "logical_channels": ["k1"],
                },
                {
                    "id": "PRF01",
                    "kind": "flag",
                    "display_name": "PRF01",
                    "order": 2,
                    "tags": [],
                    "limits": {},
                    "logical_channels": ["image"],
                },
            ],
        }
        real_channels = {
            "backend": "real",
            "channels": {
                "Q01": {"k1": "REAL:Q01:K1"},
                "PRF01": {"image": "REAL:PRF01:IMAGE"},
            },
        }
        vm_workflow = {
            "simple_segment_start_ids": ["Q01"],
            "simple_segment_end_ids": ["PRF02"],
            "default_start_id": "Q01",
            "default_end_id": "PRF02",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            _write_directory_profile_fixture(
                temp_root,
                "vminvalidend",
                machine_json,
                backends={"real": real_channels},
                apps={"virtual_machine": vm_workflow},
            )
            with patch("half_linac.src.shared.machine_profile.loader.repo_root", return_value=temp_root):
                with self.assertRaisesRegex(MachineProfileError, "Unknown element id: PRF02"):
                    load_profile("vminvalidend")

    def test_virtual_machine_workflow_rejects_default_outside_candidate_list(self):
        machine_json = {
            "schema_version": "1",
            "machine": {
                "id": "vminvaliddefault",
                "family": "linac",
                "display_name": "VM Invalid Default",
                "default_mode": "real",
            },
            "elements": [
                {
                    "id": "Q01",
                    "kind": "quad",
                    "display_name": "Q01",
                    "order": 1,
                    "tags": [],
                    "limits": {},
                    "logical_channels": ["k1"],
                },
                {
                    "id": "Q02",
                    "kind": "quad",
                    "display_name": "Q02",
                    "order": 2,
                    "tags": [],
                    "limits": {},
                    "logical_channels": ["k1"],
                },
                {
                    "id": "PRF01",
                    "kind": "flag",
                    "display_name": "PRF01",
                    "order": 3,
                    "tags": [],
                    "limits": {},
                    "logical_channels": ["image"],
                },
            ],
        }
        real_channels = {
            "backend": "real",
            "channels": {
                "Q01": {"k1": "REAL:Q01:K1"},
                "Q02": {"k1": "REAL:Q02:K1"},
                "PRF01": {"image": "REAL:PRF01:IMAGE"},
            },
        }
        vm_workflow = {
            "simple_segment_start_ids": ["Q01"],
            "simple_segment_end_ids": ["PRF01"],
            "default_start_id": "Q02",
            "default_end_id": "PRF01",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            _write_directory_profile_fixture(
                temp_root,
                "vminvaliddefault",
                machine_json,
                backends={"real": real_channels},
                apps={"virtual_machine": vm_workflow},
            )
            with patch("half_linac.src.shared.machine_profile.loader.repo_root", return_value=temp_root):
                with self.assertRaisesRegex(MachineProfileError, "default_start_id must belong"):
                    load_profile("vminvaliddefault")

    def test_virtual_machine_usedline_workflow_accepts_lattice_only_segment_ids(self):
        machine_json = {
            "schema_version": "1",
            "machine": {
                "id": "vmnewworkflow",
                "family": "linac",
                "display_name": "VM New Workflow",
                "default_mode": "real",
            },
            "elements": [
                {
                    "id": "Q01",
                    "kind": "quad",
                    "display_name": "Q01",
                    "order": 1,
                    "tags": [],
                    "limits": {},
                    "logical_channels": ["k1"],
                },
                {
                    "id": "PRF01",
                    "kind": "flag",
                    "display_name": "PRF01",
                    "order": 2,
                    "tags": [],
                    "limits": {},
                    "logical_channels": ["image"],
                },
            ],
        }
        real_channels = {
            "backend": "real",
            "channels": {
                "Q01": {"k1": "REAL:Q01:K1"},
                "PRF01": {"image": "REAL:PRF01:IMAGE"},
            },
        }
        vm_workflow = {
            "predefined_usedlines": [
                {"id": "ALL_MAIN", "label": "Main Line", "role": "main"},
                {"id": "ALL_ESA", "label": "ESA Line", "role": "energy_spectrum"},
            ],
            "default_usedline": "ALL_MAIN",
            "local_segments": [
                {
                    "id": "esa_segment",
                    "label": "ESA Segment",
                    "parent_usedline": "ALL_ESA",
                    "start_ids": ["QM19"],
                    "end_ids": ["PRFESA"],
                    "default_start_id": "QM19",
                    "default_end_id": "PRFESA",
                }
            ],
            "default_segment_id": "esa_segment",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            _write_directory_profile_fixture(
                temp_root,
                "vmnewworkflow",
                machine_json,
                backends={"real": real_channels},
                apps={"virtual_machine": vm_workflow},
            )
            with patch("half_linac.src.shared.machine_profile.loader.repo_root", return_value=temp_root):
                profile = load_profile("vmnewworkflow")
                workflow = resolve_virtual_machine_usedline_workflow(profile)

        self.assertEqual(workflow.default_usedline, "ALL_MAIN")
        self.assertEqual(workflow.local_segments[0].parent_usedline, "ALL_ESA")
        self.assertEqual(workflow.local_segments[0].end_ids, ("PRFESA",))

    def test_virtual_machine_usedline_workflow_rejects_unknown_default_segment(self):
        machine_json = {
            "schema_version": "1",
            "machine": {
                "id": "vmnewworkflowbad",
                "family": "linac",
                "display_name": "VM New Workflow Bad",
                "default_mode": "real",
            },
            "elements": [
                {
                    "id": "Q01",
                    "kind": "quad",
                    "display_name": "Q01",
                    "order": 1,
                    "tags": [],
                    "limits": {},
                    "logical_channels": ["k1"],
                },
                {
                    "id": "PRF01",
                    "kind": "flag",
                    "display_name": "PRF01",
                    "order": 2,
                    "tags": [],
                    "limits": {},
                    "logical_channels": ["image"],
                },
            ],
        }
        real_channels = {
            "backend": "real",
            "channels": {
                "Q01": {"k1": "REAL:Q01:K1"},
                "PRF01": {"image": "REAL:PRF01:IMAGE"},
            },
        }
        vm_workflow = {
            "predefined_usedlines": ["ALL_MAIN"],
            "default_usedline": "ALL_MAIN",
            "local_segments": [
                {
                    "id": "main_segment",
                    "label": "Main Segment",
                    "parent_usedline": "ALL_MAIN",
                    "start_ids": ["Q01"],
                    "end_ids": ["PRF01"],
                    "default_start_id": "Q01",
                    "default_end_id": "PRF01",
                }
            ],
            "default_segment_id": "missing_segment",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            _write_directory_profile_fixture(
                temp_root,
                "vmnewworkflowbad",
                machine_json,
                backends={"real": real_channels},
                apps={"virtual_machine": vm_workflow},
            )
            with patch("half_linac.src.shared.machine_profile.loader.repo_root", return_value=temp_root):
                with self.assertRaisesRegex(MachineProfileError, "default_segment_id must belong"):
                    load_profile("vmnewworkflowbad")

    def test_energy_spectrum_workflow_requires_vm_watch_element(self):
        machine_json = {
            "schema_version": "1",
            "machine": {
                "id": "missingwatch",
                "family": "linac",
                "display_name": "Missing Watch",
                "default_mode": "real",
            },
            "elements": [
                {
                    "id": "PRF01",
                    "kind": "flag",
                    "display_name": "PRF01",
                    "order": 1,
                    "tags": ["energy_spectrum"],
                    "limits": {},
                    "logical_channels": ["image", "exposure_time"],
                },
                {
                    "id": "SM",
                    "kind": "bend",
                    "display_name": "SM",
                    "order": 2,
                    "tags": ["energy_spectrum"],
                    "limits": {},
                    "logical_channels": ["current_set"],
                },
                {
                    "id": "QE01",
                    "kind": "quad",
                    "display_name": "QE01",
                    "order": 3,
                    "tags": ["energy_spectrum"],
                    "limits": {},
                    "logical_channels": ["k1"],
                },
                {
                    "id": "QE02",
                    "kind": "quad",
                    "display_name": "QE02",
                    "order": 4,
                    "tags": ["energy_spectrum"],
                    "limits": {},
                    "logical_channels": ["k1"],
                },
                {
                    "id": "QE03",
                    "kind": "quad",
                    "display_name": "QE03",
                    "order": 5,
                    "tags": ["energy_spectrum"],
                    "limits": {},
                    "logical_channels": ["k1"],
                },
            ],
        }
        real_channels = {
            "backend": "real",
            "channels": {
                "PRF01": {
                    "image": "REAL:PRF01:IMAGE",
                    "exposure_time": "REAL:PRF01:EXPO",
                },
                "SM": {"current_set": "REAL:SM:CURRENT"},
                "QE01": {"k1": "REAL:QE01:K1"},
                "QE02": {"k1": "REAL:QE02:K1"},
                "QE03": {"k1": "REAL:QE03:K1"},
            },
        }
        energy_spectrum_json = {
            "flag_element": "PRF01",
            "flag_image_channel": "image",
            "flag_exposure_channel": "exposure_time",
            "flag_pixel_shape": {"real": [1440, 1080]},
            "flag_pixel_width_mm": {"real": 0.02},
            "bend_element": "SM",
            "model_backend": "simulation",
            "bend_scan": {"min": 0, "max": 10, "coarse_steps": 2, "fine_steps": 2},
            "esa_quads": ["QE01", "QE02", "QE03"],
            "default_start_element": "QE01",
        }
        model_backend_json = {
            "backend": "simulation",
            "engine": "elegant",
            "config": {
                "working_dir": "src/virtual_machine/half_elegant/elegant",
                "source_lattice": "src/virtual_machine/half_elegant/elegant/lattice_ini.lte",
                "energy_ini_ele_file": "src/virtual_machine/half_elegant/elegant/esa_ini.ele",
                "energy_json_path": "src/virtual_machine/half_elegant/esa.json",
                "energy_lte_file": "src/virtual_machine/half_elegant/elegant/esa.lte",
                "energy_ele_file": "src/virtual_machine/half_elegant/elegant/esa.ele",
                "energy_mat_file": "src/virtual_machine/half_elegant/elegant/esa.mat",
                "energy_twi_file": "src/virtual_machine/half_elegant/elegant/esa.twi",
                "energy_log": "esa.log",
                "energy_dispersion_line_name": "ESA",
                "energy_twiss_line_name": "ESA",
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            machine_dir = temp_root / "configs" / "machines" / "missingwatch"
            (machine_dir / "control_backends").mkdir(parents=True)
            (machine_dir / "apps").mkdir(parents=True)
            (machine_dir / "model_backends").mkdir(parents=True)
            (machine_dir / "machine.json").write_text(
                json.dumps(machine_json, indent=2),
                encoding="utf-8",
            )
            (machine_dir / "control_backends" / "real.json").write_text(
                json.dumps(real_channels, indent=2),
                encoding="utf-8",
            )
            (machine_dir / "apps" / "energy_spectrum.json").write_text(
                json.dumps(energy_spectrum_json, indent=2),
                encoding="utf-8",
            )
            (machine_dir / "model_backends" / "simulation.elegant.json").write_text(
                json.dumps(model_backend_json, indent=2),
                encoding="utf-8",
            )

            with patch("half_linac.src.shared.machine_profile.loader.repo_root", return_value=temp_root):
                with self.assertRaisesRegex(MachineProfileError, "vm_watch_element"):
                    load_app_context("energy_spectrum", machine_id="missingwatch")

    def test_softioc_substitutions_include_vm_aliases_for_profile_only_elements(self):
        substitutions = (
            REPO_ROOT / "src" / "softIOC" / "halflinac" / "db" / "halflinac.substitutions"
        ).read_text(encoding="utf-8")
        self.assertIn('pattern {QUAD, RECORD, K1ALIAS}', substitutions)
        self.assertIn('pattern {COR, SETRECORD, SETALIAS, READRECORD}', substitutions)
        self.assertIn('pattern {FLAG, ESARECORD, ESAALIAS}', substitutions)
        self.assertIn('{ "QL03", "VMIOC:QUAD:QL03:K1", "HALF:IN:AP:QUAD:QL03:K1:ao" }', substitutions)
        self.assertIn(
            '{ "XC00", "VMIOC:COR:XC00:SET", "HALF:IN:PS:XC00:current:ao", "VMIOC:COR:XC00:READ" }',
            substitutions,
        )
        self.assertIn(
            '{ "HC01", "VMIOC:COR:HC01:SET", "HALF:IN:PS:HC01:current:ao", "VMIOC:COR:HC01:READ" }',
            substitutions,
        )
        self.assertNotIn("CQ1", substitutions)
        self.assertNotIn("CQ3", substitutions)
        self.assertNotIn("MQ1", substitutions)
        self.assertNotIn("MQ11", substitutions)
        self.assertNotIn("HIC01", substitutions)
        self.assertNotIn("VIC01", substitutions)
        self.assertIn(
            '{ "PRF07", "VMIOC:FLAG:PRF07:ESA_IMAGE", "HALF:IN:FLAG:PRFESA:image1:ArrayData:vm" }',
            substitutions,
        )
        self.assertNotIn(
            '{ "PRF04", "VMIOC:FLAG:PRF04:ESA_IMAGE", "VMIOC:FLAG:PRF04:ESA_IMAGE" }',
            substitutions,
        )

    def test_softioc_generator_no_longer_hardcodes_half_alias_builders(self):
        source = (REPO_ROOT / "src" / "softIOC" / "pv_server.py").read_text(encoding="utf-8")
        self.assertNotIn('HALF:IN:PS:', source)
        self.assertNotIn('HALF:IN:AP:QUAD:', source)
        self.assertNotIn('configs/machines/half/machine.json', source)

    def test_invalid_machine_id_from_env_raises(self):
        with patch.dict(os.environ, {"HALF_LINAC_MACHINE_ID": "../escape"}):
            with self.assertRaises(MachineProfileError):
                load_profile()

    def test_load_profile_falls_back_to_legacy_single_file_fixture(self):
        legacy_profile = {
            "schema_version": "1",
            "machine": {
                "id": "legacy",
                "family": "linac",
                "display_name": "Legacy Linac",
                "default_mode": "vm",
            },
            "elements": [
                {
                    "id": "BPM01",
                    "kind": "bpm",
                    "display_name": "BPM01",
                    "order": 1,
                    "tags": ["orbit", "bba"],
                    "limits": {},
                    "channels": {
                        "x": {"vm": "LEGACY:BPM01:X", "real": "REAL:BPM01:X"},
                        "y": {"vm": "LEGACY:BPM01:Y", "real": "REAL:BPM01:Y"},
                    },
                },
                {
                    "id": "XC01",
                    "kind": "corr",
                    "display_name": "XC01",
                    "order": 2,
                    "tags": ["orbit", "bba"],
                    "limits": {},
                    "channels": {
                        "setpoint": {"vm": "LEGACY:XC01", "real": "REAL:XC01"},
                    },
                },
                {
                    "id": "YC01",
                    "kind": "corr",
                    "display_name": "YC01",
                    "order": 3,
                    "tags": ["orbit", "bba"],
                    "limits": {},
                    "channels": {
                        "setpoint": {"vm": "LEGACY:YC01", "real": "REAL:YC01"},
                    },
                },
                {
                    "id": "Q01",
                    "kind": "quad",
                    "display_name": "Q01",
                    "order": 4,
                    "tags": ["bba", "emit_measure"],
                    "limits": {},
                    "channels": {
                        "k1": {"vm": "LEGACY:Q01:K1", "real": "REAL:Q01:K1"},
                    },
                },
                {
                    "id": "PRF01",
                    "kind": "flag",
                    "display_name": "PRF01",
                    "order": 5,
                    "tags": ["emit_measure"],
                    "limits": {},
                    "channels": {
                        "sigx": {"vm": "LEGACY:PRF01:SIGX", "real": "REAL:PRF01:SIGX"},
                        "sigy": {"vm": "LEGACY:PRF01:SIGY", "real": "REAL:PRF01:SIGY"},
                    },
                },
            ],
            "workflows": {
                "orbit": {
                    "bpms": ["BPM01"],
                    "xcors": ["XC01"],
                    "ycors": ["YC01"],
                },
                "bba": {
                    "presets": [
                        {
                            "id": "legacy_bba",
                            "family": "standard",
                            "plane": "x",
                            "quad": "Q01",
                            "corr": "XC01",
                            "bpm1": "BPM01",
                            "bpm2": "BPM01",
                            "scan": {
                                "corr_from": -0.1,
                                "corr_end": 0.1,
                                "corr_steps": 3,
                                "quad_from": 0.0,
                                "quad_end": 1.0,
                                "quad_steps": 3,
                                "samples": 2,
                                "sleeptime": 0.1,
                            },
                        }
                    ],
                    "standard": {
                        "correctors": ["XC01"],
                        "quads": ["Q01"],
                        "bpm1": ["BPM01"],
                        "bpm2": ["BPM01"],
                        "default_preset": "legacy_bba",
                    },
                    "bba2": {
                        "correctors": ["XC01"],
                        "quads": ["Q01"],
                        "bpm1": ["BPM01"],
                        "bpm2": ["BPM01"],
                        "control_backends": ["vm", "real"],
                        "default_preset": "legacy_bba",
                    },
                },
                "emit_measure": {
                    "presets": [
                        {
                            "id": "legacy_emit",
                            "quad": "Q01",
                            "flag": "PRF01",
                            "energy_mev": 220.0,
                            "scan": {
                                "k1_from": 0.0,
                                "k1_end": 1.0,
                                "k1_steps": 3,
                                "samples": 2,
                                "sleeptime": 0.1,
                            },
                        }
                    ],
                    "default_preset": "legacy_emit",
                    "twiss_quads": ["Q01"],
                },
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            legacy_dir = temp_root / "configs" / "machines" / "legacy"
            legacy_dir.mkdir(parents=True)
            (legacy_dir / "profile.json").write_text(
                json.dumps(legacy_profile, indent=2),
                encoding="utf-8",
            )
            with patch("half_linac.src.shared.machine_profile.loader.repo_root", return_value=temp_root):
                profile = load_profile("legacy")
                context = load_app_context("orbit_correct", machine_id="legacy")

        self.assertEqual(profile.machine.id, "legacy")
        self.assertEqual(context.machine.id, "legacy")
        self.assertEqual(resolve_channel(profile, "BPM01", "x", "vm"), "LEGACY:BPM01:X")
        self.assertEqual(profile.get_element("XC01").plane, "x")
        self.assertEqual(profile.get_element("YC01").plane, "y")

    def test_legacy_single_file_profile_model_apps_require_directory_migration(self):
        legacy_profile = {
            "schema_version": "1",
            "machine": {
                "id": "legacy",
                "family": "linac",
                "display_name": "Legacy Linac",
                "default_mode": "vm",
            },
            "elements": [
                {
                    "id": "BPM01",
                    "kind": "bpm",
                    "display_name": "BPM01",
                    "order": 1,
                    "tags": ["orbit", "bba"],
                    "limits": {},
                    "channels": {
                        "x": {"vm": "LEGACY:BPM01:X", "real": "REAL:BPM01:X"},
                        "y": {"vm": "LEGACY:BPM01:Y", "real": "REAL:BPM01:Y"},
                    },
                },
                {
                    "id": "XC01",
                    "kind": "corr",
                    "display_name": "XC01",
                    "order": 2,
                    "tags": ["orbit", "bba"],
                    "limits": {},
                    "channels": {
                        "setpoint": {"vm": "LEGACY:XC01", "real": "REAL:XC01"},
                    },
                },
                {
                    "id": "Q01",
                    "kind": "quad",
                    "display_name": "Q01",
                    "order": 3,
                    "tags": ["bba", "emit_measure"],
                    "limits": {},
                    "channels": {
                        "k1": {"vm": "LEGACY:Q01:K1", "real": "REAL:Q01:K1"},
                    },
                },
                {
                    "id": "PRF01",
                    "kind": "flag",
                    "display_name": "PRF01",
                    "order": 4,
                    "tags": ["emit_measure"],
                    "limits": {},
                    "channels": {
                        "sigx": {"vm": "LEGACY:PRF01:SIGX", "real": "REAL:PRF01:SIGX"},
                        "sigy": {"vm": "LEGACY:PRF01:SIGY", "real": "REAL:PRF01:SIGY"},
                    },
                },
            ],
            "workflows": {
                "emit_measure": {
                    "presets": [
                        {
                            "id": "legacy_emit",
                            "quad": "Q01",
                            "flag": "PRF01",
                            "energy_mev": 220.0,
                            "scan": {
                                "k1_from": 0.0,
                                "k1_end": 1.0,
                                "k1_steps": 3,
                                "samples": 2,
                                "sleeptime": 0.1,
                            },
                        }
                    ],
                    "default_preset": "legacy_emit",
                },
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            legacy_dir = temp_root / "configs" / "machines" / "legacy"
            legacy_dir.mkdir(parents=True)
            (legacy_dir / "profile.json").write_text(
                json.dumps(legacy_profile, indent=2),
                encoding="utf-8",
            )
            with patch("half_linac.src.shared.machine_profile.loader.repo_root", return_value=temp_root):
                with self.assertRaisesRegex(MachineProfileError, "directory machine profile"):
                    load_app_context("emit_measure", machine_id="legacy")

    def test_loader_infers_defaults_when_family_sections_are_omitted(self):
        inferred_profile = {
            "schema_version": "1",
            "machine": {
                "id": "simple",
                "family": "linac",
                "display_name": "Simple Linac",
                "default_mode": "vm",
            },
            "elements": [
                {
                    "id": "BPM01",
                    "kind": "bpm",
                    "display_name": "BPM01",
                    "order": 1,
                    "tags": ["bba"],
                    "limits": {},
                    "channels": {
                        "x": {"vm": "S:BPM01:X", "real": "R:BPM01:X"},
                        "y": {"vm": "S:BPM01:Y", "real": "R:BPM01:Y"},
                    },
                },
                {
                    "id": "XC01",
                    "kind": "corr",
                    "display_name": "XC01",
                    "order": 2,
                    "tags": ["bba"],
                    "limits": {},
                    "channels": {
                        "setpoint": {"vm": "S:XC01", "real": "R:XC01"},
                    },
                },
                {
                    "id": "Q01",
                    "kind": "quad",
                    "display_name": "Q01",
                    "order": 3,
                    "tags": ["bba", "emit"],
                    "limits": {},
                    "channels": {
                        "k1": {"vm": "S:Q01:K1", "real": "R:Q01:K1"},
                    },
                },
                {
                    "id": "PRF01",
                    "kind": "flag",
                    "display_name": "PRF01",
                    "order": 4,
                    "tags": ["emit"],
                    "limits": {},
                    "channels": {
                        "sigx": {"vm": "S:PRF01:X", "real": "R:PRF01:X"},
                        "sigy": {"vm": "S:PRF01:Y", "real": "R:PRF01:Y"},
                    },
                },
            ],
            "workflows": {
                "orbit": {
                    "bpms": ["BPM01"],
                    "xcors": ["XC01"],
                    "ycors": ["XC01"],
                },
                "bba": {
                    "presets": [
                        {
                            "id": "simple_bba1",
                            "family": "standard",
                            "plane": "x",
                            "quad": "Q01",
                            "corr": "XC01",
                            "bpm1": "BPM01",
                            "bpm2": "BPM01",
                        },
                        {
                            "id": "simple_bba2",
                            "family": "bba2",
                            "plane": "x",
                            "quad": "Q01",
                            "corr": "XC01",
                            "bpm1": "BPM01",
                            "bpm2": "BPM01",
                        },
                    ]
                },
                "emit_measure": {
                    "presets": [
                        {
                            "id": "simple_emit",
                            "quad": "Q01",
                            "flag": "PRF01",
                            "energy_mev": 100.0,
                        }
                    ]
                },
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            simple_dir = temp_root / "configs" / "machines" / "simple"
            simple_dir.mkdir(parents=True)
            (simple_dir / "profile.json").write_text(
                json.dumps(inferred_profile, indent=2),
                encoding="utf-8",
            )
            with patch("half_linac.src.shared.machine_profile.loader.repo_root", return_value=temp_root):
                profile = load_profile("simple")
                bba_workflow = load_bba_workflow(profile)
                emit_workflow = load_emit_measure_workflow(profile)

        self.assertEqual(bba_workflow.standard.default_preset, "simple_bba1")
        self.assertEqual(bba_workflow.bba2.default_preset, "simple_bba2")
        self.assertEqual(emit_workflow.default_preset, "simple_emit")

    def test_directory_profile_can_infer_orbit_workflow_without_explicit_file(self):
        machine_json = {
            "schema_version": "1",
            "machine": {
                "id": "dirsimple",
                "family": "linac",
                "display_name": "Directory Simple",
                "default_mode": "vm",
            },
            "elements": [
                {
                    "id": "BPM01",
                    "kind": "bpm",
                    "display_name": "BPM01",
                    "order": 1,
                    "tags": ["orbit", "bba"],
                    "limits": {},
                    "logical_channels": ["x", "y"],
                },
                {
                    "id": "BPM02",
                    "kind": "bpm",
                    "display_name": "BPM02",
                    "order": 2,
                    "tags": ["orbit", "bba"],
                    "limits": {},
                    "logical_channels": ["x", "y"],
                },
                {
                    "id": "XC01",
                    "kind": "corr",
                    "display_name": "XC01",
                    "order": 3,
                    "tags": ["orbit", "bba"],
                    "limits": {},
                    "logical_channels": ["setpoint"],
                },
                {
                    "id": "YC01",
                    "kind": "corr",
                    "display_name": "YC01",
                    "order": 4,
                    "tags": ["orbit", "bba"],
                    "limits": {},
                    "logical_channels": ["setpoint"],
                },
                {
                    "id": "XC02",
                    "kind": "corr",
                    "display_name": "XC02",
                    "order": 5,
                    "tags": ["orbit", "bba"],
                    "limits": {},
                    "logical_channels": ["setpoint"],
                },
                {
                    "id": "YC02",
                    "kind": "corr",
                    "display_name": "YC02",
                    "order": 6,
                    "tags": ["orbit", "bba"],
                    "limits": {},
                    "logical_channels": ["setpoint"],
                },
                {
                    "id": "Q01",
                    "kind": "quad",
                    "display_name": "Q01",
                    "order": 7,
                    "tags": ["bba", "emit"],
                    "limits": {},
                    "logical_channels": ["k1"],
                },
                {
                    "id": "PRF01",
                    "kind": "flag",
                    "display_name": "PRF01",
                    "order": 8,
                    "tags": ["emit"],
                    "limits": {},
                    "logical_channels": ["sigx", "sigy"],
                },
            ],
        }
        backend_channels = {
            "vm": {
                "backend": "vm",
                "channels": {
                    "BPM01": {"x": "VM:BPM01:X", "y": "VM:BPM01:Y"},
                    "BPM02": {"x": "VM:BPM02:X", "y": "VM:BPM02:Y"},
                    "XC01": {"setpoint": "VM:XC01"},
                    "YC01": {"setpoint": "VM:YC01"},
                    "XC02": {"setpoint": "VM:XC02"},
                    "YC02": {"setpoint": "VM:YC02"},
                    "Q01": {"k1": "VM:Q01:K1"},
                    "PRF01": {"sigx": "VM:PRF01:X", "sigy": "VM:PRF01:Y"},
                },
            },
            "real": {
                "backend": "real",
                "channels": {
                    "BPM01": {"x": "REAL:BPM01:X", "y": "REAL:BPM01:Y"},
                    "BPM02": {"x": "REAL:BPM02:X", "y": "REAL:BPM02:Y"},
                    "XC01": {"setpoint": "REAL:XC01"},
                    "YC01": {"setpoint": "REAL:YC01"},
                    "XC02": {"setpoint": "REAL:XC02"},
                    "YC02": {"setpoint": "REAL:YC02"},
                    "Q01": {"k1": "REAL:Q01:K1"},
                    "PRF01": {"sigx": "REAL:PRF01:X", "sigy": "REAL:PRF01:Y"},
                },
            },
        }
        bba_json = {
            "presets": [
                {
                    "id": "dirsimple_bba1",
                    "family": "standard",
                    "plane": "x",
                    "quad": "Q01",
                    "corr": "XC01",
                    "bpm1": "BPM01",
                    "bpm2": "BPM02",
                },
                {
                    "id": "dirsimple_bba2",
                    "family": "bba2",
                    "plane": "x",
                    "quad": "Q01",
                    "corr": "XC02",
                    "bpm1": "BPM01",
                    "bpm2": "BPM02",
                },
            ]
        }
        emit_json = {
            "presets": [
                {
                    "id": "dirsimple_emit",
                    "quad": "Q01",
                    "flag": "PRF01",
                    "energy_mev": 100.0,
                }
            ]
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            machine_dir = temp_root / "configs" / "machines" / "dirsimple"
            (machine_dir / "control_backends").mkdir(parents=True)
            (machine_dir / "apps").mkdir(parents=True)
            (machine_dir / "machine.json").write_text(
                json.dumps(machine_json, indent=2),
                encoding="utf-8",
            )
            for backend_name, payload in backend_channels.items():
                (machine_dir / "control_backends" / f"{backend_name}.json").write_text(
                    json.dumps(payload, indent=2),
                    encoding="utf-8",
                )
            (machine_dir / "apps" / "bba.json").write_text(
                json.dumps(bba_json, indent=2),
                encoding="utf-8",
            )
            (machine_dir / "apps" / "emit_measure.json").write_text(
                json.dumps(emit_json, indent=2),
                encoding="utf-8",
            )

            with patch("half_linac.src.shared.machine_profile.loader.repo_root", return_value=temp_root):
                context = load_app_context("orbit_correct", machine_id="dirsimple")

        assert context.orbit_workflow is not None
        self.assertEqual(context.orbit_workflow.bpms, ("BPM01", "BPM02"))
        self.assertEqual(context.orbit_workflow.xcors, ("XC01", "XC02"))
        self.assertEqual(context.orbit_workflow.ycors, ("YC01", "YC02"))

    def test_partial_directory_machine_can_load_only_requested_app_workflow(self):
        machine_json = {
            "schema_version": "1",
            "machine": {
                "id": "orbitonly",
                "family": "linac",
                "display_name": "Orbit Only",
                "default_mode": "vm",
            },
            "elements": [
                {
                    "id": "BPM01",
                    "kind": "bpm",
                    "display_name": "BPM01",
                    "order": 1,
                    "tags": ["orbit"],
                    "limits": {},
                    "logical_channels": ["x", "y"],
                },
                {
                    "id": "XC01",
                    "kind": "corr",
                    "display_name": "XC01",
                    "order": 2,
                    "tags": ["orbit"],
                    "limits": {},
                    "logical_channels": ["setpoint"],
                },
                {
                    "id": "YC01",
                    "kind": "corr",
                    "display_name": "YC01",
                    "order": 3,
                    "tags": ["orbit"],
                    "limits": {},
                    "logical_channels": ["setpoint"],
                },
            ],
        }
        backend_channels = {
            "vm": {
                "backend": "vm",
                "channels": {
                    "BPM01": {"x": "VM:BPM01:X", "y": "VM:BPM01:Y"},
                    "XC01": {"setpoint": "VM:XC01"},
                    "YC01": {"setpoint": "VM:YC01"},
                },
            },
            "real": {
                "backend": "real",
                "channels": {
                    "BPM01": {"x": "REAL:BPM01:X", "y": "REAL:BPM01:Y"},
                    "XC01": {"setpoint": "REAL:XC01"},
                    "YC01": {"setpoint": "REAL:YC01"},
                },
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            machine_dir = temp_root / "configs" / "machines" / "orbitonly"
            (machine_dir / "control_backends").mkdir(parents=True)
            (machine_dir / "machine.json").write_text(
                json.dumps(machine_json, indent=2),
                encoding="utf-8",
            )
            for backend_name, payload in backend_channels.items():
                (machine_dir / "control_backends" / f"{backend_name}.json").write_text(
                    json.dumps(payload, indent=2),
                    encoding="utf-8",
                )

            with patch("half_linac.src.shared.machine_profile.loader.repo_root", return_value=temp_root):
                profile = load_profile("orbitonly")
                orbit_context = load_app_context("orbit_correct", machine_id="orbitonly")
                orbit_display_context = load_app_context("orbit_display", machine_id="orbitonly")
                supported_orbit, orbit_reason = describe_app_support("orbitonly", "orbit_correct")
                supported_orbit_display, orbit_display_reason = describe_app_support("orbitonly", "orbit_display")
                supported_bba, bba_reason = describe_app_support("orbitonly", "bba")
                supported_beam, beam_reason = describe_app_support("orbitonly", "beam_monitor")
                supported_energy, energy_reason = describe_app_support("orbitonly", "energy_spectrum")
                with self.assertRaises(MachineProfileError):
                    load_app_context("bba", machine_id="orbitonly")

        self.assertEqual(profile.machine.id, "orbitonly")
        assert orbit_context.orbit_workflow is not None
        self.assertIsNone(orbit_display_context.orbit_workflow)
        self.assertTrue(supported_orbit)
        self.assertIsNone(orbit_reason)
        self.assertTrue(supported_orbit_display)
        self.assertIsNone(orbit_display_reason)
        self.assertFalse(supported_bba)
        self.assertIsNotNone(bba_reason)
        self.assertFalse(supported_beam)
        self.assertIsNotNone(beam_reason)
        self.assertFalse(supported_energy)
        self.assertIsNotNone(energy_reason)
        self.assertEqual(orbit_context.orbit_workflow.bpms, ("BPM01",))
        self.assertEqual(orbit_context.orbit_workflow.xcors, ("XC01",))
        self.assertEqual(orbit_context.orbit_workflow.ycors, ("YC01",))

    def test_duplicate_element_ids_raise(self):
        bad = {
            "schema_version": "1",
            "machine": {
                "id": "dup",
                "family": "linac",
                "display_name": "Dup",
                "default_mode": "vm",
            },
            "elements": [
                {
                    "id": "BPM01",
                    "kind": "bpm",
                    "display_name": "BPM01",
                    "order": 1,
                    "tags": [],
                    "limits": {},
                    "channels": {
                        "x": {"vm": "PV:X", "real": "PV:X"},
                        "y": {"vm": "PV:Y", "real": "PV:Y"},
                    },
                },
                {
                    "id": "BPM01",
                    "kind": "bpm",
                    "display_name": "BPM01 copy",
                    "order": 2,
                    "tags": [],
                    "limits": {},
                    "channels": {
                        "x": {"vm": "PV2:X", "real": "PV2:X"},
                        "y": {"vm": "PV2:Y", "real": "PV2:Y"},
                    },
                },
            ],
            "workflows": {
                "orbit": {
                    "bpms": ["BPM01"],
                    "xcors": ["XC01"],
                    "ycors": ["YC01"],
                },
                "bba": {
                    "presets": [],
                    "standard": {
                        "correctors": ["XC01"],
                        "quads": ["Q1"],
                        "bpm1": ["BPM01"],
                        "bpm2": ["BPM01"],
                        "default_preset": "missing",
                    },
                    "bba2": {
                        "quads": ["Q1"],
                        "correctors": ["XC01"],
                        "bpm1": ["BPM01"],
                        "bpm2": ["BPM01"],
                        "modes": ["Virtual Machine"],
                        "default_preset": "missing",
                    },
                },
                "emit_measure": {
                    "presets": [],
                    "default_preset": "missing",
                    "twiss_quads": ["Q1"],
                },
            },
        }
        with self.assertRaises(MachineProfileError):
            MachineProfile.from_dict(bad)

    def test_missing_channel_mapping_is_allowed_until_that_backend_is_resolved(self):
        bad = {
            "schema_version": "1",
            "machine": {
                "id": "bad",
                "family": "linac",
                "display_name": "Bad",
                "default_mode": "vm",
            },
            "elements": [
                {
                    "id": "BPM01",
                    "kind": "bpm",
                    "display_name": "BPM01",
                    "order": 1,
                    "tags": [],
                    "limits": {},
                    "channels": {
                        "x": {"vm": "BPM01:X", "real": "BPM01:X"},
                        "y": {"vm": "BPM01:Y"},
                    },
                },
                {
                    "id": "XC01",
                    "kind": "corr",
                    "display_name": "XC01",
                    "order": 2,
                    "tags": [],
                    "limits": {},
                    "channels": {"setpoint": {"vm": "XC01", "real": "XC01"}},
                },
                {
                    "id": "YC01",
                    "kind": "corr",
                    "display_name": "YC01",
                    "order": 3,
                    "tags": [],
                    "limits": {},
                    "channels": {"setpoint": {"vm": "YC01", "real": "YC01"}},
                },
                {
                    "id": "Q1",
                    "kind": "quad",
                    "display_name": "Q1",
                    "order": 4,
                    "tags": [],
                    "limits": {},
                    "channels": {"k1": {"vm": "Q1", "real": "Q1"}},
                },
                {
                    "id": "PRF01",
                    "kind": "flag",
                    "display_name": "PRF01",
                    "order": 5,
                    "tags": [],
                    "limits": {},
                    "channels": {
                        "sigx": {"vm": "PRF01:sigx", "real": "PRF01:sigx"},
                        "sigy": {"vm": "PRF01:sigy", "real": "PRF01:sigy"},
                    },
                },
            ],
            "workflows": {
                "orbit": {
                    "bpms": ["BPM01"],
                    "xcors": ["XC01"],
                    "ycors": ["YC01"],
                },
                "bba": {
                    "presets": [
                        {
                            "id": "bba_default",
                            "plane": "X",
                            "quad": "Q1",
                            "corr": "XC01",
                            "bpm1": "BPM01",
                            "bpm2": "BPM01",
                        }
                    ],
                    "standard": {
                        "correctors": ["XC01"],
                        "quads": ["Q1"],
                        "bpm1": ["BPM01"],
                        "bpm2": ["BPM01"],
                        "default_preset": "bba_default",
                    },
                    "bba2": {
                        "quads": ["Q1"],
                        "correctors": ["XC01"],
                        "bpm1": ["BPM01"],
                        "bpm2": ["BPM01"],
                        "modes": ["Virtual Machine"],
                        "default_preset": "bba_default",
                    },
                },
                "emit_measure": {
                    "presets": [
                        {
                            "id": "emit_default",
                            "quad": "Q1",
                            "flag": "PRF01",
                            "energy_mev": 1,
                        }
                    ],
                    "default_preset": "emit_default",
                    "twiss_quads": ["Q1"],
                },
            },
        }
        profile = MachineProfile.from_dict(bad)
        self.assertEqual(profile.control_backends, ("vm", "real"))
        self.assertEqual(resolve_channel(profile, "BPM01", "y", "vm"), "BPM01:Y")
        with self.assertRaises(MachineProfileError):
            resolve_channel(profile, "BPM01", "y", "real")

    def test_invalid_bba_plane_or_mode_raises(self):
        bad = {
            "schema_version": "1",
            "machine": {
                "id": "bad",
                "family": "linac",
                "display_name": "Bad",
                "default_mode": "vm",
            },
            "elements": [
                {
                    "id": "BPM01",
                    "kind": "bpm",
                    "display_name": "BPM01",
                    "order": 1,
                    "tags": [],
                    "limits": {},
                    "channels": {
                        "x": {"vm": "BPM01:X", "real": "BPM01:X"},
                        "y": {"vm": "BPM01:Y", "real": "BPM01:Y"},
                    },
                },
                {
                    "id": "XC01",
                    "kind": "corr",
                    "display_name": "XC01",
                    "order": 2,
                    "tags": [],
                    "limits": {},
                    "channels": {"setpoint": {"vm": "XC01", "real": "XC01"}},
                },
                {
                    "id": "YC01",
                    "kind": "corr",
                    "display_name": "YC01",
                    "order": 3,
                    "tags": [],
                    "limits": {},
                    "channels": {"setpoint": {"vm": "YC01", "real": "YC01"}},
                },
                {
                    "id": "Q1",
                    "kind": "quad",
                    "display_name": "Q1",
                    "order": 4,
                    "tags": [],
                    "limits": {},
                    "channels": {"k1": {"vm": "Q1", "real": "Q1"}},
                },
                {
                    "id": "PRF01",
                    "kind": "flag",
                    "display_name": "PRF01",
                    "order": 5,
                    "tags": [],
                    "limits": {},
                    "channels": {
                        "sigx": {"vm": "PRF01:sigx", "real": "PRF01:sigx"},
                        "sigy": {"vm": "PRF01:sigy", "real": "PRF01:sigy"},
                    },
                },
            ],
            "workflows": {
                "orbit": {
                    "bpms": ["BPM01"],
                    "xcors": ["XC01"],
                    "ycors": ["YC01"],
                },
                "bba": {
                    "presets": [
                        {
                            "id": "bba_default",
                            "plane": "horizontal",
                            "quad": "Q1",
                            "corr": "XC01",
                            "bpm1": "BPM01",
                            "bpm2": "BPM01",
                            "mode": "offline",
                        }
                    ],
                    "standard": {
                        "correctors": ["XC01"],
                        "quads": ["Q1"],
                        "bpm1": ["BPM01"],
                        "bpm2": ["BPM01"],
                        "default_preset": "bba_default",
                    },
                    "bba2": {
                        "quads": ["Q1"],
                        "correctors": ["XC01"],
                        "bpm1": ["BPM01"],
                        "bpm2": ["BPM01"],
                        "modes": ["offline"],
                        "default_preset": "bba_default",
                    },
                },
                "emit_measure": {
                    "presets": [
                        {
                            "id": "emit_default",
                            "quad": "Q1",
                            "flag": "PRF01",
                            "energy_mev": 1,
                        }
                    ],
                    "default_preset": "emit_default",
                    "twiss_quads": ["Q1"],
                },
            },
        }
        with self.assertRaises(MachineProfileError):
            MachineProfile.from_dict(bad)

    def test_orbit_length_mismatch_raises(self):
        bad = {
            "schema_version": "1",
            "machine": {
                "id": "bad",
                "family": "linac",
                "display_name": "Bad",
                "default_mode": "vm",
            },
            "elements": [
                {
                    "id": "BPM01",
                    "kind": "bpm",
                    "display_name": "BPM01",
                    "order": 1,
                    "tags": [],
                    "limits": {},
                    "channels": {
                        "x": {"vm": "BPM01:X", "real": "BPM01:X"},
                        "y": {"vm": "BPM01:Y", "real": "BPM01:Y"},
                    },
                },
                {
                    "id": "XC01",
                    "kind": "corr",
                    "display_name": "XC01",
                    "order": 2,
                    "tags": [],
                    "limits": {},
                    "channels": {"setpoint": {"vm": "XC01", "real": "XC01"}},
                },
                {
                    "id": "YC01",
                    "kind": "corr",
                    "display_name": "YC01",
                    "order": 3,
                    "tags": [],
                    "limits": {},
                    "channels": {"setpoint": {"vm": "YC01", "real": "YC01"}},
                },
                {
                    "id": "Q1",
                    "kind": "quad",
                    "display_name": "Q1",
                    "order": 4,
                    "tags": [],
                    "limits": {},
                    "channels": {"k1": {"vm": "Q1", "real": "Q1"}},
                },
                {
                    "id": "PRF01",
                    "kind": "flag",
                    "display_name": "PRF01",
                    "order": 5,
                    "tags": [],
                    "limits": {},
                    "channels": {
                        "sigx": {"vm": "PRF01:sigx", "real": "PRF01:sigx"},
                        "sigy": {"vm": "PRF01:sigy", "real": "PRF01:sigy"},
                    },
                },
            ],
            "workflows": {
                "orbit": {
                    "bpms": ["BPM01"],
                    "xcors": ["XC01", "XC01"],
                    "ycors": ["YC01"],
                },
                "bba": {
                    "presets": [
                        {
                            "id": "bba_default",
                            "plane": "X",
                            "quad": "Q1",
                            "corr": "XC01",
                            "bpm1": "BPM01",
                            "bpm2": "BPM01",
                        }
                    ],
                    "standard": {
                        "correctors": ["XC01"],
                        "quads": ["Q1"],
                        "bpm1": ["BPM01"],
                        "bpm2": ["BPM01"],
                        "default_preset": "bba_default",
                    },
                    "bba2": {
                        "quads": ["Q1"],
                        "correctors": ["XC01"],
                        "bpm1": ["BPM01"],
                        "bpm2": ["BPM01"],
                        "modes": ["Virtual Machine"],
                        "default_preset": "bba_default",
                    },
                },
                "emit_measure": {
                    "presets": [
                        {
                            "id": "emit_default",
                            "quad": "Q1",
                            "flag": "PRF01",
                            "energy_mev": 1,
                        }
                    ],
                    "default_preset": "emit_default",
                    "twiss_quads": ["Q1"],
                },
            },
        }
        with self.assertRaises(MachineProfileError):
            MachineProfile.from_dict(bad)

    def test_directory_measurement_apps_require_model_backends(self):
        machine_json = {
            "schema_version": "1",
            "machine": {
                "id": "dirmodelmissing",
                "family": "linac",
                "display_name": "Directory Missing Model",
                "default_mode": "vm",
            },
            "elements": [
                {
                    "id": "BPM01",
                    "kind": "bpm",
                    "display_name": "BPM01",
                    "order": 1,
                    "tags": ["orbit", "bba"],
                    "limits": {},
                    "logical_channels": ["x", "y"],
                },
                {
                    "id": "XC01",
                    "kind": "corr",
                    "display_name": "XC01",
                    "order": 2,
                    "tags": ["orbit", "bba"],
                    "limits": {},
                    "logical_channels": ["setpoint"],
                },
                {
                    "id": "YC01",
                    "kind": "corr",
                    "display_name": "YC01",
                    "order": 3,
                    "tags": ["orbit", "bba"],
                    "limits": {},
                    "logical_channels": ["setpoint"],
                },
                {
                    "id": "Q01",
                    "kind": "quad",
                    "display_name": "Q01",
                    "order": 4,
                    "tags": ["bba", "emit"],
                    "limits": {},
                    "logical_channels": ["k1"],
                },
                {
                    "id": "PRF01",
                    "kind": "flag",
                    "display_name": "PRF01",
                    "order": 5,
                    "tags": ["emit"],
                    "limits": {},
                    "logical_channels": ["sigx", "sigy"],
                },
            ],
        }
        backend_channels = {
            "vm": {
                "backend": "vm",
                "channels": {
                    "BPM01": {"x": "VM:BPM01:X", "y": "VM:BPM01:Y"},
                    "XC01": {"setpoint": "VM:XC01"},
                    "YC01": {"setpoint": "VM:YC01"},
                    "Q01": {"k1": "VM:Q01:K1"},
                    "PRF01": {"sigx": "VM:PRF01:X", "sigy": "VM:PRF01:Y"},
                },
            },
            "real": {
                "backend": "real",
                "channels": {
                    "BPM01": {"x": "REAL:BPM01:X", "y": "REAL:BPM01:Y"},
                    "XC01": {"setpoint": "REAL:XC01"},
                    "YC01": {"setpoint": "REAL:YC01"},
                    "Q01": {"k1": "REAL:Q01:K1"},
                    "PRF01": {"sigx": "REAL:PRF01:X", "sigy": "REAL:PRF01:Y"},
                },
            },
        }
        emit_json = {
            "presets": [
                {
                    "id": "emit_default",
                    "quad": "Q01",
                    "flag": "PRF01",
                    "energy_mev": 100.0,
                }
            ]
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            machine_dir = temp_root / "configs" / "machines" / "dirmodelmissing"
            (machine_dir / "control_backends").mkdir(parents=True)
            (machine_dir / "apps").mkdir(parents=True)
            (machine_dir / "machine.json").write_text(
                json.dumps(machine_json, indent=2),
                encoding="utf-8",
            )
            for backend_name, payload in backend_channels.items():
                (machine_dir / "control_backends" / f"{backend_name}.json").write_text(
                    json.dumps(payload, indent=2),
                    encoding="utf-8",
                )
            (machine_dir / "apps" / "emit_measure.json").write_text(
                json.dumps(emit_json, indent=2),
                encoding="utf-8",
            )

            with patch("half_linac.src.shared.machine_profile.loader.repo_root", return_value=temp_root):
                with self.assertRaisesRegex(MachineProfileError, "model_backends"):
                    load_app_context("emit_measure", machine_id="dirmodelmissing")

    def test_directory_profile_allows_single_real_backend_for_beam_and_energy(self):
        machine_json = {
            "schema_version": "1",
            "machine": {
                "id": "realonly",
                "family": "linac",
                "display_name": "Real Only",
                "default_mode": "real",
            },
            "runtime": {
                "vm": {
                    "root": "src/virtual_machine/half_elegant",
                    "ui_entrypoint": "src/virtual_machine/half_elegant/mainVM.py",
                    "manager_entrypoint": "src/virtual_machine/half_elegant/start_VM.py",
                    "runtime_json": "src/virtual_machine/half_elegant/halflinac.json",
                    "bootstrap_lattice": "src/virtual_machine/half_elegant/elegant/lattice_ini.lte",
                    "bootstrap_ele": "src/virtual_machine/half_elegant/elegant/one_ini.ele",
                    "line_name": "ALL",
                },
                "softioc": {
                    "root": "src/softIOC/halflinac",
                    "substitutions_file": "db/halflinac.substitutions",
                },
            },
            "elements": [
                {
                    "id": "PRF01",
                    "kind": "flag",
                    "display_name": "PRF01",
                    "order": 1,
                    "tags": ["emit", "energy_spectrum"],
                    "limits": {},
                    "logical_channels": ["image", "exposure_time", "sigx", "sigy"],
                },
                {
                    "id": "SM",
                    "kind": "bend",
                    "display_name": "SM",
                    "order": 2,
                    "tags": ["energy_spectrum"],
                    "limits": {},
                    "logical_channels": ["current_set"],
                },
                {
                    "id": "QE01",
                    "kind": "quad",
                    "display_name": "QE01",
                    "order": 3,
                    "tags": ["energy_spectrum", "esa_start"],
                    "limits": {},
                    "logical_channels": ["k1"],
                },
                {
                    "id": "QE02",
                    "kind": "quad",
                    "display_name": "QE02",
                    "order": 4,
                    "tags": ["energy_spectrum"],
                    "limits": {},
                    "logical_channels": ["k1"],
                },
                {
                    "id": "QE03",
                    "kind": "quad",
                    "display_name": "QE03",
                    "order": 5,
                    "tags": ["energy_spectrum"],
                    "limits": {},
                    "logical_channels": ["k1"],
                },
            ],
        }
        real_channels = {
            "backend": "real",
            "channels": {
                "PRF01": {
                    "image": "REAL:PRF01:IMAGE",
                    "exposure_time": "REAL:PRF01:EXPO",
                    "sigx": "REAL:PRF01:SIGX",
                    "sigy": "REAL:PRF01:SIGY",
                },
                "SM": {"current_set": "REAL:SM:CURRENT"},
                "QE01": {"k1": "REAL:QE01:K1"},
                "QE02": {"k1": "REAL:QE02:K1"},
                "QE03": {"k1": "REAL:QE03:K1"},
            },
        }
        beam_monitor_json = {
            "default_flag": "PRF01",
            "flag_pixel_shape": {"real": [1440, 1080]},
            "flag_pixel_width_mm": {"real": 0.02},
        }
        energy_spectrum_json = {
            "flag_element": "PRF01",
            "flag_image_channel": "image",
            "vm_watch_element": "PRF01",
            "flag_exposure_channel": "exposure_time",
            "flag_pixel_shape": {"real": [1440, 1080]},
            "flag_pixel_width_mm": {"real": 0.02},
            "bend_element": "SM",
            "model_backend": "simulation",
            "bend_scan": {"min": 0, "max": 10, "coarse_steps": 2, "fine_steps": 2},
            "esa_quads": ["QE01", "QE02", "QE03"],
            "default_start_element": "QE01",
        }
        model_backend_json = {
            "backend": "simulation",
            "engine": "elegant",
            "config": {
                "working_dir": "src/virtual_machine/half_elegant/elegant",
                "source_lattice": "src/virtual_machine/half_elegant/elegant/lattice_ini.lte",
                "energy_ini_ele_file": "src/virtual_machine/half_elegant/elegant/esa_ini.ele",
                "energy_json_path": "src/virtual_machine/half_elegant/esa.json",
                "energy_lte_file": "src/virtual_machine/half_elegant/elegant/esa.lte",
                "energy_ele_file": "src/virtual_machine/half_elegant/elegant/esa.ele",
                "energy_mat_file": "src/virtual_machine/half_elegant/elegant/esa.mat",
                "energy_twi_file": "src/virtual_machine/half_elegant/elegant/esa.twi",
                "energy_log": "esa.log",
                "energy_dispersion_line_name": "ESA",
                "energy_twiss_line_name": "ESA",
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            machine_dir = temp_root / "configs" / "machines" / "realonly"
            (machine_dir / "control_backends").mkdir(parents=True)
            (machine_dir / "apps").mkdir(parents=True)
            (machine_dir / "model_backends").mkdir(parents=True)
            (machine_dir / "machine.json").write_text(
                json.dumps(machine_json, indent=2),
                encoding="utf-8",
            )
            (machine_dir / "control_backends" / "real.json").write_text(
                json.dumps(real_channels, indent=2),
                encoding="utf-8",
            )
            (machine_dir / "apps" / "beam_monitor.json").write_text(
                json.dumps(beam_monitor_json, indent=2),
                encoding="utf-8",
            )
            (machine_dir / "apps" / "energy_spectrum.json").write_text(
                json.dumps(energy_spectrum_json, indent=2),
                encoding="utf-8",
            )
            (machine_dir / "model_backends" / "simulation.elegant.json").write_text(
                json.dumps(model_backend_json, indent=2),
                encoding="utf-8",
            )

            with patch("half_linac.src.shared.machine_profile.loader.repo_root", return_value=temp_root):
                profile = load_profile("realonly")
                beam_context = load_app_context("beam_monitor", machine_id="realonly")
                energy_context = load_app_context("energy_spectrum", machine_id="realonly")
                backend_choices = default_control_backend_choices("realonly")

        self.assertEqual(profile.control_backends, ("real",))
        self.assertEqual(backend_choices, ("real",))
        self.assertEqual(beam_context.control_backend.name, "real")
        self.assertEqual(energy_context.control_backend.name, "real")
        self.assertIsNotNone(energy_context.model_backend)


if __name__ == "__main__":
    unittest.main()
