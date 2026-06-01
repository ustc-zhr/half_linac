from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ..domain.types import MultiKnobStepRecord, RunMetadata, RunMode, RunResult, RunStatus, SampleRecord, ScanStepRecord
from .hdf5_stream import HDF5RunWriter
from .serializers import to_jsonable


class RunStore:
    def __init__(self, root: str | Path = "runs") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._active_writer: HDF5RunWriter | None = None

    def create_run_id(self) -> str:
        return datetime.now().strftime("%Y%m%d_%H%M%S_%f")

    def prepare_run_dir(self, run_id: str) -> Path:
        run_dir = self.root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    def save_metadata(self, metadata: RunMetadata) -> Path:
        run_dir = self.prepare_run_dir(metadata.run_id)
        path = run_dir / "metadata.json"
        path.write_text(json.dumps(to_jsonable(metadata), indent=2), encoding="utf-8")
        return path

    def start_run(self, metadata: RunMetadata) -> Path:
        self.close_active_stream()
        run_dir = self.prepare_run_dir(metadata.run_id)
        path = run_dir / "raw.h5"
        self._active_writer = HDF5RunWriter(path, metadata)
        return path

    def append_samples(self, samples: list[SampleRecord]) -> None:
        if self._active_writer is None:
            return
        self._active_writer.append_samples(samples)

    def append_step(self, step: ScanStepRecord | MultiKnobStepRecord) -> None:
        if self._active_writer is None:
            return
        self._active_writer.append_step(step)

    def close_active_stream(self) -> None:
        if self._active_writer is None:
            return
        self._active_writer.close()
        self._active_writer = None

    def save_result(self, result: RunResult) -> Path:
        run_dir = self.prepare_run_dir(result.metadata.run_id)
        if self._active_writer is not None and self._active_writer.run_id == result.metadata.run_id:
            writer = self._active_writer
            try:
                writer.finalize(result)
            except Exception:
                try:
                    writer.close()
                finally:
                    self._active_writer = None
                raise
            else:
                self._active_writer = None

        path = run_dir / "result.json"
        summary = {
            "metadata": to_jsonable(result.metadata),
            "status": result.status.value,
            "warnings": to_jsonable(result.warnings),
            "details": to_jsonable(result.details),
            "counts": {
                "samples": len(result.samples),
                "steps": len(result.steps),
                "connected_samples": sum(1 for sample in result.samples if sample.connected),
            },
            "files": {
                "metadata": "metadata.json",
                "raw_hdf5": "raw.h5",
                "datasets": {
                    "samples": "/samples",
                    "steps": "/steps",
                },
            },
        }
        if result.samples:
            summary["time_range"] = {
                "first": result.samples[0].timestamp.isoformat(),
                "last": result.samples[-1].timestamp.isoformat(),
            }
        path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return path

    def list_runs(self, root: str | Path | None = None) -> list[dict[str, object]]:
        scan_root = Path(root) if root is not None else self.root
        if not scan_root.exists():
            return []

        rows: list[dict[str, object]] = []
        seen_dirs: set[Path] = set()
        for run_dir in self._candidate_run_dirs(scan_root):
            resolved_dir = run_dir.resolve()
            if resolved_dir in seen_dirs:
                continue
            seen_dirs.add(resolved_dir)
            row = self._build_run_row(run_dir)
            if row is not None:
                rows.append(row)

        rows.sort(
            key=lambda row: (
                row.get("created_at") is not None,
                row.get("created_at") or datetime.min,
                str(row.get("run_id", "")),
            ),
            reverse=True,
        )
        return rows

    @staticmethod
    def _candidate_run_dirs(scan_root: Path) -> list[Path]:
        candidates = {
            path.parent
            for path in scan_root.rglob("metadata.json")
        }
        candidates.update(path.parent for path in scan_root.rglob("result.json"))
        candidates.update(path.parent for path in scan_root.rglob("raw.h5"))
        return list(candidates)

    def _build_run_row(self, run_dir: Path) -> dict[str, object] | None:
        metadata_payload = self._load_optional_json(run_dir / "metadata.json")
        summary_payload = self._load_optional_json(run_dir / "result.json")
        raw_exists = (run_dir / "raw.h5").exists()
        if not metadata_payload and not summary_payload and not raw_exists:
            return None

        metadata_source = metadata_payload.get("metadata", metadata_payload)
        summary_source = summary_payload.get("metadata", {})
        created_raw = (
            metadata_source.get("created_at")
            or summary_source.get("created_at")
            or run_dir.name
        )
        created_at = self._parse_optional_datetime(created_raw)
        return {
            "path": str(run_dir.resolve()),
            "run_id": str(metadata_source.get("run_id") or summary_source.get("run_id") or run_dir.name),
            "created_at": created_at,
            "created_at_text": created_at.strftime("%Y-%m-%d %H:%M:%S") if created_at else str(created_raw),
            "mode": str(metadata_source.get("mode") or summary_source.get("mode") or ""),
            "status": str(
                summary_payload.get("status")
                or ("incomplete" if raw_exists else "unknown")
            ),
            "operator": str(metadata_source.get("operator", "")),
            "machine": str(metadata_source.get("machine", "")),
            "notes": str(metadata_source.get("notes", "")),
            "sample_count": int(summary_payload.get("counts", {}).get("samples", 0)),
            "step_count": int(summary_payload.get("counts", {}).get("steps", 0)),
            "config_path": str(metadata_source.get("config_path", "")),
            "has_raw": raw_exists,
        }

    def load_result(self, path: str | Path) -> RunResult:
        run_dir = self._resolve_run_dir(path)
        raw_path = run_dir / "raw.h5"
        if not raw_path.exists():
            raise FileNotFoundError(f"Missing raw.h5 in {run_dir}")

        metadata_payload = self._load_optional_json(run_dir / "metadata.json")
        summary_payload = self._load_optional_json(run_dir / "result.json")

        try:
            import h5py
        except ImportError as exc:  # pragma: no cover - runtime dependency
            raise RuntimeError("h5py is required to load saved runs from HDF5.") from exc

        with h5py.File(raw_path, "r") as handle:
            metadata = self._load_metadata(handle, metadata_payload)
            samples = self._load_samples(handle)
            steps = self._load_steps(handle, samples)
            status = self._load_status(handle, summary_payload)
            warnings = self._load_warnings(handle, summary_payload)
            details = self._load_details(handle, summary_payload)

        return RunResult(
            metadata=metadata,
            status=status,
            samples=samples,
            steps=steps,
            warnings=warnings,
            details=details,
        )

    @staticmethod
    def _resolve_run_dir(path: str | Path) -> Path:
        target = Path(path)
        if target.is_dir():
            return target
        if target.name in {"raw.h5", "metadata.json", "result.json"}:
            return target.parent
        raise FileNotFoundError(f"Unsupported run path: {target}")

    @staticmethod
    def _load_optional_json(path: Path) -> dict:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _parse_optional_datetime(value) -> datetime | None:
        try:
            return datetime.fromisoformat(str(value))
        except Exception:
            return None

    @staticmethod
    def _decode(value):
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return value

    @classmethod
    def _load_metadata(cls, handle, metadata_payload: dict) -> RunMetadata:
        if metadata_payload:
            metadata_source = metadata_payload.get("metadata", metadata_payload)
            return RunMetadata(
                run_id=str(metadata_source["run_id"]),
                mode=RunMode(str(metadata_source["mode"])),
                created_at=datetime.fromisoformat(str(metadata_source["created_at"])),
                operator=str(metadata_source.get("operator", "")),
                machine=str(metadata_source.get("machine", "")),
                config_path=str(metadata_source.get("config_path", "")),
                notes=str(metadata_source.get("notes", "")),
            )

        attrs = handle.attrs
        return RunMetadata(
            run_id=str(cls._decode(attrs.get("run_id", ""))),
            mode=RunMode(str(cls._decode(attrs.get("mode", RunMode.TIMED_ACQUISITION.value)))),
            created_at=datetime.fromisoformat(str(cls._decode(attrs.get("created_at")))),
            operator=str(cls._decode(attrs.get("operator", ""))),
            machine=str(cls._decode(attrs.get("machine", ""))),
            config_path=str(cls._decode(attrs.get("config_path", ""))),
            notes=str(cls._decode(attrs.get("notes", ""))),
        )

    @classmethod
    def _load_status(cls, handle, summary_payload: dict) -> RunStatus:
        attrs = handle.attrs
        raw_status = cls._decode(attrs.get("final_status", "")) or summary_payload.get("status") or RunStatus.COMPLETED.value
        return RunStatus(str(raw_status))

    @classmethod
    def _load_warnings(cls, handle, summary_payload: dict) -> list[str]:
        attrs = handle.attrs
        raw = cls._decode(attrs.get("warnings_json", ""))
        if raw:
            return list(json.loads(str(raw)))
        return list(summary_payload.get("warnings", []))

    @classmethod
    def _load_details(cls, handle, summary_payload: dict) -> dict[str, object]:
        attrs = handle.attrs
        raw = cls._decode(attrs.get("details_json", ""))
        if raw:
            return dict(json.loads(str(raw)))
        return dict(summary_payload.get("details", {}))

    @classmethod
    def _load_samples(cls, handle) -> list[SampleRecord]:
        samples = []
        dataset = handle["samples"]
        sample_fields = set(getattr(dataset.dtype, "names", ()) or ())
        has_batch_index = "batch_index" in sample_fields
        for row in dataset:
            step_index = int(row["step_index"])
            batch_index = int(row["batch_index"]) if has_batch_index else -1
            samples.append(
                SampleRecord(
                    pv_id=str(cls._decode(row["pv_id"])),
                    value=float(row["value"]),
                    timestamp=datetime.fromisoformat(str(cls._decode(row["timestamp"]))),
                    connected=bool(row["connected"]),
                    severity=int(row["severity"]),
                    status=int(row["status"]),
                    step_index=None if step_index < 0 else step_index,
                    batch_index=None if batch_index < 0 else batch_index,
                )
            )
        return samples

    @classmethod
    def _load_steps(cls, handle, samples: list[SampleRecord]) -> list[ScanStepRecord | MultiKnobStepRecord]:
        samples_by_step: dict[int, list[SampleRecord]] = {}
        for sample in samples:
            if sample.step_index is None:
                continue
            samples_by_step.setdefault(int(sample.step_index), []).append(sample)

        steps: list[ScanStepRecord | MultiKnobStepRecord] = []
        for row in handle["steps"]:
            mode = str(cls._decode(row["mode"]))
            step_index = int(row["step_index"])
            started_at = datetime.fromisoformat(str(cls._decode(row["started_at"])))
            settled_token = str(cls._decode(row["settled_at"]))
            settled_at = datetime.fromisoformat(settled_token) if settled_token else None
            step_samples = list(samples_by_step.get(step_index, []))

            if mode == "multi_knob_random":
                target_values_raw = str(cls._decode(row["target_values_json"]))
                readback_values_raw = str(cls._decode(row["readback_values_json"]))
                steps.append(
                    MultiKnobStepRecord(
                        step_index=step_index,
                        target_values=dict(json.loads(target_values_raw or "{}")),
                        readback_values=dict(json.loads(readback_values_raw or "{}")),
                        started_at=started_at,
                        settled_at=settled_at,
                        samples=step_samples,
                    )
                )
                continue

            readback_value = float(row["readback_value"])
            steps.append(
                ScanStepRecord(
                    step_index=step_index,
                    target_value=float(row["target_value"]),
                    readback_value=None if readback_value != readback_value else readback_value,
                    started_at=started_at,
                    settled_at=settled_at,
                    samples=step_samples,
                )
            )

        return steps
