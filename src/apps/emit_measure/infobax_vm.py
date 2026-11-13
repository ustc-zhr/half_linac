"""HALF virtual-machine adapter for single-point InfoBAX measurements.

The adapter accepts only the HALF ``vm`` control backend.  Importing this
module performs no PV access and does not import EPICS or Qt.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any
import hashlib
import json
import math
import time

import numpy as np

from half_linac.src.optimization.infobax import FinitePoolSpec
from half_linac.src.shared.beam_matrix import build_measurement_matrix
from .infobax_session import PointMeasurement


class HalfVmInfoBAXError(RuntimeError):
    """Base class for HALF VM InfoBAX adapter failures."""


class HalfVmMeasurementError(HalfVmInfoBAXError):
    """A screen frame could not provide a valid scalar variance."""


class HalfVmRestoreError(HalfVmInfoBAXError):
    """One or more original VM setpoints could not be restored."""


@dataclass(frozen=True)
class MachineVariable:
    element_id: str
    quantity: str = "K1"
    unit: str = "1/m^2"
    tolerance: float = 1.0e-6

    def __post_init__(self) -> None:
        for name in ("element_id", "quantity", "unit"):
            value = str(getattr(self, name)).strip()
            if not value:
                raise ValueError(f"{name} must not be empty")
            object.__setattr__(self, name, value)
        tolerance = float(self.tolerance)
        if not math.isfinite(tolerance) or tolerance <= 0.0:
            raise ValueError("variable tolerance must be positive and finite")
        object.__setattr__(self, "tolerance", tolerance)


@dataclass(frozen=True)
class FiniteWorkpointTable:
    """Physical machine setpoints corresponding to finite-pool candidate IDs."""

    candidate_ids: Sequence[str]
    variables: Sequence[MachineVariable]
    values: Any

    def __post_init__(self) -> None:
        candidate_ids = tuple(self.candidate_ids)
        variables = tuple(self.variables)
        if not candidate_ids or any(
            not isinstance(value, str) or not value for value in candidate_ids
        ):
            raise ValueError("candidate_ids must be non-empty strings")
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("candidate_ids must be unique")
        if not variables or any(
            not isinstance(value, MachineVariable) for value in variables
        ):
            raise ValueError("variables must contain MachineVariable values")
        variable_keys = [
            (value.element_id, value.quantity.casefold()) for value in variables
        ]
        if len(set(variable_keys)) != len(variable_keys):
            raise ValueError("machine variables must be unique")
        values = np.array(self.values, dtype=float, copy=True)
        if values.shape != (len(candidate_ids), len(variables)):
            raise ValueError("workpoint values must have shape (candidate_count, dimension)")
        if not np.all(np.isfinite(values)):
            raise ValueError("workpoint values must be finite")
        if len(candidate_ids) < 2 or np.any(np.ptp(values, axis=0) <= 0.0):
            raise ValueError("every workpoint variable must vary across the finite pool")
        if len(np.unique(values, axis=0)) != len(candidate_ids):
            raise ValueError("physical workpoints must be unique")
        values.setflags(write=False)
        object.__setattr__(self, "candidate_ids", candidate_ids)
        object.__setattr__(self, "variables", variables)
        object.__setattr__(self, "values", values)

    @property
    def normalized_coordinates(self) -> np.ndarray:
        low = np.min(self.values, axis=0)
        span = np.ptp(self.values, axis=0)
        result = (self.values - low) / span
        result.setflags(write=False)
        return result

    @property
    def fingerprint(self) -> str:
        payload = {
            "candidate_ids": list(self.candidate_ids),
            "variables": [asdict(value) for value in self.variables],
            "values": self.values.tolist(),
        }
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class _ResolvedContract:
    workpoint_targets: tuple[Any, ...]
    scan_target: Any
    image_pv: str


class HalfVmInfoBAXMachine:
    """Execute finite workpoint/K1 queries against the HALF VM PV surface."""

    def __init__(
        self,
        context,
        workpoints: FiniteWorkpointTable,
        *,
        scan_quad: str,
        screen: str,
        plane: str,
        get: Callable[[str], Any] | None = None,
        put: Callable[[str, float], Any] | None = None,
        analyzer: Callable[..., Any] | None = None,
        image_samples: int = 1,
        sample_interval: float = 0.0,
        settle_time: float = 0.0,
        scan_tolerance: float = 1.0e-6,
        motion_timeout: float = 5.0,
        poll_interval: float = 0.05,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if (get is None) != (put is None):
            raise ValueError("get and put must be injected together")
        if plane not in {"x", "y"}:
            raise ValueError("plane must be 'x' or 'y'")
        if not isinstance(image_samples, int) or image_samples < 1:
            raise ValueError("image_samples must be a positive integer")
        for name, value, allow_zero in (
            ("sample_interval", sample_interval, True),
            ("settle_time", settle_time, True),
            ("scan_tolerance", scan_tolerance, False),
            ("motion_timeout", motion_timeout, False),
            ("poll_interval", poll_interval, False),
        ):
            number = float(value)
            if not math.isfinite(number) or (number < 0.0 if allow_zero else number <= 0.0):
                raise ValueError(f"{name} is invalid")

        contract = _resolve_contract(context, workpoints, scan_quad, screen)
        self.context = context
        self.workpoints = workpoints
        self.scan_quad = scan_quad
        self.screen = screen
        self.plane = plane
        self._workpoint_targets = contract.workpoint_targets
        self._scan_target = contract.scan_target
        self._image_pv = contract.image_pv
        from half_linac.src.shared.machine_profile.pixel_geometry import (
            resolve_element_image_geometry,
        )

        self._geometry = resolve_element_image_geometry(context, screen, "vm")
        self._get = get
        self._put = put
        self._analyzer = analyzer
        self.image_samples = image_samples
        self.sample_interval = float(sample_interval)
        self.settle_time = float(settle_time)
        self.scan_tolerance = float(scan_tolerance)
        self.motion_timeout = float(motion_timeout)
        self.poll_interval = float(poll_interval)
        self._clock = clock
        self._sleep = sleeper
        self._targets = self._workpoint_targets + (self._scan_target,)

        payload = {
            "adapter": "half-vm-infobax-v1",
            "workpoints": workpoints.fingerprint,
            "scan_quad": scan_quad,
            "screen": screen,
            "plane": plane,
            "measurement": {
                "image_samples": self.image_samples,
                "sample_interval": self.sample_interval,
                "settle_time": self.settle_time,
                "scan_tolerance": self.scan_tolerance,
                "motion_timeout": self.motion_timeout,
                "poll_interval": self.poll_interval,
                "image_shape": list(self._geometry.shape),
                "pixel_width_mm": self._geometry.pixel_width_mm,
                "flip_y": self._geometry.flip_y,
            },
            "targets": [
                {
                    "element_id": target.element_id,
                    "logical_channel": target.logical_channel,
                    "pv_name": target.pv_name,
                }
                for target in self._targets
            ],
            "image_pv": self._image_pv,
        }
        self._fingerprint = hashlib.sha256(
            json.dumps(
                payload, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode("utf-8")
        ).hexdigest()

    @property
    def fingerprint(self) -> str:
        return self._fingerprint

    @property
    def candidate_ids(self) -> tuple[str, ...]:
        return tuple(self.workpoints.candidate_ids)

    def snapshot(self) -> dict[str, float]:
        return {target.pv_name: self._read_scalar(target.pv_name) for target in self._targets}

    def verify_snapshot(self, snapshot: Mapping[str, float]) -> None:
        expected_keys = {target.pv_name for target in self._targets}
        if set(snapshot) != expected_keys:
            raise HalfVmInfoBAXError("machine snapshot channels do not match adapter")
        errors = []
        for index, target in enumerate(self._targets):
            expected = float(snapshot[target.pv_name])
            tolerance = self._target_tolerance(index)
            try:
                actual = self._read_scalar(target.pv_name)
                if abs(actual - expected) > tolerance:
                    errors.append(
                        f"{target.pv_name}: expected {expected:g}, got {actual:g}"
                    )
            except Exception as error:
                errors.append(f"{target.pv_name}: {error}")
        if errors:
            raise HalfVmInfoBAXError("machine snapshot mismatch: " + "; ".join(errors))

    def apply_query(self, candidate_index: int, scan_value: float) -> None:
        if (
            not isinstance(candidate_index, int)
            or isinstance(candidate_index, bool)
            or candidate_index < 0
            or candidate_index >= len(self.workpoints.candidate_ids)
        ):
            raise IndexError("candidate index outside finite workpoint table")
        scan = float(scan_value)
        if not math.isfinite(scan) or not self._scan_target.machine_limit.contains(scan):
            raise HalfVmInfoBAXError(
                f"scan value {scan!r} is outside {self._scan_target.machine_limit.describe()}"
            )
        for column, target in enumerate(self._workpoint_targets):
            self._write_and_verify(
                target,
                float(self.workpoints.values[candidate_index, column]),
                self.workpoints.variables[column].tolerance,
            )
        self._write_and_verify(self._scan_target, scan, self.scan_tolerance)
        if self.settle_time:
            self._sleep(self.settle_time)

    def measure_point(
        self, query_index: int, expected_noise_std_mm2: float
    ) -> PointMeasurement:
        noise = float(expected_noise_std_mm2)
        if not math.isfinite(noise) or noise <= 0.0:
            raise ValueError("expected observation noise must be positive and finite")
        variances = []
        statuses = []
        extent = _image_extent(self._geometry.shape, self._geometry.pixel_width_mm)
        for sample_index in range(self.image_samples):
            raw = self._read_value(self._image_pv)
            if raw is None:
                raise HalfVmMeasurementError(
                    f"failed to read screen image PV {self._image_pv}"
                )
            try:
                analyzer = self._analyzer
                if analyzer is None:
                    from half_linac.src.shared.beam_diagnostics.image_fit import (
                        analyze_raw_beam_image,
                    )

                    analyzer = analyze_raw_beam_image
                analysis = analyzer(
                    raw,
                    pixel_shape=self._geometry.shape,
                    extent=extent,
                    flip_y=self._geometry.flip_y,
                )
            except (TypeError, ValueError) as error:
                raise HalfVmMeasurementError(
                    f"invalid image from {self._image_pv}: {error}"
                ) from error
            fit = analysis[1] if isinstance(analysis, tuple) else analysis
            status = str(getattr(fit, "status", "unknown"))
            statuses.append(status)
            if not bool(getattr(fit, "valid", False)):
                message = str(getattr(fit, "message", ""))
                raise HalfVmMeasurementError(
                    f"screen image fit is not valid ({status}): {message}"
                )
            sigma = getattr(fit, "sigx_mm" if self.plane == "x" else "sigy_mm")
            if sigma is None or not math.isfinite(float(sigma)) or float(sigma) <= 0.0:
                raise HalfVmMeasurementError("screen fit returned an invalid beam size")
            variances.append(float(sigma) ** 2)
            if sample_index + 1 < self.image_samples and self.sample_interval:
                self._sleep(self.sample_interval)
        return PointMeasurement(
            variance_mm2=float(np.mean(variances)),
            noise_std_mm2=noise,
            metadata={
                "query_index": int(query_index),
                "screen": self.screen,
                "plane": self.plane,
                "image_pv": self._image_pv,
                "sample_count": self.image_samples,
                "fit_statuses": statuses,
                "sample_variances_mm2": variances,
            },
        )

    def restore(self, snapshot: Mapping[str, float]) -> None:
        expected_keys = {target.pv_name for target in self._targets}
        if set(snapshot) != expected_keys:
            raise HalfVmRestoreError("machine snapshot channels do not match adapter")
        errors = []
        for index, target in enumerate(self._targets):
            try:
                self._write_and_verify(
                    target,
                    float(snapshot[target.pv_name]),
                    self._target_tolerance(index),
                )
            except Exception as error:
                errors.append(f"{target.pv_name}: {error}")
        try:
            self.verify_snapshot(snapshot)
        except Exception as error:
            errors.append(str(error))
        if errors:
            raise HalfVmRestoreError("restore failed: " + "; ".join(errors))

    def _target_tolerance(self, target_index: int) -> float:
        if target_index < len(self.workpoints.variables):
            return self.workpoints.variables[target_index].tolerance
        return self.scan_tolerance

    def _write_and_verify(
        self, target: Any, value: float, tolerance: float
    ) -> None:
        result = self._write_value(target.pv_name, value)
        if result is False:
            raise HalfVmInfoBAXError(f"write failed for {target.pv_name}")
        deadline = self._clock() + self.motion_timeout
        while True:
            actual = self._read_scalar(target.pv_name)
            if abs(actual - value) <= tolerance:
                return
            if self._clock() >= deadline:
                raise HalfVmInfoBAXError(
                    f"readback timeout for {target.pv_name}: requested {value:g}, "
                    f"got {actual:g}"
                )
            self._sleep(self.poll_interval)

    def _read_scalar(self, pv_name: str) -> float:
        raw = self._read_value(pv_name)
        try:
            value = float(raw)
        except (TypeError, ValueError) as error:
            raise HalfVmInfoBAXError(f"non-numeric readback from {pv_name}") from error
        if not math.isfinite(value):
            raise HalfVmInfoBAXError(f"non-finite readback from {pv_name}")
        return value

    def _read_value(self, pv_name: str) -> Any:
        if self._get is not None:
            return self._get(pv_name)
        import epics

        return epics.caget(pv_name)

    def _write_value(self, pv_name: str, value: float) -> Any:
        if self._put is not None:
            return self._put(pv_name, value)
        import epics

        return epics.caput(pv_name, value, wait=True, timeout=self.motion_timeout)


def build_half_vm_infobax_pool(
    context,
    workpoints: FiniteWorkpointTable,
    *,
    scan_quad: str,
    screen: str,
    scan_values: Sequence[float],
    plane: str,
    energy_mev: float,
    observation_noise_std_mm2: float | Sequence[float],
    model_line: str | None = None,
    model_backend=None,
) -> FinitePoolSpec:
    """Build a finite pool with a HALF elegant quad-to-screen design matrix."""

    if plane not in {"x", "y"}:
        raise ValueError("plane must be 'x' or 'y'")
    energy = float(energy_mev)
    if not math.isfinite(energy) or energy <= 0.0:
        raise ValueError("energy_mev must be positive and finite")
    contract = _resolve_contract(context, workpoints, scan_quad, screen)
    scans = np.asarray(scan_values, dtype=float)
    if scans.ndim != 1 or len(scans) < 3 or not np.all(np.isfinite(scans)):
        raise ValueError("scan_values must contain at least three finite values")
    if len(np.unique(scans)) != len(scans):
        raise ValueError("scan_values must be unique")
    for value in scans:
        if not contract.scan_target.machine_limit.contains(float(value)):
            raise ValueError(
                f"scan value {value:g} is outside "
                f"{contract.scan_target.machine_limit.describe()}"
            )
    span = float(np.ptp(scans))
    if span <= 0.0:
        raise ValueError("scan_values must span a non-zero interval")
    scan_coordinates = (scans - np.min(scans)) / span

    if model_backend is None:
        from half_linac.src.shared.machine_profile.model_backend import (
            build_model_backend,
        )

        backend = build_model_backend(
            context, energy_mev=energy, line_name=model_line
        )
    else:
        backend = model_backend
    projections = []
    for k1 in scans:
        matrix = np.asarray(
            backend.get_map(
                scan_quad,
                screen,
                k1=float(k1),
                seq="ent2exit",
                twiss_only=True,
            ),
            dtype=float,
        )
        if matrix.ndim != 2 or matrix.shape[0] < 4 or matrix.shape[1] < 4:
            raise ValueError("model backend returned an invalid transport matrix")
        projection = (matrix[0, 0], matrix[0, 1]) if plane == "x" else (
            matrix[2, 2],
            matrix[2, 3],
        )
        projections.append(projection)

    return FinitePoolSpec(
        candidate_ids=workpoints.candidate_ids,
        candidate_coordinates=workpoints.normalized_coordinates,
        scan_values=scans,
        scan_coordinates=scan_coordinates,
        design_matrix=build_measurement_matrix(projections),
        energy_mev=energy,
        observation_noise_std_mm2=observation_noise_std_mm2,
    )


def _resolve_contract(
    context,
    workpoints: FiniteWorkpointTable,
    scan_quad: str,
    screen: str,
) -> _ResolvedContract:
    from half_linac.src.shared.machine_profile.resolver import (
        resolve_channel,
        resolve_write_target,
    )
    from half_linac.src.shared.machine_profile.write_control import (
        require_workflow_write_allowed,
    )

    if getattr(getattr(context, "profile", None), "machine", None) is None:
        raise ValueError("context must be a machine-profile AppContext")
    if context.profile.machine.id != "half":
        raise HalfVmInfoBAXError("InfoBAX VM adapter supports only machine 'half'")
    if getattr(getattr(context, "control_backend", None), "name", None) != "vm":
        raise HalfVmInfoBAXError("InfoBAX machine adapter permits only backend 'vm'")
    if not isinstance(workpoints, FiniteWorkpointTable):
        raise TypeError("workpoints must be a FiniteWorkpointTable")
    if any(variable.element_id == scan_quad for variable in workpoints.variables):
        raise HalfVmInfoBAXError(
            "scan quadrupole cannot also be a workpoint variable"
        )
    require_workflow_write_allowed(
        context, "emit_measure", "execute InfoBAX VM point"
    )
    targets = []
    for column, variable in enumerate(workpoints.variables):
        target = resolve_write_target(
            context,
            variable.element_id,
            quantity=variable.quantity,
            unit=variable.unit,
            mode="vm",
        )
        for value in workpoints.values[:, column]:
            if not target.machine_limit.contains(float(value)):
                raise HalfVmInfoBAXError(
                    f"{variable.element_id} value {value:g} is outside "
                    f"{target.machine_limit.describe()}"
                )
        targets.append(target)
    scan_target = resolve_write_target(
        context,
        scan_quad,
        quantity="K1",
        unit="1/m^2",
        mode="vm",
    )
    if scan_target.pv_name in {target.pv_name for target in targets}:
        raise HalfVmInfoBAXError("scan and workpoint variables resolve to the same PV")
    image_pv = resolve_channel(context, screen, "image", mode="vm")
    return _ResolvedContract(tuple(targets), scan_target, image_pv)


def _image_extent(shape: Sequence[int], pixel_width_mm: float) -> tuple[float, ...]:
    width = int(shape[0]) * float(pixel_width_mm)
    height = int(shape[1]) * float(pixel_width_mm)
    return (-0.5 * width, 0.5 * width, -0.5 * height, 0.5 * height)


__all__ = [
    "FiniteWorkpointTable",
    "HalfVmInfoBAXError",
    "HalfVmInfoBAXMachine",
    "HalfVmMeasurementError",
    "HalfVmRestoreError",
    "MachineVariable",
    "build_half_vm_infobax_pool",
]
