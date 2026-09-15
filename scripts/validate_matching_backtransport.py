"""Check QT02 backtransport against independent full-line Elegant optics."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.validate_matching_independent import (
    isolated_model, standalone, Point, MeasurementBaseline, mismatch,
)


def run(output):
    output.mkdir(parents=True, exist_ok=True)
    model = isolated_model(output)
    points = {'measurement': Point('QT02'), 'qt': Point('QT01'), 'ql': Point('QL07')}
    reference = standalone(model, output / 'reference', {}, points)
    reports = []
    failures = []
    default_energy = model.backend.energy_mev
    for label in ('qt', 'ql'):
        for fraction in (0, -.05, -.01, .01, .05):
            baseline = MeasurementBaseline(
                points['measurement'], reference['measurement']['energy'] * (1 + fraction),
                reference['measurement']['planes'], 'half', 'vm', 'ALL_MAIN', {},
                same_state_declared=True)
            inferred, energy = model.transport(baseline, points[label])
            row = {'entrance': label, 'input_bias': fraction,
                   'measurement_energy_mev': baseline.energy_mev,
                   'inferred_energy_mev': energy,
                   'reference_energy_mev': reference[label]['energy'], 'planes': {}}
            for plane, twiss in inferred.items():
                truth = reference[label]['planes'][plane]
                errors = {'beta_relative_error': twiss.beta / truth.beta - 1,
                          'alpha_error': twiss.alpha - truth.alpha,
                          'emittance_relative_error': twiss.emittance / truth.emittance - 1,
                          'bmag': mismatch(twiss, truth), 'inferred': asdict(twiss),
                          'reference': asdict(truth)}
                row['planes'][plane] = errors
                if fraction == 0 and (abs(errors['beta_relative_error']) > 1e-5
                        or abs(errors['alpha_error']) > 1e-4
                        or abs(errors['emittance_relative_error']) > 1e-5):
                    failures.append(f'{label}/{plane}: inaccurate backtransport')
            if fraction == 0:
                if abs(energy - reference[label]['energy']) > 1e-5:
                    failures.append(f'{label}: inaccurate entrance energy')
                model.backend.energy_mev = baseline.energy_mev * .8
                repeated, repeated_energy = model.transport(baseline, points[label])
                model.backend.energy_mev = default_energy
                row['default_energy_independent'] = repeated_energy == energy and all(
                    abs(repeated[plane].beta / inferred[plane].beta - 1) < 1e-10
                    for plane in inferred)
                if not row['default_energy_independent']:
                    failures.append(f'{label}: backend default leaks into transport')
            reports.append(row)
            print(json.dumps(row), flush=True)
    (output / 'report.json').write_text(json.dumps({'results': reports, 'failures': failures}, indent=2))
    assert not failures, failures


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output)
