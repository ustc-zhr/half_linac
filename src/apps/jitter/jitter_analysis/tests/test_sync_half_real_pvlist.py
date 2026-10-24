from copy import deepcopy
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from sync_half_real_pvlist import ENERGY_SPECTRUM_CHANNELS, sync_config


def test_sync_adds_energy_spectrum_objects_idempotently():
    current = {"knobs": [], "objects": [], "presets": []}
    machine = {
        "elements": [
            {
                "id": "PRF02",
                "kind": "flag",
                "display_name": "PRF02",
                "tags": ["energy_spectrum"],
            },
            {
                "id": "ENY",
                "kind": "flag",
                "display_name": "ENY",
                "tags": ["energy_spectrum"],
            },
        ]
    }
    real_backend = {
        "channels": {
            element_id: {
                channel_key: f"IN:BD:{element_id}:{channel_key}"
                for channel_key, _label in ENERGY_SPECTRUM_CHANNELS
            }
            for element_id in ("PRF02", "ENY")
        }
    }

    synced = sync_config(
        current=deepcopy(current),
        machine=machine,
        real_backend=real_backend,
    )
    synced_twice = sync_config(
        current=deepcopy(synced),
        machine=machine,
        real_backend=real_backend,
    )

    assert len(synced["objects"]) == 10
    assert synced_twice == synced
    objects_by_id = {obj["id"]: obj for obj in synced["objects"]}
    assert objects_by_id["prf02_energy_center"]["read_pv"] == (
        "IN:BD:PRF02:energy_center"
    )
    assert objects_by_id["eny_energy_spread_fwhm"]["unit"] == "MeV"
    assert objects_by_id["eny_energy_spread_fwhm"]["analysis"] == {
        "jitter": True,
        "correlation": True,
        "spectrum": True,
    }
