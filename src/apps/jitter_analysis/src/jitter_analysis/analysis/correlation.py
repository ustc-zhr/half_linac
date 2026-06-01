from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np


@dataclass(slots=True)
class CorrelationResult:
    names: list[str]
    matrix: np.ndarray
    valid_counts: np.ndarray


def compute_correlation_matrix(series_by_name: Mapping[str, Sequence[float]]) -> CorrelationResult:
    names = list(series_by_name)
    if len(names) < 2:
        raise ValueError("At least two series are required for correlation analysis")

    arrays = [np.asarray(series_by_name[name], dtype=float) for name in names]
    lengths = {array.size for array in arrays}
    if len(lengths) != 1:
        raise ValueError("All series must have the same number of samples")

    count = len(arrays)
    matrix = np.full((count, count), np.nan, dtype=float)
    valid_counts = np.zeros((count, count), dtype=int)
    finite_masks = [np.isfinite(array) for array in arrays]

    for row in range(count):
        for col in range(count):
            valid_mask = finite_masks[row] & finite_masks[col]
            valid_count = int(np.count_nonzero(valid_mask))
            valid_counts[row, col] = valid_count

            if row == col:
                matrix[row, col] = 1.0 if valid_count >= 1 else np.nan
                continue

            if valid_count < 2:
                continue

            left = arrays[row][valid_mask]
            right = arrays[col][valid_mask]
            left_std = float(np.std(left))
            right_std = float(np.std(right))
            if left_std <= 0.0 or right_std <= 0.0:
                continue
            matrix[row, col] = float(np.corrcoef(left, right)[0, 1])

    return CorrelationResult(names=names, matrix=matrix, valid_counts=valid_counts)
