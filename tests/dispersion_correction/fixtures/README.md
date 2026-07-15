# Regression Configuration Fixtures

These files preserve the standalone package's JSON/YAML parser and workflow
regression cases. Control Room runtime configuration comes from
`configs/machines/<machine>/apps/dispersion_correction.json`; these PV maps are
never selected automatically.

- `achromat_mvp.example.json`: generic offline regression configuration.
- `irfel_achromat.mock.json`: offline IRFEL model using real device names.
- `irfel_achromat.json`: legacy IRFEL EPICS adapter regression configuration.

The matching YAML files are compatibility mirrors. New fields and operational
changes must be applied to JSON first. Automated tests verify that each YAML
mirror parses to the same configuration model as its JSON source.

JSON loading uses only the Python standard library. YAML loading requires the
optional `PyYAML` dependency, available through the package's `yaml` extra.
