# Energy Feedback

Profile-driven energy monitoring and feedback with a generic scalar-knob control
engine. The application contains no HALF-specific PV names; machine wiring and
hard limits come from the selected machine profile.

For HALF, the current profile uses the independent phase setpoint/readback pairs
of LLRF18 and LLRF19. The operator can edit target energy, `ki`, `alpha`, step
limit, action window, and session limit while feedback is off. Enabling feedback
locks a parameter snapshot for that run. Writes are serialized by the shared
energy-control lock. Each put is confirmed from an independent physical
readback using an active Channel Access get; it no
longer waits for a monitor callback or timestamp advance. A partial write or
physical mismatch rolls back only the actuators already attempted.

Readback supervision is deliberately split into three layers:

- Measurement and plotting use monitor-backed reads for efficient continuous
  display.
- Before every control action, closed-loop safety inputs use active reads. The
  machine HV limit (when configured), HV setpoint-to-readback mismatch, RF phase
  drift, beam presence, step limits, and session limits remain fail-closed.
- After a put, only physical arrival is polled. The value must enter the
  configured tolerance before timeout; source timestamps and monitor receipt
  times are not additional gates. Rollback uses the same active-readback check.

This policy is specific to continuous energy feedback. Ordered motion and scan
workflows elsewhere in the repository keep strict arrival checks because their
next step physically depends on the previous move completing.

The detailed audit, design alternatives, safety boundary, and follow-up topics
are recorded in
[`READBACK_SUPERVISION_REVIEW_20261106.md`](../../../docs/apps/energy_feedback/READBACK_SUPERVISION_REVIEW_20261106.md).

The IRFEL commissioning profile is self-contained and does not read
`hv_feedback.json`. HV setpoint/readback wiring is derived from the configured
Energy Feedback actuator; the profile directly names the two monitored RF phase
elements and the HV tracking tolerance. The application does not define an HV
operating range. Its only absolute HV limit is the
`MODULATOR_HV1.voltage_set` limit in `machine.json`; if that limit is absent,
there is no software absolute bound. Energy Feedback keeps only its intentional
RF phase-drift override. ACC1 amplitude and inter-channel amplitude-ratio
checks remain owned by HV Feedback and are not Energy Feedback write gates.

IRFEL FIR samples are identified by new BPM monitor events so a cached sample is
not counted twice. EPICS source timestamps are retained in diagnostics and logs
but do not gate BPM samples, closed-loop safety reads, or writes.

Automatic feedback retains the configured per-step and per-session limits
(`0.02 kV` and `±0.2 kV` in the current IRFEL profile). Manual targets do not use
those automatic-control limits; they require operator confirmation, obey the
machine limit when present, and must pass active physical-readback verification.

The raw IRFEL profile uses the compact single-actuator fields
`actuator.element` and `feedback`. The machine-profile normalizer expands them
into the existing `control_groups[] / actuators[]` runtime schema, including
the machine-native channels and unit. HALF and other canonical profiles continue
to use the generic multi-actuator schema directly.

For IRFEL, `feedback.ki` is the only gain source and
`feedback.max_step_kv` is the only default step-limit source. Fixed policy
values such as reference capture, valid-PV enforcement, HOLD-on-fault, operator
ranges, readback polling interval, command precision, and display metadata are
code defaults rather than operator choices. The explicit 5-second write timeout
remains in the profile for commissioning review.

The HALF workflow currently selects the `energy_spectrum_target` provider as a
commissioning source. It supplies the feedback input `energy` from `H:E` in
MeV. A future three-BPM provider will derive the same `energy` value by a
different measurement method; provider selection does not change the feedback
algorithm, controls, plots, or operator workflow.

The machine profile continues to define `H:E0`, `H:DeltaE`, and `H:DE` for ENY
and PRF02 because they are valid machine diagnostics, but the current feedback
provider does not read or expose them.

The default Feedback plot shows measured-minus-target error with
deadband/action-window limits and state markers, plus LLRF phase corrections
relative to the acquisition-start readbacks. Error Focus hides the phase panel;
Full Detail additionally shows measured and target energy history. Operators can
select 10-minute, 30-minute, 1-hour, or all in-memory history; rendering is
downsampled to at most 1600 points while CSV logging remains complete.

Every sample is written to the app runtime directory with source timestamps,
quality, resolved signal names, control parameters, commands, verification, and
rollback status.

The HALF target is local to this application. At acquisition start it is
initialized from the first valid measured energy and can then be edited while
feedback is off; the loop does not depend on `LINAC_ENERGY.setpoint`. LLRF phase
actuators are configured as 360-degree cyclic coordinates, so control,
verification, session displacement, and plots remain continuous across the
-180/180-degree boundary.

Run through the Control Room launcher or, after sourcing scripts/setup.sh,
run python3 src/apps/energy_feedback/main.py.
