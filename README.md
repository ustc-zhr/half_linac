# Accelerator HLA Platform

A configuration-driven platform for high-level applications across multiple
electron accelerators. The repository retains its historical name, `half_linac`.
HALF is defined by the machine profile in `configs/machines/half/`. IRFEL and
future machines use the same profile mechanism.

The platform includes the following components.

- EPICS softIOC and IOC management scripts
- An `elegant`-based virtual machine and model backend
- PyQt operator GUIs
- Applications for online optimization, orbit correction, beam diagnostics,
  emittance measurement, energy spectrum measurement, beam-based alignment
  (BBA), and dispersion correction

## Quick Start

Linux or WSL is recommended.

```bash
git clone https://github.com/ustc-zhr/half_linac.git
cd half_linac
bash scripts/install_env.sh --check
```

The default installation includes the Python `sdds` package and checks whether
the external `elegant` command is available. The virtual machine (VM), model
backend, and energy spectrum workflows depend on both. For system installation
instructions, see the [Elegant installation guide](docs/getting_started/ELEGANT_INSTALL.md).

If you already have a working Python or Conda setup, you can use the existing
entry points directly.

```bash
conda env create -f environment.yml
conda activate half_linac
bash scripts/check.sh
bash scripts/runMe
```

If you only need to connect to an existing IOC and will run no model
calculations, you can install the lightweight environment.

```bash
bash scripts/install_env.sh --core-only --check
```

Install the external `elegant` command-line program separately from the Python
environment. Running the VM, model backend, or related model calculations
locally requires both `elegant` and Python `sdds`. These dependencies are
generally optional if you only connect to an existing IOC on a real machine
and skip model calculations. Running the repository's softIOC requires EPICS
Base. See the installation documentation for detailed steps.

## Common Commands

```bash
bash scripts/check.sh              # Run fast static checks
bash scripts/runMe                 # Launch the Control Room GUI
bash scripts/start_vm.sh           # Start the virtual machine manager
bash scripts/start_ioc_manager.sh  # Start the Python IOC manager
bash scripts/configure_softioc.sh --epics-base "$EPICS_BASE"  # Configure and rebuild the softIOC for the first time
bash scripts/build_ioc.sh          # Rebuild the softIOC with the local configuration
```

The scripts locate the repository automatically. You only need to source the
setup script when you repeatedly run Python entry points by hand in the same
shell.

```bash
source scripts/setup.sh
```

## Repository Layout

- `configs/machines/` contains machine profiles for HALF, IRFEL, and other machines.
- `src/apps/` contains PyQt operator applications.
- `src/shared/` contains shared modules for machine profiles, models, runtime
  state, and process management.
- `src/optimization/` contains online optimization entry points and algorithm integrations.
- `src/softIOC/` contains the IOC project, PV synchronization, and the IOC manager.
- `src/virtual_machine/` contains lattice parsing, VM management, and the `elegant` run directory.
- `scripts/` contains the repository's common script entry points.
- `docs/` contains installation, configuration, design, and application guides.

## Documentation

- [Documentation index](docs/README.md)
- [Detailed installation and run instructions](docs/getting_started/SETUP_AND_RUN.md)
- [Archived full README](docs/archive/PROJECT_OVERVIEW_FULL.md)
- [Machine profile structure](configs/machines/README.md)
- [Repository-wide development and safety rules](AGENTS.md)

## Operational Guidelines

Use VM or offline workflows by default when reviewing and verifying code. Treat
generated files, logs, caches, and run outputs as runtime artifacts. Before
changing anything that affects writes to a real machine, PV naming, IOC database
generation, or VM/IOC synchronization, clarify the operational impact.
