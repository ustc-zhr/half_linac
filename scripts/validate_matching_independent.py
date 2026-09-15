#!/usr/bin/env python3
"""Validate matching against standalone full-line Elegant runs, without PVs.

Measurements and acceptance optics are read directly from separate Elegant
outputs; they never call matching transport/profile to create their reference.
"""
import argparse
from copy import deepcopy
from dataclasses import asdict
import json
import math
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from repo_bootstrap import ensure_repo_import_path
ensure_repo_import_path(__file__)
from scripts.check_emittance_matching import isolated_model
from half_linac.src.apps.emit_measure.matching import (
    Point, Twiss, MeasurementBaseline, MatchingRequest, MagnetLimit,
    solve_matching, mismatch, endpoint, compare_measurement,
)
from half_linac.src.shared.elegant_runtime import run_elegant_input
from half_linac.src.shared.machine_profile.model_backend import _load_sdds_columns

MASS = 0.51099895


def standalone(model, directory, overrides, points):
    directory.mkdir(parents=True, exist_ok=True)
    state = deepcopy(model.state)
    for name, values in overrides.items():
        state['lattice'][name].update(values)
    # Small, monoenergetic beam verifies particle tracking in the linear limit.
    # This is intentionally distinct from the finite-charge operational VM beam.
    for element in state['lattice'].values():
        if element['TYPE'].upper() == 'WATCH':
            element['DISABLE'] = '1'
        if element['TYPE'].upper() == 'CHARGE':
            element['TOTAL'] = '0'
        for key in ('ZWAKEFILE', 'TRWAKEFILE', 'WAKEFILE'):
            if key in element:
                element[key] = str((Path(__file__).resolve().parents[1] /
                    'src/virtual_machine/half_elegant/elegant' / element[key]).resolve())
    original = list(state['usedline'])
    inserts = {}
    for label, point in points.items():
        index = original.index(point.element) + (point.edge == 'exit')
        name = 'CHECK_' + label.upper()
        inserts.setdefault(index, []).append(name)
        state['lattice'][name] = {'NAME': name, 'TYPE': 'WATCH', 'MODE': 'coord',
                                  'FILENAME': label + '.out'}
    line = []
    for i in range(len(original) + 1):
        line.extend(inserts.get(i, []))
        if i < len(original):
            line.append(original[i])
    state['usedline'] = line
    control = state['control']
    control['bunched_beam'] = {'n_particles_per_bunch': '20000', 'emit_nx': '1e-7',
        'emit_ny': '1e-7', 'sigma_dp': '0', 'sigma_s': '0',
        **{k: control['twiss_output'][k] for k in ('beta_x', 'alpha_x', 'beta_y', 'alpha_y')}}
    path = directory / 'check.json'
    path.write_text(json.dumps(state, indent=2))
    model.backend._new_parser().json_to_lte_ele(directory / 'check.lte', directory / 'check.ele', path)
    run_elegant_input('check.ele', 'check.log', workdir=directory)
    columns = _load_sdds_columns(directory / 'check.twi',
        ('ElementName', 'betax', 'alphax', 'betay', 'alphay', 'pCentral0'))
    result = {}
    names = [str(n) for n in columns['ElementName']]
    for label in points:
        i = names.index('CHECK_' + label.upper())
        pc = float(columns['pCentral0'][i])
        planes = {p: Twiss(float(columns['beta' + p][i]), float(columns['alpha' + p][i]), 1e-7 / pc)
                  for p in ('x', 'y')}
        particles = _load_sdds_columns(directory / (label + '.out'), ('x', 'xp', 'y', 'yp'))
        measured = {}
        for p in ('x', 'y'):
            cov = np.cov([np.asarray(particles[p], dtype=float),
                          np.asarray(particles[p + 'p'], dtype=float)], bias=True)
            emit = math.sqrt(float(np.linalg.det(cov)))
            measured[p] = Twiss(float(cov[0, 0] / emit), float(-cov[0, 1] / emit), emit)
        result[label] = {'planes': planes, 'particles': measured,
                         'energy': MASS * (math.sqrt(1 + pc * pc) - 1)}
    return result


def run(output, selected_case=None):
    output.mkdir(parents=True, exist_ok=True)
    model = isolated_model(output)
    cases = [('ql', [f'QL{i:02d}' for i in range(7, 13)], Point('QL12', 'exit'), Point('QT02'), 'QL10', .15),
             ('qt', [f'QT{i:02d}' for i in range(1, 7)], Point('QT06', 'exit'), Point('QT06', 'exit'), 'QT03', .08),
             ('cross_rf', [f'QL{i:02d}' for i in range(7, 13)], Point('QT01'), Point('QT02', 'exit'), 'QL10', .15),
             ('biased_input', [f'QL{i:02d}' for i in range(7, 13)], Point('QL12', 'exit'), Point('QT02'), 'QL10', .15),
             ('qt_biased', [f'QT{i:02d}' for i in range(1, 7)], Point('QT06', 'exit'), Point('QT06', 'exit'), 'QT03', .08)]
    reports = {}
    for label, magnets, target, measured_at, perturbed, delta in cases:
        if selected_case and label != selected_case:
            continue
        folder = output / label
        points = {'start': Point(magnets[0]), 'target': target, 'measurement': measured_at}
        design = standalone(model, folder / 'design', {}, points)
        overrides = {q: {'K1': float(e['K1'])} for q, e in model.elements.items() if 'K1' in e}
        overrides[perturbed]['K1'] += delta
        before = standalone(model, folder / 'before', overrides, points)
        baseline = MeasurementBaseline(measured_at, before['measurement']['energy'],
            before['measurement']['planes'], 'half', 'vm', 'ALL_MAIN', overrides,
            same_state_declared=True)
        if label in ('biased_input', 'qt_biased'):
            bias = .75 if label == 'biased_input' else .95
            baseline.planes = {p: Twiss(t.beta * bias, t.alpha * bias, t.emittance / bias)
                               for p, t in baseline.planes.items()}
        request = MatchingRequest(baseline, target,
            {q: MagnetLimit(overrides[q]['K1'] - 1, overrides[q]['K1'] + 1, .5) for q in magnets},
            tolerance=1e-4, max_evaluations=180)
        result = solve_matching(model, request)
        (folder / 'matching.json').write_text(json.dumps(asdict(result), indent=2))
        applied = deepcopy(overrides)
        for q, values in result.magnets.items():
            applied[q]['K1'] = values['suggested']
        after = standalone(model, folder / 'after', applied, points)
        report = {'status': result.status, 'rank': result.diagnostics['rank'], 'planes': {},
                  'measurement': asdict(measured_at), 'target': asdict(target), 'magnets': result.magnets}
        for p in ('x', 'y'):
            reference = design['target']['planes'][p]
            predicted = endpoint(result.candidate[p])
            actual = after['target']['planes'][p]
            values = {'before_bmag': mismatch(before['target']['planes'][p], reference),
                'predicted_bmag': mismatch(predicted, reference),
                'standalone_bmag': mismatch(actual, reference),
                'particle_bmag': mismatch(after['target']['particles'][p], reference),
                'prediction_vs_standalone_bmag': mismatch(predicted, actual),
                'back_inferred_start_bmag': mismatch(Twiss(**result.initial['planes'][p]), before['start']['planes'][p]),
                'prediction_beta_relative_error': predicted.beta / actual.beta - 1,
                'actual': asdict(actual), 'predicted': asdict(predicted)}
            report['planes'][p] = values
            if label not in ('biased_input', 'qt_biased'):
                assert values['standalone_bmag'] - 1 <= request.tolerance, (label, p, values)
                assert values['prediction_vs_standalone_bmag'] - 1 < 1e-6, (label, p, values)
                assert values['back_inferred_start_bmag'] - 1 < 1e-6, (label, p, values)
                assert values['particle_bmag'] - 1 < .01, (label, p, values)
        assert result.status == 'model_target_met' and result.diagnostics['rank'] == 4
        for q, values in result.magnets.items():
            limit = request.magnets[q]
            assert limit.lower <= values['suggested'] <= limit.upper
            assert abs(values['suggested'] - overrides[q]['K1']) <= limit.max_change + 1e-10
        if label in ('biased_input', 'qt_biased'):
            remeasurement = MeasurementBaseline(measured_at, after['measurement']['energy'],
                after['measurement']['planes'], 'half', 'vm', 'ALL_MAIN', applied, same_state_declared=True)
            comparison = compare_measurement(model, result, remeasurement, tolerance=.01)
            report['remeasurement'] = comparison
            assert any(v['standalone_bmag'] - 1 > .01 for v in report['planes'].values())
            assert not all(v['within_tolerance'] for v in comparison['planes'].values())
        reports[label] = report
        (output / 'report.json').write_text(json.dumps(reports, indent=2))
        print(label, json.dumps(report['planes']), flush=True)
    return reports


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--case', choices=('ql', 'qt', 'cross_rf', 'biased_input', 'qt_biased'))
    args = parser.parse_args()
    run(args.output, args.case)
