from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
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
    REAL_STATUS_COMMISSIONED,
    REAL_STATUS_READ_ONLY,
    REAL_STATUS_WRITE_BLOCKED,
    REAL_STATUS_WRITE_SMOKE_PASSED,
    apply_snapshot_conversion,
    build_model_backend,
    build_model_snapshot,
    describe_app_model_support,
    describe_app_support,
    get_bba_preset,
    get_emit_preset,
    get_workflow,
    list_elements,
    load_app_context,
    load_model_snapshot,
    load_profile,
    load_solenoid_centering_workflow,
    real_commissioning_status,
    require_workflow_write_allowed,
    resolve_bend_write_channel,
    resolve_channel,
    resolve_default_energy_spectrum_station,
    resolve_element_image_geometry,
    resolve_flag_pixel_geometry,
    resolve_machine_runtime,
    resolve_write_target,
    resolve_virtual_machine_segment_choices,
    resolve_virtual_machine_usedline_workflow,
    save_model_snapshot,
    validate_machine_profile,
    workflow_writes_allowed,
)
from half_linac.src.apps.energy_spectrum.profile_runtime import (
    resolve_energy_spectrum_runtime_paths,
)
from half_linac.src.apps.bba.profile_runtime import resolve_scan_values
from half_linac.src.shared.elegant_backend import ElegantParser
from half_linac.src.shared.machine_profile.loader import (
    _parse_solenoid_centering_preset,
    _validate_beam_monitor_workflow,
    _validate_energy_spectrum_workflow,
    load_bba_workflow,
    load_emit_measure_workflow,
)
from half_linac.src.shared.machine_profile.models import _parse_element
from half_linac.src.shared.machine_profile.runtime_selector import (
    default_control_backend_choices,
    list_machine_choices,
)
from half_linac.src.shared.machine_profile.validation import (
    _validate_real_commissioning_status,
)
from half_linac.src.virtual_machine.lattice_usedline import expand_lattice_line


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
    def test_active_profiles_use_channel_scoped_limits(self):
        for machine_id in ("half", "irfel"):
            profile = load_profile(machine_id)
            legacy = [
                element.id
                for element in profile.elements
                if "low" in element.limits or "high" in element.limits
            ]
            self.assertEqual(legacy, [], machine_id)

    def test_resolve_write_target_uses_strict_backend_defaults(self):
        profile = load_profile("half")

        vm_corrector = resolve_write_target(profile, "XC21", mode="vm")
        real_corrector = resolve_write_target(profile, "XC21", mode="real")
        vm_bend = resolve_write_target(profile, "BENY", mode="vm")
        real_bend = resolve_write_target(profile, "BENY", mode="real")
        real_solenoid = resolve_write_target(profile, "SL01-1", mode="real")
        irfel_profile = load_profile("irfel")
        vm_solenoid = resolve_write_target(irfel_profile, "MS01", mode="vm")

        self.assertEqual((vm_corrector.logical_channel, vm_corrector.unit), ("kick", "rad"))
        self.assertEqual(
            (real_corrector.logical_channel, real_corrector.unit),
            ("current_set", "A"),
        )
        self.assertEqual((vm_bend.logical_channel, vm_bend.unit), ("angle", "rad"))
        self.assertEqual((real_bend.logical_channel, real_bend.unit), ("current_set", "A"))
        self.assertEqual((vm_solenoid.logical_channel, vm_solenoid.unit), ("current_set", "A"))
        self.assertEqual((real_solenoid.logical_channel, real_solenoid.unit), ("current_set", "A"))

    def test_resolve_write_target_requires_quadrupole_quantity(self):
        profile = load_profile("half")
        with self.assertRaisesRegex(MachineProfileError, "requires quantity"):
            resolve_write_target(profile, "QT02", mode="real")

        k1_target = resolve_write_target(
            profile,
            "QT02",
            quantity="K1",
            mode="real",
        )
        current_target = resolve_write_target(
            profile,
            "QT02",
            quantity="current",
            mode="real",
        )
        self.assertEqual((k1_target.logical_channel, k1_target.unit), ("K1", "1/m^2"))
        self.assertEqual(
            (current_target.logical_channel, current_target.unit),
            ("current_set", "A"),
        )

    def test_resolve_write_target_does_not_fallback_across_physical_quantities(self):
        profile = load_profile("half")
        element = profile.get_element("XC21")
        limited_channels = {
            name: modes for name, modes in element.channels.items() if name != "kick"
        }
        modified = replace(element, channels=limited_channels)
        elements = tuple(modified if item.id == modified.id else item for item in profile.elements)
        profile = replace(
            profile,
            elements=elements,
            _elements_by_id={item.id: item for item in elements},
        )

        with self.assertRaisesRegex(MachineProfileError, "kick"):
            resolve_write_target(profile, "XC21", mode="vm")

    def test_resolve_write_target_validates_explicit_selection_and_units(self):
        profile = load_profile("half")
        with self.assertRaisesRegex(MachineProfileError, "not both"):
            resolve_write_target(
                profile,
                "QT02",
                quantity="K1",
                logical_channel="K1",
                mode="real",
            )
        with self.assertRaisesRegex(MachineProfileError, "Unit mismatch"):
            resolve_write_target(
                profile,
                "QT02",
                quantity="K1",
                unit="A",
                mode="real",
            )
        self.assertEqual(
            resolve_write_target(
                profile,
                "QT02",
                logical_channel="k1",
                mode="real",
            ).logical_channel,
            "K1",
        )

        element = profile.get_element("XC21")
        legacy_channels = dict(element.channels)
        legacy_channels["setpoint"] = legacy_channels.pop("current_set")
        legacy_element = replace(element, channels=legacy_channels)
        elements = tuple(
            legacy_element if item.id == legacy_element.id else item
            for item in profile.elements
        )
        legacy_profile = replace(
            profile,
            elements=elements,
            _elements_by_id={item.id: item for item in elements},
        )
        legacy_target = resolve_write_target(
            legacy_profile,
            "XC21",
            quantity="current",
            mode="real",
        )
        self.assertEqual(legacy_target.logical_channel, "setpoint")

        quad = profile.get_element("QT02")
        invalid_quad = replace(
            quad,
            limits={**quad.limits, "K1": {"low": -1, "high": 1, "unit": "A"}},
        )
        elements = tuple(
            invalid_quad if item.id == invalid_quad.id else item
            for item in profile.elements
        )
        invalid_profile = replace(
            profile,
            elements=elements,
            _elements_by_id={item.id: item for item in elements},
        )
        with self.assertRaisesRegex(MachineProfileError, "Unit mismatch"):
            resolve_write_target(
                invalid_profile,
                "QT02",
                quantity="K1",
                mode="real",
            )

    def test_ambiguous_flat_magnet_limits_are_rejected(self):
        for kind, channel in (("quad", "K1"), ("bend", "angle")):
            raw = {
                "id": f"{kind.upper()}TEST",
                "kind": kind,
                "display_name": f"{kind.upper()}TEST",
                "order": 1,
                "tags": [],
                "limits": {"low": -1, "high": 1},
                "channels": {channel: {"vm": f"TEST:{channel}"}},
            }
            with self.subTest(kind=kind):
                with self.assertRaisesRegex(
                    MachineProfileError,
                    "nested by logical channel",
                ):
                    _parse_element(raw, 0)

    def test_real_write_blocking_status_rejects_allowed_write_policy(self):
        profile = deepcopy(load_profile("irfel"))
        workflow = get_workflow(profile, "dispersion_correction")
        workflow["real_status"] = "write_blocked"
        workflow["write_control"]["real"] = "allowed"

        check = _validate_real_commissioning_status(profile, "dispersion_correction")

        self.assertEqual(check.status, "fail")
        self.assertIn("real_status='write_blocked'", check.detail)
        self.assertIn("write_control.real resolves to 'allowed'", check.detail)

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
            REPO_ROOT / "src/apps/energy_spectrum/main.py",
            REPO_ROOT / "src/apps/energy_spectrum/get_energy0.py",
            REPO_ROOT / "src/apps/energy_spectrum/profile_runtime.py",
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

    def test_half_vm_pvs_are_covered_by_softioc_contract(self):
        report = validate_machine_profile("half")
        check = report.get_check("vm_softioc_contract")
        self.assertIsNotNone(check)
        assert check is not None
        self.assertTrue(check.ok, check.detail)
        self.assertIn("all", check.detail)

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

    def test_irfel_beam_monitor_flags_follow_selected_control_backend(self):
        vm_context = load_app_context(
            "beam_monitor",
            machine_id="irfel",
            control_backend="vm",
        )
        real_context = load_app_context(
            "beam_monitor",
            machine_id="irfel",
            control_backend="real",
        )

        vm_flags = list_elements(
            vm_context,
            kind="flag",
            logical_channel="image",
            control_backend=vm_context.control_backend.name,
        )
        real_flags = list_elements(
            real_context,
            kind="flag",
            logical_channel="image",
            control_backend=real_context.control_backend.name,
        )

        self.assertEqual(
            [element.id for element in vm_flags],
            ["PRF01", "PRF02", "PRF03", "PRF04", "PRFESA"],
        )
        self.assertEqual(
            [element.id for element in real_flags],
            ["PRF03", "PRF04", "PRFESA"],
        )

    def test_load_solenoid_centering_app_context(self):
        with self.assertRaisesRegex(MachineProfileError, "supports only real"):
            load_app_context("solenoid_centering")

        context = load_app_context("solenoid_centering", control_backend="real")
        self.assertIsInstance(context, AppContext)
        self.assertEqual(context.machine.id, "half")
        self.assertEqual(context.control_backend.name, "real")
        self.assertIsNone(context.model_backend)
        self.assertIsNotNone(context.solenoid_centering_workflow)
        assert context.solenoid_centering_workflow is not None
        workflow = load_solenoid_centering_workflow(context.profile)
        self.assertEqual(workflow.default_preset, "sl01_1_centering")
        preset = workflow.presets_by_id["sl01_1_centering"]
        self.assertEqual(preset.solenoid, "SL01-1")
        self.assertEqual(preset.hcorr, "SL01-DX")
        self.assertEqual(preset.samples_per_point, 3)
        self.assertEqual(preset.settle_time_s, 2.0)
        self.assertEqual(preset.sample_interval_s, 0.2)
        self.assertEqual(preset.max_rounds, 2)
        self.assertEqual(preset.solenoid_scan.relative_from, -0.05)
        self.assertEqual(preset.corrector_scan.relative_to, 0.0002)
        self.assertIsNotNone(preset.motion_verification)
        self.assertEqual(preset.minimum_relative_score_improvement, 0.05)
        self.assertEqual(
            workflow.presets_by_id["sl01_2_centering"].solenoid,
            "SL01-2",
        )
        self.assertEqual(
            workflow.presets_by_id["sm01_centering"].display_name,
            "SM01 Centering",
        )
        self.assertTrue(workflow_writes_allowed(context, "solenoid_centering"))
        require_workflow_write_allowed(context, "solenoid_centering", "test write")

    def test_half_real_write_permissions_are_enabled_for_operational_apps(self):
        writable_apps = (
            ("orbit_correct", "orbit"),
            ("beam_monitor", "beam_monitor"),
            ("bba", "bba"),
            ("emit_measure", "emit_measure"),
            ("energy_spectrum", "energy_spectrum"),
            ("dispersion_correction", "dispersion_correction"),
            ("solenoid_centering", "solenoid_centering"),
        )
        for app_name, workflow_name in writable_apps:
            with self.subTest(app=app_name):
                context = load_app_context(
                    app_name,
                    machine_id="half",
                    control_backend="real",
                )
                self.assertTrue(workflow_writes_allowed(context, workflow_name))
                require_workflow_write_allowed(context, workflow_name, "test write")

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
        self.assertEqual(workflow.presets_by_id["ms01_centering"].solenoid, "MS01")
        self.assertEqual(workflow.presets_by_id["ms01_centering"].hcorr, "MSHC")
        self.assertEqual(workflow.presets_by_id["ms01_centering"].bpm, "BPM01")
        self.assertEqual(
            workflow.presets_by_id["ms01_centering"].solenoid_scan.relative_from,
            -5.0,
        )
        self.assertEqual(workflow.presets_by_id["ms01_centering"].solenoid_scan.steps, 5)
        self.assertEqual(workflow.presets_by_id["ms01_centering"].corrector_scan.steps, 5)
        self.assertEqual(workflow.presets_by_id["ms01_centering"].samples_per_point, 2)
        self.assertEqual(workflow.presets_by_id["ms01_centering"].max_rounds, 1)
        self.assertIsNotNone(workflow.presets_by_id["ms01_centering"].motion_verification)
        self.assertEqual(workflow.presets_by_id["ss02_centering"].solenoid, "SS02")
        self.assertEqual(workflow.presets_by_id["ss02_centering"].hcorr, "HIC01")
        self.assertEqual(workflow.presets_by_id["ss02_centering"].vcorr, "VIC01")
        self.assertEqual(workflow.presets_by_id["ls01_centering"].vcorr, "VC01")
        self.assertTrue(workflow_writes_allowed(context, "solenoid_centering"))
        self.assertFalse(workflow_writes_allowed(context, "solenoid_centering", mode="vm"))
        self.assertEqual(real_commissioning_status(context), "commissioned")

    def test_solenoid_centering_rejects_invalid_readback_verification(self):
        preset = {
            "id": "test",
            "display_name": "Test",
            "solenoid": "MS01",
            "hcorr": "MSHC",
            "vcorr": "MSVC",
            "bpm": "BPM01",
            "solenoid_scan": {"relative_from": -1, "relative_to": 1, "steps": 3},
            "corrector_scan": {"relative_from": -1, "relative_to": 1, "steps": 3},
            "samples_per_point": 1,
            "settle_time_s": 0,
            "sample_interval_s": 0,
            "max_rounds": 1,
            "readback_verification": {
                "solenoid_readback_tolerance": 0,
                "corrector_readback_tolerance": 0.01,
                "readback_timeout_s": 1,
            },
        }

        with self.assertRaisesRegex(MachineProfileError, "tolerances must be positive"):
            _parse_solenoid_centering_preset(preset, "preset")

    def test_solenoid_centering_structured_scan_validates_unit_and_mode(self):
        preset = {
            "id": "test",
            "display_name": "Test",
            "solenoid": "MS01",
            "hcorr": "MSHC",
            "vcorr": "MSVC",
            "bpm": "BPM01",
            "scan": {
                "solenoid": {"low": -1, "high": 1, "steps": 3, "unit": "A", "mode": "absolute"},
                "corrector": {"low": -1, "high": 1, "steps": 3, "unit": "A", "mode": "relative"},
            },
            "samples_per_point": 1,
            "settle_time_s": 0,
            "sample_interval_s": 0,
            "max_iters": 1,
        }

        with self.assertRaisesRegex(MachineProfileError, "mode must be 'relative'"):
            _parse_solenoid_centering_preset(preset, "preset")

        preset["scan"]["solenoid"]["mode"] = "relative"
        preset["scan"]["corrector"]["unit"] = "mrad"
        with self.assertRaisesRegex(MachineProfileError, "unit must be 'A'"):
            _parse_solenoid_centering_preset(preset, "preset")

    def test_solenoid_centering_legacy_scan_format_remains_supported(self):
        preset = {
            "id": "legacy",
            "display_name": "Legacy",
            "solenoid": "MS01",
            "hcorr": "MSHC",
            "vcorr": "MSVC",
            "bpm": "BPM01",
            "solenoid_scan": {"relative_from": -1, "relative_to": 1, "steps": 3},
            "corrector_scan": {"relative_from": -2, "relative_to": 2, "steps": 5},
            "samples_per_point": 1,
            "settle_time_s": 0,
            "sample_interval_s": 0,
            "max_rounds": 1,
        }

        parsed = _parse_solenoid_centering_preset(preset, "preset")
        self.assertEqual(parsed.solenoid_scan.relative_from, -1.0)
        self.assertEqual(parsed.corrector_scan.relative_to, 2.0)
        self.assertEqual(parsed.max_rounds, 1)

    def test_describe_app_model_support_reports_model_app_readiness(self):
        for machine_id in ("half", "irfel"):
            for app_name in ("bba", "emit_measure", "energy_spectrum"):
                supported, reason = describe_app_model_support(machine_id, app_name)
                self.assertTrue(supported, f"{machine_id} {app_name}: {reason}")
                self.assertIsNone(reason)

        supported, reason = describe_app_model_support("half", "orbit_correct")
        self.assertTrue(supported)
        self.assertIsNone(reason)

    def test_model_snapshot_reads_live_pv_and_builds_lattice_overrides(self):
        context = load_app_context(
            "energy_spectrum",
            machine_id="half",
            control_backend="vm",
        )
        qe01_pv = resolve_channel(context, "QE01", "k1", "vm")

        snapshot = build_model_snapshot(
            context,
            (("QE01", "K1"),),
            pv_reader=lambda pv_name: 1.75 if pv_name == qe01_pv else None,
        )

        self.assertEqual(snapshot.source, "live_from_vm")
        self.assertEqual(snapshot.lattice_overrides, {"QE01": {"K1": 1.75}})
        metadata = snapshot.as_metadata()
        self.assertEqual(metadata["fields"][0]["source_pv"], qe01_pv)
        self.assertEqual(metadata["fields"][0]["conversion"], {"type": "direct"})

    def test_half_bl01_model_snapshot_supports_vm_and_real_k1_channels(self):
        fields = tuple((f"QL{index:02d}", "K1") for index in range(1, 13))
        for backend, expected_source in (
            ("vm", "live_from_vm"),
            ("real", "live_from_real"),
        ):
            context = load_app_context(
                "dispersion_correction",
                machine_id="half",
                control_backend=backend,
            )
            pv_values = {
                resolve_channel(context, element_id, "k1", backend): float(index)
                for index, (element_id, _field_name) in enumerate(fields, start=1)
            }
            snapshot = build_model_snapshot(
                context,
                fields,
                pv_reader=pv_values.__getitem__,
            )

            self.assertEqual(snapshot.source, expected_source)
            self.assertEqual(len(snapshot.fields), 12)
            self.assertEqual(snapshot.lattice_overrides["QL12"]["K1"], 12.0)

        for index in range(1, 13):
            element_id = f"QL{index:02d}"
            pv_prefix = f"IN:MG:L002:QUAD:{element_id}:K1"
            self.assertEqual(
                resolve_channel(context, element_id, "k1", "real"),
                pv_prefix,
            )
            self.assertEqual(
                resolve_channel(context, element_id, "K1_adj", "real"),
                f"{pv_prefix}:ADJ",
            )
            self.assertEqual(
                resolve_channel(context, element_id, "K1_total", "real"),
                f"{pv_prefix}:TOTAL",
            )

    def test_half_dispersion_hv_draft_is_real_only(self):
        profile = load_profile("half")

        self.assertEqual(
            resolve_channel(
                profile,
                "MODULATOR_HV1",
                "voltage_set",
                "real",
            ),
            "HALF:modulator1:HV_set:ao",
        )
        with self.assertRaises(MachineProfileError):
            resolve_channel(
                profile,
                "MODULATOR_HV1",
                "voltage_set",
                "vm",
            )

    def test_model_snapshot_conversion_helpers(self):
        self.assertEqual(apply_snapshot_conversion(2.0, {"type": "direct"}), 2.0)
        self.assertEqual(
            apply_snapshot_conversion(
                2.0,
                {"type": "scale_offset", "scale": 3.0, "offset": -1.0},
            ),
            5.0,
        )
        self.assertEqual(
            apply_snapshot_conversion(
                2.0,
                {"type": "polynomial", "coefficients": [1.0, 2.0, 3.0]},
            ),
            17.0,
        )

    def test_model_snapshot_requires_live_mapping_for_requested_field(self):
        context = load_app_context(
            "energy_spectrum",
            machine_id="irfel",
            control_backend="real",
        )

        with self.assertRaisesRegex(MachineProfileError, "QM01.K1"):
            build_model_snapshot(
                context,
                (("QM01", "K1"),),
                pv_reader=lambda _pv_name: 1.0,
            )

    def test_emit_measure_model_snapshot_fields_build_lattice_overrides(self):
        half_context = load_app_context(
            "emit_measure",
            machine_id="half",
            control_backend="vm",
        )
        qt02_pv = resolve_channel(half_context, "QT02", "k1", "vm")
        ql27_pv = resolve_channel(half_context, "QL27", "k1", "vm")
        half_values = {qt02_pv: 2.5, ql27_pv: 1.75}

        half_snapshot = build_model_snapshot(
            half_context,
            (("QT02", "K1"), ("QL27", "K1")),
            pv_reader=half_values.__getitem__,
        )

        self.assertEqual(
            half_snapshot.lattice_overrides,
            {"QT02": {"K1": 2.5}, "QL27": {"K1": 1.75}},
        )

        irfel_context = load_app_context(
            "emit_measure",
            machine_id="irfel",
            control_backend="vm",
        )
        qm11_pv = resolve_channel(irfel_context, "QM11", "k1", "vm")
        qm12_pv = resolve_channel(irfel_context, "QM12", "k1", "vm")
        irfel_values = {qm11_pv: -8.0, qm12_pv: 30.0}

        irfel_snapshot = build_model_snapshot(
            irfel_context,
            (("QM11", "K1"), ("QM12", "K1")),
            pv_reader=irfel_values.__getitem__,
        )

        self.assertEqual(
            irfel_snapshot.lattice_overrides,
            {"QM11": {"K1": -8.0}, "QM12": {"K1": 30.0}},
        )

    def test_bba2_model_snapshot_fields_build_lattice_overrides(self):
        half_context = load_app_context(
            "bba",
            machine_id="half",
            control_backend="vm",
        )
        qt04_pv = resolve_channel(half_context, "QT04", "k1", "vm")
        half_snapshot = build_model_snapshot(
            half_context,
            (("QT04", "K1"),),
            pv_reader={qt04_pv: -3.5}.__getitem__,
        )

        self.assertEqual(half_snapshot.lattice_overrides, {"QT04": {"K1": -3.5}})

        irfel_context = load_app_context(
            "bba",
            machine_id="irfel",
            control_backend="vm",
        )
        qm04_pv = resolve_channel(irfel_context, "QM04", "k1", "vm")
        irfel_snapshot = build_model_snapshot(
            irfel_context,
            (("QM04", "K1"),),
            pv_reader={qm04_pv: -70.0}.__getitem__,
        )

        self.assertEqual(irfel_snapshot.lattice_overrides, {"QM04": {"K1": -70.0}})

    def test_emit_measure_model_snapshot_path_fields_are_path_scoped(self):
        context = load_app_context(
            "emit_measure",
            machine_id="half",
            control_backend="vm",
        )
        backend = build_model_backend(context)

        qt02_path_quads = [
            element["NAME"]
            for element in backend.get_line_elements("QT02", "PRF07")
            if element.get("TYPE") == "QUAD" and "K1" in element
        ]
        self.assertEqual(qt02_path_quads, ["QT02"])

        ql27_path_quads = [
            element["NAME"]
            for element in backend.get_line_elements("QL27", "PRF07")
            if element.get("TYPE") == "QUAD" and "K1" in element
        ]
        self.assertEqual(ql27_path_quads, ["QL27", "QT01", "QT02"])

    def test_model_snapshot_can_use_design_lattice_without_pv_read(self):
        context = load_app_context(
            "energy_spectrum",
            machine_id="half",
            control_backend="vm",
        )
        snapshot = build_model_snapshot(
            context,
            (("QE01", "K1"),),
            source="design",
            pv_reader=lambda _pv_name: self.fail("design snapshot must not read PVs"),
        )

        self.assertEqual(snapshot.source, "design")
        self.assertIn("QE01", snapshot.lattice_overrides)
        self.assertIn("K1", snapshot.lattice_overrides["QE01"])
        self.assertIsNone(snapshot.fields[0].source_pv)

    def test_model_snapshot_can_round_trip_through_saved_file(self):
        context = load_app_context(
            "energy_spectrum",
            machine_id="half",
            control_backend="vm",
        )
        qe01_pv = resolve_channel(context, "QE01", "k1", "vm")
        qe02_pv = resolve_channel(context, "QE02", "k1", "vm")
        values = {qe01_pv: 1.75, qe02_pv: -2.5}
        snapshot = build_model_snapshot(
            context,
            (("QE01", "K1"), ("QE02", "K1")),
            pv_reader=values.__getitem__,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = Path(temp_dir) / "snapshot.json"
            saved_path = save_model_snapshot(
                snapshot,
                snapshot_path,
                extra_metadata={"app": "energy_spectrum"},
            )
            saved = load_model_snapshot(
                saved_path,
                requested_fields=(("QE02", "K1"),),
                app_context=context,
            )
            rebuilt = build_model_snapshot(
                context,
                (("QE02", "K1"),),
                source="saved",
                saved_snapshot_path=saved_path,
                pv_reader=lambda _pv_name: self.fail("saved snapshot must not read PVs"),
            )

            raw = json.loads(snapshot_path.read_text(encoding="utf-8"))

        self.assertEqual(saved.source, "saved")
        self.assertEqual(saved.origin_source, "live_from_vm")
        self.assertEqual(saved.lattice_overrides, {"QE02": {"K1": -2.5}})
        self.assertEqual(rebuilt.lattice_overrides, {"QE02": {"K1": -2.5}})
        self.assertEqual(raw["schema_version"], "1")
        self.assertEqual(raw["extra_metadata"]["app"], "energy_spectrum")

    def test_saved_model_snapshot_rejects_machine_mismatch(self):
        context = load_app_context(
            "energy_spectrum",
            machine_id="half",
            control_backend="vm",
        )
        irfel_context = load_app_context(
            "energy_spectrum",
            machine_id="irfel",
            control_backend="vm",
        )
        qe01_pv = resolve_channel(context, "QE01", "k1", "vm")
        snapshot = build_model_snapshot(
            context,
            (("QE01", "K1"),),
            pv_reader=lambda pv_name: 1.75 if pv_name == qe01_pv else None,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = save_model_snapshot(snapshot, Path(temp_dir) / "snapshot.json")
            with self.assertRaisesRegex(MachineProfileError, "machine mismatch"):
                load_model_snapshot(snapshot_path, app_context=irfel_context)

    def test_energy_spectrum_runtime_paths_include_latest_model_snapshot(self):
        context = load_app_context(
            "energy_spectrum",
            machine_id="half",
            control_backend="vm",
        )
        paths = resolve_energy_spectrum_runtime_paths(context)

        self.assertTrue(str(paths["runtime_dir"]).endswith("src/apps/energy_spectrum/runtime/half/vm"))
        self.assertEqual(paths["latest_dir"], paths["runtime_dir"] / "latest")
        self.assertEqual(paths["latest_metadata_path"], paths["latest_dir"] / "metadata.json")
        self.assertEqual(paths["runs_dir"], paths["runtime_dir"] / "runs")
        self.assertEqual(paths["result_archive_dir"], paths["runtime_dir"] / "runs")
        self.assertEqual(
            paths["background_image_path"], paths["latest_dir"] / "background.npy"
        )
        self.assertEqual(
            paths["background_metadata_path"],
            paths["latest_dir"] / "background.json",
        )
        self.assertEqual(paths["model_snapshot_path"], paths["latest_dir"] / "model_snapshot.json")

    def test_half_flags_keep_backend_image_geometry(self):
        profile = load_profile("half")
        workflow = get_workflow(profile, "beam_monitor")
        self.assertEqual(workflow["default_flag"], "PRF06")
        self.assertEqual(workflow["profile_method"], "Gaussian fit")
        self.assertEqual(workflow["background_sample_count"], 5)
        self.assertEqual(workflow["background_sample_interval_s"], 1.0)
        self.assertEqual(
            resolve_element_image_geometry(profile, "PRF06", "vm").shape,
            (360, 270),
        )
        self.assertEqual(
            resolve_element_image_geometry(profile, "ENY", "vm").shape,
            (720, 270),
        )

    def test_all_directory_flags_define_geometry_for_each_image_backend(self):
        for machine_id in ("_template", "half", "irfel"):
            profile = load_profile(machine_id)
            for element in profile.elements:
                for backend_name in element.channels.get("image", {}):
                    geometry = resolve_element_image_geometry(
                        profile, element.id, backend_name
                    )
                    self.assertGreater(geometry.shape[0], 0)
                    self.assertGreater(geometry.shape[1], 0)
                    self.assertGreater(geometry.pixel_width_mm, 0)

    def test_beam_monitor_rejects_unknown_profile_method(self):
        profile = load_profile("irfel")
        workflow = dict(get_workflow(profile, "beam_monitor"))
        workflow["profile_method"] = "unknown"

        with self.assertRaisesRegex(MachineProfileError, "profile_method"):
            _validate_beam_monitor_workflow(profile, workflow)

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
            ("ALL_MAIN", "ALL_ESA", "ALL_DUMP"),
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

    def test_half_dump_usedline_matches_configured_lattice(self):
        runtime = resolve_machine_runtime(load_profile("half"))
        state = ElegantParser(
            runtime.vm.bootstrap_lattice,
            runtime.vm.bootstrap_ele,
            runtime.vm.line_name,
        ).build_runtime_state()
        lattice = state["lattice"]
        usedline = expand_lattice_line(lattice, "ALL_DUMP")

        self.assertEqual(usedline[0], "C")
        self.assertEqual(
            usedline[-9:],
            [
                "QT06",
                "DUMP_QT06_QDU01",
                "QDU01",
                "XCA23",
                "DUMP_QDU01_QDU02",
                "QDU02",
                "YCA23",
                "DUMP_QDU02_PRFD",
                "PRFD",
            ],
        )
        expected_values = {
            "QDU01": {"L": 0.15, "K1": 1.0},
            "QDU02": {"L": 0.3, "K1": 1.0},
            "DUMP_QT06_QDU01": {"L": 10.45},
            "DUMP_QDU01_QDU02": {"L": 9.925},
            "DUMP_QDU02_PRFD": {"L": 8.49},
        }
        for element_id, fields in expected_values.items():
            for field_name, expected in fields.items():
                self.assertEqual(float(lattice[element_id][field_name]), expected)

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
        real_context = load_app_context("bba", machine_id="half", control_backend="real")
        self.assertIsInstance(context, AppContext)
        self.assertIsNotNone(context.bba_workflow)
        self.assertIsNotNone(context.model_backend)
        assert context.bba_workflow is not None
        self.assertEqual(context.bba_workflow.bba1.default_preset, "bba1_qt04_xc21")
        self.assertEqual(context.bba_workflow.bba2.default_preset, "bba2_qt04_xc22")
        self.assertEqual(context.bba_workflow.bba1.quads, ())
        self.assertEqual(context.bba_workflow.bba1.correctors, ())
        self.assertEqual(context.bba_workflow.bba2.quads, ())
        self.assertEqual(context.bba_workflow.bba2.correctors, ())
        self.assertEqual(context.bba_workflow.bba2.control_backends, ())
        assert context.model_backend is not None
        self.assertEqual(context.model_backend.engine, "elegant")
        self.assertTrue(workflow_writes_allowed(real_context, "bba"))
        require_workflow_write_allowed(real_context, "bba", "test write")

    def test_bba_runtime_paths_are_machine_backend_scoped(self):
        from half_linac.src.apps.bba.profile_runtime import (
            new_bba_scan_archive_dir,
            resolve_bba_runtime_paths,
        )

        context = load_app_context("bba", machine_id="irfel", control_backend="vm")
        paths = resolve_bba_runtime_paths(context)

        self.assertTrue(str(paths["runtime_dir"]).endswith("src/apps/bba/runtime/irfel/vm"))
        self.assertEqual(paths["latest_dir"], paths["runtime_dir"] / "latest")
        self.assertEqual(paths["archive_dir"], paths["runtime_dir"] / "runs")
        self.assertEqual(paths["runs_dir"], paths["runtime_dir"] / "runs")
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
        self.assertEqual(context.emit_measure_workflow.default_preset, "emit_qt02_prf07")
        self.assertEqual(context.emit_measure_workflow.twiss_quads, ())
        adaptive = context.emit_measure_workflow.presets_by_id[
            "emit_qt02_prf07"
        ].scan.adaptive
        self.assertIsNotNone(adaptive)
        assert adaptive is not None
        self.assertEqual((adaptive.k1_min, adaptive.k1_max), (0.5, 3.5))
        self.assertEqual(adaptive.initial_points, 4)
        self.assertEqual(adaptive.max_unique_points, 16)
        scan = context.emit_measure_workflow.presets_by_id["emit_qt02_prf07"].scan
        self.assertEqual(scan.unit, "1/m^2")
        self.assertEqual(scan.mode, "absolute")
        assert context.model_backend is not None
        self.assertEqual(context.model_backend.engine, "elegant")

    def test_emit_loader_keeps_legacy_flat_scan_compatibility(self):
        profile = load_profile("half")
        workflow = deepcopy(profile.workflows["emit_measure"])
        workflow["presets"][0]["scan"] = {
            "k1_from": -2,
            "k1_end": 2,
            "k1_steps": 7,
            "unit": "1/m^2",
            "mode": "relative",
            "samples": 2,
            "settle_time": 0.5,
            "sample_interval": 0.25,
        }
        loaded = load_emit_measure_workflow(
            replace(profile, workflows={**profile.workflows, "emit_measure": workflow})
        )

        scan = loaded.presets[0].scan
        self.assertEqual((scan.k1_from, scan.k1_end, scan.k1_steps), (-2, 2, 7))
        self.assertEqual((scan.samples, scan.settle_time, scan.sample_interval), (2, 0.5, 0.25))
        self.assertEqual((scan.unit, scan.mode), ("1/m^2", "relative"))

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
        with self.assertRaisesRegex(MachineProfileError, "QL27.*K1.*real"):
            resolve_channel(profile, "QL27", "k1", "real")
        self.assertEqual(
            resolve_channel(profile, "PRF07", "sigx", "Virtual Machine"),
            "HALF:IN:FLAG:PRF07:sigx",
        )
        self.assertEqual(
            resolve_channel(profile, "PRF07", "image", "vm"),
            "HALF:IN:FLAG:PRF07:image1:ArrayData:vm",
        )
        self.assertEqual(
            resolve_channel(profile, "ENY", "image", "vm"),
            "HALF:IN:FLAG:ENY:image1:ArrayData:vm",
        )
        self.assertEqual(
            resolve_channel(profile, "ENY", "image", "real"),
            "HALF:IN:FLAG:ENY:image1:ArrayData",
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
            resolve_channel(profile, "SM01-DX", "current_set", "real"),
            "IN:PS:LE07:SM01-DX:current:ao",
        )
        self.assertEqual(
            resolve_channel(profile, "SL01-DY", "current_set", "real"),
            "IN:PS:LE07:SL01-DY:current:ao",
        )
        self.assertEqual(
            resolve_channel(profile, "QT01", "k1", "real"),
            "IN:MG:L002:QUAD:QT01:K1",
        )
        self.assertEqual(
            resolve_channel(profile, "QL03", "k1", "real"),
            "IN:MG:L002:QUAD:QL03:K1",
        )

    def test_half_added_magnet_power_supplies_match_reference_sheet(self):
        profile = load_profile("half")
        expected_set_pvs = {
            "SS01": "IN:PS:LE07:SS01:current:ao",
            "SS02": "IN:PS:LE07:SS02:current:ao",
            "SL01-1": "IN:PS:LE07:SL01-1:current:ao",
            "SL01-2": "IN:PS:LE07:SL01-2:current:ao",
            "BL01-A-CP": "IN:PS:L001:BL01-A-CP:current:ao",
            "XCA23": "IN:PS:LE16:XCA23:current:ao",
            "YCA23": "IN:PS:LE16:YCA23:current:ao",
            "BENY-CP": "IN:PS:LE16:BENY-CP:current:ao",
        }
        for element_id, set_pv in expected_set_pvs.items():
            with self.subTest(element_id=element_id):
                element = profile.get_element(element_id)
                self.assertEqual(
                    resolve_channel(profile, element_id, "current_set", "real"),
                    set_pv,
                )
                self.assertEqual(
                    resolve_channel(profile, element_id, "current_readback", "real"),
                    set_pv[:-2] + "ai",
                )
                expected_high = 12 if element_id.startswith("SS") else 100
                if element.kind == "corr":
                    expected_high = 10
                self.assertEqual(
                    element.limits_for("current_set"),
                    {
                        "low": -expected_high if element.kind == "corr" else 0,
                        "high": expected_high,
                        "unit": "A",
                    },
                )

        self.assertEqual(
            resolve_channel(profile, "XCA23", "kick", "vm"),
            "HALF:IN:COR:XCA23:ao",
        )
        self.assertEqual(
            resolve_channel(profile, "YCA23", "kick", "vm"),
            "HALF:IN:COR:YCA23:ao",
        )

        with self.assertRaisesRegex(MachineProfileError, "Unknown element id: SL01"):
            profile.get_element("SL01")

    def test_vm_backend_uses_softioc_alias_naming_for_magnets(self):
        profile = load_profile("half")
        self.assertEqual(
            resolve_channel(profile, "XC00", "kick", "vm"),
            "HALF:IN:COR:XC00:ao",
        )
        self.assertEqual(
            resolve_channel(profile, "XC21", "kick", "vm"),
            "HALF:IN:COR:XC21:ao",
        )
        self.assertEqual(
            resolve_channel(profile, "SM01-DX", "kick", "vm"),
            "HALF:IN:COR:SM01-DX:ao",
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
        self.assertEqual(workflow["corrector_upperlimit_by_backend"]["vm"]["value"], 0.001)
        runtime = load_orbit_runtime_settings(load_app_context("orbit_correct", machine_id="half", control_backend="vm"))
        self.assertEqual(runtime["corrector_upperlimit"], 0.001)
        self.assertEqual(runtime["corrector_upperlimit_unit"], "rad")
        self.assertEqual(runtime["svd_relative_cutoff"], 0.001)
        real_runtime = load_orbit_runtime_settings(
            load_app_context("orbit_correct", machine_id="half", control_backend="real")
        )
        self.assertEqual(real_runtime["svd_relative_cutoff"], 0.01)

    def test_bba_and_emit_defaults_exist(self):
        bba_context = load_app_context("bba")
        emit_context = load_app_context("emit_measure")

        bba_preset_ids = {preset.id for preset in bba_context.bba_workflow.presets}
        emit_preset_ids = {preset.id for preset in emit_context.emit_measure_workflow.presets}

        self.assertIn(bba_context.bba_workflow.bba1.default_preset, bba_preset_ids)
        self.assertIn(bba_context.bba_workflow.bba2.default_preset, bba_preset_ids)
        self.assertIn(emit_context.emit_measure_workflow.default_preset, emit_preset_ids)

    def test_context_preset_helpers_return_default_presets(self):
        bba_context = load_app_context("bba")
        emit_context = load_app_context("emit_measure")
        bba_preset = get_bba_preset(bba_context)
        emit_preset = get_emit_preset(emit_context)

        self.assertEqual(bba_preset.id, bba_context.bba_workflow.bba1.default_preset)
        self.assertEqual(bba_preset.plane, "x")
        self.assertEqual(emit_preset.id, emit_context.emit_measure_workflow.default_preset)
        self.assertGreater(emit_preset.energy_mev, 0)

    def test_context_preset_helpers_support_explicit_bba_preset(self):
        bba_context = load_app_context("bba")
        preset = get_bba_preset(bba_context, "bba2_qt04_xc22")
        self.assertEqual(preset.family, "bba2")
        self.assertIsNone(preset.mode)
        self.assertEqual(preset.energy_mev, 2200)
        self.assertEqual(preset.corr, "XC22")
        self.assertEqual(preset.scan["quad_from"], -5)
        self.assertEqual(preset.analysis["energy_mev"], 2200)
        self.assertEqual(preset.analysis["bpm1_samples"], 1)
        self.assertEqual(preset.analysis.leff_by, 0.058287)
        self.assertEqual(preset.analysis.quad_leff, 0.15)

    def test_bba_structured_scan_selects_backend_specific_range_and_unit(self):
        vm_preset = get_bba_preset(
            load_app_context("bba", machine_id="irfel", control_backend="vm"),
            "irfel_bba1_qm04_hc01",
        )
        real_preset = get_bba_preset(
            load_app_context("bba", machine_id="irfel", control_backend="real"),
            "irfel_bba1_qm04_hc01",
        )

        self.assertEqual(vm_preset.scan.corr_unit, "rad")
        self.assertEqual(real_preset.scan.corr_unit, "A")
        self.assertEqual(vm_preset.scan.quad_unit, "1/m^2")
        self.assertEqual(real_preset.scan.quad_unit, "1/m^2")
        self.assertEqual(vm_preset.scan.corr_mode, "absolute")
        self.assertEqual(real_preset.scan.corr_mode, "relative")
        self.assertEqual(vm_preset.scan.quad_mode, "relative")
        self.assertEqual(real_preset.scan.quad_mode, "relative")
        self.assertEqual(vm_preset.scan.corr_from, -0.001)
        self.assertEqual(vm_preset.scan.corr_end, 0.001)
        self.assertEqual(real_preset.scan.corr_from, -1)
        self.assertEqual(real_preset.scan.corr_end, 1)
        self.assertEqual(vm_preset.scan.quad_from, real_preset.scan.quad_from)
        self.assertEqual(vm_preset.scan.quad_from, -10)
        self.assertEqual(vm_preset.scan.quad_end, 10)
        self.assertEqual(vm_preset.scan.sample_interval, 0.2)

    def test_bba_scan_values_support_absolute_and_relative_modes(self):
        np.testing.assert_allclose(resolve_scan_values(-1, 1, 3, "absolute", 10), [-1, 0, 1])
        np.testing.assert_allclose(resolve_scan_values(-1, 1, 3, "relative", 10), [9, 10, 11])

    def test_bba_scan_values_reject_unknown_mode(self):
        with self.assertRaisesRegex(ValueError, "Unsupported scan mode"):
            resolve_scan_values(-1, 1, 3, "offset", 10)

    def test_bba_loader_accepts_relative_scan_mode(self):
        profile = load_profile("half")
        workflow = deepcopy(profile.workflows["bba"])
        workflow["presets"][0]["scan"]["corrector"]["vm"]["mode"] = "relative"
        loaded = load_bba_workflow(
            replace(profile, workflows={**profile.workflows, "bba": workflow}),
            "vm",
        )

        self.assertEqual(loaded.presets[0].scan.corr_mode, "relative")

    def test_bba_loader_keeps_legacy_flat_scan_compatibility(self):
        profile = load_profile("half")
        workflow = deepcopy(profile.workflows["bba"])
        preset = workflow["presets"][0]
        preset["scan"] = {
            "corr_from": -0.1,
            "corr_end": 0.1,
            "corr_steps": 3,
            "quad_from": -1,
            "quad_end": 1,
            "quad_steps": 5,
            "samples": 2,
            "settle_time": 0.5,
            "sample_interval": 0.25,
        }
        loaded = load_bba_workflow(
            replace(profile, workflows={**profile.workflows, "bba": workflow}),
            "vm",
        )

        scan = loaded.presets[0].scan
        self.assertEqual(scan.corr_from, -0.1)
        self.assertEqual(scan.quad_steps, 5)
        self.assertEqual(scan.samples, 2)
        self.assertEqual(scan.sample_interval, 0.25)
        self.assertIsNone(scan.corr_unit)

    def test_context_preset_helpers_support_default_emit_preset(self):
        emit_context = load_app_context("emit_measure")
        preset = get_emit_preset(emit_context)
        self.assertEqual(preset.quad, "QT02")
        self.assertEqual(preset.flag, "PRF07")
        self.assertEqual(preset.energy_mev, 2200)
        self.assertEqual(preset.scan.k1_steps, 15)
        self.assertEqual(preset.scan["samples"], 1)
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
        self.assertEqual(
            {element.id for element in flag_elements},
            {
                "PRF01",
                "PRF02",
                "PRF03",
                "PRF04",
                "PRF05",
                "PRF06",
                "PRF07",
                "PRF08",
                "PRF09",
                "PRF10",
                "PRF11",
                "PRF12",
                "PRF13",
                "PRF14",
                "PRFD",
                "ENY",
            },
        )

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
        expected_image_flags = {
            "PRF01",
            "PRF02",
            "PRF03",
            "PRF04",
            "PRF05",
            "PRF06",
            "PRF07",
            "PRF08",
            "PRF09",
            "PRF10",
            "PRF11",
            "PRF12",
            "PRF13",
            "PRF14",
            "PRFD",
            "ENY",
        }
        self.assertEqual(flag_ids, expected_image_flags)
        self.assertEqual(image_flags, expected_image_flags)
        self.assertEqual(esa_flags, set())

        real_image_flags = {
            element.id
            for element in list_elements(
                profile,
                kind="flag",
                logical_channel="image",
                control_backend="real",
            )
        }
        vm_image_flags = {
            element.id
            for element in list_elements(
                profile,
                kind="flag",
                logical_channel="image",
                control_backend="vm",
            )
        }
        self.assertEqual(real_image_flags, expected_image_flags)
        self.assertEqual(vm_image_flags, expected_image_flags - {"PRF01"})
        for index in range(1, 15):
            flag_id = f"PRF{index:02d}"
            self.assertEqual(
                resolve_channel(profile, flag_id, "image", "real"),
                f"HALF:IN:FLAG:{flag_id}:image1:ArrayData",
            )
        self.assertIn("XC00", x_corrs)
        self.assertIn("SM01-DX", x_corrs)
        self.assertNotIn("YC00", x_corrs)
        self.assertIn("YC00", y_corrs)
        self.assertIn("SL01-DY", y_corrs)
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
        asset_dir = Path(context.model_backend.config["asset_dir"])
        self.assertTrue(source_json.is_absolute())
        self.assertTrue(source_lattice.is_absolute())
        self.assertTrue(working_dir.is_absolute())
        self.assertTrue(asset_dir.is_absolute())
        self.assertTrue(str(source_json).endswith("src/virtual_machine/half_elegant/halflinac.json"))
        self.assertTrue(str(source_lattice).endswith("src/virtual_machine/half_elegant/elegant/lattice_ini.lte"))
        self.assertTrue(str(asset_dir).endswith("src/virtual_machine/half_elegant/elegant"))
        self.assertTrue(str(working_dir).endswith("runtime/model_backend/half/simulation/emit"))
        lattice_text = source_lattice.read_text(encoding="utf-8")
        self.assertIn("BPME02: MARK", lattice_text)
        self.assertEqual(lattice_text.count("BPME02"), 1)

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
            REAL_STATUS_COMMISSIONED,
        )
        self.assertEqual(real_commissioning_status(profile, "orbit_display"), REAL_STATUS_READ_ONLY)
        self.assertEqual(real_commissioning_status(profile, "beam_monitor"), REAL_STATUS_COMMISSIONED)
        self.assertEqual(real_commissioning_status(profile, "bba"), REAL_STATUS_WRITE_SMOKE_PASSED)
        self.assertEqual(
            real_commissioning_status(profile, "emit_measure"),
            REAL_STATUS_WRITE_SMOKE_PASSED,
        )
        self.assertEqual(real_commissioning_status(profile, "energy_spectrum"), REAL_STATUS_COMMISSIONED)
        self.assertTrue(workflow_writes_allowed(orbit_context, "orbit"))
        self.assertTrue(workflow_writes_allowed(vm_orbit_context, "orbit"))
        self.assertTrue(workflow_writes_allowed(real_beam_context, "beam_monitor"))
        self.assertTrue(workflow_writes_allowed(beam_context, "beam_monitor"))
        self.assertTrue(workflow_writes_allowed(real_emit_context, "emit_measure"))
        self.assertTrue(workflow_writes_allowed(emit_context, "emit_measure"))
        self.assertTrue(workflow_writes_allowed(real_energy_context, "energy_spectrum"))
        self.assertTrue(workflow_writes_allowed(energy_context, "energy_spectrum"))
        self.assertTrue(workflow_writes_allowed(real_bba_context, "bba"))
        self.assertTrue(workflow_writes_allowed(bba_context, "bba"))
        require_workflow_write_allowed(real_bba_context, "bba", "test write")
        require_workflow_write_allowed(orbit_context, "orbit", "test write")
        require_workflow_write_allowed(real_beam_context, "beam_monitor", "test write")
        require_workflow_write_allowed(real_emit_context, "emit_measure", "test write")
        require_workflow_write_allowed(real_energy_context, "energy_spectrum", "test write")
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
        self.assertEqual(workflow["corrector_upperlimit_by_backend"]["real"]["value"], 10.0)
        self.assertEqual(workflow["corrector_upperlimit_by_backend"]["real"]["unit"], "A")
        irfel_vm_orbit_runtime = orbit_runtime.load_orbit_runtime_settings(vm_orbit_context)
        irfel_real_orbit_runtime = orbit_runtime.load_orbit_runtime_settings(orbit_context)
        self.assertEqual(irfel_vm_orbit_runtime["corrector_upperlimit"], 0.001)
        self.assertEqual(irfel_vm_orbit_runtime["corrector_upperlimit_unit"], "rad")
        self.assertEqual(irfel_real_orbit_runtime["corrector_upperlimit"], 10.0)
        self.assertEqual(irfel_real_orbit_runtime["corrector_upperlimit_unit"], "A")
        self.assertEqual(irfel_vm_orbit_runtime["bpm_position_scale_to_m"], 1.0)
        self.assertEqual(irfel_real_orbit_runtime["bpm_position_scale_to_m"], 1e-3)
        self.assertEqual(irfel_vm_orbit_runtime["correction_settle_s"], 2.0)
        self.assertEqual(irfel_real_orbit_runtime["correction_settle_s"], 1.0)
        self.assertEqual(irfel_vm_orbit_runtime["svd_relative_cutoff"], 0.01)
        self.assertEqual(irfel_real_orbit_runtime["svd_relative_cutoff"], 0.01)
        self.assertEqual(irfel_vm_orbit_runtime["runtime_defaults"]["method"], "global")
        self.assertEqual(
            irfel_vm_orbit_runtime["runtime_defaults"]["local_response_source"],
            "measure_live",
        )
        self.assertEqual(irfel_vm_orbit_runtime["runtime_defaults"]["sampling_interval_s"], 2.0)
        self.assertEqual(irfel_vm_orbit_runtime["runtime_defaults"]["accuracy_um"], 10.0)
        self.assertEqual(irfel_vm_orbit_runtime["runtime_defaults"]["samples_per_step"], 1)
        self.assertEqual(irfel_vm_orbit_runtime["runtime_defaults"]["global_max_iter"], 20)
        self.assertEqual(irfel_vm_orbit_runtime["runtime_defaults"]["correction_gain"], 0.3)
        self.assertEqual(irfel_real_orbit_runtime["runtime_defaults"], irfel_vm_orbit_runtime["runtime_defaults"])
        self.assertEqual(beam_workflow["default_flag"], "PRFESA")
        self.assertEqual(
            resolve_element_image_geometry(profile, "PRF03", "vm").shape,
            (360, 270),
        )
        self.assertEqual(
            resolve_element_image_geometry(profile, "PRF04", "real").pixel_width_mm,
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
        self.assertEqual(bba_context.bba_workflow.bba1.control_backends, ("vm", "real"))
        self.assertEqual(bba_context.bba_workflow.bba2.control_backends, ("vm", "real"))
        self.assertEqual(bba_context.bba_workflow.bba1.quads, ())
        self.assertEqual(bba_context.bba_workflow.bba1.correctors, ())
        self.assertEqual(bba_context.bba_workflow.bba1.bpm1, ())
        self.assertEqual(bba_context.bba_workflow.bba1.bpm2, ())
        self.assertEqual(bba_workflow["bba1"]["control_backends"], ["vm", "real"])
        self.assertEqual(bba_workflow["write_control"]["real"], "allowed")
        self.assertIn("BPM02", vm_start_ids)
        self.assertEqual(vm_end_ids, ("PRF03",))
        self.assertEqual(vm_default_start, "QM13")
        self.assertEqual(vm_default_end, "PRF03")
        self.assertEqual(
            tuple(choice.id for choice in vm_workflow.predefined_usedlines),
            ("ALL_MAIN", "ALL_ESA", "ALL_DUMP"),
        )
        self.assertEqual(vm_workflow.segment_wait_s, 3.0)
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

    def test_orbit_real_response_matrix_rejects_legacy_raw_bpm_units(self):
        from half_linac.src.apps.orbit_correct import profile_runtime as orbit_runtime

        context = load_app_context(
            "orbit_correct",
            machine_id="irfel",
            control_backend="real",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(orbit_runtime, "ORBIT_RUNTIME_ROOT", Path(temp_dir)):
                record = orbit_runtime.write_response_matrix_snapshot(context, np.eye(10))
                metadata_path = Path(record["metadata_path"])
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                metadata.pop("bpm_position_scale_to_m")
                metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

                with self.assertRaisesRegex(ValueError, "legacy raw BPM units"):
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

    def test_orbit_real_bpm_readings_are_normalized_from_mm_to_m(self):
        from half_linac.src.apps.orbit_correct import correct as correct_module
        from half_linac.src.apps.orbit_correct import findresponse

        with patch.dict(
            os.environ,
            {
                "HALF_LINAC_MACHINE_ID": "irfel",
                "HALF_LINAC_CONTROL_BACKEND": "real",
            },
        ):
            corrector = correct_module.OrbitCorrector(
                sample_interval=0.0,
                samples_perstep=1,
                target_BPMlist=["BPM07"],
                target_BPMx_values=[-3e-3],
                target_BPMy_values=[8e-3],
            )
            with patch.object(
                correct_module,
                "caget_many",
                return_value=[-3.609395, 8.475415],
            ):
                x_value, y_value = corrector._get_avg_readings2(["x", "y"])
            calculator = findresponse.ResponseMatrixCalculator()
            with patch.object(
                findresponse,
                "caget_many",
                return_value=[-3.609395, 8.475415],
            ):
                response_values = calculator._read_bpm_values(["x", "y"], "test")

        self.assertAlmostEqual(x_value, -3.609395e-3)
        self.assertAlmostEqual(y_value, 8.475415e-3)
        self.assertAlmostEqual(corrector.target_BPMx_values[0] - x_value, 0.609395e-3)
        self.assertAlmostEqual(corrector.target_BPMy_values[0] - y_value, -0.475415e-3)
        np.testing.assert_allclose(response_values, [-3.609395e-3, 8.475415e-3])

    def test_orbit_svd_cutoff_is_invariant_to_bpm_position_units(self):
        from half_linac.src.apps.orbit_correct.correct import OrbitCorrector

        response_mm = np.array([[2.0, 0.0], [0.0, 0.5]])
        response_m = response_mm * 1e-3
        inverse_mm = OrbitCorrector._truncated_pseudo_inverse(response_mm, 0.01)
        inverse_m = OrbitCorrector._truncated_pseudo_inverse(response_m, 0.01)

        np.testing.assert_allclose(inverse_m, inverse_mm * 1e3)

    def test_orbit_svd_cutoff_controls_retained_weak_modes(self):
        from half_linac.src.apps.orbit_correct.correct import OrbitCorrector

        response = np.diag([1.0, 0.005])
        conservative = OrbitCorrector._truncated_pseudo_inverse(response, 0.01)
        permissive = OrbitCorrector._truncated_pseudo_inverse(response, 0.001)

        np.testing.assert_allclose(conservative, np.diag([1.0, 0.0]))
        np.testing.assert_allclose(permissive, np.diag([1.0, 200.0]))

    def test_orbit_correction_settle_is_separate_from_sample_interval(self):
        from half_linac.src.apps.orbit_correct import correct as correct_module

        with patch.dict(
            os.environ,
            {
                "HALF_LINAC_MACHINE_ID": "irfel",
                "HALF_LINAC_CONTROL_BACKEND": "real",
            },
        ):
            corrector = correct_module.OrbitCorrector(
                sample_interval=0.2,
                samples_perstep=3,
                target_BPMlist=["BPM07"],
                target_BPMx_values=[0.0],
                target_BPMy_values=[0.0],
                correction_settle_s=1.0,
            )
            with (
                patch.object(correct_module.time, "sleep") as sleep,
                patch.object(
                    correct_module,
                    "caget_many",
                    return_value=[-3.0, 8.0],
                ),
            ):
                corrector._wait_for_correction_settle()
                values = corrector._get_avg_readings2(["x", "y"])

        self.assertEqual([item.args[0] for item in sleep.call_args_list], [1.0, 0.2, 0.2])
        np.testing.assert_allclose(values, [-3e-3, 8e-3])

    def test_orbit_one_to_one_can_use_active_matrix_local_response(self):
        from half_linac.src.apps.orbit_correct import correct as correct_module

        with patch.dict(
            os.environ,
            {
                "HALF_LINAC_MACHINE_ID": "irfel",
                "HALF_LINAC_CONTROL_BACKEND": "vm",
            },
        ):
            corrector = correct_module.OrbitCorrector(
                sample_interval=0.0,
                cor_accuracy=1e-5,
                samples_perstep=1,
                target_BPMlist=["BPM07"],
                target_BPMx_values=[0.0],
                target_BPMy_values=[0.0],
                local_response_source="active_matrix",
            )
            corrector.pvBPMx = ["bpm-x"]
            corrector.pvBPMy = ["bpm-y"]
            corrector.pvCORx = ["cor-x"]
            corrector.pvCORy = ["cor-y"]
            matrix = np.eye(10)

            with (
                patch.object(corrector, "_load_valid_response_matrix", return_value=matrix),
                patch.object(
                    corrector,
                    "_get_avg_readings",
                    side_effect=[
                        [1e-3, 1e-3, 0.0, 0.0],
                        [0.0, 0.0],
                        [0.0, 0.0],
                    ],
                ),
                patch.object(corrector, "_write_pv") as write_pv,
            ):
                succeeded = corrector.correct_one_to_one()

        self.assertTrue(succeeded)
        write_pv.assert_not_called()
        self.assertEqual(corrector._local_response_coefficients(matrix, 1), (1.0, 1.0))

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
            "IRFEL:PS:QM01:K1:ao",
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
            "IRFEL:modulator1:HV_set:ao",
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

    def test_half_energy_spectrum_uses_coordinated_linac_energy_control(self):
        profile = load_profile("half")
        workflow = resolve_default_energy_spectrum_station(
            get_workflow(profile, "energy_spectrum")
        )
        self.assertEqual(
            resolve_channel(profile, "LINAC_ENERGY", "setpoint", "real"),
            "IN:LA:ENG",
        )
        self.assertEqual(
            resolve_channel(profile, "TRANSPORT_ENERGY", "setpoint", "real"),
            "IN:TL:ENG",
        )
        self.assertEqual(workflow["energy_element"], "LINAC_ENERGY")
        self.assertEqual(workflow["energy_set_channel"], "setpoint")
        self.assertEqual(workflow["energy_reference_channel"], "setpoint")
        self.assertEqual(workflow["energy_control_backends"], ["real"])
        self.assertNotIn("auto_tune_control_backends", workflow)
        self.assertNotIn("auto_tune_actuator", workflow)
        scan = workflow["auto_tune"]["scan"]
        self.assertEqual(scan["low"], 0)
        self.assertEqual(scan["high"], 2450)
        self.assertEqual(scan["unit"], "MeV")
        self.assertEqual(scan["mode"], "absolute")

    def test_irfel_energy_spectrum_uses_coordinated_real_energy_control(self):
        profile = load_profile("irfel")
        workflow = get_workflow(profile, "energy_spectrum")

        self.assertEqual(workflow["energy_element"], "ESA_ENERGY")
        self.assertEqual(workflow["energy_set_channel"], "setpoint")
        self.assertEqual(workflow["energy_reference_channel"], "setpoint")
        energy_element = profile.get_element("ESA_ENERGY")
        self.assertEqual(energy_element.kind, "energy")
        self.assertEqual(
            energy_element.limits,
            {"setpoint": {"low": 0, "high": 65, "unit": "MeV"}},
        )
        self.assertEqual(
            resolve_channel(profile, "ESA_ENERGY", "setpoint", "real"),
            "IRFEL:AP:ENG:A3:ao",
        )
        self.assertNotIn("auto_tune_control_backends", workflow)
        defaults = workflow["auto_tune_defaults"]
        scan = workflow["auto_tune"]["scan"]
        self.assertEqual(defaults["objective"], "brightness_then_profile_lock")
        self.assertNotIn("auto_tune_actuator", workflow)
        self.assertEqual(scan["low"], 0)
        self.assertEqual(scan["high"], 65)
        self.assertEqual(scan["unit"], "MeV")
        self.assertEqual(scan["mode"], "absolute")
        self.assertEqual(scan["coarse_steps"], 16)
        self.assertEqual(scan["fine_steps"], 31)
        center_lock = defaults["center_lock"]
        self.assertEqual(center_lock["frame_samples"], 3)
        self.assertEqual(
            center_lock["verification_frame_samples"], 5
        )
        self.assertEqual(
            center_lock["verification_min_valid_frames"], 3
        )
        self.assertEqual(center_lock["center_step"], 0.05)
        self.assertEqual(center_lock["max_total_offset"], 1.0)
        self.assertEqual(center_lock["center_tolerance_mm"], 0.2)
        self.assertEqual(workflow["default_start_element"], "QM12")
        self.assertEqual(
            workflow["optics_input_presets"]["QM12"],
            {
                "alpha_x": -2.26,
                "beta_x_m": 10.0,
                "emittance_x_nm": 102.81183,
            },
        )

    def test_energy_spectrum_auto_tune_scan_may_exceed_actuator_limits(self):
        profile = load_profile("irfel")
        workflow = dict(get_workflow(profile, "energy_spectrum"))
        workflow["auto_tune"] = {
            "scan": {**workflow["auto_tune"]["scan"], "high": 66},
        }

        _validate_energy_spectrum_workflow(profile, workflow)

    def test_energy_spectrum_rejects_unknown_auto_tune_objective(self):
        profile = load_profile("irfel")
        workflow = dict(get_workflow(profile, "energy_spectrum"))
        workflow["auto_tune_defaults"] = {
            **workflow["auto_tune_defaults"],
            "objective": "unknown",
        }

        with self.assertRaisesRegex(MachineProfileError, "auto_tune_objective"):
            _validate_energy_spectrum_workflow(profile, workflow)

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
                    "image_geometry": {"vm": {"shape": [360, 270], "pixel_width_mm": 0.02}, "real": {"shape": [1440, 1080], "pixel_width_mm": 0.02}},
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
                    "image_geometry": {"vm": {"shape": [360, 270], "pixel_width_mm": 0.02}, "real": {"shape": [1440, 1080], "pixel_width_mm": 0.02}},
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
                    "image_geometry": {"vm": {"shape": [360, 270], "pixel_width_mm": 0.02}, "real": {"shape": [1440, 1080], "pixel_width_mm": 0.02}},
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
                    "image_geometry": {"vm": {"shape": [360, 270], "pixel_width_mm": 0.02}, "real": {"shape": [1440, 1080], "pixel_width_mm": 0.02}},
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
                    "image_geometry": {"vm": {"shape": [360, 270], "pixel_width_mm": 0.02}, "real": {"shape": [1440, 1080], "pixel_width_mm": 0.02}},
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
                    "image_geometry": {"vm": {"shape": [360, 270], "pixel_width_mm": 0.02}, "real": {"shape": [1440, 1080], "pixel_width_mm": 0.02}},
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
                    "image_geometry": {"vm": {"shape": [360, 270], "pixel_width_mm": 0.02}, "real": {"shape": [1440, 1080], "pixel_width_mm": 0.02}},
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

    def test_energy_spectrum_uses_flag_element_as_vm_watch_source(self):
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
                    "image_geometry": {"vm": {"shape": [360, 270], "pixel_width_mm": 0.02}, "real": {"shape": [1440, 1080], "pixel_width_mm": 0.02}},
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
                "working_dir": "runtime/model_backend/test_machine/simulation/emit",
                "source_lattice": "src/virtual_machine/half_elegant/elegant/lattice_ini.lte",
                "energy_ini_ele_file": "src/virtual_machine/half_elegant/elegant/esa_ini.ele",
                "energy_working_dir": "runtime/model_backend/test_machine/simulation/energy",
                "energy_json_path": "runtime/model_backend/test_machine/simulation/energy/esa.json",
                "energy_lte_file": "runtime/model_backend/test_machine/simulation/energy/esa.lte",
                "energy_ele_file": "runtime/model_backend/test_machine/simulation/energy/esa.ele",
                "energy_mat_file": "runtime/model_backend/test_machine/simulation/energy/esa.mat",
                "energy_twi_file": "runtime/model_backend/test_machine/simulation/energy/esa.twi",
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
                context = load_app_context("energy_spectrum", machine_id="missingwatch")
            workflow = get_workflow(context.profile, "energy_spectrum")
            self.assertEqual(workflow["flag_element"], "PRF01")
            self.assertNotIn("vm_watch_element", workflow)

    def test_softioc_substitutions_include_vm_aliases_for_profile_only_elements(self):
        substitutions = (
            REPO_ROOT / "src" / "softIOC" / "halflinac" / "db" / "halflinac.substitutions"
        ).read_text(encoding="utf-8")
        self.assertIn('pattern {QUAD, RECORD, K1ALIAS}', substitutions)
        self.assertIn('pattern {COR, SETRECORD, SETALIAS, READRECORD}', substitutions)
        self.assertIn('pattern {FLAG, ESARECORD, ESAALIAS}', substitutions)
        self.assertIn('{ "QL03", "VMIOC:QUAD:QL03:K1", "HALF:IN:AP:QUAD:QL03:K1:ao" }', substitutions)
        self.assertIn(
            '{ "XC00", "VMIOC:COR:XC00:SET", "HALF:IN:COR:XC00:ao", "VMIOC:COR:XC00:READ" }',
            substitutions,
        )
        self.assertIn(
            '{ "YC00", "VMIOC:COR:YC00:SET", "HALF:IN:COR:YC00:ao", "VMIOC:COR:YC00:READ" }',
            substitutions,
        )
        self.assertNotIn("CQ1", substitutions)
        self.assertNotIn("CQ3", substitutions)
        self.assertNotIn("MQ1", substitutions)
        self.assertNotIn("MQ11", substitutions)
        self.assertNotIn("HIC01", substitutions)
        self.assertNotIn("VIC01", substitutions)
        self.assertIn(
            '{ "ENY", "VMIOC:FLAG:ENY:IMAGE", "HALF:IN:FLAG:ENY:image1:ArrayData:vm" }',
            substitutions,
        )
        self.assertNotIn("VMIOC:FLAG:PRF07:ESA_IMAGE", substitutions)
        self.assertIn(
            '{ "PRF02", "VMIOC:FLAG:PRF02:IMAGE", "HALF:IN:FLAG:PRF02:image1:ArrayData:vm" }',
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
                    "image_geometry": {"vm": {"shape": [360, 270], "pixel_width_mm": 0.02}, "real": {"shape": [1440, 1080], "pixel_width_mm": 0.02}},
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
                            "family": "bba1",
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
                    "bba1": {
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
                    "image_geometry": {"vm": {"shape": [360, 270], "pixel_width_mm": 0.02}, "real": {"shape": [1440, 1080], "pixel_width_mm": 0.02}},
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
                    "image_geometry": {"vm": {"shape": [360, 270], "pixel_width_mm": 0.02}, "real": {"shape": [1440, 1080], "pixel_width_mm": 0.02}},
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
                            "family": "bba1",
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

        self.assertEqual(bba_workflow.bba1.default_preset, "simple_bba1")
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
                    "image_geometry": {"vm": {"shape": [360, 270], "pixel_width_mm": 0.02}, "real": {"shape": [1440, 1080], "pixel_width_mm": 0.02}},
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
                    "family": "bba1",
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
                    "bba1": {
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
                    "image_geometry": {"vm": {"shape": [360, 270], "pixel_width_mm": 0.02}, "real": {"shape": [1440, 1080], "pixel_width_mm": 0.02}},
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
                    "bba1": {
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
                    "image_geometry": {"vm": {"shape": [360, 270], "pixel_width_mm": 0.02}, "real": {"shape": [1440, 1080], "pixel_width_mm": 0.02}},
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
                    "bba1": {
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
                    "image_geometry": {"vm": {"shape": [360, 270], "pixel_width_mm": 0.02}, "real": {"shape": [1440, 1080], "pixel_width_mm": 0.02}},
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
                    "bba1": {
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
                    "image_geometry": {"vm": {"shape": [360, 270], "pixel_width_mm": 0.02}, "real": {"shape": [1440, 1080], "pixel_width_mm": 0.02}},
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
                    "image_geometry": {"vm": {"shape": [360, 270], "pixel_width_mm": 0.02}, "real": {"shape": [1440, 1080], "pixel_width_mm": 0.02}},
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
                "working_dir": "runtime/model_backend/realonly/simulation/emit",
                "source_lattice": "src/virtual_machine/half_elegant/elegant/lattice_ini.lte",
                "energy_ini_ele_file": "src/virtual_machine/half_elegant/elegant/esa_ini.ele",
                "energy_working_dir": "runtime/model_backend/realonly/simulation/energy",
                "energy_json_path": "runtime/model_backend/realonly/simulation/energy/esa.json",
                "energy_lte_file": "runtime/model_backend/realonly/simulation/energy/esa.lte",
                "energy_ele_file": "runtime/model_backend/realonly/simulation/energy/esa.ele",
                "energy_mat_file": "runtime/model_backend/realonly/simulation/energy/esa.mat",
                "energy_twi_file": "runtime/model_backend/realonly/simulation/energy/esa.twi",
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
