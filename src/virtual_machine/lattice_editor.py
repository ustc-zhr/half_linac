"""Safe, type-scoped editing for VM beam-cleaning and aperture elements."""
from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from half_linac.src.shared.runtime_state import (
    ensure_runtime_state,
    read_runtime_state,
    update_runtime_state,
)
from half_linac.src.virtual_machine.beam_source import bootstrap_runtime_state


LATTICE_EDITOR_STATE_KEY = "lattice_editor_state"
SUPPORTED_ELEMENT_TYPES = frozenset({"CLEAN", "RCOL", "ECOL", "MAXAMP"})
APERTURE_TYPES = frozenset({"RCOL", "ECOL", "MAXAMP"})
CLEAN_LIMIT_FIELDS = ("XLIMIT", "XPLIMIT", "YLIMIT", "YPLIMIT", "TLIMIT", "DELTALIMIT")


class LatticeEditorError(ValueError):
    pass


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    unit: str
    display_scale: float
    decimals: int
    maximum: float


FIELD_SPECS = {
    "X_MAX": FieldSpec("X_MAX", "Horizontal half-aperture", "mm", 1e3, 6, 1e6),
    "Y_MAX": FieldSpec("Y_MAX", "Vertical half-aperture", "mm", 1e3, 6, 1e6),
    "TLIMIT": FieldSpec("TLIMIT", "Arrival-time limit", "ps", 1e12, 6, 1e9),
}


def editable_fields(element_type: str) -> tuple[str, ...]:
    normalized = str(element_type).upper()
    if normalized == "CLEAN":
        return ("TLIMIT",)
    if normalized in APERTURE_TYPES:
        return ("X_MAX", "Y_MAX")
    return ()


def editable_lattice_elements(state: Mapping[str, Any]) -> tuple[str, ...]:
    lattice = state.get("lattice", {})
    if not isinstance(lattice, Mapping):
        return ()

    result: list[str] = []
    seen: set[str] = set()
    usedline = state.get("usedline", ())
    if isinstance(usedline, (list, tuple)):
        for raw_name in usedline:
            name = str(raw_name)
            element = lattice.get(name)
            if name not in seen and _supported_element(element):
                result.append(name)
                seen.add(name)
    for name in sorted(str(key) for key in lattice):
        if name not in seen and _supported_element(lattice.get(name)):
            result.append(name)
    return tuple(result)


def element_is_enabled(element: Mapping[str, Any]) -> bool:
    element_type = str(element.get("TYPE", "")).upper()
    if element_type == "CLEAN":
        return any(_number(element.get(field, 0), field) > 0 for field in CLEAN_LIMIT_FIELDS)
    if element_type in APERTURE_TYPES:
        return any(_number(element.get(field, 0), field) > 0 for field in ("X_MAX", "Y_MAX"))
    raise LatticeEditorError(f"Unsupported lattice element type: {element_type or '--'}")


def displayed_element_values(state: Mapping[str, Any], element_id: str) -> dict[str, float]:
    element = _require_supported_element(state, element_id)
    fields = editable_fields(element["TYPE"])
    saved = _saved_values(state, element_id)
    values: dict[str, float] = {}
    for field in fields:
        raw = saved.get(field, element.get(field, 0)) if not element_is_enabled(element) else element.get(field, 0)
        values[field] = _number(raw, f"{element_id}.{field}")
    if str(element["TYPE"]).upper() == "MAXAMP":
        raw = saved.get("ELLIPTICAL", element.get("ELLIPTICAL", 0))
        values["ELLIPTICAL"] = 1.0 if _as_bool(raw) else 0.0
    return values


def apply_lattice_element_settings(
    runtime,
    element_id: str,
    values: Mapping[str, Any],
    *,
    enabled: bool,
) -> dict[str, Any]:
    ensure_runtime_state(runtime.vm.runtime_json, lambda: bootstrap_runtime_state(runtime))
    normalized_values: dict[str, float] = {}

    def apply(state: dict[str, Any]) -> bool:
        nonlocal normalized_values
        element = _require_supported_element(state, element_id)
        element_type = str(element["TYPE"]).upper()
        normalized_values = _normalize_values(element_id, element_type, values, enabled=enabled)
        editor_state = state.setdefault(LATTICE_EDITOR_STATE_KEY, {})
        if not isinstance(editor_state, dict):
            editor_state = state[LATTICE_EDITOR_STATE_KEY] = {}
        editor_state[element_id] = {
            "type": element_type,
            "enabled": bool(enabled),
            "values": {key: _format_number(value) for key, value in normalized_values.items()},
        }

        if enabled:
            for field in editable_fields(element_type):
                element[field] = _format_number(normalized_values[field])
        else:
            for field in _disabled_fields(element_type):
                element[field] = "0"
        if element_type == "MAXAMP":
            element["ELLIPTICAL"] = str(int(normalized_values.get("ELLIPTICAL", 0)))
        return True

    state, _ = update_runtime_state(runtime.vm.runtime_json, apply)
    return state


def reset_lattice_element_to_design(runtime, element_id: str) -> dict[str, Any]:
    design_state = bootstrap_runtime_state(runtime)
    design_element = copy.deepcopy(_require_supported_element(design_state, element_id))
    ensure_runtime_state(runtime.vm.runtime_json, lambda: bootstrap_runtime_state(runtime))

    def reset(state: dict[str, Any]) -> bool:
        element = _require_supported_element(state, element_id)
        if str(element["TYPE"]).upper() != str(design_element["TYPE"]).upper():
            raise LatticeEditorError(f"Design type mismatch for {element_id}.")
        for field in (*editable_fields(element["TYPE"]), "ELLIPTICAL"):
            if field in design_element:
                element[field] = str(design_element[field])
            elif field in element:
                element.pop(field)
        editor_state = state.get(LATTICE_EDITOR_STATE_KEY)
        if isinstance(editor_state, dict):
            editor_state.pop(element_id, None)
            if not editor_state:
                state.pop(LATTICE_EDITOR_STATE_KEY, None)
        return True

    state, _ = update_runtime_state(runtime.vm.runtime_json, reset)
    return state


class LatticeElementEditor(QGroupBox):
    changed = pyqtSignal(str)

    def __init__(self, runtime, parent: QWidget | None = None):
        super().__init__("Beam Cleaning & Apertures", parent)
        self.runtime = runtime
        self.design_state = bootstrap_runtime_state(runtime)
        self.runtime_state: dict[str, Any] | None = None
        self._editable = True
        self._build_ui()
        try:
            state = read_runtime_state(runtime.vm.runtime_json)
        except (FileNotFoundError, ValueError):
            state = self.design_state
        self.set_runtime_state(state)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 12, 8, 8)
        layout.setSpacing(7)

        self.note = QLabel(
            "Only CLEAN, RCOL, ECOL and MAXAMP safety-scoped fields are editable. "
            "Zero limits disable the selected element in Elegant."
        )
        self.note.setWordWrap(True)
        self.note.setProperty("role", "field")
        layout.addWidget(self.note)

        self.element_combo = QComboBox(self)
        self.element_combo.setObjectName("latticeElementEditorChoice")
        self.element_combo.currentIndexChanged.connect(self._load_selection)
        layout.addWidget(self.element_combo)

        self.selection_summary = QLabel("No supported elements")
        self.selection_summary.setWordWrap(True)
        self.selection_summary.setProperty("role", "field")
        layout.addWidget(self.selection_summary)

        self.enabled_check = QCheckBox("Enabled", self)
        self.enabled_check.setObjectName("latticeElementEnabled")
        self.enabled_check.toggled.connect(self._refresh_controls)
        layout.addWidget(self.enabled_check)

        self.fields_widget = QWidget(self)
        fields_layout = QGridLayout(self.fields_widget)
        fields_layout.setContentsMargins(0, 0, 0, 0)
        fields_layout.setHorizontalSpacing(8)
        fields_layout.addWidget(QLabel("Parameter"), 0, 0)
        fields_layout.addWidget(QLabel("Runtime value"), 0, 1)
        fields_layout.addWidget(QLabel("Design"), 0, 2)
        self.field_rows: dict[str, tuple[QLabel, QDoubleSpinBox, QLabel]] = {}
        for row, spec in enumerate(FIELD_SPECS.values(), start=1):
            label = QLabel(spec.label, self.fields_widget)
            spin = QDoubleSpinBox(self.fields_widget)
            spin.setObjectName(f"latticeEditor{spec.key}")
            spin.setDecimals(spec.decimals)
            spin.setRange(0, spec.maximum)
            spin.setSingleStep(1 if spec.unit in {"mm", "ps"} else 0.1)
            spin.setSuffix(f" {spec.unit}")
            design = QLabel("--", self.fields_widget)
            design.setProperty("role", "field")
            fields_layout.addWidget(label, row, 0)
            fields_layout.addWidget(spin, row, 1)
            fields_layout.addWidget(design, row, 2)
            self.field_rows[spec.key] = (label, spin, design)
        self.elliptical_check = QCheckBox("Elliptical aperture", self.fields_widget)
        self.elliptical_check.setObjectName("latticeEditorElliptical")
        fields_layout.addWidget(self.elliptical_check, len(FIELD_SPECS) + 1, 0, 1, 3)
        fields_layout.setColumnStretch(1, 1)
        layout.addWidget(self.fields_widget)

        actions = QHBoxLayout()
        self.reset_button = QPushButton("Reset to Design", self)
        self.reset_button.setObjectName("resetLatticeElement")
        self.reset_button.clicked.connect(self._reset_design)
        self.apply_button = QPushButton("Apply Element", self)
        self.apply_button.setObjectName("applyLatticeElement")
        self.apply_button.clicked.connect(self._apply)
        actions.addWidget(self.reset_button)
        actions.addWidget(self.apply_button)
        layout.addLayout(actions)

        self.status = QLabel("", self)
        self.status.setWordWrap(True)
        self.status.setProperty("role", "field")
        layout.addWidget(self.status)

    def set_runtime_state(self, state: Mapping[str, Any]) -> None:
        self.runtime_state = copy.deepcopy(dict(state))
        selected = self.element_combo.currentData()
        choices = editable_lattice_elements(state)
        current_choices = tuple(self.element_combo.itemData(i) for i in range(self.element_combo.count()))
        if current_choices != choices:
            self.element_combo.blockSignals(True)
            self.element_combo.clear()
            lattice = state.get("lattice", {})
            for element_id in choices:
                element_type = str(lattice[element_id].get("TYPE", "")).upper()
                self.element_combo.addItem(f"{element_id} · {element_type}", element_id)
            index = self.element_combo.findData(selected)
            self.element_combo.setCurrentIndex(index if index >= 0 else (0 if choices else -1))
            self.element_combo.blockSignals(False)
        self._load_selection()

    def set_editor_enabled(self, enabled: bool) -> None:
        self._editable = bool(enabled)
        self._refresh_controls()

    def _selected(self) -> tuple[str, Mapping[str, Any]] | None:
        if self.runtime_state is None:
            return None
        element_id = self.element_combo.currentData()
        lattice = self.runtime_state.get("lattice", {})
        element = lattice.get(element_id) if isinstance(lattice, Mapping) else None
        if not element_id or not isinstance(element, Mapping):
            return None
        return str(element_id), element

    def _load_selection(self, _index: int | None = None) -> None:
        selected = self._selected()
        if selected is None:
            self.selection_summary.setText("No supported elements")
            self.fields_widget.setEnabled(False)
            self._refresh_controls()
            return
        element_id, element = selected
        element_type = str(element.get("TYPE", "")).upper()
        usedline = self.runtime_state.get("usedline", ()) if self.runtime_state else ()
        location = "in current usedline" if element_id in usedline else "outside current usedline"
        self.selection_summary.setText(f"{element_id} · {element_type} · {location}")
        fields = set(editable_fields(element_type))
        runtime_values = displayed_element_values(self.runtime_state, element_id)
        design_element = self.design_state.get("lattice", {}).get(element_id, {})
        for key, (label, spin, design_label) in self.field_rows.items():
            visible = key in fields
            for widget in (label, spin, design_label):
                widget.setVisible(visible)
            if not visible:
                continue
            spec = FIELD_SPECS[key]
            spin.setValue(runtime_values[key] * spec.display_scale)
            try:
                design_value = _number(design_element.get(key, 0), f"design {element_id}.{key}")
            except LatticeEditorError:
                design_label.setText("--")
            else:
                design_label.setText(f"{design_value * spec.display_scale:.6g} {spec.unit}")
        is_maxamp = element_type == "MAXAMP"
        self.elliptical_check.setVisible(is_maxamp)
        if is_maxamp:
            self.elliptical_check.setChecked(bool(runtime_values.get("ELLIPTICAL", 0)))
        self.enabled_check.blockSignals(True)
        self.enabled_check.setChecked(element_is_enabled(element))
        self.enabled_check.blockSignals(False)
        self.status.setText("")
        self._refresh_controls()

    def _refresh_controls(self) -> None:
        selected = self._selected() is not None
        active = selected and self._editable
        self.enabled_check.setEnabled(active)
        self.fields_widget.setEnabled(active)
        self.reset_button.setEnabled(active)
        self.apply_button.setEnabled(active)
        for _label, spin, _design in self.field_rows.values():
            spin.setEnabled(active and self.enabled_check.isChecked())
        self.elliptical_check.setEnabled(active and self.enabled_check.isChecked())

    def _values(self) -> dict[str, Any]:
        selected = self._selected()
        if selected is None:
            return {}
        _element_id, element = selected
        values: dict[str, Any] = {}
        for key in editable_fields(element["TYPE"]):
            spec = FIELD_SPECS[key]
            values[key] = self.field_rows[key][1].value() / spec.display_scale
        if str(element["TYPE"]).upper() == "MAXAMP":
            values["ELLIPTICAL"] = int(self.elliptical_check.isChecked())
        return values

    def _apply(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        element_id, _element = selected
        try:
            state = apply_lattice_element_settings(
                self.runtime,
                element_id,
                self._values(),
                enabled=self.enabled_check.isChecked(),
            )
        except Exception as exc:
            self.status.setText(f"Not applied: {exc}")
            QMessageBox.warning(self, "Lattice Element", str(exc))
            return
        self.set_runtime_state(state)
        message = f"Applied {element_id}; VM recalculation requested."
        self.status.setText(message)
        self.changed.emit(message)

    def _reset_design(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        element_id, _element = selected
        try:
            state = reset_lattice_element_to_design(self.runtime, element_id)
        except Exception as exc:
            self.status.setText(f"Not reset: {exc}")
            QMessageBox.warning(self, "Lattice Element", str(exc))
            return
        self.set_runtime_state(state)
        message = f"Restored {element_id} to design values; VM recalculation requested."
        self.status.setText(message)
        self.changed.emit(message)


def _supported_element(element: Any) -> bool:
    return isinstance(element, Mapping) and str(element.get("TYPE", "")).upper() in SUPPORTED_ELEMENT_TYPES


def _require_supported_element(state: Mapping[str, Any], element_id: str) -> dict[str, Any]:
    lattice = state.get("lattice")
    if not isinstance(lattice, Mapping):
        raise LatticeEditorError("VM runtime state does not contain a lattice mapping.")
    element = lattice.get(element_id)
    if not isinstance(element, dict):
        raise LatticeEditorError(f"Lattice element {element_id!r} is not available.")
    element_type = str(element.get("TYPE", "")).upper()
    if element_type not in SUPPORTED_ELEMENT_TYPES:
        raise LatticeEditorError(
            f"{element_id} has unsupported type {element_type or '--'}; "
            "only CLEAN, RCOL, ECOL and MAXAMP are editable."
        )
    return element


def _normalize_values(
    element_id: str,
    element_type: str,
    values: Mapping[str, Any],
    *,
    enabled: bool,
) -> dict[str, float]:
    fields = editable_fields(element_type)
    normalized = {field: _number(values.get(field), f"{element_id}.{field}") for field in fields}
    if enabled and any(normalized[field] <= 0 for field in fields):
        raise LatticeEditorError(f"Enabled {element_id} limits must be greater than zero.")
    if any(normalized[field] < 0 for field in fields):
        raise LatticeEditorError(f"{element_id} limits must not be negative.")
    if element_type == "MAXAMP":
        normalized["ELLIPTICAL"] = 1.0 if _as_bool(values.get("ELLIPTICAL", 0)) else 0.0
    return normalized


def _disabled_fields(element_type: str) -> tuple[str, ...]:
    return CLEAN_LIMIT_FIELDS if element_type == "CLEAN" else ("X_MAX", "Y_MAX")


def _saved_values(state: Mapping[str, Any], element_id: str) -> Mapping[str, Any]:
    editor_state = state.get(LATTICE_EDITOR_STATE_KEY, {})
    if not isinstance(editor_state, Mapping):
        return {}
    saved = editor_state.get(element_id, {})
    if not isinstance(saved, Mapping):
        return {}
    values = saved.get("values", {})
    return values if isinstance(values, Mapping) else {}


def _number(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise LatticeEditorError(f"{name} must be a number.") from exc
    if not math.isfinite(number):
        raise LatticeEditorError(f"{name} must be finite.")
    return number


def _format_number(value: float) -> str:
    return f"{float(value):.12g}"


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"", "0", "0.0", "false", "no", "off"}


__all__ = [
    "APERTURE_TYPES",
    "LATTICE_EDITOR_STATE_KEY",
    "LatticeEditorError",
    "LatticeElementEditor",
    "SUPPORTED_ELEMENT_TYPES",
    "apply_lattice_element_settings",
    "displayed_element_values",
    "editable_fields",
    "editable_lattice_elements",
    "element_is_enabled",
    "reset_lattice_element_to_design",
]
