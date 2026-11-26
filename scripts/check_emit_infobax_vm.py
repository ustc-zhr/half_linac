#!/usr/bin/env python3
"""Bounded elegant/InfoBAX smoke test with in-memory PV I/O and no IOC access."""

from __future__ import annotations

import argparse
import copy
from contextlib import redirect_stdout
from dataclasses import replace
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

from half_linac.src.apps.emit_measure.infobax_session import InfoBAXSession, InfoBAXSessionConfig
from half_linac.src.apps.emit_measure.infobax_vm import (
    FiniteWorkpointTable, HalfVmInfoBAXMachine, MachineVariable, build_half_vm_infobax_pool,
)
from half_linac.src.optimization.infobax import InfoBAXCore, InfoBAXConfig
from half_linac.src.shared.elegant_backend import ElegantParser, VmPublisher, build_vm_publish_plan
from half_linac.src.shared.machine_profile import load_app_context, load_profile, resolve_write_target
from half_linac.src.shared.runtime_state import read_runtime_state, write_runtime_state
from half_linac.src.virtual_machine.common.start_VM import _update_vm_outputs
from half_linac.src.virtual_machine.workbench_data import collect_results, input_version, observation_dir


class OfflineVm(VmPublisher):
    """Run the real VM calculation/publication path into an in-memory PV store."""

    def __init__(self, context, root):
        super().__init__(wait_for_image_completion=True)
        self.root = root
        self.runtime_json = root / "machine.json"
        self.parser = ElegantParser(root / "source.lte", root / "source.ele", "SMOKE",
                                    runtime_json_path=self.runtime_json, elegant_dir=root)
        write_runtime_state(self.runtime_json, self.parser.build_runtime_state())
        plan = build_vm_publish_plan(load_profile("half"))
        self.plan = replace(plan, bpm_specs=(), watch_scalar_specs=(), watch_image_specs=tuple(
            spec for spec in plan.watch_image_specs
            if spec.target_element_id == "PRF07" and spec.logical_channel == "image"
        ))
        self.channels = {
            resolve_write_target(context, element, quantity="K1", unit="1/m^2", mode="vm").pv_name: element
            for element in ("QT01", "QT02")
        }
        self.images = {}
        self.calculations = {}

    def get(self, pv):
        if pv in self.channels:
            return float(read_runtime_state(self.runtime_json)["lattice"][self.channels[pv]]["K1"])
        return self.images.get(pv)

    def put(self, pv, value):
        state = read_runtime_state(self.runtime_json)
        state["lattice"][self.channels[pv]]["K1"] = str(float(value))
        write_runtime_state(self.runtime_json, state)
        return True

    def _publish_one_best_effort(self, *, label, pv_name, value, wait=False):
        if not wait:
            raise RuntimeError("smoke test requires synchronous image publication")
        self.images[pv_name] = value.copy()
        return True

    def sleep(self, seconds):
        state = read_runtime_state(self.runtime_json)
        version = input_version(state)
        if version in self.calculations:
            time.sleep(seconds)
            return
        directory = observation_dir(self.runtime_json)
        original = copy.deepcopy(state)
        state["control"]["run_setup"]["sigma"] = "%s.sig"
        frozen = directory / "input.json"
        write_runtime_state(frozen, state)
        self.parser.lattice = state["lattice"]
        started = time.time()
        publication = _update_vm_outputs(self.parser, self, self.plan, self.root, frozen)
        result = collect_results(state, self.root, newer_than=started)
        number = len(self.calculations) + 1
        result.update(input_state=original, input_version=version, session="offline-smoke", calculation=number)
        write_runtime_state(directory / "result.json", result)
        write_runtime_state(directory / "status.json", dict(
            phase="Ready", session="offline-smoke", calculation=number,
            input_version=version, result_version=version, publication=publication,
        ))
        self.calculations[version] = original


def prepare_workspace(root, *, sextupole_k2=None, particles=10000):
    context = load_app_context("emit_measure", machine_id="half", control_backend="vm")
    # Compact test optics bearing machine-native IDs; no repository runtime files
    # or archived beams are used. Linear optics supplies an engineering check,
    # not a claim that changing an upstream quad optimizes emittance.
    (root / "source.lte").write_text(
        'QT01: QUAD,L=0.15,K1=0.1\nD1: DRIF,L=1\nQT02: QUAD,L=0.15,K1=-0.2\n'
        'D2: DRIF,L=2\nPRF07: WATCH,FILENAME="PRF07.out",MODE="coord",DISABLE=0\n'
        + (f'S1: SEXT,L=0.2,K2={float(sextupole_k2)}\n' if sextupole_k2 is not None else '')
        + ('SMOKE: LINE=(QT01,D1,S1,QT02,D2,PRF07)\n' if sextupole_k2 is not None
           else 'SMOKE: LINE=(QT01,D1,QT02,D2,PRF07)\n')
    )
    (root / "source.ele").write_text(
        '&run_setup\n lattice=source.lte,\n use_beamline=SMOKE,\n p_central=441.31,\n'
        ' random_number_seed=12345,\n centroid="%s.cen",\n &end\n'
        '&run_control\n n_steps=1,\n &end\n'
        '&twiss_output\n filename="%s.twi",\n matched=0,\n beta_x=10,\n beta_y=10,\n &end\n'
        f'&bunched_beam\n n_particles_per_bunch={int(particles)},\n emit_x=1e-8,\n emit_y=1e-8,\n'
        ' beta_x=10,\n beta_y=10,\n sigma_dp=0.001,\n sigma_s=0.001,\n &end\n'
        '&track &end\n'
    )
    runner = root / "one"
    runner.write_text('#!/usr/bin/env bash\nexec elegant one.ele\n')
    runner.chmod(0o755)
    config = dict(context.model_backend.config)
    config.update(source_lattice=str(root / "source.lte"), optics_working_dir=str(root / "optics"))
    for key, name in (("optics_lte", "optics.lte"), ("optics_ele", "optics.ele"),
                      ("optics_json", "optics.json"), ("optics_mat", "optics.mat")):
        config[key] = str(root / "optics" / name)
    context = replace(context, model_backend=replace(context.model_backend, config=config))
    return context


def check(root, output):
    context = prepare_workspace(root)
    table = FiniteWorkpointTable(("low", "center", "high"), (MachineVariable("QT01"),), [[-1], [0], [1]])
    pool = build_half_vm_infobax_pool(
        context, table, scan_quad="QT02", screen="PRF07", plane="x", model_line="SMOKE",
        scan_values=(-1.0, 0.0, 1.0), energy_mev=225, observation_noise_std_mm2=0.002,
    )
    vm = OfflineVm(context, root)
    machine = HalfVmInfoBAXMachine(
        context, table, scan_quad="QT02", screen="PRF07", plane="x",
        runtime_json=vm.runtime_json, get=vm.get, put=vm.put, sleeper=vm.sleep,
    )
    initial = machine.snapshot()
    holder = {}

    def progress(payload):
        if payload["observation_count"] == 3 and payload["record"] is not None:
            holder["session"].request_pause()

    session = InfoBAXSession(InfoBAXCore(pool, InfoBAXConfig(seed=0)), machine,
                            output / "session", InfoBAXSessionConfig(max_observations=7), progress=progress)
    holder["session"] = session
    session.run()
    assert session.status == "paused" and len(session.core.observations) == 3
    assert machine.snapshot() == initial
    resumed = InfoBAXSession.from_run_dir(pool, machine, output / "session")
    resumed.run()
    assert resumed.status == "completed" and len(resumed.core.observations) == 7
    assert machine.snapshot() == initial
    for record in resumed.records:
        metadata = record["measurement"]["metadata"]
        state = vm.calculations[metadata["vm_input_version"]]
        assert float(state["lattice"]["QT01"]["K1"]) == float(table.values[record["candidate_index"], 0])
        assert float(state["lattice"]["QT02"]["K1"]) == record["scan_value"]
    return dict(status="passed", observations=7, active_queries=1, paused_and_resumed=True,
                restored=True, input_versions_matched=True, ioc_access=False,
                optics="compact linear smoke lattice", pool_fingerprint=pool.fingerprint)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    with TemporaryDirectory(prefix="infobax-elegant-") as temporary, (
        args.output_dir / "elegant.log"
    ).open("w") as log, redirect_stdout(log):
        result = check(Path(temporary), args.output_dir)
    (args.output_dir / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
