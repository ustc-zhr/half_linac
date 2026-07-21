from __future__ import annotations

from dataclasses import dataclass

from .models import MachineProfile, MachineProfileError
from .resolver import (
    list_elements,
    resolve_bend_write_channel,
    resolve_channel,
    resolve_corrector_write_channel,
)


@dataclass(frozen=True)
class SoftIocAlias:
    element_id: str
    kind: str
    logical_channel: str
    pv_name: str


def resolve_softioc_vm_alias(
    profile: MachineProfile,
    element_id: str,
    element_kind: str,
    logical_channel: str,
) -> str | None:
    try:
        if element_kind == "corr" and logical_channel == "kick":
            return resolve_corrector_write_channel(profile, element_id, "vm")
        if element_kind == "bend" and logical_channel == "angle":
            return resolve_bend_write_channel(profile, element_id, "vm")
        return resolve_channel(profile, element_id, logical_channel, "vm")
    except MachineProfileError:
        return None


def iter_softioc_vm_aliases(profile: MachineProfile) -> tuple[SoftIocAlias, ...]:
    aliases: list[SoftIocAlias] = []

    def add(element_id: str, kind: str, logical_channel: str) -> None:
        pv_name = resolve_softioc_vm_alias(profile, element_id, kind, logical_channel)
        if pv_name:
            aliases.append(
                SoftIocAlias(
                    element_id=element_id,
                    kind=kind,
                    logical_channel=logical_channel,
                    pv_name=pv_name,
                )
            )

    for element in list_elements(profile, kind="quad", logical_channel="k1"):
        add(element.id, element.kind, "k1")

    for element in list_elements(profile, kind="bend"):
        add(element.id, element.kind, "angle")

    for element in list_elements(profile, kind="bpm"):
        add(element.id, element.kind, "x")
        add(element.id, element.kind, "y")

    for element in list_elements(profile, kind="flag"):
        add(element.id, element.kind, "image")
        add(element.id, element.kind, "esa_image")
        add(element.id, element.kind, "sigx")
        add(element.id, element.kind, "sigy")
        add(element.id, element.kind, "exposure_time")

    for element in list_elements(profile, kind="ct"):
        add(element.id, element.kind, "charge")

    for element in list_elements(profile, kind="corr"):
        add(element.id, element.kind, "kick")

    return tuple(aliases)
