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

`src/apps/emit_measure/infobax_vm.py` provides the corresponding HALF-only VM
adapter and finite-pool builder. Physical workpoints are mapped through the
machine profile, the scan design matrix comes from the configured elegant
quad-to-screen transport model, and a single screen image fit is converted to
beam-size variance in mm². The adapter rejects every backend except `vm` and
does not import EPICS, Qt, GOTAcc, or research code on module import.

P2-B1 still does not add a GUI, authorize the real backend, or constitute
machine commissioning. It also does not add Mphi, PSD, Laplace, HMC, or any
physics-informed recommendation path. The existing RCDS/BO/cBO workflows are
unchanged.
