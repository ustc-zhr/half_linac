from __future__ import annotations

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

from half_linac.src.shared.elegant_backend import ElegantParser
from half_linac.src.shared.machine_profile import (
    MachineProfileError,
    list_elements,
    load_profile,
    resolve_channel,
    resolve_machine_runtime,
)
from half_linac.src.shared.machine_profile.softioc_contract import resolve_softioc_vm_alias
from half_linac.src.shared.runtime_state import (
    ensure_runtime_state,
    read_runtime_state,
    update_runtime_state,
    write_runtime_state,
)


BEND_TYPES = {"BEND", "CSBEND", "CSRCSBEND", "SBEND", "SBEN"}
COR_TYPES = {"HKICK", "VKICK"}
LATTICE_FIELD_BY_CHANNEL = {
    ("quad", "k1"): "K1",
    ("corr", "kick"): "KICK",
    ("bend", "angle"): "ANGLE",
}


class pv_server:
    def __init__(self, jsonpath, iocpath, machine_id: str | None = None):
        self.pvl = []
        self.pv_val = []
        self.pv_objects = []

        self.jsonpath = Path(jsonpath)
        self.iocpath = Path(iocpath)
        self.machine_profile = load_profile(machine_id)
        self.runtime = resolve_machine_runtime(self.machine_profile)
        self.substitutions_path = self.runtime.softioc.substitutions_file
        self._pv_to_lattice_field: dict[str, tuple[str, str]] = {}

    def _read_lattice_json(self):
        return read_runtime_state(self.jsonpath)

    def _write_lattice_json(self, data):
        write_runtime_state(self.jsonpath, data)

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

    @staticmethod
    def _internal_record_name(*parts: str) -> str:
        return ":".join(("VMIOC", *parts))

    def _resolve_vm_channel(self, element_id: str, logical_channel: str) -> str | None:
        try:
            return resolve_channel(self.machine_profile, element_id, logical_channel, "vm")
        except MachineProfileError:
            return None

    def _resolve_vm_writable_channel(
        self,
        element_id: str,
        element_kind: str,
        logical_channel: str,
    ) -> str | None:
        return resolve_softioc_vm_alias(
            self.machine_profile,
            element_id,
            element_kind,
            logical_channel,
        )

    def _flag_record_name(self, element_id: str, logical_channel: str) -> str:
        return self._internal_record_name("FLAG", element_id, logical_channel.upper())

    @staticmethod
    def _write_substitution_section(handle, template_name: str, pattern: str, rows: list[str]) -> None:
        handle.write(f"file db/{template_name} {{\n")
        handle.write(f"  pattern {{{pattern}}}\n")
        for row in rows:
            handle.write(f"{row}\n")
        handle.write("}\n")

    def gen_substitution_file(self):
        self.substitutions_path.parent.mkdir(parents=True, exist_ok=True)

        quad_elements = list_elements(self.machine_profile, kind="quad", logical_channel="k1")
        corr_elements = list_elements(self.machine_profile, kind="corr")
        bpm_elements = list_elements(self.machine_profile, kind="bpm")
        bend_elements = list_elements(self.machine_profile, kind="bend")
        flag_elements = list_elements(self.machine_profile, kind="flag")

        with self.substitutions_path.open("w", encoding="utf-8") as handle:
            quad_rows: list[str] = []
            for element in quad_elements:
                alias = self._resolve_vm_channel(element.id, "k1")
                if not alias:
                    continue
                record = self._internal_record_name("QUAD", element.id, "K1")
                quad_rows.append(f'  {{ "{element.id}", "{record}", "{alias}" }}')
            self._write_substitution_section(handle, "quad.template", "QUAD, RECORD, K1ALIAS", quad_rows)

            bend_rows: list[str] = []
            for element in bend_elements:
                alias = self._resolve_vm_writable_channel(element.id, element.kind, "angle")
                if not alias:
                    continue
                record = self._internal_record_name("BEND", element.id, "ANGLE")
                bend_rows.append(f'  {{ "{element.id}", "{record}", "{alias}" }}')
            self._write_substitution_section(
                handle,
                "bend.template",
                "BEND, RECORD, ANGLEALIAS",
                bend_rows,
            )

            bpm_rows: list[str] = []
            for element in bpm_elements:
                x_alias = self._resolve_vm_channel(element.id, "x")
                y_alias = self._resolve_vm_channel(element.id, "y")
                if not x_alias or not y_alias:
                    continue
                x_record = self._internal_record_name("BPM", element.id, "X")
                y_record = self._internal_record_name("BPM", element.id, "Y")
                bpm_rows.append(
                    f'  {{ "{element.id}", "{x_record}", "{x_alias}", "{y_record}", "{y_alias}" }}'
                )
            self._write_substitution_section(
                handle,
                "bpm.template",
                "BPM, XRECORD, XALIAS, YRECORD, YALIAS",
                bpm_rows,
            )

            flag_image_rows: list[str] = []
            flag_esa_rows: list[str] = []
            flag_sigx_rows: list[str] = []
            flag_sigy_rows: list[str] = []
            flag_exposure_rows: list[str] = []
            for element in flag_elements:
                image_alias = self._resolve_vm_channel(element.id, "image")
                if image_alias:
                    flag_image_rows.append(
                        f'  {{ "{element.id}", "{self._flag_record_name(element.id, "image")}", "{image_alias}" }}'
                    )

                esa_alias = self._resolve_vm_channel(element.id, "esa_image")
                if esa_alias:
                    flag_esa_rows.append(
                        f'  {{ "{element.id}", "{self._flag_record_name(element.id, "esa_image")}", "{esa_alias}" }}'
                    )

                sigx_alias = self._resolve_vm_channel(element.id, "sigx")
                if sigx_alias:
                    flag_sigx_rows.append(
                        f'  {{ "{element.id}", "{self._flag_record_name(element.id, "sigx")}", "{sigx_alias}" }}'
                    )

                sigy_alias = self._resolve_vm_channel(element.id, "sigy")
                if sigy_alias:
                    flag_sigy_rows.append(
                        f'  {{ "{element.id}", "{self._flag_record_name(element.id, "sigy")}", "{sigy_alias}" }}'
                    )

                expo_alias = self._resolve_vm_channel(element.id, "exposure_time")
                if expo_alias:
                    flag_exposure_rows.append(
                        f'  {{ "{element.id}", "{self._flag_record_name(element.id, "exposure_time")}", "{expo_alias}" }}'
                    )

            self._write_substitution_section(
                handle,
                "flag.template",
                "FLAG, IMAGERECORD, IMAGEALIAS",
                flag_image_rows,
            )
            self._write_substitution_section(
                handle,
                "flag_esa.template",
                "FLAG, ESARECORD, ESAALIAS",
                flag_esa_rows,
            )
            self._write_substitution_section(
                handle,
                "flag_sigx.template",
                "FLAG, SIGXRECORD, SIGXALIAS",
                flag_sigx_rows,
            )
            self._write_substitution_section(
                handle,
                "flag_sigy.template",
                "FLAG, SIGYRECORD, SIGYALIAS",
                flag_sigy_rows,
            )
            self._write_substitution_section(
                handle,
                "flag_expotime.template",
                "FLAG, EXPOTIMERECORD, EXPOTIMEALIAS",
                flag_exposure_rows,
            )

            corr_rows: list[str] = []
            for element in corr_elements:
                alias = self._resolve_vm_writable_channel(element.id, element.kind, "kick")
                if not alias:
                    continue
                set_record = self._internal_record_name("COR", element.id, "SET")
                read_record = self._internal_record_name("COR", element.id, "READ")
                corr_rows.append(
                    f'  {{ "{element.id}", "{set_record}", "{alias}", "{read_record}" }}'
                )
            self._write_substitution_section(
                handle,
                "corr.template",
                "COR, SETRECORD, SETALIAS, READRECORD",
                corr_rows,
            )

    def prepare_initial_pvs(self):
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

        pvl: list[str] = []
        pv_val: list[str] = []
        pv_to_lattice_field: dict[str, tuple[str, str]] = {}

        for element in self.machine_profile.elements:
            element_lattice = lattice.get(element.id)
            if not isinstance(element_lattice, dict):
                continue

            for logical_channel, field_name in self._writable_channels_for_element(element.kind):
                pv_name = self._resolve_vm_writable_channel(
                    element.id,
                    element.kind,
                    logical_channel,
                )
                if not pv_name or field_name not in element_lattice:
                    continue
                pvl.append(pv_name)
                pv_val.append(element_lattice[field_name])
                pv_to_lattice_field[pv_name] = (element.id, field_name)

        self.pvl = pvl
        self.pv_val = pv_val
        self._pv_to_lattice_field = pv_to_lattice_field
        return pvl, pv_val

    def init_lattice_pv(self):
        if not self.pvl:
            self.prepare_initial_pvs()

        epics.caput_many(self.pvl, self.pv_val)

    def _writable_channels_for_element(self, kind: str) -> tuple[tuple[str, str], ...]:
        writable = [
            (logical_channel, field_name)
            for (element_kind, logical_channel), field_name in LATTICE_FIELD_BY_CHANNEL.items()
            if element_kind == kind
        ]
        return tuple(writable)

    def _update_element_from_pv(self, lattice, pvname, value):
        if pvname is None or value is None:
            return False

        element_field = self._pv_to_lattice_field.get(pvname)
        if element_field is None:
            return False

        element_id, field_name = element_field
        element = lattice.get(element_id)
        if not isinstance(element, dict):
            return False

        current_value = element.get(field_name)
        if not self._field_needs_update(current_value, value):
            return False

        element[field_name] = self._normalize_numeric_string(value)
        return True

    def monitor_json(self):
        def onChanges(pvname=None, value=None, char_value=None, **kw):
            _, changed = update_runtime_state(
                self.jsonpath,
                lambda lte: self._update_element_from_pv(lte["lattice"], pvname, value),
            )

            if not changed:
                return

            print("PV Changed:", pvname, ", new value=", value, ", time:", time.ctime())
            print("lattice.json has been updated.\n")

        self.pv_objects = []
        for pv in self.pvl:
            mypv = epics.PV(pv)
            mypv.add_callback(onChanges)
            self.pv_objects.append(mypv)


if __name__ == "__main__":
    runtime = resolve_machine_runtime()
    jsonpath = runtime.vm.runtime_json
    iocpath = runtime.softioc.root

    def build_initial_state():
        return ElegantParser(
            runtime.vm.bootstrap_lattice,
            runtime.vm.bootstrap_ele,
            runtime.vm.line_name,
        ).build_runtime_state()

    ensure_runtime_state(jsonpath, build_initial_state)

    myserver = pv_server(str(jsonpath), str(iocpath))
    myserver.gen_substitution_file()

    ioc_proc = Popen(["bash", "runMe"], cwd=str(iocpath), shell=False)
    try:
        time.sleep(2.0)
        myserver.init_lattice_pv()
        myserver.monitor_json()
        print("Now wait for changes")

        while ioc_proc.poll() is None:
            time.sleep(1.0)
    finally:
        if ioc_proc.poll() is None:
            ioc_proc.terminate()
            ioc_proc.wait(timeout=3.0)
