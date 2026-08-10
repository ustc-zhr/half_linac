from __future__ import annotations

import sys

from PyQt5.QtWidgets import QApplication, QMessageBox

from half_linac.src.apps.dispersion_correction.gui.main_window import MainWindow
from half_linac.src.apps.dispersion_correction.profile_runtime import load_profile_run_config
from half_linac.src.shared.machine_profile import MachineProfileError


def main(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    app.setStyle("Fusion")
    try:
        context, config = load_profile_run_config()
    except (MachineProfileError, ValueError) as exc:
        QMessageBox.critical(None, "Dispersion Correction", str(exc))
        return 2
    window = MainWindow(config=config, app_context=context)
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
