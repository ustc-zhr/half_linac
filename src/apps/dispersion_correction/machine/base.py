from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence

from half_linac.src.apps.dispersion_correction.models import BPMReading, MachineSnapshot


class MachineInterface(ABC):
    """Minimal machine contract required by the MVP achromat workflow."""

    @abstractmethod
    def read_bpm(self, bpm_names: Sequence[str]) -> BPMReading:
        raise NotImplementedError

    @abstractmethod
    def get_energy_delta(self) -> float:
        raise NotImplementedError

    @abstractmethod
    def set_energy_delta(self, value: float) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_knobs(self, knob_names: Sequence[str]) -> dict[str, float]:
        raise NotImplementedError

    @abstractmethod
    def set_knobs(self, knob_values: Mapping[str, float]) -> None:
        raise NotImplementedError

    @abstractmethod
    def apply_device_deltas(self, device_deltas: Mapping[str, float]) -> None:
        raise NotImplementedError

    @abstractmethod
    def snapshot(self) -> MachineSnapshot:
        raise NotImplementedError

    @abstractmethod
    def restore(self, snapshot: MachineSnapshot) -> None:
        raise NotImplementedError

    @abstractmethod
    def wait_stable(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def is_safe(self) -> bool:
        raise NotImplementedError

    @property
    @abstractmethod
    def backend_name(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def mode(self) -> str:
        raise NotImplementedError
