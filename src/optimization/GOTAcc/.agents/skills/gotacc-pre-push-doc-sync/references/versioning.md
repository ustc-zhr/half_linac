# GOTAcc Versioning Rules

Use this reference when deciding whether to update `CHANGELOG.md`,
`src/gotacc/version.py`, `README.md`, or `pyproject.toml`.

## Source Of Truth

- The project version comes from `src/gotacc/version.py`.
- `pyproject.toml` uses `dynamic = ["version"]`, so it should not be edited for
  a normal version bump.

## Release-Facing File Rules

- Update `CHANGELOG.md` when the diff introduces user-visible functionality,
  bug fixes, packaging changes, or workflow changes worth calling out.
- Update `src/gotacc/version.py` only when the diff clearly warrants a release
  version bump.
- Update `README.md` when installation, supported optimizers, public workflows,
  entry points, examples, or GUI behavior materially changed.
- Update `pyproject.toml` only when packaging metadata changed:
  dependencies, optional extras, entry points, Python requirement, package
  data, classifiers, or project URLs.

## Version Bump Guide

- `patch`: user-visible bug fixes, small behavior fixes, packaging-only fixes,
  or documentation corrections tied to shipped behavior.
- `minor`: new optimizer support, new GUI workflow, new public configuration
  capability, new backend surface, or materially expanded documented feature
  set.
- `major`: intentional breaking change to public APIs, CLI behavior,
  configuration semantics, or packaging compatibility.

## Conservative Defaults

- Do not bump the version for internal refactors, local experiments, or partial
  work that is not clearly release-ready.
- If the diff is ambiguous and there is no clear release boundary, prefer
  leaving `src/gotacc/version.py` and `CHANGELOG.md` unchanged.
- If a README update is justified but a version bump is not, update only the
  README.

## Changelog Format

- Keep the newest entry at the top.
- Use the heading format `## X.Y.Z - YYYY-MM-DD`.
- Summarize changes as factual bullet points derived from the actual diff.
- Group related code changes into concise release notes instead of listing every
  touched file.

## README Scope

- Update only the sections affected by the diff.
- Keep claims aligned with implemented functionality.
- Avoid promising future work or undocumented assumptions.

## Pyproject Scope

- Do not edit `description`, `authors`, URLs, extras, or entry points unless
  the diff requires it.
- Never copy release notes into `pyproject.toml`.
