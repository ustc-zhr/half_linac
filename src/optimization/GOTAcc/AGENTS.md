# GOTAcc Agent Guide

## Project purpose
GOTAcc is a General Optimization Toolkit for Accelerator Applications.
It supports offline benchmark optimization, ASTRA/Elegant simulation optimization, and EPICS-based online accelerator optimization.

## Architecture rules
- Do not introduce legacy config formats.
- All task definitions should go through task_config().
- Policy behavior should be controlled by policy_kwargs / objective_policy_kwargs.
- Multi-objective tasks should use vector mode instead of weighted_sum unless explicitly requested.
- Offline and EPICS backends should share the same high-level task interface.

## Safety rules
- Never modify EPICS write PV logic without explicit user confirmation.
- Never add caput calls in background threads without safety checks.
- For online accelerator code, separate dry-run mode from live mode.
- Preserve snapshot/restore logic when modifying online control workflows.

## Verification
Before finishing a code change:
- run the smallest relevant test or import check;
- explain changed files;
- identify possible risks;
- do not claim online safety unless only offline tests were run.