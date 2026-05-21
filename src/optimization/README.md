# Optimization Integration

`src/optimization/` is now the connection layer between `half_linac` and the vendored `GOTAcc` project.

## Layout

- `mainOPT.py`
  Launcher-facing bridge that boots the GOTAcc GUI from inside this repo.
- `GOTAcc/`
  Vendored external optimization toolkit. Prefer syncing it as a subtree-like copy instead of mixing `half_linac`-specific patches throughout it.
- `configs/`
  `half_linac`-owned wrapper configs and future local integration code.

## Recommended Boundary

- Keep generic optimizer, GUI, and backend code inside `GOTAcc/`.
- Keep HALF-specific task definitions, launcher wrappers, and repo-local compatibility glue in `src/optimization/`.
- If GOTAcc is updated from its standalone repo, prefer replacing the vendored tree and then re-checking the bridge layer, instead of redoing local patches inside GOTAcc.

## Current HALF Entry Points

- GUI launcher entry: `python3 src/optimization/mainOPT.py`
- HALF task-config wrapper: `src/optimization/configs/para_half.py`

The wrapper config currently forwards to the vendored GOTAcc HALF config so the call site can stabilize first. If you later move the full HALF configuration out of GOTAcc, the `half_linac` side can keep using the same wrapper path.

## Runtime Artifacts

- GOTAcc manages its own GUI cache under `GOTAcc/.cache/`.
- Optimization histories, plots, and exported task files should be treated as runtime outputs, not source of truth.
