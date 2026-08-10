# Jitter Analysis

EPICS online acquisition and analysis GUI for IRFEL jitter studies. The package provides:

- timed multi-PV acquisition with HDF5 storage
- single-knob and random multi-knob scan workflows
- jitter, correlation, spectrum, sensitivity, and waveform analysis views
- JSON-based PV library configuration
- offline loading of saved runs

## Repository Layout

```text
configs/              PV library examples and documentation
docs/                 design notes
src/jitter_analysis/  application package
tests/                unit tests
runs/README.md        local run notes; raw run data is ignored by Git
```

The `runs/` directory can contain very large `raw.h5` files and is intentionally ignored by Git. Keep production run archives outside the repository or in a separate data store.

## Environment

Recommended conda setup:

```bash
conda env create -f environment.yml
conda activate jitter-analysis
python -m pip install -e ".[dev]"
```

If you are using the existing local environment mentioned during review:

```bash
/home/zhanghaoran/anaconda3/envs/half/bin/python -m pip install -e ".[dev]"
```

## Run The GUI

```bash
jitter-analysis
```

or directly:

```bash
python main.py
```

The GUI auto-loads `configs/irfel_pvlist.json` when present. You can load another PV library from the interface.

## Run Tests

```bash
python -m pytest -q
```

Some runtime features require access to EPICS and a working Qt desktop session. The unit tests use fakes for the EPICS client where possible.

## Configuration Notes

PV libraries use schema version `2.0`. See [configs/README.md](configs/README.md) for the full JSON structure.

Important details:

- `shot_interval_sec` is the current sampling interval field.
- waveform objects require `capture_mode: "waveform"` and a positive `waveform_sample_interval_sec`.
- waveform capture is currently available in Monitor mode only.
- knob readback PVs are automatically exposed as derived read PV objects when they are not already present.
- jitter RMS is computed from mean-centered samples, not from the raw DC-offset signal.

## Data Safety

Single-knob and random multi-knob scans can write EPICS PVs. Configure conservative `limits` for every knob and keep `defaults.safety.confirm_before_write` enabled for operator-facing use.
