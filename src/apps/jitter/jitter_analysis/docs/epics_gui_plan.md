# EPICS Online Acquisition GUI Plan

## 1. Design Target

This tool is aimed at control-room online acquisition and light analysis, with EPICS as the device interface. The first implementation should optimize for three things:

1. Stable online sampling of read-only objects.
2. Safe knob scan with configurable settling logic.
3. Fast inspection of jitter, correlation, and spectrum without mixing analysis code into the GUI layer.

## 2. Notes On The Current `irfel_pvlist.json`

The current file already has the right split between `knobs` and `objectives`, but it is still missing the fields needed for a reliable operator-facing GUI:

- No schema version, so future format upgrades will be fragile.
- No explicit units, precision, or display hints.
- No write limits or safety settings for knobs.
- No default sampling parameters or preset scan tasks.
- No stable group identifiers.
  The current file mixes `steering-y` and `steering-Y`, which will create UI and filtering bugs.

## 3. Recommended `pvlist.json` Structure

Recommended top-level layout:

```json
{
  "schema_version": "2.0",
  "machine": { "...": "..." },
  "defaults": { "...": "..." },
  "groups": [ "..."],
  "knobs": [ "..."],
  "objects": [ "..."],
  "presets": [ "..."]
}
```

### 3.1 Top-Level Fields

- `schema_version`
  Used by the loader to validate and migrate config files.
- `machine`
  Machine name, facility, description, and optional operator notes.
- `defaults`
  Default acquisition, scan, storage, and safety behavior.
- `groups`
  Central group registry for UI filters, colors, ordering, and stable IDs.
- `knobs`
  Writable EPICS channels used for scans or controlled studies.
- `objects`
  Read-only EPICS channels sampled during acquisition or scans.
- `presets`
  Saved tasks for fast operator reuse.

### 3.2 Group Design

All `group` references should use stable snake_case IDs such as `steering_x`, `bpm_x`, `ct`, `radiation`.

Suggested group fields:

- `id`: machine-readable unique ID
- `label`: display text in GUI
- `kind`: `knob` or `object`
- `color`: plot color hint
- `order`: display order

### 3.3 Knob Entry Design

Suggested knob fields:

- `id`: stable internal ID
- `name`: short display name
- `group`: group ID
- `write_pv`: EPICS output PV
- `readback_pv`: EPICS readback PV
- `unit`: engineering unit
- `access`: normally `rw`
- `limits.low` and `limits.high`: software safety window
- `step_hint`: suggested scan step for GUI
- `settle.mode`: `fixed_delay` or `readback_tolerance`
- `settle.delay_sec`: minimum wait after `caput`
- `settle.readback_tolerance`: acceptable write/readback delta
- `settle.max_wait_sec`: hard wait timeout
- `tags`: optional search tags
- `note`: optional operator note

### 3.4 Object Entry Design

Suggested object fields:

- `id`: stable internal ID
- `name`: display name
- `group`: group ID
- `read_pv`: EPICS input PV
- `unit`: engineering unit
- `precision`: display precision
- `kind`: `scalar` for first version
- `access`: normally `ro`
- `analysis.jitter`: allow jitter statistics
- `analysis.correlation`: allow correlation view
- `analysis.spectrum`: allow FFT/PSD view
- `tags`: optional search tags
- `note`: optional operator note

### 3.5 Defaults And Presets

Keep operational defaults in the config file instead of hardcoding them in the GUI:

- `defaults.acquisition.shot_interval_sec`
- `defaults.acquisition.sample_count`
- `defaults.acquisition.timeout_sec`
- `defaults.scan.settle_mode`
- `defaults.scan.settle_delay_sec`
- `defaults.scan.sample_count_per_step`
- `defaults.scan.restore_initial_value`
- `defaults.storage.format`

Use `presets` for operator workflows, for example:

- BPM fast jitter check
- FEL energy correlation watch
- HC01 orbit response scan

## 4. Example Config

An example file is added at [configs/irfel_pvlist_v2.example.json](/home/zhanghaoran/gitproj/jitter_analysis/configs/irfel_pvlist_v2.example.json:1).

This example keeps your current `knobs/objectives` idea but makes three structural changes:

1. Rename `objectives` to `objects`.
2. Replace `pv_name` and `readback` with explicit `write_pv`, `readback_pv`, and `read_pv`.
3. Add defaults, groups, limits, settle rules, and presets.

## 5. GUI Layout

Recommended main window layout:

```text
+----------------------------------------------------------------------------------+
| Toolbar: Config | Connect EPICS | Start | Stop | Save Dir | Preset | Run ID     |
+---------------------------+--------------------------------------+---------------+
| Left Control Panel        | Center Plot / Analysis Area          | Right Status  |
|                           |                                      |               |
| [Config Tab]              | [Realtime Trend]                     | Connection    |
| - config file             | - multi-channel live plot            | - CA status   |
| - save directory          | - cursor / zoom                      | - IOC errors  |
| - run metadata            |                                      |               |
|                           | [Analysis Tabs]                      | Task Status   |
| [Objects Tab]             | - Jitter stats table                 | - mode        |
| - search/filter           | - Correlation scatter / heatmap      | - interval    |
| - group tree              | - Spectrum PSD/FFT                   | - sample idx  |
| - selected objects        |                                      | - step idx    |
|                           | [Knob Response Plot]                 |               |
| [Knob Scan Tab]           | - knob value vs object response      | Selected PV   |
| - knob picker             |                                      | - current val |
| - scan range / list       |                                      | - unit        |
| - settle settings         |                                      | - timestamp   |
| - samples per step        |                                      |               |
+---------------------------+--------------------------------------+---------------+
| Bottom Log: warnings, caput results, timeout, disconnected PVs                   |
+----------------------------------------------------------------------------------+
```

### 5.1 Page Breakdown

- Toolbar
  Main task actions only: load config, connect, start, stop, load preset, export run.
- Left control panel
  Task definition area. This should be the only place where operators change scan setup.
- Center plot area
  Main workspace for live trend, scan response, and analysis results.
- Right status panel
  Read-only operational visibility: connection state, run progress, current PV value, alarms.
- Bottom log
  Timestamped event log for operator trust and postmortem review.

### 5.2 Recommended Tabs

- `Objects`
  Search PVs, filter by group, select sample targets, inspect units and precision.
- `Timed Acquisition`
  Set interval, point count, trigger mode, optional auto-save.
- `Knob Scan`
  Choose one knob, define point list or start/stop/step, set settling method, enable restore-after-scan.
- `Analysis`
  View jitter statistics, correlation, and spectrum on the latest run or selected window.
- `History`
  Reopen previous runs and rerender plots without touching the machine.

## 6. Python Project Skeleton

Recommended repository layout:

```text
jitter_analysis/
  configs/
    irfel_pvlist_v2.example.json
  docs/
    epics_gui_plan.md
  src/
    jitter_analysis/
      __init__.py
      app.py
      bootstrap.py
      config/
        __init__.py
        loader.py
        models.py
        validator.py
      epics/
        __init__.py
        client.py
        monitor.py
      acquisition/
        __init__.py
        plans.py
        sampler.py
        scan_executor.py
        workers.py
      analysis/
        __init__.py
        jitter.py
        correlation.py
        spectrum.py
      storage/
        __init__.py
        run_store.py
        serializers.py
      gui/
        __init__.py
        main_window.py
        state.py
        widgets/
          config_panel.py
          object_panel.py
          scan_panel.py
          status_panel.py
        plots/
          trend_plot.py
          response_plot.py
          spectrum_plot.py
      services/
        __init__.py
        run_service.py
        task_service.py
      domain/
        __init__.py
        types.py
        events.py
  tests/
    test_config_loader.py
    test_scan_executor.py
    test_jitter_analysis.py
```

### 6.1 Module Responsibilities

- `config`
  Load, validate, and normalize `pvlist.json`.
- `epics`
  Wrap `pyepics` access behind a clean interface.
  First version should isolate `caget`, `caput`, connection check, and optional monitors.
- `acquisition`
  Own task planning and execution.
  This layer should know how to run timed acquisition and knob scans, but not how to draw widgets.
- `analysis`
  Pure numeric functions for statistics, correlation, FFT, PSD.
- `storage`
  Save raw samples, run metadata, analysis summaries, and logs.
- `gui`
  Qt widgets, plots, signals, task forms, and session state display.
- `services`
  Glue layer between GUI and backend tasks.
- `domain`
  Dataclasses and enums shared across modules.

### 6.2 Recommended Data Models

At minimum, define these models early:

- `KnobSpec`
- `ObjectSpec`
- `GroupSpec`
- `TimedAcquisitionPlan`
- `KnobScanPlan`
- `SampleRecord`
- `ScanStepRecord`
- `RunMetadata`
- `RunResult`

## 7. Implementation Recommendation

### 7.1 Technical Stack

- GUI: `PyQt5`
- Live plots: `pyqtgraph`
- EPICS CA: `pyepics`
- Data analysis: `numpy`, `scipy`, `pandas`
- Storage: `parquet` or `hdf5`

### 7.2 First Deliverable

The first usable version should only include:

1. Load and validate `pvlist.json`.
2. Connect to EPICS and read selected objects.
3. Timed acquisition with fixed interval and sample count.
4. Live trend plot.
5. Save run data and metadata.
6. Jitter statistics on the acquired run.

Do not put knob writing in the first checkpoint unless the read path is already stable and the run data format is fixed.

### 7.3 Second Deliverable

After first-version stability:

1. Add knob scan with limits and restore-after-scan.
2. Add correlation matrix and scatter view.
3. Add FFT/PSD spectrum analysis.
4. Add run history viewer.

## 8. EPICS-Specific Notes

- Always separate `write_pv` from `readback_pv`.
  Do not assume `caput` success means hardware reached the requested value.
- Keep actual timestamps for every sample.
  Do not reconstruct timestamps from nominal interval during analysis.
- Surface connection state and timeout count in the GUI.
- Log every knob write with requested value, readback value, and wait result.
- Allow future upgrade from polling to monitors, but start with polling for deterministic scan logic.
