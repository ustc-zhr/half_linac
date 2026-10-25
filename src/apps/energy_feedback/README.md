# Energy Feedback

Profile-driven energy monitoring and feedback with a generic scalar-knob control
engine. The application contains no HALF-specific PV names; machine wiring and
hard limits come from the selected machine profile.

For HALF, the current profile uses the independent phase setpoint/readback pairs
of LLRF18 and LLRF19. The operator can edit target energy, `ki`, `alpha`, step
limit, action window, and session limit while feedback is off. Enabling feedback
locks a parameter snapshot for that run. Writes are serialized by the shared
energy-control lock, verified from independent readbacks, and rolled back as a
pair after a partial write or verification failure.

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
