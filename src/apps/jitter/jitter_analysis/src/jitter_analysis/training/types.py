from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable


class RunRole(str, Enum):
    IGNORE = "ignore"
    TRAIN = "train"
    TEST = "test"


@dataclass(frozen=True, slots=True)
class FeatureRecipe:
    id: str
    label: str
    channels: tuple[str, ...]
    include_target_history: bool = False


@dataclass(frozen=True, slots=True)
class TrainingTaskDefinition:
    id: str
    version: str
    label: str
    machine: str
    target_channel: str
    gate_channel: str
    required_channels: tuple[str, ...]
    feature_recipes: tuple[FeatureRecipe, ...]
    lag_windows: tuple[int, ...] = (1, 2, 3, 6, 12, 30)


@dataclass(slots=True)
class TrainingExperimentSpec:
    run_root: str
    output_root: str
    run_roles: dict[str, str]
    task_id: str = "fir_bpm04_forecast"
    subtract_upstream_orbit: bool = False
    coefficient_a: float = 0.0
    coefficient_b: float = 0.0
    beam_threshold: float = 1000.0
    window_seconds: float = 10.0
    horizon_windows: int = 1

    @property
    def train_paths(self) -> list[str]:
        return [path for path, role in self.run_roles.items() if role == RunRole.TRAIN.value]

    @property
    def test_paths(self) -> list[str]:
        return [path for path, role in self.run_roles.items() if role == RunRole.TEST.value]


@dataclass(frozen=True, slots=True)
class RunDescriptor:
    path: str
    run_id: str
    created_at: str
    mode: str
    status: str
    machine: str
    notes: str
    sample_interval_seconds: float | None
    logical_sample_count: int
    object_ids: tuple[str, ...]
    units: dict[str, str]
    compatible: bool
    issues: tuple[str, ...] = ()


@dataclass(slots=True)
class RunQuality:
    run_id: str
    role: str
    windows: int
    valid_windows: int
    beam_valid_fraction: float
    target_mean: float
    target_std: float
    missing_fraction: float
    sample_interval_seconds: float


@dataclass(slots=True)
class CandidateMetric:
    scope: str
    run_id: str
    candidate_id: str
    feature_group: str
    model: str
    exportable: bool
    samples: int
    rmse_mm: float
    mae_mm: float
    persistence_rmse_mm: float
    skill_vs_persistence: float
    delta_correlation: float
    direction_accuracy_large_half: float


@dataclass(slots=True)
class TrainingExperimentResult:
    experiment_id: str
    experiment_dir: str
    task_id: str
    recommended_candidate_id: str | None
    plot_candidate_id: str | None
    candidate_model_paths: dict[str, str]
    metrics_path: str
    predictions_path: str
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ModelPackageManifest:
    schema_version: int
    package_kind: str
    task_id: str
    task_version: str
    candidate_id: str
    target: dict[str, object]
    data_contract: dict[str, object]
    feature_names: list[str]
    preprocessing: dict[str, object]
    model: dict[str, object]
    provenance: dict[str, object]
    metrics: list[dict[str, object]]
    runtime: dict[str, str]
    model_sha256: str


ProgressCallback = Callable[[int, str], None]
CancelCallback = Callable[[], bool]
