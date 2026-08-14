from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from half_linac.src.shared.elegant_backend import ElegantParser

from .models import AppContext, MachineProfileError, normalize_mode
from .resolver import resolve_channel


MODEL_SNAPSHOT_SOURCE_DESIGN = "design"
MODEL_SNAPSHOT_SOURCE_LIVE_FROM_REAL = "live_from_real"
MODEL_SNAPSHOT_SOURCE_LIVE_FROM_VM = "live_from_vm"
MODEL_SNAPSHOT_SOURCE_SAVED = "saved"
MODEL_SNAPSHOT_SCHEMA_VERSION = "1"

ModelSnapshotSource = str
ModelFieldRequest = tuple[str, str]
PvReader = Callable[[str], Any]


@dataclass(frozen=True)
class ModelSnapshotField:
    element_id: str
    field_name: str
    value: float
    source: str
    source_pv: str | None
    source_value: float | None
    source_unit: str | None
    model_unit: str | None
    conversion: Mapping[str, Any]
    status: str = "ok"

    def as_metadata(self) -> dict[str, Any]:
        return {
            "element_id": self.element_id,
            "field_name": self.field_name,
            "value": self.value,
            "source": self.source,
            "source_pv": self.source_pv,
            "source_value": self.source_value,
            "source_unit": self.source_unit,
            "model_unit": self.model_unit,
            "conversion": dict(self.conversion),
            "status": self.status,
        }


@dataclass(frozen=True)
class ModelSnapshot:
    source: str
    machine_id: str
    control_backend: str
    created_at: str
    fields: tuple[ModelSnapshotField, ...]
    origin_source: str | None = None
    source_path: str | None = None

    @property
    def lattice_overrides(self) -> dict[str, dict[str, float]]:
        overrides: dict[str, dict[str, float]] = {}
        for field in self.fields:
            overrides.setdefault(field.element_id, {})[field.field_name] = field.value
        return overrides

    def as_metadata(self) -> dict[str, Any]:
        metadata = {
            "schema_version": MODEL_SNAPSHOT_SCHEMA_VERSION,
            "source": self.source,
            "machine_id": self.machine_id,
            "control_backend": self.control_backend,
            "created_at": self.created_at,
            "fields": [field.as_metadata() for field in self.fields],
        }
        if self.origin_source is not None:
            metadata["origin_source"] = self.origin_source
        if self.source_path is not None:
            metadata["source_path"] = self.source_path
        return metadata


def build_model_snapshot(
    app_context: AppContext,
    requested_fields: Sequence[ModelFieldRequest],
    *,
    source: ModelSnapshotSource | None = None,
    pv_reader: PvReader | None = None,
    saved_snapshot_path: str | Path | None = None,
) -> ModelSnapshot:
    if app_context.model_backend is None:
        raise MachineProfileError(
            f"AppContext for {app_context.app_name!r} does not define a model backend."
        )
    if not requested_fields:
        raise MachineProfileError("Model snapshot requires at least one requested field.")

    snapshot_source = _resolve_snapshot_source(source, app_context.control_backend.name)
    if snapshot_source == MODEL_SNAPSHOT_SOURCE_SAVED:
        if saved_snapshot_path is None:
            raise MachineProfileError("Saved model snapshot source requires saved_snapshot_path.")
        return load_model_snapshot(
            saved_snapshot_path,
            requested_fields=requested_fields,
            app_context=app_context,
        )

    created_at = datetime.now().astimezone().isoformat(timespec="seconds")
    if snapshot_source == MODEL_SNAPSHOT_SOURCE_DESIGN:
        fields = _build_design_fields(app_context, requested_fields)
    elif snapshot_source in {MODEL_SNAPSHOT_SOURCE_LIVE_FROM_REAL, MODEL_SNAPSHOT_SOURCE_LIVE_FROM_VM}:
        fields = _build_live_fields(
            app_context,
            requested_fields,
            source=snapshot_source,
            pv_reader=pv_reader or _default_pv_reader,
        )
    else:
        raise MachineProfileError(f"Unsupported model snapshot source: {snapshot_source!r}")

    return ModelSnapshot(
        source=snapshot_source,
        machine_id=app_context.machine.id,
        control_backend=app_context.control_backend.name,
        created_at=created_at,
        fields=tuple(fields),
    )


def save_model_snapshot(
    snapshot: ModelSnapshot,
    path: str | Path,
    *,
    extra_metadata: Mapping[str, Any] | None = None,
) -> Path:
    snapshot_path = Path(path)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    metadata = snapshot.as_metadata()
    if extra_metadata:
        metadata["extra_metadata"] = dict(extra_metadata)
    try:
        snapshot_path.write_text(
            json.dumps(metadata, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except (OSError, TypeError) as exc:
        raise MachineProfileError(f"Failed to save model snapshot: {snapshot_path}") from exc
    return snapshot_path


def load_model_snapshot(
    path: str | Path,
    *,
    requested_fields: Sequence[ModelFieldRequest] | None = None,
    app_context: AppContext | None = None,
) -> ModelSnapshot:
    snapshot_path = Path(path)
    try:
        raw = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MachineProfileError(f"Saved model snapshot does not exist: {snapshot_path}") from exc
    except json.JSONDecodeError as exc:
        raise MachineProfileError(f"Invalid saved model snapshot JSON: {snapshot_path}") from exc

    if not isinstance(raw, Mapping):
        raise MachineProfileError(f"Saved model snapshot must be a JSON object: {snapshot_path}")
    snapshot = _model_snapshot_from_metadata(raw, source_path=snapshot_path)

    if app_context is not None and snapshot.machine_id != app_context.machine.id:
        raise MachineProfileError(
            "Saved model snapshot machine mismatch: "
            f"file={snapshot.machine_id!r}, current={app_context.machine.id!r}."
        )

    if requested_fields is None:
        return snapshot
    return _select_snapshot_fields(snapshot, requested_fields)


def model_snapshot_lattice_overrides(
    snapshot: ModelSnapshot | Mapping[str, Any] | None,
) -> dict[str, dict[str, float]] | None:
    if snapshot is None:
        return None
    if isinstance(snapshot, ModelSnapshot):
        return snapshot.lattice_overrides or None
    if not isinstance(snapshot, Mapping):
        return None

    fields = snapshot.get("fields", [])
    if not isinstance(fields, list):
        return None
    overrides: dict[str, dict[str, float]] = {}
    for field in fields:
        if not isinstance(field, Mapping):
            continue
        element_id = str(field.get("element_id", "")).strip()
        field_name = str(field.get("field_name", "")).strip()
        if not element_id or not field_name or "value" not in field:
            continue
        try:
            value = _finite_float(
                field["value"],
                f"model snapshot {element_id}.{field_name}.value",
            )
        except MachineProfileError:
            continue
        overrides.setdefault(element_id, {})[field_name] = value
    return overrides or None


def _resolve_snapshot_source(source: str | None, control_backend: str) -> str:
    if source is None or str(source).strip().lower() == "live":
        backend = normalize_mode(control_backend, "control_backend")
        return (
            MODEL_SNAPSHOT_SOURCE_LIVE_FROM_REAL
            if backend == "real"
            else MODEL_SNAPSHOT_SOURCE_LIVE_FROM_VM
        )

    normalized = str(source).strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "design": MODEL_SNAPSHOT_SOURCE_DESIGN,
        "live_from_real": MODEL_SNAPSHOT_SOURCE_LIVE_FROM_REAL,
        "real": MODEL_SNAPSHOT_SOURCE_LIVE_FROM_REAL,
        "live_real": MODEL_SNAPSHOT_SOURCE_LIVE_FROM_REAL,
        "live_from_vm": MODEL_SNAPSHOT_SOURCE_LIVE_FROM_VM,
        "vm": MODEL_SNAPSHOT_SOURCE_LIVE_FROM_VM,
        "live_vm": MODEL_SNAPSHOT_SOURCE_LIVE_FROM_VM,
        "saved": MODEL_SNAPSHOT_SOURCE_SAVED,
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise MachineProfileError(f"Unknown model snapshot source: {source!r}") from exc


def _model_snapshot_from_metadata(
    metadata: Mapping[str, Any],
    *,
    source_path: Path,
) -> ModelSnapshot:
    schema_version = str(metadata.get("schema_version", "")).strip()
    if schema_version != MODEL_SNAPSHOT_SCHEMA_VERSION:
        raise MachineProfileError(
            f"Unsupported model snapshot schema {schema_version!r}; "
            f"expected {MODEL_SNAPSHOT_SCHEMA_VERSION!r}."
        )

    saved_source = _required_string(metadata.get("source"), "model snapshot source")
    original_source = _optional_string(metadata.get("origin_source")) or saved_source
    machine_id = _required_string(metadata.get("machine_id"), "model snapshot machine_id")
    control_backend = _required_string(
        metadata.get("control_backend"),
        "model snapshot control_backend",
    )
    created_at = _required_string(metadata.get("created_at"), "model snapshot created_at")
    raw_fields = metadata.get("fields")
    if not isinstance(raw_fields, list) or not raw_fields:
        raise MachineProfileError("Saved model snapshot requires a non-empty fields list.")

    fields = []
    for index, raw_field in enumerate(raw_fields):
        if not isinstance(raw_field, Mapping):
            raise MachineProfileError(f"model snapshot fields[{index}] must be a mapping.")
        fields.append(_model_snapshot_field_from_metadata(raw_field, index))

    return ModelSnapshot(
        source=MODEL_SNAPSHOT_SOURCE_SAVED,
        machine_id=machine_id,
        control_backend=control_backend,
        created_at=created_at,
        fields=tuple(fields),
        origin_source=original_source,
        source_path=str(source_path),
    )


def _model_snapshot_field_from_metadata(
    metadata: Mapping[str, Any],
    index: int,
) -> ModelSnapshotField:
    location = f"model snapshot fields[{index}]"
    conversion = metadata.get("conversion", {"type": "direct"})
    if not isinstance(conversion, Mapping):
        raise MachineProfileError(f"{location}.conversion must be a mapping.")
    source_value = metadata.get("source_value")
    return ModelSnapshotField(
        element_id=_required_string(metadata.get("element_id"), f"{location}.element_id"),
        field_name=_required_string(metadata.get("field_name"), f"{location}.field_name"),
        value=_finite_float(metadata.get("value"), f"{location}.value"),
        source=_optional_string(metadata.get("source")) or MODEL_SNAPSHOT_SOURCE_SAVED,
        source_pv=_optional_string(metadata.get("source_pv")),
        source_value=None
        if source_value is None
        else _finite_float(source_value, f"{location}.source_value"),
        source_unit=_optional_string(metadata.get("source_unit")),
        model_unit=_optional_string(metadata.get("model_unit")),
        conversion=dict(conversion),
        status=_optional_string(metadata.get("status")) or "ok",
    )


def _select_snapshot_fields(
    snapshot: ModelSnapshot,
    requested_fields: Sequence[ModelFieldRequest],
) -> ModelSnapshot:
    by_key = {(field.element_id, field.field_name): field for field in snapshot.fields}
    selected = []
    for element_id, field_name in requested_fields:
        key = (str(element_id), str(field_name))
        try:
            selected.append(by_key[key])
        except KeyError as exc:
            raise MachineProfileError(
                f"Saved model snapshot does not define {key[0]}.{key[1]}."
            ) from exc

    return ModelSnapshot(
        source=snapshot.source,
        machine_id=snapshot.machine_id,
        control_backend=snapshot.control_backend,
        created_at=snapshot.created_at,
        fields=tuple(selected),
        origin_source=snapshot.origin_source,
        source_path=snapshot.source_path,
    )


def _build_design_fields(
    app_context: AppContext,
    requested_fields: Sequence[ModelFieldRequest],
) -> list[ModelSnapshotField]:
    lattice = _load_design_lattice(app_context)
    specs = _snapshot_field_specs(app_context)
    defaults = _snapshot_default_specs(app_context)
    fields: list[ModelSnapshotField] = []

    for element_id, field_name in requested_fields:
        element_id = str(element_id)
        field_name = str(field_name)
        try:
            raw_value = lattice[element_id][field_name]
        except KeyError as exc:
            raise MachineProfileError(
                f"Design lattice does not define {element_id}.{field_name}."
            ) from exc
        value = _finite_float(raw_value, f"design lattice {element_id}.{field_name}")
        spec = _find_snapshot_field_spec(
            specs,
            defaults,
            element_id,
            field_name,
            required=False,
        )
        fields.append(
            ModelSnapshotField(
                element_id=element_id,
                field_name=field_name,
                value=value,
                source=MODEL_SNAPSHOT_SOURCE_DESIGN,
                source_pv=None,
                source_value=value,
                source_unit=_optional_string(spec.get("model_unit") if spec else None),
                model_unit=_optional_string(spec.get("model_unit") if spec else None),
                conversion={"type": "design"},
            )
        )
    return fields


def _build_live_fields(
    app_context: AppContext,
    requested_fields: Sequence[ModelFieldRequest],
    *,
    source: str,
    pv_reader: PvReader,
) -> list[ModelSnapshotField]:
    backend = "real" if source == MODEL_SNAPSHOT_SOURCE_LIVE_FROM_REAL else "vm"
    specs = _snapshot_field_specs(app_context)
    defaults = _snapshot_default_specs(app_context)
    fields: list[ModelSnapshotField] = []

    for element_id, field_name in requested_fields:
        element_id = str(element_id)
        field_name = str(field_name)
        spec = _find_snapshot_field_spec(
            specs,
            defaults,
            element_id,
            field_name,
            required=True,
        )
        logical_channel = _required_string(
            spec.get("logical_channel"),
            f"model snapshot {element_id}.{field_name}.logical_channel",
        )
        pv_name = resolve_channel(app_context.profile, element_id, logical_channel, backend)
        raw_value = pv_reader(pv_name)
        source_value = _finite_float(raw_value, f"PV {pv_name}")
        conversion = _conversion_mapping(spec)
        model_value = apply_snapshot_conversion(source_value, conversion)
        fields.append(
            ModelSnapshotField(
                element_id=element_id,
                field_name=field_name,
                value=model_value,
                source=source,
                source_pv=pv_name,
                source_value=source_value,
                source_unit=_optional_string(spec.get("source_unit")),
                model_unit=_optional_string(spec.get("model_unit")),
                conversion=conversion,
            )
        )
    return fields


def apply_snapshot_conversion(source_value: float, conversion: Mapping[str, Any]) -> float:
    conversion_type = str(conversion.get("type", "direct")).strip().lower().replace("-", "_")
    if conversion_type == "direct":
        return _finite_float(source_value, "direct model snapshot conversion")

    if conversion_type == "scale_offset":
        scale = _finite_float(conversion.get("scale", 1.0), "scale_offset.scale")
        offset = _finite_float(conversion.get("offset", 0.0), "scale_offset.offset")
        return _finite_float(scale * source_value + offset, "scale_offset result")

    if conversion_type == "polynomial":
        coefficients = conversion.get("coefficients")
        if not isinstance(coefficients, list) or not coefficients:
            raise MachineProfileError("polynomial conversion requires non-empty coefficients.")
        result = 0.0
        for power, coefficient in enumerate(coefficients):
            result += _finite_float(coefficient, f"polynomial.coefficients[{power}]") * (
                source_value**power
            )
        return _finite_float(result, "polynomial result")

    raise MachineProfileError(f"Unsupported model snapshot conversion type: {conversion_type!r}")


def _snapshot_field_specs(app_context: AppContext) -> Mapping[str, Any]:
    snapshot_mapping = _snapshot_mapping(app_context)
    fields = snapshot_mapping.get("fields", {})
    if not isinstance(fields, Mapping):
        raise MachineProfileError("model backend config.snapshot_mapping.fields must be a mapping.")
    return fields


def _snapshot_default_specs(app_context: AppContext) -> Mapping[str, Any]:
    snapshot_mapping = _snapshot_mapping(app_context)
    defaults = snapshot_mapping.get("defaults", {})
    if not isinstance(defaults, Mapping):
        raise MachineProfileError(
            "model backend config.snapshot_mapping.defaults must be a mapping."
        )
    return defaults


def _snapshot_mapping(app_context: AppContext) -> Mapping[str, Any]:
    if app_context.model_backend is None:
        raise MachineProfileError(
            f"AppContext for {app_context.app_name!r} does not define a model backend."
        )
    snapshot_mapping = app_context.model_backend.config.get("snapshot_mapping")
    if snapshot_mapping is None:
        snapshot_mapping = app_context.model_backend.config.get("snapshot")
    if snapshot_mapping is None:
        return {}
    if not isinstance(snapshot_mapping, Mapping):
        raise MachineProfileError("model backend config.snapshot_mapping must be a mapping.")
    return snapshot_mapping


def resolve_model_snapshot_field_spec(
    app_context: AppContext,
    element_id: str,
    field_name: str,
) -> Mapping[str, Any]:
    return _find_snapshot_field_spec(
        _snapshot_field_specs(app_context),
        _snapshot_default_specs(app_context),
        str(element_id),
        str(field_name),
        required=True,
    )


def _find_snapshot_field_spec(
    specs: Mapping[str, Any],
    defaults: Mapping[str, Any],
    element_id: str,
    field_name: str,
    *,
    required: bool,
) -> Mapping[str, Any]:
    element_specs = specs.get(element_id)
    field_spec = element_specs.get(field_name) if isinstance(element_specs, Mapping) else None
    if not isinstance(field_spec, Mapping):
        field_spec = defaults.get(field_name)
    if not isinstance(field_spec, Mapping):
        if required:
            raise MachineProfileError(
                f"model backend snapshot_mapping is missing field mapping for {element_id}.{field_name}."
            )
        return {}
    return field_spec


def _conversion_mapping(spec: Mapping[str, Any]) -> Mapping[str, Any]:
    conversion = spec.get("conversion", {"type": "direct"})
    if not isinstance(conversion, Mapping):
        raise MachineProfileError("model snapshot conversion must be a mapping.")
    if "type" not in conversion:
        return {"type": "direct", **dict(conversion)}
    return dict(conversion)


def _load_design_lattice(app_context: AppContext) -> Mapping[str, Mapping[str, str]]:
    if app_context.model_backend is None:
        raise MachineProfileError(
            f"AppContext for {app_context.app_name!r} does not define a model backend."
        )
    config = app_context.model_backend.config
    source_lattice = _required_string(config.get("source_lattice"), "model backend source_lattice")
    ele_file = _required_string(
        config.get("optics_ini_ele") or config.get("emit_ini_ele"),
        "model backend optics_ini_ele",
    )
    line_name = _required_string(config.get("line_name"), "model backend line_name")
    parser = ElegantParser(source_lattice, ele_file, line_name)
    return parser.build_runtime_state()["lattice"]


def _default_pv_reader(pv_name: str) -> Any:
    from epics import caget

    return caget(pv_name)


def _finite_float(value: Any, location: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MachineProfileError(f"{location} must be numeric, got {value!r}.") from exc
    if not math.isfinite(number):
        raise MachineProfileError(f"{location} must be finite, got {value!r}.")
    return number


def _required_string(value: Any, location: str) -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        raise MachineProfileError(f"{location} must be a non-empty string.")
    return text


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
