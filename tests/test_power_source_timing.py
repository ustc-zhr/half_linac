from __future__ import annotations

import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QAbstractSpinBox

import numpy as np

from half_linac.src.apps.power_source_timing.epics_client import (
    BatchWriteWorker,
    WaveformMonitor,
    WaveformSnapshot,
)
from half_linac.src.apps.power_source_timing.main import TimingWindow
from half_linac.src.apps.power_source_timing.model import (
    DEVICES,
    WAVEFORM_DEVICES,
    CoalescingWriteQueue,
    TimingValues,
)
from half_linac.src.apps.power_source_timing.profile_runtime import (
    TimingGroup,
    load_timing_runtime,
)
from half_linac.src.apps.power_source_timing.waveform_view import TRACE_COLORS
from half_linac.src.apps.power_source_timing.waveform import analyze_waveform
from half_linac.src.shared.machine_profile import MachineProfileError, load_app_context


class PowerSourceTimingConfigTests(unittest.TestCase):
    def test_half_real_profile_has_klystron_and_prebuncher_timing_groups(self):
        with patch.dict(
            os.environ,
            {
                "HALF_LINAC_MACHINE_ID": "half",
                "HALF_LINAC_CONTROL_BACKEND": "real",
            },
        ):
            runtime = load_timing_runtime()

        self.assertEqual(len(runtime.groups), 21)
        self.assertEqual(runtime.groups[0].element_id, "PREBUNCHER")
        self.assertEqual(runtime.waveform_alignment.sample_rate_mhz, 105.78)
        self.assertFalse(runtime.waveform_alignment.shared_time_origin)
        groups = {group.element_id: group for group in runtime.groups}
        for index in range(1, 21):
            number = f"{index:02d}"
            group = groups[f"KLY{number}"]
            self.assertEqual(group.devices, DEVICES)
            for device in DEVICES:
                prefix = device.upper()
                fields = group.channels[device]
                self.assertEqual(
                    fields["delay_set"], f"IN:TM:{prefix}{number}:delay:ao"
                )
                self.assertEqual(
                    fields["delay_readback"], f"IN:TM:{prefix}{number}:delay:ai"
                )
                self.assertEqual(
                    fields["enable"], f"IN:TM:{prefix}{number}:enable:bo"
                )
                self.assertEqual(
                    fields["width_set"], f"IN:TM:{prefix}{number}:width:ao"
                )
                self.assertEqual(
                    fields["width_readback"], f"IN:TM:{prefix}{number}:width:ai"
                )
            self.assertEqual(
                group.waveforms,
                {
                    "llrf": f"IN:MW:LLRF{number}:CH8_WFAMP",
                    "ssa": f"IN:MW:LLRF{number}:CH1_WFAMP",
                    "kly": f"IN:MW:LLRF{number}:CH2_WFAMP",
                },
            )
            self.assertNotIn("hv", group.waveforms)

        prebuncher = groups["PREBUNCHER"]
        self.assertEqual(prebuncher.devices, ("llrf", "ssa"))
        self.assertEqual(
            prebuncher.channels,
            {
                "llrf": {
                    "delay_set": "IN:TM:LLRF00:delay:ao",
                    "delay_readback": "IN:TM:LLRF00:delay:ai",
                    "enable": "IN:TM:LLRF00:enable:bo",
                    "width_set": "IN:TM:LLRF00:width:ao",
                    "width_readback": "IN:TM:LLRF00:width:ai",
                },
                "ssa": {
                    "delay_set": "IN:TM:SSA00:delay:ao",
                    "delay_readback": "IN:TM:SSA00:delay:ai",
                    "enable": "IN:TM:SSA00:enable:bo",
                    "width_set": "IN:TM:SSA00:width:ao",
                    "width_readback": "IN:TM:SSA00:width:ai",
                },
            },
        )
        self.assertEqual(
            prebuncher.waveforms,
            {
                "llrf": "IN:MW:LLRFPB:CH8_WFAMP",
                "ssa": "IN:MW:LLRFPB:CH1_WFAMP",
                "pickup": "IN:MW:LLRFPB:CH2_WFAMP",
            },
        )

    def test_timing_channels_have_no_vm_mapping(self):
        with patch.dict(
            os.environ,
            {
                "HALF_LINAC_MACHINE_ID": "half",
                "HALF_LINAC_CONTROL_BACKEND": "real",
            },
        ):
            context = load_app_context("power_source_timing")
        for element in context.profile.elements:
            if "power_source_timing" not in element.tags:
                continue
            for logical, backends in element.channels.items():
                if logical.startswith(tuple(f"{device}_" for device in DEVICES)):
                    self.assertEqual(set(backends), {"real"})

    def test_vm_backend_is_rejected(self):
        with patch.dict(
            os.environ,
            {
                "HALF_LINAC_MACHINE_ID": "half",
                "HALF_LINAC_CONTROL_BACKEND": "vm",
            },
        ):
            with self.assertRaisesRegex(MachineProfileError, "does not support backend"):
                load_app_context("power_source_timing")

    def test_runtime_uses_profile_machine_backend_and_element_ids(self):
        backend = "vm"
        channels = {
            f"{device}_{field}": {backend: f"SITE:{device.upper()}:{field}"}
            for device in DEVICES
            for field in (
                "delay_set",
                "delay_readback",
                "enable",
                "width_set",
                "width_readback",
            )
        }
        workflow = {
            "element_tag": "power_source_timing",
            "default_element": "RF-STATION-A",
            "minimum_us": 0.0,
            "readback_tolerance_us": 0.001,
            "delay_step_us": 0.1,
            "width_step_us": 0.1,
            "step_choices_us": [0.01, 0.1],
            "button_repeat_delay_ms": 300,
            "button_repeat_interval_ms": 150,
            "write_control": {backend: "allowed"},
        }
        context = SimpleNamespace(
            machine=SimpleNamespace(id="site-a", display_name="Site A"),
            control_backend=SimpleNamespace(name=backend),
            profile=SimpleNamespace(
                workflows={"power_source_timing": workflow},
                elements=(
                    SimpleNamespace(
                        id="RF-STATION-A",
                        display_name="RF Station A",
                        tags=("power_source_timing",),
                        channels=channels,
                    ),
                ),
            ),
        )
        with patch(
            "half_linac.src.apps.power_source_timing.profile_runtime.load_app_context",
            return_value=context,
        ), patch(
            "half_linac.src.apps.power_source_timing.profile_runtime.require_workflow_write_allowed"
        ):
            runtime = load_timing_runtime()

        self.assertEqual(runtime.context.machine.id, "site-a")
        self.assertEqual(runtime.groups[0].element_id, "RF-STATION-A")
        self.assertEqual(runtime.groups[0].devices, DEVICES)
        self.assertEqual(runtime.groups[0].waveforms, {})
        self.assertEqual(
            runtime.groups[0].channels["hv"]["delay_set"],
            "SITE:HV:delay_set",
        )


class TimingValuesTests(unittest.TestCase):
    def setUp(self):
        self.values = TimingValues(minimum_us=0.0)
        for index, device in enumerate(DEVICES):
            self.values.sync_setpoint(
                device, "delay", 10.0 + index, follow_target=True
            )
            self.values.sync_setpoint(
                device, "width", 2.0 + index, follow_target=True
            )
            self.values.set_enabled(device, False)

    def test_group_shift_changes_only_delays(self):
        before_widths = {
            device: self.values.target[(device, "width")] for device in DEVICES
        }
        changed = self.values.shift_group_delay(0.1)
        self.assertEqual(set(changed), {(device, "delay") for device in DEVICES})
        for index, device in enumerate(DEVICES):
            self.assertAlmostEqual(self.values.target[(device, "delay")], 10.1 + index)
            self.assertEqual(self.values.target[(device, "width")], before_widths[device])

    def test_single_adjustments_do_not_affect_other_values(self):
        before = dict(self.values.target)
        changed = self.values.shift_one("ssa", "width", -0.25)
        self.assertEqual(changed, {("ssa", "width"): 3.75})
        for key, value in before.items():
            if key != ("ssa", "width"):
                self.assertEqual(self.values.target[key], value)

    def test_disabled_channel_allows_delay_preset(self):
        self.assertFalse(self.values.enabled["hv"])
        self.values.request_value("hv", "delay", 8.5)
        self.assertEqual(self.values.target[("hv", "delay")], 8.5)

    def test_negative_target_is_rejected_without_mutation(self):
        before = dict(self.values.target)
        with self.assertRaisesRegex(ValueError, "below"):
            self.values.shift_one("hv", "delay", -20.0)
        self.assertEqual(self.values.target, before)

    def test_readback_tolerance_is_inclusive(self):
        self.values.request_value("kly", "delay", 1.0)
        self.values.sync_readback("kly", "delay", 1.001)
        self.assertTrue(self.values.matches("kly", "delay", 0.001))
        self.values.sync_readback("kly", "delay", 1.0011)
        self.assertFalse(self.values.matches("kly", "delay", 0.001))

    def test_linked_shift_uses_only_selected_group_devices(self):
        values = TimingValues(minimum_us=0.0, devices=("llrf", "ssa"))
        values.sync_setpoint("llrf", "delay", 3.0, follow_target=True)
        values.sync_setpoint("ssa", "delay", 4.0, follow_target=True)
        changed = values.shift_group_delay(0.25)
        self.assertEqual(
            changed,
            {("llrf", "delay"): 3.25, ("ssa", "delay"): 4.25},
        )
        with self.assertRaisesRegex(ValueError, "not available"):
            values.request_value("kly", "delay", 1.0)


class WriteQueueTests(unittest.TestCase):
    def test_clicks_while_busy_coalesce_to_latest_target(self):
        queue = CoalescingWriteQueue()
        queue.enqueue({("hv", "delay"): 1.0, ("llrf", "delay"): 2.0})
        first = queue.begin_next()
        self.assertEqual(first[("hv", "delay")], 1.0)
        queue.enqueue({("hv", "delay"): 1.1})
        queue.enqueue({("hv", "delay"): 1.2, ("ssa", "delay"): 3.0})
        queue.finish()
        second = queue.begin_next()
        self.assertEqual(
            second,
            {("hv", "delay"): 1.2, ("ssa", "delay"): 3.0},
        )

    def test_batch_writer_reports_partial_failure(self):
        captured = []
        worker = BatchWriteWorker(
            {
                ("hv", "delay"): ("TEST:HV", 1.0),
                ("llrf", "delay"): ("TEST:LLRF", 2.0),
            }
        )
        worker.completed.connect(lambda results, error: captured.append((results, error)))
        with patch(
            "half_linac.src.apps.power_source_timing.epics_client.epics.caput_many",
            return_value=[1, -1],
        ):
            worker.run()
        self.assertEqual(captured[0][0][("hv", "delay")], True)
        self.assertEqual(captured[0][0][("llrf", "delay")], False)
        self.assertIn("failed", captured[0][1].lower())


class WaveformAnalysisTests(unittest.TestCase):
    def test_positive_and_negative_pulses_share_the_same_edge(self):
        positive = [2.0, 2.0, 2.0, 4.0, 6.0, 6.0]
        negative = [-value for value in positive]
        positive_result = analyze_waveform(
            positive, threshold_fraction=0.5, baseline_fraction=0.25
        )
        negative_result = analyze_waveform(
            negative, threshold_fraction=0.5, baseline_fraction=0.25
        )
        self.assertEqual(positive_result.polarity, 1)
        self.assertEqual(negative_result.polarity, -1)
        self.assertAlmostEqual(positive_result.baseline, 2.0)
        self.assertAlmostEqual(positive_result.edge_position, 3.0)
        self.assertAlmostEqual(
            positive_result.edge_position, negative_result.edge_position
        )

    def test_threshold_interpolation_and_roi(self):
        result = analyze_waveform(
            [0.0, 0.0, 0.25, 0.75, 1.0],
            threshold_fraction=0.5,
            baseline_fraction=0.2,
            roi_start=1,
            roi_stop=5,
        )
        self.assertAlmostEqual(result.edge_position, 2.5)
        excluded = analyze_waveform(
            [0.0, 0.0, 0.25, 0.75, 1.0],
            threshold_fraction=0.5,
            baseline_fraction=0.2,
            roi_start=3,
            roi_stop=5,
        )
        self.assertIsNone(excluded.edge_position)

    def test_invalid_waveforms_are_rejected(self):
        for values in ([], [np.nan, np.nan], [1.0, 1.0, 1.0], ["bad", "data"]):
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    analyze_waveform(values)


class WaveformMonitorTests(unittest.TestCase):
    def test_latest_array_connection_timestamp_and_generation(self):
        created = []

        class FakePV:
            def __init__(self, name, **kwargs):
                self.name = name
                self.callback = kwargs["callback"]
                self.connection_callback = kwargs["connection_callback"]
                created.append(self)

            def clear_callbacks(self):
                pass

            def disconnect(self):
                pass

        first = TimingGroup("A", "A", {}, {"llrf": "PV:A"})
        second = TimingGroup("B", "B", {}, {"llrf": "PV:B"})
        monitor = WaveformMonitor()
        with patch(
            "half_linac.src.apps.power_source_timing.epics_client.epics.PV",
            FakePV,
        ):
            monitor.bind(first)
            old_callback = created[-1].callback
            created[-1].connection_callback(conn=True)
            created[-1].callback(value=np.array([1.0, 2.0]), timestamp=12.5)
            created[-1].callback(value=np.array([3.0, 4.0]), timestamp=13.5)
            snapshot = monitor.snapshots()["llrf"]
            self.assertTrue(snapshot.connected)
            self.assertEqual(snapshot.epics_timestamp, 13.5)
            np.testing.assert_array_equal(snapshot.value, [3.0, 4.0])
            self.assertLess(time.monotonic() - snapshot.received_monotonic, 1.0)
            created[-1].connection_callback(conn=False)
            disconnected = monitor.snapshots()["llrf"]
            self.assertFalse(disconnected.connected)
            np.testing.assert_array_equal(disconnected.value, [3.0, 4.0])

            monitor.bind(second)
            old_callback(value=np.array([99.0]), timestamp=99.0)
            self.assertIsNone(monitor.snapshots()["llrf"].value)
        monitor.close()


class TimingWindowSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_window_builds_without_epics_connections(self):
        with patch.dict(
            os.environ,
            {
                "HALF_LINAC_MACHINE_ID": "half",
                "HALF_LINAC_CONTROL_BACKEND": "real",
            },
        ):
            runtime = load_timing_runtime()
        with patch(
            "half_linac.src.apps.power_source_timing.main.GroupMonitor.bind",
            return_value=None,
        ), patch(
            "half_linac.src.apps.power_source_timing.waveform_view.WaveformMonitor.bind",
            return_value=None,
        ):
            window = TimingWindow(runtime)
        try:
            self.assertEqual(len(window.group_buttons), 21)
            self.assertEqual(next(iter(window.group_buttons)), "PREBUNCHER")
            self.assertEqual(set(window.channel_widgets), set(DEVICES))
            self.assertEqual(
                set(window.waveform_view.trace_widgets), set(WAVEFORM_DEVICES)
            )
            self.assertEqual(window.waveform_view.fit_button.text(), "Full View")
            for device in DEVICES:
                self.assertIn(
                    TRACE_COLORS[device],
                    window.channel_widgets[device].channel_label.styleSheet(),
                )
            self.assertEqual(window.current_group.element_id, "KLY01")
            self.assertEqual(
                window.windowTitle(), "HALF Linac · RF Power Source Timing"
            )
            self.assertTrue(window.delay_step.isEditable())
            self.assertTrue(window.width_step.isEditable())
            window.delay_step.setEditText("0.037")
            window.width_step.setEditText("0.125")
            self.assertAlmostEqual(window._delay_step(), 0.037)
            self.assertAlmostEqual(window._width_step(), 0.125)
            delay_request = window.channel_widgets["hv"].delay_target
            self.assertEqual(
                delay_request.buttonSymbols(), QAbstractSpinBox.NoButtons
            )
            self.assertEqual(delay_request.text(), "—")
            self.assertFalse(delay_request.isEnabled())
            window._on_connection("hv", "delay_set", True)
            window._on_pv_value("hv", "delay_set", 0.0)
            self.assertEqual(delay_request.text(), "0.000000")
            self.assertTrue(delay_request.isEnabled())
            window._on_pv_value("hv", "delay_set", 1.25)
            self.assertEqual(delay_request.text(), "1.250000")
            self.assertAlmostEqual(delay_request.value(), 1.25)
            self.assertEqual(window.waveform_view.current_group.element_id, "KLY01")
            self.assertEqual(
                window.waveform_view.trace_widgets["hv"].status.text(),
                "Not configured",
            )
            self.assertTrue(
                window.waveform_view.trace_widgets["pickup"].summary.isHidden()
            )
            self.assertFalse(
                window.waveform_view.trace_widgets["kly"].summary.isHidden()
            )
            self.assertTrue(window.group_advance.autoRepeat())
            self.assertEqual(window.group_advance.autoRepeatDelay(), 300)
            self.assertEqual(window.group_advance.autoRepeatInterval(), 150)
            trigger = window.channel_widgets["hv"].enable
            self.assertEqual(trigger.text(), "Unavailable")
            self.assertFalse(trigger.isEnabled())
            self.assertFalse(trigger.autoRepeat())

            window._on_connection("hv", "enable", True)
            self.assertEqual(trigger.text(), "Waiting…")
            self.assertFalse(trigger.isEnabled())
            window._on_pv_value("hv", "enable", 0)
            self.assertEqual(trigger.text(), "Disabled")
            self.assertTrue(trigger.isEnabled())
            self.assertFalse(trigger.isChecked())

            with patch.object(window, "_start_next_write"):
                trigger.click()
            self.assertEqual(window.enable_requests["hv"], True)
            self.assertEqual(window.queue.pending[("hv", "enable")], 1.0)
            self.assertEqual(trigger.text(), "Enabling…")
            self.assertFalse(trigger.isEnabled())
            self.assertEqual(
                window.channel_widgets["hv"].status.text(), "Updating trigger"
            )

            window.queue.begin_next()
            window._batch_results = {("hv", "enable"): True}
            window._on_worker_finished()
            self.assertNotIn("hv", window.enable_requests)
            self.assertEqual(trigger.text(), "Disabled")
            window._on_pv_value("hv", "enable", 1)
            self.assertEqual(trigger.text(), "Enabled")
            self.assertTrue(trigger.isChecked())

            with patch.object(window, "_start_next_write"):
                trigger.click()
            self.assertEqual(trigger.text(), "Disabling…")
            window.queue.begin_next()
            window._batch_results = {("hv", "enable"): False}
            window._batch_error = "simulated trigger failure"
            window._on_worker_finished()
            self.assertNotIn("hv", window.enable_requests)
            self.assertEqual(trigger.text(), "Enabled")
            self.assertTrue(trigger.isChecked())
            self.assertEqual(
                window.channel_widgets["hv"].status.text(),
                "Trigger write failed",
            )

            window._on_pv_value("hv", "enable", 0)
            self.assertEqual(trigger.text(), "Disabled")
            self.assertFalse(trigger.isChecked())
            now = time.monotonic()
            snapshots = {
                "llrf": WaveformSnapshot(
                    np.array([0.0, 0.0, 0.0, 1.0]), True, 1.0, now
                ),
                "ssa": WaveformSnapshot(
                    np.array([0.0, 0.0, 0.0, 0.5, 1.0]), True, 1.0, now
                ),
                "kly": WaveformSnapshot(
                    np.array([0.0, 0.0, -1.0]), True, 1.0, now
                ),
            }
            with patch.object(
                window.waveform_view.monitor,
                "snapshots",
                return_value=snapshots,
            ) as waveform_snapshots:
                window.waveform_view.refresh_now()
                self.assertEqual(
                    window.waveform_view.reference_combo.currentData(), "llrf"
                )
                window.waveform_view.display_mode.setCurrentIndex(
                    window.waveform_view.display_mode.findData("raw")
                )
                self.assertEqual(window.waveform_view.display_mode.currentData(), "raw")
                ssa_result = window.waveform_view.trace_widgets["ssa"].result.text()
                kly_result = window.waveform_view.trace_widgets["kly"].result.text()
                self.assertIn("μs local", ssa_result)
                self.assertIn("μs local", kly_result)
                self.assertNotIn("Δ", ssa_result)
                self.assertNotIn("Earlier", kly_result)
                self.assertTrue(window.waveform_view.reference_combo.isHidden())
                self.assertEqual(
                    window.waveform_view.title_label.text(), "Waveform Inspection"
                )
                self.assertIn(
                    "independent channel origins",
                    window.waveform_view.info_label.text(),
                )
                window.waveform_view.trace_widgets["ssa"].visible.setChecked(False)
                self.assertEqual(
                    window.waveform_view.trace_widgets["ssa"].result.text(),
                    "Unavailable",
                )
                window.waveform_view.freeze_button.setChecked(True)
                self.assertEqual(window.waveform_view.freeze_button.text(), "Frozen")
                window.waveform_view.freeze_button.setChecked(False)
                self.assertEqual(window.waveform_view.freeze_button.text(), "Freeze")
                if window.waveform_view.roi is not None:
                    roi_start, roi_stop = window.waveform_view.roi_bounds()
                    self.assertEqual(roi_start, 0)
                    self.assertGreaterEqual(roi_stop, 4)
                    x_values, _y_values = window.waveform_view.curves["llrf"].getData()
                    self.assertAlmostEqual(x_values[-1], 3.0 / 105.78)
                    self.assertEqual(
                        window.waveform_view.plot.getAxis("bottom").labelText,
                        "Local Time (μs, independent origins)",
                    )
                    full_low, full_high = window.waveform_view.roi.getRegion()
                    selected_low = full_low + (full_high - full_low) * 0.25
                    selected_high = full_low + (full_high - full_low) * 0.75
                    window.waveform_view.roi.setRegion(
                        (selected_low, selected_high)
                    )
                    window.waveform_view._on_roi_change_finished()
                    view_low, view_high = window.waveform_view.plot.viewRange()[0]
                    self.assertLess(view_low, selected_low)
                    self.assertGreater(view_high, selected_high)
                    self.assertLess(view_high - view_low, full_high - full_low)
                    self.assertFalse(
                        window.waveform_view.plot.getViewBox().state["autoRange"][0]
                    )
                    focus_snapshots = {}
                    for device, pulse_bounds in {
                        "llrf": (30, 40),
                        "ssa": (45, 55),
                        "kly": (60, 70),
                    }.items():
                        values = np.zeros(100)
                        values[slice(*pulse_bounds)] = 1.0
                        focus_snapshots[device] = WaveformSnapshot(
                            values, True, 1.0, now
                        )
                    waveform_snapshots.return_value = focus_snapshots
                    window.waveform_view.trace_widgets["ssa"].visible.setChecked(True)
                    window.waveform_view.refresh_now()
                    window.waveform_view.focus_pulse_button.click()
                    self.assertEqual(window.waveform_view.roi_bounds(), (26, 74))
                    self.assertIn(
                        "Focused visible pulse region",
                        window.waveform_view.info_label.text(),
                    )
                original_theme = window._theme
                window._toggle_theme()
                self.assertNotEqual(window._theme, original_theme)
            stale_snapshots = dict(snapshots)
            stale_snapshots["kly"] = WaveformSnapshot(
                np.array([0.0, 0.0, -1.0]), True, 1.0, now - 3.0
            )
            with patch.object(
                window.waveform_view.monitor,
                "snapshots",
                return_value=stale_snapshots,
            ):
                window.waveform_view.trace_widgets["ssa"].visible.setChecked(True)
                window.waveform_view.refresh_now()
                self.assertTrue(
                    window.waveform_view.trace_widgets["kly"].status.text().startswith(
                        "Stale"
                    )
                )
                self.assertEqual(
                    window.waveform_view.trace_widgets["kly"].result.text(),
                    "Unavailable",
                )
            with patch.object(window.monitor, "bind"), patch.object(
                window.waveform_view.monitor, "bind"
            ):
                window._select_group("PREBUNCHER")
            window._resume_adjust_buttons()
            self.assertEqual(window.current_group.element_id, "PREBUNCHER")
            self.assertEqual(window.values.devices, ("llrf", "ssa"))
            self.assertEqual(
                window.linked_delay_label.text(), "Linked Delay (2 channels)"
            )
            self.assertTrue(window.channel_widgets["hv"].channel_label.isHidden())
            self.assertFalse(window.channel_widgets["llrf"].channel_label.isHidden())
            self.assertFalse(window.channel_widgets["ssa"].channel_label.isHidden())
            self.assertTrue(window.channel_widgets["kly"].channel_label.isHidden())
            self.assertEqual(
                window.waveform_view.current_group.waveforms,
                {
                    "llrf": "IN:MW:LLRFPB:CH8_WFAMP",
                    "ssa": "IN:MW:LLRFPB:CH1_WFAMP",
                    "pickup": "IN:MW:LLRFPB:CH2_WFAMP",
                },
            )
            self.assertTrue(
                window.waveform_view.trace_widgets["pickup"].visible.isEnabled()
            )
            self.assertFalse(
                window.waveform_view.trace_widgets["pickup"].summary.isHidden()
            )
            self.assertTrue(
                window.waveform_view.trace_widgets["kly"].summary.isHidden()
            )
            self.assertFalse(window.group_advance.isEnabled())
            for index, device in enumerate(("llrf", "ssa")):
                window._on_connection(device, "delay_set", True)
                window._on_pv_value(device, "delay_set", 5.0 + index)
            self.assertTrue(window.group_advance.isEnabled())
            with patch.object(window, "_start_next_write"):
                window._shift_group(0.25)
            self.assertEqual(
                window.queue.pending,
                {("llrf", "delay"): 5.25, ("ssa", "delay"): 6.25},
            )
            window.queue.clear()
        finally:
            window.close()

    def test_disconnect_gating_and_external_ao_resync(self):
        with patch.dict(
            os.environ,
            {
                "HALF_LINAC_MACHINE_ID": "half",
                "HALF_LINAC_CONTROL_BACKEND": "real",
            },
        ):
            runtime = load_timing_runtime()
        with patch(
            "half_linac.src.apps.power_source_timing.main.GroupMonitor.bind",
            return_value=None,
        ), patch(
            "half_linac.src.apps.power_source_timing.waveform_view.WaveformMonitor.bind",
            return_value=None,
        ):
            window = TimingWindow(runtime)
        try:
            hv_row = window.channel_widgets["hv"]
            self.assertFalse(hv_row.delay_advance.isEnabled())
            self.assertFalse(window.group_advance.isEnabled())

            for index, device in enumerate(DEVICES):
                window._on_connection(device, "delay_set", True)
                window._on_pv_value(device, "delay_set", 10.0 + index)
            self.assertTrue(hv_row.delay_target.isEnabled())
            self.assertTrue(hv_row.delay_advance.isEnabled())
            self.assertTrue(window.group_advance.isEnabled())

            window._on_connection("hv", "delay_set", False)
            previous_target = window.values.target[("hv", "delay")]
            self.assertFalse(hv_row.delay_target.isEnabled())
            self.assertFalse(hv_row.delay_advance.isEnabled())
            self.assertFalse(window.group_advance.isEnabled())
            window._shift_one("hv", "delay", 0.1)
            self.assertEqual(window.values.target[("hv", "delay")], previous_target)
            self.assertFalse(window.queue.pending)

            window._on_connection("hv", "delay_set", True)
            self.assertTrue(hv_row.delay_target.isEnabled())
            self.assertTrue(hv_row.delay_advance.isEnabled())
            self.assertTrue(window.group_advance.isEnabled())

            with patch.object(window, "_start_next_write"):
                window._shift_group(0.1)
            self.assertEqual(
                {key for key in window.queue.pending if key[1] == "delay"},
                {(device, "delay") for device in DEVICES},
            )
            window._on_connection("hv", "delay_set", False)
            self.assertFalse(
                {key for key in window.queue.pending if key[1] == "delay"}
            )
            for device in DEVICES:
                self.assertEqual(
                    window.values.target[(device, "delay")],
                    window.values.setpoint[(device, "delay")],
                )
            window._on_connection("hv", "delay_set", True)

            with patch.object(window, "_start_next_write"):
                window._shift_one("hv", "delay", 0.1)
            window.queue.begin_next()
            with patch.object(window, "_start_next_write"):
                window._shift_one("hv", "delay", 0.1)
            self.assertIn(("hv", "delay"), window.queue.pending)

            window._on_pv_value("hv", "delay_set", 42.0)
            self.assertNotIn(("hv", "delay"), window.queue.pending)
            self.assertIn(("hv", "delay"), window.external_resync_keys)
            self.assertNotEqual(window.values.target[("hv", "delay")], 42.0)

            window._batch_results = {("hv", "delay"): True}
            window._on_worker_finished()
            self.assertFalse(window.external_resync_keys)
            self.assertEqual(window.values.target[("hv", "delay")], 42.0)
            self.assertEqual(hv_row.delay_target.text(), "42.000000")
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
