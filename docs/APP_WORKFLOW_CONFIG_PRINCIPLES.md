# App Workflow Configuration Principles

These guidelines apply to `configs/machines/<machine>/apps/*.json`.

## Responsibilities

- Keep machine-native facts in `machine.json`: element identity, kind, order,
  logical channels, physical channel limits, and device properties such as
  per-FLAG image geometry.
- Keep backend PV names in `control_backends/*.json`.
- Use app JSON only for workflow policy that cannot be inferred reliably from
  the machine inventory: scan policy, sampling, solver settings, safety policy,
  model boundaries, physical device combinations, and recommended defaults.
- Prefer element ids plus logical channels over direct PV strings.

## Structure

- Put shared defaults at the outer level. A station, section, or preset should
  contain only its differences from those defaults.
- Define stations, sections, or presets before their `default_*` selection so
  the reader sees the available choices first.
- Use the same recommended structure in `_template`; reserve old shapes for
  parser compatibility, not for current examples.
- Keep related small objects on one line when they remain easy to compare and
  edit. Expand longer objects when compact formatting hides their structure.
- Do not introduce references or shared-default layers merely to remove a few
  repeated lines; local readability takes priority over minimum line count.

## Scan And Control Policy

- Use structured scan objects with descriptive names. Prefer `low`/`high` for
  ranges and `step`/`max_offset` for a relative cumulative correction limit;
  avoid an ambiguous bare `limit` field.
- State `mode` explicitly as `absolute` or `relative`.
- Keep `unit` explicit while the app configurations use this convention. It
  makes a scan understandable without consulting its channel resolver.
- Express genuine backend differences with a backend mapping, for example
  `{"vm": "K1", "real": "current"}`. Do not create a backend mapping for a
  value that is backend-independent.
- Treat logical-channel selection as explicit workflow policy when more than
  one writable physical quantity is valid. A future shared abstraction may
  replace app-specific fields only when the requirements are common.

## Limits

The effective writable range is the intersection of all applicable ranges:

```text
app scan or operation range
∩ app backend-specific ceiling
∩ machine.json limit for the selected logical channel
```

- Convert relative app ranges around the current value before intersecting
  them with absolute machine limits.
- For a multi-device knob, validate every resulting device target. The first
  device to reach its physical limit constrains the combined operation.
- Never apply a limit from a different logical channel or unit.
- If none of the three layers defines a limit, the operation is unbounded by
  configuration; write policy and runtime safety checks still apply.

## Derivation And Explicitness

- Omit a field only when its value is reliable and unambiguous to derive.
  Suitable examples include a zero target array, a label identical to an
  element id, or a station value inherited unchanged from the outer level.
- Keep operationally important intent explicit: control mode, model entrance
  and exit, write policy, stable GUI/report names, and any logical-channel
  choice that has multiple valid alternatives.
- Remove empty values when omission has exactly the same meaning, such as an
  empty optional calibration that the GUI can create later.
- An explicit empty list may remain when it documents an intentional workflow
  choice, such as a template section with no monitor BPMs.

## Compatibility And Verification

- Write active profiles in one recommended format. Parsers may continue to
  accept a reasonable legacy shape for migration.
- Configuration cleanup must not silently change physical values, real-machine
  write policy, PV selection, or model boundaries.
- Validate the affected machine profiles, focused app tests, static checks,
  and `git diff --check`. Do not start VM, IOC, GUI, or real-PV operations for
  configuration-only verification unless runtime testing is requested.
