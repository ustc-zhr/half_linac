"""Three-BPM transport reconstruction used by energy diagnostics."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping, Sequence

import numpy as np


def _matrix_tuple(matrix: Sequence[Sequence[float]], name: str) -> tuple[tuple[float, ...], ...]:
    array = np.asarray(matrix, dtype=float)
    if array.shape != (6, 6):
        raise ValueError(f"{name} must be a 6x6 transfer matrix")
    if not np.isfinite(array).all():
        raise ValueError(f"{name} contains non-finite values")
    return tuple(tuple(float(value) for value in row) for row in array)


@dataclass(frozen=True)
class BpmTransportModel:
    """HLS three-BPM energy model using exit-to-exit transfer matrices.

    BPM positions and offsets are expressed in millimetres.  The coefficients
    are therefore multiplied by ``1e-3`` when calculating relative momentum
    deviation.
    """

    bpm_ids: tuple[str, str, str]
    offsets_mm: tuple[float, float, float]
    m1: tuple[tuple[float, ...], ...]
    m2: tuple[tuple[float, ...], ...]
    coefficients_per_m: tuple[float, float, float]
    coordinate_definition: str = "exit2exit"

    @classmethod
    def from_matrices(
        cls,
        bpm_ids: Sequence[str],
        offsets_mm: Sequence[float],
        m1: Sequence[Sequence[float]],
        m2: Sequence[Sequence[float]],
        *,
        min_abs_r12_m: float = 1e-6,
        min_abs_r16_m: float = 1e-6,
    ) -> "BpmTransportModel":
        if len(bpm_ids) != 3 or len(set(bpm_ids)) != 3:
            raise ValueError("bpm_ids must contain three distinct BPM names")
        if len(offsets_mm) != 3:
            raise ValueError("offsets_mm must contain exactly three values")
        offsets = tuple(float(value) for value in offsets_mm)
        if not all(math.isfinite(value) for value in offsets):
            raise ValueError("BPM offsets must be finite")

        m1_tuple = _matrix_tuple(m1, "M1")
        m2_tuple = _matrix_tuple(m2, "M2")
        a = np.asarray(m1_tuple)
        b = np.asarray(m2_tuple)
        r12 = float(a[0, 1])
        r16 = float(b[0, 5])
        if abs(r12) <= float(min_abs_r12_m):
            raise ValueError(
                f"Degenerate M1: |R12|={abs(r12):.6g} m must exceed "
                f"{float(min_abs_r12_m):.6g} m"
            )
        if abs(r16) <= float(min_abs_r16_m):
            raise ValueError(
                f"Degenerate M2: |R16|={abs(r16):.6g} m must exceed "
                f"{float(min_abs_r16_m):.6g} m"
            )

        # Keep the full 2x2 determinant.  The measured line is not assumed to
        # be exactly symplectic after numeric/model processing.
        determinant = float(a[0, 0] * a[1, 1] - a[0, 1] * a[1, 0])
        coefficients = (
            float(b[0, 1] * determinant / (r12 * r16)),
            float(-(b[0, 0] * r12 + b[0, 1] * a[1, 1]) / (r16 * r12)),
            float(1.0 / r16),
        )
        if not all(math.isfinite(value) for value in coefficients):
            raise ValueError("Calculated BPM energy coefficients are non-finite")

        return cls(
            bpm_ids=tuple(str(value) for value in bpm_ids),  # type: ignore[arg-type]
            offsets_mm=offsets,  # type: ignore[arg-type]
            m1=m1_tuple,
            m2=m2_tuple,
            coefficients_per_m=coefficients,
        )

    def relative_momentum_deviation(self, positions_mm: Sequence[float]) -> float:
        if len(positions_mm) != 3:
            raise ValueError("positions_mm must contain exactly three values")
        positions = tuple(float(value) for value in positions_mm)
        if not all(math.isfinite(value) for value in positions):
            raise ValueError("BPM positions must be finite")
        return 1e-3 * sum(
            coefficient * (position - offset)
            for coefficient, position, offset in zip(
                self.coefficients_per_m, positions, self.offsets_mm
            )
        )

    def energy_mev(
        self, positions_mm: Sequence[float], reference_energy_mev: float
    ) -> tuple[float, float, float]:
        reference = float(reference_energy_mev)
        if not math.isfinite(reference):
            raise ValueError("reference_energy_mev must be finite")
        delta = self.relative_momentum_deviation(positions_mm)
        delta_energy = reference * delta
        return delta, delta_energy, reference + delta_energy

    def as_metadata(self) -> dict[str, object]:
        return {
            "bpm_ids": list(self.bpm_ids),
            "offsets_mm": dict(zip(self.bpm_ids, self.offsets_mm)),
            "coordinate_definition": self.coordinate_definition,
            "M1": [list(row) for row in self.m1],
            "M2": [list(row) for row in self.m2],
            "coefficients_per_m": dict(zip(self.bpm_ids, self.coefficients_per_m)),
        }


@dataclass(frozen=True)
class BpmEnergySample:
    """One time-aligned three-BPM energy sample and its quality result."""

    sampled_at: float
    source_timestamp: float | None
    bpm_values_mm: Mapping[str, float | None]
    bpm_source_timestamps: Mapping[str, float | None]
    timestamp_skew_s: float | None
    reference_energy_mev: float | None
    delta: float | None
    delta_energy_mev: float | None
    energy_raw_mev: float | None
    energy_filtered_mev: float | None
    k1_values_per_m2: Mapping[str, float | None]
    quality_state: str
    quality_reason: str
    raw_pv_values: Mapping[str, float | None] = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        return self.quality_state == "OK"
