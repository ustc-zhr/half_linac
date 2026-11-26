"""Standalone current/K1 adapters; construction never reads or writes PVs."""
from __future__ import annotations

from dataclasses import asdict
import math
import time

import numpy as np
from scipy.spatial.distance import pdist

from .measurement_service import fingerprint
from .infobax_session import PointMeasurement
from .infobax_workflow import current_vector


class FakeInfoBAXMachine:
    machine_id, backend = "half", "fake"

    def __init__(self, config, *, initial_current=0.0, seed=0):
        self.config, self.current, self.k1 = config, float(initial_current), 0.0
        self.candidate_ids = config.candidate_ids
        self.candidates = ()
        self.check = lambda: None
        self.archive_dir = None
        self.moves = []
        self.seed = seed

    @property
    def fingerprint(self):
        return fingerprint({"adapter": "fake-current-v1", "config": self.config.fingerprint,
                            "seed": self.seed})

    def configure(self, candidates):
        self.candidates = tuple(candidates)

    def validate_pool(self, pool):
        if pool.candidate_ids != self.candidate_ids or tuple(pool.scan_values) != self.config.scan_values:
            raise ValueError("Machine/pool mismatch")

    def snapshot(self):
        return {"current_a": self.current, "scan_k1": self.k1}

    def verify_snapshot(self, expected):
        if self.snapshot() != dict(expected):
            raise RuntimeError("Device drift detected")

    def apply_current(self, candidate):
        self.check()
        self.current = self.candidates[candidate]
        self.moves.append(("current", self.current))

    def apply_query(self, candidate, scan_value):
        self.apply_current(candidate)
        self.k1 = float(scan_value)
        self.moves.append(("scan", self.k1))

    def measure_point(self, query_index, expected_noise_std_mm2):
        self.check()
        j = self.config.scan_values.index(self.k1)
        # Positive beam matrix at every workpoint; optimum near the lower quartile.
        optimum = self.config.current_low + 0.25*(self.config.current_high-self.config.current_low)
        scale = 1.0 + 0.5*((self.current-optimum)/(self.config.current_high-self.config.current_low))**2
        value = float(self.config.design[j] @ (np.array([0.08, -0.015, 0.06])*scale))
        return PointMeasurement(value, max(value*0.005, 1e-5),
                                {"backend": "fake", "frame_count": 5, "synthetic": True})

    def restore(self, snapshot):
        self.current, self.k1 = snapshot["current_a"], snapshot["scan_k1"]
        self.moves.append(("restore", self.current, self.k1))


def camera_contract(context, screen):
    from half_linac.src.shared.machine_profile import resolve_channel
    declaration = context.profile.workflows["emit_measure"].get("infobax_camera", {}).get(screen, {})
    if declaration.get("frame_id_matches_image") is not True:
        raise ValueError(f"{screen}: profile must confirm frame_id_matches_image after a read-only site check")
    return {name: resolve_channel(context, screen, name) for name in ("image", "frame_id", "exposure_time")}


class HalfRealInfoBAXMachine:
    machine_id, backend = "half", "real"

    def __init__(self, context, variable, config, camera, *, scan_quad, get=None, put=None,
                 settle_time=8.0, motion_timeout=10.0, tolerance=0.01, model_checks=None):
        from .optimization import EpicsVariableGroup, OptimizationConfig, OptimizationVariable
        from half_linac.src.shared.machine_profile import resolve_channel, resolve_write_target
        if context.profile.machine.id != "half" or context.control_backend.name != "real":
            raise ValueError("HALF real backend required")
        for value in (settle_time, motion_timeout, tolerance):
            if not math.isfinite(value) or value < 0:
                raise ValueError("Invalid motion setting")
        if motion_timeout <= 0 or tolerance <= 0:
            raise ValueError("Motion timeout and tolerance must be positive")
        if camera.settings.plane != config.plane:
            raise ValueError("Camera plane must match the workflow target")
        self.context, self.config, self.camera = context, config, camera
        self.candidate_ids = config.candidate_ids
        self.scan_quad = scan_quad
        self.settle_time, self.motion_timeout, self.tolerance = settle_time, motion_timeout, tolerance
        variables = (variable,) if isinstance(variable, OptimizationVariable) else tuple(variable)
        if len(variables) != len(current_vector(config.current_low)):
            raise ValueError("Selected solenoids do not match the workflow current vector")
        device_config = OptimizationConfig(variables, config.plane, None,
            settle_time=settle_time, motion_timeout=motion_timeout, readback_tolerance=tolerance)
        self.device = EpicsVariableGroup(context, device_config, get=get, put=put)
        self.device.validate(read_values=False)
        self.k1_target = resolve_write_target(context, scan_quad, quantity="K1", unit="1/m^2")
        self.quad_pvs = {name: resolve_channel(context, scan_quad, name)
                         for name in ("K1", "current_set", "current_readback")}
        self.check = lambda: None
        self.archive_dir = None
        self.candidates = ()
        self.quad = None
        self._pending_query = None
        self.model_checks = dict(model_checks or {})
        declared = [asdict(item) for item in variables]
        self._fingerprint = fingerprint({"adapter": "half-real-current-v1", "camera": camera.fingerprint,
            "variable": declared[0] if len(declared) == 1 else declared,
            "quad": self.quad_pvs, "config": config.fingerprint,
            "solenoid": [(target.pv_name, read) for target, read in self.device.channels],
            "motion": [settle_time, motion_timeout, tolerance], "model_checks": self.model_checks})

    @property
    def fingerprint(self):
        return self._fingerprint

    def configure(self, candidates):
        self.device.validate(read_values=False)
        self.candidates = tuple(candidates)
        if len(self.candidates) != self.config.current_candidate_count:
            raise ValueError("Current candidate count does not match the workflow")
        points = [self.device._validate_point(current_vector(value)) for value in self.candidates]
        if np.min(pdist(points, metric="chebyshev")) <= 2*self.tolerance:
            raise ValueError("Candidate spacing must exceed twice the current readback tolerance")
        if self.archive_dir is not None:
            from pathlib import Path
            self.camera.archive_dir = self.archive_dir
            if self.camera.background is not None:
                np.save(Path(self.archive_dir)/"background.npy", self.camera.background, allow_pickle=False)
        self.camera.check = self.check

    def validate_pool(self, pool):
        if pool.candidate_ids != self.candidate_ids or tuple(pool.scan_values) != self.config.scan_values:
            raise ValueError("Machine/pool mismatch")

    def _model_check(self):
        for pv, expected in self.model_checks.items():
            if not math.isclose(self.device.read(pv), expected, rel_tol=1e-8, abs_tol=1e-6):
                raise RuntimeError(f"Transport model setting changed: {pv}")

    def _make_quad(self, initial):
        # Rehydrate the established verified restore helper without taking a new
        # initial snapshot, including after a process restart or partial failure.
        from .optimization import VerifiedQuadRestore
        quad = object.__new__(VerifiedQuadRestore)
        quad.get, quad.put = self.device.get, self.device.put
        quad.timeout, quad.tolerance, quad.quad_name = self.motion_timeout, self.tolerance, self.scan_quad
        quad.k1_pv = self.quad_pvs["K1"]
        quad.current_pv, quad.set_pv = self.quad_pvs["current_readback"], self.quad_pvs["current_set"]
        quad.initial_k1, quad.initial_current = initial["scan_k1"], initial["quad_current_a"]
        quad.initial_readback = initial["quad_readback_a"]
        return quad

    def snapshot(self):
        self._model_check()
        currents = self.device.initial()
        if not self._currents_match(self.device.readback(), currents):
            raise RuntimeError("Solenoid has not reached its setpoint")
        snap = {"current_a": currents[0] if len(currents) == 1 else currents,
                "scan_k1": self.device.read(self.quad_pvs["K1"]),
                "quad_current_a": self.device.read(self.quad_pvs["current_set"]),
                "quad_readback_a": self.device.read(self.quad_pvs["current_readback"]),
                "exposure": self.camera.exposure()}
        if abs(snap["quad_current_a"]-snap["quad_readback_a"]) > self.tolerance:
            raise RuntimeError("Scan quadrupole has not reached its setpoint")
        from .profile_runtime import effective_k1_scan_limit
        low, high = min(self.config.scan_values), max(self.config.scan_values)
        limits = effective_k1_scan_limit(self.context, self.scan_quad, low, high,
                                         "absolute", "1/m^2", snap["scan_k1"])
        if not limits.contains(low) or not limits.contains(high):
            raise ValueError("Requested K1 range exceeds the machine limit")
        if self.quad is None:
            self.quad = self._make_quad(snap)
        if self.camera.frozen_exposure is None:
            self.camera.arm()
        if self.camera.frozen_exposure is not None and snap["exposure"] != self.camera.frozen_exposure:
            raise RuntimeError("Camera exposure changed during snapshot acquisition")
        return snap

    def _currents_match(self, actual, target):
        return len(actual) == len(target) and all(abs(a-b) <= self.tolerance for a, b in zip(actual, target))

    def verify_snapshot(self, expected):
        actual = self.snapshot()
        if not self._currents_match(current_vector(actual["current_a"]), current_vector(expected["current_a"])):
            raise RuntimeError("Device/settings drift: current_a")
        for key, value in expected.items():
            if key == "current_a":
                continue
            tolerance = self.tolerance if key.endswith("_a") else 1e-6
            if not math.isclose(actual[key], value, rel_tol=0, abs_tol=tolerance):
                raise RuntimeError(f"Device/settings drift: {key}")
        if actual["exposure"] != expected["exposure"]:
            raise RuntimeError("Camera exposure changed")

    def apply_current(self, candidate):
        self.check()
        self._model_check()
        target = current_vector(self.candidates[candidate])
        if self._currents_match(self.device.initial(), target) and self._currents_match(self.device.readback(), target):
            return
        self.device.move(target, self.check)

    def apply_query(self, candidate, scan_value):
        from half_linac.src.shared.machine_profile import require_workflow_write_allowed
        require_workflow_write_allowed(self.context, "emit_measure", "InfoBAX point")
        if scan_value not in self.config.scan_values:
            raise ValueError("K1 is outside the frozen scan list")
        self.apply_current(candidate)
        self.quad.move(scan_value, self.check)
        end = time.monotonic() + self.settle_time
        while time.monotonic() < end:
            self.check()
            time.sleep(0.05)
        # Confirm readbacks again after settling, before accepting any frame.
        self._pending_query = (candidate, float(scan_value))
        self._verify_query()

    def _verify_query(self):
        if self._pending_query is None:
            raise RuntimeError("No confirmed query is pending")
        candidate, k1 = self._pending_query
        actual = self.snapshot()
        if (not self._currents_match(current_vector(actual["current_a"]), current_vector(self.candidates[candidate]))
                or abs(actual["scan_k1"]-k1) > 1e-6):
            raise RuntimeError("Device drifted from the pending query")

    def measure_point(self, query_index, expected_noise_std_mm2):
        self._verify_query()
        self.camera.check = self.check
        result = self.camera.measure(query_index)
        self._verify_query()
        self._pending_query = None
        return result

    def restore(self, initial):
        from half_linac.src.shared.machine_profile import require_workflow_write_allowed
        errors = []
        initial_currents = current_vector(initial["current_a"])
        for operation in (lambda: self.device.restore(initial_currents),
                          lambda: self._restore_quad(initial)):
            try:
                require_workflow_write_allowed(self.context, "emit_measure", "InfoBAX restore")
                operation()
            except Exception as exc:
                errors.append(str(exc))
        if not errors:
            try:
                for read_currents in (self.device.initial, self.device.readback):
                    if not self._currents_match(read_currents(), initial_currents):
                        raise RuntimeError("Final solenoid restore verification failed")
                for pv, expected, tolerance in (
                    (self.quad_pvs["K1"], initial["scan_k1"], 1e-6),
                    (self.quad_pvs["current_set"], initial["quad_current_a"], self.tolerance),
                    (self.quad_pvs["current_readback"], initial["quad_current_a"], self.tolerance),
                ):
                    if abs(self.device.read(pv)-expected) > tolerance:
                        raise RuntimeError(f"Final restore verification failed: {pv}")
            except Exception as exc:
                errors.append(str(exc))
        if errors:
            raise RuntimeError("; ".join(errors))

    def _restore_quad(self, initial):
        self._make_quad(initial)(initial["scan_k1"])
