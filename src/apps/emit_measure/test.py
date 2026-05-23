from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import sdds

_REPO_BOOTSTRAP_ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / "repo_bootstrap.py").is_file()
)
if str(_REPO_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_BOOTSTRAP_ROOT))

from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

from half_linac.src.shared.machine_profile import load_app_context


def load_emit_transfer_matrix(machine_id: str | None = None) -> np.ndarray:
    context = load_app_context("emit_measure", machine_id=machine_id)
    if context.model_backend is None:
        raise RuntimeError("emit_measure app context does not define a model backend.")

    emit_mat_path = Path(context.model_backend.config["emit_mat"])
    matrix_file = sdds.SDDS(0)
    matrix_file.load(str(emit_mat_path))
    values = [matrix_file.columnData[i][0][0] for i in range(12, 48)]
    return np.array(values).reshape(6, 6)


def main() -> int:
    matrix = load_emit_transfer_matrix()
    print(matrix)
    print(matrix.T)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
