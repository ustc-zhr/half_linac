#!/usr/bin/env python3
"""Run Python modules/scripts using this checkout, including renamed worktrees."""
import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from repo_bootstrap import ensure_repo_import_path
ensure_repo_import_path(__file__)
args = sys.argv[1:]
if not args:
    raise SystemExit('Usage: python scripts/repo_python.py [-m module | script.py] [arguments]')
if args[0] == '-m':
    sys.argv = args[1:]
    runpy.run_module(args[1], run_name='__main__', alter_sys=True)
else:
    sys.argv = args
    runpy.run_path(args[0], run_name='__main__')
