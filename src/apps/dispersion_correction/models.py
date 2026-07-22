from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

import numpy as np


ArrayLike = np.ndarray


@dataclass(frozen=True)
class BackendConfig:
    type: str = "offline"
    mode: str = "read_only"
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EnergyKnobConfig:
    name: str = "ENERGY_DELTA"
    delta: float = 1.0e-4
    actuator: str = "delta"
    actuator_unit: str = "delta_p_over_p"
    calibration: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class KnobConfig:
    name: str
    devices: dict[str, float]
    scan_step: float
    limit: float


@dataclass(frozen=True)
class MeasurementConfig:
    plane: str = "x"
    samples_per_step: int = 5
    sample_interval_s: float = 0.0
    final_samples: int = 10
    settle_time_s: float = 0.0


@dataclass(frozen=True)
class SolverConfig:
    svd_cut: float = 1.0e-3
    regularization: float = 1.0e-3
    gain: float = 0.5
    max_step_fraction: float = 0.25
    max_iter: int = 5
    response_update: str = "once"
    min_step_improvement: float = 0.05
    success_min_improvement: float = 2.0


@dataclass(frozen=True)
class SafetyConfig:
    max_reference_orbit_change_mm: float = 1.0


@dataclass(frozen=True)
class ModelObservableConfig:
    name: str
    element: str
    component: str
    target: float = 0.0

    @property
    def unit(self) -> str:
        return "mm" if self.component in {"dx", "dy"} else "mrad"


@dataclass(frozen=True)
class DispersionSectionConfig:
    id: str = "default"
    display_name: str = "Default"
    model_entrance: str | None = None
    model_exit: str | None = None
    target_dispersion_mm: tuple[float, ...] = ()
    model_observables: tuple[ModelObservableConfig, ...] = ()
    model_only: bool = False


@dataclass(frozen=True)
class RunConfig:
    backend: BackendConfig
    energy_knob: EnergyKnobConfig
    target_bpms: tuple[str, ...]
    knobs: tuple[KnobConfig, ...]
    section: DispersionSectionConfig
    measurement: MeasurementConfig
    solver: SolverConfig
    safety: SafetyConfig


@dataclass(frozen=True)
class BPMReading:
    names: tuple[str, ...]
    x_mm: ArrayLike
    y_mm: ArrayLike
    valid: ArrayLike
    charge: float | None = None
    loss: float | None = None

    def __post_init__(self) -> None:
        names = tuple(self.names)
        x_mm = np.asarray(self.x_mm, dtype=float)
        y_mm = np.asarray(self.y_mm, dtype=float)
        valid = np.asarray(self.valid, dtype=bool)
        if x_mm.shape != y_mm.shape or x_mm.shape != valid.shape:
            raise ValueError("BPM x, y, and valid arrays must have the same shape")
        if len(names) != x_mm.size:
            raise ValueError("BPM names length must match reading arrays")
        object.__setattr__(self, "names", names)
        object.__setattr__(self, "x_mm", x_mm)
        object.__setattr__(self, "y_mm", y_mm)
        object.__setattr__(self, "valid", valid)


@dataclass(frozen=True)
class MachineSnapshot:
    energy_delta: float
    device_values: dict[str, float]
    charge: float | None = None
    loss: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SafetyStatus:
    ok: bool
    reason: str = "OK"
    max_orbit_change_mm: float | None = None


@dataclass(frozen=True)
class DispersionMeasurement:
    bpm_names: tuple[str, ...]
    plane: str
    delta: float
    values_mm: ArrayLike
    valid: ArrayLike
    plus: BPMReading
    minus: BPMReading
    target_values_mm: ArrayLike | None = None

    def __post_init__(self) -> None:
        values = np.asarray(self.values_mm, dtype=float)
        valid = np.asarray(self.valid, dtype=bool)
        target = (
            np.zeros_like(values)
            if self.target_values_mm is None
            else np.asarray(self.target_values_mm, dtype=float)
        )
        if values.shape != valid.shape:
            raise ValueError("Dispersion values and valid mask must have same shape")
        if target.shape != values.shape:
            raise ValueError("Target dispersion values must match measured values")
        if len(self.bpm_names) != values.size:
            raise ValueError("BPM names length must match dispersion values")
        object.__setattr__(self, "bpm_names", tuple(self.bpm_names))
        object.__setattr__(self, "values_mm", values)
        object.__setattr__(self, "valid", valid)
        object.__setattr__(self, "target_values_mm", target)

    @property
    def valid_values_mm(self) -> ArrayLike:
        return self.values_mm[self.valid]

    @property
    def residual_values_mm(self) -> ArrayLike:
        return self.values_mm - self.target_values_mm

    @property
    def valid_residual_values_mm(self) -> ArrayLike:
        return self.residual_values_mm[self.valid]

    @property
    def measured_rms_mm(self) -> float:
        values = self.valid_values_mm
        if values.size == 0:
            return float("nan")
        return float(np.sqrt(np.mean(values * values)))

    @property
    def rms_mm(self) -> float:
        """Return RMS residual relative to the configured target dispersion."""

        values = self.valid_residual_values_mm
        if values.size == 0:
            return float("nan")
        return float(np.sqrt(np.mean(values * values)))


@dataclass(frozen=True)
class ResponseMatrixResult:
    matrix: ArrayLike
    bpm_names: tuple[str, ...]
    knob_names: tuple[str, ...]
    measurement: DispersionMeasurement
    singular_values: ArrayLike
    condition_number: float

    def __post_init__(self) -> None:
        matrix = np.asarray(self.matrix, dtype=float)
        singular_values = np.asarray(self.singular_values, dtype=float)
        object.__setattr__(self, "matrix", matrix)
        object.__setattr__(self, "singular_values", singular_values)


@dataclass(frozen=True)
class ModelResponseResult:
    section_id: str
    observable_names: tuple[str, ...]
    observable_elements: tuple[str, ...]
    observable_components: tuple[str, ...]
    observable_units: tuple[str, ...]
    knob_names: tuple[str, ...]
    baseline_values: ArrayLike
    target_values: ArrayLike
    response_matrix: ArrayLike
    singular_values: ArrayLike
    condition_number: float
    retained_rank: int
    derived_knobs: tuple[KnobConfig, ...]
    baseline_curve: "ModelOpticsCurve"
    preview_knob_deltas: dict[str, float]
    preview_values: ArrayLike
    preview_curve: "ModelOpticsCurve"

    def __post_init__(self) -> None:
        baseline = np.asarray(self.baseline_values, dtype=float)
        target = np.asarray(self.target_values, dtype=float)
        preview = np.asarray(self.preview_values, dtype=float)
        matrix = np.asarray(self.response_matrix, dtype=float)
        singular_values = np.asarray(self.singular_values, dtype=float)
        row_count = len(self.observable_names)
        metadata_lengths = {
            len(self.observable_elements),
            len(self.observable_components),
            len(self.observable_units),
        }
        if metadata_lengths != {row_count}:
            raise ValueError("Model observable metadata lengths must match")
        if baseline.shape != target.shape or baseline.shape != preview.shape:
            raise ValueError("Model baseline, target, and preview values must match")
        if baseline.shape != (row_count,):
            raise ValueError("Model values must match observable_names")
        if matrix.shape != (row_count, len(self.knob_names)):
            raise ValueError("Model response matrix shape must match observables and knobs")
        if set(self.preview_knob_deltas) != set(self.knob_names):
            raise ValueError("Preview knob deltas must match knob_names")
        object.__setattr__(self, "baseline_values", baseline)
        object.__setattr__(self, "target_values", target)
        object.__setattr__(self, "preview_values", preview)
        object.__setattr__(self, "response_matrix", matrix)
        object.__setattr__(self, "singular_values", singular_values)

    @property
    def residual_values(self) -> ArrayLike:
        return self.baseline_values - self.target_values

    @property
    def preview_residual_values(self) -> ArrayLike:
        return self.preview_values - self.target_values

    @property
    def baseline_rms(self) -> float:
        return float(np.sqrt(np.mean(self.residual_values**2)))

    @property
    def preview_rms(self) -> float:
        return float(np.sqrt(np.mean(self.preview_residual_values**2)))


@dataclass(frozen=True)
class ModelOpticsCurve:
    element_names: tuple[str, ...]
    element_types: tuple[str, ...]
    element_occurrences: tuple[int, ...]
    element_lengths_m: ArrayLike
    element_k1_m2: ArrayLike
    element_angles_rad: ArrayLike
    element_tilts_rad: ArrayLike
    s_m: ArrayLike
    dx_mm: ArrayLike
    dxp_mrad: ArrayLike
    dy_mm: ArrayLike
    dyp_mrad: ArrayLike
    beta_x_m: ArrayLike
    beta_y_m: ArrayLike

    def __post_init__(self) -> None:
        arrays = {
            name: np.asarray(getattr(self, name), dtype=float)
            for name in (
                "element_lengths_m",
                "element_k1_m2",
                "element_angles_rad",
                "element_tilts_rad",
                "s_m",
                "dx_mm",
                "dxp_mrad",
                "dy_mm",
                "dyp_mrad",
                "beta_x_m",
                "beta_y_m",
            )
        }
        size = len(self.element_names)
        if len(self.element_types) != size or len(self.element_occurrences) != size:
            raise ValueError("Model element metadata must match element_names")
        if any(values.shape != (size,) for values in arrays.values()):
            raise ValueError("Model optics curve arrays must match element_names")
        for name, values in arrays.items():
            object.__setattr__(self, name, values)


@dataclass(frozen=True)
class CorrectionStep:
    iteration: int
    gain: float
    delta_knobs: dict[str, float]
    accepted: bool
    reason: str
    rms_before_mm: float
    rms_after_mm: float | None = None


@dataclass(frozen=True)
class CorrectionResult:
    success: bool
    reason: str
    initial: DispersionMeasurement
    final: DispersionMeasurement
    initial_knobs: dict[str, float]
    final_knobs: dict[str, float]
    steps: tuple[CorrectionStep, ...]
    response: ResponseMatrixResult | None
    safety: SafetyStatus

    @property
    def improvement(self) -> float:
        final = self.final.rms_mm
        initial = self.initial.rms_mm
        if not np.isfinite(final) or final <= 0:
            return float("inf")
        return float(initial / final)

    @property
    def knob_delta(self) -> dict[str, float]:
        return {
            name: self.final_knobs.get(name, 0.0) - value
            for name, value in self.initial_knobs.items()
        }


def as_float_mapping(values: Mapping[str, Any]) -> dict[str, float]:
    return {str(key): float(value) for key, value in values.items()}
