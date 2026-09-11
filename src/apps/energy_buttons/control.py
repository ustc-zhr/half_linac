"""Energy-only operations; never calculate or write magnet currents."""
import math
import sys
from pathlib import Path

_ROOT = next(parent for parent in Path(__file__).resolve().parents
             if (parent / "repo_bootstrap.py").is_file())
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from repo_bootstrap import ensure_repo_import_path
ensure_repo_import_path(__file__)
from half_linac.src.shared.machine_profile import load_profile


def load_energy_references():
    profile = load_profile("half")
    elements = sorted((element for element in profile.elements
                       if element.kind == "energy_reference"), key=lambda element: element.order)
    if not elements:
        raise ValueError("HALF has no energy references configured")
    return tuple((element.display_name, element.channels["energy"]["real"])
                 for element in elements)


REFERENCES = load_energy_references()
PVS = tuple(pv for _, pv in REFERENCES)
LABELS = dict((pv, label) for label, pv in REFERENCES)
DEMO_VALUES = (250, 400, 660, 870, 1120, 1358, 1600, 1840, 2140)


def energy(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Energy must be finite and positive")
    return value


def same(a, b):
    return math.isclose(a, b, rel_tol=0, abs_tol=0.0001)


def execute(backend, plan, report):
    """Preflight the whole batch, then stop on the first uncertain write."""
    for name, before, target in plan:
        energy(target)
        backend.check(name, target)
        if not same(backend.read(name), before):
            raise ValueError(f"{name} changed externally; reset edits and regenerate targets")
    for name, before, target in plan:
        try:
            if not same(backend.read(name), before):
                raise ValueError("Current value changed externally")
            backend.write(name, target)
            if not same(backend.read(name), target):
                raise ValueError("Energy value not confirmed; check current value")
        except Exception as exc:
            report(name, f"Unconfirmed: {exc}")
            raise RuntimeError(f"{name}: {exc}; remaining writes stopped") from exc
        report(name, "Confirmed")


class DemoBackend:
    def __init__(self):
        self.values = dict(zip(PVS, DEMO_VALUES))

    def read(self, name):
        return self.values[name]

    def check(self, name, target):
        energy(target)

    def write(self, name, target):
        self.values[name] = target


class EpicsBackend:
    def __init__(self):
        import epics
        self.pvs = {name: epics.PV(name) for name in PVS}

    def read(self, name):
        value = self.pvs[name].get(timeout=1, use_monitor=False)
        if value is None:
            raise ValueError("Disconnected or read timeout")
        return energy(value)

    def check(self, name, target):
        pv = self.pvs[name]
        if not pv.wait_for_connection(timeout=1) or not pv.write_access:
            raise ValueError(f"{name} disconnected or no write access")
        pv.get_ctrlvars(timeout=1)
        low, high = pv.lower_ctrl_limit, pv.upper_ctrl_limit
        if low is not None and high is not None and high > low:
            if not low <= target <= high:
                raise ValueError(f"{name} outside control limits [{low}, {high}]")

    def write(self, name, target):
        if self.pvs[name].put(target, wait=True, timeout=3) != 1:
            raise ValueError("Write timed out or failed; verify the result")
