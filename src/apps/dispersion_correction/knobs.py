from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np

from half_linac.src.apps.dispersion_correction.models import KnobConfig


class SymmetricKnobSet:
    """Map high-level symmetric knobs to individual quadrupole device deltas."""

    def __init__(self, configs: Sequence[KnobConfig], start_values: Mapping[str, float] | None = None) -> None:
        self.configs = tuple(configs)
        if not self.configs:
            raise ValueError("At least one knob config is required")
        self.names = tuple(config.name for config in self.configs)
        self._index = {name: idx for idx, name in enumerate(self.names)}
        if len(self._index) != len(self.names):
            raise ValueError("Knob names must be unique")
        self.start_values = {
            name: float(start_values.get(name, 0.0)) if start_values is not None else 0.0
            for name in self.names
        }

    def vector_from_mapping(self, values: Mapping[str, float]) -> np.ndarray:
        return np.asarray([float(values[name]) for name in self.names], dtype=float)

    def mapping_from_vector(self, values: Sequence[float]) -> dict[str, float]:
        array = np.asarray(values, dtype=float)
        if array.shape != (len(self.names),):
            raise ValueError(f"Expected {len(self.names)} knob values")
        return {name: float(array[idx]) for idx, name in enumerate(self.names)}

    def scan_steps(self) -> np.ndarray:
        return np.asarray([config.scan_step for config in self.configs], dtype=float)

    def limits(self) -> np.ndarray:
        return np.asarray([config.limit for config in self.configs], dtype=float)

    def step_limits(self, fraction: float) -> np.ndarray:
        if not 0 < fraction <= 1:
            raise ValueError("fraction must be in (0, 1]")
        return self.limits() * float(fraction)

    def within_total_limits(self, values: Mapping[str, float]) -> bool:
        for config in self.configs:
            start = self.start_values[config.name]
            if abs(float(values[config.name]) - start) > config.limit + 1.0e-15:
                return False
        return True

    def device_deltas(self, values: Mapping[str, float]) -> dict[str, float]:
        device_values: dict[str, float] = {}
        for config in self.configs:
            knob_value = float(values[config.name])
            for device, scale in config.devices.items():
                device_values[device] = device_values.get(device, 0.0) + knob_value * float(scale)
        return device_values

    def add_step(self, values: Mapping[str, float], step: Sequence[float]) -> dict[str, float]:
        base = self.vector_from_mapping(values)
        return self.mapping_from_vector(base + np.asarray(step, dtype=float))
