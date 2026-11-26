"""Recoverable machine-session orchestration for finite-pool InfoBAX.

This module deliberately knows nothing about EPICS or Qt.  A machine adapter
implements the small protocol below; the session owns budgets, raw records,
checkpointing, and restoration of the pre-run machine state.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Event
from typing import Any, Protocol
import json
import fcntl
import math
import os
import time

from half_linac.src.optimization.infobax import (
    InfoBAXCore,
    PointObservation,
    PoolExhaustedError,
    RecommendationResult,
)


SESSION_SCHEMA = "half_infobax_session_v1"
SESSION_FILENAME = "infobax_session.json"


class InfoBAXSessionError(RuntimeError):
    """Base class for explicit InfoBAX session failures."""


class SessionCompatibilityError(InfoBAXSessionError):
    """A persisted run is incompatible with the requested pool or machine."""


class SessionDriftError(InfoBAXSessionError):
    """The machine no longer matches the state restored by a paused run."""


class SessionRunFailure(InfoBAXSessionError):
    """A query could not be completed and committed."""


class SessionRestoreFailure(SessionRunFailure):
    """Restoring the pre-run state failed and overrides the primary failure."""


@dataclass(frozen=True)
class PointMeasurement:
    """One measured scalar submitted to the numerical InfoBAX core."""

    variance_mm2: float
    noise_std_mm2: float
    metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        variance = float(self.variance_mm2)
        noise = float(self.noise_std_mm2)
        if not math.isfinite(variance) or variance <= 0.0:
            raise ValueError("variance_mm2 must be positive and finite")
        if not math.isfinite(noise) or noise <= 0.0:
            raise ValueError("noise_std_mm2 must be positive and finite")
        if not isinstance(self.metadata, Mapping):
            raise ValueError("measurement metadata must be a mapping")
        metadata = _json_compatible(dict(self.metadata), "measurement metadata")
        object.__setattr__(self, "variance_mm2", variance)
        object.__setattr__(self, "noise_std_mm2", noise)
        object.__setattr__(self, "metadata", metadata)


@dataclass(frozen=True)
class InfoBAXSessionConfig:
    max_observations: int = 14
    max_minutes: float = 120.0

    def __post_init__(self) -> None:
        if (
            not isinstance(self.max_observations, int)
            or isinstance(self.max_observations, bool)
            or self.max_observations <= 0
        ):
            raise ValueError("max_observations must be a positive integer")
        minutes = float(self.max_minutes)
        if not math.isfinite(minutes) or minutes <= 0.0:
            raise ValueError("max_minutes must be positive and finite")
        object.__setattr__(self, "max_minutes", minutes)

    def to_dict(self) -> dict[str, int | float]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "InfoBAXSessionConfig":
        if not isinstance(payload, Mapping) or set(payload) != {
            "max_observations",
            "max_minutes",
        }:
            raise SessionCompatibilityError("session config fields do not match")
        try:
            return cls(**dict(payload))
        except (TypeError, ValueError) as error:
            raise SessionCompatibilityError(f"invalid session config: {error}") from error


class InfoBAXMachine(Protocol):
    """Narrow machine boundary required by :class:`InfoBAXSession`."""

    @property
    def fingerprint(self) -> str: ...

    @property
    def candidate_ids(self) -> tuple[str, ...]: ...

    def validate_pool(self, pool) -> None: ...

    def snapshot(self) -> Mapping[str, float]: ...

    def verify_snapshot(self, snapshot: Mapping[str, float]) -> None: ...

    def apply_query(self, candidate_index: int, scan_value: float) -> None: ...

    def measure_point(
        self, query_index: int, expected_noise_std_mm2: float
    ) -> PointMeasurement: ...

    def restore(self, snapshot: Mapping[str, float]) -> None: ...


class InfoBAXSession:
    """Run and persist a VM-safe single-point InfoBAX measurement loop."""

    def __init__(
        self,
        core: InfoBAXCore,
        machine: InfoBAXMachine,
        run_dir: str | Path,
        config: InfoBAXSessionConfig | None = None,
        *,
        cancelled: Event | None = None,
        progress: Callable[[Mapping[str, Any]], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        _load_existing: bool = False,
    ) -> None:
        if not isinstance(core, InfoBAXCore):
            raise TypeError("core must be an InfoBAXCore")
        machine_candidate_ids = getattr(machine, "candidate_ids", None)
        if machine_candidate_ids is not None and tuple(machine_candidate_ids) != tuple(
            core.pool.candidate_ids
        ):
            raise ValueError(
                "machine workpoint candidate IDs do not match the finite pool"
            )
        machine.validate_pool(core.pool)
        selected_config = config or InfoBAXSessionConfig()
        if selected_config.max_observations < core.config.initial_actions:
            raise ValueError(
                "max_observations must cover the complete InfoBAX initial design"
            )
        self.core = core
        self.machine = machine
        self.run_dir = Path(run_dir)
        if not _load_existing and self.checkpoint_path.exists():
            raise SessionCompatibilityError(
                "run directory already has a checkpoint; use from_run_dir to resume"
            )
        self.config = selected_config
        self.cancelled = cancelled or Event()
        self._pause_requested = Event()
        self._progress = progress
        self._clock = clock
        self._checkpoint_content: bytes | None = None

        self.status = "ready"
        self.error: str | None = None
        self.restoration_status = "not_required"
        self.restoration_error: str | None = None
        self.initial_snapshot: dict[str, float] | None = None
        self.records: list[dict[str, Any]] = []
        self.recommendation: RecommendationResult | None = None
        self.elapsed_seconds = 0.0

    @property
    def checkpoint_path(self) -> Path:
        return self.run_dir / SESSION_FILENAME

    def request_pause(self) -> None:
        """Request a stop at the next point between complete observations."""

        self._pause_requested.set()

    def run(self) -> RecommendationResult | None:
        """Run until a budget, pause, failure, or reliability gate stops it."""

        with self._exclusive_run():
            return self._run()

    @contextmanager
    def _exclusive_run(self):
        self.run_dir.mkdir(parents=True, exist_ok=True)
        with (self.run_dir / ".infobax_session.lock").open("a+") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise InfoBAXSessionError("another session is using this run directory") from error
            try:
                current = (
                    self.checkpoint_path.read_bytes()
                    if self.checkpoint_path.exists() else None
                )
                if current != self._checkpoint_content:
                    raise SessionCompatibilityError(
                        "checkpoint changed; reload with from_run_dir before continuing"
                    )
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _run(self) -> RecommendationResult | None:
        if self.status == "completed":
            return self.recommendation
        if self.initial_snapshot is None:
            self.initial_snapshot = _validated_snapshot(self.machine.snapshot())
        else:
            try:
                self.machine.verify_snapshot(self.initial_snapshot)
            except Exception as error:
                self.status = "drift_detected"
                self.error = str(error)
                self._save()
                raise SessionDriftError(
                    "machine state drifted after the prior run was restored"
                ) from error

        self.status = "running"
        self.error = None
        self.restoration_status = "pending"
        self.restoration_error = None
        self._save()
        self._emit_progress()

        started = self._clock()
        primary_failure: Exception | None = None
        try:
            while True:
                if self._pause_requested.is_set() or self.cancelled.is_set():
                    self.status = "paused"
                    break
                if len(self.core.observations) >= self.config.max_observations:
                    self.recommendation = self.core.recommend()
                    self.status = "completed"
                    break
                if self._elapsed(started) >= self.config.max_minutes * 60.0:
                    self.status = "time_budget_exhausted"
                    break

                try:
                    suggestion = self.core.propose()
                except PoolExhaustedError:
                    self.status = "pool_exhausted"
                    if len(self.core.observations) >= self.core.config.initial_actions:
                        self.recommendation = self.core.recommend()
                    break

                if suggestion.status == "degraded_negative_eig":
                    self.status = "degraded_negative_eig"
                    self.error = (
                        "maximum raw EIG is negative; automatic execution is blocked"
                    )
                    break

                record = {
                    "sequence": len(self.records),
                    "query_index": suggestion.query_index,
                    "candidate_index": suggestion.candidate_index,
                    "candidate_id": suggestion.candidate_id,
                    "scan_index": suggestion.scan_index,
                    "scan_value": suggestion.scan_value,
                    "phase": suggestion.phase,
                    "suggestion_status": suggestion.status,
                    "raw_eig_nats": suggestion.raw_eig_nats,
                    "state": "pending",
                    "measurement": None,
                    "error": None,
                }
                self.records.append(record)
                self._save()
                self._emit_progress(record)
                try:
                    self.machine.apply_query(
                        suggestion.candidate_index, suggestion.scan_value
                    )
                    expected_noise = float(
                        self.core.pool.observation_noise_std_mm2[
                            suggestion.query_index
                        ]
                    )
                    measurement = self.machine.measure_point(
                        suggestion.query_index, expected_noise
                    )
                    self.core.observe(
                        PointObservation(
                            query_index=suggestion.query_index,
                            variance_mm2=measurement.variance_mm2,
                            noise_std_mm2=measurement.noise_std_mm2,
                        )
                    )
                except Exception as error:
                    record["state"] = "failed"
                    record["error"] = str(error)
                    self.status = "failed"
                    self.error = str(error)
                    raise SessionRunFailure(
                        f"query {suggestion.query_index} failed: {error}"
                    ) from error

                record["state"] = "observed"
                record["measurement"] = {
                    "variance_mm2": measurement.variance_mm2,
                    "noise_std_mm2": measurement.noise_std_mm2,
                    "metadata": dict(measurement.metadata),
                }
                self._save()
                self._emit_progress(record)
        except Exception as error:
            primary_failure = error
            if self.status not in {"failed", "restore_failed"}:
                self.status = "failed"
                self.error = str(error)
        finally:
            # Consume our pause request. A caller-owned cancellation Event must
            # be cleared by its owner before continuing the same object.
            self._pause_requested.clear()
            self.elapsed_seconds = self._elapsed(started)
            try:
                self.machine.restore(self.initial_snapshot)
                self.restoration_status = "succeeded"
            except Exception as error:
                self.restoration_status = "failed"
                self.restoration_error = str(error)
                self.status = "restore_failed"
                self.error = str(error)
                primary_failure = SessionRestoreFailure(
                    f"failed to restore initial machine state: {error}"
                )
            self._save()
            self._emit_progress()

        if primary_failure is not None:
            raise primary_failure
        return self.recommendation

    def _elapsed(self, started: float) -> float:
        return self.elapsed_seconds + max(0.0, float(self._clock()) - started)

    def _emit_progress(self, record: Mapping[str, Any] | None = None) -> None:
        if self._progress is None:
            return
        self._progress(
            {
                "status": self.status,
                "observation_count": len(self.core.observations),
                "max_observations": self.config.max_observations,
                "record": None if record is None else dict(record),
            }
        )

    def _payload(self) -> dict[str, Any]:
        return {
            "schema": SESSION_SCHEMA,
            "pool_fingerprint": self.core.pool.fingerprint,
            "machine_fingerprint": self.machine.fingerprint,
            "config": self.config.to_dict(),
            "core_checkpoint": self.core.to_checkpoint(),
            "initial_snapshot": self.initial_snapshot,
            "records": self.records,
            "recommendation": (
                None if self.recommendation is None else asdict(self.recommendation)
            ),
            "status": self.status,
            "error": self.error,
            "restoration_status": self.restoration_status,
            "restoration_error": self.restoration_error,
            "elapsed_seconds": self.elapsed_seconds,
        }

    def _save(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        payload = _json_compatible(self._payload(), "session checkpoint")
        temporary = self.checkpoint_path.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(
                payload,
                handle,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(self.checkpoint_path)
        self._checkpoint_content = self.checkpoint_path.read_bytes()

    @classmethod
    def from_run_dir(
        cls,
        pool,
        machine: InfoBAXMachine,
        run_dir: str | Path,
        *,
        cancelled: Event | None = None,
        progress: Callable[[Mapping[str, Any]], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> "InfoBAXSession":
        """Load raw state; machine drift is checked when :meth:`run` resumes."""

        path = Path(run_dir) / SESSION_FILENAME
        try:
            checkpoint_content = path.read_bytes()
            payload = json.loads(checkpoint_content)
        except (OSError, json.JSONDecodeError) as error:
            raise SessionCompatibilityError(
                f"cannot read InfoBAX session checkpoint: {error}"
            ) from error
        expected = {
            "schema",
            "pool_fingerprint",
            "machine_fingerprint",
            "config",
            "core_checkpoint",
            "initial_snapshot",
            "records",
            "recommendation",
            "status",
            "error",
            "restoration_status",
            "restoration_error",
            "elapsed_seconds",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise SessionCompatibilityError("session checkpoint fields do not match")
        if payload["schema"] != SESSION_SCHEMA:
            raise SessionCompatibilityError("session checkpoint schema is unsupported")
        if payload["pool_fingerprint"] != pool.fingerprint:
            raise SessionCompatibilityError("finite-pool fingerprint does not match")
        if payload["machine_fingerprint"] != machine.fingerprint:
            raise SessionCompatibilityError("machine/workpoint fingerprint does not match")

        config = InfoBAXSessionConfig.from_dict(payload["config"])
        core = InfoBAXCore.from_checkpoint(pool, payload["core_checkpoint"])
        session = cls(
            core,
            machine,
            run_dir,
            config,
            cancelled=cancelled,
            progress=progress,
            clock=clock,
            _load_existing=True,
        )
        snapshot = payload["initial_snapshot"]
        if snapshot is None:
            raise SessionCompatibilityError("session checkpoint has no initial snapshot")
        session.initial_snapshot = _validated_snapshot(snapshot)
        records = payload["records"]
        if not isinstance(records, list) or not all(
            isinstance(record, dict) for record in records
        ):
            raise SessionCompatibilityError("session records must be a list of objects")
        session.records = _json_compatible(records, "session records")
        for record in session.records:
            if record.get("state") == "pending":
                record["state"] = "interrupted"
                record["error"] = "run stopped before observation was committed"

        recommendation = payload["recommendation"]
        if recommendation is not None:
            try:
                session.recommendation = RecommendationResult(**recommendation)
            except (TypeError, ValueError) as error:
                raise SessionCompatibilityError(
                    f"invalid persisted recommendation: {error}"
                ) from error
        session.status = str(payload["status"])
        session.error = payload["error"]
        session.restoration_status = str(payload["restoration_status"])
        session.restoration_error = payload["restoration_error"]
        try:
            session.elapsed_seconds = float(payload["elapsed_seconds"])
        except (TypeError, ValueError) as error:
            raise SessionCompatibilityError("invalid elapsed time") from error
        if not math.isfinite(session.elapsed_seconds) or session.elapsed_seconds < 0.0:
            raise SessionCompatibilityError("invalid elapsed time")
        session._checkpoint_content = checkpoint_content
        return session


def _validated_snapshot(value: Mapping[str, Any]) -> dict[str, float]:
    if not isinstance(value, Mapping) or not value:
        raise SessionCompatibilityError("machine snapshot must be a non-empty mapping")
    result: dict[str, float] = {}
    for key, raw in value.items():
        if not isinstance(key, str) or not key:
            raise SessionCompatibilityError("machine snapshot keys must be strings")
        number = float(raw)
        if not math.isfinite(number):
            raise SessionCompatibilityError("machine snapshot values must be finite")
        result[key] = number
    return result


def _json_compatible(value: Any, label: str) -> Any:
    try:
        encoded = json.dumps(value, allow_nan=False)
        return json.loads(encoded)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} is not JSON compatible: {error}") from error


__all__ = [
    "InfoBAXMachine",
    "InfoBAXSession",
    "InfoBAXSessionConfig",
    "InfoBAXSessionError",
    "PointMeasurement",
    "SESSION_FILENAME",
    "SessionCompatibilityError",
    "SessionDriftError",
    "SessionRestoreFailure",
    "SessionRunFailure",
]
