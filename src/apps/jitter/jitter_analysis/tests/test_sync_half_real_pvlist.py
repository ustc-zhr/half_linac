from copy import deepcopy
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from sync_half_real_pvlist import (
    ENERGY_SPECTRUM_CHANNELS,
    sync_config,
)


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


def test_sync_adds_llrf_channel_waveforms_idempotently():
    sample_interval = 1.0 / 105_780_000.0
    current = {
        "defaults": {"waveform": {"sample_interval_sec": sample_interval}},
        "knobs": [],
        "objects": [
            {
                "id": "llrf01_ch3_amp",
                "name": "LLRF01 ACC1 input amplitude",
                "group": "rf_amplitude",
                "unit": "a.u.",
                "precision": 3,
                "tags": ["rf", "llrf", "amplitude", "ch3", "acc1_input"],
            },
            {
                "id": "llrf01_ch3_phase",
                "name": "LLRF01 ACC1 input phase",
                "group": "rf_channel_phase",
                "unit": "deg",
                "precision": 3,
                "tags": ["rf", "llrf", "phase", "ch3", "acc1_input"],
            },
            {
                "id": "llrfpb_ch2_amp",
                "name": "LLRFPB Pickup 1 amplitude",
                "group": "rf_amplitude",
                "unit": "a.u.",
                "precision": 3,
                "tags": ["rf", "llrf", "amplitude", "ch2", "pickup_1"],
            },
            {
                "id": "llrf01_ch3_power",
                "name": "LLRF01 ACC1 input power",
                "group": "rf_power",
                "unit": "MW",
                "precision": 6,
                "tags": ["rf", "llrf", "power", "ch3", "acc1_input"],
            },
        ],
        "presets": [],
    }
    machine = {"elements": []}
    real_backend = {"channels": {}}

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

    assert synced_twice == synced
    objects_by_id = {obj["id"]: obj for obj in synced["objects"]}
    amp_waveform = objects_by_id["llrf01_ch3_amp_waveform"]
    phase_waveform = objects_by_id["llrf01_ch3_phase_waveform"]
    prebuncher_waveform = objects_by_id["llrfpb_ch2_amp_waveform"]
    assert amp_waveform["read_pv"] == "IN:WM:LLRF01:CH3_WFAMP"
    assert phase_waveform["read_pv"] == "IN:WM:LLRF01:CH3_WFPHA"
    assert prebuncher_waveform["read_pv"] == "IN:WM:LLRFPB:CH2_WFAMP"
    assert amp_waveform["capture_mode"] == "waveform"
    assert synced["defaults"]["waveform"]["sample_interval_sec"] == sample_interval
    assert "waveform_sample_interval_sec" not in amp_waveform
    assert amp_waveform["analysis"] == {
        "jitter": False,
        "correlation": False,
        "spectrum": False,
    }
    assert "llrf01_ch3_power_waveform" not in objects_by_id

    overridden = deepcopy(synced)
    overridden_by_id = {obj["id"]: obj for obj in overridden["objects"]}
    overridden_by_id["llrf01_ch3_amp_waveform"][
        "waveform_sample_interval_sec"
    ] = 2.5e-9
    resynced = sync_config(
        current=overridden,
        machine=machine,
        real_backend=real_backend,
    )
    resynced_by_id = {obj["id"]: obj for obj in resynced["objects"]}
    assert (
        resynced_by_id["llrf01_ch3_amp_waveform"][
            "waveform_sample_interval_sec"
        ]
        == 2.5e-9
    )
