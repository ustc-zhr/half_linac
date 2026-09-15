"""Immutable, PV-independent observation data for the VM workbench."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from half_linac.src.shared.runtime_state import write_runtime_state


def input_version(state):
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


def observation_dir(runtime_json):
    return Path(runtime_json).parent / '.vm-workbench'


def occurrences(state):
    result, position = [], 0.0
    for index, name in enumerate(state['usedline']):
        element = state['lattice'][name]
        length = float(element.get('L', 0))
        result.append(dict(index=index, name=name, kind=element['TYPE'],
                           s=position, length=length, parameters=element))
        position += length
    return result


def columns(path, names, *, cache=None):
    from half_linac.src.shared.elegant_backend.parser import _new_legacy_sdds_dataset
    if not Path(path).is_file():
        raise ValueError(f'Missing output: {Path(path).name}')
    table = cache.get(path) if cache is not None else None
    if table is None:
        dataset = _new_legacy_sdds_dataset()
        dataset.load(str(path))
        table = dict(zip(dataset.columnName, dataset.columnData))
        # The legacy binding reuses SDDS index 0. Cache Python data, not handles.
        del dataset
        if cache is not None:
            cache[path] = table
    values = []
    for name in names:
        if name not in table:
            raise ValueError(f'Missing column: {name}')
        pages = table[name]
        if not len(pages):
            raise ValueError('Empty output')
        array = np.asarray(pages[-1], dtype=float)
        if array.ndim != 1 or not len(array) or not np.all(np.isfinite(array)):
            raise ValueError(f'Invalid or empty column: {name}')
        values.append(array)
    if len({len(value) for value in values}) != 1:
        raise ValueError('Mismatched column lengths')
    return values


def collect_results(state, elegant_dir, *, newer_than=0):
    """Read only outputs produced by this run, then serialize bounded plot data."""
    root = Path(elegant_dir)
    result = dict(elements=occurrences(state), curves={}, screens={})
    cache = {}

    def read(path, names, *, cached=True):
        if path.is_file() and path.stat().st_mtime < newer_than:
            raise ValueError(f'Output was not refreshed: {path.name}')
        return columns(path, names, cache=cache if cached else None)

    for label, suffix, names, scale, unit in (
        ('Orbit', 'cen', ('s', 'Cx', 'Cy'), 1000, 'mm'),
        ('Twiss', 'twi', ('s', 'betax', 'betay'), 1, 'm'),
        ('Tracked Twiss', 'sig', ('s', 'betaxBeam', 'betayBeam'), 1, 'm'),
        ('Dispersion', 'twi', ('s', 'etax', 'etay'), 1, 'm'),
        ('Beam Size', 'sig', ('s', 'Sx', 'Sy'), 1000, 'mm'),
        ('Orbit Angle', 'cen', ('s', 'Cxp', 'Cyp'), 1000, 'mrad'),
        ('RMS Divergence', 'sig', ('s', 'Sxp', 'Syp'), 1000, 'mrad'),
        ('Normalized Emittance', 'sig', ('s', 'enx', 'eny'), 1e6, 'mm·mrad'),
        ('Geometric Emittance', 'sig', ('s', 'ex', 'ey'), 1e6, 'mm·mrad'),
        ('Normalized Emittance (corrected)', 'sig', ('s', 'ecnx', 'ecny'), 1e6, 'mm·mrad'),
        ('Geometric Emittance (corrected)', 'sig', ('s', 'ecx', 'ecy'), 1e6, 'mm·mrad'),
    ):
        try:
            s, x, y = read(root / f'one.{suffix}', names)
            result['curves'][label] = dict(s=s.tolist(), x=(x*scale).tolist(),
                                           y=(y*scale).tolist(), unit=unit)
        except Exception as exc:
            result['curves'][label] = dict(error=str(exc))
    for label, suffix, names, unit in (
        ('Kinetic Energy', 'cen', ('s', 'pCentral', 'Cdelta'), 'MeV'),
        ('RMS Bunch Length', 'sig', ('s', 'St'), 'ps'),
        ('Relative Momentum Spread', 'sig', ('s', 'Sdelta'), '%'),
        ('Transmission', 'cen', ('s', 'Particles'), '%'),
    ):
        try:
            s, *values = read(root / f'one.{suffix}', names)
            value = values[0]
            if label == 'Kinetic Energy':
                momentum = value * (1 + values[1])
                if np.any(momentum <= 0):
                    raise ValueError('Nonpositive mean momentum')
                # Electron kinetic energy evaluated at the mean momentum.
                value = 0.51099895 * (np.sqrt(1 + momentum**2) - 1)
            elif label == 'RMS Bunch Length':
                value = value * 1e12
            elif label == 'Relative Momentum Spread':
                cen_s, delta = read(root / 'one.cen', ('s', 'Cdelta'))
                if not np.array_equal(s, cen_s):
                    raise ValueError('Sigma and centroid positions do not match')
                if np.any(1 + delta <= 0):
                    raise ValueError('Nonpositive mean momentum')
                value = 100 * value / (1 + delta)
            else:
                if value[0] <= 0:
                    raise ValueError('No entrance particles')
                value = 100 * value / value[0]
            if not np.all(np.isfinite(value)):
                raise ValueError('Nonfinite derived values')
            result['curves'][label] = dict(s=s.tolist(), x=value.tolist(), unit=unit,
                                           labels=[label])
        except Exception as exc:
            result['curves'][label] = dict(error=str(exc))
    # Release statistics before reading the larger, uncached particle screen files.
    cache.clear()
    for item in result['elements']:
        element = item['parameters']
        if item['kind'].upper() != 'WATCH':
            continue
        if str(element.get('MODE', 'coordinate')).strip('"').lower() not in ('coord', 'coordinate', 'coordinates'):
            continue
        key = str(item['index'])
        screen = dict(name=item['name'], s=item['s'])
        result['screens'][key] = screen
        try:
            if state['usedline'].count(item['name']) > 1:
                raise ValueError('Repeated WATCH: output cannot be assigned to one occurrence')
            if str(element.get('DISABLE', '0')) not in ('0', '0.0'):
                raise ValueError('WATCH is disabled')
            filename = str(element.get('FILENAME', f"{item['name']}.out")).strip('"').replace('%s', 'one')
            x, y = read(root / filename, ('x', 'y'), cached=False)
            x, y = x*1000, y*1000
            hist, xe, ye = np.histogram2d(x, y, bins=100)
            screen.update(image=hist.T.tolist(), extent=[xe[0], xe[-1], ye[0], ye[-1]],
                          cx=float(x.mean()), cy=float(y.mean()), sx=float(x.std()), sy=float(y.std()))
        except Exception as exc:
            screen['error'] = str(exc)
    return result


def tracked_twiss(curves):
    """Derive projected RMS beta on demand from existing tracking moments."""
    try:
        size = curves.get('Beam Size', {'error': 'No beam size result'})
        emit = curves.get('Geometric Emittance', {'error': 'No emittance result'})
        for curve in (size, emit):
            if 'error' in curve:
                raise ValueError(curve['error'])
        if size['unit'] != 'mm' or emit['unit'] != 'mm·mrad':
            raise ValueError('Unexpected moment units')
        if not np.array_equal(size['s'], emit['s']):
            raise ValueError('Beam size and emittance positions do not match')
        result = dict(s=size['s'], unit='m', labels=['βx', 'βy'])
        for plane in ('x', 'y'):
            sigma = np.asarray(size[plane], dtype=float) * 1e-3
            epsilon = np.asarray(emit[plane], dtype=float) * 1e-6
            if sigma.shape != epsilon.shape or sigma.shape != (len(size['s']),):
                raise ValueError('Mismatched moment lengths')
            if not len(sigma) or np.any(epsilon <= 0) or np.any(sigma < 0):
                raise ValueError('Invalid beam size or nonpositive emittance')
            with np.errstate(over='ignore', invalid='ignore', divide='ignore'):
                beta = sigma**2 / epsilon
            if not np.all(np.isfinite(beta)):
                raise ValueError('Nonfinite tracked Twiss values')
            result[plane] = beta.tolist()
        return result
    except (KeyError, TypeError, ValueError) as exc:
        return dict(error=str(exc))


def save_observation(path, data):
    write_runtime_state(path, data)
