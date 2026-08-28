import json

import pytest

from gotacc.gui.services.machine_profile import (
    MACHINE_PROFILE_SCHEMA,
    MACHINE_PROFILE_VERSION,
    MachineProfile,
    load_machine_profile,
    save_machine_profile,
)


def test_machine_profile_round_trip_preserves_owned_machine_configuration(tmp_path):
    profile = MachineProfile.create(
        "IR-FEL Commissioning",
        {
            "ca_address": "10.0.0.255",
            "mapping": [
                {"Role": "objective", "Name": "energy", "PV Name": "FEL:ENERGY"}
            ],
            "policy_bindings": [
                {
                    "kind": "objective",
                    "target": "energy",
                    "enabled": True,
                    "preset": "fel_energy_guard",
                    "policy": {"name": "sample_guard", "kwargs": {}},
                }
            ],
            "policy_presets": [],
            "write_links": [],
            "unowned_task_field": "discarded",
        },
        profile_id="irfel-commissioning",
    )
    path = save_machine_profile(profile, tmp_path / "irfel.json")

    loaded = load_machine_profile(path)

    assert loaded == profile
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema"] == MACHINE_PROFILE_SCHEMA
    assert payload["version"] == MACHINE_PROFILE_VERSION
    assert "unowned_task_field" not in payload["machine"]


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"version": 1}, "schema"),
        (
            {
                "schema": MACHINE_PROFILE_SCHEMA,
                "version": 99,
                "profile_id": "test",
                "name": "Test",
                "machine": {},
            },
            "version",
        ),
        (
            {
                "schema": MACHINE_PROFILE_SCHEMA,
                "version": 1,
                "profile_id": "",
                "name": "Test",
                "machine": {},
            },
            "ID",
        ),
    ],
)
def test_machine_profile_rejects_unversioned_or_unsupported_documents(payload, message):
    with pytest.raises(ValueError, match=message):
        MachineProfile.from_dict(payload)
