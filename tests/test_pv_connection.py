from __future__ import annotations

import pytest

from half_linac.src.shared.machine_profile import load_profile
from half_linac.src.shared.pv_connection import (
    collect_pv_endpoints,
    probe_pv_connections,
    unique_pv_names,
)


def test_collect_endpoints_covers_real_and_vm_backends() -> None:
    profile = load_profile("irfel")

    endpoints = collect_pv_endpoints(profile)

    assert {endpoint.backend for endpoint in endpoints} == {"real", "vm"}
    assert len(endpoints) > len(collect_pv_endpoints(profile, ("real",)))
    assert len(endpoints) > len(collect_pv_endpoints(profile, ("vm",)))


def test_collect_endpoints_rejects_unknown_backend() -> None:
    with pytest.raises(ValueError, match="Unknown control backend"):
        collect_pv_endpoints(load_profile("half"), ("missing",))


def test_unique_pv_names_preserves_first_occurrence() -> None:
    endpoints = collect_pv_endpoints(load_profile("half"))
    names = unique_pv_names(endpoints)

    assert names == tuple(dict.fromkeys(endpoint.pv_name for endpoint in endpoints))


def test_batch_probe_uses_one_context_and_cleans_every_channel() -> None:
    calls = []

    class FakeCA:
        @staticmethod
        def use_initial_context():
            calls.append(("attach",))

        @staticmethod
        def create_channel(pv_name, **kwargs):
            calls.append(("create", pv_name, kwargs))
            return pv_name

        @staticmethod
        def flush_io():
            calls.append(("flush",))

        @staticmethod
        def isConnected(channel):
            return channel.endswith(":OK")

        @staticmethod
        def poll(**kwargs):
            calls.append(("poll", kwargs))

        @staticmethod
        def clear_channel(channel):
            calls.append(("clear", channel))

        @staticmethod
        def detach_context():
            calls.append(("detach",))

    results = []
    cancelled = probe_pv_connections(
        ("TEST:OK", "TEST:MISSING"),
        0.001,
        on_result=results.append,
        ca_module=FakeCA,
    )

    assert not cancelled
    assert {result.pv_name: result.connected for result in results} == {
        "TEST:OK": True,
        "TEST:MISSING": False,
    }
    assert ("create", "TEST:OK", {"connect": False, "auto_cb": True}) in calls
    assert ("create", "TEST:MISSING", {"connect": False, "auto_cb": True}) in calls
    assert ("clear", "TEST:OK") in calls
    assert ("clear", "TEST:MISSING") in calls
    assert calls[-1] == ("detach",)


def test_batch_probe_can_stop_before_creating_channels() -> None:
    class FakeCA:
        use_initial_context = staticmethod(lambda: None)
        detach_context = staticmethod(lambda: None)

    results = []
    cancelled = probe_pv_connections(
        ("TEST:PV",),
        1.0,
        on_result=results.append,
        stop_requested=lambda: True,
        ca_module=FakeCA,
    )

    assert cancelled
    assert results == []
