from __future__ import annotations

import threading
import time

try:
    from PyQt5 import QtCore
except ImportError:  # pragma: no cover - optional runtime dependency
    QtCore = None


def _wait_until_deadline(stop_event: threading.Event, deadline: float) -> bool:
    wait_time = deadline - time.monotonic()
    if wait_time <= 0:
        return stop_event.is_set()
    return bool(stop_event.wait(wait_time))


if QtCore is not None:

    class WorkerSignals(QtCore.QObject):
        started = QtCore.pyqtSignal()
        progressed = QtCore.pyqtSignal(str)
        finished = QtCore.pyqtSignal()
        failed = QtCore.pyqtSignal(str)


    class TimedAcquisitionSignals(QtCore.QObject):
        started = QtCore.pyqtSignal(int)
        batch_ready = QtCore.pyqtSignal(int, object)
        connection_status = QtCore.pyqtSignal(int, int)
        progress = QtCore.pyqtSignal(int, int)
        finished = QtCore.pyqtSignal(str)
        failed = QtCore.pyqtSignal(str)


    class KnobScanSignals(QtCore.QObject):
        started = QtCore.pyqtSignal(int, int)
        batch_ready = QtCore.pyqtSignal(int, int, int, float, object, object)
        step_ready = QtCore.pyqtSignal(int, int, float, object, object)
        progress = QtCore.pyqtSignal(int, int, int, int)
        message = QtCore.pyqtSignal(str)
        finished = QtCore.pyqtSignal(str, bool)
        failed = QtCore.pyqtSignal(str)


    class MultiKnobRandomSignals(QtCore.QObject):
        started = QtCore.pyqtSignal(int, int, int)
        batch_ready = QtCore.pyqtSignal(int, int, int, object, object, object)
        step_ready = QtCore.pyqtSignal(int, int, object, object, object)
        progress = QtCore.pyqtSignal(int, int, int, int)
        message = QtCore.pyqtSignal(str)
        finished = QtCore.pyqtSignal(str, bool)
        failed = QtCore.pyqtSignal(str)


    class TimedAcquisitionWorker(QtCore.QObject):
        def __init__(self, sampler, objects, shot_interval_sec: float, sample_count: int) -> None:
            super().__init__()
            self.sampler = sampler
            self.objects = list(objects)
            self.shot_interval_sec = shot_interval_sec
            self.sample_count = sample_count
            self.connection_status_interval_sec = 1.0
            self.signals = TimedAcquisitionSignals()
            self._stop_event = threading.Event()

        @QtCore.pyqtSlot()
        def run(self) -> None:
            try:
                self.signals.started.emit(self.sample_count)
                next_deadline = time.monotonic()
                next_connection_status_deadline = next_deadline
                for sample_index in range(self.sample_count):
                    if self._stop_event.is_set():
                        self.signals.finished.emit("stopped")
                        return

                    batch = self.sampler.sample_batch(self.objects, batch_index=sample_index)
                    self.signals.batch_ready.emit(sample_index, batch)
                    self.signals.progress.emit(sample_index + 1, self.sample_count)
                    if time.monotonic() >= next_connection_status_deadline:
                        self._emit_connection_status()
                        next_connection_status_deadline = time.monotonic() + self.connection_status_interval_sec

                    next_deadline += self.shot_interval_sec
                    if _wait_until_deadline(self._stop_event, next_deadline):
                        self.signals.finished.emit("stopped")
                        return

                self.signals.finished.emit("completed")
            except Exception as exc:  # pragma: no cover - thread runtime integration
                self.signals.failed.emit(str(exc))

        def stop(self) -> None:
            self._stop_event.set()

        def _emit_connection_status(self) -> None:
            pv_names = [obj.read_pv for obj in self.objects]
            try:
                connected_flags = self.sampler.client.snapshot_connections(pv_names)
            except Exception:
                return
            self.signals.connection_status.emit(sum(1 for flag in connected_flags if flag), len(connected_flags))


    class KnobScanWorker(QtCore.QObject):
        def __init__(
            self,
            client,
            sampler,
            knob,
            objects,
            scan_values,
            settle_delay_sec: float,
            sample_count_per_step: int,
            shot_interval_sec: float,
            restore_initial_value: bool,
        ) -> None:
            super().__init__()
            self.client = client
            self.sampler = sampler
            self.knob = knob
            self.objects = list(objects)
            self.scan_values = list(scan_values)
            self.settle_delay_sec = settle_delay_sec
            self.sample_count_per_step = sample_count_per_step
            self.shot_interval_sec = shot_interval_sec
            self.restore_initial_value = restore_initial_value
            self.signals = KnobScanSignals()
            self._stop_event = threading.Event()

        @QtCore.pyqtSlot()
        def run(self) -> None:
            initial_value = None
            restored = False
            wrote_anything = False
            try:
                initial_value = self._read_numeric(self.knob.readback_pv or self.knob.write_pv)
                total_steps = len(self.scan_values)
                self.signals.started.emit(total_steps, self.sample_count_per_step)
                overall_sample_index = 0

                for step_index, target_value in enumerate(self.scan_values):
                    self._raise_if_stopped()
                    self._validate_target(target_value)

                    if not self.client.write(self.knob.write_pv, target_value):
                        raise RuntimeError(f"caput failed for {self.knob.name} -> {target_value}")
                    wrote_anything = True
                    self.signals.message.emit(
                        f"Wrote {self.knob.name} to {target_value:.6g} {self.knob.unit}"
                    )

                    readback_value = self._wait_for_settle(target_value)
                    step_samples = []
                    next_deadline = time.monotonic()

                    for sample_index in range(self.sample_count_per_step):
                        self._raise_if_stopped()
                        batch = self.sampler.sample_objects(
                            self.objects,
                            step_index=step_index,
                            batch_index=overall_sample_index,
                        )
                        step_samples.extend(batch)
                        self.signals.batch_ready.emit(
                            overall_sample_index,
                            step_index,
                            sample_index,
                            target_value,
                            readback_value,
                            batch,
                        )
                        self.signals.progress.emit(
                            step_index + 1,
                            total_steps,
                            sample_index + 1,
                            self.sample_count_per_step,
                        )
                        overall_sample_index += 1
                        if sample_index + 1 < self.sample_count_per_step:
                            next_deadline += self.shot_interval_sec
                            if _wait_until_deadline(self._stop_event, next_deadline):
                                raise InterruptedError()

                    self.signals.step_ready.emit(
                        step_index,
                        total_steps,
                        target_value,
                        readback_value,
                        step_samples,
                    )

                if self.restore_initial_value and initial_value is not None and wrote_anything:
                    restored = self._restore_initial_value(initial_value)
                self.signals.finished.emit("completed", restored)
            except InterruptedError:
                if self.restore_initial_value and initial_value is not None and wrote_anything:
                    restored = self._restore_initial_value(initial_value)
                self.signals.finished.emit("stopped", restored)
            except Exception as exc:  # pragma: no cover - thread runtime integration
                if self.restore_initial_value and initial_value is not None and wrote_anything:
                    restored = self._restore_initial_value(initial_value)
                self.signals.failed.emit(str(exc))

        def stop(self) -> None:
            self._stop_event.set()

        def _sleep_or_stop(self, seconds: float) -> None:
            if seconds <= 0:
                self._raise_if_stopped()
                return
            if self._stop_event.wait(seconds):
                raise InterruptedError()

        def _raise_if_stopped(self) -> None:
            if self._stop_event.is_set():
                raise InterruptedError()

        def _read_numeric(self, pv_name: str) -> float | None:
            result = self.client.read(pv_name)
            if not result.connected or result.value is None:
                return None
            try:
                return float(result.value)
            except (TypeError, ValueError):
                return None

        def _validate_target(self, target_value: float) -> None:
            low = self.knob.limits.low
            high = self.knob.limits.high
            if target_value < low or target_value > high:
                raise ValueError(
                    f"Target {target_value} is outside limits [{low}, {high}] for {self.knob.name}"
                )

        def _wait_for_settle(self, target_value: float):
            self._sleep_or_stop(self.settle_delay_sec)
            readback_pv = self.knob.readback_pv or self.knob.write_pv

            mode = str(self.knob.settle.mode).strip().lower()
            if mode != "readback_tolerance":
                return self._read_numeric(readback_pv)

            tolerance = float(self.knob.settle.readback_tolerance)
            max_wait_sec = float(self.knob.settle.max_wait_sec)
            deadline = time.monotonic() + max_wait_sec
            readback_value = None
            while time.monotonic() <= deadline:
                self._raise_if_stopped()
                readback_value = self._read_numeric(readback_pv)
                if readback_value is not None and abs(readback_value - target_value) <= tolerance:
                    return readback_value
                self._sleep_or_stop(0.05)
            return readback_value

        def _restore_initial_value(self, initial_value: float) -> bool:
            if not self.client.write(self.knob.write_pv, initial_value):
                self.signals.message.emit(
                    f"Failed to restore {self.knob.name} to {initial_value:.6g} {self.knob.unit}"
                )
                return False
            self.signals.message.emit(
                f"Restored {self.knob.name} to {initial_value:.6g} {self.knob.unit}"
            )
            return True


    class MultiKnobRandomWorker(QtCore.QObject):
        def __init__(
            self,
            client,
            sampler,
            knobs,
            objects,
            target_steps,
            settle_delay_sec: float,
            sample_count_per_point: int,
            shot_interval_sec: float,
            restore_initial_values: bool,
        ) -> None:
            super().__init__()
            self.client = client
            self.sampler = sampler
            self.knobs = list(knobs)
            self.objects = list(objects)
            self.target_steps = [dict(step) for step in target_steps]
            self.settle_delay_sec = settle_delay_sec
            self.sample_count_per_point = sample_count_per_point
            self.shot_interval_sec = shot_interval_sec
            self.restore_initial_values = restore_initial_values
            self.signals = MultiKnobRandomSignals()
            self._stop_event = threading.Event()

        @QtCore.pyqtSlot()
        def run(self) -> None:
            initial_values = {}
            restored = False
            wrote_anything = False
            try:
                initial_values = self._read_numeric_map(
                    {
                        knob.id: knob.readback_pv or knob.write_pv
                        for knob in self.knobs
                    }
                )

                total_steps = len(self.target_steps)
                self.signals.started.emit(total_steps, self.sample_count_per_point, len(self.knobs))
                overall_sample_index = 0

                for step_index, target_values in enumerate(self.target_steps):
                    self._raise_if_stopped()
                    self._validate_targets(target_values)

                    wrote_anything = self._write_targets(target_values) or wrote_anything

                    readback_values = self._wait_for_settle(target_values)
                    step_samples = []
                    next_deadline = time.monotonic()

                    for sample_index in range(self.sample_count_per_point):
                        self._raise_if_stopped()
                        batch = self.sampler.sample_objects(
                            self.objects,
                            step_index=step_index,
                            batch_index=overall_sample_index,
                        )
                        step_samples.extend(batch)
                        self.signals.batch_ready.emit(
                            overall_sample_index,
                            step_index,
                            sample_index,
                            dict(target_values),
                            dict(readback_values),
                            batch,
                        )
                        self.signals.progress.emit(
                            step_index + 1,
                            total_steps,
                            sample_index + 1,
                            self.sample_count_per_point,
                        )
                        overall_sample_index += 1
                        if sample_index + 1 < self.sample_count_per_point:
                            next_deadline += self.shot_interval_sec
                            if _wait_until_deadline(self._stop_event, next_deadline):
                                raise InterruptedError()

                    self.signals.step_ready.emit(
                        step_index,
                        total_steps,
                        dict(target_values),
                        dict(readback_values),
                        step_samples,
                    )

                if self.restore_initial_values and wrote_anything:
                    restored = self._restore_initial_values(initial_values)
                self.signals.finished.emit("completed", restored)
            except InterruptedError:
                if self.restore_initial_values and wrote_anything:
                    restored = self._restore_initial_values(initial_values)
                self.signals.finished.emit("stopped", restored)
            except Exception as exc:  # pragma: no cover - thread runtime integration
                if self.restore_initial_values and wrote_anything:
                    restored = self._restore_initial_values(initial_values)
                self.signals.failed.emit(str(exc))

        def stop(self) -> None:
            self._stop_event.set()

        def _sleep_or_stop(self, seconds: float) -> None:
            if seconds <= 0:
                self._raise_if_stopped()
                return
            if self._stop_event.wait(seconds):
                raise InterruptedError()

        def _raise_if_stopped(self) -> None:
            if self._stop_event.is_set():
                raise InterruptedError()

        def _read_numeric(self, pv_name: str) -> float | None:
            result = self.client.read(pv_name)
            if not result.connected or result.value is None:
                return None
            try:
                return float(result.value)
            except (TypeError, ValueError):
                return None

        def _read_numeric_map(self, pv_names_by_id: dict[str, str]) -> dict[str, float | None]:
            if not pv_names_by_id:
                return {}
            item_ids = list(pv_names_by_id)
            results = self.client.read_many([pv_names_by_id[item_id] for item_id in item_ids])
            if len(results) != len(item_ids):
                raise RuntimeError("EPICS client returned a mismatched bulk read result.")
            values: dict[str, float | None] = {}
            for item_id, result in zip(item_ids, results):
                if not result.connected or result.value is None:
                    values[item_id] = None
                    continue
                try:
                    values[item_id] = float(result.value)
                except (TypeError, ValueError):
                    values[item_id] = None
            return values

        def _validate_targets(self, target_values: dict[str, float]) -> None:
            for knob in self.knobs:
                if knob.id not in target_values:
                    continue
                target_value = float(target_values[knob.id])
                low = float(knob.limits.low)
                high = float(knob.limits.high)
                if target_value < low or target_value > high:
                    raise ValueError(
                        f"Target {target_value} is outside limits [{low}, {high}] for {knob.name}"
                    )

        def _wait_for_settle(self, target_values: dict[str, float]) -> dict[str, float | None]:
            self._sleep_or_stop(self.settle_delay_sec)
            readback_values = self._read_numeric_map(
                {
                    knob.id: knob.readback_pv or knob.write_pv
                    for knob in self.knobs
                    if knob.id in target_values
                }
            )

            tolerance_knobs = []
            for knob in self.knobs:
                if knob.id not in target_values:
                    continue
                if str(knob.settle.mode).strip().lower() == "readback_tolerance":
                    tolerance_knobs.append(knob)

            if not tolerance_knobs:
                return readback_values

            max_wait_sec = max(float(knob.settle.max_wait_sec) for knob in tolerance_knobs)
            deadline = time.monotonic() + max_wait_sec
            while time.monotonic() <= deadline:
                self._raise_if_stopped()
                current_values = self._read_numeric_map(
                    {
                        knob.id: knob.readback_pv or knob.write_pv
                        for knob in tolerance_knobs
                    }
                )
                readback_values.update(current_values)
                all_settled = True
                for knob in tolerance_knobs:
                    readback_value = readback_values.get(knob.id)
                    tolerance = float(knob.settle.readback_tolerance)
                    target_value = float(target_values[knob.id])
                    if readback_value is None or abs(readback_value - target_value) > tolerance:
                        all_settled = False
                if all_settled:
                    return readback_values
                self._sleep_or_stop(0.05)
            return readback_values

        def _write_targets(self, target_values: dict[str, float]) -> bool:
            pending_writes = []
            for knob in self.knobs:
                if knob.id not in target_values:
                    continue
                pending_writes.append((knob, float(target_values[knob.id])))
            if not pending_writes:
                return False

            statuses = self.client.write_many(
                [(knob.write_pv, target_value) for knob, target_value in pending_writes]
            )
            if len(statuses) != len(pending_writes):
                raise RuntimeError("EPICS client returned a mismatched bulk write result.")

            for (knob, target_value), ok in zip(pending_writes, statuses):
                if not ok:
                    raise RuntimeError(f"caput failed for {knob.name} -> {target_value}")
                self.signals.message.emit(
                    f"Wrote {knob.name} to {target_value:.6g} {knob.unit}"
                )
            return True

        def _restore_initial_values(self, initial_values: dict[str, float | None]) -> bool:
            restored_all = True
            pending_writes = []
            for knob in self.knobs:
                initial_value = initial_values.get(knob.id)
                if initial_value is None:
                    self.signals.message.emit(
                        f"Skipped restore for {knob.name}: initial value was unavailable"
                    )
                    restored_all = False
                    continue
                pending_writes.append((knob, float(initial_value)))

            if not pending_writes:
                return restored_all

            statuses = self.client.write_many(
                [(knob.write_pv, initial_value) for knob, initial_value in pending_writes]
            )
            if len(statuses) != len(pending_writes):
                raise RuntimeError("EPICS client returned a mismatched bulk write result.")

            for (knob, initial_value), ok in zip(pending_writes, statuses):
                if not ok:
                    self.signals.message.emit(
                        f"Failed to restore {knob.name} to {initial_value:.6g} {knob.unit}"
                    )
                    restored_all = False
                    continue
                self.signals.message.emit(
                    f"Restored {knob.name} to {initial_value:.6g} {knob.unit}"
                )
            return restored_all


    class AcquisitionWorker(QtCore.QRunnable):
        def __init__(self, fn, *args, **kwargs) -> None:
            super().__init__()
            self.fn = fn
            self.args = args
            self.kwargs = kwargs
            self.signals = WorkerSignals()

        def run(self) -> None:
            self.signals.started.emit()
            try:
                self.fn(*self.args, **self.kwargs)
            except Exception as exc:  # pragma: no cover - UI thread integration
                self.signals.failed.emit(str(exc))
            else:
                self.signals.finished.emit()

else:

    class WorkerSignals:  # pragma: no cover - fallback for non-GUI tests
        pass


    class AcquisitionWorker:  # pragma: no cover - fallback for non-GUI tests
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("PyQt5 is required to create acquisition workers")


    class TimedAcquisitionWorker:  # pragma: no cover - fallback for non-GUI tests
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("PyQt5 is required to create timed acquisition workers")


    class KnobScanWorker:  # pragma: no cover - fallback for non-GUI tests
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("PyQt5 is required to create knob scan workers")


    class MultiKnobRandomWorker:  # pragma: no cover - fallback for non-GUI tests
        def __init__(self, *args, **kwargs) -> None:
            raise RuntimeError("PyQt5 is required to create multi-knob random workers")
