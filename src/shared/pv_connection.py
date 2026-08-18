from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Iterable

from .machine_profile.models import MachineProfile, MachineProfileError


@dataclass(frozen=True, order=True)
class PvEndpoint:
    backend: str
    element_id: str
    element_kind: str
    logical_channel: str
    pv_name: str


@dataclass(frozen=True)
class PvConnectionResult:
    pv_name: str
    connected: bool
    detail: str


def collect_pv_endpoints(
    profile: MachineProfile,
    backends: Iterable[str] | None = None,
) -> tuple[PvEndpoint, ...]:
    selected = tuple(profile.control_backends if backends is None else backends)
    unknown = sorted(set(selected) - set(profile.control_backends))
    if unknown:
        raise MachineProfileError(
            f"Unknown control backend(s) for {profile.machine.id}: {', '.join(unknown)}."
        )

    endpoints = []
    selected_set = set(selected)
    for element in profile.elements:
        for logical_channel, channel_backends in element.channels.items():
            for backend, pv_name in channel_backends.items():
                if backend not in selected_set:
                    continue
                endpoints.append(
                    PvEndpoint(
                        backend=backend,
                        element_id=element.id,
                        element_kind=element.kind,
                        logical_channel=logical_channel,
                        pv_name=pv_name,
                    )
                )
    return tuple(sorted(endpoints))


def unique_pv_names(endpoints: Iterable[PvEndpoint]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(endpoint.pv_name for endpoint in endpoints))


def probe_pv_connections(
    pv_names: Iterable[str],
    timeout_s: float,
    *,
    on_result: Callable[[PvConnectionResult], None],
    stop_requested: Callable[[], bool] | None = None,
    ca_module=None,
) -> bool:
    """Probe a PV batch in one CA context and return whether it was cancelled."""
    if timeout_s <= 0:
        raise ValueError("PV connection timeout must be greater than zero.")

    if ca_module is None:
        import epics.ca as ca_module

    names = tuple(dict.fromkeys(str(name).strip() for name in pv_names if str(name).strip()))
    should_stop = stop_requested or (lambda: False)
    channels = {}
    attached = False
    try:
        ca_module.use_initial_context()
        attached = True
        for pv_name in names:
            if should_stop():
                return True
            try:
                channels[pv_name] = ca_module.create_channel(
                    pv_name,
                    connect=False,
                    auto_cb=True,
                )
            except Exception as exc:
                on_result(
                    PvConnectionResult(
                        pv_name,
                        False,
                        f"{type(exc).__name__}: {exc}",
                    )
                )
        ca_module.flush_io()

        pending = set(channels)
        deadline = time.monotonic() + timeout_s
        while pending and not should_stop():
            connected = [
                pv_name
                for pv_name in pending
                if ca_module.isConnected(channels[pv_name])
            ]
            for pv_name in connected:
                pending.remove(pv_name)
                on_result(PvConnectionResult(pv_name, True, "Connected"))

            remaining_s = deadline - time.monotonic()
            if not pending or remaining_s <= 0:
                break
            ca_module.poll(
                evt=min(0.02, remaining_s),
                iot=min(0.05, remaining_s),
            )

        cancelled = should_stop()
        if not cancelled:
            for pv_name in pending:
                on_result(
                    PvConnectionResult(
                        pv_name,
                        False,
                        f"No connection within {timeout_s:g} s",
                    )
                )
        return cancelled
    finally:
        for channel in channels.values():
            try:
                ca_module.clear_channel(channel)
            except Exception:
                pass
        if channels:
            try:
                ca_module.flush_io()
            except Exception:
                pass
        if attached:
            try:
                ca_module.detach_context()
            except Exception:
                pass
