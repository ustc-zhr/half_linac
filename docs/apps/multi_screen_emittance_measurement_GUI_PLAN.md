# Multi-Screen Emittance GUI Revised Plan

## 1. Product Decision

The Multi-Screen page is an image-centred measurement workspace. A target image
must be visible while acquiring and reviewing a sample. The image is required
to judge clipping, saturation, background contamination, under-resolution,
wrong screen selection and shot-to-shot drift. A table of beam sizes alone is
not sufficient for a trustworthy emittance measurement.

The existing Quad Scan page remains unchanged. Multi-Screen reuses its image
analysis path and presents the same two size sources:

- `Local fit`: calculated from the displayed image and used for reconstruction.
- `Published PV`: optional `sigx/sigy` cross-check only; never used as the beam
  matrix input.

The first online version is read-only with respect to the machine. It does not
insert/retract screens, write quadrupoles, or run the optics optimizer.

## 2. User Workflow

### States

1. `Configuring`: edit preset, model line, reference and screen order.
2. `Preparing`: build one frozen model snapshot and all screen transport maps.
3. `Ready`: optics passed rank/coupling/conditioning checks; configuration is
   frozen.
4. `Acquiring`: preview or collect samples from the selected screen.
5. `Fit Ready`: every configured screen has the requested number of accepted
   local fits.
6. `Complete`, `Partial` or `Invalid`: show independent X/Y reconstruction
   status and diagnostics.
7. `Archived`: display a saved session without accessing current PVs or model
   files.

Changing model line, reference, screen list, energy or sample target after
`Ready` invalidates the session and returns to `Configuring`. Samples must not
be silently reused with a different optics snapshot.

### Primary actions

- `Prepare Measurement`: freeze optics and show observability diagnostics.
- `Preview`: read the selected image and update the display without adding a
  sample.
- `Acquire Sample`: read, fit and accept one image sample after the operator
  confirms the selected screen and beam stability.
- `Add Manual Sample`: enter positive finite local σx/σy for offline or
  exceptional data; record source as `manual`.
- `Reconstruct`: fit X and Y independently when all screens are complete.
- `New Measurement`: discard current samples and return to the current runtime
  configuration.
- `Save As...` and `Load Archive`: explicit user export/import in addition to
  automatic runtime archiving.

## 3. Revised Layout

### Header

- Runtime context: machine, backend and model line.
- Session state and a short reason, for example `Ready · X/Y observable` or
  `Partial · X non-physical`.
- Compact actions: `Prepare`, `New`, `Load Archive`.

### Left: screen sequence

The screen list is the measurement sequence, not merely a selector. Each row
shows screen id, reference marker, accepted/target sample count, local σx/σy
mean and standard error, fit quality (`usable`, `clipped`, `under-resolved`,
`poor fit`, `unavailable`), and a marker for the selected screen.

The list supports add, remove and reorder. Duplicate screens are forbidden and
at least three screens are required. Candidates are derived from configured
FLAG elements with an image channel on the selected model line.

### Centre: current target image

This is the largest area of the page and is always tied to the selected screen.

- 2-D image with the active ROI and fit overlay;
- intensity scale, colormap and background controls reused from Quad Scan;
- X/Y projections with the local Gaussian fit overlaid;
- centroid, σx, σy, fit residual, containment and edge/saturation warnings;
- timestamp and sample source (`image` or `manual`).

The image remains available after acquisition so the operator can inspect the
accepted sample. Failed quality checks are displayed but are not added.

### Right: local fit and PV cross-check

Keep the two sources visually separate and label them explicitly:

```text
Local image fit (used)
  σx = ... mm    σy = ... mm

Published PV cross-check (not used)
  σx = ... mm    σy = ... mm
```

When the PV is missing, show `Unavailable`, not zero. When the difference is
large, show a warning but continue using the local fit if its quality is valid.
Below this, show the selected screen's `(R11, R12)` and `(R33, R34)` rows.

### Bottom: reconstruction and diagnostics

- X/Y geometric and normalized emittance, β, α and γ;
- uncertainty when a parameter covariance is available;
- rank, singular values, condition number, degrees of freedom and solver;
- weighted versus unweighted fit reason;
- measured variance, fitted variance and residual for every screen;
- concise warnings for non-physical covariance, coupling, dispersion caveat
  and poor conditioning.

Plots should be compact: one X/Y variance fit with residuals, not decorative
cards or duplicated configuration panels.

## 4. Shared Image and Fit Services

Extract the common Quad Scan operations into an injectable service used by both
pages:

1. Resolve FLAG geometry and image PV from the machine profile.
2. Read and reshape the image; apply `flip_y`, background and ROI.
3. Run the existing Gaussian/RMS projection fit.
4. Produce quality flags and a display payload containing image, projections,
   fit parameters and optional `sigx/sigy` PV values.
5. Convert local fit dimensions from mm to SI before reconstruction.

The Multi-Screen domain module remains PyQt- and EPICS-free. The workspace owns
the adapter and display; `BeamMatrixReconstruction` owns optics and statistics.

## 5. Session and Archive

The session freezes runtime context, preset, model line, reference, energy,
ordered screens, complete transport matrices, accepted samples, source,
timestamp, ROI/background metadata, fit quality, and reconstruction diagnostics.

After a successful reconstruction, write atomically to both:

```text
runtime/<machine>/<backend>/runs/multi_screen_<timestamp>/measurement.json
runtime/<machine>/<backend>/latest/multi_screen_measurement.json
```

The JSON archive never embeds raw image pixels. It stores fit metadata and
cross-check values so that review can distinguish local-fit input from PV
readback. Loading an archive is read-only and recomputes from archived maps and
samples without calling the current model or EPICS.

## 6. Implementation Phases

### Phase A: image-centred page

- Add the image/projection panel and selected-screen binding.
- Reuse Quad Scan image fit, ROI, background and PV cross-check logic.
- Add a screen table with sample progress and quality state.
- Keep synchronous reads bounded for the first VM version; show a clear busy
  state while reading.

### Phase B: robust acquisition

- Move image reads and fitting to a cancellable worker.
- Add `Preview`, `Acquire Sample`, retry after quality failure and stop support.
- Store full per-sample fit metadata and machine snapshot identifiers.
- Show a visible warning when standard errors are zero/unavailable and fitting
  falls back to unweighted least squares.

### Phase C: reconstruction review

- Add measured-versus-fitted variance and residual plots.
- Show X/Y partial validity independently.
- Add optics row details, dispersion/coupling warnings and conditioning bands.
- Disable reconstruction for rank-deficient or solver-limit cases.

### Phase D: archive review and acceptance

- Strict schema validation and archive integrity comparison.
- Cross-backend read-only review without PV/model access.
- Save As/Load Archive offscreen tests and full VM acceptance.

Optics optimization, magnet writes, screen insertion control and resolution
recalibration remain outside this GUI scope.

## 7. Verification

### Automated

- Shared image service: valid fit, clipping, saturation, under-resolution,
  malformed image, missing PV, ROI/background and optional size PV.
- Workspace: default HALF `PRF06–PRF09`, selection/reordering, minimum-three
  constraint, preparation invalidation, sample progress, manual source and
  Archived button state.
- Reconstruction: three-screen exact solution, multi-screen weighted fit,
  zero-SE unweighted fallback, partial X/Y result, covariance uncertainty and
  archive round trip.
- Offscreen GUI: all four tabs, current-screen image visibility, local/PV labels,
  state transitions and no EPICS/model calls while reviewing archives.

### VM acceptance

1. Start the HALF softIOC and VM in `half_linac`.
2. Open Emittance with `HALF_LINAC_CONTROL_BACKEND=vm`.
3. Prepare `PRF06–PRF09` and verify frozen maps and conditioning.
4. Preview each target and inspect the actual image/ROI/fit overlay.
5. Acquire three accepted local-fit samples per screen; verify `PRF09` works
   without `sigx/sigy` PVs.
6. Reconstruct and inspect local-fit/PV cross-check separation, residuals and
   weighting warning.
7. Confirm automatic `runs/` and `latest/` archives, then load the archive in
   a second read-only window.

