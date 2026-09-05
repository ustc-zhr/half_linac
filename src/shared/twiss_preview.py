from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .machine_profile import (
    MachineProfile,
    build_model_backend,
    load_model_context,
)


@dataclass(frozen=True)
class TwissPreviewRow:
    element_name: str
    element_type: str
    element_length_m: float
    element_k1_m2: float
    s_m: float
    design: Mapping[str, float]
    target: Mapping[str, float]


@dataclass(frozen=True)
class TwissPreviewResult:
    machine_id: str
    line_name: str
    overrides: Mapping[str, Mapping[str, float]]
    rows: tuple[TwissPreviewRow, ...]

    @property
    def max_delta_beta_x(self) -> float:
        return max((abs(row.target["beta_x_m"] - row.design["beta_x_m"]) for row in self.rows), default=0.0)

    @property
    def max_delta_beta_y(self) -> float:
        return max((abs(row.target["beta_y_m"] - row.design["beta_y_m"]) for row in self.rows), default=0.0)

    @property
    def max_delta_eta_x(self) -> float:
        return max((abs(row.target["dx_m"] - row.design["dx_m"]) for row in self.rows), default=0.0)

    @property
    def max_delta_eta_y(self) -> float:
        return max((abs(row.target["dy_m"] - row.design["dy_m"]) for row in self.rows), default=0.0)


def _row_key(row: Mapping[str, object]) -> tuple[str, int]:
    return str(row["element_name"]), int(row.get("element_occurrence", 0))


def run_twiss_preview(
    backend,
    *,
    overrides: Mapping[str, Mapping[str, float]],
    machine_id: str = "",
) -> TwissPreviewResult:
    if not overrides:
        raise ValueError("Twiss preview requires at least one staged Target override.")
    first, last = backend.get_line_endpoints()
    design_rows = tuple(backend.get_optics_profile(first, last, twiss_only=True))
    target_rows = tuple(
        backend.get_optics_profile(
            first,
            last,
            lattice_overrides=overrides,
            twiss_only=True,
        )
    )
    design_by_key = {_row_key(row): row for row in design_rows}
    target_by_key = {_row_key(row): row for row in target_rows}
    rows: list[TwissPreviewRow] = []
    for key, design in design_by_key.items():
        target = target_by_key.get(key)
        if target is None:
            continue
        fields = ("beta_x_m", "beta_y_m", "dx_m", "dy_m")
        rows.append(
            TwissPreviewRow(
                element_name=key[0],
                element_type=str(target.get("element_type", "")),
                element_length_m=float(target.get("element_length_m", 0.0)),
                element_k1_m2=float(target.get("element_k1_m2", float("nan"))),
                s_m=float(target["s_m"]),
                design={field: float(design[field]) for field in fields},
                target={field: float(target[field]) for field in fields},
            )
        )
    if not rows:
        raise ValueError("Elegant returned no comparable Twiss rows.")
    return TwissPreviewResult(
        machine_id=machine_id,
        line_name=backend.line_name,
        overrides={key: dict(value) for key, value in overrides.items()},
        rows=tuple(rows),
    )


def build_twiss_preview(
    profile: MachineProfile,
    overrides: Mapping[str, Mapping[str, float]],
    *,
    model_backend: str = "simulation",
    line_name: str | None = None,
) -> TwissPreviewResult:
    context = load_model_context(profile.machine.id, model_backend=model_backend)
    backend = build_model_backend(context, line_name=line_name)
    return run_twiss_preview(backend, overrides=overrides, machine_id=profile.machine.id)
