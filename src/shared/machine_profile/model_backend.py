from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

import numpy as np
import sdds

from half_linac.src.shared.elegant_backend import ElegantParser
from half_linac.src.shared.elegant_runtime import run_elegant_input

from .models import AppContext, MachineProfileError, ModelBackendConfig


LatticeOverrides = Mapping[str, Mapping[str, float | int | str]]


@dataclass(frozen=True)
class EnergyOpticsResult:
    beta_x_m: float
    alpha_x: float
    dispersion_x_m: float


@dataclass(frozen=True)
class TwissProfileResult:
    matrix: np.ndarray
    rows: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class EnergyModelPaths:
    working_dir: Path
    ini_ele: Path
    json: Path
    lte: Path
    ele: Path
    mat: Path
    twi: Path
    log: str

    @property
    def generated_outputs(self) -> tuple[Path, ...]:
        return (self.json, self.lte, self.ele, self.mat, self.twi)


def prepare_elegant_model_workdir(
    working_dir: str | Path,
    *,
    output_paths: Iterable[str | Path] = (),
) -> Path:
    workdir = Path(working_dir)
    workdir.mkdir(parents=True, exist_ok=True)
    for output_path in output_paths:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    return workdir


class BeamModelBackend(Protocol):
    def get_map(
        self,
        elem1: str,
        elem2: str,
        k1: float | None = None,
        element_overrides: Mapping[str, float] | None = None,
        lattice_overrides: LatticeOverrides | None = None,
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
        lattice_overrides: LatticeOverrides | None = None,
        seq: str = "exit2exit",
    ) -> float: ...

    def get_twiss1(
        self,
        quad1: str,
        quad2: str,
        twiss0: Mapping[str, float],
        plane: str = "xplane",
        inverse: bool = False,
        lattice_overrides: LatticeOverrides | None = None,
    ) -> Mapping[str, float]: ...

    def get_twiss_profile(
        self,
        elem1: str,
        elem2: str,
        twiss0: Mapping[str, float],
        plane: str = "xplane",
        inverse: bool = False,
        lattice_overrides: LatticeOverrides | None = None,
    ) -> TwissProfileResult: ...

    def get_line_elements(
        self,
        elem1: str,
        elem2: str,
        *,
        include_endpoints: bool = True,
    ) -> tuple[Mapping[str, str], ...]: ...

    def get_lattice_element(self, element_id: str) -> Mapping[str, str]: ...

    def get_optics_profile(
        self,
        elem1: str,
        elem2: str,
        *,
        lattice_overrides: LatticeOverrides | None = None,
        seq: str = "exit2exit",
    ) -> tuple[Mapping[str, Any], ...]: ...


class EnergyModelBackend(Protocol):
    def validate_energy_capability(self) -> None: ...

    def get_energy_dispersion(
        self,
        line_name: str,
        *,
        lattice_overrides: LatticeOverrides | None = None,
    ) -> float: ...

    def get_energy_optics(
        self,
        line_name: str,
        start_element: str,
        target_element: str,
        *,
        beta_x_m: float,
        alpha_x: float,
        lattice_overrides: LatticeOverrides | None = None,
    ) -> EnergyOpticsResult: ...


class ElegantModelBackend:
    def __init__(
        self,
        model_config: ModelBackendConfig,
        energy_mev: float | None = None,
        line_name: str | None = None,
    ):
        if model_config.engine != "elegant":
            raise MachineProfileError(
                f"ElegantModelBackend requires engine='elegant', got {model_config.engine!r}."
            )

        config = dict(model_config.config)
        self.config = config
        self.energy_mev = energy_mev
        self.source_json = Path(_require_config(config, "source_json"))
        self.source_lattice = Path(_require_config(config, "source_lattice"))
        self.asset_dir = Path(
            str(config.get("asset_dir") or config.get("working_dir") or self.source_lattice.parent)
        )
        self.optics_ini_ele = Path(_require_config_alias(config, "optics_ini_ele", "emit_ini_ele"))
        self.optics_lte = Path(_require_config_alias(config, "optics_lte", "emit_lte"))
        self.optics_ele = Path(_require_config_alias(config, "optics_ele", "emit_ele"))
        self.optics_json = Path(_require_config_alias(config, "optics_json", "emit_json"))
        self.optics_mat = Path(_require_config_alias(config, "optics_mat", "emit_mat"))
        self.optics_log = _require_config_alias(config, "optics_log", "emit_log")
        self.line_name = line_name or _require_config(config, "line_name")
        working_dir = (
            config.get("optics_working_dir")
            or config.get("emit_working_dir")
            or config.get("working_dir")
        )
        self.working_dir = Path(str(working_dir)) if working_dir is not None else self.optics_ele.parent

    def get_lattice_element(self, element_id: str) -> Mapping[str, str]:
        parser = self._new_parser()
        runtime_state = parser.build_runtime_state()
        try:
            return dict(runtime_state["lattice"][element_id])
        except KeyError as exc:
            raise MachineProfileError(
                f"Model backend lattice does not define element {element_id!r}."
            ) from exc

    def get_line_elements(
        self,
        elem1: str,
        elem2: str,
        *,
        include_endpoints: bool = True,
    ) -> tuple[Mapping[str, str], ...]:
        parser = self._new_parser()
        runtime_state = parser.build_runtime_state()
        usedline = runtime_state["usedline"]
        lattice = runtime_state["lattice"]
        id1, id2 = self._usedline_index_pair_from_usedline(usedline, elem1, elem2)
        if id2 < id1:
            id1, id2 = id2, id1
        if include_endpoints:
            element_ids = usedline[id1 : id2 + 1]
        else:
            element_ids = usedline[id1 + 1 : id2]
        return tuple(dict(lattice[element_id]) for element_id in element_ids)

    def get_twiss1(
        self,
        quad1: str,
        quad2: str,
        twiss0: Mapping[str, float],
        plane: str = "xplane",
        inverse: bool = False,
        lattice_overrides: LatticeOverrides | None = None,
    ) -> Mapping[str, float]:
        id1, id2 = self._usedline_index_pair(quad1, quad2)
        if inverse:
            if id1 < id2:
                raise MachineProfileError(
                    "Backward Twiss transport requires From to be downstream of To. "
                    "Choose Forward or swap From/To."
                )
            mat = np.linalg.inv(
                self.get_map(
                    quad2,
                    quad1,
                    lattice_overrides=lattice_overrides,
                    seq="ent2exit",
                )
            )
        else:
            if id2 < id1:
                raise MachineProfileError(
                    "Forward Twiss transport requires To to be downstream of From. "
                    "Choose Backward or swap From/To."
                )
            mat = self.get_map(
                quad1,
                quad2,
                lattice_overrides=lattice_overrides,
                seq="ent2exit",
            )

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

        return _transport_twiss(
            np.array([[m11, m12], [m21, m22]]),
            twiss0,
        )

    def get_twiss_profile(
        self,
        elem1: str,
        elem2: str,
        twiss0: Mapping[str, float],
        plane: str = "xplane",
        inverse: bool = False,
        lattice_overrides: LatticeOverrides | None = None,
    ) -> TwissProfileResult:
        twiss0 = _normalize_initial_twiss(twiss0)
        id1, id2 = self._usedline_index_pair(elem1, elem2)
        if inverse:
            if id1 < id2:
                raise MachineProfileError(
                    "Backward Twiss transport requires From to be downstream of To. "
                    "Choose Forward or swap From/To."
                )
            forward_matrix, _ = self._run_optics_profile(
                elem2,
                elem1,
                lattice_overrides=lattice_overrides,
                seq="ent2exit",
            )
            matrix = np.linalg.inv(forward_matrix)
            transported = _transport_twiss(_plane_matrix(matrix, plane), twiss0)
            upstream_twiss = {
                "beta0": transported["beta"],
                "alpha0": transported["alpha"],
                "gamma0": transported["gamma"],
            }
            _, forward_rows = self._run_optics_profile(
                elem2,
                elem1,
                lattice_overrides=lattice_overrides,
                seq="ent2exit",
                initial_twiss=upstream_twiss,
                plane=plane,
            )
            return TwissProfileResult(
                matrix=matrix,
                rows=_select_twiss_profile_rows(
                    reversed(forward_rows),
                    plane=plane,
                    reverse_distance=True,
                    initial_element=elem1,
                    final_element=elem2,
                ),
            )

        if id2 < id1:
            raise MachineProfileError(
                "Forward Twiss transport requires To to be downstream of From. "
                "Choose Backward or swap From/To."
            )
        matrix, rows = self._run_optics_profile(
            elem1,
            elem2,
            lattice_overrides=lattice_overrides,
            seq="ent2exit",
            initial_twiss=twiss0,
            plane=plane,
        )
        return TwissProfileResult(
            matrix=matrix,
            rows=_select_twiss_profile_rows(
                rows,
                plane=plane,
                initial_element=elem1,
                final_element=elem2,
            ),
        )

    def get_map(
        self,
        elem1: str,
        elem2: str,
        k1: float | None = None,
        element_overrides: Mapping[str, float] | None = None,
        lattice_overrides: LatticeOverrides | None = None,
        seq: str = "exit2exit",
        initial_twiss: Mapping[str, float] | None = None,
        twiss_plane: str = "xplane",
    ) -> np.ndarray:
        prepare_elegant_model_workdir(
            self.working_dir,
            output_paths=(self.optics_json, self.optics_lte, self.optics_ele, self.optics_mat),
        )
        parser = self._new_parser()
        parser.dump_runtime_state()
        with self.optics_json.open("r", encoding="utf-8") as handle:
            lte = json.load(handle)

        control = lte["control"]
        lattice = lte["lattice"]
        usedline = lte["usedline"]

        try:
            id1 = usedline.index(elem1)
            id2 = usedline.index(elem2)
        except ValueError as exc:
            missing = elem1 if elem1 not in usedline else elem2
            raise MachineProfileError(
                f"Model backend line {self.line_name!r} does not contain element {missing!r}."
            ) from exc
        if id2 < id1:
            raise MachineProfileError(
                f"Model backend cannot build a forward map from {elem1!r} to {elem2!r}: "
                f"{elem2!r} is upstream of {elem1!r}."
            )

        if seq == "exit2exit":
            scanline = usedline[id1 + 1 : id2 + 1]
        elif seq == "ent2exit":
            scanline = usedline[id1 : id2 + 1]
        else:
            raise ValueError(f"Unsupported transfer sequence: {seq}")
        if not scanline:
            raise MachineProfileError(
                f"Model backend generated an empty map line from {elem1!r} to {elem2!r}."
            )

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

        normalized_overrides = _normalize_lattice_overrides(
            lattice_overrides,
            element_overrides=element_overrides,
            k1_element=elem1 if k1 is not None else None,
            k1=k1,
        )
        _apply_lattice_overrides(lattice, normalized_overrides)

        control["run_setup"]["lattice"] = self.optics_lte.name
        if initial_twiss is not None:
            _apply_initial_twiss(control, initial_twiss, twiss_plane)
        lte["control"] = control
        lte["lattice"] = lattice
        lte["usedline"] = scanline

        with self.optics_json.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(lte, indent=4))

        parser.json_to_lte_ele(self.optics_lte, self.optics_ele)
        run_elegant_input(
            self.optics_ele.name,
            self.optics_log,
            workdir=self.working_dir,
        )

        return _load_matrix(self.optics_mat)

    def get_matrix_element(
        self,
        elem1: str,
        elem2: str,
        row: int,
        col: int,
        *,
        k1: float | None = None,
        element_overrides: Mapping[str, float] | None = None,
        lattice_overrides: LatticeOverrides | None = None,
        seq: str = "exit2exit",
    ) -> float:
        matrix = self.get_map(
            elem1,
            elem2,
            k1=k1,
            element_overrides=element_overrides,
            lattice_overrides=lattice_overrides,
            seq=seq,
        )
        return float(matrix[row, col])

    def get_optics_profile(
        self,
        elem1: str,
        elem2: str,
        *,
        lattice_overrides: LatticeOverrides | None = None,
        seq: str = "exit2exit",
        initial_twiss: Mapping[str, float] | None = None,
        plane: str = "xplane",
    ) -> tuple[Mapping[str, Any], ...]:
        """Return one Elegant Twiss/dispersion row per element in a model segment."""

        _, rows = self._run_optics_profile(
            elem1,
            elem2,
            lattice_overrides=lattice_overrides,
            seq=seq,
            initial_twiss=initial_twiss,
            plane=plane,
        )
        return rows

    def _run_optics_profile(
        self,
        elem1: str,
        elem2: str,
        *,
        lattice_overrides: LatticeOverrides | None = None,
        seq: str = "exit2exit",
        initial_twiss: Mapping[str, float] | None = None,
        plane: str = "xplane",
    ) -> tuple[np.ndarray, tuple[Mapping[str, Any], ...]]:
        matrix = self.get_map(
            elem1,
            elem2,
            lattice_overrides=lattice_overrides,
            seq=seq,
            initial_twiss=initial_twiss,
            twiss_plane=plane,
        )
        twiss_path = self.optics_ele.with_suffix(".twi")
        twiss = sdds.SDDS(0)
        twiss.load(str(twiss_path))
        with self.optics_json.open("r", encoding="utf-8") as handle:
            emitted_lattice = json.load(handle).get("lattice", {})
        columns = {
            name: twiss.columnData[index][0]
            for index, name in enumerate(twiss.columnName)
        }
        required = (
            "ElementName",
            "ElementOccurence",
            "ElementType",
            "s",
            "etax",
            "etaxp",
            "etay",
            "etayp",
            "betax",
            "alphax",
            "betay",
            "alphay",
        )
        missing = [name for name in required if name not in columns]
        if missing:
            raise MachineProfileError(
                f"Elegant Twiss output {twiss_path} is missing columns: {', '.join(missing)}"
            )
        row_count = len(columns["s"])
        rows = []
        for index in range(row_count):
            element_name = str(columns["ElementName"][index])
            element = emitted_lattice.get(element_name, {})
            rows.append(
                {
                    "element_name": element_name,
                    "element_occurrence": int(columns["ElementOccurence"][index]),
                    "element_type": str(columns["ElementType"][index]),
                    "element_length_m": _optional_float(element.get("L"), default=0.0),
                    "element_k1_m2": _optional_float(element.get("K1")),
                    "element_angle_rad": _optional_float(element.get("ANGLE")),
                    "element_tilt_rad": _optional_float(element.get("TILT"), default=0.0),
                    "s_m": float(columns["s"][index]),
                    "dx_m": float(columns["etax"][index]),
                    "dxp_rad": float(columns["etaxp"][index]),
                    "dy_m": float(columns["etay"][index]),
                    "dyp_rad": float(columns["etayp"][index]),
                    "beta_x_m": float(columns["betax"][index]),
                    "alpha_x": float(columns["alphax"][index]),
                    "beta_y_m": float(columns["betay"][index]),
                    "alpha_y": float(columns["alphay"][index]),
                }
            )
        return matrix, tuple(rows)

    def validate_energy_capability(self) -> None:
        self.energy_paths()

    def get_energy_dispersion(
        self,
        line_name: str,
        *,
        lattice_overrides: LatticeOverrides | None = None,
    ) -> float:
        paths, parser, state = self._prepare_energy_state(line_name, lattice_overrides)
        state["control"]["run_setup"]["lattice"] = paths.lte.name
        self._run_energy_state(paths, parser, state)
        matrix = _load_matrix(paths.mat)
        return float(matrix[0, 5])

    def get_energy_optics(
        self,
        line_name: str,
        start_element: str,
        target_element: str,
        *,
        beta_x_m: float,
        alpha_x: float,
        lattice_overrides: LatticeOverrides | None = None,
    ) -> EnergyOpticsResult:
        if beta_x_m <= 0:
            raise MachineProfileError("Energy optics beta_x_m must be positive.")
        paths, parser, state = self._prepare_energy_state(line_name, lattice_overrides)
        usedline = state["usedline"]
        try:
            start_index = usedline.index(start_element)
            target_index = usedline.index(target_element)
        except ValueError as exc:
            missing = start_element if start_element not in usedline else target_element
            raise MachineProfileError(
                f"Energy model line {line_name!r} does not contain element {missing!r}."
            ) from exc
        if target_index < start_index:
            raise MachineProfileError(
                f"Twiss target {target_element!r} is upstream of start element {start_element!r}."
            )

        control = state["control"]
        control["run_setup"]["lattice"] = paths.lte.name
        control["twiss_output"]["beta_x"] = str(beta_x_m)
        control["twiss_output"]["alpha_x"] = str(alpha_x)
        state["usedline"] = usedline[start_index : target_index + 1]
        self._run_energy_state(paths, parser, state)

        twiss = sdds.SDDS(0)
        twiss.load(str(paths.twi))
        columns = {
            name.lower(): twiss.columnData[index][0]
            for index, name in enumerate(twiss.columnName)
        }
        missing_columns = [name for name in ("betax", "alphax", "etax") if name not in columns]
        if missing_columns:
            raise MachineProfileError(
                f"Elegant Twiss output {paths.twi} is missing columns: "
                f"{', '.join(missing_columns)}"
            )
        return EnergyOpticsResult(
            beta_x_m=float(columns["betax"][-1]),
            alpha_x=float(columns["alphax"][-1]),
            dispersion_x_m=float(columns["etax"][-1]),
        )

    def energy_paths(self) -> EnergyModelPaths:
        config = self.config
        working_dir = _require_config_alias(
            config,
            "energy_working_dir",
            "working_dir",
        )
        return EnergyModelPaths(
            working_dir=Path(working_dir),
            ini_ele=Path(_require_config_alias(config, "energy_ini_ele", "energy_ini_ele_file")),
            json=Path(_require_config_alias(config, "energy_json", "energy_json_path")),
            lte=Path(_require_config_alias(config, "energy_lte", "energy_lte_file")),
            ele=Path(_require_config_alias(config, "energy_ele", "energy_ele_file")),
            mat=Path(_require_config_alias(config, "energy_mat", "energy_mat_file")),
            twi=Path(_require_config_alias(config, "energy_twi", "energy_twi_file")),
            log=_require_config(config, "energy_log"),
        )

    def _prepare_energy_state(
        self,
        line_name: str,
        lattice_overrides: LatticeOverrides | None,
    ) -> tuple[EnergyModelPaths, ElegantParser, dict[str, Any]]:
        paths = self.energy_paths()
        prepare_elegant_model_workdir(
            paths.working_dir,
            output_paths=paths.generated_outputs,
        )
        parser = ElegantParser(
            self.source_lattice,
            paths.ini_ele,
            line_name,
            runtime_json_path=paths.json,
            elegant_dir=paths.working_dir,
        )
        state = parser.build_runtime_state()
        _apply_lattice_overrides(state["lattice"], lattice_overrides or {})
        return paths, parser, state

    @staticmethod
    def _run_energy_state(
        paths: EnergyModelPaths,
        parser: ElegantParser,
        state: Mapping[str, Any],
    ) -> None:
        with paths.json.open("w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=4)
        parser.json_to_lte_ele(paths.lte, paths.ele, paths.json)
        run_elegant_input(paths.ele.name, paths.log, workdir=paths.working_dir)

    def _new_parser(self) -> ElegantParser:
        return ElegantParser(
            self.source_lattice,
            self.optics_ini_ele,
            self.line_name,
            runtime_json_path=self.optics_json,
            elegant_dir=self.working_dir,
        )

    def _usedline_index_pair(self, elem1: str, elem2: str) -> tuple[int, int]:
        parser = self._new_parser()
        runtime_state = parser.build_runtime_state()
        return self._usedline_index_pair_from_usedline(runtime_state["usedline"], elem1, elem2)

    def _usedline_index_pair_from_usedline(
        self,
        usedline: list[str],
        elem1: str,
        elem2: str,
    ) -> tuple[int, int]:
        try:
            return usedline.index(elem1), usedline.index(elem2)
        except ValueError as exc:
            missing = elem1 if elem1 not in usedline else elem2
            raise MachineProfileError(
                f"Model backend line {self.line_name!r} does not contain element {missing!r}."
            ) from exc


def build_model_backend(
    app_context: AppContext,
    *,
    energy_mev: float | None = None,
    line_name: str | None = None,
) -> BeamModelBackend:
    if app_context.model_backend is None:
        raise MachineProfileError(
            f"AppContext for {app_context.app_name!r} does not define a model backend."
        )

    if app_context.model_backend.engine == "elegant":
        return ElegantModelBackend(
            app_context.model_backend,
            energy_mev=energy_mev,
            line_name=line_name,
        )

    raise MachineProfileError(
        f"Unsupported model backend engine: {app_context.model_backend.engine!r}."
    )


def _apply_initial_twiss(
    control: dict[str, Any],
    twiss0: Mapping[str, float],
    plane: str,
) -> None:
    twiss_output = control.get("twiss_output")
    if not isinstance(twiss_output, dict):
        raise MachineProfileError("Elegant model input does not define &twiss_output.")
    beta_key, alpha_key = (
        ("beta_x", "alpha_x") if plane == "xplane" else ("beta_y", "alpha_y")
    )
    twiss_output[beta_key] = str(float(twiss0["beta0"]))
    twiss_output[alpha_key] = str(float(twiss0["alpha0"]))


def _normalize_initial_twiss(twiss0: Mapping[str, float]) -> dict[str, float]:
    try:
        beta = float(twiss0["beta0"])
        alpha = float(twiss0["alpha0"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MachineProfileError("Initial Twiss requires numeric beta0 and alpha0 values.") from exc
    if not np.isfinite(beta) or beta <= 0 or not np.isfinite(alpha):
        raise MachineProfileError("Initial Twiss beta must be positive and beta/alpha must be finite.")
    return {
        "beta0": beta,
        "alpha0": alpha,
        "gamma0": float((1.0 + alpha**2) / beta),
    }


def _plane_matrix(matrix: np.ndarray, plane: str) -> np.ndarray:
    if plane == "xplane":
        return np.asarray(matrix[np.ix_((0, 1), (0, 1))], dtype=float)
    if plane == "yplane":
        return np.asarray(matrix[np.ix_((2, 3), (2, 3))], dtype=float)
    raise MachineProfileError(f"Unsupported Twiss plane: {plane!r}.")


def _transport_twiss(
    matrix: np.ndarray,
    twiss0: Mapping[str, float],
) -> dict[str, float]:
    m11, m12 = matrix[0]
    m21, m22 = matrix[1]
    beta0 = float(twiss0["beta0"])
    alpha0 = float(twiss0["alpha0"])
    gamma0 = float(twiss0["gamma0"])
    return {
        "beta": float(m11**2 * beta0 - 2 * m11 * m12 * alpha0 + m12**2 * gamma0),
        "alpha": float(
            -m11 * m21 * beta0
            + (m11 * m22 + m12 * m21) * alpha0
            - m12 * m22 * gamma0
        ),
        "gamma": float(
            m21**2 * beta0 - 2 * m21 * m22 * alpha0 + m22**2 * gamma0
        ),
    }


def _select_twiss_profile_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    plane: str,
    initial_element: str,
    final_element: str,
    reverse_distance: bool = False,
) -> tuple[Mapping[str, Any], ...]:
    source_rows = tuple(rows)
    if not source_rows:
        raise MachineProfileError("Elegant returned an empty Twiss profile.")
    beta_key, alpha_key = (
        ("beta_x_m", "alpha_x") if plane == "xplane" else ("beta_y_m", "alpha_y")
    )
    origin_s = float(source_rows[0]["s_m"])
    selected = []
    for index, row in enumerate(source_rows):
        beta = float(row[beta_key])
        alpha = float(row[alpha_key])
        if beta <= 0:
            raise MachineProfileError(
                f"Elegant returned non-positive beta at {row['element_name']!r}: {beta}."
            )
        element_name = str(row["element_name"])
        if element_name == "_BEG_":
            element_name = initial_element if index == 0 else final_element
        s_m = float(row["s_m"])
        distance_m = origin_s - s_m if reverse_distance else s_m - origin_s
        selected.append(
            {
                "element_name": element_name,
                "element_type": str(row["element_type"]),
                "element_length_m": float(row.get("element_length_m", 0.0)),
                "element_k1_m2": float(row.get("element_k1_m2", float("nan"))),
                "element_angle_rad": float(
                    row.get("element_angle_rad", float("nan"))
                ),
                "element_tilt_rad": float(row.get("element_tilt_rad", 0.0)),
                "distance_m": float(max(0.0, distance_m)),
                "beta": beta,
                "alpha": alpha,
                "gamma": float((1.0 + alpha**2) / beta),
            }
        )
    return tuple(selected)


def _require_config(config: Mapping[str, object], key: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise MachineProfileError(f"Model backend config is missing {key!r}.")
    return value.strip()


def _require_config_alias(
    config: Mapping[str, object],
    key: str,
    legacy_key: str,
) -> str:
    value = config.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return _require_config(config, legacy_key)


def _load_matrix(path: Path | str) -> np.ndarray:
    matrix_file = sdds.SDDS(0)
    matrix_file.load(str(path))
    values = [matrix_file.columnData[index][0][0] for index in range(12, 48)]
    return np.asarray(values, dtype=float).reshape(6, 6)


def _optional_float(value: object, *, default: float = float("nan")) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_lattice_overrides(
    lattice_overrides: LatticeOverrides | None,
    *,
    element_overrides: Mapping[str, float] | None,
    k1_element: str | None,
    k1: float | None,
) -> dict[str, dict[str, float | int | str]]:
    normalized: dict[str, dict[str, float | int | str]] = {
        str(element_id): {str(field_name): value for field_name, value in fields.items()}
        for element_id, fields in (lattice_overrides or {}).items()
    }

    for element_id, override in (element_overrides or {}).items():
        normalized.setdefault(str(element_id), {})["K1"] = override

    if k1_element is not None and k1 is not None:
        normalized.setdefault(k1_element, {})["K1"] = k1

    return normalized


def _apply_lattice_overrides(
    lattice: dict[str, dict[str, str]],
    overrides: Mapping[str, Mapping[str, float | int | str]],
) -> None:
    for element_id, field_overrides in overrides.items():
        try:
            element = lattice[element_id]
        except KeyError as exc:
            raise MachineProfileError(
                f"Model backend override references unknown element {element_id!r}."
            ) from exc

        for field_name, value in field_overrides.items():
            if field_name not in element:
                raise MachineProfileError(
                    f"Element {element_id!r} does not support {field_name!r} override "
                    "in the model backend."
                )
            element[field_name] = str(float(value))
