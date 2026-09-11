"""Helpers for running HALF entrypoints without shell-level PYTHONPATH setup."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


def find_repo_root(entry_file: str) -> Path:
    entry_path = Path(entry_file).resolve()
    for candidate in entry_path.parents:
        if (candidate / "repo_bootstrap.py").is_file() and (candidate / "src").is_dir():
            return candidate

    raise RuntimeError(f"Could not locate HALF repository root from {entry_file!r}.")


def ensure_repo_import_path(entry_file: str) -> Path:
    repo_root = find_repo_root(entry_file)
    repo_parent = str(repo_root.parent)

    if repo_parent not in sys.path:
        sys.path.insert(0, repo_parent)

    # Entry points must never import a sibling checkout named half_linac.
    import_root = str(repo_root / "scripts" / "python_imports")
    if import_root not in sys.path:
        sys.path.insert(0, import_root)
    package = importlib.import_module("half_linac")
    if Path(next(iter(package.__path__))).resolve() != repo_root:
        raise RuntimeError("A different HALF checkout is already imported; start a fresh process.")
    os.environ["HALF_LINAC_ROOT"] = str(repo_root)
    os.environ["halflinac_ROOT"] = str(repo_root)
    return repo_root
