"""Convert one forward, equal-weight electron bunch to or from ELEGANT SDDS."""

from pathlib import Path
from threading import Lock

import numpy as np

from ..beam_matrix import ELECTRON_REST_ENERGY_MEV
from ..digital_shadow.beam_state import BeamParticles, BeamReference, BeamState6D, C_M_S
from .parser import _new_legacy_sdds_dataset


_COLUMNS = (("x", "m"), ("xp", ""), ("y", "m"), ("yp", ""), ("t", "s"), ("p", "m$be$nc"))
# The legacy binding assigns the same dataset ID to each conversion in this module.
_SDDS_LOCK = Lock()


def _time_origin(reference, time_offset_s):
    origin = reference.t_ref_s + time_offset_s
    if not np.isfinite(origin):
        raise ValueError("ELEGANT reference time must be finite")
    return origin


def write_elegant_beam(state: BeamState6D, path: str | Path, *, time_offset_s: float = 0.0) -> Path:
    """Write the native particle coordinates. The caller supplies tracking charge separately."""
    beam = state.payload
    if not isinstance(beam, BeamParticles):
        raise ValueError("ELEGANT particle input requires particles")
    if not np.all(beam.weights == beam.weights[0]):
        raise ValueError("ELEGANT particle input requires equal weights")
    reference = state.reference
    x, ux, y, uy, tau, delta = beam.coordinates.T
    magnitude = 1 + delta
    transverse = np.hypot(ux, uy)
    p_s = np.sqrt(magnitude - transverse) * np.sqrt(magnitude + transverse)
    coordinates = (
        x, ux / p_s, y, uy / p_s, _time_origin(reference, time_offset_s) + tau / C_M_S,
        magnitude * (reference.p_ref_mev_c / ELECTRON_REST_ENERGY_MEV),
    )
    if not np.all(np.isfinite(coordinates)) or np.any(coordinates[-1] <= 0):
        raise ValueError("Invalid ELEGANT particle coordinates")
    path = Path(path)
    with _SDDS_LOCK:
        dataset = _new_legacy_sdds_dataset()
        dataset.mode = dataset.SDDS_BINARY
        for (name, unit), values in zip(_COLUMNS, coordinates):
            dataset.defineColumn(name, units=unit, type=dataset.SDDS_DOUBLE)
            dataset.setColumnValueLists(name, [values.tolist()])
        if beam.particle_ids is not None:
            dataset.defineColumn("particleID", type=dataset.SDDS_ULONG64)
            dataset.setColumnValueLists("particleID", [beam.particle_ids.tolist()])
        dataset.save(str(path))
    return path


def read_elegant_particles(
    path: str | Path, reference: BeamReference, *, time_offset_s: float = 0.0,
    expected_step: int | None = None, expected_pass: int | None = None,
) -> BeamParticles:
    """Read one page from an equal-weight source at the caller's verified reference plane."""
    with _SDDS_LOCK:
        dataset = _new_legacy_sdds_dataset()
        dataset.load(str(path))
        columns = dict(zip(dataset.columnName, zip(dataset.columnDefinition, dataset.columnData)))
        parameters = dict(zip(dataset.parameterName, dataset.parameterData))
    for name, expected in (("Step", expected_step), ("Pass", expected_pass)):
        if expected is not None and list(parameters.get(name, ())) != [expected]:
            raise ValueError(f"ELEGANT particle output must contain the requested {name}={expected}")
    values = []
    for name, unit in _COLUMNS:
        if name not in columns or columns[name][0][1] != unit or len(columns[name][1]) != 1:
            raise ValueError(f"ELEGANT particle output requires one page and column {name} in {unit or 'slope units'}")
        values.append(columns[name][1][0])
    x, xp, y, yp, times, momenta = np.asarray(values, dtype=np.float64)
    if not len(x):
        raise ValueError("ELEGANT particle output has no survivors")
    if np.any(momenta <= 0):
        raise ValueError("ELEGANT particle momentum must be positive")
    magnitude = momenta * (ELECTRON_REST_ENERGY_MEV / reference.p_ref_mev_c)
    p_s = magnitude / np.hypot(1, np.hypot(xp, yp))
    coordinates = np.column_stack((
        x, xp * p_s, y, yp * p_s, C_M_S * (times - _time_origin(reference, time_offset_s)), magnitude - 1,
    ))
    particle_ids = None
    if "particleID" in columns:
        pages = columns["particleID"][1]
        if len(pages) != 1 or any(type(value) is not int or not 0 < value < 2**64 for value in pages[0]):
            raise ValueError("ELEGANT particle IDs must be positive uint64 values on one page")
        particle_ids = np.asarray(pages[0], dtype=np.uint64)
    return BeamParticles(coordinates, np.ones(len(x)), particle_ids)
