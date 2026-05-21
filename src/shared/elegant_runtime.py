from pathlib import Path
import subprocess

import half_linac.runtime_config as st


VM_ELEGANT_DIR = Path(st.rootpath) / "src/virtual_machine/half_elegant/elegant"


def run_elegant_input(ele_name, log_name):
    log_path = VM_ELEGANT_DIR / log_name

    try:
        with log_path.open("w", encoding="utf-8") as log_file:
            subprocess.run(
                ["elegant", ele_name],
                cwd=str(VM_ELEGANT_DIR),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                check=True,
            )
    except FileNotFoundError as exc:
        raise RuntimeError("elegant executable is not available in PATH.") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"elegant {ele_name} failed; see {log_path}") from exc

    return log_path
