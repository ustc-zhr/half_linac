from __future__ import annotations

import sys
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
    print(args)
    try:
        if args[1] == "gene_err":
            sta_err_quad_sigma_dxdy = float(args[2])
            jit_err_quad_sigma_k1 = float(args[3])

            error_ele.gen_static_err(sta_err_quad_sigma_dxdy)
            error_ele.gen_jitter_err(jit_err_quad_sigma_k1)
        elif args[1] == "err_off":
            error_ele.err_off()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
