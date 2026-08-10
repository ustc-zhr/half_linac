# elegant And SDDS Installation

This note describes how to install the external `elegant` executable and the
SDDS dependencies needed by `half_linac` model-backend workflows.

Official software entrypoint:

- APS Accelerator Operations and Physics Software:
  <https://www.aps.anl.gov/Accelerator-Operations-Physics/Software>
- Python `sdds` conda package:
  <https://anaconda.org/soliday/sdds>

## What Needs Installing

There are three related but separate pieces:

- `elegant`: external command-line program used by VM and model calculations.
- SDDS Toolkit: command-line/library support distributed with APS SDDS tools.
- Python `sdds`: Python module used by this repo to read elegant output files
  such as `.mat` and `.twi`.

The repo expects the shell command below to work:

```bash
elegant
```

The Python environment expects this import to work:

```bash
python3 -c "import sdds; print('sdds OK')"
```

## Recommended Control-Room Path

If the control-room machine already has a shared software stack or environment
modules, prefer that first:

```bash
module avail elegant
module load elegant
which elegant
elegant
```

If `module` is not available, ask the local controls/IT maintainer whether APS
SDDS/elegant is already installed under a shared path such as `/opt`, `/usr/local`,
or an accelerator software mount. If it is installed but not on `PATH`, add the
binary directory:

```bash
export PATH=/path/to/elegant/bin:$PATH
which elegant
```

Put the final `PATH` setup in the control-room environment loader, not in repo
source code.

## Install From APS Packages

The APS Software page publishes prepackaged SDDS Toolkit and elegant builds. Pick
the packages matching the host OS as closely as possible.

Check the host:

```bash
cat /etc/os-release
uname -m
```

Download:

- `SDDSToolKit-...<os>...x86_64.rpm`
- `elegant-...<os>...x86_64.rpm`

The `elegant` packages may be split by MPI implementation, for example `mpich`
or `openmpi`. For this repo we call the normal `elegant` executable, not
`Pelegant`; choose the package matching the MPI runtime already supported on the
control-room machine. If unsure, ask the local system maintainer.

### RHEL/Fedora/openSUSE

Use the native RPM package manager:

```bash
sudo dnf install ./SDDSToolKit-*.rpm
sudo dnf install ./elegant-*.rpm
```

On older RHEL/CentOS systems:

```bash
sudo yum install ./SDDSToolKit-*.rpm
sudo yum install ./elegant-*.rpm
```

### Ubuntu/Debian

The APS page lists Ubuntu/Debian builds as RPM files and notes `alien -i` for
those systems. Install `alien`, then convert/install:

```bash
sudo apt update
sudo apt install alien
sudo alien -i SDDSToolKit-*.rpm
sudo alien -i elegant-*.rpm
```

If the control-room machine has stricter package policies, ask the maintainer to
install these under a shared software prefix instead of installing system-wide.

## Install Python sdds

Inside the `half_linac` conda environment:

```bash
conda activate half_linac
conda install soliday::sdds
```

`environment.yml` intentionally does not declare `soliday::sdds`, so the base
control-room environment can be solved without the extra channel. The command
above has been verified in the control room and should be run after creating the
main `half_linac` environment.

Verify:

```bash
python3 - <<'PY'
import sdds
print("Python sdds OK:", getattr(sdds, "__file__", "built-in"))
PY
```

## Final Verification

Run these checks before using model-dependent workflows:

```bash
which elegant
elegant
python3 -c "import sdds; print('sdds OK')"
```

Then check the repo:

```bash
bash scripts/check.sh
```

`scripts/check.sh` validates the profile/configuration side. Full model runtime
verification still requires `elegant` to be callable when using GUI actions such
as `Update eta`, `Update optics`, `emit_measure` Twiss/recalculate, and VM
elegant workflows.

## Troubleshooting

- `which elegant` prints nothing:
  `elegant` is not on `PATH`. Load the local module or add its bin directory.
- `elegant: command not found`:
  Same as above, or package installation failed.
- MPI library errors:
  The installed `elegant` package likely does not match the available MPI
  runtime. Use the package variant matching local `mpich` or `openmpi`.
- `import sdds` fails:
  Activate the correct conda environment and run `conda install soliday::sdds`.
- GUI opens but `Update eta`/`Update optics` fails:
  Check both `which elegant` and Python `import sdds` from the same shell used to
  start the GUI.
