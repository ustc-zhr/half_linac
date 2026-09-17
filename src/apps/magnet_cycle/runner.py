"""Blocking runner intended for a worker thread; stop never restores automatically."""
import time
from .model import Cycle, finite
from .profile_runtime import group_magnets


class Stopped(Exception):
    pass


class Simulator:
    def __init__(self, magnets):
        self.values = {}
        self.readbacks = {}
        for m in magnets:
            self.values[m.set_pv] = min(m.high, max(m.low, 0.0))
            self.readbacks[m.set_pv] = self.values[m.set_pv]

    def read(self, m):
        current = self.readbacks[m.set_pv]
        current += (self.values[m.set_pv] - current) * 0.8
        self.readbacks[m.set_pv] = current
        return self.values[m.set_pv], current

    def write(self, m, value):
        self.values[m.set_pv] = value

    def close(self):
        pass


class EpicsIO:
    def __init__(self):
        import epics
        epics.ca.use_initial_context()
        self.epics = epics
        self.pvs = {}

    def pv(self, name):
        if name not in self.pvs:
            self.pvs[name] = self.epics.PV(name, auto_monitor=False)
        pv = self.pvs[name]
        if not pv.wait_for_connection(timeout=1):
            raise RuntimeError(f'PV 未连接: {name}')
        return pv

    def read(self, m):
        return tuple(finite(self.pv(name).get(use_monitor=False, timeout=1)) for name in (m.set_pv, m.read_pv))

    def write(self, m, value):
        if not m.low <= finite(value) <= m.high:
            raise ValueError(f'{m.name}: 写入越限')
        pv = self.pv(m.set_pv)
        if not pv.write_access or pv.put(value, wait=True, timeout=1) != 1:
            raise RuntimeError(f'PV 写入失败: {m.set_pv}')

    def close(self):
        for pv in self.pvs.values():
            pv.disconnect()


def run_cycles(magnets, parameters, io, stop, report, record=lambda event: None):
    groups = group_magnets(magnets)
    states = []

    def check_stop():
        if stop.is_set():
            raise Stopped('已停止后续写入；电源可能继续到最后设定值')

    # Validate every initial value before making any writes.
    for group in groups:
        check_stop()
        m = group[0]
        setpoint, readback = io.read(m)
        cycle = Cycle(m.low, m.high, setpoint, readback, parameters, time.monotonic())
        states.append((group, cycle))
        record(dict(event='initial', magnets=[x.name for x in group], set_pv=m.set_pv,
                    read_pv=m.read_pv, initial=cycle.initial, low=m.low, high=m.high))
        for member in group:
            report(member.name, cycle.initial, setpoint, readback, '准备')
    while any(not cycle.done for _, cycle in states):
        check_stop()
        for group, cycle in states:
            check_stop()
            if cycle.done:
                continue
            m = group[0]
            try:
                setpoint, readback = io.read(m)
                check_stop()
                command = cycle.tick(time.monotonic(), setpoint, readback)
                if command is not None:
                    check_stop()
                    io.write(m, command)
                    record(dict(event='write', magnet=m.name, value=command))
                    setpoint = command
                for member in group:
                    report(member.name, cycle.initial, setpoint, readback, cycle.status)
            except Stopped:
                raise
            except Exception as exc:
                raise RuntimeError(f'{m.name}: {exc}') from exc
        stop.wait(parameters.period)
    check_stop()
