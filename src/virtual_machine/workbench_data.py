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


def columns(path, names):
    from half_linac.src.shared.elegant_backend.parser import _new_legacy_sdds_dataset
    if not Path(path).is_file():
        raise ValueError(f'Missing output: {Path(path).name}')
    dataset = _new_legacy_sdds_dataset()
    dataset.load(str(path))
    values = []
    for name in names:
        if name not in dataset.columnName:
            raise ValueError(f'Missing column: {name}')
        pages = dataset.columnData[dataset.columnName.index(name)]
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

    def read(path, names):
        if path.is_file() and path.stat().st_mtime < newer_than:
            raise ValueError(f'Output was not refreshed: {path.name}')
        return columns(path, names)

    for label, suffix, names, scale, unit in (
        ('Orbit', 'cen', ('s', 'Cx', 'Cy'), 1000, 'mm'),
        ('Twiss', 'twi', ('s', 'betax', 'betay'), 1, 'm'),
        ('Dispersion', 'twi', ('s', 'etax', 'etay'), 1, 'm'),
        ('Beam Size', 'sig', ('s', 'Sx', 'Sy'), 1000, 'mm'),
    ):
        try:
            s, x, y = read(root / f'one.{suffix}', names)
            result['curves'][label] = dict(s=s.tolist(), x=(x*scale).tolist(),
                                           y=(y*scale).tolist(), unit=unit)
        except Exception as exc:
            result['curves'][label] = dict(error=str(exc))
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
            x, y = read(root / filename, ('x', 'y'))
            x, y = x*1000, y*1000
            hist, xe, ye = np.histogram2d(x, y, bins=100)
            screen.update(image=hist.T.tolist(), extent=[xe[0], xe[-1], ye[0], ye[-1]],
                          cx=float(x.mean()), cy=float(y.mean()), sx=float(x.std()), sy=float(y.std()))
        except Exception as exc:
            screen['error'] = str(exc)
    return result


def save_observation(path, data):
    write_runtime_state(path, data)
