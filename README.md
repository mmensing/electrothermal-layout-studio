# Electrothermal Layout Studio

[![CI checks](https://github.com/mmensing/electrothermal-layout-studio/actions/workflows/ci.yml/badge.svg)](https://github.com/mmensing/electrothermal-layout-studio/actions/workflows/ci.yml)

A Windows desktop and Python/CLI application for electrical, thermal and coupled
electrothermal analysis of GDSII/OASIS layouts. It uses thickness-averaged 2.5D
finite elements to explore current sharing, interconnect loss, thermal paths and
pulsed heating in explicitly configured physical stacks.

**0.4.0.dev0 source edition · research prototype · MPL-2.0 original project source.**
Windows 11 x64 / Python 3.12 is the validated platform.
The Python namespace and command remain `etlayout`; portable `.etl` projects retain
their existing format. This is an untagged public source edition. No public
Windows binary or public release is supplied by this initial publication.

![Actual application results view](docs/images/desktop/results.png)

## What it does

- Import hierarchical layouts and define physical layers, contacts, finite-feed
  ports, material laws, thermal reservoirs and measurement regions.
- Run steady electrical/thermal/coupled calculations and transient heat flow with
  quasi-static electrical feedback, pulse/PWL inputs and saved-state continuation.
- Inspect physical layers, exact-support measurements, probes, profiles,
  synchronized maps, comparisons and conservation diagnostics.
- Edit polygon and hole vertices in a project-owned copy; drag or enter coordinates,
  insert/delete points and move polygons without overwriting the imported file.
- Compare bounded routing alternatives at equal delivered service and review local
  mesh-refinement suggestions before applying them.
- Open 24 reference projects: small analytical benchmarks plus larger synthetic
  RDL, long-finger/multi-bank GaN and SiC on-state interconnect examples.

The seven-stage desktop workflow keeps a large canvas, contextual inspector and
Issues/Jobs/History drawer. Simulation runs in cancellable background processes.
Execution settings default to at most four available logical CPUs; larger FEM
assembly and independent studies can share that budget. Sparse direct
factorization and some geometry/reporting work remain serial. There is no GPU backend.

## Start from source on Windows

Use a clean Windows 11 x64 environment with CPython 3.12. Other platforms have not
received equivalent desktop or deployment validation. In PowerShell:

```powershell
git clone https://github.com/mmensing/electrothermal-layout-studio.git
cd electrothermal-layout-studio
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes --only-binary=:all: -r requirements-windows-py312.lock
.\.venv\Scripts\python.exe -m pip install --no-build-isolation --no-deps -e .
.\.venv\Scripts\python.exe -m etlayout gui
```

Dependency installation needs Internet access; normal calculations run offline.
The lock includes development/build tools as well as runtime dependencies; it is
not a claim that every installed package is shipped. See [Windows setup](docs/windows.md)
and [licensing](docs/licensing.md) before building or redistributing executables.

Start with **File → Ready-to-run examples → E1 · Straight conductor**, then follow
[the guided workflow](docs/desktop-workflow.md). Large reference examples are
clearly labeled and do not run automatically when opened.

## A first calculation

```powershell
.\.venv\Scripts\python.exe -m etlayout inspect examples/E1_straight.oas
.\.venv\Scripts\python.exe -m etlayout solve examples/E1_straight.etl --no-plots --output results/straight
```

The configured copper strip has an analytical resistance of approximately 0.084 Ω,
current 0.238095 A and Joule loss 4.76190 mW. Compare residuals, balance and mesh
sensitivity as well as convergence. The [worked calculation](docs/handbook/first-calculation.md)
explains the inputs and independent reference.

```python
from etlayout.io.project import load_project, save_project
from etlayout.simulation.runner import run_project

project, _ = load_project('examples/E1_straight.etl')
result = run_project(project)
print(result.converged, result.metrics)
save_project(project, 'straight-result.etl', result)
```

Portable ETL archives embed layouts, material snapshots, configuration identity
and optional fields. Numerical inputs/exports remain SI; maps and readouts use
engineering units, including mA/µm² for volumetric current density and mA/µm for
sheet current. GUI and CLI use the same backend.

## Model limits

This is a resistive, thickness-averaged model, not a calibrated transistor/device
rating tool. It does not model full 3D spreading, drift diffusion, high-field
velocity saturation, breakdown, trapping, electrical capacitance/inductance or
switching overshoot. GaN examples use a local-gate-bias sheet law; SiC examples
are explicitly fixed-on-state interconnect models with area-specific resistance.
A drawn insulated gate does not create missing semiconductor physics.

Synthetic examples and material seeds require process calibration. Corner peaks
can depend strongly on the mesh: inspect exact physical averages, conservation
and refinement before ranking alternatives. Generated, converged and mesh-verified
cases are different states. [Physics](docs/handbook/model-and-physics.md) and
[scientific acceptance](docs/scientific-acceptance.md) state these limits precisely.

## Documentation and contribution

- [Handbook](docs/handbook/README.md): physics, setup, worked examples and pulses.
- [Example gallery](docs/examples.md) and [power-device examples](docs/power-examples.md).
- [Layout editor](docs/layout-editor.md), [local refinement](docs/local-refinement.md)
  and [power-routing studies](docs/POWER_ROUTING.md).
- [Developer guide](docs/developer-guide.md), [validation](docs/validation.md),
  [architecture](docs/architecture.md) and [change history](CHANGELOG.md).
- [Contributing](CONTRIBUTING.md), [security reporting](SECURITY.md) and
  [maintainer procedure](docs/maintainer-guide.md).

Original project material is licensed under the unmodified [MPL-2.0](LICENSE).
Third-party components retain their own licenses and exceptions; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and [the licensing guide](docs/licensing.md).
MPL does not make a combined frozen application MPL-only. Public binary publication
requires a separate corresponding-source and redistribution review.
