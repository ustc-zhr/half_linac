# AGENTS.md

## Scope
- Applies to `src/softIOC/` and its subdirectories.

## Structure
- `mainIOC.py` supervises JSON generation, substitution generation, and the IOC subprocess.
- `pv_server.py` owns PV initialization, JSON synchronization, and substitution file generation.
- `halflinac/db/*.template` are source templates; `halflinac.substitutions` is generated output.
- `halflinac/configure/RELEASE` is the editable EPICS path source; `halflinac/iocBoot/ioctarget/envPaths` is generated during IOC rebuilds.
- `halflinac/runMe` enters `iocBoot/ioctarget` and starts `st.cmd`.

## Editing Rules
- Prefer changing generators and supervisors instead of manually editing generated substitutions.
- Treat `iocBoot/ioctarget/envPaths` as generated output; prefer editing `configure/RELEASE` and rebuilding the IOC instead of hand-editing `envPaths`.
- Be careful with subprocess lifecycle changes because `mainIOC.py` is intended to supervise a long-running IOC.

## Verification
- Use `python3 -m compileall src/softIOC`.
- Use `bash scripts/build_ioc.sh` after changing `halflinac/configure/RELEASE` or `halflinac/iocBoot/ioctarget/envPaths`.
- IOC runtime verification requires EPICS and may start long-running processes; only do that when explicitly needed.
