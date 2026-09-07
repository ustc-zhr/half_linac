from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
PARENT = REPO_ROOT.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

from half_linac.src.shared.machine_profile import resolve_machine_runtime
from half_linac.src.shared.runtime_state import read_runtime_state, write_runtime_state
from half_linac.src.virtual_machine.beam_source import (
    BEAM_SOURCE_CONFIG_KEY,
    BUNCHED_BEAM,
    SDDS_BEAM,
    BeamSourceError,
    apply_beam_source_config,
    apply_preferred_beam_source,
    bootstrap_control,
    load_beam_source_config,
    normalize_beam_source_config,
)
from half_linac.src.virtual_machine.lattice_usedline import (
    reload_initial_runtime_state,
    simplify_usedline_segment,
)


class VmBeamSourceTests(unittest.TestCase):
    def test_machine_bootstrap_defaults_select_expected_beam_source(self):
        half = bootstrap_control(resolve_machine_runtime("half"))
        irfel = bootstrap_control(resolve_machine_runtime("irfel"))

        self.assertIn(BUNCHED_BEAM, half)
        self.assertNotIn(SDDS_BEAM, half)
        self.assertIn(SDDS_BEAM, irfel)
        self.assertNotIn(BUNCHED_BEAM, irfel)
        self.assertEqual(half["run_setup"]["p_central"], "223.4028")
        self.assertEqual(irfel["run_setup"]["p_central_mev"], "36")

    def test_normalize_bunched_beam_validates_and_quotes_distribution(self):
        config = self._bunched_config()
        normalized = normalize_beam_source_config(
            config,
            elegant_dir=REPO_ROOT,
            reference_key="p_central",
        )

        self.assertEqual(normalized[BUNCHED_BEAM]["n_particles_per_bunch"], "1000")
        self.assertEqual(normalized[BUNCHED_BEAM]["distribution_type[0]"], '"gaussian"')
        self.assertEqual(normalized["run_setup"]["random_number_seed"], "12345")

        config[BUNCHED_BEAM]["sigma_dp"] = "-1"
        with self.assertRaisesRegex(BeamSourceError, "sigma_dp"):
            normalize_beam_source_config(
                config,
                elegant_dir=REPO_ROOT,
                reference_key="p_central",
            )

    def test_normalize_sdds_beam_uses_relative_path_and_checks_bounds(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            elegant_dir = Path(tmpdir)
            input_path = elegant_dir / "input beam.sdds"
            input_path.write_bytes(b"SDDS1\n")
            config = self._sdds_config(str(input_path))
            normalized = normalize_beam_source_config(
                config,
                elegant_dir=elegant_dir,
                reference_key="p_central_mev",
            )

            self.assertEqual(normalized[SDDS_BEAM]["input"], '"input beam.sdds"')
            self.assertEqual(normalized[SDDS_BEAM]["input_type"], '"elegant"')
            self.assertEqual(normalized[SDDS_BEAM]["center_arrival_time"], "1")

            config[SDDS_BEAM]["p_lower"] = "50"
            config[SDDS_BEAM]["p_upper"] = "40"
            with self.assertRaisesRegex(BeamSourceError, "p_lower"):
                normalize_beam_source_config(
                    config,
                    elegant_dir=elegant_dir,
                    reference_key="p_central_mev",
                )

    def test_apply_is_atomic_and_preserves_lattice_usedline_and_errors(self):
        runtime = resolve_machine_runtime("half")
        bootstrap = {
            "control": bootstrap_control(runtime),
            "lattice": {"Q1": {"NAME": "Q1", "TYPE": "QUAD", "K1": "1"}},
            "usedline": ["Q1"],
        }
        bootstrap["control"]["error_element"]["amplitude"] = "9e-6"

        with tempfile.TemporaryDirectory() as tmpdir:
            fake_runtime = SimpleNamespace(
                vm=SimpleNamespace(
                    runtime_json=Path(tmpdir) / "runtime.json",
                    bootstrap_lattice=runtime.vm.bootstrap_lattice,
                    bootstrap_ele=runtime.vm.bootstrap_ele,
                    line_name=runtime.vm.line_name,
                )
            )
            write_runtime_state(fake_runtime.vm.runtime_json, bootstrap)
            original_lattice = copy.deepcopy(bootstrap["lattice"])
            original_usedline = list(bootstrap["usedline"])
            config = self._bunched_config()

            apply_beam_source_config(fake_runtime, config)
            state = read_runtime_state(fake_runtime.vm.runtime_json)

        self.assertIn(BEAM_SOURCE_CONFIG_KEY, state)
        self.assertIn(BUNCHED_BEAM, state["control"])
        self.assertNotIn(SDDS_BEAM, state["control"])
        self.assertEqual(state["control"]["error_element"]["amplitude"], "9e-6")
        self.assertEqual(state["lattice"], original_lattice)
        self.assertEqual(state["usedline"], original_usedline)

    def test_preferred_beam_source_is_mutually_exclusive(self):
        control = {
            BUNCHED_BEAM: {"n_particles_per_bunch": "5"},
            SDDS_BEAM: {"input": '"old.sdds"'},
            "run_setup": {"p_central": "1"},
        }
        config = self._sdds_config("new.sdds")
        apply_preferred_beam_source(control, config)

        self.assertNotIn(BUNCHED_BEAM, control)
        self.assertEqual(control[SDDS_BEAM]["input"], "new.sdds")
        self.assertEqual(control["run_setup"]["p_central_mev"], "36")
        self.assertNotIn("p_central", control["run_setup"])

    def test_legacy_segment_without_metadata_displays_bootstrap_preference(self):
        runtime = resolve_machine_runtime("half")
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_path = Path(tmpdir) / "runtime.json"
            write_runtime_state(
                runtime_path,
                {
                    "control": {
                        "run_setup": {"p_central": "42"},
                        SDDS_BEAM: {"input": "pre.bun"},
                    },
                    "usedline_context": {"mode": "segment"},
                },
            )
            fake_runtime = SimpleNamespace(
                vm=SimpleNamespace(
                    runtime_json=runtime_path,
                    bootstrap_ele=runtime.vm.bootstrap_ele,
                )
            )
            config = load_beam_source_config(fake_runtime)

        self.assertEqual(config["selected"], BUNCHED_BEAM)
        self.assertEqual(config["run_setup"]["p_central"], "223.4028")

    def test_simplified_segment_uses_preference_upstream_and_pre_bun_downstream(self):
        runtime = resolve_machine_runtime("half")
        baseline_control = bootstrap_control(runtime)
        preference = self._sdds_config("custom.sdds")
        state = {
            "control": copy.deepcopy(baseline_control),
            "lattice": {
                "Q1": {"NAME": "Q1", "TYPE": "QUAD", "K1": "1"},
                "D1": {"NAME": "D1", "TYPE": "DRIF", "L": "1"},
                "Q2": {"NAME": "Q2", "TYPE": "QUAD", "K1": "2"},
                "ALL_MAIN": {"NAME": "ALL_MAIN", "TYPE": "LINE", "LINE": "Q1,D1,Q2"},
            },
            "usedline": ["Q1", "D1", "Q2"],
            BEAM_SOURCE_CONFIG_KEY: preference,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_path = Path(tmpdir) / "runtime.json"
            write_runtime_state(runtime_path, state)
            fake_runtime = SimpleNamespace(
                profile=runtime.profile,
                vm=SimpleNamespace(
                    runtime_json=runtime_path,
                    bootstrap_lattice=runtime.vm.bootstrap_lattice,
                    bootstrap_ele=runtime.vm.bootstrap_ele,
                    line_name=runtime.vm.line_name,
                ),
            )
            prewatch_states = []

            def capture_prewatch(_seconds):
                prewatch_states.append(read_runtime_state(runtime_path))

            with patch(
                "half_linac.src.virtual_machine.lattice_usedline.resolve_machine_runtime",
                return_value=fake_runtime,
            ), patch(
                "half_linac.src.virtual_machine.lattice_usedline._build_baseline_control",
                return_value=baseline_control,
            ), patch(
                "half_linac.src.virtual_machine.lattice_usedline._read_last_pcentral",
                return_value=42.0,
            ), patch(
                "half_linac.src.virtual_machine.lattice_usedline.time.sleep",
                side_effect=capture_prewatch,
            ):
                simplify_usedline_segment("ALL_MAIN", "D1", "Q2", wait_s=0)
            final = read_runtime_state(runtime_path)

        self.assertEqual(prewatch_states[0]["usedline"], ["Q1", "PREW"])
        self.assertEqual(prewatch_states[0]["control"][SDDS_BEAM]["input"], "custom.sdds")
        self.assertEqual(final["usedline"], ["D1", "Q2"])
        self.assertEqual(final["control"][SDDS_BEAM]["input"], "pre.bun")
        self.assertNotIn(BUNCHED_BEAM, final["control"])
        self.assertEqual(final[BEAM_SOURCE_CONFIG_KEY], preference)

    def test_reload_initial_lattice_clears_saved_beam_preference(self):
        runtime = resolve_machine_runtime("half")
        state = {
            "control": {"run_setup": {}, BUNCHED_BEAM: {}},
            "lattice": {},
            "usedline": [],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_path = Path(tmpdir) / "runtime.json"
            write_runtime_state(runtime_path, {**state, BEAM_SOURCE_CONFIG_KEY: self._bunched_config()})
            fake_runtime = SimpleNamespace(
                profile=runtime.profile,
                vm=SimpleNamespace(
                    runtime_json=runtime_path,
                    bootstrap_lattice=Path(tmpdir) / "lattice_ini.lte",
                    bootstrap_ele=Path(tmpdir) / "one_ini.ele",
                    line_name="ALL_MAIN",
                ),
            )
            fake_parser = SimpleNamespace(build_runtime_state=lambda: copy.deepcopy(state))
            with patch(
                "half_linac.src.virtual_machine.lattice_usedline.resolve_machine_runtime",
                return_value=fake_runtime,
            ), patch(
                "half_linac.src.virtual_machine.lattice_usedline.ElegantParser",
                return_value=fake_parser,
            ), patch(
                "half_linac.src.virtual_machine.lattice_usedline._sync_writable_vm_pvs"
            ):
                reload_initial_runtime_state()
            reloaded = read_runtime_state(runtime_path)

        self.assertNotIn(BEAM_SOURCE_CONFIG_KEY, reloaded)

    @staticmethod
    def _bunched_config():
        return {
            "selected": BUNCHED_BEAM,
            BUNCHED_BEAM: {
                "n_particles_per_bunch": "1000",
                "emit_nx": "1e-6",
                "emit_ny": "2e-6",
                "sigma_s": "1e-3",
                "sigma_dp": "4e-3",
                "distribution_type[0]": "gaussian",
                "distribution_type[1]": "gaussian",
                "distribution_type[2]": "gaussian",
                "distribution_cutoff[0]": "5",
                "distribution_cutoff[1]": "5",
                "distribution_cutoff[2]": "5",
            },
            SDDS_BEAM: {},
            "twiss_output": {"beta_x": "39", "beta_y": "40", "alpha_x": ".7", "alpha_y": "-.7"},
            "run_setup": {"p_central": "223.4", "random_number_seed": "12345"},
        }

    @staticmethod
    def _sdds_config(input_path):
        return {
            "selected": SDDS_BEAM,
            BUNCHED_BEAM: {},
            SDDS_BEAM: {
                "input": input_path,
                "sample_interval": "2",
                "center_arrival_time": True,
                "reuse_bunch": False,
                "p_lower": "10",
                "p_upper": "70",
            },
            "twiss_output": {},
            "run_setup": {"p_central_mev": "36"},
        }


if __name__ == "__main__":
    unittest.main()
