---
name: gotacc-pre-push-doc-sync
description: Update GOTAcc release-facing files before push. Use when code, optimizer, GUI, config, packaging, dependency, or workflow changes may require synchronizing CHANGELOG.md, README.md, src/gotacc/version.py, and pyproject.toml before git push, release preparation, or tagging.
---

# GOTAcc Pre-Push Doc Sync

Inspect the current repository diff and keep release-facing files aligned with actual changes. Prefer minimal edits and leave release files untouched when the diff does not justify them.

## Workflow

1. Inspect the working tree before editing.
   Run `git status --short`, `git diff --stat`, and targeted diffs for changed files.
2. Read [references/versioning.md](references/versioning.md) before deciding whether to update changelog or version files.
3. Update only the release-facing files justified by the diff:
   - `CHANGELOG.md` for user-visible features, fixes, packaging changes, or workflow changes.
   - `src/gotacc/version.py` when the diff clearly warrants a release version bump.
   - `README.md` when installation, supported optimizers, workflows, CLI or GUI behavior, or examples materially changed.
   - `pyproject.toml` only when packaging metadata, dependencies, optional extras, console scripts, or Python requirements changed.
4. Preserve existing formatting and section structure.
   Keep changelog entries newest-first and use the existing heading style `## X.Y.Z - YYYY-MM-DD`.
5. If the current diff is not release-facing, leave these files unchanged and state that no sync was needed.

## GOTAcc Rules

- Treat `src/gotacc/version.py` as the version source of truth.
- Do not try to insert a static project version into `pyproject.toml`; this repository uses `dynamic = ["version"]`.
- Keep changelog language factual and tied to the actual diff.
- Do not document speculative capabilities, partial experiments, or TODOs.
- Update only the README sections affected by the diff instead of rewriting unrelated sections.
- Do not change authors, URLs, dependency groups, or entry points unless the diff requires it.

## Validation

- Re-open each edited file and check cross-file consistency.
- Confirm README mentions only features or workflows supported by the current diff.
- Confirm changelog heading and `src/gotacc/version.py` agree when a version bump is made.
- Confirm `pyproject.toml` changed only for packaging metadata, not for release notes.
