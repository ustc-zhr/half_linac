from __future__ import annotations

import sys
from pathlib import Path

_REPO_BOOTSTRAP_ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / "repo_bootstrap.py").is_file()
)
if str(_REPO_BOOTSTRAP_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_BOOTSTRAP_ROOT))

from repo_bootstrap import ensure_repo_import_path

ensure_repo_import_path(__file__)

from PyQt5.QtWidgets import QApplication

from half_linac.src.shared.machine_profile import MachineProfileError
from half_linac.src.virtual_machine.half_elegant.mainVM import myWindow


if __name__ == "__main__":
    app = QApplication(sys.argv)
    try:
        window = myWindow()
    except MachineProfileError as exc:
        print(f"failed to initialize VM control: {exc}")
        raise SystemExit(1) from exc
    window.show()
    sys.exit(app.exec_())
