# GOTAcc TODO

## Deferred Policy Improvements

The current Policy workflow intentionally favors a small, validated GUI over a
general-purpose rule language. The following ideas are deferred until concrete
machine use cases require them:

- Generate Policy Editor fields from Registry-owned schemas.
- Support additional declarative metrics, actions, or nested condition groups.
- Add Policy Template revisions, provenance comparison, and reset-to-template
  workflows.
- Add a standalone Policy simulation or sample-data testing page.
- Add recommended parameter ranges or automatic engineering-unit discovery.
- Add complex policy priorities, dependencies, or conflict-resolution rules.
- Read live PV values directly from the Policy Editor.
- Allow arbitrary Python expressions or executable user policy code.

Any future online-facing implementation must preserve dry-run/live separation,
snapshot and restore behavior, and the existing EPICS write safety checks.
