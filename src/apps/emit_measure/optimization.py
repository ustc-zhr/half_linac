"""HALF multi-variable emittance optimization. No Qt or automatic PV access on import."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from copy import deepcopy
import json
import math
from pathlib import Path
from threading import Event
import time


class Stopped(RuntimeError):
    pass


class BudgetExhausted(RuntimeError):
    pass


class RestoreFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class OptimizationVariable:
    element_id: str
    low: float
    high: float
    quantity: str = 'current'
    unit: str = 'A'

    def validate(self):
        if not self.element_id or self.quantity != 'current' or self.unit != 'A':
            raise ValueError('This version supports solenoid current variables in A only.')
        if not math.isfinite(self.low) or not math.isfinite(self.high) or self.low >= self.high:
            raise ValueError(f'{self.element_id}: set finite, increasing current bounds.')


@dataclass(frozen=True)
class OptimizationConfig:
    variables: tuple[OptimizationVariable, ...]
    plane: str
    other_limit: float
    max_measurements: int = 20
    max_minutes: float = 120
    readback_tolerance: float = 0.01
    motion_timeout: float = 10
    settle_time: float = 2
    algorithm: str = 'rcds'
    rcds_initial_step: float = 0.2
    bo_initial_samples: int | None = None
    bo_exploration: float = 0.01
    bo_random_seed: int = 0

    def validate(self):
        if not self.variables or self.plane not in ('x', 'y'):
            raise ValueError('Select at least one solenoid and a target plane.')
        for variable in self.variables:
            variable.validate()
        if len({v.element_id for v in self.variables}) != len(self.variables):
            raise ValueError('Optimization variables must be unique.')
        if not all(math.isfinite(v) for v in (
            self.other_limit, self.max_minutes, self.readback_tolerance,
            self.motion_timeout, self.settle_time, self.rcds_initial_step,
            self.bo_exploration,
        )):
            raise ValueError('Settings must be finite.')
        if self.other_limit <= 0:
            raise ValueError('Set a positive other-plane limit.')
        if self.max_measurements < 5 or self.max_minutes <= 0:
            raise ValueError('Allow at least five measurements and a positive time budget.')
        if self.readback_tolerance <= 0 or self.motion_timeout <= 0 or self.settle_time < 0:
            raise ValueError('Invalid motion settings.')
        if self.algorithm not in ('rcds', 'bo'):
            raise ValueError('Optimization algorithm must be RCDS or BO.')
        if not 0 < self.rcds_initial_step <= 1:
            raise ValueError('RCDS initial step fraction must be greater than 0 and at most 1.')
        if self.bo_exploration < 0:
            raise ValueError('BO exploration must be non-negative.')
        if (self.bo_initial_samples is not None and
                (not math.isfinite(self.bo_initial_samples) or
                 int(self.bo_initial_samples) != self.bo_initial_samples)):
            raise ValueError('BO initial samples must be an integer.')
        if int(self.bo_random_seed) != self.bo_random_seed or self.bo_random_seed < 0:
            raise ValueError('BO random seed must be a non-negative integer.')
        if self.algorithm == 'bo':
            initial_samples = self.effective_bo_initial_samples()
            search_budget = self.max_measurements - 4
            if initial_samples < 3 or initial_samples > search_budget:
                raise ValueError(
                    f'BO initial samples must be between 3 and the search budget ({search_budget}).'
                )

    def effective_bo_initial_samples(self):
        return (max(3, 2 * len(self.variables) + 1)
                if self.bo_initial_samples is None else int(self.bo_initial_samples))

    def optimizer_settings(self):
        if self.algorithm == 'bo':
            return {
                'name': 'bo', 'backend': 'scikit-learn',
                'surrogate': 'Gaussian process / Matérn 5/2',
                'acquisition': 'expected improvement',
                'initial_samples': self.effective_bo_initial_samples(),
                'exploration': self.bo_exploration,
                'random_seed': int(self.bo_random_seed),
            }
        return {
            'name': 'rcds', 'backend': 'GOTAcc',
            'initial_step_fraction': self.rcds_initial_step,
        }

    def point(self, values, *, bounded=True):
        # A scalar is accepted only for the one-variable case.
        values = (values,) if isinstance(values, (int, float)) else tuple(values)
        if len(values) != len(self.variables):
            raise ValueError('Candidate dimension does not match the selected variables.')
        point = tuple(float(value) for value in values)
        for variable, value in zip(self.variables, point):
            if not math.isfinite(value) or (bounded and not variable.low <= value <= variable.high):
                raise ValueError(f'{variable.element_id}: current {value!r} is outside optimization bounds.')
        return point

    def named(self, point):
        return dict(zip((v.element_id for v in self.variables), point))


def measurement_values(result, strategy=None):
    """Read a completed reconstruction using the active scan strategy."""
    if not result.get('restored', False):
        raise RestoreFailure(result.get('restore_error') or 'Scan quadrupole restoration not verified.')
    if result.get('error'):
        if result.get('fatal'):
            raise RuntimeError(result['error'])
        raise ValueError(result['error'])
    values = {}
    for plane in ('x', 'y'):
        item = result.get(plane + 'plane', {})
        if item.get('status') != 'valid':
            raise ValueError(f'{plane.upper()} measurement reconstruction is not valid.')
        if strategy == 'adaptive_quality' and item.get('validation_status') != 'validated':
            raise ValueError(f'{plane.upper()} measurement did not pass adaptive quality checks.')
        value = float(item.get('exn_raw', item.get('exn', float('nan'))))
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f'{plane.upper()} normalized emittance is not finite and positive.')
        values[plane] = value
    return values


def json_safe(value):
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_safe(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, 'tolist'):
        return json_safe(value.tolist())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


class OptimizationSession:
    """Synchronous worker-side controller with injected motion and measurement."""
    def __init__(self, config, device, measure, run_dir, *, cancelled=None,
                 progress=None, optimizer=None, clock=time.monotonic, metadata=None,
                 background=None, recover_quad=None):
        config.validate()
        self.config, self.device, self.measure = config, device, measure
        self.run_dir = Path(run_dir)
        self.cancelled = cancelled if cancelled is not None else Event()
        self.progress = progress or (lambda record: None)
        self.clock = clock
        self.background = background
        self.recover_quad = recover_quad
        self.motion_attempted = False
        if optimizer is None:
            if config.algorithm == 'rcds':
                from half_linac.src.optimization.emittance_rcds import optimize_currents
                optimizer = lambda evaluate, low, high, initial, budget: optimize_currents(
                    evaluate, low, high, initial, budget,
                    initial_step=config.rcds_initial_step,
                )
            else:
                from half_linac.src.optimization.emittance_bo import optimize_currents
                optimizer = lambda evaluate, low, high, initial, budget: optimize_currents(
                    evaluate, low, high, initial, budget,
                    initial_samples=config.effective_bo_initial_samples(),
                    exploration=config.bo_exploration,
                    random_seed=int(config.bo_random_seed),
                )
        self.optimizer = optimizer
        self.records = []
        self.initial = None
        self.best = None
        self.confirmed = False
        self.baseline = None
        self.deadline = float('inf')
        self.summary = {'schema_version': 'emit_optimization_v2',
                        'variable_order': [v.element_id for v in config.variables],
                        'config': asdict(config), 'measurement': metadata or {},
                        'optimizer': config.optimizer_settings(),
                        'status': 'ready', 'restored': False, 'confirmed': False}

    def save(self):
        self.summary['confirmed'] = self.confirmed
        if hasattr(self.device, 'last_restore'):
            self.summary['device_restoration'] = deepcopy(self.device.last_restore)
        payload = dict(self.summary, initial=self.initial,
                       initial_currents=self.config.named(self.initial) if self.initial is not None else None,
                       best=self.best,
                       baseline=self.baseline, confirmed=self.confirmed, records=self.records)
        destination = self.run_dir / 'optimization.json'
        temporary = destination.with_suffix('.tmp')
        temporary.write_text(json.dumps(json_safe(payload), indent=2, allow_nan=False), encoding='utf-8')
        temporary.replace(destination)

    def checkpoint(self):
        if self.cancelled.is_set():
            raise Stopped('Stopped by operator.')
        if self.clock() >= self.deadline:
            raise BudgetExhausted('Time budget exhausted.')

    def emit(self, stage, **extra):
        self.progress(deepcopy(dict(stage=stage, count=len(self.records), records=self.records, **extra)))

    def acquire(self, current, stage, reserve=0):
        current = self.config.point(current)
        for attempt in range(2):
            self.checkpoint()
            if len(self.records) >= self.config.max_measurements - reserve:
                raise BudgetExhausted('Measurement budget exhausted.')
            motion = dict(stage=stage, currents=self.config.named(current), status='pending', time=time.time())
            self.summary.setdefault('motions', []).append(motion)
            self.save()
            self.motion_attempted = True
            try:
                self.device.move(current, self.checkpoint)
                motion['status'] = 'complete'
            except Exception as exc:
                motion.update(status='failed', error=str(exc))
                raise
            self.checkpoint()
            index = len(self.records) + 1
            path = self.run_dir / f'measurement_{index:03d}'
            path.mkdir()
            record = dict(index=index, stage=stage, point=current, currents=self.config.named(current), attempt=attempt + 1,
                          archive=str(path), started_at=time.time(), valid=False)
            self.records.append(record)
            self.save()
            self.emit(stage, current=current)
            # The bridge must not return until the scan has exited and restored the scan quadrupole.
            result = self.measure(path, self.checkpoint)
            record['result'] = result
            record['finished_at'] = time.time()
            self.save()
            if result.get('restored'):
                self.checkpoint()
            try:
                values = measurement_values(result, self.summary['measurement'].get('scan_strategy'))
            except (ValueError, TypeError) as exc:
                record['error'] = str(exc)
                self.save()
                self.emit('Invalid measurement', record=record)
                self.checkpoint()
                if attempt:
                    raise RuntimeError(f'Measurement failed twice: {exc}') from exc
                continue
            record.update(values=values, valid=True,
                          feasible=values['y' if self.config.plane == 'x' else 'x'] <= self.config.other_limit)
            self.save()
            self.emit(stage, record=record)
            self.checkpoint()
            return record
        raise AssertionError('unreachable')

    def run(self):
        self.run_dir.mkdir(parents=True, exist_ok=False)
        self.deadline = self.clock() + self.config.max_minutes * 60
        self.summary['status'] = 'running'
        try:
            if self.background is not None:
                import numpy as np
                np.save(self.run_dir / 'background.npy', self.background, allow_pickle=False)
                self.summary['measurement']['background_snapshot'] = 'background.npy'
            self.device.validate()
            self.initial = self.config.point(self.device.initial(), bounded=False)
            self.config.point(self.initial)  # All bounds must contain the actual initial setpoints.
            self.summary['initial_readback'] = self.config.named(self.config.point(self.device.readback(), bounded=False))
            self.save()
            baseline = [self.acquire(self.initial, 'Baseline', reserve=2) for _ in range(2)]
            if not all(r['feasible'] for r in baseline):
                raise RuntimeError('Baseline exceeds the other-plane limit.')
            self.baseline = {p: sum(r['values'][p] for r in baseline) / 2 for p in ('x', 'y')}
            self.summary['baseline_difference'] = {
                p: abs(baseline[0]['values'][p] - baseline[1]['values'][p]) for p in ('x', 'y')}

            def evaluate(current):
                current = self.config.point(current)
                r = self.acquire(current, 'Search', reserve=2)
                value = r['values'][self.config.plane]
                if r['feasible'] and (self.best is None or value < self.best['values'][self.config.plane]):
                    self.best = r
                self.save()
                return value / (value + self.baseline[self.config.plane]) if r['feasible'] else 2.0

            try:
                self.optimizer(evaluate, tuple(v.low for v in self.config.variables),
                               tuple(v.high for v in self.config.variables), self.initial,
                               self.config.max_measurements - len(self.records) - 2)
            except BudgetExhausted:
                self.checkpoint()  # Measurement budget reserves verification; time does not.
            if self.best is not None:
                verify = [self.acquire(self.best['point'], 'Verify best') for _ in range(2)]
                mean = sum(r['values'][self.config.plane] for r in verify) / 2
                self.confirmed = all(r['feasible'] for r in verify) and mean < self.baseline[self.config.plane]
                self.summary['verification_mean'] = mean
                self.summary['verification_difference'] = abs(
                    verify[0]['values'][self.config.plane] - verify[1]['values'][self.config.plane])
            self.summary['status'] = 'complete'
        except Stopped as exc:
            self.summary.update(status='stopped', error=str(exc))
            self.confirmed = False
        except BudgetExhausted as exc:
            self.summary.update(status='budget_exhausted', error=str(exc))
            self.confirmed = False
        except RestoreFailure as exc:
            self.summary.update(status='restore_failed', quad_restore_error=str(exc))
            self.confirmed = False
        except Exception as exc:
            self.summary.update(status='failed', error=str(exc))
            self.confirmed = False
        finally:
            # Cancellation never bypasses restoration; the measure bridge already joined the scan thread.
            if self.initial is not None and self.motion_attempted:
                self.emit('Restoring initial currents', currents=self.config.named(self.initial))
                try:
                    self.device.restore(self.initial)
                    self.summary['restored'] = True
                except Exception as exc:
                    self.summary.update(status='restore_failed', restore_error=str(exc), restored=False)
                    self.confirmed = False
                self.summary['device_restoration'] = getattr(self.device, 'last_restore', {})
            self.save()
            self.emit(self.summary['status'], summary=dict(self.summary), confirmed=self.confirmed)
        return self.summary

    def manual_move(self, *, best=False):
        if self.initial is None or (best and not self.confirmed):
            raise ValueError('No verified value available.')
        # During restore, a disconnected member must not prevent attempts on others.
        self.device.validate(read_values=best)
        value = self.best['point'] if best else self.initial
        action = dict(time=time.time(), currents=self.config.named(value), best=best, status='pending')
        self.summary.setdefault('actions', []).append(action)
        self.save()  # Persist intent and original values before the manual write.
        try:
            if self.summary.get('quad_restore_error'):
                if best or self.recover_quad is None:
                    raise RestoreFailure(self.summary['quad_restore_error'])
                self.recover_quad()
                self.summary.pop('quad_restore_error')
            if best:
                self.device.move(value, lambda: None)
            else:
                self.device.restore(value)
        except Exception as exc:
            action.update(status='failed', error=str(exc))
            self.confirmed = False
            self.summary.update(status='manual_write_failed', error=str(exc), restored=False)
            try:
                self.device.restore(self.initial)
                self.summary['restored'] = True
            except Exception as restore_exc:
                self.summary.update(status='restore_failed', restore_error=str(restore_exc))
            self.save()
            raise
        self.summary.pop('restore_error', None)
        self.summary.pop('error', None)
        self.summary.update(status='best_applied' if best else 'initial_restored', restored=not best)
        action['status'] = 'complete'
        self.save()


class EpicsVariableGroup:
    """An ordered group: validate every variable before the first write.

    Restore attempts every member even when an earlier supply fails. Complete
    initial snapshots are owned by the session, never taken during rollback.
    """
    def __init__(self, context, config, *, get=None, put=None):
        import epics
        self.context, self.config = context, config
        self.get, self.put = get or epics.caget, put or epics.caput
        self.channels = ()
        self.last_restore = {}

    def validate(self, *, read_values=True):
        from half_linac.src.shared.machine_profile import resolve_channel, resolve_write_target, require_workflow_write_allowed
        self.config.validate()
        if self.context.profile.machine.id != 'half' or self.context.control_backend.name != 'real':
            raise ValueError('This version requires HALF real channels; use injected devices for offline tests.')
        require_workflow_write_allowed(self.context, 'emit_measure', 'Emittance optimization')
        channels = []
        for variable in self.config.variables:
            element = self.context.profile.get_element(variable.element_id)
            if element.kind != 'solenoid':
                raise ValueError(f'{element.id}: only solenoids are supported; scan quadrupoles are excluded.')
            target = resolve_write_target(self.context, element.id, quantity=variable.quantity, unit=variable.unit)
            read_pv = resolve_channel(self.context, element.id, 'current_readback')
            if not target.machine_limit.contains(variable.low) or not target.machine_limit.contains(variable.high):
                raise ValueError(f'{element.id}: requested current range exceeds machine limits.')
            channels.append((target, read_pv))
        if self.channels and self.channels != tuple(channels):
            raise ValueError('Device mapping or machine limits changed since the run.')
        self.channels = tuple(channels)
        if read_values:
            self.initial()
            self.readback()

    def read(self, pv):
        value = self.get(pv, timeout=self.config.motion_timeout, use_monitor=False)
        if value is None or not math.isfinite(float(value)):
            raise RuntimeError(f'Cannot read finite value from {pv}.')
        return float(value)

    def initial(self):
        return tuple(self.read(target.pv_name) for target, _ in self.channels)

    def readback(self):
        return tuple(self.read(pv) for _, pv in self.channels)

    def _validate_point(self, values, *, restoring=False):
        point = self.config.point(values, bounded=not restoring)
        if len(self.channels) != len(point):
            raise ValueError('Validate the device group before writing.')
        for (target, _), value in zip(self.channels, point):
            if not target.machine_limit.contains(value):
                raise ValueError(f'{target.element_id}: current is outside machine limits.')
        return point

    def _put(self, index, value):
        from half_linac.src.shared.machine_profile import require_workflow_write_allowed
        require_workflow_write_allowed(self.context, 'emit_measure', 'Emittance optimization current write')
        target, _ = self.channels[index]
        if self.put(target.pv_name, value, wait=True, timeout=self.config.motion_timeout) != 1:
            raise RuntimeError(f'{target.element_id}: write failed, target {value:g} A.')

    def _wait(self, indices, point, check):
        deadline = time.monotonic() + self.config.motion_timeout
        last = {}
        while time.monotonic() < deadline:
            check()
            for index in indices:
                target, pv = self.channels[index]
                last[target.element_id] = self.read(pv)
            if all(abs(last[self.channels[i][0].element_id] - point[i]) <= self.config.readback_tolerance for i in indices):
                return last
            time.sleep(0.05)
        detail = '; '.join(f'{self.channels[i][0].element_id}: target {point[i]:g} A, '
                           f'last readback {last.get(self.channels[i][0].element_id)!r} A' for i in indices)
        raise RuntimeError(detail + '; timed out.')

    def move(self, values, check, *, restoring=False):
        point = self._validate_point(values, restoring=restoring)
        for index, value in enumerate(point):
            check()
            self._put(index, value)
        indices = tuple(range(len(point)))
        self._wait(indices, point, check)
        deadline = time.monotonic() + self.config.settle_time
        while time.monotonic() < deadline:
            check()
            time.sleep(0.05)
        self._wait(indices, point, check)

    def restore(self, values):
        point = self._validate_point(values, restoring=True)
        self.last_restore = {}
        errors = []
        for index, value in enumerate(point):
            name = self.config.variables[index].element_id
            entry = self.last_restore[name] = dict(target=value, restored=False)
            try:
                self._put(index, value)
                readbacks = self._wait((index,), point, lambda: None)
                entry.update(restored=True, readback=readbacks[name])
            except Exception as exc:
                entry['error'] = str(exc)
                errors.append(str(exc))
        if errors:
            raise RestoreFailure('; '.join(errors))
        # Verify the entire group together after all restore writes have finished.
        try:
            self._wait(tuple(range(len(point))), point, lambda: None)
        except Exception as exc:
            for entry in self.last_restore.values():
                entry.update(restored=False, error=str(exc))
            raise RestoreFailure(str(exc)) from exc


class VerifiedQuadRestore:
    """Restore K1 and verify both K1 and the physical supply current."""
    def __init__(self, context, quad_name="QL09", *, timeout=10, tolerance=0.01):
        import epics
        from half_linac.src.shared.machine_profile import resolve_channel
        self.get, self.put = epics.caget, epics.caput
        self.timeout, self.tolerance = timeout, tolerance
        self.quad_name = quad_name
        self.k1_pv = resolve_channel(context, quad_name, 'K1')
        self.current_pv = resolve_channel(context, quad_name, 'current_readback')
        self.set_pv = resolve_channel(context, quad_name, 'current_set')
        self.initial_k1 = self.read(self.k1_pv)
        self.initial_current = self.read(self.set_pv)
        self.initial_readback = self.read(self.current_pv)
        if abs(self.initial_current - self.initial_readback) > tolerance:
            raise RuntimeError(f'{self.quad_name} current is not at its setpoint before optimization.')

    def move(self, value, check):
        """Confirm the scan write and physical current, not just a fixed delay."""
        check()
        if self.put(self.k1_pv, value, wait=True, timeout=self.timeout) != 1:
            raise RuntimeError(f'{self.quad_name}: scan write failed, K1={value:g}.')
        deadline = time.monotonic() + self.timeout
        last = None
        while time.monotonic() < deadline:
            check()
            target = self.read(self.set_pv)
            last = self.read(self.current_pv)
            if abs(last - target) <= self.tolerance and abs(self.read(self.k1_pv) - value) <= 1e-6:
                return
            time.sleep(0.05)
        raise RuntimeError(f'{self.quad_name}: scan motion timed out at K1={value:g}, last current={last!r} A.')

    def read(self, pv):
        value = self.get(pv, timeout=self.timeout, use_monitor=False)
        if value is None or not math.isfinite(float(value)):
            raise RuntimeError(f'Cannot read {self.quad_name} channel {pv}.')
        return float(value)

    def __call__(self, value):
        if value is None:
            return  # No initial value was acquired, so the scan never wrote anything.
        if self.put(self.k1_pv, value, wait=True, timeout=self.timeout) != 1:
            raise RestoreFailure(f'{self.quad_name}: restore write failed; initial K1={value:g}.')
        deadline = time.monotonic() + self.timeout
        last_k1 = last_current = None
        while time.monotonic() < deadline:
            last_k1 = self.read(self.k1_pv)
            last_current = self.read(self.current_pv)
            if abs(last_k1 - value) <= 1e-6 and abs(last_current - self.initial_current) <= self.tolerance:
                return
            time.sleep(0.05)
        raise RestoreFailure(f'{self.quad_name} restoration failed: initial K1={value:g}, last K1={last_k1}; '
                             f'initial current={self.initial_current:g} A, last readback={last_current} A.')
