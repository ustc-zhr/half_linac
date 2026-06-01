# Jitter Analysis Agent Guide

## Scope
- Applies to `src/apps/jitter_analysis/` and its subdirectories.

## Project purpose
- `jitter_analysis` is a vendored EPICS online acquisition and analysis GUI maintained primarily outside `half_linac`.

## Architecture rules
- Within this repo, prefer the smallest compatibility or integration change needed to keep `jitter_analysis` running normally.
- Avoid large feature work, deep refactors, or behavior changes inside `jitter_analysis` unless the user explicitly asks for `jitter_analysis`-focused development here.
- Prefer launcher, wrapper, config, or environment fixes around the package over intrusive internal edits.

## Safety rules
- Do not change EPICS write/read behavior or live-machine assumptions without explicit user confirmation.
- Treat `runs/`, `saved_setups/`, caches, and local tool state as runtime artifacts unless the task explicitly targets them.

## Verification
- Before finishing a code change, run the smallest relevant import check or `python3 -m compileall` on the narrowest relevant subtree.
- Do not claim online safety unless only offline checks were required and completed.
