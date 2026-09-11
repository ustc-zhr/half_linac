"""Resolve the canonical package name inside a checkout with any directory name."""
from pathlib import Path

__path__ = [str(Path(__file__).resolve().parents[3])]
