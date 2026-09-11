from __future__ import annotations

import os
from unittest.mock import patch

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("epics")

from half_linac.src.apps.llrf_control.epics_client import WriteWorker
from half_linac.src.apps.llrf_control.main import LlrfControlWindow
from half_linac.src.apps.llrf_control.model import CoalescingWriteQueue
from half_linac.src.apps.llrf_control.profile_runtime import load_llrf_runtime


def test_half_runtime_discovers_all_llrfs_with_limits_and_steps() -> None:
    with patch.dict(
        os.environ,
        {
            "HALF_LINAC_MACHINE_ID": "half",
            "HALF_LINAC_CONTROL_BACKEND": "real",
        },
    ):
        runtime = load_llrf_runtime()

    assert [group.element_id for group in runtime.groups] == [
        "LLRFPB",
        *(f"LLRF{index:02d}" for index in range(1, 21)),
    ]
    assert runtime.default_element == "LLRF01"
    assert runtime.groups[0].display_name == "Prebuncher LLRF"
    assert runtime.groups[1].display_name == "LLRF01"
    for group in runtime.groups:
        phase = group.quantities["phase"]
        amplitude = group.quantities["amplitude"]
        assert (phase.low, phase.high, phase.unit, phase.default_step) == (-180, 180, "deg", 5)
        assert (amplitude.low, amplitude.high, amplitude.unit, amplitude.default_step) == (0, 100, "%", 2)
        assert phase.readback_tolerance == 0.1
        assert amplitude.readback_tolerance == 1
        assert phase.setpoint_pv == f"IN:MW:{group.element_id}:SET_PHASE"
        assert phase.readback_pv == f"IN:MW:{group.element_id}:GET_PHASE"
        assert amplitude.setpoint_pv == f"IN:MW:{group.element_id}:SET_AMP"
        assert amplitude.readback_pv == f"IN:MW:{group.element_id}:GET_AMP"


def test_phase_wrap_preserves_range_endpoints_and_wraps_overflow() -> None:
    assert LlrfControlWindow._wrap_phase(180, -180, 180) == 180
    assert LlrfControlWindow._wrap_phase(185, -180, 180) == -175
    assert LlrfControlWindow._wrap_phase(-185, -180, 180) == 175


def test_write_worker_reports_caput_result() -> None:
    worker = WriteWorker("phase", "TEST:SET_PHASE", 12.5)
    emissions = []
    worker.completed.connect(lambda *args: emissions.append(args))

    with patch("half_linac.src.apps.llrf_control.epics_client.epics.caput", return_value=1) as caput:
        worker.run()

    caput.assert_called_once_with("TEST:SET_PHASE", 12.5, wait=True, timeout=5)
    assert emissions == [("phase", 12.5, True, "")]


def test_write_queue_coalesces_repeated_requests_to_latest_value() -> None:
    queue = CoalescingWriteQueue()
    queue.enqueue("phase", 5)
    assert queue.begin_next() == ("phase", 5)

    queue.enqueue("phase", 10)
    queue.enqueue("phase", 15)
    assert queue.requested["phase"] == 15
    assert queue.finish() == ("phase", 5)
    assert queue.begin_next() == ("phase", 15)
    queue.finish()
    queue.acknowledge("phase", 15)

    assert not queue.busy
    assert "phase" not in queue.requested


def test_write_queue_keeps_other_quantity_and_can_cancel_external_conflict() -> None:
    queue = CoalescingWriteQueue()
    queue.enqueue("phase", 5)
    queue.enqueue("amplitude", 80)
    assert queue.begin_next() == ("phase", 5)

    queue.cancel_pending("amplitude")
    assert "amplitude" not in queue.pending
    assert "amplitude" not in queue.requested
    assert queue.expected_values("phase") == (5,)


@pytest.fixture
def runtime():
    with patch.dict(os.environ, {"HALF_LINAC_MACHINE_ID": "half", "HALF_LINAC_CONTROL_BACKEND": "real"}):
        return load_llrf_runtime()


@pytest.fixture
def snapshot(runtime):
    from half_linac.src.apps.llrf_control.snapshot import make_snapshot, targets
    return make_snapshot(runtime, [12.1234567890123] * len(targets(runtime)))


def test_snapshot_roundtrip(runtime, snapshot, tmp_path):
    from half_linac.src.apps.llrf_control.snapshot import save_snapshot, load_snapshot, validate_snapshot
    path = tmp_path / "settings.json"
    save_snapshot(path, snapshot)
    restored = load_snapshot(path)
    assert restored == snapshot
    assert validate_snapshot(runtime, restored) == [12.1234567890123] * 42


@pytest.mark.parametrize("fault", ["version", "machine", "backend", "missing", "extra", "duplicate", "pv", "unit", "nan", "range", "bool"])
def test_invalid_snapshot_never_accesses_epics(runtime, snapshot, fault):
    from half_linac.src.apps.llrf_control.epics_client import SnapshotWorker
    if fault in {"version", "machine", "backend"}:
        snapshot[fault] = "invalid"
    elif fault == "missing":
        snapshot["parameters"].pop()
    elif fault == "extra":
        snapshot["parameters"].append(dict(snapshot["parameters"][0], device="UNKNOWN"))
    elif fault == "duplicate":
        snapshot["parameters"].append(snapshot["parameters"][0])
    else:
        key, value = {"pv": ("pv", "OTHER:PV"), "unit": ("unit", "rad"),
                      "nan": ("value", float("nan")), "range": ("value", 999),
                      "bool": ("value", True)}[fault]
        snapshot["parameters"][0][key] = value
    worker = SnapshotWorker(runtime, data=snapshot)
    with patch("half_linac.src.apps.llrf_control.epics_client.epics.PV") as pv:
        worker.run()
    assert not worker.success
    pv.assert_not_called()


def test_bad_json_and_atomic_save(snapshot, tmp_path):
    from half_linac.src.apps.llrf_control.snapshot import save_snapshot, load_snapshot
    path = tmp_path / "settings.json"
    for text in ('{', '{"version":1,"version":2}'):
        path.write_text(text)
        with pytest.raises(ValueError):
            load_snapshot(path)
    path.write_text("original")
    with patch("half_linac.src.apps.llrf_control.snapshot.os.replace", side_effect=OSError("failed")):
        with pytest.raises(OSError):
            save_snapshot(path, snapshot)
    assert path.read_text() == "original"
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("mode,fault", [("save", None), ("restore", None),
    ("save", "read"), ("restore", "connection"), ("restore", "write"), ("restore", "verify")])
def test_snapshot_worker_order_and_failure(runtime, snapshot, tmp_path, mode, fault):
    from unittest.mock import MagicMock
    from half_linac.src.apps.llrf_control.epics_client import SnapshotWorker
    from half_linac.src.apps.llrf_control.snapshot import load_snapshot, targets
    path = tmp_path / "saved.json"
    calls, instances = [], []
    def factory(name, **kwargs):
        index = len(instances)
        pv = MagicMock()
        pv.wait_for_connection.return_value = not (index == 1 and fault == "connection")
        pv.put.side_effect = lambda value, **kw: calls.append((name, value)) or (None if index == 1 and fault == "write" else 1)
        pv.get.return_value = None if index == 1 and fault == "read" else (99 if index == 1 and fault == "verify" else 12.1234567890123)
        instances.append(pv)
        return pv
    worker = SnapshotWorker(runtime, path=str(path) if mode == "save" else None,
                            data=snapshot if mode == "restore" else None)
    with patch("half_linac.src.apps.llrf_control.epics_client.epics.PV", side_effect=factory), patch("half_linac.src.apps.llrf_control.epics_client.epics.ca.use_initial_context"):
        worker.run()
    assert worker.success == (fault is None)
    assert len(instances) == (42 if fault is None else 2)
    for pv in instances:
        pv.disconnect.assert_called_once()
        if pv.get.called:
            pv.get.assert_called_once_with(use_monitor=False, timeout=3)
    if mode == "save":
        assert calls == []
        assert path.exists() == (fault is None)
        if not fault:
            assert load_snapshot(path)["parameters"] == snapshot["parameters"]
    else:
        expected = [(spec.setpoint_pv, 12.1234567890123) for _, _, spec in targets(runtime)]
        assert calls == expected[:len(calls)]
        assert len(calls) == (42 if fault is None else 1 if fault == "connection" else 2)
    if fault:
        assert "Completed (1)" in worker.message
        assert "LLRFPB amplitude" in worker.message
        assert "Not executed:\nLLRF01 phase" in worker.message
    if fault == "write":
        assert "not confirmed" in worker.message


def test_snapshot_ui_lock_cancel_and_refresh(runtime, snapshot):
    from PyQt5.QtWidgets import QApplication, QFileDialog, QMessageBox
    from unittest.mock import MagicMock
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    with patch("half_linac.src.apps.llrf_control.main.LlrfMonitor"):
        window = LlrfControlWindow(runtime)
        spec = window.current_group.quantities["phase"]
        window._on_connection(spec.set_channel, True)
        window._on_connection(spec.readback_channel, True)
        window._on_value(spec.set_channel, 10)
        assert window.quantity_widgets["phase"].target.isEnabled()
        with patch.object(QFileDialog, "getSaveFileName", return_value=("", "")), patch.object(window, "_start_snapshot") as start:
            window._save_snapshot()
            start.assert_not_called()
        with patch.object(QFileDialog, "getOpenFileName", return_value=("", "")), patch.object(window, "_start_snapshot") as start:
            window._restore_snapshot()
            start.assert_not_called()
        worker = MagicMock(success=True, message="Restored 42 AO parameters")
        window._snapshot_worker = worker
        window._snapshot_lock()
        assert not window.save_button.isEnabled()
        assert not window.restore_button.isEnabled()
        assert not window.quantity_widgets["phase"].target.isEnabled()
        assert not any(b.isEnabled() for b in window.group_buttons.values())
        event = MagicMock()
        window.closeEvent(event)
        event.ignore.assert_called_once()
        window._snapshot_finished()
        assert window.save_button.isEnabled()
        assert window.restore_button.isEnabled()
        assert all(b.isEnabled() for b in window.group_buttons.values())
        window.monitor.bind.assert_called_with(window.current_group)
        assert not window.values
        window.queue.enqueue("phase", 3)
        window._update_group_buttons()
        assert not window.save_button.isEnabled()
        with patch.object(QFileDialog, "getSaveFileName") as dialog:
            window._save_snapshot()
            dialog.assert_not_called()
        window.queue.clear()
        window.close()
        app.processEvents()


def test_restore_ui_validates_and_requires_confirmation(runtime, snapshot, tmp_path):
    from PyQt5.QtWidgets import QApplication, QFileDialog, QMessageBox
    from half_linac.src.apps.llrf_control.snapshot import save_snapshot
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "settings.json"
    save_snapshot(path, snapshot)
    with patch("half_linac.src.apps.llrf_control.main.LlrfMonitor"):
        window = LlrfControlWindow(runtime)
        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(path), "")), patch.object(window, "_start_snapshot") as start:
            with patch.object(QMessageBox, "question", return_value=QMessageBox.No):
                window._restore_snapshot()
                start.assert_not_called()
            with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
                window._restore_snapshot()
                start.assert_called_once_with(data=snapshot)
            start.reset_mock()
            path.write_text("invalid")
            with patch.object(QMessageBox, "warning") as warning, patch.object(QMessageBox, "question") as question:
                window._restore_snapshot()
                warning.assert_called_once()
                question.assert_not_called()
                start.assert_not_called()
        assert window.save_button.isEnabled()
        window.close()
        app.processEvents()


def test_snapshot_background_thread_finishes_and_unlocks(runtime, tmp_path):
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtCore import QEventLoop, QTimer
    from half_linac.src.apps.llrf_control.snapshot import load_snapshot
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    with patch("half_linac.src.apps.llrf_control.main.LlrfMonitor"), patch("half_linac.src.apps.llrf_control.epics_client.epics.PV") as factory, patch("half_linac.src.apps.llrf_control.epics_client.epics.ca.use_initial_context"):
        factory.return_value.wait_for_connection.return_value = True
        factory.return_value.get.return_value = 12.5
        window = LlrfControlWindow(runtime)
        path = tmp_path / "settings.json"
        window._start_snapshot(path=str(path))
        worker = window._snapshot_worker
        loop = QEventLoop()
        worker.finished.connect(loop.quit)
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        timer.start(5000)
        loop.exec_()
        timer.stop()
        assert window._snapshot_worker is None
        assert window.save_button.isEnabled()
        assert len(load_snapshot(path)["parameters"]) == 42
        assert "Saved 42" in window.statusBar().currentMessage()
        window.close()
        app.processEvents()
