from __future__ import annotations

import copy
from pathlib import Path

import half_linac.runtime_config as st
from half_linac.src.shared.elegant_backend import EleParser, ElegantParser
from half_linac.src.shared.machine_profile import resolve_machine_runtime
from half_linac.src.shared.runtime_state import read_runtime_state, write_runtime_state
from half_linac.src.virtual_machine.half_elegant.vm_publish import HalfVmPublisher


BEND_TYPES = {"CSRCSBEND", "CSBEND", "BEND", "SBEN", "SBEND"}
COR_TYPES = {"HKICK", "VKICK"}


class elegant_parser:
    """
    HALF compatibility wrapper around the shared Elegant parser.
    """

    def __init__(self, lat_file, ele_file, line_name):
        runtime = resolve_machine_runtime()
        self.lattice_file = lat_file
        self.line = line_name
        self.ele_file = ele_file
        self.vm_dir = runtime.vm.root
        self.elegant_dir = runtime.vm.bootstrap_lattice.parent
        self.runtime_json_path = runtime.vm.runtime_json
        self.default_lattice_output_path = self.elegant_dir / "lattice.lte"
        self.default_ele_output_path = self.elegant_dir / "one.ele"

        self._shared = ElegantParser(
            self.lattice_file,
            self.ele_file,
            self.line,
            runtime_json_path=self.runtime_json_path,
            elegant_dir=self.elegant_dir,
        )
        self.lattice = self._shared.lattice
        self.trackline_names_list = self._shared.trackline_names_list
        self.ele = EleParser(self.ele_file)
        self.control = self._shared.control
        self._publisher = HalfVmPublisher(runtime.profile)

    def _resolve_runtime_json_path(self, j_file=None):
        if j_file is None:
            return self.runtime_json_path
        path = Path(j_file)
        default_aliases = {
            self.runtime_json_path.name,
            f"./{self.runtime_json_path.name}",
        }
        if not path.is_absolute() and path.as_posix() in default_aliases:
            return self.runtime_json_path
        return path

    def _resolve_elegant_path(self, pathlike, default_path):
        if pathlike is None:
            return default_path

        path = Path(pathlike)
        if path.is_absolute():
            return path

        aliases = {
            default_path.name,
            f"./{default_path.name}",
        }
        try:
            relative_default = default_path.relative_to(self.vm_dir).as_posix()
        except ValueError:
            relative_default = None
        if relative_default is not None:
            aliases.add(relative_default)
            aliases.add(f"./{relative_default}")

        if path.as_posix() in aliases:
            return default_path
        return path

    def build_runtime_state(self):
        state = self._shared.build_runtime_state()
        lattice = copy.deepcopy(state["lattice"])
        self._add_channel(lattice)
        state["lattice"] = lattice
        self.lattice = lattice
        self.control = state["control"]
        self.trackline_names_list = state["usedline"]
        return state

    def dump2json(self, j_file=None):
        runtime_json_path = self._resolve_runtime_json_path(j_file)
        write_runtime_state(runtime_json_path, self.build_runtime_state())
        return runtime_json_path

    def dump_runtime_state(self, json_path=None):
        if json_path is None:
            return self.dump2json()
        return self.dump2json(str(json_path))

    def _add_channel(self, lattice):
        for element_id, element in lattice.items():
            if element["TYPE"] == "QUAD":
                element["AP"] = st.pv_prefix_quad + element_id + st.pv_suffix_quad
            elif element["TYPE"] in BEND_TYPES:
                element["AP"] = st.pv_prefix_bend + element_id + st.pv_suffix_bend
            elif element["TYPE"] in COR_TYPES:
                element["AP"] = st.pv_prefix_cor + element_id + st.pv_suffix_cor

    def json2lte_ele(self, lat_f=None, ele_f=None, j_file=None):
        runtime_json_path = self._resolve_runtime_json_path(j_file)
        lattice_path = self._resolve_elegant_path(lat_f, self.default_lattice_output_path)
        ele_path = self._resolve_elegant_path(ele_f, self.default_ele_output_path)
        return self._shared.json_to_lte_ele(lattice_path, ele_path, runtime_json_path)

    def json_to_lte_ele(self, lattice_path, ele_path, json_path=None):
        return self._shared.json_to_lte_ele(lattice_path, ele_path, json_path)

    def broadcast_bpm(self):
        return self._publisher.publish_bpm(self.elegant_dir / "one.bpmcen")

    def broadcast_flag(self):
        usedline = read_runtime_state(self.runtime_json_path)["usedline"]
        return self._publisher.publish_flags(
            lattice=self.lattice,
            usedline=usedline,
            elegant_dir=self.elegant_dir,
        )


class ele_parser(EleParser):
    pass


    
    
    
