"""Read a screen only after the existing VM result acknowledges a query."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
import json
import math

from half_linac.src.virtual_machine.workbench_data import input_version, observation_dir


class VmObservationError(RuntimeError):
    """The VM cannot supply an image belonging to the requested input."""


class VmPointReader:
    """Bracket an uncached image read with coherent VM input/result checks.

    The VM publishes once per changed input, so this reader returns one frame
    per query. Repeated reads of that frame are not independent observations.
    """

    def __init__(
        self, runtime_json: str | Path, *, screen: str, scan_quad: str, image_pv: str,
        timeout: float, poll_interval: float, clock: Callable[[], float],
        sleeper: Callable[[float], None],
    ) -> None:
        self.runtime_json = Path(runtime_json)
        self.directory = observation_dir(self.runtime_json)
        self.screen = screen
        self.scan_quad = scan_quad
        self.image_pv = image_pv
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.clock = clock
        self.sleep = sleeper

    @staticmethod
    def _load(path: Path) -> dict[str, Any]:
        value = json.loads(path.read_bytes())
        if not isinstance(value, dict):
            raise ValueError(f"expected an object in {path.name}")
        return value

    def _ready(self, expected: Mapping[str, tuple[str, float, float]]):
        state = self._load(self.runtime_json)
        usedline = state["usedline"]
        lattice = state["lattice"]
        for element in (*expected, self.screen):
            if usedline.count(element) != 1:
                raise VmObservationError(f"VM line must contain {element} exactly once")
        scan_index = usedline.index(self.scan_quad)
        if scan_index >= usedline.index(self.screen) or any(
            usedline.index(element) >= scan_index
            for element in expected if element != self.scan_quad
        ):
            raise VmObservationError("VM workpoints must be upstream of the scan quadrupole")
        for element, (field, requested, tolerance) in expected.items():
            actual = float(lattice[element][field])
            if not math.isfinite(actual) or abs(actual - requested) > tolerance:
                raise ValueError("waiting for IOC setpoints to reach VM input")
        screen = lattice[self.screen]
        if (
            screen["TYPE"].upper() != "WATCH"
            or str(screen.get("DISABLE", "0")) not in {"0", "0.0"}
            or str(screen.get("MODE", "")).lower() != "coord"
        ):
            raise VmObservationError("requested VM screen is not an enabled coordinate WATCH")
        version = input_version(state)
        status = self._load(self.directory / "status.json")
        if status.get("input_version") == version and status.get("phase") == "Failed":
            raise VmObservationError(f"VM calculation failed: {status.get('error')}")
        if not (
            status.get("phase") == "Ready"
            and status.get("input_version") == status.get("result_version") == version
        ):
            raise ValueError("waiting for VM result matching the query input")
        if status.get("publication", {}).get("screens") is not True:
            raise VmObservationError("VM screen publication did not succeed")
        if status.get("publication", {}).get("screens_acknowledged") is not True:
            raise VmObservationError("VM image publication is unacknowledged; restart the updated VM")
        if self.image_pv not in status["publication"].get("screen_image_pvs", ()):
            raise VmObservationError("VM did not acknowledge the requested screen image PV")
        result = self._load(self.directory / "result.json")
        if not (
            result.get("input_version") == version
            and input_version(result["input_state"]) == version
            and result.get("session") == status["session"]
            and result.get("calculation") == status["calculation"]
        ):
            raise ValueError("waiting for a coherent VM result")
        return (status["session"], status["calculation"], version)

    def read(self, expected, read_image: Callable[[], Any]):
        deadline = self.clock() + self.timeout
        reason = "VM result is unavailable"
        while True:
            try:
                token = self._ready(expected)
                raw = read_image()
                if self._ready(expected) == token:
                    return raw, {
                        "vm_session": token[0], "vm_calculation": token[1],
                        "vm_input_version": token[2],
                    }
                reason = "VM result changed while reading the image"
            except (OSError, ValueError, KeyError, TypeError) as error:
                reason = str(error)
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise VmObservationError(f"VM image timeout: {reason}")
            self.sleep(min(self.poll_interval, remaining))
