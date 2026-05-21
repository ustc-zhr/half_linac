# AGENTS.md

## Scope
- Applies to `src/shared/` and its subdirectories.

## Purpose
- Keep cross-app helpers here when they are not owned by a single GUI, IOC, VM, or optimization subtree.

## Editing Rules
- Prefer small, dependency-light helpers that can be reused from multiple entrypoints.
- Avoid adding live-machine policy or app-specific UI logic here unless it is genuinely shared.

## Verification
- Use `python3 -m compileall src/shared`.
- If callers change too, run the narrowest relevant compile check on those subtrees as well.
