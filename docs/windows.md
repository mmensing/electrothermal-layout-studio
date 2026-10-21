# Native Windows build and verification

The application targets 64-bit Windows 11 with Python 3.12. Normal operation is
offline. The development environment and build must be created on Windows; an
executable generated on another operating system is not a Windows build.

Use the complete Windows ZIP from [GitHub Releases](https://github.com/mmensing/electrothermal-layout-studio/releases)
when a version is published. No historical pre-public binary is an MPL release of
this repository. Original source is MPL-2.0; the reviewed combined Windows bundle
uses the [GPL distribution route](licensing.md). Releases include corresponding
source, build information, notices and checksums. The owner-only
[maintainer procedure](maintainer-guide.md) governs publication.

Install the current supported **Microsoft Visual C++ x64 v14 Redistributable**
directly from Microsoft if it is absent. See [the runtime prerequisite](windows-runtime.md).
The application ZIP contains neither its DLLs nor its installer.

## Python runtime and verification scope

Hosted Windows workflows explicitly use **CPython 3.12.10 x64**. The official
[Actions Python manifest](https://github.com/actions/python-versions/blob/main/versions-manifest.json)
provides that Windows artifact; its later 3.12.14 entry has no Windows download.
The reviewed public Windows profile uses **3.12.14**, pinned to the public
python-build-standalone archive and source recipe in [source provenance](compliance-sources.md). These are
separate runtime environments, not identical patch-version evidence. The locked
Windows wheels target the same CPython 3.12 ABI; exact-commit hosted tests establish
actual CI compatibility. The CI artifact pin is not a claim that 3.12.10 includes
later Python security fixes. Runtime updates need their own reviewed validation.

## Build from source

In PowerShell, from the repository root:

```powershell
py -3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install --require-hashes --only-binary=:all: -r requirements-windows-py312.lock
.venv/Scripts/python.exe -m pip install --no-build-isolation --no-deps -e .
.venv/Scripts/python.exe scripts/prepare_qt_runtime.py
.venv/Scripts/python.exe -m pytest --basetemp=.pytest_build
.venv/Scripts/python.exe scripts/build_windows.py
```

Build the [source-patched Qt runtime](qt-runtime.md) with CMake 3.26+ and the x64 MSVC C++ build tools / Windows SDK before desktop tests.
The lock describes the original bootstrap wheels; the source-built Shiboken local patch is then recorded separately.

The lock file records the complete bootstrap dependency set.
It includes build and test tools; the frozen application contains runtime
dependencies discovered from the actual imports. The build records installed
versions and source hashes in `BUILD_MANIFEST.json`.

Output files are:

- `dist/ElectrothermalLayoutStudio/ElectrothermalLayoutStudio.exe`: windowed GUI.
- `dist/ElectrothermalLayoutStudio/etlayout.exe`: command-line interface with a console.
- `dist/ElectrothermalLayoutStudio-<version>-windows-x64.zip`: complete standalone folder.
- `dist/etlayout-<version>-source.zip`: exact application working-tree source and rebuild scripts.
- `dist/SHA256SUMS.json`: archive hashes.
- `build/verification/`: native startup, import, solve and GUI smoke evidence.

Extract the entire ZIP before starting the application. Keep `_internal` next to
both executables. The adjacent `examples`, `docs`, `licenses`, `LICENSE` and
`THIRD_PARTY_NOTICES.md` are part of the distribution. Do not copy only the EXE.

The executables under `build/pyinstaller-windows/` are intermediate build outputs,
not runnable standalone application folders. Starting one there can report a
missing `_internal/python312.dll`. Launch the GUI under
`dist/ElectrothermalLayoutStudio/` or extract the complete release ZIP instead.
Keep the entire application folder, including `_internal`, together.
Use short local paths for extraction, projects and build/test temporary files;
paths beyond the traditional Windows path-length limit can fail in this profile.

```powershell
dist/ElectrothermalLayoutStudio/etlayout.exe --version
dist/ElectrothermalLayoutStudio/etlayout.exe build-info
dist/ElectrothermalLayoutStudio/etlayout.exe capabilities
dist/ElectrothermalLayoutStudio/etlayout.exe solve examples/E1_straight.etl --no-plots --output results
dist/ElectrothermalLayoutStudio/ElectrothermalLayoutStudio.exe gui examples/E7_hemt.etl
```

The GUI executable also accepts a single `.etl` path, making Explorer's
"Open with" association possible. The build does not change file associations,
install system services or modify machine-wide settings. Application settings,
recovery files and editable materials are stored in the normal user application
data locations. Source development can use `python -m etlayout` and
`python -m etlayout.app.main`.

**Edit → Execution settings…** controls the total CPU budget and maximum parallel study
workers. The default budget is four logical CPUs, reduced when fewer are available; the worker
limit cannot exceed it. These settings apply to future runs and persist on this computer.
The console offers `--cpu-threads` and `--study-workers`. Supported native numerical thread
pools and larger FEM assembly batches share this budget across the spawned study workers.
Small batches, SuperLU factorization and some meshing/geometry steps remain serial, so CPU
activity varies by phase and more CPUs do not guarantee a faster individual solve. Parallel
studies also consume more memory. The distribution uses CPU execution and requires no GPU runtime.

The 0.4.1 native protocol requires **27 distinct checks**, including a frozen
`build-info` comparison with the exact source stamp and an actual default eager
File Open journey. The latter checks recorded fields, read-only history/return,
mesh-study dialog cancellation and compact example-browser opening without an
automatic solve. Readable native scientific glyphs and screenshots are required.
These checks supplement source tests; they do not certify a clean machine.

## Deployment choice

Qt's `pyside6-deploy` wraps Nuitka and documents `dumpbin` from MSVC as a Windows
dependency. That compiler tooling was absent on the validation host. It remains
a future packaging option, but adding an untested compiler toolchain would not
improve this prototype's reproducibility. See the official
[Qt deployment documentation](https://doc.qt.io/qtforpython-6/deployment/deployment-pyside6-deploy.html).

The build uses PyInstaller 6.22.3 in its supported folder distribution mode. A
checked-in specification lists dynamic native dependencies and resources, while
PyInstaller's Qt hook collects the Windows platform plugin. The specification
creates GUI and console executables sharing the same libraries. See
[PyInstaller's specification documentation](https://pyinstaller.org/en/stable/spec-files.html).

Explicit native dependency handling includes:

- The reviewed source-built Gmsh DLL, selected with paired `--gmsh-library` and
  `--native-build-manifest` inputs. Stock-wheel local candidates are explicitly
  ineligible for that release profile. The DLL sits beside frozen `gmsh.py`.
- KLayout database DLLs and format plugin DLLs for GDSII and OASIS import.
- Qt Widgets, SVG, image plugins and the `qwindows` platform plugin.
- NumPy/SciPy native numerical libraries, Shapely/GEOS and matplotlib's Agg,
  SVG and PDF export backends.
- `threadpoolctl` for native numerical thread limits and `psutil` for CPU availability and
  cancellation of the coordinator's process tree, including nested study workers.
- `multiprocessing.freeze_support()` before any dispatch, required by frozen Windows workers.

The application uses native Widgets. The specification excludes WebEngine,
QML/Quick, PDF and multimedia Python bindings and unused Mesa software OpenGL, but dependency collection can
still retain additional Qt DLLs required by collected plugins. Verify the final
module inventory when assembling dependency corresponding sources; Python import
exclusions alone do not prove native DLL exclusions. UPX and one-file extraction
are disabled.

## What the automated native smoke test proves

`scripts/build_windows.py` runs the generated console executable with `--version`,
imports both GDSII and OASIS examples, solves E1 using its bundled numerical and
meshing libraries, exports its numerical data and matplotlib field figures,
starts the GUI using the native Windows Qt plugin, saves a
rendered frame, and starts the separate windowed executable. It also runs a real
E1 simulation in the windowed executable's spawned GUI worker, verifying frozen
`multiprocessing` dispatch and IPC with the numerical result. It removes development
Python and venv-specific environment variables from these child processes,
and limits their DLL search PATH to Windows system directories. The build uses
the same isolation plus its selected Python environment: this avoids accidentally
bundling incompatible DLLs from unrelated tools such as Poppler's ICU library.
Every command, exit status and duration is saved in the manifest and verification
logs. The test uses the actual EXEs rather than source Python entry points.
The current matrix additionally solves multilayer E19, resumes and replays its
saved transient, and runs a time-step study through a windowed coordinator with
nested spawn workers. A Qt heartbeat must keep advancing, the CPU settings dialog
must accept its detected defaults, and the bundled offline guide must resolve.
The power-routing matrix also runs a real four-cell coupled analysis, passive
multiport extraction, an OASIS routing edit and a one-evaluation experiment
including baseline refinement through the frozen CLI. That bounded smoke run
tests packaged dispatch and dependencies; the complete source tests and worked
demonstrations separately exercise finalist selection and verified exports.
Another fresh frozen process reopens the embedded project and resumes the
completed experiment, requiring an unchanged history and a feasible refined
baseline rather than checking the command's exit code alone.
Both executables carry the same build-time source fingerprint, so cached
experiments cannot be resumed by different code merely sharing a version string.
Power-example checks generate compact GaN/SiC projects, solve their configured
loads and open the engineering reference projects without running them. The
windowed editor check performs a real polygon drag, verifies an immutable source
copy and unchanged import, exercises Undo/Redo and saves an embedded edited
project. A separate frozen CLI process solves that saved project. These are
packaged-build checks, distinct from source/native acceptance captures.
Historical pre-public frozen verification included a 5 A engineering E21 solve
with four actual assembly workers. See [validation](validation.md) for the
difference between historical and current source/native/build evidence. Any
future public candidate needs a fresh build from its exact audited public commit.
After archiving, it CRC-checks the ZIP and extracts it into a fresh temporary
directory outside the checkout. Seven extracted-payload checks exercise the version,
implementation receipt, GDSII/OASIS imports, E1 numerical solve, the windowed GUI and its spawned worker.
The recipient-like execution uses only Windows system directories in PATH,
removes development Python/Qt/coverage variables, and uses the native Windows Qt
plugin. E1 must converge with positive resistance, and the worker must produce a
converged result while the Qt heartbeat advances. The temporary payload is removed
afterwards; logs, a GUI capture, numerical metrics and isolation scope remain in
`build/verification/archive-results.json` and adjacent artifacts.

This proves native startup and the exercised workflow **on the build host**.
A Windows Sandbox/VM with no development tools is still needed for a clean-machine
compatibility claim. Binaries are unsigned; code signing and installer integration
are separate release work. Hosted CI is additional source-installation/test evidence;
select the relevant branch and commit in the
[Windows validation workflow](https://github.com/mmensing/electrothermal-layout-studio/actions/workflows/ci.yml).

`--skip-build` re-tests an existing folder; `--no-archive` avoids compressing
development iterations; `--skip-tests` explicitly records verification as not
performed. Output paths are restricted to children of the repository. The script
does not recursively delete arbitrary directories.

## Source and licensing

Eligible original project source is MPL-2.0. Dependencies retain their own
licenses, including GPL and LGPL obligations and specifically limited exceptions.
The entire frozen application is not MPL-only. Preserve the unmodified license,
third-party notices, corresponding sources and build information required for
the exact shipped components. An application-source ZIP alone is insufficient.
See [licensing](licensing.md) and [third-party notices](../THIRD_PARTY_NOTICES.md).

The [Windows candidate workflow](../.github/workflows/windows-package.yml) is
owner-requested on main. It executes the actual frozen GUI/CLI and extracted
payload; it uploads only explicit validation receipts and screenshots, not binaries
or ZIPs. Public binary publication remains blocked until the compliance gate is
satisfied. Public source CI runs independently with read-only permissions, using
locked dependencies. No tag push builds or publishes a release automatically.

A future owner-reviewed binary must be rebuilt from tested public source under an
unambiguous version, with complete vetted notices/source materials and matching
hashes. Follow [the maintainer procedure](maintainer-guide.md). Do not reuse old
pre-public binaries or private artifact URLs.


Source ZIPs from Git checkouts contain indexed maintained files, including
`.gitattributes`, and exclude generated `*.egg-info` metadata. Untracked local
notes, ignored build files and stale package versions are not source inputs.
Tracked modifications remain identified by exact per-file hashes; owner release
still requires a clean tested main. Unpacked source archives without `.git` use
the maintained source roots with the same generated-metadata exclusion.
