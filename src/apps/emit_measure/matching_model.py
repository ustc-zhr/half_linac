"""Elegant adapter for matching; reads model files, never controls devices."""
from copy import deepcopy
import hashlib
import json
import math

import numpy as np

from .matching import Point, Twiss, propagate, positive

RF_TYPES = {"RFCA", "RFCW", "MODRF"}
MASS_MEV = 0.51099895


def momentum(kinetic):
    kinetic = positive(kinetic, "kinetic energy along path")
    return math.sqrt(kinetic * (kinetic + 2 * MASS_MEV))


class ElegantMatchingModel:
    def __init__(self, backend):
        self.backend = backend
        self.state = backend._new_parser().build_runtime_state()
        self.names = list(self.state["usedline"])
        self.elements = self.state["lattice"]
        self.files = (backend.source_lattice, backend.optics_ini_ele)
        self.snapshot = {
            "fingerprint": self._fingerprint(), "line": backend.line_name,
            "files": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.files},
            "lattice": deepcopy(self.elements), "control": deepcopy(self.state["control"]),
            "rf": {n: deepcopy(self.elements[n]) for n in self.names
                   if self.elements[n]["TYPE"].upper() in RF_TYPES},
        }
        self.fingerprint = self.snapshot["fingerprint"]

    def _fingerprint(self):
        # Include evaluated lattice to capture included model files, not just the root.
        state = self.backend._new_parser().build_runtime_state()
        content = json.dumps(state, sort_keys=True).encode()
        for path in self.files:
            content += path.read_bytes()
        return hashlib.sha256(content).hexdigest()

    def assert_unchanged(self, fingerprint):
        if self._fingerprint() != fingerprint:
            raise ValueError("Model changed; reload baseline and recalculate the suggestion")

    def position(self, point):
        if self.names.count(point.element) != 1:
            raise ValueError("Select a uniquely named reference element: " + point.element)
        return self.names.index(point.element) + (point.edge == "exit")

    def required_quads(self, points):
        indices = [self.position(p) for p in points]
        return [n for n in self.names[min(indices):max(indices)]
                if self.elements[n]["TYPE"].upper() == "QUAD"]

    def energy_at(self, energy, source, target, overrides):
        source_index, target_index = self.position(source), self.position(target)
        return self._energy_indices(energy, source_index, target_index, overrides)

    def _energy_indices(self, energy, source_index, target_index, overrides):
        gain = 0.0
        sign = 1 if target_index >= source_index else -1
        for name in self.names[min(source_index, target_index):max(source_index, target_index)]:
            element = dict(self.elements[name])
            element.update(overrides.get(name, {}))
            if element["TYPE"].upper() in RF_TYPES:
                gain += float(element.get("VOLT", 0)) / 1e6 * math.sin(math.radians(float(element.get("PHASE", 0))))
        return positive(energy + sign * gain, "transported kinetic energy")

    def _map(self, source, target, energy, overrides):
        a, b = self.position(source), self.position(target)
        if a == b:
            return np.eye(6)
        if a > b:
            upstream_energy = self.energy_at(energy, source, target, overrides)
            return np.linalg.inv(self._map(target, source, upstream_energy, overrides))
        old = self.backend.energy_mev
        try:
            self.backend.energy_mev = energy
            return self.backend.get_map(
                source.element, target.element, lattice_overrides=overrides,
                seq=("ent" if source.edge == "entrance" else "exit") + "2" +
                    ("ent" if target.edge == "entrance" else "exit"), twiss_only=True)
        finally:
            self.backend.energy_mev = old

    def transport(self, measurement, target):
        matrix = self._map(measurement.point, target, measurement.energy_mev, measurement.overrides)
        values = {p: propagate(measurement.planes[p], matrix[i:i+2, i:i+2])
                  for p, i in (("x", 0), ("y", 2))}
        return values, self.energy_at(measurement.energy_mev, measurement.point, target, measurement.overrides)

    def profile(self, start, target, initial, energy, overrides, *, interiors=True):
        if start.edge != "entrance":
            raise ValueError("Matching profiles start at an element entrance")
        old = self.backend.energy_mev
        result = {}
        try:
            self.backend.energy_mev = energy
            for p in ("x", "y"):
                rows = self.backend.get_optics_profile(
                    start.element, target.element, lattice_overrides=overrides,
                    seq="ent2ent" if target.edge == "entrance" else "ent2exit",
                    initial_twiss=initial[p].initial(), plane=p + "plane", twiss_only=True)
                output = []
                for index, row in enumerate(rows):
                    point = start if index == 0 else Point(row["element_name"], "exit")
                    local_energy = self._energy_indices(energy, self.position(start),
                                                        self.position(start) + index, overrides)
                    emittance = initial[p].emittance * momentum(energy) / momentum(local_energy)
                    beta = float(row[f"beta_{p}_m"])
                    output.append({"element": point.element, "edge": point.edge,
                                   "s_m": float(row["s_m"] - rows[0]["s_m"]),
                                   "beta": beta, "alpha": float(row[f"alpha_{p}"]),
                                   "emittance": emittance, "energy_mev": local_energy,
                                   "sigma_m": math.sqrt(beta * emittance)})
                # Adjacent exit/entrance represent the same physical location.
                output[-1].update(element=target.element, edge=target.edge)
                result[p] = self._quad_extrema(output, p, overrides) if interiors else output
        finally:
            self.backend.energy_mev = old
        return result

    def design(self, start, target):
        control = self.state["control"]
        twiss = control["twiss_output"]
        setup = control["run_setup"]
        if "p_central_mev" in setup:
            pc = float(setup["p_central_mev"])
        else:
            pc = float(setup["p_central"]) * MASS_MEV
        energy = math.sqrt(pc**2 + MASS_MEV**2) - MASS_MEV
        initial = {p: Twiss(float(twiss[f"beta_{p}"]), float(twiss[f"alpha_{p}"]), 1e-6)
                   for p in ("x", "y")}
        first = Point(self.names[0])
        full = self.profile(first, target, initial, energy, {}, interiors=False)
        start_index = self.position(start)
        result = {}
        for p, rows in full.items():
            selected = deepcopy(rows[start_index:])
            offset = selected[0]["s_m"]
            for row in selected:
                row["s_m"] -= offset
            selected[0].update(element=start.element, edge=start.edge)
            result[p] = self._quad_extrema(selected, p, {})
        return result

    def _quad_extrema(self, rows, plane, overrides):
        """Include analytic beta extrema inside hard-edge quadrupoles.

        Drift beta is convex so its maximum is at an endpoint. RF and other
        elements retain Elegant boundary samples; that limitation is reported.
        """
        output = []
        for left, right in zip(rows, rows[1:]):
            output.append(left)
            # A target entrance is the exit of the preceding occurrence.
            name = right["element"]
            if right["edge"] == "entrance":
                index = self.position(Point(name))
                if index == 0:
                    continue
                name = self.names[index - 1]
            element = self.elements[name]
            if element["TYPE"].upper() != "QUAD":
                continue
            length = right["s_m"] - left["s_m"]
            k = float(overrides.get(name, {}).get("K1", element["K1"]))
            if plane == "y":
                k = -k
            t = Twiss(left["beta"], left["alpha"], left["emittance"])
            roots = []
            if abs(k) < 1e-15:
                roots = [t.alpha / t.gamma]
            elif k > 0:
                w = math.sqrt(k)
                phase = math.atan2(-t.alpha, (w * t.beta - t.gamma / w) / 2)
                first_root = math.ceil(-phase / math.pi)
                last_root = math.floor((2 * w * length - phase) / math.pi)
                if last_root - first_root > 10000:
                    raise ValueError("Quadrupole phase advance exceeds matching sampling budget")
                roots = [(phase + n * math.pi) / (2*w) for n in range(first_root, last_root + 1)]
            else:
                w = math.sqrt(-k)
                ratio = 2 * t.alpha / (w * t.beta + t.gamma / w)
                if abs(ratio) < 1:
                    roots = [math.atanh(ratio) / (2*w)]
            for z in sorted(v for v in roots if 1e-10 < v < length - 1e-10):
                if abs(k) < 1e-15:
                    matrix = np.array([[1., z], [0., 1.]])
                elif k > 0:
                    c, sn = math.cos(w*z), math.sin(w*z)
                    matrix = np.array([[c, sn/w], [-w*sn, c]])
                else:
                    c, sn = math.cosh(w*z), math.sinh(w*z)
                    matrix = np.array([[c, sn/w], [w*sn, c]])
                interior = propagate(t, matrix)
                row = dict(left)
                row.update(element=name, edge="interior", s_m=left["s_m"] + z,
                           beta=interior.beta, alpha=interior.alpha,
                           emittance=interior.emittance,
                           sigma_m=math.sqrt(interior.beta * interior.emittance))
                output.append(row)
        output.append(rows[-1])
        return output
