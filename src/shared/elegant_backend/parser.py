from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import sdds

from half_linac.src.shared.runtime_state import read_runtime_state, write_runtime_state
from half_linac.src.virtual_machine.lattice_parser import lattice_parser


def _new_legacy_sdds_dataset():
    dataset_factory = getattr(sdds, "SDDS", None)
    if dataset_factory is None:
        raise RuntimeError(
            "SDDS helper requires the legacy python SDDS binding with an SDDS class."
        )
    return dataset_factory(0)


def _load_bpm_centroids_from_sdds(bpmcen_path: Path) -> dict[str, dict[str, float]]:
    bpm_file = _new_legacy_sdds_dataset()
    bpm_file.load(str(bpmcen_path))

    columns = {}
    for index, column_name in enumerate(bpm_file.columnName):
        columns[column_name] = bpm_file.columnData[index][0]

    bpm = {}
    for index, element_name in enumerate(columns["ElementName"]):
        bpm[element_name.upper()] = {
            "Cx": columns["Cx"][index],
            "Cy": columns["Cy"][index],
        }
    return bpm


def _load_watch_image_from_sdds(
    watch_output_path: Path,
    *,
    pixel_shape: tuple[int, int],
    pixel_width_mm: float,
) -> np.ndarray:
    x_pixels, y_pixels = pixel_shape
    x1 = -0.5 * x_pixels * pixel_width_mm * 1e-3
    x2 = 0.5 * x_pixels * pixel_width_mm * 1e-3
    y1 = -0.5 * y_pixels * pixel_width_mm * 1e-3
    y2 = 0.5 * y_pixels * pixel_width_mm * 1e-3

    watch_file = _new_legacy_sdds_dataset()
    watch_file.load(str(watch_output_path))
    x_values = watch_file.columnData[0][0]
    y_values = watch_file.columnData[2][0]

    hist, _, _ = np.histogram2d(
        x_values,
        y_values,
        bins=[x_pixels, y_pixels],
        range=[[x1, x2], [y1, y2]],
    )
    return np.reshape(hist.transpose(), (np.size(hist),))


class EleParser(lattice_parser):
    """
    Parser for Elegant ``*.ele`` files.
    """

    def __init__(self, file_name: str | Path):
        super().__init__(str(file_name))
        self.fileName = str(file_name)
        self.control = self.get_control()

    def get_control(self) -> dict[str, dict[str, str]]:
        lines = [line.replace(" ", "") for line in self.get_brieflines()]
        control: dict[str, dict[str, str]] = {}
        section_name: str | None = None

        for line in lines:
            section_match = re.match(r"&\w+", line, re.I)
            section_end = re.match(r"&end", line, re.I)

            if section_match:
                section_name = section_match.group()[1:]
                control.setdefault(section_name, {})
                continue

            if section_end:
                continue

            if section_name is None:
                continue

            assignments = re.split(r";|,", line)
            assignments = [item for item in assignments if item]
            for assignment in assignments:
                name, value = assignment.split("=", 1)
                control[section_name][name] = value

        return control

    def back2ele(self, ele_file: str | Path, lattice_file: str | Path) -> None:
        ele_path = Path(ele_file)
        lattice_path = Path(lattice_file)

        with ele_path.open("w", encoding="utf-8") as handle:
            lines = []

            self.control["run_setup"]["lattice"] = lattice_path.name
            for section_name in (
                "run_setup",
                "run_control",
                "twiss_output",
                "matrix_output",
                "error_control",
                "error_element",
            ):
                if section_name in self.control:
                    lines.extend(self._section_lines(section_name))

            if "sdds_beam" in self.control:
                lines.extend(self._section_lines("sdds_beam"))

            if "bunched_beam" in self.control:
                lines.extend(self._section_lines("bunched_beam"))

            lines.append("&track &end")
            handle.write("".join(lines))

    def _section_lines(self, section_name: str) -> list[str]:
        lines = [f"&{section_name}\n"]
        for key, value in self.control[section_name].items():
            lines.append(f"    {key} = {value},\n")
        lines.append("&end\n\n")
        return lines


class ElegantParser:
    """
    Generic Elegant parser that only understands lattice/ele/runtime-state files.
    """

    def __init__(
        self,
        lattice_file: str | Path,
        ele_file: str | Path,
        line_name: str,
        *,
        runtime_json_path: str | Path | None = None,
        elegant_dir: str | Path | None = None,
    ):
        self.lattice_file = Path(lattice_file)
        self.ele_file = Path(ele_file)
        self.line_name = line_name
        self.runtime_json_path = Path(runtime_json_path) if runtime_json_path is not None else None
        self.elegant_dir = Path(elegant_dir) if elegant_dir is not None else self.ele_file.parent

        lattice = lattice_parser(str(self.lattice_file), self.line_name)
        self.lattice, self.trackline_names_list = lattice.get_lattice_tracklinenameslist()
        self.ele = EleParser(str(self.ele_file))
        self.control = self.ele.get_control()

    def build_runtime_state(self) -> dict[str, Any]:
        return {
            "control": copy.deepcopy(self.control),
            "lattice": copy.deepcopy(self.lattice),
            "usedline": list(self.trackline_names_list),
        }

    def dump_runtime_state(self, json_path: str | Path | None = None) -> Path:
        runtime_json_path = self._resolve_runtime_json_path(json_path)
        write_runtime_state(runtime_json_path, self.build_runtime_state())
        return runtime_json_path

    def json_to_lte_ele(
        self,
        lattice_path: str | Path,
        ele_path: str | Path,
        json_path: str | Path | None = None,
    ) -> tuple[Path, Path]:
        runtime_json_path = self._resolve_runtime_json_path(json_path)
        lattice_path = Path(lattice_path)
        ele_path = Path(ele_path)

        lte = read_runtime_state(runtime_json_path)

        with lattice_path.open("w", encoding="utf-8") as handle:
            lattice = lte["lattice"]
            usedline = lte["usedline"]
            seen_names: list[str] = []

            for element_id in usedline:
                element = lattice[element_id]
                seen_names.append(element["NAME"])
                if seen_names.count(element["NAME"]) > 1:
                    continue

                line = f'{element["NAME"]}: {element["TYPE"]}'
                for key, value in element.items():
                    if key not in {"NAME", "TYPE", "AP"}:
                        line += f',{key}="{value}"'
                handle.write(f"{line}\n")

            handle.write(f'\n{self.line_name}: LINE = ({",".join(usedline)})')

        self.ele.control = lte["control"]
        self.ele.control["run_setup"]["use_beamline"] = self.line_name
        self.ele.back2ele(ele_path, lattice_path)
        return lattice_path, ele_path

    def load_bpm_centroids(self, bpmcen_path: str | Path) -> dict[str, dict[str, float]]:
        return _load_bpm_centroids_from_sdds(Path(bpmcen_path))

    def load_watch_image(
        self,
        watch_output_path: str | Path,
        *,
        pixel_shape: tuple[int, int] | list[int],
        pixel_width_mm: float,
    ) -> np.ndarray:
        shape = (int(pixel_shape[0]), int(pixel_shape[1]))
        return _load_watch_image_from_sdds(
            Path(watch_output_path),
            pixel_shape=shape,
            pixel_width_mm=float(pixel_width_mm),
        )

    def _resolve_runtime_json_path(self, json_path: str | Path | None) -> Path:
        if json_path is not None:
            return Path(json_path)
        if self.runtime_json_path is None:
            raise ValueError("runtime_json_path is required for this operation.")
        return self.runtime_json_path


__all__ = [
    "EleParser",
    "ElegantParser",
]
