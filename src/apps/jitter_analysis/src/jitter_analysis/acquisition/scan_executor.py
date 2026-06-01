from __future__ import annotations

from datetime import datetime

from ..config.models import KnobSpec, ObjectSpec
from ..domain.types import ScanStepRecord
from ..epics.client import PyEpicsClient
from .plans import KnobScanPlan
from .sampler import AcquisitionSampler


class KnobScanExecutor:
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
        for index, value in enumerate(plan.scan_values):
            step = ScanStepRecord(
                step_index=index,
                target_value=value,
                readback_value=None,
                started_at=datetime.now(),
            )
            for sample_offset in range(plan.sample_count_per_step):
                batch_index = index * plan.sample_count_per_step + sample_offset
                step.samples.extend(
                    self.sampler.sample_objects(
                        objects,
                        step_index=index,
                        batch_index=batch_index,
                    )
                )
            if knob.readback_pv:
                readback = self.client.read(knob.readback_pv)
                step.readback_value = float(readback.value) if readback.value is not None else None
            step.settled_at = datetime.now()
            steps.append(step)
        return steps
