from __future__ import annotations

import sys
import math
from pathlib import Path

from scipy.stats import truncnorm

_REPO_BOOTSTRAP_ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / "repo_bootstrap.py").is_file()
)
if str(_REPO_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_BOOTSTRAP_ROOT))

from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

from half_linac.src.shared.machine_profile import resolve_machine_runtime
from half_linac.src.shared.runtime_state import update_runtime_state


class errorVM:
    def __init__(self, sigma_default, jsonpath):
        self.sigma_default = sigma_default
        self.jsonpath = Path(jsonpath)

    def gen_static_err(self, sigma=None):
        if sigma is None:
            sigma = self.sigma_default

        mu = 0
        sigma = sigma * 1e-6

        def apply_static_error(runtime_state):
            lattice = runtime_state["lattice"]
            for key in lattice:
                if lattice[key]["TYPE"] == "QUAD":
                    datax = truncnorm.rvs(-3, 3, loc=mu, scale=sigma)
                    datay = truncnorm.rvs(-3, 3, loc=mu, scale=sigma)
                    lattice[key]["DX"] = str(datax)
                    lattice[key]["DY"] = str(datay)
            return True

        update_runtime_state(self.jsonpath, apply_static_error)

        print("static error is added:   Q DX/DY-", sigma, " m")

    def gen_jitter_err(self, sigma_ppm=None):
        sigma = sigma_ppm * 1e-6

        def apply_jitter_error(runtime_state):
            runtime_state["control"]["error_element"]["amplitude"] = str(sigma)
            return True

        update_runtime_state(self.jsonpath, apply_jitter_error)

        print("jitter is added:   Q K1-", sigma_ppm, " ppm")

    def err_off(self):
        def disable_errors(runtime_state):
            lattice = runtime_state["lattice"]
            for key in lattice:
                if lattice[key]["TYPE"] == "QUAD":
                    lattice[key]["DX"] = "0"
                    lattice[key]["DY"] = "0"

            runtime_state["control"]["error_element"]["amplitude"] = "0"
            return True

        update_runtime_state(self.jsonpath, disable_errors)

        print("static/jitter error is off")


def main(argv=None):
    args = list(sys.argv if argv is None else argv)
    error_ele = errorVM(0, resolve_machine_runtime().vm.runtime_json)
    try:
        if len(args) < 2 or args[1] not in {"gene_err", "err_off"}:
            raise ValueError("Expected gene_err OFFSET_UM JITTER_PPM or err_off")
        offset, jitter = (float(args[2]), float(args[3])) if args[1] == "gene_err" else (0.0, 0.0)
        if not all(math.isfinite(value) and value >= 0 for value in (offset, jitter)):
            raise ValueError("Error RMS values must be finite and nonnegative")
        def apply(state):
            control = state["control"].get("error_element")
            if control is None:
                raise ValueError("This lattice has no error_element configuration")
            for element in state["lattice"].values():
                if element["TYPE"] == "QUAD":
                    element["DX"] = str(truncnorm.rvs(-3, 3, scale=offset*1e-6)) if offset else "0"
                    element["DY"] = str(truncnorm.rvs(-3, 3, scale=offset*1e-6)) if offset else "0"
            control["amplitude"] = str(jitter*1e-6)
            return True
        update_runtime_state(error_ele.jsonpath, apply)
        print(f"Applied error model: offset {offset} um RMS, jitter {jitter} ppm RMS")
        return 0
    except Exception as exc:
        print(f"Error model update failed: {exc}", file=sys.stderr)
        return 1



if __name__ == "__main__":
    raise SystemExit(main())
