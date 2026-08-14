from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import sdds

REPO_ROOT = Path(__file__).resolve().parents[1]
PARENT = REPO_ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from half_linac.src.shared.elegant_backend import (
    ElegantParser,
    VmBpmPublishSpec,
    VmPublishPlan,
    VmPublisher,
    VmWatchImagePublishSpec,
    build_vm_publish_plan,
)
from half_linac.src.shared.machine_profile import (
    build_model_backend,
    load_app_context,
    load_profile,
    resolve_machine_runtime,
)
from half_linac.src.shared.machine_profile.model_backend import ElegantModelBackend
from half_linac.src.shared.machine_profile.models import ModelBackendConfig
from half_linac.src.shared.runtime_state import read_runtime_state
from half_linac.src.virtual_machine.half_elegant.elegant_parser import elegant_parser
from half_linac.src.virtual_machine.lattice_usedline import (
    expand_lattice_line,
    format_usedline_context,
    infer_usedline_context,
    reload_initial_runtime_state,
    select_esa_line_name,
)


class ElegantBackendTests(unittest.TestCase):
    def setUp(self):
        self.elegant_dir = REPO_ROOT / "src/virtual_machine/half_elegant/elegant"
        self.lattice_file = self.elegant_dir / "lattice_ini.lte"
        self.ele_file = self.elegant_dir / "one_ini.ele"

    def test_shared_runtime_state_matches_half_wrapper(self):
        shared_parser = ElegantParser(self.lattice_file, self.ele_file, "ALL_MAIN")
        compat_parser = elegant_parser(str(self.lattice_file), str(self.ele_file), "ALL_MAIN")

        shared_state = shared_parser.build_runtime_state()
        compat_state = compat_parser.build_runtime_state()

        self.assertEqual(shared_state, compat_state)
        self.assertFalse(
            any("AP" in element for element in compat_state["lattice"].values()),
            "HALF compatibility wrapper should no longer inject AP fields.",
        )

    def test_half_dogleg_uses_independent_symmetric_qt_quads(self):
        main_state = ElegantParser(self.lattice_file, self.ele_file, "ALL_MAIN").build_runtime_state()
        esa_state = ElegantParser(self.lattice_file, self.ele_file, "ALL_ESA").build_runtime_state()

        dogleg_quads = tuple(f"QT{index:02d}" for index in range(5, 13))
        dogleg_quad_set = set(dogleg_quads)
        self.assertEqual(
            tuple(element for element in main_state["usedline"] if element in dogleg_quad_set),
            dogleg_quads,
        )
        self.assertEqual(
            tuple(element for element in esa_state["usedline"] if element in dogleg_quad_set),
            ("QT05", "QT06"),
        )
        for upstream, downstream in (
            ("QT05", "QT12"),
            ("QT06", "QT11"),
            ("QT07", "QT10"),
            ("QT08", "QT09"),
        ):
            self.assertEqual(main_state["lattice"][upstream]["L"], "0.3")
            self.assertEqual(
                main_state["lattice"][upstream]["L"],
                main_state["lattice"][downstream]["L"],
            )
            self.assertEqual(
                main_state["lattice"][upstream]["K1"],
                main_state["lattice"][downstream]["K1"],
            )

        for reference_id, active_ids in {
            "QHL1": ("QT05", "QT12"),
            "QHL2": ("QT06", "QT11"),
            "QHL3": ("QT07", "QT10"),
            "QHL4": ("QT08", "QT09"),
        }.items():
            self.assertIn(reference_id, main_state["lattice"])
            self.assertNotIn(reference_id, main_state["usedline"])
            self.assertNotIn(reference_id, esa_state["usedline"])
            for active_id in active_ids:
                self.assertEqual(
                    main_state["lattice"][reference_id]["L"],
                    main_state["lattice"][active_id]["L"],
                )
                self.assertEqual(
                    main_state["lattice"][reference_id]["K1"],
                    main_state["lattice"][active_id]["K1"],
                )

    def test_json_to_lte_ele_matches_half_wrapper_output(self):
        shared_parser = ElegantParser(self.lattice_file, self.ele_file, "ALL_MAIN")
        compat_parser = elegant_parser(str(self.lattice_file), str(self.ele_file), "ALL_MAIN")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            shared_dir = tmpdir_path / "shared"
            compat_dir = tmpdir_path / "compat"
            shared_dir.mkdir()
            compat_dir.mkdir()
            shared_json = shared_dir / "runtime.json"
            compat_json = compat_dir / "runtime.json"
            shared_lte = shared_dir / "lattice.lte"
            shared_ele = shared_dir / "one.ele"
            compat_lte = compat_dir / "lattice.lte"
            compat_ele = compat_dir / "one.ele"

            shared_parser.dump_runtime_state(shared_json)
            compat_parser.dump2json(str(compat_json))

            shared_parser.json_to_lte_ele(shared_lte, shared_ele, shared_json)
            compat_parser.json2lte_ele(str(compat_lte), str(compat_ele), str(compat_json))

            self.assertEqual(shared_lte.read_text(encoding="utf-8"), compat_lte.read_text(encoding="utf-8"))
            self.assertEqual(shared_ele.read_text(encoding="utf-8"), compat_ele.read_text(encoding="utf-8"))

    def test_irfel_elegant_inputs_parse_and_roundtrip(self):
        elegant_dir = REPO_ROOT / "src/virtual_machine/irfel_elegant/elegant"
        parser = ElegantParser(
            elegant_dir / "lattice_ini.lte",
            elegant_dir / "one_ini.ele",
            "ALL_MAIN",
        )
        state = parser.build_runtime_state()

        self.assertIn("QM01", state["lattice"])
        self.assertIn("UND", state["usedline"])
        self.assertNotIn("USE", state["lattice"])
        self.assertEqual(
            state["control"]["error_control"]["clear_error_settings"],
            "1",
        )
        self.assertEqual(
            state["control"]["error_element"]["element_type"],
            "QUAD",
        )
        self.assertEqual(
            state["control"]["error_element"]["amplitude"],
            "0e-5",
        )
        esa_state = ElegantParser(
            elegant_dir / "lattice_ini.lte",
            elegant_dir / "one_ini.ele",
            "ALL_ESA",
        ).build_runtime_state()
        self.assertIn("PRFESA", esa_state["usedline"])

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            runtime_json = tmpdir_path / "irfel.json"
            parser.dump_runtime_state(runtime_json)
            lattice_path, ele_path = parser.json_to_lte_ele(
                tmpdir_path / "lattice.lte",
                tmpdir_path / "one.ele",
                runtime_json,
            )
            self.assertIn("ALL_MAIN: LINE", lattice_path.read_text(encoding="utf-8"))
            self.assertIn("&sdds_beam", ele_path.read_text(encoding="utf-8"))
            self.assertIn("&error_control", ele_path.read_text(encoding="utf-8"))
            self.assertIn("&error_element", ele_path.read_text(encoding="utf-8"))
            self.assertIn("use_beamline = ALL_MAIN", ele_path.read_text(encoding="utf-8"))

        self.assertEqual(lattice_path.name, "lattice.lte")
        self.assertEqual(ele_path.name, "one.ele")

    def test_model_backend_skips_unmatched_error_element_for_quad_to_flag_map(self):
        class FakeSdds:
            def __init__(self, _index):
                self.columnData = [[[float(index)]] for index in range(48)]

            def load(self, _path):
                return None

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            elegant_dir = tmpdir_path / "elegant"
            elegant_dir.mkdir()
            source_lte = elegant_dir / "lattice_ini.lte"
            emit_ini = elegant_dir / "emit_ini.ele"
            emit_lte = elegant_dir / "emit.lte"
            emit_ele = elegant_dir / "emit.ele"
            emit_json = tmpdir_path / "emit.json"
            emit_mat = elegant_dir / "emit.mat"

            source_lte.write_text(
                "\n".join(
                    [
                        "QT02: QUAD,L=0.15,K1=2.5",
                        "D1: DRIF,L=1.0",
                        'PRF07: WATCH,FILENAME="PRF07.out",MODE="coord",DISABLE=0',
                        "ALL_MAIN: LINE = (QT02,D1,PRF07)",
                    ]
                ),
                encoding="utf-8",
            )
            emit_ini.write_text(
                "\n".join(
                    [
                        "&run_setup",
                        "    lattice = emit_ini.lte,",
                        "    use_beamline = ALL_MAIN,",
                        "&end",
                        "&run_control",
                        "    n_steps = 1,",
                        "&end",
                        "&matrix_output",
                        "    SDDS_output = %s.mat,",
                        "&end",
                        "&error_control",
                        "    clear_error_settings = 1,",
                        "&end",
                        "&error_element",
                        "    name = *,",
                        "    element_type = QUAD,",
                        "    item = FSE,",
                        "    amplitude = 0e-5,",
                        "&end",
                        "&bunched_beam",
                        "    n_particles_per_bunch = 1,",
                        "&end",
                        "&track &end",
                    ]
                ),
                encoding="utf-8",
            )
            backend = ElegantModelBackend(
                ModelBackendConfig(
                    name="simulation",
                    engine="elegant",
                    config={
                        "working_dir": str(elegant_dir),
                        "source_json": str(tmpdir_path / "runtime.json"),
                        "source_lattice": str(source_lte),
                        "emit_ini_ele": str(emit_ini),
                        "emit_lte": str(emit_lte),
                        "emit_ele": str(emit_ele),
                        "emit_json": str(emit_json),
                        "emit_mat": str(emit_mat),
                        "emit_log": "emit.log",
                        "line_name": "ALL_MAIN",
                    },
                )
            )

            with patch(
                "half_linac.src.shared.machine_profile.model_backend.run_elegant_input",
            ), patch("half_linac.src.shared.machine_profile.model_backend.sdds.SDDS", FakeSdds):
                backend.get_map("QT02", "PRF07")

            self.assertNotIn("&error_element", emit_ele.read_text(encoding="utf-8"))
            self.assertNotIn("QT02", emit_lte.read_text(encoding="utf-8"))

    def test_model_backend_applies_field_level_lattice_overrides(self):
        class FakeSdds:
            def __init__(self, _index):
                self.columnData = [[[float(index)]] for index in range(48)]

            def load(self, _path):
                return None

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            elegant_dir = tmpdir_path / "elegant"
            elegant_dir.mkdir()
            source_lte = elegant_dir / "lattice_ini.lte"
            emit_ini = elegant_dir / "emit_ini.ele"
            emit_lte = elegant_dir / "emit.lte"
            emit_ele = elegant_dir / "emit.ele"
            emit_json = tmpdir_path / "emit.json"
            emit_mat = elegant_dir / "emit.mat"

            source_lte.write_text(
                "\n".join(
                    [
                        "D0: DRIF,L=0.1",
                        "QT02: QUAD,L=0.15,K1=2.5",
                        "C1: HKICK,L=0,KICK=0.001",
                        'PRF07: WATCH,FILENAME="PRF07.out",MODE="coord",DISABLE=0',
                        "ALL_MAIN: LINE = (D0,QT02,C1,PRF07)",
                    ]
                ),
                encoding="utf-8",
            )
            emit_ini.write_text(
                "\n".join(
                    [
                        "&run_setup",
                        "    lattice = emit_ini.lte,",
                        "    use_beamline = ALL_MAIN,",
                        "&end",
                        "&run_control",
                        "    n_steps = 1,",
                        "&end",
                        "&matrix_output",
                        "    SDDS_output = %s.mat,",
                        "&end",
                        "&track &end",
                    ]
                ),
                encoding="utf-8",
            )
            backend = ElegantModelBackend(
                ModelBackendConfig(
                    name="simulation",
                    engine="elegant",
                    config={
                        "working_dir": str(elegant_dir),
                        "source_json": str(tmpdir_path / "runtime.json"),
                        "source_lattice": str(source_lte),
                        "emit_ini_ele": str(emit_ini),
                        "emit_lte": str(emit_lte),
                        "emit_ele": str(emit_ele),
                        "emit_json": str(emit_json),
                        "emit_mat": str(emit_mat),
                        "emit_log": "emit.log",
                        "line_name": "ALL_MAIN",
                    },
                )
            )

            with patch(
                "half_linac.src.shared.machine_profile.model_backend.run_elegant_input",
            ), patch("half_linac.src.shared.machine_profile.model_backend.sdds.SDDS", FakeSdds):
                backend.get_map(
                    "D0",
                    "PRF07",
                    lattice_overrides={
                        "QT02": {"K1": 9.5},
                        "C1": {"KICK": 0.004},
                    },
                )

            emit_lte_text = emit_lte.read_text(encoding="utf-8")
            self.assertIn('QT02: QUAD,L="0.15",K1="9.5"', emit_lte_text)
            self.assertIn('C1: HKICK,L="0",KICK="0.004"', emit_lte_text)

    def test_model_backend_twiss_profile_orders_forward_and_backward_paths(self):
        backend = build_model_backend(
            load_app_context(
                "emit_measure",
                machine_id="half",
                control_backend="vm",
            )
        )
        matrix = np.eye(6)
        rows = (
            {
                "element_name": "_BEG_",
                "element_type": "MARK",
                "s_m": 0.0,
                "beta_x_m": 10.0,
                "alpha_x": 1.0,
            },
            {
                "element_name": "D1",
                "element_type": "DRIF",
                "s_m": 1.0,
                "beta_x_m": 11.0,
                "alpha_x": 0.5,
            },
            {
                "element_name": "Q2",
                "element_type": "QUAD",
                "element_length_m": 0.2,
                "element_k1_m2": -3.0,
                "s_m": 2.0,
                "beta_x_m": 12.0,
                "alpha_x": 0.0,
            },
        )
        twiss0 = {"beta0": 10.0, "alpha0": 1.0, "gamma0": 0.2}

        with patch.object(backend, "_usedline_index_pair", return_value=(0, 2)), patch.object(
            backend,
            "_run_optics_profile",
            return_value=(matrix, rows),
        ):
            forward = backend.get_twiss_profile("Q1", "Q2", twiss0)

        self.assertEqual([row["element_name"] for row in forward.rows], ["Q1", "D1", "Q2"])
        self.assertEqual([row["distance_m"] for row in forward.rows], [0.0, 1.0, 2.0])
        self.assertAlmostEqual(forward.rows[0]["gamma"], 0.2)
        self.assertAlmostEqual(forward.rows[-1]["element_length_m"], 0.2)
        self.assertAlmostEqual(forward.rows[-1]["element_k1_m2"], -3.0)

        with patch.object(backend, "_usedline_index_pair", return_value=(2, 0)), patch.object(
            backend,
            "_run_optics_profile",
            side_effect=((matrix, ()), (matrix, rows)),
        ):
            backward = backend.get_twiss_profile("Q2", "Q1", twiss0, inverse=True)

        self.assertEqual([row["element_name"] for row in backward.rows], ["Q2", "D1", "Q1"])
        self.assertEqual([row["distance_m"] for row in backward.rows], [0.0, 1.0, 2.0])
        self.assertAlmostEqual(backward.rows[-1]["gamma"], 0.2)

    def test_model_backend_owns_energy_dispersion_and_optics_runs(self):
        class FakeSdds:
            def __init__(self, _index):
                self.columnName = []
                self.columnData = []

            def load(self, path):
                if str(path).endswith(".mat"):
                    self.columnData = [[[float(index)]] for index in range(48)]
                    return
                self.columnName = ["s", "betax", "alphax", "x", "etax"]
                self.columnData = [
                    [[0.0, 1.0]],
                    [[10.0, 12.0]],
                    [[-1.0, -2.0]],
                    [[0.0, 0.0]],
                    [[0.4, 0.75]],
                ]

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source_lattice = root / "lattice_ini.lte"
            optics_ini = root / "optics_ini.ele"
            energy_ini = root / "esa_ini.ele"
            source_lattice.write_text(
                "\n".join(
                    [
                        "Q1: QUAD,L=0.1,K1=1.0",
                        "D1: DRIF,L=1.0",
                        'FLAG: WATCH,FILENAME="flag.out",MODE="coord",DISABLE=0',
                        "ESA: LINE = (Q1,D1,FLAG)",
                    ]
                ),
                encoding="utf-8",
            )
            controls = "\n".join(
                [
                    "&run_setup",
                    "  lattice=initial.lte,use_beamline=ESA",
                    "&end",
                    "&run_control",
                    "  n_steps=1",
                    "&end",
                    "&matrix_output",
                    "  SDDS_output=%s.mat",
                    "&end",
                    "&twiss_output",
                    "  filename=%s.twi,beta_x=1,alpha_x=0",
                    "&end",
                    "&track",
                    "&end",
                ]
            )
            optics_ini.write_text(controls, encoding="utf-8")
            energy_ini.write_text(controls, encoding="utf-8")
            backend = ElegantModelBackend(
                ModelBackendConfig(
                    name="simulation",
                    engine="elegant",
                    config={
                        "source_json": str(root / "source.json"),
                        "source_lattice": str(source_lattice),
                        "asset_dir": str(root),
                        "optics_working_dir": str(root / "optics"),
                        "optics_ini_ele": str(optics_ini),
                        "optics_lte": str(root / "optics/optics.lte"),
                        "optics_ele": str(root / "optics/optics.ele"),
                        "optics_json": str(root / "optics/optics.json"),
                        "optics_mat": str(root / "optics/optics.mat"),
                        "optics_log": "optics.log",
                        "line_name": "ESA",
                        "energy_working_dir": str(root / "energy"),
                        "energy_ini_ele": str(energy_ini),
                        "energy_json": str(root / "energy/esa.json"),
                        "energy_lte": str(root / "energy/esa.lte"),
                        "energy_ele": str(root / "energy/esa.ele"),
                        "energy_mat": str(root / "energy/esa.mat"),
                        "energy_twi": str(root / "energy/esa.twi"),
                        "energy_log": "esa.log",
                    },
                )
            )

            with patch(
                "half_linac.src.shared.machine_profile.model_backend.run_elegant_input",
            ), patch("half_linac.src.shared.machine_profile.model_backend.sdds.SDDS", FakeSdds):
                dispersion = backend.get_energy_dispersion(
                    "ESA",
                    lattice_overrides={"Q1": {"K1": 2.5}},
                )
                optics = backend.get_energy_optics(
                    "ESA",
                    "Q1",
                    "FLAG",
                    beta_x_m=8.0,
                    alpha_x=-0.5,
                    lattice_overrides={"Q1": {"K1": 2.5}},
                )

            self.assertEqual(dispersion, 17.0)
            self.assertEqual(optics.beta_x_m, 12.0)
            self.assertEqual(optics.alpha_x, -2.0)
            self.assertEqual(optics.dispersion_x_m, 0.75)
            self.assertIn('K1="2.5"', (root / "energy/esa.lte").read_text(encoding="utf-8"))

    def test_lattice_usedline_helper_expands_irfel_main_and_esa_lines(self):
        runtime = resolve_machine_runtime("irfel")
        state = read_runtime_state(runtime.vm.runtime_json)

        main_usedline = expand_lattice_line(state["lattice"], runtime.vm.line_name)
        esa_line = select_esa_line_name(state["lattice"], configured_line_name="ALL_ESA")
        esa_usedline = expand_lattice_line(state["lattice"], esa_line)

        self.assertEqual(esa_line, "ALL_ESA")
        self.assertIn("PRF03", main_usedline)
        self.assertNotIn("PRF03", esa_usedline)
        self.assertIn("PRFESA", esa_usedline)
        self.assertTrue(all(state["lattice"][element]["TYPE"] != "LINE" for element in main_usedline))

    def test_runtime_usedline_context_describes_full_and_segment_lines(self):
        runtime = resolve_machine_runtime("irfel")
        state = read_runtime_state(runtime.vm.runtime_json)

        main_usedline = expand_lattice_line(state["lattice"], "ALL_MAIN")
        state["usedline"] = main_usedline
        full_context = infer_usedline_context(runtime, state)

        start = main_usedline.index("QM13")
        end = main_usedline.index("PRF03") + 1
        state["usedline"] = main_usedline[start:end]
        segment_context = infer_usedline_context(runtime, state)

        self.assertEqual(full_context["mode"], "full")
        self.assertEqual(full_context["line"], "ALL_MAIN")
        self.assertEqual(format_usedline_context(full_context), f"ALL_MAIN (full, {len(main_usedline)} elements)")
        self.assertEqual(segment_context["mode"], "segment")
        self.assertEqual(segment_context["parent_usedline"], "ALL_MAIN")
        self.assertEqual(segment_context["start"], "QM13")
        self.assertEqual(segment_context["end"], "PRF03")

    def test_half_compat_parser_defaults_follow_machine_runtime_metadata(self):
        runtime = resolve_machine_runtime()
        compat_parser = elegant_parser(str(self.lattice_file), str(self.ele_file), "ALL_MAIN")

        self.assertEqual(compat_parser._resolve_runtime_json_path(), runtime.vm.runtime_json)
        self.assertEqual(
            compat_parser._resolve_runtime_json_path(runtime.vm.runtime_json.name),
            runtime.vm.runtime_json,
        )
        self.assertEqual(
            compat_parser._resolve_runtime_json_path(f"./{runtime.vm.runtime_json.name}"),
            runtime.vm.runtime_json,
        )
        self.assertEqual(
            compat_parser._resolve_elegant_path(None, compat_parser.default_lattice_output_path),
            compat_parser.default_lattice_output_path,
        )
        self.assertEqual(
            compat_parser._resolve_elegant_path(
                "./elegant/lattice.lte",
                compat_parser.default_lattice_output_path,
            ),
            compat_parser.default_lattice_output_path,
        )
        self.assertEqual(
            compat_parser._resolve_elegant_path(
                "./elegant/one.ele",
                compat_parser.default_ele_output_path,
            ),
            compat_parser.default_ele_output_path,
        )

    def test_load_bpm_centroids_matches_current_half_sample(self):
        if not hasattr(sdds, "SDDS"):
            self.skipTest("Legacy SDDS python binding is unavailable in this test environment.")
        parser = ElegantParser(self.lattice_file, self.ele_file, "ALL_MAIN")
        try:
            bpm = parser.load_bpm_centroids(self.elegant_dir / "one.bpmcen")
        except RuntimeError as exc:
            self.skipTest(str(exc))
        self.assertTrue(bpm)
        sample = next(iter(bpm.values()))
        self.assertIn("Cx", sample)
        self.assertIn("Cy", sample)
        self.assertIsInstance(sample["Cx"], float)
        self.assertIsInstance(sample["Cy"], float)

    def test_load_watch_image_matches_half_geometry(self):
        if not hasattr(sdds, "SDDS"):
            self.skipTest("Legacy SDDS python binding is unavailable in this test environment.")
        parser = ElegantParser(self.lattice_file, self.ele_file, "ALL_MAIN")
        try:
            image = parser.load_watch_image(
                self.elegant_dir / "PRF06.out",
                pixel_shape=(360, 270),
                pixel_width_mm=0.02,
            )
        except RuntimeError as exc:
            self.skipTest(str(exc))
        self.assertIsInstance(image, np.ndarray)
        self.assertEqual(image.shape, (360 * 270,))
        self.assertGreaterEqual(float(np.sum(image)), 0.0)

    def test_shared_callers_no_longer_import_half_elegant_parser(self):
        managed_paths = [
            REPO_ROOT / "src/shared/machine_profile/model_backend.py",
            REPO_ROOT / "src/apps/emit_measure/main.py",
            REPO_ROOT / "src/apps/energy_spectrum/main.py",
            REPO_ROOT / "src/softIOC/mainIOC.py",
            REPO_ROOT / "src/softIOC/pv_server.py",
        ]
        offenders = []
        for path in managed_paths:
            text = path.read_text(encoding="utf-8")
            if "half_elegant.elegant_parser" in text:
                offenders.append(str(path.relative_to(REPO_ROOT)))
        self.assertEqual(
            offenders,
            [],
            f"Shared/model/IOC callers must not import half_elegant.elegant_parser: {offenders}",
        )

    def test_build_vm_publish_plan_uses_profile_channel_mappings(self):
        profile = load_profile("half")
        plan = build_vm_publish_plan(profile)

        bpm_specs = {spec.element_id: spec for spec in plan.bpm_specs}
        self.assertEqual(bpm_specs["BPM03"].x_pv, "HALF:IN:BPM:BPM03:X:ao")
        self.assertEqual(bpm_specs["BPM03"].y_pv, "HALF:IN:BPM:BPM03:Y:ao")
        self.assertEqual(len(bpm_specs), 43)

        watch_specs = {
            (spec.target_element_id, spec.logical_channel): spec
            for spec in plan.watch_image_specs
        }
        self.assertNotIn(("PRF01", "image"), watch_specs)
        for flag_id in (
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
            "ENY",
        ):
            spec = watch_specs[(flag_id, "image")]
            self.assertEqual(spec.source_watch_id, flag_id)
            self.assertEqual(spec.pv_name, f"HALF:IN:FLAG:{flag_id}:image1:ArrayData:vm")

        eny_specs = [
            spec
            for spec in plan.watch_image_specs
            if spec.source_watch_id == "ENY" and spec.target_element_id == "ENY"
        ]
        self.assertEqual(len(eny_specs), 1)
        self.assertEqual(eny_specs[0].logical_channel, "image")
        self.assertEqual(eny_specs[0].pv_name, "HALF:IN:FLAG:ENY:image1:ArrayData:vm")
        self.assertEqual(eny_specs[0].pixel_shape, (720, 270))
        self.assertEqual(eny_specs[0].pixel_width_mm, 0.02)

    def test_shared_publisher_uses_plan_pvs_for_bpm_updates(self):
        publisher = VmPublisher()
        plan = VmPublishPlan(
            bpm_specs=(
                VmBpmPublishSpec(
                    element_id="BPMX",
                    x_pv="CUSTOM:BPMX:X",
                    y_pv="CUSTOM:BPMX:Y",
                ),
            )
        )

        with patch(
            "half_linac.src.shared.elegant_backend.publisher._load_bpm_centroids_from_sdds",
            return_value={"BPMX": {"Cx": 1.25, "Cy": -2.5}},
        ), patch(
            "half_linac.src.shared.elegant_backend.publisher.caput_many",
            side_effect=([True], [True]),
        ) as caput_many_mock:
            ok = publisher.publish_bpms(plan, REPO_ROOT / "fake.bpmcen")

        self.assertTrue(ok)
        self.assertEqual(caput_many_mock.call_count, 2)
        self.assertEqual(caput_many_mock.call_args_list[0].args[0], ["CUSTOM:BPMX:X"])
        self.assertEqual(caput_many_mock.call_args_list[0].args[1], [1.25])
        self.assertEqual(caput_many_mock.call_args_list[1].args[0], ["CUSTOM:BPMX:Y"])
        self.assertEqual(caput_many_mock.call_args_list[1].args[1], [-2.5])

    def test_shared_publisher_reports_missing_watch_source(self):
        publisher = VmPublisher()
        plan = VmPublishPlan(
            watch_image_specs=(
                VmWatchImagePublishSpec(
                    source_watch_id="PRFESA",
                    target_element_id="PRF07",
                    logical_channel="esa_image",
                    pv_name="CUSTOM:FLAG:ESA",
                    pixel_shape=(4, 3),
                    pixel_width_mm=0.02,
                ),
            )
        )

        with patch("builtins.print") as print_mock:
            ok = publisher.publish_watch_images(
                plan,
                lattice={},
                usedline=["PRFESA"],
                elegant_dir=self.elegant_dir,
            )

        self.assertFalse(ok)
        self.assertTrue(any("PRFESA" in str(call) for call in print_mock.call_args_list))

    def test_shared_publisher_skips_watch_not_in_usedline(self):
        publisher = VmPublisher()
        plan = VmPublishPlan(
            watch_image_specs=(
                VmWatchImagePublishSpec(
                    source_watch_id="PRF06",
                    target_element_id="PRF06",
                    logical_channel="image",
                    pv_name="CUSTOM:FLAG:PRF06",
                    pixel_shape=(4, 3),
                    pixel_width_mm=0.02,
                ),
            )
        )
        lattice = {
            "PRF06": {
                "TYPE": "WATCH",
                "MODE": "coord",
                "DISABLE": "0",
            }
        }

        with patch("half_linac.src.shared.elegant_backend.publisher.caput") as caput_mock:
            ok = publisher.publish_watch_images(
                plan,
                lattice=lattice,
                usedline=[],
                elegant_dir=self.elegant_dir,
            )

        self.assertTrue(ok)
        caput_mock.assert_not_called()

    def test_shared_publisher_uses_watch_filename_from_lattice(self):
        publisher = VmPublisher()
        plan = VmPublishPlan(
            watch_image_specs=(
                VmWatchImagePublishSpec(
                    source_watch_id="PRF03",
                    target_element_id="PRF03",
                    logical_channel="image",
                    pv_name="CUSTOM:FLAG:PRF03",
                    pixel_shape=(2, 2),
                    pixel_width_mm=0.02,
                ),
            )
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            elegant_dir = Path(tmpdir)
            watch_path = elegant_dir / "custom-prf03.out"
            watch_path.write_text("stub", encoding="utf-8")
            lattice = {
                "PRF03": {
                    "TYPE": "WATCH",
                    "MODE": "coord",
                    "DISABLE": "0",
                    "FILENAME": '"custom-prf03.out"',
                }
            }

            with patch(
                "half_linac.src.shared.elegant_backend.publisher._load_watch_image_from_sdds",
                return_value=np.arange(4),
            ) as load_image_mock, patch(
                "half_linac.src.shared.elegant_backend.publisher.caput",
                return_value=True,
            ) as caput_mock:
                ok = publisher.publish_watch_images(
                    plan,
                    lattice=lattice,
                    usedline=["PRF03"],
                    elegant_dir=elegant_dir,
                )

        self.assertTrue(ok)
        load_image_mock.assert_called_once()
        self.assertEqual(load_image_mock.call_args.args[0], watch_path)
        self.assertEqual(caput_mock.call_args.args[0], "CUSTOM:FLAG:PRF03")

    def test_shared_publisher_sources_do_not_import_runtime_config_or_half_pv_prefixes(self):
        parser_source = (REPO_ROOT / "src/shared/elegant_backend/parser.py").read_text(encoding="utf-8")
        publisher_source = (
            REPO_ROOT / "src/shared/elegant_backend/publisher.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("runtime_config as st", parser_source)
        self.assertNotIn("runtime_config as st", publisher_source)
        self.assertNotIn("HALF:IN:", publisher_source)

    def test_machine_driven_gui_entrypoints_still_repopulate_generated_default_choices(self):
        beam_source = (REPO_ROOT / "src/apps/beam_monitor/main.py").read_text(encoding="utf-8")
        emit_source = (REPO_ROOT / "src/apps/emit_measure/main.py").read_text(encoding="utf-8")
        self.assertIn("self.flag_selec.clear()", beam_source)
        self.assertIn("self.flag_selec.addItems(self.flag_ids)", beam_source)
        self.assertIn("self._set_combo_items(self.comboBox_4, flag_items)", emit_source)

    def test_emit_measure_fit_summary_marks_partial_plane_results(self):
        os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "matplotlib"))
        emit_app_dir = REPO_ROOT / "src/apps/emit_measure"
        if str(emit_app_dir) not in sys.path:
            sys.path.insert(0, str(emit_app_dir))

        from half_linac.src.apps.emit_measure.main import _method_fit_summary

        summary = _method_fit_summary(
            "leastSquares",
            {"status": "valid", "ex": 0.1, "determinant": 0.01},
            {"status": "non_physical", "message": "non-physical beam matrix determinant=-1"},
        )

        self.assertEqual(summary["status"], "partial")
        self.assertEqual(summary["xplane"]["status"], "valid")
        self.assertEqual(summary["yplane"]["status"], "non_physical")
        self.assertEqual(summary["xplane"]["emittance"], 0.1)
        self.assertIn("determinant", summary["yplane"]["message"])

    def test_emit_measure_transfer_matrix_fit_uses_stable_lstsq_diagnostics(self):
        os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "matplotlib"))
        emit_app_dir = REPO_ROOT / "src/apps/emit_measure"
        if str(emit_app_dir) not in sys.path:
            sys.path.insert(0, str(emit_app_dir))

        from half_linac.src.apps.emit_measure.main import scanThread

        design = np.array(
            [
                [1.0, 0.0, 0.0],
                [1.0, 2.0, 1.0],
                [1.0, 4.0, 4.0],
                [1.0, 6.0, 9.0],
            ]
        )
        beam_matrix = np.array([4.0, 1.0, 2.0])
        result = scanThread._solveMat(
            SimpleNamespace(EnergyMeV=2200.0),
            design.reshape(-1),
            np.arange(design.shape[0]),
            design @ beam_matrix,
        )

        self.assertEqual(result.status, "valid")
        self.assertEqual(result.solver, "numpy.linalg.lstsq")
        self.assertEqual(result.rank, 3)
        self.assertGreater(result.condition_number, 1.0)
        self.assertLess(result.residual_rms, 1.0e-12)
        self.assertAlmostEqual(result.determinant, 7.0)

    def test_emit_measure_transfer_matrix_fit_rejects_rank_deficient_scan(self):
        os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "matplotlib"))
        emit_app_dir = REPO_ROOT / "src/apps/emit_measure"
        if str(emit_app_dir) not in sys.path:
            sys.path.insert(0, str(emit_app_dir))

        from half_linac.src.apps.emit_measure.main import scanThread

        design = np.tile([1.0, 2.0, 1.0], (4, 1))
        result = scanThread._solveMat(
            SimpleNamespace(EnergyMeV=2200.0),
            design.reshape(-1),
            np.arange(design.shape[0]),
            np.full(design.shape[0], 8.0),
        )

        self.assertEqual(result.status, "rank_deficient")
        self.assertLess(result.rank, 3)
        self.assertIn("scan points", result.message)

    def test_emit_measure_transfer_matrix_fit_rejects_ill_conditioned_scan(self):
        os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "matplotlib"))
        emit_app_dir = REPO_ROOT / "src/apps/emit_measure"
        if str(emit_app_dir) not in sys.path:
            sys.path.insert(0, str(emit_app_dir))

        from half_linac.src.apps.emit_measure.main import scanThread

        design = np.diag([1.0, 1.0e-7, 1.0e-13])
        result = scanThread._solveMat(
            SimpleNamespace(EnergyMeV=2200.0),
            design.reshape(-1),
            np.arange(design.shape[0]),
            design @ np.array([1.0, 0.0, 1.0]),
        )

        self.assertEqual(result.status, "ill_conditioned")
        self.assertEqual(result.rank, 3)
        self.assertGreater(result.condition_number, 1.0e12)
        self.assertIn("condition", result.message)

    def test_emit_measure_adaptive_validation_combines_coverage_and_reconstruction(self):
        os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "matplotlib"))
        emit_app_dir = REPO_ROOT / "src/apps/emit_measure"
        if str(emit_app_dir) not in sys.path:
            sys.path.insert(0, str(emit_app_dir))

        from half_linac.src.apps.emit_measure.main import (
            _method_fit_summary,
            scanThread,
        )

        x_payload = {"status": "valid", "ex": 0.1}
        y_payload = {
            "status": "non_physical",
            "message": "non-physical beam matrix determinant=-1",
        }
        summary = _method_fit_summary("leastSquares", x_payload, y_payload)
        worker = SimpleNamespace(
            scan_strategy="adaptive",
            adaptive_plane_validation={
                "x": {"status": "validated", "message": "waist bracketed", "warnings": []},
                "y": {"status": "validated", "message": "waist bracketed", "warnings": []},
                "supplement_attempted": [],
            },
            final_plane_validation=None,
        )

        scanThread._attach_adaptive_plane_validation(
            worker,
            summary,
            {"xplane": x_payload, "yplane": y_payload},
        )

        self.assertEqual(summary["quality_status"], "partial")
        self.assertEqual(summary["xplane"]["validation_status"], "validated")
        self.assertEqual(summary["yplane"]["validation_status"], "non_physical")
        self.assertEqual(worker.final_plane_validation["status"], "partial")

    def test_esa_auto_tuner_demo_uses_machine_profile_instead_of_half_bend_pv(self):
        source = (REPO_ROOT / "src/apps/energy_spectrum/esa_auto_tuner.py").read_text(encoding="utf-8")
        self.assertNotIn("HALF:IN:ESA:PRF01:CurrentSet", source)
        self.assertIn("load_profile()", source)
        self.assertIn("resolve_write_target(", source)

    def test_softioc_bend_substitutions_use_angle_channel(self):
        source = (REPO_ROOT / "src/softIOC/pv_server.py").read_text(encoding="utf-8")
        self.assertIn('_resolve_vm_writable_channel(element.id, element.kind, "angle")', source)
        self.assertIn("ANGLEALIAS", source)
        self.assertNotIn('"VMIOC:BEND:{element.id}:KICK"', source)

    def test_half_vm_helper_scripts_use_runtime_resolver_for_paths(self):
        helper_paths = [
            REPO_ROOT / "src/virtual_machine/half_elegant/full_VM.py",
            REPO_ROOT / "src/virtual_machine/half_elegant/simply_VM.py",
            REPO_ROOT / "src/virtual_machine/half_elegant/err_gene_VM.py",
            REPO_ROOT / "src/virtual_machine/half_elegant/transfer_ESAline.py",
        ]
        offenders = []
        legacy_path_snippet = 'src/virtual_machine/half_elegant/halflinac.json'
        for path in helper_paths:
            text = path.read_text(encoding="utf-8")
            if legacy_path_snippet in text:
                offenders.append(str(path.relative_to(REPO_ROOT)))
        self.assertEqual(
            offenders,
            [],
            f"HALF VM helper scripts should resolve runtime JSON through machine runtime metadata: {offenders}",
        )

    def test_full_vm_command_reloads_initial_runtime_state(self):
        source = (REPO_ROOT / "src/virtual_machine/common/full_VM.py").read_text(encoding="utf-8")
        gui_source = (REPO_ROOT / "src/virtual_machine/common/mainVM.py").read_text(encoding="utf-8")

        self.assertIn("reload_initial_runtime_state_cli", source)
        self.assertNotIn("restore_main_usedline_cli", source)
        self.assertIn("Reload Initial Lattice", gui_source)

    def test_reload_initial_runtime_state_syncs_irfel_vm_writable_pvs(self):
        runtime = resolve_machine_runtime("irfel")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            fake_runtime = SimpleNamespace(
                profile=runtime.profile,
                vm=SimpleNamespace(
                    bootstrap_lattice=runtime.vm.bootstrap_lattice,
                    bootstrap_ele=runtime.vm.bootstrap_ele,
                    line_name=runtime.vm.line_name,
                    runtime_json=tmpdir_path / "irfel.json",
                ),
            )

            with patch(
                "half_linac.src.virtual_machine.lattice_usedline.resolve_machine_runtime",
                return_value=fake_runtime,
            ), patch("epics.caput_many", return_value=[True] * 200) as caput_many_mock, patch(
                "builtins.print"
            ):
                reload_initial_runtime_state()

            state = read_runtime_state(fake_runtime.vm.runtime_json)

        caput_many_mock.assert_called_once()
        pv_names, pv_values = caput_many_mock.call_args.args[:2]
        qm12_index = pv_names.index("IRFEL:VM:AP:QUAD:QM12:K1:ao")

        self.assertEqual(state["lattice"]["QM12"]["K1"], "50.43989105768644")
        self.assertEqual(state["usedline_context"]["mode"], "full")
        self.assertEqual(state["usedline_context"]["line"], "ALL_MAIN")
        self.assertEqual(pv_values[qm12_index], "50.43989105768644")
        self.assertFalse(caput_many_mock.call_args.kwargs["wait"])
        self.assertLessEqual(caput_many_mock.call_args.kwargs["connection_timeout"], 0.5)

    def test_half_compat_parser_no_longer_hardcodes_half_runtime_default_paths(self):
        source = (
            REPO_ROOT / "src/virtual_machine/half_elegant/elegant_parser.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn('dump2json(self, j_file="halflinac.json")', source)
        self.assertNotIn(
            'json2lte_ele(self, lat_f="./elegant/lattice.lte", ele_f="./elegant/one.ele", j_file="halflinac.json")',
            source,
        )
