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
        self._publisher = HalfVmPublisher()

    def _resolve_runtime_json_path(self, j_file):
        if j_file == "halflinac.json":
            return self.runtime_json_path
        return Path(j_file)

    def _resolve_elegant_path(self, pathlike, default_name):
        if pathlike == default_name:
            return self.elegant_dir / default_name
        return Path(pathlike)

    def build_runtime_state(self):
        state = self._shared.build_runtime_state()
        lattice = copy.deepcopy(state["lattice"])
        self._add_channel(lattice)
        state["lattice"] = lattice
        self.lattice = lattice
        self.control = state["control"]
        self.trackline_names_list = state["usedline"]
        return state

    def dump2json(self, j_file="halflinac.json"):
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

    def json2lte_ele(self, lat_f="./elegant/lattice.lte", ele_f="./elegant/one.ele", j_file="halflinac.json"):
        runtime_json_path = self._resolve_runtime_json_path(j_file)
        lattice_path = self._resolve_elegant_path(lat_f, "lattice.lte")
        ele_path = self._resolve_elegant_path(ele_f, "one.ele")
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


    
    
    
