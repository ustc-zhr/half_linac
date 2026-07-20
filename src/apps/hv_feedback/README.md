# HV Feedback

This app stabilizes a selected RF channel by applying bounded integral
corrections to its feedback unit's high-voltage setpoint. A machine profile may
define multiple feedback units, each with one high-voltage actuator and one or
more peer RF channels. One unit and one channel are active at a time.

- Official entrypoint: Control Room launcher (`main.py`).
- Supported runtime: IRFEL with the `real` control backend only.
- `Start Monitor` is read-only. `Start Feedback` requires a fresh operator
  confirmation and re-checks machine-profile write policy before every caput.
- Feedback Unit and Feedback Channel may be selected only while stopped. The
  current IRFEL profile defaults to KLY1 / ACC1 to preserve the commissioned
  behavior; the other channel is monitored for phase and amplitude-ratio drift.
- PV names come only from the IRFEL machine profile. Unit-scoped runtime
  snapshots contain control, reference, and safety values, never PV, machine,
  backend, or feedback-channel overrides.
- Reference measurement collects the configured number of valid samples at the
  configured interval before calculating the reference values.
- The trend view defaults to reference-relative values and a recent 15-minute
  window; raw values, the whole in-memory run, wall-clock time, and standard
  Matplotlib navigation are available from the plot header and toolbar.
- Reference measurement records absolute amplitude and phase for every RF
  channel. Relative ratios are derived against the selected Feedback Channel.
- `Latest Sample` and trend curves are generated from the selected unit's RF
  channel list. Invalid or stale PV data is shown explicitly.
- Logs and snapshots are stored under `runtime/irfel/real/` and are not source.

The defaults were migrated from the commissioned
`irfel_kly1_hv_feedback_20260624_2.json` configuration. Offline tests use a
test-only fake EPICS client; the operator application has no mock or VM mode.
