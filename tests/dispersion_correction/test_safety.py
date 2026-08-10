import numpy as np

from half_linac.src.apps.dispersion_correction.models import (
    BPMReading,
    SafetyConfig,
)
from half_linac.src.apps.dispersion_correction.safety import evaluate_safety


def reading(
    names: tuple[str, ...],
    x_mm: tuple[float, ...],
    valid: tuple[bool, ...] | None = None,
) -> BPMReading:
    return BPMReading(
        names=names,
        x_mm=np.asarray(x_mm),
        y_mm=np.zeros(len(names)),
        valid=np.ones(len(names), dtype=bool) if valid is None else np.asarray(valid),
    )


def test_orbit_limit_failure_identifies_value_limit_and_bpm() -> None:
    config = SafetyConfig(max_reference_orbit_change_mm=0.5)
    reference = reading(("BPM07", "BPM08", "BPM09"), (0.0, 0.1, -0.2))
    current = reading(("BPM07", "BPM08", "BPM09"), (0.2, 0.0, 0.6))

    status = evaluate_safety(config, reference, current)

    assert not status.ok
    assert status.max_orbit_change_mm == 0.8
    assert (
        status.reason
        == "Reference orbit change 0.800 mm at BPM09 exceeded 0.500 mm limit"
    )


def test_orbit_check_rejects_changed_bpm_order() -> None:
    config = SafetyConfig(max_reference_orbit_change_mm=0.5)
    reference = reading(("BPM07", "BPM08"), (0.0, 0.0))
    current = reading(("BPM08", "BPM07"), (0.0, 0.0))

    status = evaluate_safety(config, reference, current)

    assert not status.ok
    assert status.reason == "BPM order changed during safety orbit check"


def test_orbit_check_uses_only_common_valid_bpms() -> None:
    config = SafetyConfig(max_reference_orbit_change_mm=0.5)
    reference = reading(("BPM07", "BPM08"), (0.0, 0.0), (True, False))
    current = reading(("BPM07", "BPM08"), (0.4, 10.0), (True, True))

    status = evaluate_safety(config, reference, current)

    assert status.ok
    assert status.max_orbit_change_mm == 0.4
