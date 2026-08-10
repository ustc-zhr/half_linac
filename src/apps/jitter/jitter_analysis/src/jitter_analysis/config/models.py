from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class MachineSpec:
    name: str
    facility: str
    description: str


@dataclass(slots=True)
class AcquisitionDefaults:
    shot_interval_sec: float
    sample_count: int
    timeout_sec: float
    mode: str = "poll"


@dataclass(slots=True)
class ScanDefaults:
    settle_mode: str
    settle_delay_sec: float
    sample_count_per_step: int
    restore_initial_value: bool
    max_wait_sec: float


@dataclass(slots=True)
class StorageDefaults:
    format: str
    save_raw_data: bool
    save_analysis_summary: bool


@dataclass(slots=True)
class SafetyDefaults:
    confirm_before_write: bool
    abort_on_disconnection: bool


@dataclass(slots=True)
class DefaultsSpec:
    acquisition: AcquisitionDefaults
    scan: ScanDefaults
    storage: StorageDefaults
    safety: SafetyDefaults


@dataclass(slots=True)
class GroupSpec:
    id: str
    label: str
    kind: str
    color: str
    order: int


@dataclass(slots=True)
class LimitSpec:
    low: float
    high: float


@dataclass(slots=True)
class SettleSpec:
    mode: str
    delay_sec: float
    readback_tolerance: float
    max_wait_sec: float


@dataclass(slots=True)
class AnalysisFlags:
    jitter: bool = True
    correlation: bool = True
    spectrum: bool = True


@dataclass(slots=True)
class KnobSpec:
    id: str
    name: str
    group: str
    write_pv: str
    readback_pv: str
    unit: str
    access: str
    limits: LimitSpec
    step_hint: float
    settle: SettleSpec
    tags: list[str] = field(default_factory=list)
    note: str = ""


@dataclass(slots=True)
class ObjectSpec:
    id: str
    name: str
    group: str
    read_pv: str
    unit: str
    precision: int
    kind: str
    access: str
    analysis: AnalysisFlags
    value_reducer: str = "none"
    capture_mode: str = "scalar"
    waveform_sample_interval_sec: float | None = None
    tags: list[str] = field(default_factory=list)
    note: str = ""


@dataclass(slots=True)
class PresetSpec:
    id: str
    name: str
    mode: str
    targets: list[str]
    knob_id: str | None = None
    shot_interval_sec: float | None = None
    sample_count: int | None = None
    settle_delay_sec: float | None = None
    sample_count_per_step: int | None = None
    scan_values: list[float] = field(default_factory=list)


@dataclass(slots=True)
class PvListConfig:
    schema_version: str
    machine: MachineSpec
    defaults: DefaultsSpec
    groups: list[GroupSpec]
    knobs: list[KnobSpec]
    objects: list[ObjectSpec]
    presets: list[PresetSpec]
    source_path: str | None = None
    source_text: str = ""
