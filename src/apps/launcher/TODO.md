# Launcher TODO

- Implement reliable one-click foregrounding for already-running managed apps without showing the `Open / Stop / Cancel` dialog. Current blockers are platform-specific focus rules in WSLg/Wayland and the lack of a robust activation token or IPC path.
- Improve machine/app configuration access for early real-machine commissioning. Start with a lightweight Control Room entry for the active machine configuration, offline profile validation, safe backup/rollback, and named presets. Reassess actual onsite edit frequency before adding app-specific forms; do not build a general-purpose JSON editor by default, and keep PV mappings, write policy, commissioning status, and safety limits protected.
