"""Algorithm package for GOTAcc.

Subpackages are intentionally not imported eagerly so lightweight optimizers can
be used without importing optional heavy dependencies required by other
algorithms.
"""

__all__ = [
    "single_objective",
    "multi_objective",
]
