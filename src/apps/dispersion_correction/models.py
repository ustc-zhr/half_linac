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
    readback_tolerance: float | None = None
    readback_confirmations: int = 1
    round_actuator_step_to_integer: bool = False
    wrap_period: float | None = None
    wrap_origin: float = 0.0


@dataclass(frozen=True)
class KnobConfig:
    name: str
    devices: dict[str, float]
    scan_step: float
    limit: float
    scan_mode: str = "relative"
    unit: str = ""


@dataclass(frozen=True)
class MeasurementConfig:
    plane: str = "x"
    samples_per_step: int = 5
    sample_interval_s: float = 0.0
    final_samples: int = 10
    settle_time_s: float = 0.0

    @property
    def planes(self) -> tuple[str, ...]:
        return ("x", "y") if self.plane == "xy" else (self.plane,)


@dataclass(frozen=True)
class SolverConfig:
    svd_cut: float = 1.0e-3
    regularization: float = 1.0e-3
    gain: float = 0.5
    max_step_fraction: float = 0.25
    max_iter: int = 5
    response_update: str = "once"
    min_step_improvement: float = 0.05


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
class JointDispersionTargetConfig:
    bpm: str
    plane: str
    target_mm: float = 0.0
    tolerance_mm: float = 1.0

    @property
    def name(self) -> str:
        return f"{self.bpm} η{self.plane}"


@dataclass(frozen=True)
class JointResponseAnalysisConfig:
    targets: tuple[JointDispersionTargetConfig, ...] = ()
    knobs: tuple[KnobConfig, ...] = ()

    @property
    def enabled(self) -> bool:
        return bool(self.targets and self.knobs)


@dataclass(frozen=True)
class DispersionSectionConfig:
    id: str = "default"
    display_name: str = "Default"
    model_entrance: str | None = None
    model_exit: str | None = None
    target_dispersion_mm: tuple[float, ...] = ()
    model_observables: tuple[ModelObservableConfig, ...] = ()
    joint_response_analysis: JointResponseAnalysisConfig = field(
        default_factory=JointResponseAnalysisConfig
    )
    model_only: bool = False
    diagnostic_only: bool = False


@dataclass(frozen=True)
class RunConfig:
    backend: BackendConfig
    energy_knob: EnergyKnobConfig
    target_bpms: tuple[str, ...]
    monitor_bpms: tuple[str, ...]
    knobs: tuple[KnobConfig, ...]
    section: DispersionSectionConfig
    measurement: MeasurementConfig
    solver: SolverConfig
    safety: SafetyConfig

    @property
    def measurement_bpms(self) -> tuple[str, ...]:
        """BPMs read during a scan; monitor points never become solve targets."""

        return tuple(dict.fromkeys((*self.monitor_bpms, *self.target_bpms)))

    @property
    def runtime_knobs(self) -> tuple[KnobConfig, ...]:
        """Knobs requiring backend access in correction or joint-analysis mode."""

        return (
            self.knobs
            if self.knobs
            else self.section.joint_response_analysis.knobs
        )


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
    target_mask: ArrayLike | None = None

    def __post_init__(self) -> None:
        values = np.asarray(self.values_mm, dtype=float)
        valid = np.asarray(self.valid, dtype=bool)
        target = (
            np.zeros_like(values)
            if self.target_values_mm is None
            else np.asarray(self.target_values_mm, dtype=float)
        )
        target_mask = (
            np.ones_like(valid, dtype=bool)
            if self.target_mask is None
            else np.asarray(self.target_mask, dtype=bool)
        )
        if values.shape != valid.shape:
            raise ValueError("Dispersion values and valid mask must have same shape")
        if target.shape != values.shape:
            raise ValueError("Target dispersion values must match measured values")
        if target_mask.shape != values.shape:
            raise ValueError("Target BPM mask must match measured values")
        if len(self.bpm_names) != values.size:
            raise ValueError("BPM names length must match dispersion values")
        object.__setattr__(self, "bpm_names", tuple(self.bpm_names))
        object.__setattr__(self, "values_mm", values)
        object.__setattr__(self, "valid", valid)
        object.__setattr__(self, "target_values_mm", target)
        object.__setattr__(self, "target_mask", target_mask)

    @property
    def valid_values_mm(self) -> ArrayLike:
        return self.values_mm[self.valid]

    @property
    def residual_values_mm(self) -> ArrayLike:
        return self.values_mm - self.target_values_mm

    @property
    def valid_residual_values_mm(self) -> ArrayLike:
        return self.residual_values_mm[self.valid & self.target_mask]

    @property
    def correction_valid(self) -> ArrayLike:
        return self.valid & self.target_mask

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
class MultiPlaneDispersionMeasurement:
    measurements: tuple[DispersionMeasurement, ...]

    def __post_init__(self) -> None:
        measurements = tuple(self.measurements)
        planes = tuple(item.plane for item in measurements)
        if planes != ("x", "y"):
            raise ValueError(
                "Multi-plane dispersion requires measurements ordered as x, y"
            )
        first = measurements[0]
        for measurement in measurements[1:]:
            if measurement.bpm_names != first.bpm_names:
                raise ValueError(
                    "Multi-plane measurements must use the same BPM order"
                )
            if measurement.delta != first.delta:
                raise ValueError(
                    "Multi-plane measurements must use the same energy step"
                )
            if measurement.plus is not first.plus or measurement.minus is not first.minus:
                raise ValueError(
                    "Multi-plane measurements must share one energy scan"
                )
        object.__setattr__(self, "measurements", measurements)

    @property
    def planes(self) -> tuple[str, ...]:
        return tuple(item.plane for item in self.measurements)

    @property
    def primary(self) -> DispersionMeasurement:
        return self.measurements[0]

    def for_plane(self, plane: str) -> DispersionMeasurement:
        normalized = str(plane).strip().lower()
        for measurement in self.measurements:
            if measurement.plane == normalized:
                return measurement
        raise KeyError(f"No dispersion measurement for plane {plane!r}")


@dataclass(frozen=True)
class ImportedDispersionDataset:
    section_id: str
    bpm_names: tuple[str, ...]
    etax_mm: ArrayLike
    etax_sigma_mm: ArrayLike
    source_path: str

    def __post_init__(self) -> None:
        names = tuple(self.bpm_names)
        values = np.asarray(self.etax_mm, dtype=float)
        uncertainties = np.asarray(self.etax_sigma_mm, dtype=float)
        if values.shape != uncertainties.shape or values.shape != (len(names),):
            raise ValueError("Imported eta_x values, uncertainties, and BPM names must match")
        if not np.all(np.isfinite(values)):
            raise ValueError("Imported eta_x values must be finite")
        if np.any(np.isinf(uncertainties)):
            raise ValueError("Imported eta_x uncertainties must be finite or blank")
        finite_uncertainties = uncertainties[np.isfinite(uncertainties)]
        if np.any(finite_uncertainties < 0):
            raise ValueError("Imported eta_x uncertainties must be non-negative")
        object.__setattr__(self, "bpm_names", names)
        object.__setattr__(self, "etax_mm", values)
        object.__setattr__(self, "etax_sigma_mm", uncertainties)


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
class JointResponseAnalysisResult:
    matrix: ArrayLike
    target_names: tuple[str, ...]
    target_bpms: tuple[str, ...]
    target_planes: tuple[str, ...]
    target_values_mm: ArrayLike
    tolerances_mm: ArrayLike
    baseline_values_mm: ArrayLike
    valid: ArrayLike
    knob_names: tuple[str, ...]
    baseline: MultiPlaneDispersionMeasurement
    delta_knobs: dict[str, float]
    baseline_device_values: dict[str, float]
    target_device_values: dict[str, float]
    predicted_values_mm: ArrayLike
    singular_values: ArrayLike
    retained_rank: int
    condition_number: float
    normalized_rms_before: float
    normalized_rms_after: float
    uncontrollable_rms: float

    def __post_init__(self) -> None:
        matrix = np.asarray(self.matrix, dtype=float)
        targets = np.asarray(self.target_values_mm, dtype=float)
        tolerances = np.asarray(self.tolerances_mm, dtype=float)
        baseline = np.asarray(self.baseline_values_mm, dtype=float)
        valid = np.asarray(self.valid, dtype=bool)
        predicted = np.asarray(self.predicted_values_mm, dtype=float)
        singular_values = np.asarray(self.singular_values, dtype=float)
        row_count = len(self.target_names)
        if matrix.shape != (row_count, len(self.knob_names)):
            raise ValueError("Joint response matrix shape does not match rows and knobs")
        if any(
            values.shape != (row_count,)
            for values in (targets, tolerances, baseline, valid, predicted)
        ):
            raise ValueError("Joint response row arrays must match target names")
        if len(self.target_bpms) != row_count or len(self.target_planes) != row_count:
            raise ValueError("Joint response target metadata lengths must match")
        if np.any(tolerances <= 0):
            raise ValueError("Joint response tolerances must be positive")
        if tuple(self.delta_knobs) != self.knob_names:
            raise ValueError("Joint recommendation knob order must match response columns")
        object.__setattr__(self, "matrix", matrix)
        object.__setattr__(self, "target_values_mm", targets)
        object.__setattr__(self, "tolerances_mm", tolerances)
        object.__setattr__(self, "baseline_values_mm", baseline)
        object.__setattr__(self, "valid", valid)
        object.__setattr__(self, "predicted_values_mm", predicted)
        object.__setattr__(self, "singular_values", singular_values)


@dataclass(frozen=True)
class JointCorrectionStep:
    iteration: int
    response: JointResponseAnalysisResult
    measured_after: MultiPlaneDispersionMeasurement
    normalized_rms_after: float
    accepted: bool
    reason: str
    device_values_before: dict[str, float] | None = None
    device_values_trial: dict[str, float] | None = None
    restored: bool = False


@dataclass(frozen=True)
class JointCorrectionResult:
    success: bool
    reason: str
    initial: MultiPlaneDispersionMeasurement
    final: MultiPlaneDispersionMeasurement
    steps: tuple[JointCorrectionStep, ...]

    @property
    def normalized_rms_before(self) -> float:
        return self.steps[0].response.normalized_rms_before

    @property
    def normalized_rms_after(self) -> float:
        return (
            self.steps[-1].normalized_rms_after
            if self.steps
            else self.normalized_rms_before
        )


@dataclass(frozen=True)
class CorrectionRecommendation:
    measurement: DispersionMeasurement
    response: ResponseMatrixResult
    delta_knobs: dict[str, float]
    device_deltas: dict[str, float]
    baseline_device_values: dict[str, float]
    target_device_values: dict[str, float]
    predicted_values_mm: ArrayLike
    predicted_residual_values_mm: ArrayLike
    valid: ArrayLike
    predicted_rms_mm: float
    singular_values: ArrayLike
    condition_number: float
    reason: str = "Ready for review"

    def __post_init__(self) -> None:
        predicted = np.asarray(self.predicted_values_mm, dtype=float)
        residual = np.asarray(self.predicted_residual_values_mm, dtype=float)
        valid = np.asarray(self.valid, dtype=bool)
        singular_values = np.asarray(self.singular_values, dtype=float)
        expected = self.measurement.values_mm.shape
        if predicted.shape != expected or residual.shape != expected or valid.shape != expected:
            raise ValueError("Recommendation arrays must match the dispersion measurement")
        if tuple(self.delta_knobs) != self.response.knob_names:
            raise ValueError("Recommendation knob order must match response columns")
        if not np.isfinite(self.predicted_rms_mm):
            raise ValueError("Recommendation predicted RMS must be finite")
        object.__setattr__(self, "predicted_values_mm", predicted)
        object.__setattr__(self, "predicted_residual_values_mm", residual)
        object.__setattr__(self, "valid", valid)
        object.__setattr__(self, "singular_values", singular_values)

    @property
    def ready(self) -> bool:
        return bool(
            np.any(self.valid)
            and self.delta_knobs
            and any(abs(value) > 0 for value in self.delta_knobs.values())
        )


@dataclass(frozen=True)
class ModelResponseResult:
    section_id: str
    observable_names: tuple[str, ...]
    observable_elements: tuple[str, ...]
    observable_components: tuple[str, ...]
    observable_units: tuple[str, ...]
    device_names: tuple[str, ...]
    selected_values: ArrayLike
    target_values: ArrayLike
    design_reference_values: ArrayLike
    selected_curve: "ModelOpticsCurve"
    design_reference_curve: "ModelOpticsCurve"
    selected_k1: dict[str, float]
    design_k1: dict[str, float]
    design_reference_deltas: dict[str, float]
    model_source: str = "design"
    design_curve: "ModelOpticsCurve | None" = None
    snapshot_metadata: dict[str, Any] | None = None
    entrance_condition: str = ""

    def __post_init__(self) -> None:
        selected = np.asarray(self.selected_values, dtype=float)
        target = np.asarray(self.target_values, dtype=float)
        design_reference = np.asarray(self.design_reference_values, dtype=float)
        row_count = len(self.observable_names)
        metadata_lengths = {
            len(self.observable_elements),
            len(self.observable_components),
            len(self.observable_units),
        }
        if metadata_lengths != {row_count}:
            raise ValueError("Model observable metadata lengths must match")
        if selected.shape != target.shape or selected.shape != design_reference.shape:
            raise ValueError("Selected, target, and design-reference values must match")
        if selected.shape != (row_count,):
            raise ValueError("Model values must match observable_names")
        device_names = set(self.device_names)
        if set(self.selected_k1) != device_names:
            raise ValueError("Selected K1 values must match device_names")
        if set(self.design_k1) != device_names:
            raise ValueError("Design K1 values must match device_names")
        if set(self.design_reference_deltas) != device_names:
            raise ValueError("Design-reference deltas must match device_names")
        object.__setattr__(self, "selected_values", selected)
        object.__setattr__(self, "target_values", target)
        object.__setattr__(self, "design_reference_values", design_reference)

    @property
    def baseline_values(self) -> ArrayLike:
        """Compatibility alias for the selected model values."""
        return self.selected_values

    @property
    def preview_values(self) -> ArrayLike:
        """Compatibility alias for the design-reference values."""
        return self.design_reference_values

    @property
    def baseline_curve(self) -> "ModelOpticsCurve":
        """Compatibility alias for the selected model curve."""
        return self.selected_curve

    @property
    def preview_curve(self) -> "ModelOpticsCurve":
        """Compatibility alias for the design-reference curve."""
        return self.design_reference_curve

    @property
    def residual_values(self) -> ArrayLike:
        return self.selected_values - self.target_values

    @property
    def design_reference_residual_values(self) -> ArrayLike:
        return self.design_reference_values - self.target_values

    @property
    def selected_rms(self) -> float:
        return float(np.sqrt(np.mean(self.residual_values**2)))

    @property
    def design_reference_rms(self) -> float:
        return float(np.sqrt(np.mean(self.design_reference_residual_values**2)))

    @property
    def preview_residual_values(self) -> ArrayLike:
        """Compatibility alias for design_reference_residual_values."""
        return self.design_reference_residual_values

    @property
    def baseline_rms(self) -> float:
        """Compatibility alias for selected_rms."""
        return self.selected_rms

    @property
    def preview_rms(self) -> float:
        """Compatibility alias for design_reference_rms."""
        return self.design_reference_rms


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
    measurement_before: DispersionMeasurement | None = None
    measurement_after: DispersionMeasurement | None = None
    knobs_before: dict[str, float] | None = None
    knobs_trial: dict[str, float] | None = None
    device_values_before: dict[str, float] | None = None
    device_values_trial: dict[str, float] | None = None
    restored: bool = False


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
    def reduction_percent(self) -> float:
        initial = self.initial.rms_mm
        final = self.final.rms_mm
        if not np.isfinite(initial) or initial <= 0 or not np.isfinite(final):
            return float("nan")
        return float(100.0 * (1.0 - final / initial))

    @property
    def knob_delta(self) -> dict[str, float]:
        return {
            name: self.final_knobs.get(name, 0.0) - value
            for name, value in self.initial_knobs.items()
        }


def as_float_mapping(values: Mapping[str, Any]) -> dict[str, float]:
    return {str(key): float(value) for key, value in values.items()}
