"""Shared startup-theme handoff for independently themed applications."""

from __future__ import annotations

import os
from collections.abc import Mapping


INITIAL_THEME_ENV = "HALF_LINAC_INITIAL_THEME"
THEME_NAMES = frozenset({"dark", "light"})


def resolve_initial_theme(
    default: str = "dark",
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Return a valid one-time startup theme from the process environment."""
    normalized_default = str(default).strip().lower()
    if normalized_default not in THEME_NAMES:
        raise ValueError(f"Unsupported default theme: {default!r}.")

    source = os.environ if environ is None else environ
    requested = str(source.get(INITIAL_THEME_ENV, "")).strip().lower()
    return requested if requested in THEME_NAMES else normalized_default


def environment_with_initial_theme(
    theme: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Copy an environment and add the initial theme for one child process."""
    normalized = str(theme).strip().lower()
    if normalized not in THEME_NAMES:
        raise ValueError(f"Unsupported initial theme: {theme!r}.")

    child_environment = dict(os.environ if environ is None else environ)
    child_environment[INITIAL_THEME_ENV] = normalized
    return child_environment
