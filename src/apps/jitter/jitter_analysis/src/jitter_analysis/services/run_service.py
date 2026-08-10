from __future__ import annotations

from datetime import datetime

from ..domain.types import (
    MultiKnobStepRecord,
    RunMetadata,
    RunMode,
    RunResult,
    SampleRecord,
    ScanStepRecord,
    TimedRunSeriesSnapshot,
    WaveformIndexEntry,
    WaveformRecord,
)
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
        config_snapshot_text: str = "",
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
        if str(config_snapshot_text).strip():
            self.store.save_config_snapshot(run_id, config_snapshot_text)
        self.store.start_run(metadata)
        return metadata

    def append_samples(self, samples: list[SampleRecord]) -> None:
        self.store.append_samples(samples)

    def append_waveforms(self, waveforms: list[WaveformRecord]) -> None:
        self.store.append_waveforms(waveforms)

    def append_step(self, step: ScanStepRecord | MultiKnobStepRecord) -> None:
        self.store.append_step(step)

    def save_result(self, result: RunResult):
        return self.store.save_result(result)

    def load_result(self, path: str):
        return self.store.load_result(path)

    def load_timed_acquisition_series_fast(
        self,
        path: str,
        minimum_record_count: int = RunStore.TIMED_FAST_PATH_MIN_RECORDS,
        requested_object_ids: list[str] | None = None,
    ) -> TimedRunSeriesSnapshot | None:
        return self.store.load_timed_acquisition_series_fast(
            path,
            minimum_record_count=minimum_record_count,
            requested_object_ids=requested_object_ids,
        )

    def load_waveform_index(self, path: str) -> list[WaveformIndexEntry]:
        return self.store.load_waveform_index(path)

    def load_waveform(self, path: str, entry: WaveformIndexEntry) -> WaveformRecord:
        return self.store.load_waveform(path, entry)

    def list_runs(self, root: str | None = None):
        return self.store.list_runs(root)

    def preferred_config_path(self, path: str, metadata_config_path: str = "") -> str:
        return self.store.preferred_config_path_for(path, metadata_config_path)
