# Offline InfoBAX kernel (P2-B0)

This package is the production boundary for the finite-pool M0 InfoBAX
baseline. It is a pure numerical component: importing or using it does not
start EPICS, a GUI, GOTAcc, the VM, or any machine operation.

## Lifecycle

```python
from half_linac.src.optimization.infobax import (
    FinitePoolSpec, InfoBAXConfig, InfoBAXCore, PointObservation,
)

core = InfoBAXCore(pool, InfoBAXConfig(seed=0))
query = core.propose()
core.observe(PointObservation(query.query_index, variance_mm2, noise_std_mm2))
result = core.recommend()
checkpoint = core.to_checkpoint()
```

Queries are candidate-major: `candidate_index * scan_count + scan_index`.
Candidate and scan model coordinates are normalized to `[0, 1]`; physical
scan setpoints remain available in `scan_values`. The design matrix uses the
repository beam-matrix convention and maps three scaled beam moments to
beam-size variance in mm². Observations are single-point variances in mm².

The first six default queries are a deterministic farthest-point design.
Subsequent queries maximize raw InfoBAX EIG using 24 stratified-normal
fantasies and 512 joint posterior samples. Negative EIG is preserved and
reported as `degraded_negative_eig`. Recommendations use 1024 joint samples
and the frozen `best_valid` policy; if the explicit INVALID probability is at
least 0.5, no candidate is recommended.

Checkpoints contain only schema/version identifiers, the pool fingerprint,
configuration, and ordered observations. Derived posterior matrices are
recomputed after restore.

## Evidence and boundary

`tests/fixtures/infobax_m0_golden.json` was generated from the frozen research
implementation at commit `7c67b0a8a14e5fba167a679d56ed6ab8447edc70`.
Production tests compare query selection, EIG diagnostics, and recommendation
probabilities without importing `research/` at runtime.

On 2026-11-13, scoring the 57 unobserved queries in the research 7×9 pool took
0.595 s in the local HALF Python environment, below the 2 s reporting target.
Timing is intentionally not a cross-machine CI gate.

## HALF VM session integration (P2-B1)

`src/apps/emit_measure/infobax_session.py` adds a recoverable orchestration
layer around this numerical core. It records one candidate/K1/image result per
query, writes an atomic `infobax_session.json` checkpoint after every committed
observation, restores the pre-run machine state on every terminal path, and
refuses automatic execution when the core reports `degraded_negative_eig`.
Paused sessions can be loaded with `InfoBAXSession.from_run_dir(...)`; finite
pool and machine/workpoint fingerprints must still match, and resume is refused
if the restored VM state has drifted.

The same session object can also continue with `run()` after a pause or a
recoverable failure; every continuation verifies the restored snapshot before
writing. The session consumes its own pause request. A caller-owned cancellation
Event must be cleared by its owner. Existing checkpoints cannot be overwritten
by a new session, concurrent writers are refused, and an older loaded session
must be reloaded if another writer has advanced the checkpoint. Loading a
checkpoint alone does not modify it.

`src/apps/emit_measure/infobax_vm.py` provides the corresponding HALF-only VM
adapter and finite-pool builder. Physical workpoints are mapped through the
machine profile, the scan design matrix comes from the configured elegant
quad-to-screen transport model, and a single screen image fit is converted to
beam-size variance in mm². The adapter rejects every backend except `vm` and
does not import EPICS, Qt, GOTAcc, or research code on module import.

Build the pool with `build_half_vm_infobax_pool(...)` using the same workpoint
table, scan quadrupole, screen and plane as the adapter. The session calls
`machine.validate_pool(pool)` before execution; an unbound offline pool or a
different physical/measurement contract is refused. Workpoint variables must
be upstream of the scan quadrupole so all candidates share one transport
design matrix. Execution accepts only K1 values in the bound finite pool.

Each query waits for the IOC values to reach the VM input, then for matching
input/result hashes, VM session and calculation number. An uncached image read
is bracketed by these checks and records the VM identifiers in measurement
metadata. `measurement_timeout` defaults to 120 seconds. Missing/stale results,
failed calculations, failed publication and input changes cannot be committed.
The VM publishes one frame for each changed input, so `image_samples` must be 1;
repeated reads of a deterministic frame do not count as independent samples.

**Restart the updated VM before using this adapter.** The VM runner now waits
for image-put completion and records `screens_acknowledged` plus the acknowledged
`screen_image_pvs` in its existing status file. Older VM status files are refused.
PV names and IOC DB generation are unchanged. Adapter v2 and bound-pool
fingerprints intentionally refuse checkpoints created by the older VM adapter;
unbound numerical-only M0 checkpoints and golden results remain compatible.

Run the focused offline checks with:

```bash
python3 -m unittest tests.test_infobax_core tests.test_emit_infobax_session tests.test_emit_infobax_vm
python3 scripts/check_emit_infobax_vm.py --output-dir /tmp/emit-infobax-smoke
bash scripts/check.sh
```

The smoke script requires elegant and legacy Python SDDS. It runs in a temporary
workspace with compact linear optics, real model maps/tracking/screen fitting,
and in-memory PV I/O. It checks six initial observations, one active query,
pause/load/resume, input-version accounting and restoration. It never contacts
an IOC and refuses an existing output directory. Its summary, log and session
checkpoint remain in the output directory. This is an engineering check;
linear upstream focusing preserves emittance, so this lattice cannot establish
an optimization gain. Channel Access testing with an isolated VM IOC and a
matched-budget optimization benchmark remain required before GUI acceptance.
