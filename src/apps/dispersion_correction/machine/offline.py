from __future__ import annotations

from collections.abc import Mapping, Sequence
import time

import numpy as np

from half_linac.src.apps.dispersion_correction.machine.base import MachineInterface
from half_linac.src.apps.dispersion_correction.models import BPMReading, MachineSnapshot, RunConfig


class OfflineMachine(MachineInterface):
    """Deterministic linear bend-section model for development and tests."""

    def __init__(
        self,
        config: RunConfig,
        response_matrix: np.ndarray | None = None,
        initial_dispersion_mm: np.ndarray | None = None,
        noise_mm: float = 0.0,
        seed: int = 1234,
    ) -> None:
        self.config = config
        self._mode = config.backend.mode
        model_options = config.backend.options.get("model", {})
        if not isinstance(model_options, dict):
            raise ValueError("backend.options.model must be a mapping when provided")
        self._energy_delta = 0.0
        self._knobs = {knob.name: 0.0 for knob in config.knobs}
        self._device_values: dict[str, float] = {}
        self._quadrupole_readbacks = {
            str(name): float(value)
            for name, value in (model_options.get("quadrupole_readbacks", {}) or {}).items()
        }
        configured_reference_orbit = model_options.get("reference_orbit_mm")
        self._reference_orbit = (
            np.asarray(configured_reference_orbit, dtype=float)
            if configured_reference_orbit is not None
            else np.zeros(len(config.measurement_bpms), dtype=float)
        )
        self._rng = np.random.default_rng(int(model_options.get("seed", seed)))
        self._noise_mm = float(model_options.get("noise_mm", noise_mm))
        n_bpm = len(config.measurement_bpms)
        n_knob = len(config.knobs)
        configured_initial_dispersion = model_options.get("initial_dispersion_mm")
        configured_response_matrix = model_options.get("response_matrix")
        self._initial_dispersion = (
            np.asarray(initial_dispersion_mm, dtype=float)
            if initial_dispersion_mm is not None
            else np.asarray(configured_initial_dispersion, dtype=float)
            if configured_initial_dispersion is not None
            else np.linspace(90.0, 130.0, n_bpm)
        )
        default_response = -np.column_stack(
            [
                np.linspace(8000.0, 12000.0, n_bpm),
                np.linspace(5000.0, -7000.0, n_bpm),
            ]
        )
        if n_knob != 2:
            default_response = -self._rng.normal(9000.0, 1500.0, size=(n_bpm, n_knob))
        self._response = (
            np.asarray(response_matrix, dtype=float)
            if response_matrix is not None
            else np.asarray(configured_response_matrix, dtype=float)
            if configured_response_matrix is not None
            else default_response
        )
        if self._response.shape != (n_bpm, n_knob):
            raise ValueError("response_matrix shape must be (n_bpm, n_knob)")
        if self._initial_dispersion.shape != (n_bpm,):
            raise ValueError("initial_dispersion_mm length must match measurement_bpms")
        if self._reference_orbit.shape != (n_bpm,):
            raise ValueError("reference_orbit_mm length must match measurement_bpms")
        self._charge = 1.0
        self._loss = 1.0
        self.unsafe = False

    @property
    def backend_name(self) -> str:
        return "offline"

    @property
    def mode(self) -> str:
        return self._mode

    def read_bpm(self, bpm_names: Sequence[str]) -> BPMReading:
        if tuple(bpm_names) != self.config.measurement_bpms:
            raise ValueError(
                "OfflineMachine requires reading configured measurement_bpms in order"
            )
        dispersion = self.current_dispersion()
        x = self._reference_orbit + dispersion * self._energy_delta
        if self._noise_mm > 0:
            x = x + self._rng.normal(0.0, self._noise_mm, size=x.shape)
        y = np.zeros_like(x)
        return BPMReading(
            names=tuple(bpm_names),
            x_mm=x,
            y_mm=y,
            valid=np.ones_like(x, dtype=bool),
            charge=self._charge,
            loss=self._loss,
        )

    def get_energy_delta(self) -> float:
        return self._energy_delta

    def set_energy_delta(self, value: float) -> None:
        self._energy_delta = float(value)

    def get_knobs(self, knob_names: Sequence[str]) -> dict[str, float]:
        return {name: self._knobs[name] for name in knob_names}

    def set_knobs(self, knob_values: Mapping[str, float]) -> None:
        for name, value in knob_values.items():
            if name not in self._knobs:
                raise KeyError(f"Unknown knob: {name}")
            self._knobs[name] = float(value)

    def apply_device_deltas(self, device_deltas: Mapping[str, float]) -> None:
        self._device_values = {str(name): float(value) for name, value in device_deltas.items()}

    def snapshot(self) -> MachineSnapshot:
        return MachineSnapshot(
            energy_delta=self._energy_delta,
            device_values=dict(self._knobs),
            charge=self._charge,
            loss=self._loss,
            metadata={"backend": self.backend_name, "devices": dict(self._device_values)},
        )

    def restore(self, snapshot: MachineSnapshot) -> None:
        self._energy_delta = float(snapshot.energy_delta)
        self._knobs = {name: float(value) for name, value in snapshot.device_values.items()}
        self._charge = snapshot.charge if snapshot.charge is not None else self._charge
        self._loss = snapshot.loss if snapshot.loss is not None else self._loss
        devices = snapshot.metadata.get("devices") if snapshot.metadata else None
        if isinstance(devices, dict):
            self._device_values = {str(name): float(value) for name, value in devices.items()}

    def wait_stable(self) -> None:
        settle_time_s = max(0.0, float(self.config.measurement.settle_time_s))
        if settle_time_s > 0:
            time.sleep(min(settle_time_s, 0.05))

    def is_safe(self) -> bool:
        return not self.unsafe

    def current_dispersion(self) -> np.ndarray:
        knob_vector = np.asarray([self._knobs[knob.name] for knob in self.config.knobs], dtype=float)
        return self._initial_dispersion + self._response @ knob_vector

    def read_quadrupole_readbacks(self) -> dict[str, float]:
        if not self._quadrupole_readbacks:
            return dict(self._knobs)
        values = dict(self._quadrupole_readbacks)
        for name, delta in self._device_values.items():
            values[name] = values.get(name, 0.0) + float(delta)
        return values
