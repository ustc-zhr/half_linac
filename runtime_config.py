"""Canonical shared runtime configuration for the HALF codebase."""

from pathlib import Path

# path
rootpath = str(Path(__file__).resolve().parent)

# consts
c_light = 2.99792458e8  # [m/s]
electron_mass = 0.51099895000e6  # [eV]
proton_mass = 938.27208816e6  # [eV]

# refreash time for machine
runtime_machine = 8
runtime_vmmachine = 8  # [s] 8 for VM

# upper limit of corrector
corrector_upperlimit = 0.001  # rad

# flag information for VM and real facility
flag_pixel = [1440, 1080]
flag_pixel_width = 0.02  # [mm]
# ESAflag information
ESAflag_pv = "HALF:IN:FLAG:PRF07:image1:ArrayData"
ESAflag_pixel = [1440, 1080]
ESAflag_pixel_width = 0.02  # [mm]
ESAflag_expotime_pv = "HALF:IN:FLAG:PRF07:cam1:AcquireTime"

# machine_type
machine_type = "vm"  # "real" or "vm"

# ===========if vm======================
# refreash time for vm machine
runtime_vmmachine = 8  # [s] 8 for VM
# flag information for VM and real facility
flag_pixel_vm = [360, 270]  # [360,270]如果与实际一样的话，caget速度太慢，约为1.几秒的样子。
flag_pixel_width_vm = 0.02  # [mm]
# ESAflag information
ESAflag_pv_vm = "HALF:IN:FLAG:PRFESA:image1:ArrayData:vm"
ESAflag_pixel_vm = [720, 270]
ESAflag_pixel_width_vm = 0.02  # [mm]

pv_prefix_quad, pv_suffix_quad = "HALF:IN:QUAD:", ":K1"
pv_prefix_bpmx, pv_suffix_bpmx = "HALF:IN:BPM:", ":X:ao"
pv_prefix_bpmy, pv_suffix_bpmy = "HALF:IN:BPM:", ":Y:ao"
pv_prefix_cor, pv_suffix_cor = "HALF:IN:COR:", ":ao"
pv_prefix_bend, pv_suffix_bend = "HALF:IN:BEND:", ":ANGLE"

__all__ = [
    "rootpath",
    "c_light",
    "electron_mass",
    "proton_mass",
    "runtime_machine",
    "runtime_vmmachine",
    "corrector_upperlimit",
    "flag_pixel",
    "flag_pixel_width",
    "ESAflag_pv",
    "ESAflag_pixel",
    "ESAflag_pixel_width",
    "ESAflag_expotime_pv",
    "machine_type",
    "flag_pixel_vm",
    "flag_pixel_width_vm",
    "ESAflag_pv_vm",
    "ESAflag_pixel_vm",
    "ESAflag_pixel_width_vm",
    "pv_prefix_quad",
    "pv_suffix_quad",
    "pv_prefix_bpmx",
    "pv_suffix_bpmx",
    "pv_prefix_bpmy",
    "pv_suffix_bpmy",
    "pv_prefix_cor",
    "pv_suffix_cor",
    "pv_prefix_bend",
    "pv_suffix_bend",
]
