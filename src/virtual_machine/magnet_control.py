"""VM-only magnet transactions. No JSON writes and no real-backend fallback."""
from __future__ import annotations

import copy
import math
import time
from dataclasses import dataclass, asdict

from half_linac.src.shared.machine_profile import resolve_write_target
from half_linac.src.shared.control_point import control_defaults_from_profile
from half_linac.src.virtual_machine.workbench_data import input_version


@dataclass(frozen=True)
class Magnet:
    element_id: str
    field: str
    pv: str
    kind: str
    scale: float
    unit: str
    low: float | None
    high: float | None
    tolerance: float | None = None

    def close(self, a, b):
        return math.isclose(float(a), float(b), rel_tol=0 if self.tolerance else 1e-9,
                            abs_tol=self.tolerance or 1e-12)

    def validate(self, value):
        value = float(value)
        if not math.isfinite(value):
            raise ValueError('Value must be finite')
        if (self.low is not None and value < self.low) or (self.high is not None and value > self.high):
            raise ValueError('Value is outside configured limits')
        return value


def magnets_for(profile, state):
    """Only model-backed writable VM magnets, in first occurrence order."""
    defaults = control_defaults_from_profile(profile, 'vm')
    result, seen = [], set()
    for name in state['usedline']:
        try:
            element = profile.get_element(name)
            if element.kind not in ('quad', 'corr'):
                continue
            field = 'K1' if element.kind == 'quad' else 'KICK'
            if field not in state['lattice'][name] and field != 'KICK':
                continue
            target = resolve_write_target(profile, name, mode='vm',
                **({'quantity': 'K1'} if field == 'K1' else {'logical_channel': 'kick'}))
            if target.control_backend != 'vm':
                continue
            if target.pv_name in seen:
                continue
            seen.add(target.pv_name)
            result.append(Magnet(name, field, target.pv_name, element.kind,
                1 if field == 'K1' else 1000, 'm⁻²' if field == 'K1' else 'mrad',
                target.machine_limit.low, target.machine_limit.high,
                defaults.tolerance_for(element.kind, target.logical_channel,
                                       f'{name}/{target.logical_channel}')))
        except (KeyError, ValueError):
            continue
    return result


def model_value(state, magnet):
    element = state['lattice'][magnet.element_id]
    return float(element.get('KICK', 0) if magnet.field == 'KICK' else element[magnet.field])


def compatibility_key(state, magnets, profile_key):
    other = copy.deepcopy(state)
    for magnet in magnets:
        other['lattice'][magnet.element_id][magnet.field] = '<magnet-setting>'
    return input_version({'state': other, 'profile': profile_key,
                          'channels': [asdict(magnet) for magnet in magnets]})


@dataclass
class Baseline:
    result: dict
    magnets: tuple
    profile_key: str
    key: str

    def compatible(self, state, magnets, profile_key):
        return self.key == compatibility_key(state, magnets, profile_key)


def capture_baseline(result, state, status, session, magnets, profile_key, read):
    source = result.get('input_state')
    version = input_version(state)
    if (not session or status.get('session') != session or status.get('phase') != 'Ready'
            or result.get('session') != session or source is None
            or input_version(source) != version or result.get('input_version') != version
            or status.get('result_version') != version
            or status.get('calculation') != result.get('calculation')):
        raise ValueError('Baseline requires the latest successful result from this session')
    for magnet in magnets:
        if not magnet.close(read(magnet), model_value(source, magnet)):
            raise ValueError(f'{magnet.element_id}: PV and result input differ')
    return Baseline(copy.deepcopy(result), tuple(magnets), profile_key,
                    compatibility_key(source, magnets, profile_key))


class Cancelled(Exception):
    pass


class Superseded(Exception):
    pass


class MagnetOperation:
    """Worker-thread transaction, with injectable I/O and clock for offline tests.

    Expected state permits only our confirmed writes. Any other JSON change
    supersedes the operation, including during the model acknowledgement window.
    """
    def __init__(self, client, read_state, read_status, *, emit=lambda *args: None,
                 cancelled=lambda: False, context_valid=lambda: True, monotonic=time.monotonic, sleep=time.sleep):
        self.client, self.read_state, self.read_status = client, read_state, read_status
        self.emit, self.cancelled = emit, cancelled
        self.context_valid = context_valid
        self.monotonic, self.sleep = monotonic, sleep

    def check_cancel(self):
        if self.cancelled():
            raise Cancelled('Cancelled; confirmed writes were not rolled back')
        if not self.context_valid():
            raise Superseded('VM context is no longer active')

    def check_session(self, session):
        status = self.read_status()
        if status.get('session') != session or status.get('phase') == 'Stopped':
            raise Superseded('VM session changed or stopped')

    def exact_state(self, expected):
        if input_version(self.read_state()) != input_version(expected):
            raise Superseded('Model input changed externally')

    def execute(self, items, initial, session):
        # items: (Magnet, initial PV value, requested PV value)
        expected = copy.deepcopy(initial)
        report = [dict(element=m.element_id, pv=m.pv, before=old, target=new,
                       status='Not executed') for m, old, new in items]
        try:
            if not items:
                raise ValueError('No changed magnets')
            self.check_cancel()
            self.check_session(session)
            self.exact_state(expected)
            for magnet, old, new in items:
                magnet.validate(new)
                if not magnet.close(self.client.read(magnet), old):
                    raise Superseded(f'{magnet.element_id}: PV changed before submission')
                if not magnet.close(model_value(expected, magnet), old):
                    raise Superseded(f'{magnet.element_id}: PV and model are not synchronized')
            for i, (magnet, old, target) in enumerate(items):
                self.check_cancel()
                self.check_session(session)
                self.exact_state(expected)
                if not magnet.close(self.client.read(magnet), old):
                    raise Superseded(f'{magnet.element_id}: external PV change')
                self.emit('Writing PV', magnet.element_id)
                report[i]['status'] = 'Write attempted'
                self.client.write(magnet, target, timeout=2.0)
                self.check_cancel()
                report[i]['status'] = 'PV written'
                self.emit('Waiting for Model', magnet.element_id)
                deadline = self.monotonic() + 5.0
                while True:
                    self.check_cancel()
                    self.check_session(session)
                    actual_pv = self.client.read(magnet)
                    if not magnet.close(actual_pv, target):
                        raise Superseded(f'{magnet.element_id}: PV differs from requested value')
                    current = self.read_state()
                    before = input_version(expected)
                    candidate = copy.deepcopy(current)
                    old_element = expected['lattice'][magnet.element_id]
                    if magnet.field in old_element:
                        candidate['lattice'][magnet.element_id][magnet.field] = old_element[magnet.field]
                    else:
                        candidate['lattice'][magnet.element_id].pop(magnet.field, None)
                    if input_version(candidate) != before:
                        raise Superseded('Model changed outside this operation')
                    value = model_value(current, magnet)
                    if magnet.close(value, target):
                        expected = current
                        report[i]['status'] = 'Model confirmed'
                        break
                    if not magnet.close(value, old):
                        raise Superseded(f'{magnet.element_id}: unexpected model value')
                    if self.monotonic() >= deadline:
                        raise TimeoutError(f'{magnet.element_id}: model synchronization timed out')
                    self.sleep(.05)
            version = input_version(expected)
            self.emit('Calculating', version)
            while True:
                self.check_cancel()
                self.check_session(session)
                self.exact_state(expected)
                for magnet, _, target in items:
                    if not magnet.close(self.client.read(magnet), target):
                        raise Superseded(f'{magnet.element_id}: PV changed while calculating')
                status = self.read_status()
                if status.get('session') != session:
                    raise Superseded('VM session changed')
                if status.get('phase') == 'Stopped' or (status.get('phase') == 'Failed' and status.get('input_version') == version):
                    raise RuntimeError(status.get('error') or 'VM stopped')
                if status.get('phase') == 'Ready' and status.get('result_version') == version:
                    self.exact_state(expected)
                    for item in report:
                        item['status'] = 'Applied'
                    return dict(phase='Applied', detail='Calculation completed', items=report,
                                version=version, publication=status.get('publication', {}))
                self.sleep(.1)
        except Exception as exc:
            phase = 'Superseded' if isinstance(exc, Superseded) else 'Cancelled' if isinstance(exc, Cancelled) else 'Failed'
            for item in report:
                if item['status'] in ('Write attempted', 'PV written'):
                    item['status'] += ' — verification incomplete'
            return dict(phase=phase, detail=str(exc), items=report)
