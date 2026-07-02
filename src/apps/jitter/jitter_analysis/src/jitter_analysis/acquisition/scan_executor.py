from __future__ import annotations

from datetime import datetime
import time

from ..config.models import KnobSpec, ObjectSpec
from ..domain.types import ScanStepRecord
from ..epics.client import PyEpicsClient
from .plans import KnobScanPlan
from .sampler import AcquisitionSampler


class KnobScanExecutor:
    settle_poll_interval_sec = 0.05

    def __init__(self, client: PyEpicsClient, sampler: AcquisitionSampler) -> None:
        self.client = client
        self.sampler = sampler

    def create_step_records(
        self,
        plan: KnobScanPlan,
        knob: KnobSpec,
        objects: list[ObjectSpec],
    ) -> list[ScanStepRecord]:
        steps: list[ScanStepRecord] = []
        if plan.sample_count_per_step <= 0:
            raise ValueError("sample_count_per_step must be positive")

        readback_pv = knob.readback_pv or knob.write_pv
        initial_value = self._read_numeric(readback_pv)
        wrote_anything = False

        try:
            for index, target_value in enumerate(plan.scan_values):
                target = float(target_value)
                self._validate_target(knob, target)
                if not self.client.write(knob.write_pv, target):
                    raise RuntimeError(f"caput failed for {knob.name} -> {target}")
                wrote_anything = True

                step = ScanStepRecord(
                    step_index=index,
                    target_value=target,
                    readback_value=None,
                    started_at=datetime.now(),
                )
                step.readback_value = self._wait_for_settle(plan, knob, target)
                step.settled_at = datetime.now()

                for sample_offset in range(plan.sample_count_per_step):
                    if sample_offset > 0 and plan.per_step_interval_sec is not None:
                        self._sleep(max(float(plan.per_step_interval_sec), 0.0))
                    batch_index = index * plan.sample_count_per_step + sample_offset
                    step.samples.extend(
                        self.sampler.sample_objects(
                            objects,
                            step_index=index,
                            batch_index=batch_index,
                        )
                    )
                steps.append(step)
        except Exception:
            if plan.restore_initial_value and wrote_anything and initial_value is not None:
                self.client.write(knob.write_pv, initial_value)
            raise

        if plan.restore_initial_value and wrote_anything and initial_value is not None:
            self.client.write(knob.write_pv, initial_value)
        return steps

    def _sleep(self, seconds: float) -> None:
        if seconds > 0.0:
            time.sleep(seconds)

    @staticmethod
    def _validate_target(knob: KnobSpec, target_value: float) -> None:
        low = float(knob.limits.low)
        high = float(knob.limits.high)
        if target_value < low or target_value > high:
            raise ValueError(
                f"Target {target_value} is outside limits [{low}, {high}] for {knob.name}"
            )

    def _read_numeric(self, pv_name: str) -> float | None:
        if not pv_name:
            return None
        result = self.client.read(pv_name)
        if not result.connected or result.value is None:
            return None
        try:
            return float(result.value)
        except (TypeError, ValueError):
            return None

    def _wait_for_settle(self, plan: KnobScanPlan, knob: KnobSpec, target_value: float) -> float | None:
        self._sleep(max(float(plan.settle_delay_sec), 0.0))
        readback_pv = knob.readback_pv or knob.write_pv
        mode = str(knob.settle.mode).strip().lower()
        if mode != "readback_tolerance":
            return self._read_numeric(readback_pv)

        tolerance = float(knob.settle.readback_tolerance)
        max_wait_sec = max(float(plan.max_wait_sec), 0.0)
        deadline = time.monotonic() + max_wait_sec
        readback_value = self._read_numeric(readback_pv)
        while time.monotonic() <= deadline:
            if readback_value is not None and abs(readback_value - target_value) <= tolerance:
                return readback_value
            self._sleep(self.settle_poll_interval_sec)
            readback_value = self._read_numeric(readback_pv)
        return readback_value
