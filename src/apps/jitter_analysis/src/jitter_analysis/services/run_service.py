from __future__ import annotations

from datetime import datetime

from ..domain.types import MultiKnobStepRecord, RunMetadata, RunMode, RunResult, SampleRecord, ScanStepRecord
from ..storage.run_store import RunStore


class RunService:
    def __init__(self, store: RunStore | None = None) -> None:
        self.store = store or RunStore()

    def configure_store(self, root: str) -> None:
        self.store.close_active_stream()
        self.store = RunStore(root)

    def create_metadata(
        self,
        mode: RunMode,
        machine: str = "",
        config_path: str = "",
        operator: str = "",
        notes: str = "",
    ) -> RunMetadata:
        run_id = self.store.create_run_id()
        metadata = RunMetadata(
            run_id=run_id,
            mode=mode,
            created_at=datetime.now(),
            operator=operator,
            machine=machine,
            config_path=config_path,
            notes=notes,
        )
        self.store.save_metadata(metadata)
        self.store.start_run(metadata)
        return metadata

    def append_samples(self, samples: list[SampleRecord]) -> None:
        self.store.append_samples(samples)

    def append_step(self, step: ScanStepRecord | MultiKnobStepRecord) -> None:
        self.store.append_step(step)

    def save_result(self, result: RunResult):
        return self.store.save_result(result)

    def load_result(self, path: str):
        return self.store.load_result(path)

    def list_runs(self, root: str | None = None):
        return self.store.list_runs(root)
