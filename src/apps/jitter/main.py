from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Sequence


def _bootstrap_jitter_analysis() -> None:
    """Run the externally maintained jitter_analysis tree from this wrapper."""
    app_root = Path(__file__).resolve().parent
    jitter_root = app_root / "jitter_analysis"
    jitter_src = jitter_root / "src"

    if not jitter_root.is_dir():
        raise FileNotFoundError(f"jitter_analysis directory not found: {jitter_root}")

    if str(jitter_src) not in sys.path:
        sys.path.insert(0, str(jitter_src))

    # Keep relative runtime outputs such as runs/ inside the external subtree.
    os.chdir(jitter_root)


def main(argv: Sequence[str] | None = None) -> int:
    _bootstrap_jitter_analysis()

    from jitter_analysis.app import main as jitter_main

    return jitter_main(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
