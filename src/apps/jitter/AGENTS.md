# AGENTS.md

## Scope
- Applies to `src/apps/jitter/` except for `src/apps/jitter/jitter_analysis/`,
  which is externally maintained.

## Integration Rules
- Keep `jitter_analysis/` as a direct checkout of
  `https://github.com/ustc-zhr/jitter_analysis`.
- Prefer wrapper, launcher, environment, or compatibility changes in this
  outer directory instead of editing files inside `jitter_analysis/`.
- If upstream changes are needed, make them in the external repository first,
  then update this checkout.

## Verification
- For wrapper changes, run `python3 -m compileall src/apps/jitter`.
- For upstream app changes, run tests from `src/apps/jitter/jitter_analysis`.

