from __future__ import annotations

from typing import Any


REQUIRED_TOP_LEVEL_KEYS = {
    "schema_version",
    "machine",
    "defaults",
    "groups",
    "knobs",
    "objects",
    "presets",
}

SUPPORTED_OBJECT_VALUE_REDUCERS = {
    "none",
    "mean",
}

SUPPORTED_OBJECT_CAPTURE_MODES = {
    "scalar",
    "waveform",
}

SUPPORTED_OBJECT_KINDS = {
    "scalar",
    "waveform",
}

REQUIRED_MACHINE_KEYS = {"name", "facility", "description"}
REQUIRED_DEFAULT_GROUPS = {"acquisition", "scan", "storage", "safety"}
REQUIRED_ACQUISITION_DEFAULT_KEYS = {"shot_interval_sec", "sample_count", "timeout_sec", "mode"}
REQUIRED_SCAN_DEFAULT_KEYS = {
    "settle_mode",
    "settle_delay_sec",
    "sample_count_per_step",
    "restore_initial_value",
    "max_wait_sec",
}
REQUIRED_STORAGE_DEFAULT_KEYS = {"format", "save_raw_data", "save_analysis_summary"}
REQUIRED_SAFETY_DEFAULT_KEYS = {"confirm_before_write", "abort_on_disconnection"}
REQUIRED_GROUP_KEYS = {"id", "label", "kind", "color", "order"}
REQUIRED_KNOB_KEYS = {
    "id",
    "name",
    "group",
    "write_pv",
    "readback_pv",
    "unit",
    "access",
    "limits",
    "step_hint",
    "settle",
}
REQUIRED_LIMIT_KEYS = {"low", "high"}
REQUIRED_SETTLE_KEYS = {"mode", "delay_sec", "readback_tolerance", "max_wait_sec"}
REQUIRED_OBJECT_KEYS = {
    "id",
    "name",
    "group",
    "read_pv",
    "unit",
    "precision",
    "kind",
    "access",
    "analysis",
}
REQUIRED_ANALYSIS_KEYS = {"jitter", "correlation", "spectrum"}
REQUIRED_PRESET_KEYS = {"id", "name", "mode", "targets"}


def validate_config_dict(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("Config root must be a JSON object")

    missing = REQUIRED_TOP_LEVEL_KEYS.difference(data)
    if missing:
        missing_keys = ", ".join(sorted(missing))
        raise ValueError(f"Missing required config keys: {missing_keys}")

    machine = _require_mapping(data, "machine")
    _require_keys(machine, REQUIRED_MACHINE_KEYS, "machine")

    defaults = _require_mapping(data, "defaults")
    _require_keys(defaults, REQUIRED_DEFAULT_GROUPS, "defaults")
    acquisition_defaults = _require_mapping(defaults, "defaults.acquisition", key="acquisition")
    scan_defaults = _require_mapping(defaults, "defaults.scan", key="scan")
    storage_defaults = _require_mapping(defaults, "defaults.storage", key="storage")
    safety_defaults = _require_mapping(defaults, "defaults.safety", key="safety")
    _require_keys(acquisition_defaults, REQUIRED_ACQUISITION_DEFAULT_KEYS, "defaults.acquisition")
    _require_keys(scan_defaults, REQUIRED_SCAN_DEFAULT_KEYS, "defaults.scan")
    _require_keys(storage_defaults, REQUIRED_STORAGE_DEFAULT_KEYS, "defaults.storage")
    _require_keys(safety_defaults, REQUIRED_SAFETY_DEFAULT_KEYS, "defaults.safety")
    _require_positive_float(acquisition_defaults["shot_interval_sec"], "defaults.acquisition.shot_interval_sec")
    _require_positive_int(acquisition_defaults["sample_count"], "defaults.acquisition.sample_count")
    _require_positive_float(acquisition_defaults["timeout_sec"], "defaults.acquisition.timeout_sec")
    _require_nonnegative_float(scan_defaults["settle_delay_sec"], "defaults.scan.settle_delay_sec")
    _require_positive_int(scan_defaults["sample_count_per_step"], "defaults.scan.sample_count_per_step")
    _require_nonnegative_float(scan_defaults["max_wait_sec"], "defaults.scan.max_wait_sec")

    groups = _require_list(data, "groups")
    knobs = _require_list(data, "knobs")
    objects = _require_list(data, "objects")
    presets = _require_list(data, "presets")

    for index, group in enumerate(groups):
        _require_mapping_item(group, f"groups[{index}]")
        _require_keys(group, REQUIRED_GROUP_KEYS, f"groups[{index}]")
        _require_nonblank(group["id"], f"groups[{index}].id")
    _raise_if_duplicates([item["id"] for item in groups], "group id")
    group_ids = {item["id"] for item in data["groups"]}

    for index, knob in enumerate(knobs):
        _require_mapping_item(knob, f"knobs[{index}]")
        _require_keys(knob, REQUIRED_KNOB_KEYS, f"knobs[{index}]")
        _require_nonblank(knob["id"], f"knobs[{index}].id")
        _require_nonblank(knob["write_pv"], f"knobs[{index}].write_pv")
        if knob["group"] not in group_ids:
            raise ValueError(f"Unknown group for knob {knob['id']}: {knob['group']}")
        limits = _require_mapping(knob, f"knobs[{index}].limits", key="limits")
        settle = _require_mapping(knob, f"knobs[{index}].settle", key="settle")
        _require_keys(limits, REQUIRED_LIMIT_KEYS, f"knobs[{index}].limits")
        _require_keys(settle, REQUIRED_SETTLE_KEYS, f"knobs[{index}].settle")
        low = _require_float(limits["low"], f"knobs[{index}].limits.low")
        high = _require_float(limits["high"], f"knobs[{index}].limits.high")
        if low > high:
            raise ValueError(f"knobs[{index}].limits.low must be <= limits.high")
        _require_float(knob["step_hint"], f"knobs[{index}].step_hint")
        _require_nonnegative_float(settle["delay_sec"], f"knobs[{index}].settle.delay_sec")
        _require_nonnegative_float(
            settle["readback_tolerance"],
            f"knobs[{index}].settle.readback_tolerance",
        )
        _require_nonnegative_float(settle["max_wait_sec"], f"knobs[{index}].settle.max_wait_sec")
    _raise_if_duplicates([item["id"] for item in knobs], "knob id")

    for index, obj in enumerate(objects):
        _require_mapping_item(obj, f"objects[{index}]")
        _require_keys(obj, REQUIRED_OBJECT_KEYS, f"objects[{index}]")
        _require_nonblank(obj["id"], f"objects[{index}].id")
        _require_nonblank(obj["read_pv"], f"objects[{index}].read_pv")
        if obj["group"] not in group_ids:
            raise ValueError(f"Unknown group for object {obj['id']}: {obj['group']}")
        _require_nonnegative_int(obj["precision"], f"objects[{index}].precision")
        analysis = _require_mapping(obj, f"objects[{index}].analysis", key="analysis")
        _require_keys(analysis, REQUIRED_ANALYSIS_KEYS, f"objects[{index}].analysis")
        kind = _normalize_token(obj.get("kind", "scalar"))
        if kind not in SUPPORTED_OBJECT_KINDS:
            raise ValueError(f"Unsupported kind for object {obj['id']}: {obj.get('kind')}")
        reducer = _normalize_token(obj.get("value_reducer", "none"))
        if reducer not in SUPPORTED_OBJECT_VALUE_REDUCERS:
            raise ValueError(
                f"Unsupported value_reducer for object {obj['id']}: {obj.get('value_reducer')}"
            )
        capture_mode = _normalize_token(obj.get("capture_mode", "scalar"))
        if capture_mode not in SUPPORTED_OBJECT_CAPTURE_MODES:
            raise ValueError(
                f"Unsupported capture_mode for object {obj['id']}: {obj.get('capture_mode')}"
            )
        if kind == "scalar" and reducer != "none":
            raise ValueError(
                f"Scalar object {obj['id']} must use value_reducer 'none', got {obj.get('value_reducer')!r}"
            )
        if capture_mode == "waveform" and kind != "waveform":
            raise ValueError(
                f"Raw waveform capture for object {obj['id']} requires kind 'waveform'."
            )
        if kind == "waveform" and capture_mode == "scalar" and reducer != "mean":
            raise ValueError(
                f"Waveform object {obj['id']} captured as scalar must use value_reducer 'mean'."
            )
        if capture_mode == "waveform":
            if reducer != "none":
                raise ValueError(
                    f"Waveform object {obj['id']} must use value_reducer 'none', got {obj.get('value_reducer')!r}"
                )
            raw_sample_interval = obj.get("waveform_sample_interval_sec")
            if raw_sample_interval is None:
                raise ValueError(f"Waveform object {obj['id']} requires waveform_sample_interval_sec.")
            sample_interval = _require_float(
                raw_sample_interval,
                f"objects[{index}].waveform_sample_interval_sec",
            )
            if sample_interval <= 0.0:
                raise ValueError(
                    f"Waveform object {obj['id']} waveform_sample_interval_sec must be positive."
                )
    _raise_if_duplicates([item["id"] for item in objects], "object id")

    knob_ids = {item["id"] for item in data["knobs"]}
    object_ids = {item["id"] for item in data["objects"]}
    for index, preset in enumerate(presets):
        _require_mapping_item(preset, f"presets[{index}]")
        _require_keys(preset, REQUIRED_PRESET_KEYS, f"presets[{index}]")
        _require_nonblank(preset["id"], f"presets[{index}].id")
        target_ids = set(preset.get("targets", []))
        unknown_targets = target_ids.difference(object_ids)
        if unknown_targets:
            raise ValueError(
                f"Unknown preset targets for {preset['id']}: {', '.join(sorted(unknown_targets))}"
            )
        knob_id = preset.get("knob_id")
        if knob_id and knob_id not in knob_ids:
            raise ValueError(f"Unknown preset knob for {preset['id']}: {knob_id}")
        if preset.get("sample_count") is not None:
            _require_positive_int(preset["sample_count"], f"presets[{index}].sample_count")
        if preset.get("shot_interval_sec") is not None:
            _require_positive_float(preset["shot_interval_sec"], f"presets[{index}].shot_interval_sec")
        if preset.get("settle_delay_sec") is not None:
            _require_nonnegative_float(preset["settle_delay_sec"], f"presets[{index}].settle_delay_sec")
        if preset.get("sample_count_per_step") is not None:
            _require_positive_int(
                preset["sample_count_per_step"],
                f"presets[{index}].sample_count_per_step",
            )
    _raise_if_duplicates([item["id"] for item in presets], "preset id")

    return data


def _normalize_token(value: object) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _require_mapping(container: dict[str, Any], path: str, *, key: str | None = None) -> dict[str, Any]:
    value = container[path] if key is None else container[key]
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    return value


def _require_mapping_item(value: object, path: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")


def _require_list(container: dict[str, Any], key: str) -> list[Any]:
    value = container[key]
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list")
    return value


def _require_keys(payload: dict[str, Any], required_keys: set[str], path: str) -> None:
    missing = required_keys.difference(payload)
    if missing:
        raise ValueError(f"Missing required config keys in {path}: {', '.join(sorted(missing))}")


def _require_nonblank(value: object, path: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{path} must not be blank")
    return text


def _require_float(value: object, path: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{path} must be numeric") from exc


def _require_positive_float(value: object, path: str) -> float:
    numeric_value = _require_float(value, path)
    if numeric_value <= 0.0:
        raise ValueError(f"{path} must be positive")
    return numeric_value


def _require_nonnegative_float(value: object, path: str) -> float:
    numeric_value = _require_float(value, path)
    if numeric_value < 0.0:
        raise ValueError(f"{path} must be non-negative")
    return numeric_value


def _require_positive_int(value: object, path: str) -> int:
    try:
        numeric_value = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{path} must be an integer") from exc
    if numeric_value <= 0:
        raise ValueError(f"{path} must be positive")
    return numeric_value


def _require_nonnegative_int(value: object, path: str) -> int:
    try:
        numeric_value = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{path} must be an integer") from exc
    if numeric_value < 0:
        raise ValueError(f"{path} must be non-negative")
    return numeric_value


def _raise_if_duplicates(values: list[object], label: str) -> None:
    seen = set()
    duplicates = []
    for value in values:
        token = str(value).strip()
        if token in seen and token not in duplicates:
            duplicates.append(token)
        seen.add(token)
    if duplicates:
        raise ValueError(f"Duplicate {label}(s): {', '.join(sorted(duplicates))}")
