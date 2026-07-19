# HV Feedback

This app stabilizes the IRFEL KLY1 CH3 amplitude by applying bounded integral
corrections to the modulator-1 high-voltage setpoint.

- Official entrypoint: Control Room launcher (`main.py`).
- Supported runtime: IRFEL with the `real` control backend only.
- `Start Monitor` is read-only. `Start Feedback` requires a fresh operator
  confirmation and re-checks machine-profile write policy before every caput.
- PV names come only from the IRFEL machine profile. Runtime snapshots contain
  control, reference, and safety values, never PV overrides.
- Reference measurement collects the configured number of valid samples at the
  configured interval before calculating the reference values.
- The trend view defaults to reference-relative values and a recent 15-minute
  window; raw values, the whole in-memory run, wall-clock time, and standard
  Matplotlib navigation are available from the plot header and toolbar.
- `Latest Sample` reports feedback-relevant derived values. Invalid or stale PV
  data is shown explicitly instead of leaving the previous value looking live.
- Logs and snapshots are stored under `runtime/irfel/real/` and are not source.

The defaults were migrated from the commissioned
`irfel_kly1_hv_feedback_20260624_2.json` configuration. Offline tests use a
test-only fake EPICS client; the operator application has no mock or VM mode.
