"""Final BBA1 line fit with approximate 1-sigma center uncertainty."""

import numpy as np


def analyze_bba1_inner_fit(k1_values, bpm2_samples):
    """Diagnose one BPM2-vs-K1 fit without changing the production fit."""
    result = {
        "slope": None, "slope_sigma": None, "slope_significance": None,
        "r_squared": None, "residual_rms_m": None, "noise_mean_m": None,
        "signal_span_m": None, "signal_to_noise": None, "reduced_chi2": None,
        "quality": "invalid", "reasons": [],
    }
    try:
        x = np.asarray(k1_values, dtype=float).reshape(-1)
        groups = [np.asarray(values, dtype=float).reshape(-1) for values in bpm2_samples]
    except (TypeError, ValueError):
        result["reasons"] = ["Inner fit data is malformed."]
        return result
    if len(x) != len(groups) or len(x) < 3 or any(values.size == 0 for values in groups):
        result["reasons"] = ["At least three K1 points are required."]
        return result
    if not np.all(np.isfinite(x)) or any(not np.all(np.isfinite(values)) for values in groups):
        result["reasons"] = ["Inner fit data contains non-finite values."]
        return result
    means = np.asarray([np.mean(values) for values in groups], dtype=float)
    if np.ptp(x) <= np.finfo(float).eps * max(np.max(np.abs(x)), 1.0):
        result["reasons"] = ["K1 scan has no usable span."]
        return result
    coeff = np.polyfit(x, means, deg=1)
    slope = float(coeff[0])
    fitted = np.polyval(coeff, x)
    residuals = means - fitted
    residual_ss = float(np.sum(residuals ** 2))
    total_ss = float(np.sum((means - np.mean(means)) ** 2))
    result["slope"] = slope
    result["signal_span_m"] = float(np.ptp(means))
    result["residual_rms_m"] = float(np.sqrt(np.mean(residuals ** 2)))
    result["r_squared"] = float(1 - residual_ss / total_ss) if total_ss > 0 else None

    variances = [float(np.var(values, ddof=1)) for values in groups if values.size >= 2]
    noise_mean = float(np.mean(np.sqrt(variances))) if variances else None
    result["noise_mean_m"] = noise_mean
    if variances:
        dof_noise = sum(values.size - 1 for values in groups if values.size >= 2)
        pooled_variance = sum(float(np.sum((values - np.mean(values)) ** 2))
                              for values in groups if values.size >= 2) / dof_noise
        noise_sigma = float(np.sqrt(max(0.0, pooled_variance)))
        if noise_sigma > 0:
            result["signal_to_noise"] = result["signal_span_m"] / noise_sigma
            result["reduced_chi2"] = (residual_ss / noise_sigma ** 2 /
                                       max(1, len(x) - 2))
        if len(x) >= 3:
            design = np.column_stack((x - np.mean(x), np.ones(len(x))))
            covariance = pooled_variance / max(1, len(x) - 2) * np.linalg.pinv(design.T @ design)
            sigma = float(np.sqrt(max(0.0, covariance[0, 0])))
            result["slope_sigma"] = sigma
            if sigma > 0:
                result["slope_significance"] = abs(slope) / sigma
    if result["signal_to_noise"] is None and noise_mean is not None and noise_mean > 0:
        result["signal_to_noise"] = result["signal_span_m"] / noise_mean

    reasons = []
    if result["reduced_chi2"] is not None and result["reduced_chi2"] > 4:
        reasons.append("Residuals exceed repeated-sample noise.")
    elif (result["noise_mean_m"] is not None and result["noise_mean_m"] > 0 and
          result["residual_rms_m"] > 3 * result["noise_mean_m"]):
        reasons.append("Residuals exceed repeated-sample noise.")
    if result["reduced_chi2"] is None and result["r_squared"] is not None and result["r_squared"] < 0.95:
        reasons.append("Inner response is visibly non-linear.")
    weak = ((result["slope_significance"] is not None and result["slope_significance"] < 2) or
            (result["signal_to_noise"] is not None and result["signal_to_noise"] < 3) or
            (abs(slope) <= np.finfo(float).eps * 100 * max(np.max(np.abs(means)), 1.0)))
    if reasons:
        quality = "review"
    elif weak:
        quality = "weak"
        reasons = ["Inner signal is close to the noise floor or has a weak slope."]
    else:
        quality = "good"
    result.update(quality=quality, reasons=reasons)
    return result


def summarize_bba1_inner_fits(inner_fits):
    summary = {"total": len(inner_fits), "good": 0, "weak": 0, "review": 0, "invalid": 0}
    for item in inner_fits:
        quality = item.get("quality", "invalid")
        summary[quality] = summary.get(quality, 0) + 1
    return summary


def build_bba1_scan_guidance(fit_quality):
    """Return read-only next-scan hints from the existing fit diagnostics."""
    if not isinstance(fit_quality, dict):
        return ["Inner diagnostics are unavailable; no range recommendation can be made."]
    inner_fits = fit_quality.get("inner_fits") or []
    if not inner_fits:
        return ["Inner diagnostics are unavailable; no range recommendation can be made."]

    guidance = []
    summary = fit_quality.get("inner_summary") or summarize_bba1_inner_fits(inner_fits)
    total = summary.get("total", len(inner_fits))
    weak = summary.get("weak", 0)
    review = summary.get("review", 0)
    usable_endpoints = [item.get("quality") == "good" for item in inner_fits]
    if weak >= max(1, total - 1):
        guidance.append("Most inner signals are weak; consider widening the K1 range or increasing repeated samples.")
    elif weak and usable_endpoints and all(usable_endpoints[edge] for edge in (0, -1)):
        guidance.append("Weak inner points are concentrated near the response crossing; this may be expected and is not by itself a K1 failure.")
    if review:
        guidance.append("Reviewed inner fits have excess residuals; inspect K1 nonlinearity or outliers, and consider narrowing the K1 range or increasing settling time.")

    positions = np.asarray(fit_quality.get("positions_m", []), dtype=float)
    offset = fit_quality.get("offset_m")
    if positions.size and offset is not None and np.isfinite(offset):
        low, high = float(np.min(positions)), float(np.max(positions))
        span = high - low
        near_edge = span > 0 and min(abs(offset - low), abs(offset - high)) < 0.1 * span
        if offset < low or offset > high or near_edge:
            direction = None
            correctors = np.asarray([item.get("corrector", np.nan) for item in inner_fits], dtype=float)
            slopes = np.asarray([item.get("slope", np.nan) for item in inner_fits], dtype=float)
            if (len(correctors) >= 2 and np.all(np.isfinite(correctors)) and np.all(np.isfinite(slopes)) and
                    np.ptp(correctors) > 0):
                coefficients = np.polyfit(correctors, slopes, deg=1)
                if abs(coefficients[0]) > np.finfo(float).eps:
                    crossing = float(-coefficients[1] / coefficients[0])
                    if crossing < np.min(correctors):
                        direction = "lower COR setpoints"
                    elif crossing > np.max(correctors):
                        direction = "higher COR setpoints"
            target = direction or ("lower BPM1 positions" if offset < low else "higher BPM1 positions")
            guidance.append(f"The response crossing is near or outside the scan edge; extend the COR range toward {target}.")
    if not guidance:
        guidance.append("No scan range change is indicated by the current diagnostics.")
    return guidance


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
