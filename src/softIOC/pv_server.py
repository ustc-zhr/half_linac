import json
import sys
import time
from pathlib import Path
from subprocess import Popen

_REPO_BOOTSTRAP_ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / "repo_bootstrap.py").is_file()
)
if str(_REPO_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_BOOTSTRAP_ROOT))

from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

import epics

import half_linac.runtime_config as st
from half_linac.src.virtual_machine.half_elegant.runtime_state import (
    ensure_runtime_state,
    read_runtime_state,
    update_runtime_state,
    write_runtime_state,
)


BEND_TYPES = {"BEND", "CSBEND", "CSRCSBEND", "SBEND", "SBEN"}
COR_TYPES = {"HKICK", "VKICK"}


class pv_server:
    def __init__(self, jsonpath, iocpath):
        self.pvl = []
        self.pv_val = []
        self.pv_objects = []

        self.jsonpath = Path(jsonpath)
        self.iocpath = Path(iocpath)
        self.substitutions_name = self.jsonpath.stem

    def _read_lattice_json(self):
        return read_runtime_state(self.jsonpath)

    def _write_lattice_json(self, data):
        write_runtime_state(self.jsonpath, data)

    @staticmethod
    def _dedupe_in_order(names):
        seen = set()
        ordered = []
        for name in names:
            if not name or name in seen:
                continue
            seen.add(name)
            ordered.append(name)
        return ordered

    def _machine_profile_path(self):
        return Path(st.rootpath) / "configs" / "machines" / "half" / "machine.json"

    def _machine_profile_elements_by_kind(self, kind):
        machine_path = self._machine_profile_path()
        if not machine_path.is_file():
            return []

        machine_data = json.loads(machine_path.read_text(encoding="utf-8"))
        return [
            element["id"]
            for element in machine_data.get("elements", [])
            if element.get("kind") == kind
        ]

    @staticmethod
    def _lattice_element_names(lattice, valid_types):
        return [
            lattice[key]["NAME"]
            for key in lattice
            if lattice[key]["TYPE"] in valid_types
        ]

    @staticmethod
    def _quad_alias(name):
        return f"HALF:IN:AP:QUAD:{name}:K1:ao"

    @staticmethod
    def _corr_set_alias(name):
        return f"HALF:IN:PS:{name}:current:ao"

    @staticmethod
    def _corr_read_alias(name):
        return f"HALF:IN:PS:{name}:current:ai"

    def gen_substitution_file(self):
        """
        generate db/half.substitutions file
        """
        lattice = self._read_lattice_json()["lattice"]
        substitutions_path = self.iocpath / "db" / f"{self.substitutions_name}.substitutions"
        quad_names = self._dedupe_in_order(
            self._lattice_element_names(lattice, {"QUAD"})
            + self._machine_profile_elements_by_kind("quad")
        )
        corr_names = self._dedupe_in_order(
            self._lattice_element_names(lattice, COR_TYPES)
            + self._machine_profile_elements_by_kind("corr")
        )

        with substitutions_path.open("w", encoding="utf-8") as f:
            f.write("file db/quad.template {\n")
            f.write("  pattern {QUAD, K1ALIAS}\n")
            for name in quad_names:
                f.write(f'  {{ "{name}", "{self._quad_alias(name)}" }}\n')
            f.write("}\n")

            f.write("file db/bend.template {\n")
            f.write("  pattern {BEND}\n")
            for key in lattice:
                if lattice[key]["TYPE"] in BEND_TYPES:
                    f.write(f'  {{ "{lattice[key]["NAME"]}" }}\n')
            f.write("}\n")

            f.write("file db/bpm.template {\n")
            f.write("  pattern {BPM}\n")
            for key in lattice:
                if lattice[key]["TYPE"] == "MONI":
                    f.write(f'  {{ "{lattice[key]["NAME"]}" }}\n')
            f.write("}\n")

            f.write("file db/flag.template {\n")
            f.write("  pattern {FLAG}\n")
            for key in lattice:
                if lattice[key]["TYPE"] == "WATCH":
                    f.write(f'  {{ "{lattice[key]["NAME"]}" }}\n')
            f.write("}\n")

            f.write("file db/corr.template {\n")
            f.write("  pattern {COR, SETALIAS, READALIAS}\n")
            for name in corr_names:
                f.write(
                    f'  {{ "{name}", "{self._corr_set_alias(name)}", "{self._corr_read_alias(name)}" }}\n'
                )
            f.write("}\n")

    def prepare_initial_pvs(self):
        """
        collect all PV names and initial values based on lattice.json
        """
        def ensure_corrector_defaults(lte):
            changed = False
            lattice = lte["lattice"]

            for key in lattice:
                if lattice[key]["TYPE"] in COR_TYPES and "KICK" not in lattice[key]:
                    lattice[key]["KICK"] = "0"
                    changed = True

            return changed

        lte, _ = update_runtime_state(self.jsonpath, ensure_corrector_defaults)
        lattice = lte["lattice"]

        pvl = []
        pv_val = []

        for key in lattice:
            elem_type = lattice[key]["TYPE"]
            if elem_type == "QUAD":
                pvl.append(lattice[key]["AP"])
                pv_val.append(lattice[key]["K1"])
            elif elem_type in BEND_TYPES:
                pvl.append(lattice[key]["AP"])
                pv_val.append(lattice[key]["ANGLE"])
            elif elem_type in COR_TYPES:
                pvl.append(lattice[key]["AP"])
                pv_val.append(lattice[key]["KICK"])

        self.pvl = pvl
        self.pv_val = pv_val

        return pvl, pv_val

    def init_lattice_pv(self):
        """
        initialize all PVs' value based on lattice.json
        """
        if not self.pvl:
            self.prepare_initial_pvs()

        epics.caput_many(self.pvl, self.pv_val)

    @staticmethod
    def _normalize_numeric_string(value):
        try:
            return format(float(value), ".16g")
        except (TypeError, ValueError):
            return str(value)

    @classmethod
    def _field_needs_update(cls, current_value, new_value):
        try:
            return float(current_value) != float(new_value)
        except (TypeError, ValueError):
            return str(current_value) != str(new_value)

    def _update_element_from_pv(self, lattice, pvname, value):
        if pvname is None or value is None:
            return False

        parts = pvname.split(":")
        if len(parts) < 4:
            return False

        elem_type = parts[2]
        elem_name = parts[3]
        if elem_name not in lattice:
            return False

        if elem_type == "QUAD":
            field = "K1"
        elif elem_type == "BEND":
            field = "ANGLE"
        elif elem_type == "COR":
            field = "KICK"
        else:
            return False

        current_value = lattice[elem_name].get(field)
        if not self._field_needs_update(current_value, value):
            return False

        lattice[elem_name][field] = self._normalize_numeric_string(value)
        return True

    def monitor_json(self):
        """
        In case caput a new value to a PV, the lattice.json should also be updated
        """

        def onChanges(pvname=None, value=None, char_value=None, **kw):
            _, changed = update_runtime_state(
                self.jsonpath,
                lambda lte: self._update_element_from_pv(lte["lattice"], pvname, value),
            )

            if not changed:
                return

            print('PV Changed:', pvname, ", new value=", value, ", time:", time.ctime())
            print("lattice.json has been updated.\n")

        self.pv_objects = []
        for pv in self.pvl:
            mypv = epics.PV(pv)
            mypv.add_callback(onChanges)
            self.pv_objects.append(mypv)


if __name__ == '__main__':
    jsonpath = Path(st.rootpath) / "src/virtual_machine/half_elegant/halflinac.json"
    iocpath = Path(st.rootpath) / "src/softIOC/halflinac"

    def build_initial_state():
        lattice_file = Path(st.rootpath) / "src/virtual_machine/half_elegant/elegant/lattice_ini.lte"
        ele_file = Path(st.rootpath) / "src/virtual_machine/half_elegant/elegant/one_ini.ele"

        from half_linac.src.virtual_machine.half_elegant.elegant_parser import elegant_parser

        return elegant_parser(str(lattice_file), str(ele_file), "ALL").build_runtime_state()

    ensure_runtime_state(jsonpath, build_initial_state)

    myserver = pv_server(str(jsonpath), str(iocpath))
    myserver.gen_substitution_file()

    ioc_proc = Popen(["bash", "runMe"], cwd=str(iocpath), shell=False)
    try:
        time.sleep(2.0)
        myserver.init_lattice_pv()
        myserver.monitor_json()
        print('Now wait for changes')

        while ioc_proc.poll() is None:
            time.sleep(1.0)
    finally:
        if ioc_proc.poll() is None:
            ioc_proc.terminate()
            ioc_proc.wait(timeout=3.0)
