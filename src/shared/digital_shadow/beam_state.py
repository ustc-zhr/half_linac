"""File exchange for a forward electron bunch at a fixed reference plane."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import math
from numbers import Real
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory

import numpy as np

from ..beam_matrix import ELECTRON_REST_ENERGY_MEV
from ..runtime_state import read_runtime_state, write_runtime_state
from .models import text


SCHEMA = "beam_state_6d_v1"
FRAME = "forward_electron_reference_plane_v1"
COORDINATES = ("x", "ux", "y", "uy", "tau", "delta")
UNITS = ("m", "1", "m", "1", "m", "1")
C_M_S = 299_792_458.0
ELECTRON_MASS_KG = 9.1093837139e-31
ELECTRON_CHARGE_C = -1.602176634e-19


def _check_sha256(value, label):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    return float(value)


def _charge(value, label):
    charge = _number(value, label)
    if charge > 0:
        raise ValueError(f"{label} must be signed electron charge in coulombs")
    return charge


def _finite_array(value, label):
    array = np.asarray(value)
    if array.dtype.kind not in "iuf":
        raise ValueError(f"{label} must contain finite numbers")
    with np.errstate(over="ignore"):
        array = np.asarray(array, dtype=np.float64)
    if not np.all(np.isfinite(array)):
        raise ValueError(f"{label} must contain finite numbers")
    return array


def _immutable(array):
    return np.frombuffer(array.tobytes(), dtype=array.dtype).reshape(array.shape)


@dataclass(frozen=True)
class BeamBoundary:
    boundary_id: str
    machine_id: str
    lattice_sha256: str
    line: str
    element: str
    occurrence: int
    edge: str

    def __post_init__(self):
        for name in ("boundary_id", "machine_id", "line", "element"):
            object.__setattr__(self, name, text(getattr(self, name), name))
        _check_sha256(self.lattice_sha256, "lattice_sha256")
        if type(self.occurrence) is not int or self.occurrence < 1:
            raise ValueError("occurrence must be a one-based integer")
        if self.edge not in ("entrance", "exit"):
            raise ValueError("edge must be entrance or exit")


def resolve_boundary(boundary_id, bindings, usedline, *, machine_id, lattice_sha256, line):
    """Return the slice index of the next element at an explicitly bound plane."""
    boundary = bindings.get(boundary_id)
    if boundary is None:
        raise ValueError(f"unresolved_boundary: {boundary_id}")
    if (boundary.boundary_id, boundary.machine_id, boundary.lattice_sha256, boundary.line) != (
        boundary_id, machine_id, lattice_sha256, line,
    ):
        raise ValueError(f"Boundary {boundary_id} differs from the selected machine, lattice or line")
    matches = [index for index, name in enumerate(usedline) if name == boundary.element]
    if len(matches) < boundary.occurrence:
        raise ValueError(f"Boundary {boundary_id} has no model occurrence {boundary.occurrence}")
    return matches[boundary.occurrence - 1] + (boundary.edge == "exit")


@dataclass(frozen=True)
class RFReference:
    rf_id: str
    frequency_hz: float
    phase_rad: float | None
    calibration_version: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "rf_id", text(self.rf_id, "rf_id"))
        frequency = _number(self.frequency_hz, "frequency_hz")
        if frequency <= 0:
            raise ValueError("frequency_hz must be positive")
        object.__setattr__(self, "frequency_hz", frequency)
        if self.calibration_version is not None:
            object.__setattr__(self, "calibration_version", text(self.calibration_version, "RF calibration_version"))
        if self.phase_rad is not None:
            phase = _number(self.phase_rad, "phase_rad")
            if not -math.pi <= phase < math.pi:
                raise ValueError("phase_rad must be in [-pi, pi)")
            text(self.calibration_version, "RF calibration_version")
            object.__setattr__(self, "phase_rad", phase)


@dataclass(frozen=True)
class BeamReference:
    p_ref_mev_c: float
    t_ref_s: float
    clock_id: str
    bunch_id: str
    basis: str
    rf: tuple[RFReference, ...] = ()

    def __post_init__(self):
        momentum = _number(self.p_ref_mev_c, "p_ref_mev_c")
        if momentum <= 0:
            raise ValueError("p_ref_mev_c must be positive")
        object.__setattr__(self, "p_ref_mev_c", momentum)
        object.__setattr__(self, "t_ref_s", _number(self.t_ref_s, "t_ref_s"))
        for name in ("clock_id", "bunch_id", "basis"):
            object.__setattr__(self, name, text(getattr(self, name), name))
        rf = tuple(self.rf)
        if any(not isinstance(item, RFReference) for item in rf) or len({item.rf_id for item in rf}) != len(rf):
            raise ValueError("RF references must have unique IDs")
        object.__setattr__(self, "rf", rf)

    @property
    def kinetic_energy_mev(self):
        return math.hypot(self.p_ref_mev_c, ELECTRON_REST_ENERGY_MEV) - ELECTRON_REST_ENERGY_MEV


@dataclass(frozen=True)
class BeamProvenance:
    source_kind: str
    detail: str
    model_version: str | None = None
    calibration_version: str | None = None
    snapshot_id: str | None = None
    snapshot_sha256: str | None = None
    stage: str | None = None

    def __post_init__(self):
        if self.source_kind not in ("engine", "surrogate", "external_file", "synthetic", "measurement"):
            raise ValueError("Unsupported beam source_kind")
        object.__setattr__(self, "detail", text(self.detail, "beam source detail"))
        for name in ("model_version", "calibration_version", "stage"):
            if getattr(self, name) is not None:
                object.__setattr__(self, name, text(getattr(self, name), name))
        if self.source_kind in ("engine", "surrogate"):
            text(self.model_version, "model_version")
        if self.snapshot_id is not None or self.snapshot_sha256 is not None:
            object.__setattr__(self, "snapshot_id", text(self.snapshot_id, "snapshot_id"))
            _check_sha256(self.snapshot_sha256, "snapshot_sha256")


@dataclass(frozen=True)
class BeamLoss:
    """A measured or tracked loss summary for a segment without resampling."""

    input_boundary_id: str
    input_count: int
    surviving_count: int
    input_charge_c: float
    surviving_charge_c: float
    basis: str

    def __post_init__(self):
        object.__setattr__(self, "input_boundary_id", text(self.input_boundary_id, "input_boundary_id"))
        object.__setattr__(self, "basis", text(self.basis, "loss basis"))
        if (
            type(self.input_count) is not int or type(self.surviving_count) is not int
            or self.input_count <= 0 or not 0 <= self.surviving_count <= self.input_count
        ):
            raise ValueError("Invalid loss counts")
        incoming = _charge(self.input_charge_c, "input_charge_c")
        surviving = _charge(self.surviving_charge_c, "surviving_charge_c")
        if (
            surviving < incoming or (self.surviving_count == 0 and surviving != 0)
            or (self.surviving_count == self.input_count and surviving != incoming)
        ):
            raise ValueError("Loss counts and charge are inconsistent")
        object.__setattr__(self, "input_charge_c", incoming)
        object.__setattr__(self, "surviving_charge_c", surviving)

    @property
    def lost_charge_c(self):
        return self.surviving_charge_c - self.input_charge_c

    @property
    def loss_fraction(self):
        if self.input_charge_c == 0:
            return None
        return self.lost_charge_c / abs(self.input_charge_c)


@dataclass(frozen=True, eq=False)
class BeamMoments:
    mean: np.ndarray
    covariance: np.ndarray

    def __post_init__(self):
        mean = _finite_array(self.mean, "Beam mean")
        covariance = _finite_array(self.covariance, "Beam covariance")
        if mean.shape != (6,) or covariance.shape != (6, 6):
            raise ValueError("Beam moments require a 6-vector and 6x6 covariance")
        diagonal = covariance.diagonal()
        active = diagonal > 0
        if np.any(diagonal < 0) or np.any(covariance[~active, :]) or np.any(covariance[:, ~active]):
            raise ValueError("Beam covariance must be positive semidefinite")
        scale = np.sqrt(diagonal[active])
        with np.errstate(over="ignore", invalid="ignore"):
            correlation = covariance[np.ix_(active, active)] / scale[:, None] / scale[None, :]
        if not np.all(np.isfinite(correlation)) or not np.allclose(
            correlation, correlation.T, rtol=0, atol=1e-12,
        ):
            raise ValueError("Beam covariance must be symmetric")
        if np.any(np.linalg.eigvalsh(correlation) < -1e-12):
            raise ValueError("Beam covariance must be positive semidefinite")
        object.__setattr__(self, "mean", _immutable(mean))
        object.__setattr__(self, "covariance", _immutable(covariance))


@dataclass(frozen=True, eq=False)
class BeamParticles:
    coordinates: np.ndarray
    weights: np.ndarray
    particle_ids: np.ndarray | None = None

    def __post_init__(self):
        coordinates = _finite_array(self.coordinates, "Particle coordinates")
        weights = _finite_array(self.weights, "Particle weights")
        if coordinates.ndim != 2 or coordinates.shape[1] != 6 or not len(coordinates):
            raise ValueError("Particles require a nonempty Nx6 coordinate array")
        if weights.shape != (len(coordinates),) or np.any(weights <= 0):
            raise ValueError("Particles require a positive weight per row")
        ux, uy, delta = coordinates[:, [1, 3, 5]].T
        if np.any(1 + delta <= np.hypot(ux, uy)):
            raise ValueError("Particles must have positive forward longitudinal momentum")
        if self.particle_ids is not None:
            ids = np.asarray(self.particle_ids)
            if (
                ids.dtype.kind not in "iu" or ids.shape != weights.shape
                or np.any(ids <= 0) or len(np.unique(ids)) != len(ids)
            ):
                raise ValueError("Particle IDs must be unique positive integers")
            object.__setattr__(self, "particle_ids", _immutable(ids.astype(np.uint64)))
        object.__setattr__(self, "coordinates", _immutable(coordinates))
        object.__setattr__(self, "weights", _immutable(weights))

    def moments(self):
        fractions = self.weights / self.weights.max()
        fractions /= fractions.sum()
        mean = fractions @ self.coordinates
        deviations = self.coordinates - mean
        covariance = (deviations.T * fractions) @ deviations
        return BeamMoments(mean, covariance)


@dataclass(frozen=True)
class BeamState6D:
    boundary: BeamBoundary
    reference: BeamReference
    provenance: BeamProvenance
    payload: BeamMoments | BeamParticles
    bunch_charge_c: float | None
    loss: BeamLoss | None = None

    def __post_init__(self):
        if not (
            isinstance(self.boundary, BeamBoundary) and isinstance(self.reference, BeamReference)
            and isinstance(self.provenance, BeamProvenance) and isinstance(self.payload, (BeamMoments, BeamParticles))
        ):
            raise ValueError("BeamState6D requires typed boundary, reference, provenance and payload")
        if self.bunch_charge_c is not None:
            object.__setattr__(self, "bunch_charge_c", _charge(self.bunch_charge_c, "bunch_charge_c"))
        if self.loss is not None:
            if not isinstance(self.loss, BeamLoss) or self.loss.surviving_count == 0:
                raise ValueError("A beam state requires survivors, even when loss is reported")
            if self.bunch_charge_c != self.loss.surviving_charge_c or (
                isinstance(self.payload, BeamParticles) and len(self.payload.coordinates) != self.loss.surviving_count
            ):
                raise ValueError("Beam payload differs from the loss summary")

    def as_moments(self):
        if isinstance(self.payload, BeamMoments):
            return self
        return replace(self, payload=self.payload.moments())


def save_beam_state(state: BeamState6D, path: str | Path) -> Path:
    """Publish the metadata after its immutable particle artifact is available."""
    path = Path(path)
    if path.suffix != ".json":
        raise ValueError("BeamState6D metadata path must end in .json")
    payload = state.payload
    if isinstance(payload, BeamParticles):
        path.parent.mkdir(parents=True, exist_ok=True)
        arrays = {"coordinates": payload.coordinates, "weights": payload.weights}
        if payload.particle_ids is not None:
            arrays["particle_ids"] = payload.particle_ids
        with TemporaryDirectory(dir=path.parent) as directory:
            staged = Path(directory) / "particles.npz"
            np.savez(staged, **arrays)
            with staged.open("rb") as stream:
                checksum = hashlib.file_digest(stream, "sha256").hexdigest()
            filename = f"particles-{checksum}.npz"
            os.replace(staged, path.parent / filename)
        data = {"format": "numpy_npz_v1", "path": filename, "sha256": checksum, "shape": list(payload.coordinates.shape)}
        representation = "particles"
    else:
        data = {"mean": payload.mean.tolist(), "covariance": payload.covariance.tolist()}
        representation = "moments"
    write_runtime_state(path, {
        "schema_version": SCHEMA,
        "frame": FRAME,
        "coordinates": COORDINATES,
        "units": UNITS,
        "species": "electron",
        "rest_mass_kg": ELECTRON_MASS_KG,
        "particle_charge_c": ELECTRON_CHARGE_C,
        "boundary": asdict(state.boundary),
        "reference": asdict(state.reference),
        "provenance": asdict(state.provenance),
        "bunch_charge_c": state.bunch_charge_c,
        "loss": asdict(state.loss) if state.loss is not None else None,
        "representation": representation,
        "data": data,
    })
    return path


def load_beam_state(path: str | Path) -> BeamState6D:
    state, _ = load_beam_state_archive(path)
    return state


def load_beam_state_archive(path: str | Path) -> tuple[BeamState6D, dict]:
    """Load a validated state and the metadata used to verify its source payload."""
    path = Path(path)
    record = read_runtime_state(path)
    if not isinstance(record, dict) or record.get("schema_version") != SCHEMA:
        raise ValueError("Unsupported BeamState6D schema")
    if (
        record.get("frame") != FRAME or record.get("coordinates") != list(COORDINATES)
        or record.get("units") != list(UNITS) or record.get("species") != "electron"
        or record.get("rest_mass_kg") != ELECTRON_MASS_KG or record.get("particle_charge_c") != ELECTRON_CHARGE_C
    ):
        raise ValueError("BeamState6D frame, units or species are incompatible")
    try:
        data = record["data"]
        if record["representation"] == "particles":
            payload = _load_particles(path.parent, data)
        elif record["representation"] == "moments":
            payload = BeamMoments(data["mean"], data["covariance"])
        else:
            raise ValueError("Unsupported BeamState6D representation")
        reference = dict(record["reference"])
        reference["rf"] = tuple(RFReference(**item) for item in reference["rf"])
        loss = record["loss"]
        state = BeamState6D(
            BeamBoundary(**record["boundary"]), BeamReference(**reference),
            BeamProvenance(**record["provenance"]), payload, record["bunch_charge_c"],
            BeamLoss(**loss) if loss is not None else None,
        )
        return state, record
    except (KeyError, TypeError) as exc:
        raise ValueError("Malformed BeamState6D metadata") from exc


def _load_particles(directory, data):
    checksum = data["sha256"]
    _check_sha256(checksum, "particle artifact SHA-256")
    artifact = directory / f"particles-{checksum}.npz"
    if data["format"] != "numpy_npz_v1" or data["path"] != artifact.name or artifact.is_symlink():
        raise ValueError("Invalid particle artifact reference")
    with artifact.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != checksum:
            raise ValueError("Particle artifact SHA-256 mismatch")
        stream.seek(0)
        with np.load(stream, allow_pickle=False) as arrays:
            coordinates = arrays["coordinates"]
            weights = arrays["weights"]
            ids = arrays.get("particle_ids")
    if coordinates.shape != tuple(data["shape"]) or coordinates.dtype.type != np.float64 or weights.dtype.type != np.float64:
        raise ValueError("Particle artifact has invalid shapes or dtypes")
    if ids is not None and ids.dtype.type != np.uint64:
        raise ValueError("Particle artifact IDs must be uint64")
    return BeamParticles(coordinates, weights, ids)
