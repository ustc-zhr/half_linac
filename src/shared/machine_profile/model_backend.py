from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, Protocol

import numpy as np
import sdds

from half_linac.src.shared.elegant_backend import ElegantParser
from half_linac.src.shared.elegant_runtime import run_elegant_input

from .models import AppContext, MachineProfileError, ModelBackendConfig


class BeamModelBackend(Protocol):
    def get_map(
        self,
        elem1: str,
        elem2: str,
        k1: float | None = None,
        element_overrides: Mapping[str, float] | None = None,
        seq: str = "exit2exit",
    ) -> np.ndarray: ...

    def get_matrix_element(
        self,
        elem1: str,
        elem2: str,
        row: int,
        col: int,
        *,
        k1: float | None = None,
        element_overrides: Mapping[str, float] | None = None,
        seq: str = "exit2exit",
    ) -> float: ...

    def get_twiss1(
        self,
        quad1: str,
        quad2: str,
        twiss0: Mapping[str, float],
        plane: str = "xplane",
        inverse: bool = False,
    ) -> Mapping[str, float]: ...

    def get_lattice_element(self, element_id: str) -> Mapping[str, str]: ...


class ElegantModelBackend:
    def __init__(self, model_config: ModelBackendConfig, energy_mev: float | None = None):
        if model_config.engine != "elegant":
            raise MachineProfileError(
                f"ElegantModelBackend requires engine='elegant', got {model_config.engine!r}."
            )

        config = dict(model_config.config)
        self.energy_mev = energy_mev
        self.source_json = Path(_require_config(config, "source_json"))
        self.source_lattice = Path(_require_config(config, "source_lattice"))
        self.emit_ini_ele = Path(_require_config(config, "emit_ini_ele"))
        self.emit_lte = Path(_require_config(config, "emit_lte"))
        self.emit_ele = Path(_require_config(config, "emit_ele"))
        self.emit_json = Path(_require_config(config, "emit_json"))
        self.emit_mat = Path(_require_config(config, "emit_mat"))
        self.emit_log = _require_config(config, "emit_log")
        self.line_name = _require_config(config, "line_name")
        working_dir = config.get("working_dir")
        self.working_dir = Path(str(working_dir)) if working_dir is not None else self.emit_ele.parent

    def get_lattice_element(self, element_id: str) -> Mapping[str, str]:
        parser = self._new_parser()
        runtime_state = parser.build_runtime_state()
        try:
            return dict(runtime_state["lattice"][element_id])
        except KeyError as exc:
            raise MachineProfileError(
                f"Model backend lattice does not define element {element_id!r}."
            ) from exc

    def get_twiss1(
        self,
        quad1: str,
        quad2: str,
        twiss0: Mapping[str, float],
        plane: str = "xplane",
        inverse: bool = False,
    ) -> Mapping[str, float]:
        mat = self.get_map(quad1, quad2, seq="ent2exit")
        if inverse:
            mat = np.linalg.inv(mat)

        if plane == "xplane":
            m11 = mat[0, 0]
            m12 = mat[0, 1]
            m21 = mat[1, 0]
            m22 = mat[1, 1]
        else:
            m11 = mat[2, 2]
            m12 = mat[2, 3]
            m21 = mat[3, 2]
            m22 = mat[3, 3]

        beta0 = twiss0["beta0"]
        alpha0 = twiss0["alpha0"]
        gamma0 = twiss0["gamma0"]
        beta = m11**2 * beta0 - 2 * m11 * m12 * alpha0 + m12**2 * gamma0
        alpha = -m11 * m21 * beta0 + (2 * m12 * m21 + 1) * alpha0 - m12 * m22 * gamma0
        gamma = m21**2 * beta0 - 2 * m21 * m22 * alpha0 + m22**2 * gamma0
        return {
            "beta": beta,
            "alpha": alpha,
            "gamma": gamma,
        }

    def get_map(
        self,
        elem1: str,
        elem2: str,
        k1: float | None = None,
        element_overrides: Mapping[str, float] | None = None,
        seq: str = "exit2exit",
    ) -> np.ndarray:
        parser = self._new_parser()
        parser.dump_runtime_state()
        with self.emit_json.open("r", encoding="utf-8") as handle:
            lte = json.load(handle)

        control = lte["control"]
        lattice = lte["lattice"]
        usedline = lte["usedline"]

        overrides = dict(element_overrides or {})
        if k1 is not None:
            overrides[elem1] = k1
        for element_id, override in overrides.items():
            try:
                element = lattice[element_id]
            except KeyError as exc:
                raise MachineProfileError(
                    f"Model backend override references unknown element {element_id!r}."
                ) from exc
            if "K1" not in element:
                raise MachineProfileError(
                    f"Element {element_id!r} does not support K1 override in the model backend."
                )
            element["K1"] = str(float(override))

        if seq == "exit2exit":
            id1 = usedline.index(elem1)
            id2 = usedline.index(elem2)
            scanline = usedline[id1 + 1 : id2 + 1]
        elif seq == "ent2exit":
            id1 = usedline.index(elem1)
            id2 = usedline.index(elem2)
            scanline = usedline[id1 : id2 + 1]
        else:
            raise ValueError(f"Unsupported transfer sequence: {seq}")

        for elem in usedline:
            if "DX" in lattice[elem] or "DY" in lattice[elem]:
                lattice[elem]["DX"] = "0.0"
                lattice[elem]["DY"] = "0.0"
                lattice[elem]["ROTATE_X"] = "0.0"
                lattice[elem]["ROTATE_Y"] = "0.0"
                lattice[elem]["ROTATE_Z"] = "0.0"

            if "KICK" in lattice[elem]:
                lattice[elem]["KICK"] = "0.0"

            if (
                lattice[elem]["TYPE"] == "WATCH"
                and lattice[elem]["MODE"].lower() == "coord"
                and lattice[elem]["DISABLE"] == "0"
            ):
                lattice[elem]["DISABLE"] = "1"

        control["run_setup"]["lattice"] = self.emit_lte.name
        lte["control"] = control
        lte["lattice"] = lattice
        lte["usedline"] = scanline

        with self.emit_json.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(lte, indent=4))

        parser.json_to_lte_ele(self.emit_lte, self.emit_ele)
        run_elegant_input(
            self.emit_ele.name,
            self.emit_log,
            workdir=self.working_dir,
        )

        matrix_file = sdds.SDDS(0)
        matrix_file.load(str(self.emit_mat))
        list_r = [matrix_file.columnData[i][0][0] for i in range(12, 48)]
        return np.array(list_r).reshape(6, 6)

    def get_matrix_element(
        self,
        elem1: str,
        elem2: str,
        row: int,
        col: int,
        *,
        k1: float | None = None,
        element_overrides: Mapping[str, float] | None = None,
        seq: str = "exit2exit",
    ) -> float:
        matrix = self.get_map(
            elem1,
            elem2,
            k1=k1,
            element_overrides=element_overrides,
            seq=seq,
        )
        return float(matrix[row, col])

    def _new_parser(self) -> ElegantParser:
        return ElegantParser(
            self.source_lattice,
            self.emit_ini_ele,
            self.line_name,
            runtime_json_path=self.emit_json,
            elegant_dir=self.working_dir,
        )


def build_model_backend(
    app_context: AppContext,
    *,
    energy_mev: float | None = None,
) -> BeamModelBackend:
    if app_context.model_backend is None:
        raise MachineProfileError(
            f"AppContext for {app_context.app_name!r} does not define a model backend."
        )

    if app_context.model_backend.engine == "elegant":
        return ElegantModelBackend(app_context.model_backend, energy_mev=energy_mev)

    raise MachineProfileError(
        f"Unsupported model backend engine: {app_context.model_backend.engine!r}."
    )


def _require_config(config: Mapping[str, object], key: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise MachineProfileError(f"Model backend config is missing {key!r}.")
    return value.strip()
