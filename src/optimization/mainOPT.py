from __future__ import annotations

import os
import sys
from pathlib import Path


def _bootstrap_gotacc() -> None:
    """Keep the legacy optimization entrypoint working with the vendored GOTAcc tree."""
    gotacc_root = Path(__file__).resolve().parent / "GOTAcc"
    gotacc_src = gotacc_root / "src"

    if not gotacc_root.is_dir():
        raise FileNotFoundError(f"GOTAcc directory not found: {gotacc_root}")

    if str(gotacc_src) not in sys.path:
        sys.path.insert(0, str(gotacc_src))

    # Run from the GOTAcc root so relative save/ and cache paths stay inside that subtree.
    os.chdir(gotacc_root)


_bootstrap_gotacc()

from gotacc.gui.main import main


if __name__ == "__main__":
    main()
