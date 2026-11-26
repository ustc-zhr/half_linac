# Frozen InfoBAX unit-test inputs

These 24 JSON/CSV files are byte-identical copies of the historical P2-B2–B7
inputs read by the calibration and benchmark unit tests. `manifest.json`
records their original paths and SHA-256 values. Keeping them separate makes
source checkouts independent of ignored experiment output directories.

This is not the complete experiment corpus. Full research CLI replays require
the separately preserved `benchmark-evidence.tar.gz`; unpack it at the repository
root to restore `src/optimization/infobax/benchmarks/`. Historical logs, plots,
implementation snapshots and runtime checkpoints stay in that archive.
