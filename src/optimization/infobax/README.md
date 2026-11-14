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

## Selectable posterior and initialization

`InfoBAXCore(pool, config, model="transport_moments", initial_queries=(...))`
opts into the transport-consistent moment GP and an explicit ordered initial
query list. Its length must equal `config.initial_actions`. M0 and the existing
farthest-point initial design remain the numerical defaults, with unchanged
legacy checkpoint/golden behavior. New models and explicit designs use v2
checkpoints binding model, implementation and initialization identities.
`propose(check=...)` checks cancellation or a deadline between acquisition
candidate evaluations; it does not own machine I/O.

## Research evidence and source-only checkouts

The P2-B2–B10 experiment outputs are preserved separately in
`benchmark-evidence.tar.gz` (SHA-256 `500634eafeeff77f332efee060986fa2137e5cdca06f730b732d471efa2e7619`). Full outputs, historical
implementation snapshots, plots, logs and runtime checkpoints are not application
source and `benchmarks/` is ignored. The original corpus remains unchanged in the
working directory and in the pre-commit backup under the shared Git directory's
`infobax-backups/emit-infobax-organize-20261114-195236/`.

Research unit tests use 24 byte-identical historical inputs in
`tests/fixtures/infobax_research/`; its manifest records original paths and hashes.
Tests patch only their input root. Research CLI defaults continue to point to the
full historical corpus. For a fresh checkout, obtain the preserved archive and
restore it at the repository root before replaying those CLI experiments:

```bash
tar -xzf /path/to/benchmark-evidence.tar.gz -C "$HALF_LINAC_ROOT"
```

InfoBAX unit tests can run from a source checkout without restoring that archive.
Historical P2 findings below retain their original context and acceptance limits.
The standalone prototype checkpoint is documented in
`docs/apps/EMIT_INFOBAX.md`; integrating its workflow into an optimization tab
and the common measurement service is subsequent work.

## P2-B2: isolated IOC and matched-budget benchmark

The 2026-11-13 bounded stage closed as **BENCHMARK-STOP**. Compact frozen
evidence is in `benchmarks/p2_b2/`; the config SHA-256 is
`943b23e481028caf32242d3ccbdc0f0337c7d7ed82ca772d49f87c3cab3cd4ef`.

The isolated IOC gate passed using actual softIoc, pyepics, the existing
`pv_server.monitor_json`, `start_VM.main`, and elegant. Seven observations,
pause/load/resume, restoration, and rejection of a stale image with zero
committed stale observations passed. The launcher uses private randomized PV
names, loopback-only discovery/server binding and separate ports, then cleans
up its IOC, VM and CA processes. Repository machine mappings and IOC DB files
are not changed.

The benchmark froze two upstream-sextupole recipes, a 7-by-9 pool, 16 paired
seeds and 6 initial plus 8 active observations for M0 InfoBAX and M0 Random.
All 126 reference queries returned fitted widths, but two of seven full-scan
reconstructions in the stronger nonlinear case were nonphysical. That case
therefore did not enter policy comparison. In the weaker case InfoBAX completed
16/16 trajectories and Random 15/16; one additive-noise observation was
nonpositive and was retained as a failure without clipping or retry. Only
14/16 pairs had recommendations from both policies. Their conditional mean
InfoBAX-minus-Random relative-regret difference was +0.00653, with 95% paired
bootstrap interval [-0.1463, +0.1691]. This does not establish an acquisition
gain. The configured `near_tie` name is a recipe label; its resulting reference
spread is not a validated near tie.

Reference validity, complete budget accounting, recommendation availability
and the frozen regret-superiority gate failed. GUI acceptance remains blocked.
Investigate image-width/full-scan-reference consistency and the declared
observation-noise model in a separately bounded follow-up; do not tune or
overwrite this result. No physics-informed model, historical archive or real
backend was used.

```bash
python3 scripts/check_emit_infobax_ioc.py --output-dir /tmp/emit-infobax-ioc
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python3 scripts/benchmark_emit_infobax.py --output-dir /tmp/emit-infobax-benchmark
python3 -m unittest tests.test_emit_infobax_benchmark
```

The IOC check requires permission to open local sockets. Both runners refuse
existing output directories. The benchmark writes its config and source hashes
before generating references, reports failures, keeps complete scans inside the
evaluator, and pairs initial data and query-indexed noise between policies.

## P2-B3: measurement/reference consistency and noise diagnosis

The 2026-11-13 diagnostic completed as **MEASUREMENT-NOISE-STOP**. Frozen
configuration SHA-256:
`8181a88289a0c6e29a72fc7d10156fb3aa21498eac2818091a83ad904d28a41b`.
Complete compact tables, gates, source hashes, attribution and a figure are in
`benchmarks/p2_b3/`. P2-B2 evidence is unchanged.

The offline stage used 444 tracking attempts: 189 primary queries across a
linear control and the two unchanged P2-B2 recipes, 252 additional particle
populations, and three deterministic reruns. All queries, fits and repeats
finished. A read-only WATCH immediately before QT02 confirms identical entrance
covariance across each K1 scan. The central transport map predicts particle
variance with maximum relative error `4.36e-5`.

| Width definition | Physical reconstructions | Maximum variance error vs particles | Maximum emittance error vs entrance particles |
| --- | --- | --- | --- |
| Particle RMS | 21/21 | 0% | 0.0100% |
| Pixel-center RMS | 21/21 | 7.521% | 7.214% |
| Shared image RMS | 21/21 | 7.820% | 7.513% |
| Gaussian fit | 19/21 | 32.598% | 77.347% among valid reconstructions |

**Supported:** the same shared beam-matrix solver closes accurately on particle
second moments. Gaussian width is not a calibrated substitute for those moments
on the tested distributions; the two frozen nonphysical P2-B2 results are
reproduced. Every raster retains all particles, so finite image extent is not
the cause here. Rasterization introduces significant narrow-beam bias, and the
shared endpoint axis adds a separate `(720/719)^2` variance scaling. Merely
switching to the existing image RMS method would still fail the frozen 2%
width and 5% emittance tolerances.

**Supported:** fixed additive Gaussian noise of `0.002 mm²` gives up to 5.576%
probability of a nonpositive observation at one query. Replaying the original
Random/seed-3/query-26 draw gives `-0.00144374 mm²`, exactly the positive-contract
failure retained in P2-B2. Eight simulated particle populations per point show
strongly varying standard deviations; same-input deterministic reruns agree.
These are simulation statistics, not measured camera noise or machine drift.

**Pending:** a calibrated image-variance definition, pixel-resolution treatment
and a compatible per-point noise contract. GUI and optimization acceptance stay
blocked. Any remediation and subsequent comparison needs a new frozen config
and result directory. No GP, acquisition, inference or production measurement
path was changed in P2-B3, and no optimizer, IOC or real-machine connection ran.

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python3 scripts/diagnose_emit_infobax_measurement.py --output-dir /tmp/emit-infobax-measurement-diagnostic
python3 scripts/plot_emit_infobax_measurement_diagnostic.py /tmp/emit-infobax-measurement-diagnostic --output /tmp/emit-infobax-measurement-diagnostic/measurement_diagnostic.png
python3 -m unittest tests.test_emit_infobax_measurement_diagnostic
```

The diagnostic requires elegant/legacy SDDS, refuses an existing output
directory, and freezes metadata before running. It never clips a nonpositive
observation or projects a nonphysical reconstruction into the physical set.

## P2-B4 / P2-B4.1: count-variance calibration

Both frozen attempts completed as **CALIBRATION-STOP**. Evidence is in
`benchmarks/p2_b4/` and `benchmarks/p2_b4_1/`; the original B2/B3 bundles are
unchanged. New performance comparisons were not started.

`infobax_measurement.py` adds an explicit `rms_count_variance` VM measurement
method. It uses pixel-center moments, subtracts `pixel_width_mm²/12`, and
estimates per-frame variance uncertainty from particle count and the second
and fourth moments. Integer counts, minimum count/resolution and image-edge
checks fail explicitly; no nonpositive result is clipped. The method and
`RmsVarianceConfig` parameters, including any fitted global `noise_scale`,
are bound into pool/session fingerprints. The numerical M0 core and the
existing default measurement API are unchanged.

The first attempt used 729 new tracking runs, including 189 primary frames
and independent calibration/validation particle populations. Corrected
variance maximum errors were 1.443% on primary frames and 0.878% on held-out
frames; all 21 reference reconstructions were physical. Equal-weight
reference emittance error remained 6.999%, and the held-out standardized
residual mean square was 0.269 against the frozen [0.5, 2] scale gate.

One further bounded attempt, B4.1, retained these failures and the exact same
acceptance thresholds. It reused B4 primary/training frames, used the existing
shared **weighted** SVD solver with per-point errors, and fitted a single global
noise scale (`0.8063974059901406`) from training repeats only. Eight new
held-out particle seeds required 288 new runs. The noise gates passed:
standardized mean 0.00317, mean square 0.6126 and 95% interval coverage 100%.
Held-out variance error was at most 1.008%, and all 21 weighted references
were physical.

Reference emittance precision still failed: the maximum error was 5.1378%,
above the unchanged 5% bound, at the weaker-sextupole workpoint K1 = -3.
Its fitted beam moments differ from entrance particle moments by less than
0.1% each, while cancellation in their determinant produces a 10.01% relative
determinant error. Small point-variance errors therefore do not guarantee
the required emittance accuracy. The stage stopped without relaxing the
threshold or tuning the GP/acquisition.

The RMS read/write/checkpoint path passed actual isolated IOC acceptance,
including pause/resume, restoration and stale-frame refusal. That IOC check
used unit noise scale; scale arithmetic and scale-dependent compatibility
were separately regression-tested. This is an experimental opt-in method,
not an accepted optimization measurement protocol. Camera ADU noise, machine
drift and future-query noise remain uncalibrated; future-query acquisition
noise retains the declared fixed forecast, while revealed frames carry their
own uncertainty estimates.

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python3 scripts/calibrate_emit_infobax_rms.py --output-dir /tmp/emit-infobax-rms-calibration
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python3 scripts/calibrate_emit_infobax_weighted_rms.py --output-dir /tmp/emit-infobax-weighted-calibration
python3 scripts/check_emit_infobax_ioc.py --measurement-method rms_count_variance --output-dir /tmp/emit-infobax-rms-ioc
python3 -m unittest tests.test_emit_infobax_rms_variance tests.test_emit_infobax_rms_calibration tests.test_emit_infobax_weighted_rms
```

Config SHA-256: B4
`07513d70a0741299d52516e3bd089ad6d728ff43818ad43906853329502eba17`;
B4.1 `31438d927045ce446de5918205c264e65a0ebf8bb96f8187e1bf633dff888d4e`.
Reference-resolution/statistics remediation needs a new bounded configuration;
the current results do not authorize GUI or real-machine acceptance.

## P2-B5: particle statistics and reference precision

The 2026-11-13 bounded factorial completed as **REFERENCE-PASS**. Frozen
configuration SHA-256:
`90ed7181966b6eab2971e5c4a90c3fe6128b5714ee8c81728ace89d8b408f611`.
Tables, primary-only selection, gates, source snapshots, checksums and a figure
are in `benchmarks/p2_b5/`. All 43 prior-stage evidence files are unchanged.

The experiment crossed 20,000/100,000 particles with native/double linear
image resolution over the same 3 cases, 7 workpoints and 9 scan settings.
Each tracked population was rasterized at both resolutions with the same
physical field of view: native 720×540 at 0.0434 mm/pixel, fine 1440×1080
at 0.0217 mm/pixel. Every reconstruction was compared with its own tracked
entrance covariance. The primary seed was 12345; independent validation
seeds were 104729 and 130363. Total work was 1,134 particle tracking attempts,
2,268 raster observations and 252 reference reconstructions, with no failed
frames, nonphysical references or lost image counts. Native 20k primary
variances and weighted emittances exactly reproduce B4.1.

| Offline arm | Primary max variance error | Validation max variance error | Primary max emittance error | Validation max emittance error |
| --- | --- | --- | --- | --- |
| Native, 20k | 1.443% | 0.714% | 5.138% | 3.028% |
| Fine, 20k | 0.361% | 0.205% | 0.939% | 2.965% |
| Native, 100k (selected) | 0.610% | 0.590% | 1.389% | 2.871% |
| Fine, 100k | 0.232% | 0.209% | 0.881% | 1.926% |

The unchanged limits are 2% variance and 5% reference emittance error.
The predeclared selection order preferred native 100k, then fine 20k, then
fine 100k. Native 100k passed on the primary seed and was locked before
validation began. Its 21 primary and 42 held-out references all passed;
held-out results did not change the selection. Passing native 20k held-out
results do not erase its frozen primary failure.

This establishes reference precision only over the declared offline cases
and particle populations. The inherited global noise scale is used only
for relative WLS weights; its common factor cancels from the fitted moments.
Noise calibration at 100k, future-query noise and runtime acceptance under
that setting remain pending. No production particle-count/image-geometry
setting, PV, IOC, GP, acquisition or GUI was changed, and no new optimizer
comparison ran. Next: independently calibrate and validate the selected
100k measurement noise before a new matched-budget policy benchmark.

The 115 focused regression tests, new-script compilation and
`bash scripts/check.sh` passed. An extended 166-test run had five existing
failures reproduced with the HEAD test snapshot and unchanged model backend:
four tests requiring absent generated VM/sample files, and an older Twiss
object-identity assertion. These are recorded in the bundle's `verification.json`.

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python3 scripts/validate_emit_infobax_reference_precision.py --output-dir /tmp/emit-infobax-reference-precision
python3 scripts/plot_emit_infobax_reference_precision.py /tmp/emit-infobax-reference-precision --output /tmp/emit-infobax-reference-precision/reference_precision.png
python3 -m unittest tests.test_emit_infobax_reference_precision
```

The runner requires elegant/legacy SDDS, refuses an existing output directory
and freezes configuration/source/predecessor hashes before running. It never
clips, retries, drops failed scans or selects an alternative using validation.

## P2-B6: independent noise calibration at native 100k

The 2026-11-13 bounded noise stage completed as **CALIBRATION-STOP**. Frozen
configuration SHA-256:
`9b9e4755ce55742dad5a56844fc16508b26f77f9611ab2f10d27e241eebf00d9`.
Evidence is in `benchmarks/p2_b6/`; all 67 B2–B5 evidence files are unchanged.
The B5 reference precision acceptance remains valid within its stated scope.

The selected native 720×540, 0.0434 mm/pixel, 100,000-particle setting was
kept fixed. Eight fresh training seeds and eight separate validation seeds
were evaluated at the same 36 repeated optical points used in B4/B4.1.
All seeds are disjoint from previous recorded particle populations. All
576 new frames were valid and retained every particle. The 189 native-100k
B5 primary frames were reused after removing their old uncertainty scale.

One global factor, **0.6528781294640106**, was fitted from the 288 training
frames and written before any validation tracking began. It was then applied
unchanged to 288 held-out frames. The same frozen calibration gates apply:

| Held-out noise check | Result | Gate |
| --- | --- | --- |
| Absolute standardized mean | 0.4481 | ≤0.5: passed |
| Standardized mean square | 1.5405 | [0.5, 2]: passed |
| Empirical 95% interval coverage | **87.5% (252/288)** | ≥90%: **failed** |

Held-out point-variance error was at most 0.7693%, below the 2% limit. The
21 primary weighted reference reconstructions remained physical with maximum
emittance error 1.3894%. Replacing the common uncertainty scale changed
their emittances by at most `2.565e-14` relatively. B5's independent reference
validation remains inherited evidence; it is not a new validation run.

Of 36 interval misses, 33 occur at seed 1256137035, two at 846658893 and
one at 1169911769. Each seed is reused across all 36 optical points. The
descriptive median correlation of pointwise residuals across the eight seeds
is 0.974; 288 frame scores must not be treated as 288 independent population
draws when assigning uncertainty to coverage. This does not establish a
precise population coverage deficit, but the declared empirical gate failed.
All samples and the frozen factor are retained without held-out refitting.

No optimizer comparison, IOC run, production default or GP/acquisition change
was made. The next bounded investigation should increase independent seed
counts for both training and a fresh validation set, report uncertainty by
seed, and retain the acceptance thresholds. Runtime and future-query-noise
acceptance remain pending. The 120 focused tests, static checks and new
script compilation passed.

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python3 scripts/calibrate_emit_infobax_100k_noise.py --output-dir /tmp/emit-infobax-100k-noise
python3 scripts/plot_emit_infobax_rms_calibration.py /tmp/emit-infobax-100k-noise --output /tmp/emit-infobax-100k-noise/calibration.png
python3 -m unittest tests.test_emit_infobax_100k_noise
```

The runner requires elegant/legacy SDDS, refuses existing output directories,
checks prior artifact hashes, records source hashes and rejects seed leakage,
duplicate populations, changed geometry and relaxed thresholds.

## P2-B7: expanded independent-seed noise validation

The 2026-11-13 fixed-budget continuation completed as **NOISE-PASS** under
the unchanged offline calibration gates. Configuration SHA-256:
`75f3a91c234ce4152004e33b75330471b83f1c6764baf45b08cbd555e219c781`.
Tables, uncertainty figures, source snapshots and checksums are in
`benchmarks/p2_b7/`. All 103 B2–B6 evidence files, including the B6 failure,
are unchanged.

The experiment used 32 fresh training seeds and 32 separate fresh validation
seeds, each evaluated at the same 36 optical points and native-100k setting.
All 2,304 new tracking frames were valid, retained all 100,000 particles and
used the native 720×540, 0.0434 mm/pixel geometry. Three independent worker
processes used private SDDS/model workspaces; a separate three-frame
engineering check verified this isolation using the already known seed 12345.
No B6 training or held-out populations entered the new fit or validation.

The global noise factor **0.6902834680118383** was fitted from 1,152 training
frames and frozen before generating any held-out frames. Its whole-seed
95% bootstrap interval is [0.4657, 0.8883]. There was no held-out refitting,
early success stop, sample extension, clipping or sample rejection.

| Held-out metric | Estimate | Whole-seed 95% interval | Unchanged gate |
| --- | --- | --- | --- |
| Empirical 95% interval coverage | **95.6597% (1102/1152)** | **[90.9722%, 98.6979%]** | ≥90%: passed |
| Standardized mean | -0.0898 | [-0.4247, 0.2469] | Absolute value ≤0.5: passed |
| Standardized mean square | 1.0885 | [0.7413, 1.4763] | [0.5, 2]: passed |

Both bootstrap calculations resample complete seed vectors, preserving the
correlations across 36 optical points, with 5,000 prespecified draws.
Validation intervals condition on the frozen training factor and training
means; they exclude training-fit uncertainty. Training-factor uncertainty is
reported separately. Intervals are descriptive and did not change the original
point-estimate acceptance gates. Passing this finite experiment does not prove
that B6 failed solely because its seed count was small.

Held-out point-variance error was at most 0.8752%, below the 2% bound. The
189 B5 primary frames were reused after removing their inherited scale; all
21 weighted primary reference reconstructions remained physical, with maximum
emittance error 1.3894%. The maximum relative change from the B5 emittances was
`1.666e-14`. B5's 42 independent accepted reference reconstructions remain
inherited evidence rather than additional B7 runs.

The 125 focused regression tests, static checks, script compilation and worker
engineering check passed. No production defaults, PV/IOC settings, GP or
acquisition code were changed, and no optimizer comparison ran. Next: verify
the selected 100k setting and fitted scale through the isolated IOC/session
path before a newly frozen matched-budget comparison. Future-query noise,
camera noise and machine drift remain outside this particle-noise acceptance.

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python3 scripts/calibrate_emit_infobax_expanded_noise.py --output-dir /tmp/emit-infobax-expanded-noise
python3 scripts/plot_emit_infobax_seed_uncertainty.py /tmp/emit-infobax-expanded-noise --output /tmp/emit-infobax-expanded-noise/seed_uncertainty.png
python3 -m unittest tests.test_emit_infobax_expanded_noise
```

The runner requires elegant/legacy SDDS, refuses existing output directories,
checks predecessor artifacts and source hashes, and rejects seed leakage,
changed physical settings, changed budgets and relaxed calibration gates.

## P2-B8: calibrated IOC acceptance and matched-budget comparison

The 2026-11-13 stage completed as **BENCHMARK-STOP**. The isolated IOC gate
passed; the unchanged M0 algorithm did not establish superiority to Random.
Frozen configuration SHA-256:
`8c5210207bebdc9e584b490e5395598f61f8f86efa18880995e189c5b20df95f`.
Evidence is in `benchmarks/p2_b8/`; all 144 B2–B7 evidence files are unchanged.

The private loopback IOC/VM path used native 100k count frames and B7's
training-only factor 0.6902834680118383. Seven committed frames were checked
against the actual CA image: counts, variances and scaled uncertainty matched.
Pause/load/resume, setpoint restoration, actual IOC-to-JSON monitoring and
stale-frame rejection passed, with zero stale observations committed.
Random test PV names, private ports and temporary runtime files isolated the
test, and its IOC, VM and repeater processes were cleaned up.

The first wrong-scale checkpoint probe also changed the measurement timeout.
A supplemental read-only audit reproduced the compatible pool/machine
fingerprints and loaded the completed checkpoint, then changed only the
scale with both timeouts fixed at 30 seconds. It rejected the checkpoint with
zero PV reads/writes. After the benchmark ended, the live test's timeout was
corrected and a separate full IOC run passed again. Both runtime reports,
the supplemental audit, benchmark-time sources and corrected checker are
retained. The policy data and frozen prerequisite report were not overwritten.

The comparison retained the original 16 paired seeds, two cases, 7×9 pool,
6 initial plus 8 active observations, numerical M0 controls and benchmark
thresholds. Each revealed observation came from a fresh query-indexed elegant
particle population, using corrected count variance and its calibrated
per-frame error. Common queries shared the same frame between policies; all
32 case/seed pairs had identical six-observation initial data. Reference
curves remained evaluator-only. The existing fixed future-query forecast
of 0.002 mm² was retained and is not newly validated by this comparison.

All 64 trajectories completed 14 unique observations: **896 charged
observations**, generated from **671 unique actual frames**, all valid.
All 14 full-scan reference reconstructions were physical.

| Policy | Completed runs | Available recommendations | Conditional mean relative regret |
| --- | --- | --- | --- |
| M0 InfoBAX | 32/32 | 31/32 (96.875%) | 0.16807 |
| M0 Random | 32/32 | 31/32 (96.875%) | 0.15044 |

The aggregate recommendation-rate gate (≥95%) passed. Per-case diagnostics
retain Random/nonlinear and InfoBAX/near_tie availability of 15/16 (93.75%).
Only **14/16** policy seeds have every case-wise recommendation under both
policies. On those complete seeds, the conditional mean InfoBAX-minus-Random
relative-regret difference is **+0.01891**, with paired-seed 95% bootstrap
interval **[-0.04385, +0.07846]**. The superiority gate failed: the interval
crosses zero and two complete paired seeds are missing. No unconditional
policy advantage is claimed.

The unavailable recommendations were Random/nonlinear/seed-10 and
InfoBAX/near_tie/seed-12. Their posterior invalid-output probabilities were
0.6270 and 0.5410 against the unchanged 0.50 threshold. Every posterior
summary assigned zero sampled probability to all seven candidates being
physical simultaneously; this differs from the any-candidate output gate.
These are model/recommendation diagnostics, with no measurement failures.
The near-tie recipe has a finite-reference spread of 1.3471% and a runner-up
gap of 0.06279%; that finite ranking is not population truth.

The 131 focused tests, static checks and script compilation passed; the final
checker correction also passed the full isolated IOC run. GP/acquisition,
production defaults, PV maps and real-machine paths were not changed.
GUI acceptance remains blocked. Next: diagnose M0 posterior physicality,
unavailable recommendations and consistency between its unchanged full-scan
functional and the weighted reference target, using these frozen trajectories
before deciding on model changes.

```bash
python3 scripts/check_emit_infobax_ioc.py --measurement-method rms_count_variance --particles 100000 --noise-scale 0.6902834680118383 --output-dir /tmp/emit-infobax-100k-ioc
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python3 scripts/benchmark_emit_infobax_calibrated.py --ioc-summary src/optimization/infobax/benchmarks/p2_b8/ioc_acceptance.json --output-dir /tmp/emit-infobax-calibrated-comparison
python3 -m unittest tests.test_emit_infobax_calibrated_benchmark tests.test_emit_infobax_benchmark
```

The IOC check requires local-socket permission. The benchmark example replays
the frozen historical IOC prerequisite; a fresh IOC run has a different
random-prefix report hash and requires a new frozen configuration. Both
runners refuse existing output directories. No failed measurements, withheld
recommendations or incomplete pairs are replaced, clipped or silently omitted.

## P2-B9: posterior physicality and full-scan target diagnosis

The 2026-11-13 read-only investigation completed as **DIAGNOSTIC-COMPLETE**.
Configuration SHA-256:
`7c6631f29d179215bfc7c8714e447b1c0debb914891de3fcf1a0a0859c0598f7`.
Evidence is in `benchmarks/p2_b9/`. All 214 B2–B8 files remain unchanged;
B8 remains **BENCHMARK-STOP**.

Rebuilding 18 transport maps in private temporary directories reproduced
the exact B8 pool fingerprints. All **128/128** initial/final recommendation
summaries replayed exactly from their checkpoints. The two failed histories
were also inspected at every prefix from 6 through 14 observations. No new
particle measurements, policy runs, IOC/PV operations, model changes or
threshold changes occurred.

Using 16,384 fixed diagnostic draws for each final posterior gave:

| Unavailable recommendation | Invalid-output probability | Conditional MC 95% interval | Share of invalid events with positive curves but no physical covariance |
| --- | --- | --- | --- |
| Random / nonlinear / seed 10 | 62.1582% | [61.4128%, 62.8979%] | 99.8625% |
| InfoBAX / near_tie / seed 12 | 56.6101% | [55.8498%, 57.3674%] | 100% |

Both remain above the original 50% gate. The strong-case Random history was
already above that gate at six observations. The weak-case InfoBAX history
crossed it at 12 observations under the original summary draws. These are
properties of the fitted M0 posteriors, not image-quality failures. The
intervals describe Monte Carlo error conditional on a fitted posterior;
they do not quantify model correctness or machine yield.

The current GP models the 63 response variances directly. At the 3,136
unobserved query occurrences, its Gaussian latent distributions assign
median **35.1085%** mass to negative variance. At observed positions that
mass is numerically zero. The median expected curve energy outside the
three-column transport-design span is **12.5097%** across final candidate
posteriors; deterministic primary curves have at most `1.734e-7` fractional
off-span energy. Positive response values alone therefore do not ensure a
positive-definite fitted beam matrix. These diagnostics support testing a
posterior that incorporates the transport structure.

Higher sampling also resolves rare jointly physical draws in 7/64 runs
(at most 2/16,384); 57/64 still have none. Each zero count gives a one-sided
95% conditional MC upper bound of 0.01828%. Thus the original zero joint
physicality estimates must not be read as proof of exactly zero probability.
Joint physicality of all candidates is distinct from the output's
any-physical-candidate gate.

One additional result is sensitive to the original summary sampling budget:
Random/near_tie/seed-10 has invalid probability 0.4971 under the original
1,024 draws and 0.5078 under the enhanced draws. Random/near_tie/seed-7's
enhanced MC interval also crosses 0.50. These are conditional sampling
diagnostics, recorded in `sampling_sensitivity.json`; they do not replace
B8's recommendations, rates, regret scores or outcome.

The existing M0 full-scan functional agrees with the shared **unweighted**
beam-matrix solver to `5.041e-14` relative error on the primary curves.
The **weighted** references reproduce exactly. Their weighting definitions
can differ in emittance by **1.5681%**, with changes to the near-tie ordering,
but the best complete-primary workpoint agrees in both cases. This target
difference does not directly trigger the two output-gate failures. It remains
a comparison-design issue to keep explicit.

Observed-response spread determines the GP amplitude on all 64 final
histories; the fixed future-noise floor does not dominate it. This does not
test that forecast's effect on acquisition paths. No single intervention is
claimed to explain or fix B8 regret. The next bounded step is an offline
three-moment posterior comparator constrained by the transport matrix,
fitted to the same frozen observations, retaining the invalid category and
0.50 gate. Compare physicality and withheld full-curve predictions before
starting a new policy benchmark.

All 137 distinct focused tests and static checks passed. The one-off test
command executed the eight RMS tests twice (145 successful executions);
`verification.json` records both counts. All six new diagnostic tests passed.
No model or GUI acceptance is established by this diagnostic.

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python3 scripts/diagnose_emit_infobax_posterior.py --output-dir /tmp/emit-infobax-posterior-diagnostic
python3 scripts/plot_emit_infobax_posterior.py /tmp/emit-infobax-posterior-diagnostic --output /tmp/emit-infobax-posterior-diagnostic/posterior_diagnostic.png
python3 -m unittest tests.test_emit_infobax_posterior_diagnostic
```

The runner requires elegant for transport-map reconstruction, refuses an
existing output directory, checks predecessor/source hashes and requires
exact pool fingerprints before replay. Saved `pools.json` permits subsequent
numeric-only comparisons without regenerating transport maps.

## P2-B10: experimental transport-moment posterior

The 2026-11-13 fixed-history study completed as **COMPARISON-COMPLETE**.
Configuration SHA-256:
`2ca77f3fde54238a47bdf66a5ed698ce09dc4dd54684ae14bd49efcff0e69d5b`.
Evidence is in `benchmarks/p2_b10/`. All 254 B2–B9 evidence files are unchanged.
This is a posterior comparison, with no new adaptive policy or machine run.

`transport_posterior.py` adds an explicitly experimental `TransportMomentGP`.
For the fixed design `A = Q R`, each candidate has three latent coordinates
`u`, response `y = Q u` and beam coefficients `R^-1 u`. A fixed Matérn-5/2
kernel acts across candidate coordinates, using M0's candidate length scale.
The prior mean is the observed median projected into the transport span;
the amplitude uses M0's observed-only formula. Multiplying latent prior
variance by `scan_count/3` matches average response variance to that scale.
Conditioning uses the same per-frame errors and likelihood jitter as M0.

Sampling occurs in the 21-dimensional latent space and then maps to 63
responses. No response-space eigenvalue floor introduces extra scan modes.
No physical covariance projection, clipping, sample rejection or hidden
reference fitting is used. The existing invalid category and 0.50 threshold
remain in force. This model is not selected by the default `InfoBAXCore` and
does not alter its checkpoint format or production M0 path.

The same 64 B8 histories were compared at 6 and 14 observations: 128 paired
posterior comparisons, 256 fits, with 16,384 diagnostic samples per fit.
All 64 final M0 controls reproduce B9's enhanced sample results exactly.
The moment posterior's maximum relative sample transport residual was
`1.322e-15`. Results are conditional on historical M0/Random query choices:

| Observations | Model | Available diagnostic recommendations | Mean invalid-output probability | Mean withheld normalized RMSE |
| --- | --- | --- | --- | --- |
| 6 | M0 | 32/64 | 52.9484% | 0.29448 |
| 6 | Transport moments | 60/64 | 17.9186% | 0.36789 |
| 14 | M0 | 61/64 | 19.1822% | 0.22792 |
| 14 | Transport moments | 64/64 | 0.1021% | 0.21406 |

The M0 61/64 count uses B9's enhanced sampling, including its boundary
sensitivity; it does not replace B8's native 62/64 count. Whole-seed bootstrap
intervals retain both cases and both historical policies together, using
16 seeds and 5,000 fixed draws:

| Moment-minus-M0 metric | 6 observations, 95% interval | 14 observations, 95% interval |
| --- | --- | --- |
| Mean invalid probability difference | -0.35030 [-0.44682, -0.26921] | -0.19080 [-0.22522, -0.15924] |
| Normalized RMSE difference | +0.07340 [+0.01156, +0.13830] | -0.01385 [-0.04185, +0.01140] |
| Negative log predictive density difference | -0.36297 [-0.54410, -0.19046] | -1.09508 [-1.20844, -0.97370] |

Physicality and predictive density improve on these frozen histories. Early
point prediction worsens; the 14-observation RMSE interval crosses zero.
Transport consistency alone also leaves nonphysical beam-matrix samples:
joint physicality of all seven candidates averages only 0.9292% at 14 points.
No adaptive policy gain, fully physical posterior or real-machine acceptance
is claimed. B8 remains **BENCHMARK-STOP**. All 145 distinct focused tests,
static checks and script compilation passed. No IOC, elegant process or
particle tracking ran in this numeric-only study.

### Standalone workflow direction

Following the requested route, `workflow_reuse.json` maps six existing
interfaces for a future independent InfoBAX workflow. The optimization
session and GUI files match the `half_linac` main worktree exactly. Reusable
parts include `OptimizationSession`, `EpicsVariableGroup`,
`VerifiedQuadRestore`, frozen measurement settings, worker request/result
ownership and private archives. The current GUI bridge depends on the
emittance window and launches full scans. Its solenoid-current variables in
A also differ from the present InfoBAX VM adapter's quadrupole K1 variables.

Next engineering work should separate a standalone session/measurement
bridge, define **single-point** InfoBAX budgets separately from full-scan
baseline/confirmation, and reuse profile-based limits and verified restoration.
Record total camera acquisitions and machine time alongside those separate
budgets, including repeated frames and baseline/confirmation scans.
Real camera measurements need their own variance/uncertainty contract; the
VM particle-count noise factor is not an ADU calibration. Model selection and
checkpoint fingerprints must identify the experimental posterior explicitly.
Existing RCDS/BO/cBO behavior should remain intact.

Workflow engineering validation and algorithm-superiority evidence are
separate tracks. After the point-measurement/execution and restoration path
is verified, a short-budget, operator-supervised real-machine pilot can be
planned for iteration. This study neither enables that backend nor connects
to live PVs. It does not use offline superiority as a substitute for field
engineering verification, or claim field readiness from counterfactual results.

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python3 scripts/compare_emit_infobax_transport_moments.py --output-dir /tmp/emit-infobax-transport-moments
python3 scripts/plot_emit_infobax_transport_moments.py /tmp/emit-infobax-transport-moments --output /tmp/emit-infobax-transport-moments/transport_comparison.png
python3 -m unittest tests.test_emit_infobax_transport_moments
```

The runner loads saved B9 pools without elegant, refuses existing output
directories, verifies source/predecessor hashes and uses only the specified
frozen observations for fitting. Full primary curves enter evaluation only.

The default P2-B1/M0 route remains VM-only and adds no GUI or machine
commissioning. B10 is an offline experimental component, not a production
Mphi/PSD/Laplace/HMC route. Existing RCDS/BO/cBO workflows are unchanged.
