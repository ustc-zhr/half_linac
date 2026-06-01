from __future__ import annotations

import json
from pathlib import Path

from .models import (
    AcquisitionDefaults,
    AnalysisFlags,
    DefaultsSpec,
    GroupSpec,
    KnobSpec,
    LimitSpec,
    MachineSpec,
    ObjectSpec,
    PresetSpec,
    PvListConfig,
    SafetyDefaults,
    ScanDefaults,
    SettleSpec,
    StorageDefaults,
)
from .validator import validate_config_dict


def load_config(path: str | Path) -> PvListConfig:
    source = Path(path)
    data = json.loads(source.read_text(encoding="utf-8"))
    validated = validate_config_dict(data)
    return _build_config(validated, source)


def _build_config(data: dict, source: Path) -> PvListConfig:
    machine = MachineSpec(**data["machine"])

    defaults = DefaultsSpec(
        acquisition=AcquisitionDefaults(**data["defaults"]["acquisition"]),
        scan=ScanDefaults(**data["defaults"]["scan"]),
        storage=StorageDefaults(**data["defaults"]["storage"]),
        safety=SafetyDefaults(**data["defaults"]["safety"]),
    )

    groups = [GroupSpec(**item) for item in data["groups"]]

    knobs = [
        KnobSpec(
            id=item["id"],
            name=item["name"],
            group=item["group"],
            write_pv=item["write_pv"],
            readback_pv=item["readback_pv"],
            unit=item["unit"],
            access=item["access"],
            limits=LimitSpec(**item["limits"]),
            step_hint=item["step_hint"],
            settle=SettleSpec(**item["settle"]),
            tags=list(item.get("tags", [])),
            note=item.get("note", ""),
        )
        for item in data["knobs"]
    ]

    objects = [
        ObjectSpec(
            id=item["id"],
            name=item["name"],
            group=item["group"],
            read_pv=item["read_pv"],
            unit=item["unit"],
            precision=item["precision"],
            kind=item["kind"],
            access=item["access"],
            analysis=AnalysisFlags(**item["analysis"]),
            tags=list(item.get("tags", [])),
            note=item.get("note", ""),
        )
        for item in data["objects"]
    ]
    objects.extend(_derive_knob_readback_objects(knobs, objects))

    presets = [
        PresetSpec(
            id=item["id"],
            name=item["name"],
            mode=item["mode"],
            targets=list(item.get("targets", [])),
            knob_id=item.get("knob_id"),
            sample_interval_sec=item.get("sample_interval_sec"),
            sample_count=item.get("sample_count"),
            settle_delay_sec=item.get("settle_delay_sec"),
            sample_count_per_step=item.get("sample_count_per_step"),
            scan_values=list(item.get("scan_values", [])),
        )
        for item in data["presets"]
    ]

    return PvListConfig(
        schema_version=data["schema_version"],
        machine=machine,
        defaults=defaults,
        groups=groups,
        knobs=knobs,
        objects=objects,
        presets=presets,
        source_path=str(source.resolve()),
    )


def _derive_knob_readback_objects(knobs: list[KnobSpec], existing_objects: list[ObjectSpec]) -> list[ObjectSpec]:
    existing_ids = {obj.id for obj in existing_objects}
    existing_read_pvs = {obj.read_pv for obj in existing_objects}
    derived_objects: list[ObjectSpec] = []

    for knob in knobs:
        readback_pv = str(knob.readback_pv).strip()
        if not readback_pv or readback_pv in existing_read_pvs:
            continue

        derived_id = f"{knob.id}__readback"
        suffix = 2
        while derived_id in existing_ids:
            derived_id = f"{knob.id}__readback_{suffix}"
            suffix += 1

        tags = list(dict.fromkeys([*knob.tags, "readback", "knob_readback"]))
        derived_objects.append(
            ObjectSpec(
                id=derived_id,
                name=f"{knob.name} Readback",
                group=knob.group,
                read_pv=readback_pv,
                unit=knob.unit,
                precision=6,
                kind="scalar",
                access="ro",
                analysis=AnalysisFlags(jitter=True, correlation=True, spectrum=True),
                tags=tags,
                note=f"Derived read PV from control PV {knob.name} readback.",
            )
        )
        existing_ids.add(derived_id)
        existing_read_pvs.add(readback_pv)

    return derived_objects
