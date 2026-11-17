"""Explicit inputs for the first uncoupled transverse shadow calculation."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import math
from typing import Mapping


def positive(value, label):
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a number")
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label} must be positive and finite")
    return value


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    return value.strip()


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Capture timestamps must include a time zone")
    return parsed.timestamp()


@dataclass(frozen=True)
class Diagnostic:
    element: str
    channel: str
    unit: str
    width_definition: str

    @property
    def key(self):
        return f"{self.element}/{self.channel}"


@dataclass(frozen=True)
class ShadowRequest:
    model_line: str
    reference_element: str
    reference_edge: str
    energy_mev: float
    x_moments: tuple[float, float, float]
    y_moments: tuple[float, float, float]
    beam_basis: str
    beam_provenance: str
    diagnostics: tuple[Diagnostic, ...]
    model_backend: str = "simulation"
    max_capture_duration_s: float = 5.0
    diagnostic_max_age_s: float = 5.0
    model_timeout_s: float = 30.0
    fitted_diagnostics: tuple[str, ...] = ()

    def __post_init__(self):
        for name in ("model_line", "reference_element", "model_backend", "beam_provenance"):
            object.__setattr__(self, name, text(getattr(self, name), name))
        if self.reference_edge not in {"entrance", "exit"}:
            raise ValueError("reference_edge must be entrance or exit")
        if self.beam_basis not in {"assumed", "independent_measurement"}:
            raise ValueError("beam_basis must be assumed or independent_measurement")
        for name in ("energy_mev", "max_capture_duration_s", "diagnostic_max_age_s", "model_timeout_s"):
            object.__setattr__(self, name, positive(getattr(self, name), name))
        for name in ("x_moments", "y_moments"):
            moments = getattr(self, name)
            if len(moments) != 3 or any(isinstance(v, bool) or not math.isfinite(v) for v in moments):
                raise ValueError("Beam moments must contain three finite numbers in SI units")
            a, b, c = moments
            if a <= 0 or c <= 0 or a * c - b * b <= 0:
                raise ValueError("Beam covariance must be positive definite")
            object.__setattr__(self, name, tuple(float(value) for value in moments))
        if not isinstance(self.fitted_diagnostics, (tuple, list)) or any(not isinstance(key, str) for key in self.fitted_diagnostics):
            raise ValueError("fitted_diagnostics must be an array of diagnostic keys")
        object.__setattr__(self, "fitted_diagnostics", tuple(self.fitted_diagnostics))
        if not self.diagnostics or len({d.key for d in self.diagnostics}) != len(self.diagnostics):
            raise ValueError("Select at least one unique diagnostic")
        for diagnostic in self.diagnostics:
            text(diagnostic.element, "diagnostic element")
            if diagnostic.channel not in {"sigx", "sigy"}:
                raise ValueError("Only sigx/sigy diagnostics are supported")
            if diagnostic.unit not in {"m", "mm"} or diagnostic.width_definition != "rms":
                raise ValueError("Diagnostics must explicitly declare RMS widths in m or mm")
            if diagnostic.key in self.fitted_diagnostics:
                raise ValueError(f"{diagnostic.key} was used to fit the beam input; select an independent diagnostic")

    @classmethod
    def from_dict(cls, payload: Mapping):
        if payload.get("schema") != "digital_shadow_request_v1":
            raise ValueError("Unsupported shadow request schema")
        beam = payload["beam"]
        return cls(
            model_line=payload["model_line"],
            model_backend=payload.get("model_backend", "simulation"),
            reference_element=payload["reference_element"],
            reference_edge=payload["reference_edge"],
            energy_mev=positive(beam["energy_mev"], "energy_mev"),
            x_moments=tuple(beam["x_moments"]), y_moments=tuple(beam["y_moments"]),
            beam_basis=beam["basis"], beam_provenance=beam["provenance"],
            fitted_diagnostics=beam.get("fitted_diagnostics", ()),
            diagnostics=tuple(Diagnostic(**item) for item in payload["diagnostics"]),
            **{key: positive(payload.get(key, default), key) for key, default in (
                ("max_capture_duration_s", 5), ("diagnostic_max_age_s", 5), ("model_timeout_s", 30),
            )},
        )

    def as_metadata(self):
        return {"schema": "digital_shadow_request_v1", "model_backend": self.model_backend,
                "model_line": self.model_line, "reference_element": self.reference_element,
                "reference_edge": self.reference_edge,
                "beam": {"energy_mev": self.energy_mev, "x_moments": self.x_moments,
                         "y_moments": self.y_moments, "basis": self.beam_basis,
                         "provenance": self.beam_provenance, "fitted_diagnostics": self.fitted_diagnostics},
                "diagnostics": [asdict(item) for item in self.diagnostics],
                "max_capture_duration_s": self.max_capture_duration_s,
                "diagnostic_max_age_s": self.diagnostic_max_age_s, "model_timeout_s": self.model_timeout_s}
