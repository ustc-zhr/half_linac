"""Final BBA1 line fit with approximate 1-sigma center uncertainty."""

import numpy as np


def fit_bba1_center(positions, responses):
    positions = np.asarray(positions, dtype=float)
    responses = np.asarray(responses, dtype=float)
    result = dict(quality="invalid", reasons=[], offset_m=None, offset_sigma_m=None,
                  r_squared=None, fitted=[], positions_m=[], responses=[])
    result["thresholds"] = dict(min_r_squared=0.95, max_sigma_span_ratio=0.1, min_slope_sigma_ratio=2)
    if positions.ndim != 1 or responses.shape != positions.shape or len(positions) < 2:
        result["reasons"] = ["At least two paired points are required."]
        return result
    if not np.all(np.isfinite(positions)) or not np.all(np.isfinite(responses)):
        result["reasons"] = ["Non-finite fit data."]
        return result
    result.update(positions_m=positions.tolist(), responses=responses.tolist())
    span = float(np.ptp(positions))
    if span <= np.finfo(float).eps * max(float(np.max(np.abs(positions))), np.finfo(float).tiny):
        result["reasons"] = ["BPM1 scan has no usable span."]
        return result
    center = float(np.mean(positions))
    design = np.column_stack(((positions - center) / span, np.ones(len(positions))))
    coefficients, _, _, _ = np.linalg.lstsq(design, responses, rcond=None)
    slope, intercept = coefficients
    fitted = design @ coefficients
    residual_sum = float(np.sum((responses - fitted) ** 2))
    total_sum = float(np.sum((responses - np.mean(responses)) ** 2))
    result["fitted"] = fitted.tolist()
    if total_sum > 0:
        result["r_squared"] = float(1 - residual_sum / total_sum)
    if abs(slope) <= np.finfo(float).eps * 100 * max(float(np.max(np.abs(responses))), np.finfo(float).tiny):
        result["reasons"] = ["Final fit slope is indistinguishable from zero."]
        return result
    offset = float(center - span * intercept / slope)
    if not np.isfinite(offset):
        result["reasons"] = ["Center is not finite."]
        return result
    result["offset_m"] = offset
    if len(positions) < 3:
        result["reasons"] = ["Two points cannot estimate fit uncertainty."]
        return result
    covariance = residual_sum / (len(positions) - 2) * np.linalg.inv(design.T @ design)
    if abs(slope) <= 2 * np.sqrt(max(0, covariance[0, 0])):
        result["reasons"] = ["Slope is not resolved at 2σ; center uncertainty is unreliable."]
        return result
    gradient = np.array([span * intercept / slope ** 2, -span / slope])
    sigma = float(np.sqrt(max(0, gradient @ covariance @ gradient)))
    if not np.isfinite(sigma):
        result["reasons"] = ["Center uncertainty is not finite."]
        return result
    result["offset_sigma_m"] = sigma
    reasons = []
    if result["r_squared"] is None or result["r_squared"] < 0.95:
        reasons.append("R² < 0.95.")
    if sigma > 0.1 * span:
        reasons.append("Center 1σ exceeds 10% of the BPM1 scan span.")
    if not float(np.min(positions)) <= offset <= float(np.max(positions)):
        reasons.append("Center is outside the scanned BPM1 range (extrapolation).")
    result.update(quality="review" if reasons else "good", reasons=reasons)
    return result
