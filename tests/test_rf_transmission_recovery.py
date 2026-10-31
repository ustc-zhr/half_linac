from __future__ import annotations

import json
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from half_linac.src.apps.rf_phase_scan.transmission_recovery import (
    TransmissionRecoveryCallbacks,
    TransmissionRecoveryRunner,
    TransmissionRecoverySettings,
    TransmissionRecoveryStage,
    restore_recovery_snapshot,
)
from half_linac.src.shared.machine_profile import (
    MachineProfileError,
    get_workflow,
    load_profile,
)
from half_linac.src.shared.machine_profile.loader import (
    _validate_basic_app_support,
)


EXPECTED_MAPPING = [
    ("LLRF02", "BPM08"),
    ("LLRF03", "BPM12"),
    ("LLRF04", "BPM12"),
    ("LLRF05", "BPM13"),
    ("LLRF06", "BPM13"),
    ("LLRF07", "BPM14"),
    ("LLRF08", "BPM14"),
    ("LLRF09", "BPM15"),
    ("LLRF10", "BPM15"),
    ("LLRF11", "BPM16"),
    ("LLRF12", "BPM16"),
    ("LLRF13", "BPM17"),
    ("LLRF14", "BPM17"),
    ("LLRF15", "BPM18"),
    ("LLRF16", "BPM18"),
    ("LLRF17", "BPM19"),
    ("LLRF18", "BPM19"),
    ("LLRF19", "BPM20"),
    ("LLRF20", "BPM20"),
]


class TransmissionRecoveryProfileTests(unittest.TestCase):
    def _profile_with(self, workflow):
        profile = deepcopy(load_profile("half"))
        profile.workflows["rf_phase_scan"] = workflow
        return profile

    def _validate(self, workflow):
        _validate_basic_app_support(
            self._profile_with(workflow),
            "rf_phase_scan",
            "real",
        )

    def test_half_mapping_is_complete_and_excludes_first_version_omissions(self):
        profile = load_profile("half")
        workflow = get_workflow(profile, "rf_phase_scan")
        recovery = workflow["transmission_recovery"]
        self.assertEqual(recovery["phase_scan"]["half_range_deg"], 90)
        self.assertEqual(recovery["phase_scan"]["points"], 13)
        mapping = [
            (stage["rf_element"], stage["target_bpm"])
            for stage in recovery["stages"]
        ]

        self.assertEqual(mapping, EXPECTED_MAPPING)
        self.assertNotIn("LLRF01", {rf for rf, _bpm in mapping})
        self.assertTrue({"BPM09", "BPM10", "BPM11", "BPM21", "BPM22"}.isdisjoint(
            {bpm for _rf, bpm in mapping}
        ))
        self.assertEqual(mapping.count(("LLRF03", "BPM12")), 1)
        self.assertEqual(
            sum(bpm == "BPM12" for _rf, bpm in mapping),
            2,
        )
        self._validate(deepcopy(workflow))

    def test_recovery_block_is_optional(self):
        workflow = deepcopy(get_workflow(load_profile("half"), "rf_phase_scan"))
        workflow.pop("transmission_recovery")
        self._validate(workflow)

    def test_profile_rejects_invalid_stage_references_and_order(self):
        source = get_workflow(load_profile("half"), "rf_phase_scan")
        cases = []

        workflow = deepcopy(source)
        workflow["transmission_recovery"]["stages"][0]["rf_element"] = "LLRF99"
        cases.append(("unknown RF", workflow))

        workflow = deepcopy(source)
        workflow["transmission_recovery"]["stages"][0]["target_bpm"] = "BPM99"
        cases.append(("unknown BPM", workflow))

        workflow = deepcopy(source)
        workflow["transmission_recovery"]["stages"][1]["rf_element"] = "LLRF02"
        cases.append(("duplicate RF", workflow))

        workflow = deepcopy(source)
        stages = workflow["transmission_recovery"]["stages"]
        stages[0], stages[1] = stages[1], stages[0]
        cases.append(("wrong order", workflow))

        workflow = deepcopy(source)
        workflow["transmission_recovery"]["stages"][0]["target_bpm"] = "LLRF03"
        cases.append(("non-BPM target", workflow))

        for label, workflow in cases:
            with self.subTest(label=label):
                with self.assertRaises(MachineProfileError):
                    self._validate(workflow)

    def test_profile_rejects_missing_channels_and_invalid_scan_values(self):
        source = get_workflow(load_profile("half"), "rf_phase_scan")
        cases = []

        workflow = deepcopy(source)
        workflow["transmission_recovery"]["measurement"]["channel"] = "missing"
        cases.append(("missing BPM channel", workflow))

        workflow = deepcopy(source)
        verification = workflow["transmission_recovery"]["phase_scan"][
            "readback_verification"
        ]
        verification["channel"] = "missing"
        cases.append(("missing RF readback", workflow))

        for key, value in (
            ("half_range_deg", 0),
            ("half_range_deg", 181),
            ("points", 4),
            ("points", 1),
            ("settle_time_s", -1),
        ):
            workflow = deepcopy(source)
            workflow["transmission_recovery"]["phase_scan"][key] = value
            cases.append((f"invalid {key}={value}", workflow))

        for key, value in (
            ("samples_per_point", 0),
            ("min_valid_samples", 0),
            ("sample_interval_s", -1),
        ):
            workflow = deepcopy(source)
            workflow["transmission_recovery"]["measurement"][key] = value
            cases.append((f"invalid {key}={value}", workflow))

        workflow = deepcopy(source)
        workflow["transmission_recovery"]["measurement"]["samples_per_point"] = 2
        workflow["transmission_recovery"]["measurement"]["min_valid_samples"] = 3
        cases.append(("minimum exceeds samples", workflow))

        for label, workflow in cases:
            with self.subTest(label=label):
                with self.assertRaises(MachineProfileError):
                    self._validate(workflow)


class _FakeLock:
    def __init__(self):
        self.acquires = 0
        self.releases = 0

    def acquire(self):
        self.acquires += 1

    def release(self):
        self.releases += 1


class _FakeMachine:
    def __init__(self, initial_phases, samples=None):
        self.values = {}
        for phase_pv, initial in initial_phases.items():
            self.values[phase_pv] = float(initial)
            self.values[phase_pv.replace("P", "R", 1)] = float(initial)
        self.samples = samples or {}
        self.active_phase_pv = None
        self.sample_index = 0
        self.writes = []
        self.fail_once = set()
        self.stuck_readback_once = set()

    @staticmethod
    def _key(value):
        return round(float(value), 9)

    def read(self, pv):
        if pv in self.values:
            return self.values[pv]
        if self.active_phase_pv is None:
            return 1.0
        phase = self._key(self.values[self.active_phase_pv])
        values = self.samples.get(self.active_phase_pv, {}).get(phase, [1.0])
        value = values[min(self.sample_index, len(values) - 1)]
        self.sample_index += 1
        return value

    def write(self, pv, value, **_kwargs):
        command = self._key(value)
        self.writes.append((pv, command))
        failure = (pv, command)
        if failure in self.fail_once:
            self.fail_once.remove(failure)
            return False
        self.values[pv] = command
        self.active_phase_pv = pv
        self.sample_index = 0
        if failure in self.stuck_readback_once:
            self.stuck_readback_once.remove(failure)
        else:
            self.values[pv.replace("P", "R", 1)] = command
        return True


def _settings(points=5, half_range=20.0):
    return TransmissionRecoverySettings(
        half_range_deg=half_range,
        points=points,
        settle_time_s=0.0,
        samples_per_point=3,
        sample_interval_s=0.0,
        min_valid_samples=2,
        readback_tolerance_deg=0.1,
        readback_timeout_s=0.0,
        readback_poll_interval_s=0.001,
    )


def _stage(index, target="BPM01", signal="SUM"):
    number = index + 1
    return TransmissionRecoveryStage(
        index=index,
        rf_element=f"LLRF{number:02d}",
        target_bpm=target,
        phase_pv=f"P{number}",
        phase_readback_pv=f"R{number}",
        signal_pv=signal,
    )


class TransmissionRecoveryRunnerTests(unittest.TestCase):
    def _run(
        self,
        directory,
        stages,
        machine,
        *,
        candidates=None,
        settings=None,
        callbacks=None,
    ):
        lock = _FakeLock()
        context = SimpleNamespace(
            machine=SimpleNamespace(id="half"),
            control_backend=SimpleNamespace(name="real"),
        )
        runner = TransmissionRecoveryRunner(
            context=context,
            candidates=tuple(candidates or stages),
            stages=tuple(stages),
            settings=settings or _settings(),
            directory=Path(directory),
            callbacks=callbacks,
            configuration_snapshot={"measurement": {"channel": "sum"}},
            read=machine.read,
            write=machine.write,
            lock_factory=lambda *_args: lock,
            sleeper=lambda _seconds: None,
        )
        with patch(
            "half_linac.src.apps.rf_phase_scan.transmission_recovery."
            "require_workflow_write_allowed"
        ):
            result = runner.run()
        return runner, result, lock

    def test_scan_wraps_phase_records_median_and_chooses_closest_tied_peak(self):
        stage = _stage(0)
        machine = _FakeMachine(
            {"P1": 170.0},
            {
                "P1": {
                    170.0: [1.0, 100.0, 3.0],
                    -180.0: [5.0, 7.0, 6.0],
                    -170.0: [7.0, 7.0, 7.0],
                    160.0: [6.0, 8.0, 7.0],
                    150.0: [1.0, 1.0, 1.0],
                }
            },
        )
        with tempfile.TemporaryDirectory() as temp:
            _runner, result, lock = self._run(temp, (stage,), machine)
            stored = json.loads((Path(temp) / "run.json").read_text())

        self.assertEqual(result["status"], "completed")
        item = result["items"][0]
        self.assertEqual(item["peak_offset_deg"], -10.0)
        self.assertEqual(item["applied_phase_deg"], 160.0)
        self.assertEqual(item["points"][1]["samples"], [5.0, 7.0, 6.0])
        self.assertEqual(item["points"][1]["median_sum"], 6.0)
        self.assertEqual(
            [point["offset_deg"] for point in item["points"]],
            [0.0, 10.0, 20.0, -10.0, -20.0],
        )
        self.assertIn(("P1", -180.0), machine.writes)
        self.assertEqual(machine.values["P1"], 160.0)
        self.assertEqual(stored["schema_version"], "rf_transmission_recovery_v1")
        self.assertEqual(stored["configuration"]["selected_stages"][0][
            "target_bpm"
        ], "BPM01")
        self.assertEqual(
            stored["configuration"]["workflow_snapshot"]["measurement"][
                "channel"
            ], "sum"
        )
        self.assertEqual(lock.acquires, 1)
        self.assertEqual(lock.releases, 1)

    def test_selected_pair_sharing_one_bpm_executes_in_configured_order(self):
        stages = (_stage(0, "BPM12"), _stage(1, "BPM12"))
        machine = _FakeMachine(
            {"P1": 0.0, "P2": 0.0},
            {
                "P1": {0.0: [1], 10.0: [4], -10.0: [2]},
                "P2": {0.0: [1], 10.0: [2], -10.0: [5]},
            },
        )
        with tempfile.TemporaryDirectory() as temp:
            _runner, result, _lock = self._run(
                temp,
                stages,
                machine,
                settings=_settings(points=3, half_range=10),
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            [item["rf_element"] for item in result["items"]],
            ["LLRF01", "LLRF02"],
        )
        self.assertEqual(
            [item["target_bpm"] for item in result["items"]],
            ["BPM12", "BPM12"],
        )
        self.assertEqual(machine.values["P1"], 10.0)
        self.assertEqual(machine.values["P2"], -10.0)

    def test_arbitrary_selection_keeps_candidate_order_and_rejects_reordering(self):
        candidates = (_stage(0), _stage(1), _stage(2))
        selected = (candidates[0], candidates[2])
        machine = _FakeMachine(
            {"P1": 0.0, "P3": 0.0},
            {
                "P1": {0.0: [1], 10.0: [3], -10.0: [2]},
                "P3": {0.0: [1], 10.0: [2], -10.0: [3]},
            },
        )
        with tempfile.TemporaryDirectory() as temp:
            _runner, result, _lock = self._run(
                temp,
                selected,
                machine,
                candidates=candidates,
                settings=_settings(points=3, half_range=10),
            )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            [item["rf_element"] for item in result["items"]],
            ["LLRF01", "LLRF03"],
        )

        reversed_machine = _FakeMachine({"P1": 0.0, "P3": 0.0})
        with tempfile.TemporaryDirectory() as temp:
            _runner, result, _lock = self._run(
                temp,
                tuple(reversed(selected)),
                reversed_machine,
                candidates=candidates,
                settings=_settings(points=3, half_range=10),
            )
        self.assertEqual(result["status"], "failed")
        self.assertIn("configured order", result["error"])

    def test_stop_restores_current_stage_and_skips_following_stage(self):
        stages = (_stage(0), _stage(1))
        machine = _FakeMachine(
            {"P1": 15.0, "P2": 25.0},
            {"P1": {15.0: [1], 25.0: [3], 5.0: [2]}},
        )
        holder = {}

        def stop_after_first_point(_index, _point):
            holder["runner"].stop()

        callbacks = TransmissionRecoveryCallbacks(point=stop_after_first_point)
        with tempfile.TemporaryDirectory() as temp:
            lock = _FakeLock()
            context = SimpleNamespace(
                machine=SimpleNamespace(id="half"),
                control_backend=SimpleNamespace(name="real"),
            )
            runner = TransmissionRecoveryRunner(
                context=context,
                candidates=stages,
                stages=stages,
                settings=_settings(points=3, half_range=10),
                directory=Path(temp),
                callbacks=callbacks,
                read=machine.read,
                write=machine.write,
                lock_factory=lambda *_args: lock,
                sleeper=lambda _seconds: None,
            )
            holder["runner"] = runner
            with patch(
                "half_linac.src.apps.rf_phase_scan.transmission_recovery."
                "require_workflow_write_allowed"
            ):
                result = runner.run()

        self.assertEqual(result["status"], "stopped")
        self.assertEqual(machine.values["P1"], 15.0)
        self.assertEqual(machine.values["P2"], 25.0)
        self.assertEqual(result["items"][0]["status"], "stopped")
        self.assertEqual(result["items"][1]["status"], "not_run")

    def test_flat_signal_failure_restores_current_and_keeps_prior_success(self):
        stages = (_stage(0), _stage(1), _stage(2))
        machine = _FakeMachine(
            {"P1": 0.0, "P2": 20.0, "P3": 30.0},
            {
                "P1": {0.0: [1], 10.0: [5], -10.0: [2]},
                "P2": {20.0: [4], 30.0: [4], 10.0: [4]},
            },
        )
        with tempfile.TemporaryDirectory() as temp:
            _runner, result, _lock = self._run(
                temp,
                stages,
                machine,
                settings=_settings(points=3, half_range=10),
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["items"][0]["status"], "applied")
        self.assertEqual(result["items"][1]["status"], "failed")
        self.assertIn("no measurable phase response", result["error"])
        self.assertEqual(machine.values["P1"], 10.0)
        self.assertEqual(machine.values["P2"], 20.0)
        self.assertEqual(machine.values["P3"], 30.0)
        self.assertEqual(result["items"][2]["status"], "not_run")

    def test_all_invalid_samples_fail_after_scan_and_restore_initial_phase(self):
        stage = _stage(0)
        machine = _FakeMachine(
            {"P1": 0.0},
            {
                "P1": {
                    0.0: [None, None, None],
                    10.0: [None, None, None],
                    -10.0: [None, None, None],
                }
            },
        )
        with tempfile.TemporaryDirectory() as temp:
            _runner, result, _lock = self._run(
                temp,
                (stage,),
                machine,
                settings=_settings(points=3, half_range=10),
            )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(machine.values["P1"], 0.0)
        self.assertTrue(all(
            point["median_sum"] is None
            for point in result["items"][0]["points"]
        ))

    def test_write_and_readback_failures_restore_initial_phase(self):
        for mode in ("write", "readback"):
            with self.subTest(mode=mode):
                stage = _stage(0)
                machine = _FakeMachine(
                    {"P1": 0.0},
                    {"P1": {0.0: [1], 10.0: [3], -10.0: [2]}},
                )
                failure = ("P1", 10.0)
                if mode == "write":
                    machine.fail_once.add(failure)
                else:
                    machine.stuck_readback_once.add(failure)
                with tempfile.TemporaryDirectory() as temp:
                    _runner, result, _lock = self._run(
                        temp,
                        (stage,),
                        machine,
                        settings=_settings(points=3, half_range=10),
                    )
                self.assertEqual(result["status"], "failed")
                self.assertEqual(machine.values["P1"], 0.0)
                self.assertEqual(result["items"][0]["status"], "failed")

    def test_snapshot_restore_refuses_external_drift_then_restores_clean_state(self):
        stage = _stage(0)
        machine = _FakeMachine(
            {"P1": 5.0},
            {"P1": {5.0: [1], 15.0: [4], -5.0: [2]}},
        )
        with tempfile.TemporaryDirectory() as temp:
            _runner, result, _lock = self._run(
                temp,
                (stage,),
                machine,
                settings=_settings(points=3, half_range=10),
            )

        context = SimpleNamespace(
            machine=SimpleNamespace(id="half"),
            control_backend=SimpleNamespace(name="real"),
        )
        restore_lock = _FakeLock()
        machine.values["P1"] = 20.0
        machine.values["R1"] = 20.0
        with patch(
            "half_linac.src.apps.rf_phase_scan.transmission_recovery."
            "require_workflow_write_allowed"
        ):
            with self.assertRaisesRegex(RuntimeError, "refused"):
                restore_recovery_snapshot(
                    context=context,
                    stages=(stage,),
                    settings=_settings(points=3, half_range=10),
                    snapshot=result["initial_snapshot"],
                    expected_phases=result["expected_phases_deg"],
                    read=machine.read,
                    write=machine.write,
                    lock_factory=lambda *_args: restore_lock,
                )
        self.assertEqual(machine.values["P1"], 20.0)

        expected = result["expected_phases_deg"]["LLRF01"]
        machine.values["P1"] = expected
        machine.values["R1"] = expected
        with patch(
            "half_linac.src.apps.rf_phase_scan.transmission_recovery."
            "require_workflow_write_allowed"
        ):
            restored = restore_recovery_snapshot(
                context=context,
                stages=(stage,),
                settings=_settings(points=3, half_range=10),
                snapshot=result["initial_snapshot"],
                expected_phases=result["expected_phases_deg"],
                read=machine.read,
                write=machine.write,
                lock_factory=lambda *_args: restore_lock,
            )
        self.assertEqual(restored, {"LLRF01": 5.0})
        self.assertEqual(machine.values["P1"], 5.0)


class TransmissionRecoveryGuiTests(unittest.TestCase):
    def test_window_pages_defaults_controls_and_live_updates(self):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        os.environ["HALF_LINAC_MACHINE_ID"] = "half"
        os.environ["HALF_LINAC_CONTROL_BACKEND"] = "vm"
        from PyQt5.QtCore import Qt
        from PyQt5.QtWidgets import QApplication
        from half_linac.src.apps.rf_phase_scan.main import RFPhaseScanWindow

        app = QApplication.instance() or QApplication([])
        window = RFPhaseScanWindow()
        page = window.recovery_page
        window.resize(1500, 1040)
        window.show()
        app.processEvents()

        self.assertGreaterEqual(page.table.width(), 710)
        self.assertGreaterEqual(page.plot.width(), 500)
        self.assertEqual(page.table.horizontalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
        self.assertFalse(page.plot.axes.axison)

        self.assertEqual(window.windowTitle(), "HALF Linac RF Phase Tuning")
        self.assertEqual(window.mode_tabs.count(), 2)
        self.assertEqual(window.mode_tabs.tabText(0).strip(), "Transmission Recovery")
        self.assertEqual(window.mode_tabs.tabText(1), "Crest && Gain")
        self.assertEqual(window.mode_tabs.currentIndex(), 0)
        self.assertEqual(page.table.rowCount(), len(EXPECTED_MAPPING))
        self.assertEqual(len(page.stage_checkboxes), len(EXPECTED_MAPPING))
        self.assertTrue(all(
            selector.isChecked() and selector.property("stageSelector")
            for selector in page.stage_checkboxes
        ))
        page._select_all(False)
        self.assertFalse(any(selector.isChecked() for selector in page.stage_checkboxes))
        page._select_all(True)
        self.assertTrue(all(selector.isChecked() for selector in page.stage_checkboxes))

        page.worker = SimpleNamespace()
        page._set_running(True)
        self.assertFalse(page.table.isEnabled())
        self.assertFalse(page.points.isEnabled())
        self.assertFalse(window.mode_tabs.isTabEnabled(1))
        page.worker = None
        page._set_running(False)
        self.assertTrue(page.table.isEnabled())
        self.assertTrue(window.mode_tabs.isTabEnabled(1))

        page.selected_rows = [0]
        page._item(
            0,
            {
                "rf_element": "LLRF02",
                "target_bpm": "BPM08",
                "initial_phase_deg": 1.0,
                "peak_phase_deg": 6.0,
                "peak_sum": 12.5,
                "applied_phase_deg": 6.0,
                "status": "scanning",
            },
        )
        page._point(
            0,
            {"offset_deg": 5.0, "median_sum": 12.5},
        )
        self.assertEqual(page.table.item(0, 5).text(), "12.500")
        self.assertEqual(len(page.plot.axes.lines), 1)

        page.last_stages = (page.candidates[0],)
        page._done({
            "status": "completed",
            "error": "",
            "json_path": "/tmp/run.json",
            "initial_snapshot": {"devices": {"LLRF02": {}}},
            "expected_phases_deg": {"LLRF02": 6.0},
        })
        self.assertTrue(page.restore_button.isEnabled())

        window.close()
        app.processEvents()


if __name__ == "__main__":
    unittest.main()
