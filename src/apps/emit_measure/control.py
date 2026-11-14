"""One cooperative control lease shared by HALF emittance entrypoints."""
from contextlib import contextmanager

from half_linac.src.shared.machine_profile.control_lock import MachineControlLock


def emittance_lock(machine_id, backend):
    return MachineControlLock.for_machine_resource(
        machine_id, f"emittance_{backend}",
        {"resource": "Emittance control", "backend": backend})


@contextmanager
def emittance_lease(context, inherited=None):
    expected = emittance_lock(context.profile.machine.id, context.control_backend.name)
    if inherited is None:
        with expected:
            yield
    else:
        if (not isinstance(inherited, MachineControlLock) or inherited.path != expected.path
                or inherited._stream is None):
            raise RuntimeError("Internal scan requires a held matching emittance lease")
        # The outer session retains ownership, including during approval waits.
        yield
