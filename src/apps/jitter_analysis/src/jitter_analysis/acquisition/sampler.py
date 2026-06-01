from __future__ import annotations

from datetime import datetime

from ..config.models import ObjectSpec
from ..domain.types import SampleRecord
from ..epics.client import PyEpicsClient


class AcquisitionSampler:
    def __init__(self, client: PyEpicsClient) -> None:
        self.client = client

    def sample_object(
        self,
        obj: ObjectSpec,
        step_index: int | None = None,
        batch_index: int | None = None,
    ) -> SampleRecord:
        return self.sample_objects([obj], step_index=step_index, batch_index=batch_index)[0]

    def sample_objects(
        self,
        objects: list[ObjectSpec],
        step_index: int | None = None,
        batch_index: int | None = None,
    ) -> list[SampleRecord]:
        if not objects:
            return []

        results = self.client.read_many([obj.read_pv for obj in objects])
        if len(results) != len(objects):
            raise RuntimeError("EPICS client returned a mismatched bulk read result.")

        # One batch timestamp makes each acquisition round behave like a coherent snapshot.
        batch_timestamp = datetime.now()
        samples = []
        for obj, result in zip(objects, results):
            value = float(result.value) if result.value is not None else float("nan")
            samples.append(
                SampleRecord(
                    pv_id=obj.id,
                    value=value,
                    timestamp=batch_timestamp,
                    connected=result.connected,
                    step_index=step_index,
                    batch_index=batch_index,
                )
            )
        return samples
