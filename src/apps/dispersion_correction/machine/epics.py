from __future__ import annotations

from collections.abc import Mapping, Sequence
import time

import numpy as np

from half_linac.src.apps.dispersion_correction.calibration import (
    calibration_actuator_per_delta,
    is_direct_delta_actuator,
)
from half_linac.src.apps.dispersion_correction.machine.base import MachineInterface
from half_linac.src.apps.dispersion_correction.models import BPMReading, MachineSnapshot, RunConfig


class EpicsMachine(MachineInterface):
    """EPICS adapter with calibrated energy and relative quadrupole writes."""

    def __init__(self, config: RunConfig, epics_client=None) -> None:
        self.config = config
        self._mode = config.backend.mode
        self._allow_write = self._mode == "write_enabled"
        self._ca_timeout = float(config.backend.options.get("ca_timeout", 1.0))
        self._write_timeout = float(config.backend.options.get("write_timeout", self._ca_timeout))
        self._readback_timeout = float(config.backend.options.get("readback_timeout", 2.0))
        self._poll_interval = float(config.backend.options.get("readback_poll_interval", 0.05))
        self._bpm_position_scale_to_mm = float(
            config.backend.options.get("bpm_position_scale_to_mm", 1.0)
        )
        if not np.isfinite(self._bpm_position_scale_to_mm) or self._bpm_position_scale_to_mm <= 0:
            raise ValueError("backend.options.bpm_position_scale_to_mm must be finite and positive")
        default_energy_tolerance = (
            config.energy_knob.readback_tolerance
            if config.energy_knob.readback_tolerance is not None
            else 1.0e-6
        )
        self._energy_tolerance = float(
            config.backend.options.get(
                "energy_knob_readback_tolerance",
                config.backend.options.get("energy_readback_tolerance", default_energy_tolerance),
            )
        )
        quadrupole_tolerance = config.backend.options.get("quadrupole_readback_tolerance")
        self._quadrupole_tolerance_override = (
            None if quadrupole_tolerance is None else float(quadrupole_tolerance)
        )
        self._pv_map = config.backend.options.get("pv_map", {})
        if not isinstance(self._pv_map, dict):
            raise ValueError("backend.options.pv_map must be a mapping")
        if epics_client is not None:
            self._epics = epics_client
            self._import_error = None
        else:
            try:
                import epics  # type: ignore
            except Exception as exc:  # pragma: no cover - depends on site environment
                self._epics = None
                self._import_error = exc
            else:  # pragma: no cover - exercised only with pyepics installed
                self._epics = epics
                self._import_error = None
        self._knob_values = {knob.name: 0.0 for knob in config.knobs}
        self._device_baseline: dict[str, float] | None = None
        if self._allow_write:
            self._validate_write_configuration()

    @property
    def backend_name(self) -> str:
        return "epics"

    @property
    def mode(self) -> str:
        return self._mode

    def read_bpm(self, bpm_names: Sequence[str]) -> BPMReading:
        bpm_map = self._mapping("bpms")
        x_values = []
        y_values = []
        valid = []
        for name in bpm_names:
            item = self._nested_mapping(bpm_map, name, "BPM")
            x_pv = self._required_pv(item, "x", f"BPM {name}")
            y_pv = item.get("y")
            x_value = self._caget_float(x_pv)
            raw_y_value = self._caget_float(y_pv) if y_pv else 0.0
            y_value = raw_y_value if np.isfinite(raw_y_value) else 0.0
            x_values.append(x_value * self._bpm_position_scale_to_mm)
            y_values.append(y_value * self._bpm_position_scale_to_mm)
            valid.append(np.isfinite(x_value))

        diagnostics = self._pv_map.get("diagnostics", {})
        charge = self._optional_caget_float(diagnostics.get("charge")) if isinstance(diagnostics, dict) else None
        loss = self._optional_caget_float(diagnostics.get("loss")) if isinstance(diagnostics, dict) else None

        return BPMReading(
            names=tuple(bpm_names),
            x_mm=np.asarray(x_values, dtype=float),
            y_mm=np.asarray(y_values, dtype=float),
            valid=np.asarray(valid, dtype=bool),
            charge=charge,
            loss=loss,
        )

    def get_energy_delta(self) -> float:
        actuator_value = self._read_energy_actuator()
        scale = self._energy_actuator_per_delta()
        return actuator_value / scale

    def get_energy_setpoint_delta(self) -> float:
        actuator_value = self._read_energy_setpoint()
        scale = self._energy_actuator_per_delta()
        return actuator_value / scale

    def set_energy_delta(self, value: float) -> None:
        self._require_write_enabled()
        item = self._mapping("energy_knob")
        set_pv = item.get("set") or item.get("phase_set")
        if not set_pv:
            raise ValueError("pv_map.energy_knob requires set for writes")
        readback_pv = item.get("readback") or item.get("phase_readback") or set_pv
        scale = self._energy_actuator_per_delta()
        actuator_target = float(value) * scale
        self._write_and_verify(
            str(set_pv),
            actuator_target,
            str(readback_pv),
            self._energy_tolerance,
            "energy knob",
        )

    def get_knobs(self, knob_names: Sequence[str]) -> dict[str, float]:
        unknown = [name for name in knob_names if name not in self._knob_values]
        if unknown:
            raise KeyError(f"Unknown knobs: {', '.join(unknown)}")
        return {name: self._knob_values[name] for name in knob_names}

    def set_knobs(self, knob_values: Mapping[str, float]) -> None:
        self._require_write_enabled()
        limits = {knob.name: knob.limit for knob in self.config.knobs}
        for name, value in knob_values.items():
            if name not in self._knob_values:
                raise KeyError(f"Unknown knob: {name}")
            numeric = float(value)
            if not np.isfinite(numeric):
                raise ValueError(f"Knob {name} target must be finite")
            if abs(numeric) > limits[name] + 1.0e-15:
                raise ValueError(f"Knob {name} target {numeric:g} exceeds configured limit ±{limits[name]:g}")
        self._knob_values.update({name: float(value) for name, value in knob_values.items()})

    def apply_device_deltas(self, device_deltas: Mapping[str, float]) -> None:
        self._require_write_enabled()
        if self._device_baseline is None:
            self._device_baseline = self.read_quadrupole_readbacks()
        targets: dict[str, float] = {}
        for name, delta in device_deltas.items():
            if name not in self._device_baseline:
                raise KeyError(f"Unknown quadrupole device: {name}")
            numeric = float(delta)
            if not np.isfinite(numeric):
                raise ValueError(f"Quadrupole {name} delta must be finite")
            targets[name] = self._device_baseline[name] + numeric
        self._write_quadrupole_targets(targets)

    def set_device_targets(self, device_targets: Mapping[str, float]) -> None:
        """Write explicitly reviewed physical quadrupole targets."""

        self._require_write_enabled()
        targets = {str(name): float(value) for name, value in device_targets.items()}
        if not targets:
            raise ValueError("At least one quadrupole target is required")
        if not all(np.isfinite(value) for value in targets.values()):
            raise ValueError("Quadrupole targets must be finite")
        self._write_quadrupole_targets(targets)

    def snapshot(self) -> MachineSnapshot:
        energy_setpoint = self.get_energy_setpoint_delta()
        energy_readback = self.get_energy_delta()
        device_values = self.read_quadrupole_readbacks()
        if self._device_baseline is None:
            self._device_baseline = dict(device_values)
        diagnostics = self._pv_map.get("diagnostics", {})
        charge = self._optional_caget_float(diagnostics.get("charge")) if isinstance(diagnostics, dict) else None
        loss = self._optional_caget_float(diagnostics.get("loss")) if isinstance(diagnostics, dict) else None
        return MachineSnapshot(
            energy_delta=energy_setpoint,
            device_values=device_values,
            charge=charge,
            loss=loss,
            metadata={
                "backend": self.backend_name,
                "mode": self.mode,
                "energy_readback_delta": energy_readback,
                "quadrupole_readbacks": device_values,
                "knob_values": dict(self._knob_values),
            },
        )

    def restore(self, snapshot: MachineSnapshot) -> None:
        self._require_write_enabled()
        errors: list[str] = []
        try:
            self.set_energy_delta(snapshot.energy_delta)
        except Exception as exc:
            errors.append(f"energy: {exc}")
        try:
            self._write_quadrupole_targets(snapshot.device_values)
        except Exception as exc:
            errors.append(f"quadrupoles: {exc}")
        if errors:
            raise RuntimeError("EPICS restore failed: " + "; ".join(errors))
        knob_values = snapshot.metadata.get("knob_values", {}) if snapshot.metadata else {}
        if isinstance(knob_values, dict):
            for name in self._knob_values:
                if name in knob_values:
                    self._knob_values[name] = float(knob_values[name])

    def wait_stable(self) -> None:
        settle_time_s = max(0.0, float(self.config.measurement.settle_time_s))
        if settle_time_s > 0:
            time.sleep(settle_time_s)

    def is_safe(self) -> bool:
        if self._epics is None:
            return False
        try:
            energy = self.get_energy_delta()
            quadrupoles = self.read_quadrupole_readbacks()
            bpm = self.read_bpm(self.config.target_bpms)
        except Exception:
            return False
        return bool(
            np.isfinite(energy)
            and quadrupoles
            and all(np.isfinite(value) for value in quadrupoles.values())
            and len(bpm.valid) == len(self.config.target_bpms)
            and np.all(bpm.valid)
        )

    def read_quadrupole_readbacks(self) -> dict[str, float]:
        quadrupoles = self._mapping("quadrupoles")
        values: dict[str, float] = {}
        for name, raw in quadrupoles.items():
            if not isinstance(raw, dict):
                raise ValueError(f"Quadrupole {name} mapping must be a mapping")
            values[str(name)] = self._read_quadrupole(str(name))
        return values

    def read_quadrupole_setpoints(self) -> dict[str, float]:
        quadrupoles = self._mapping("quadrupoles")
        values: dict[str, float] = {}
        for name, raw in quadrupoles.items():
            if not isinstance(raw, dict):
                raise ValueError(f"Quadrupole {name} mapping must be a mapping")
            control = self._quadrupole_control(raw, str(name))
            key = "current_set" if control == "current" else "K1_set"
            pv = raw.get(key) or (raw.get("K1") if control == "k1" else None)
            if not pv:
                raise ValueError(f"Quadrupole {name} requires a {control} setpoint PV")
            value = self._caget_float(str(pv))
            if not np.isfinite(value):
                raise RuntimeError(f"Quadrupole {name} setpoint is unavailable: {pv}")
            values[str(name)] = value
        return values

    def quadrupole_readback_tolerance(self, name: str) -> float:
        """Return the configured same-unit setpoint/readback tolerance."""

        return self._quadrupole_tolerance(name)

    def _read_energy_actuator(self) -> float:
        item = self._mapping("energy_knob")
        pv = (
            item.get("readback")
            or item.get("phase_readback")
            or item.get("set")
            or item.get("phase_set")
        )
        if not pv:
            raise ValueError("pv_map.energy_knob requires readback or set")
        value = self._caget_float(str(pv))
        if not np.isfinite(value):
            raise RuntimeError(f"Energy knob readback is unavailable: {pv}")
        return value

    def _read_energy_setpoint(self) -> float:
        item = self._mapping("energy_knob")
        pv = item.get("set") or item.get("phase_set")
        if not pv:
            raise ValueError("pv_map.energy_knob requires a setpoint PV")
        value = self._caget_float(str(pv))
        if not np.isfinite(value):
            raise RuntimeError(f"Energy knob setpoint is unavailable: {pv}")
        return value

    def _energy_actuator_per_delta(self) -> float:
        if is_direct_delta_actuator(self.config.energy_knob.actuator):
            return 1.0
        try:
            scale = calibration_actuator_per_delta(self.config.energy_knob.calibration)
        except (TypeError, ValueError) as exc:
            raise ValueError(str(exc)) from exc
        if scale is None:
            raise ValueError(
                "Physical energy actuator requires calibration.actuator_per_delta"
            )
        return scale

    def _write_quadrupole_targets(self, targets: Mapping[str, float]) -> None:
        if not targets:
            return
        previous = {name: self._read_quadrupole(name) for name in targets}
        attempted: list[str] = []
        try:
            for name, target in targets.items():
                attempted.append(name)
                set_pv, readback_pv = self._quadrupole_write_pvs(name)
                self._write_and_verify(
                    set_pv,
                    float(target),
                    readback_pv,
                    self._quadrupole_tolerance(name),
                    f"quadrupole {name}",
                )
        except Exception as exc:
            rollback_errors: list[str] = []
            for name in reversed(attempted):
                try:
                    set_pv, readback_pv = self._quadrupole_write_pvs(name)
                    self._write_and_verify(
                        set_pv,
                        previous[name],
                        readback_pv,
                        self._quadrupole_tolerance(name),
                        f"quadrupole {name} rollback",
                    )
                except Exception as rollback_exc:
                    rollback_errors.append(str(rollback_exc))
            message = f"Quadrupole write failed: {exc}"
            if rollback_errors:
                message += "; rollback failed: " + "; ".join(rollback_errors)
            raise RuntimeError(message) from exc

    def _read_quadrupole(self, name: str) -> float:
        item = self._nested_mapping(self._mapping("quadrupoles"), name, "Quadrupole")
        control = self._quadrupole_control(item, name)
        if control == "current":
            readback_pv = item.get("current_readback") or item.get("current_set")
        else:
            readback_pv = item.get("K1_readback") or item.get("K1")
        if not readback_pv:
            raise ValueError(f"Quadrupole {name} requires a {control} readback/setpoint PV")
        value = self._caget_float(str(readback_pv))
        if not np.isfinite(value):
            raise RuntimeError(f"Quadrupole {name} readback is unavailable: {readback_pv}")
        return value

    def _quadrupole_write_pvs(self, name: str) -> tuple[str, str]:
        item = self._nested_mapping(self._mapping("quadrupoles"), name, "Quadrupole")
        control = self._quadrupole_control(item, name)
        if control == "current":
            set_pv = item.get("current_set")
            readback_pv = item.get("current_readback") or item.get("current_set")
        else:
            set_pv = item.get("K1_set") or item.get("K1")
            readback_pv = item.get("K1_readback") or item.get("K1")
        if not set_pv or not readback_pv:
            raise ValueError(f"Quadrupole {name} requires {control} setpoint and readback PVs for writes")
        return str(set_pv), str(readback_pv)

    def _quadrupole_control(self, item: Mapping, name: str) -> str:
        control = str(item.get("control", "k1")).lower()
        if control not in {"k1", "current"}:
            raise ValueError(f"Quadrupole {name} control must be 'k1' or 'current'")
        return control

    def _quadrupole_tolerance(self, name: str) -> float:
        item = self._nested_mapping(self._mapping("quadrupoles"), name, "Quadrupole")
        configured = item.get("readback_tolerance", self._quadrupole_tolerance_override)
        if configured is not None:
            return float(configured)
        return 0.01 if self._quadrupole_control(item, name) == "current" else 1.0e-5

    def _write_and_verify(
        self,
        set_pv: str,
        target: float,
        readback_pv: str,
        tolerance: float,
        label: str,
    ) -> None:
        if not np.isfinite(target):
            raise ValueError(f"{label} target must be finite")
        self._caput(set_pv, target)
        deadline = time.monotonic() + max(0.0, self._readback_timeout)
        while True:
            actual = self._caget_float(readback_pv)
            if np.isfinite(actual) and abs(actual - target) <= tolerance:
                return
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"{label} readback mismatch: target={target:g}, readback={actual:g}, tolerance={tolerance:g}"
                )
            time.sleep(max(0.001, self._poll_interval))

    def _caput(self, pv: str, value: float) -> None:
        if self._epics is None:
            raise RuntimeError(f"pyepics is not available: {self._import_error}")
        try:
            result = self._epics.caput(pv, value, wait=True, timeout=self._write_timeout)
        except TypeError:
            result = self._epics.caput(pv, value)
        except Exception as exc:
            raise RuntimeError(f"caput failed for {pv}: {exc}") from exc
        try:
            succeeded = result is not None and float(result) > 0
        except (TypeError, ValueError):
            succeeded = bool(result)
        if not succeeded:
            raise RuntimeError(f"caput failed for {pv}: result={result!r}")

    def _validate_write_configuration(self) -> None:
        if self._write_timeout <= 0 or self._readback_timeout < 0 or self._poll_interval <= 0:
            raise ValueError("EPICS write/readback timeouts must be positive")
        if self._energy_tolerance < 0:
            raise ValueError("EPICS readback tolerances must be non-negative")
        if self._quadrupole_tolerance_override is not None and self._quadrupole_tolerance_override < 0:
            raise ValueError("EPICS readback tolerances must be non-negative")
        self._energy_actuator_per_delta()
        energy = self._mapping("energy_knob")
        if not (energy.get("set") or energy.get("phase_set")):
            raise ValueError("write_enabled requires pv_map.energy_knob.set")
        for knob in self.config.knobs:
            for device in knob.devices:
                self._quadrupole_write_pvs(device)

    def _require_write_enabled(self) -> None:
        if not self._allow_write:
            raise PermissionError("EPICS writes are disabled unless backend.mode is write_enabled")

    def _mapping(self, key: str) -> dict:
        value = self._pv_map.get(key, {})
        if not isinstance(value, dict) or not value:
            raise ValueError(f"pv_map.{key} must be a non-empty mapping")
        return value

    def _nested_mapping(self, parent: Mapping, key: str, label: str) -> dict:
        value = parent.get(key)
        if not isinstance(value, dict):
            raise ValueError(f"{label} {key} is not configured")
        return value

    def _required_pv(self, item: Mapping, key: str, label: str) -> str:
        value = item.get(key)
        if not value:
            raise ValueError(f"{label} requires PV field {key}")
        return str(value)

    def _caget_float(self, pv: str) -> float:
        if self._epics is None:
            raise RuntimeError(f"pyepics is not available: {self._import_error}")
        try:
            value = self._epics.caget(
                pv,
                timeout=self._ca_timeout,
                connection_timeout=self._ca_timeout,
            )
        except TypeError:
            value = self._epics.caget(pv)
        except Exception:
            return float("nan")
        if value is None:
            return float("nan")
        try:
            return float(value)
        except (TypeError, ValueError):
            return float("nan")

    def _optional_caget_float(self, pv: object) -> float | None:
        if not pv:
            return None
        value = self._caget_float(str(pv))
        return value if np.isfinite(value) else None
