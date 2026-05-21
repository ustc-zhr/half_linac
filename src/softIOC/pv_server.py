import time
from pathlib import Path
from subprocess import Popen

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

    def gen_substitution_file(self):
        """
        generate db/half.substitutions file
        """
        lattice = self._read_lattice_json()["lattice"]
        substitutions_path = self.iocpath / "db" / f"{self.substitutions_name}.substitutions"

        with substitutions_path.open("w", encoding="utf-8") as f:
            f.write("file db/quad.template {\n")
            f.write("  pattern {QUAD}\n")
            for key in lattice:
                if lattice[key]["TYPE"] == "QUAD":
                    f.write(f'  {{ "{lattice[key]["NAME"]}" }}\n')
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
            f.write("  pattern {COR}\n")
            for key in lattice:
                if lattice[key]["TYPE"] in COR_TYPES:
                    f.write(f'  {{ "{lattice[key]["NAME"]}" }}\n')
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
