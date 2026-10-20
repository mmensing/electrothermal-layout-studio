# Electrothermal Layout Studio

[![CI checks](https://github.com/mmensing/electrothermal-layout-studio/actions/workflows/ci.yml/badge.svg)](https://github.com/mmensing/electrothermal-layout-studio/actions/workflows/ci.yml)

**Explore where current flows, where power is lost, and how heat moves through your layout.**

Electrothermal Layout Studio turns a GDSII or OASIS layout into an interactive
experiment. Build a physical layer stack, connect electrical feeds and thermal
reservoirs, and follow the result from individual contacts to the complete device.
Use it to investigate a crowded bend, unequal via loading, heat spreading between
banks, or the temperature rise during a pulse—all in one Windows desktop workspace.

The large layout canvas stays beside a contextual inspector and a numbered
workflow. Start with a ready-to-run example, inspect its assumptions, change one
input, and compare saved runs. You can use the complete desktop workflow without
writing Python or editing JSON.

![E11 heater temperature rise in the running application, with the layer and quantity inspector visible](docs/images/showcase/electrothermal-temperature.png)

*E11 in the actual desktop: heater temperature rise above the configured 300 K
backside reservoir, with 20 mA drive and its documented 2 mW prescribed heat load.
This run is solver-converged; mesh verification is separate. The examples are
synthetic engineering demonstrations, not device ratings.*

**[Explore the feature catalog](docs/features.md) · [Choose a guided example](docs/examples.md) · [Follow the desktop guide](docs/desktop-workflow.md)**

## From layout to an engineering question

![Seven stages: layout and region, layers and materials, contacts and connections, operating conditions, mesh and checks, run, results](docs/images/workflow-overview.svg)

Choose **Analysis type** in the header, then work through the seven stages. The
**Issues** drawer explains missing prerequisites and offers **Locate** or **Fix**
where available. **Jobs** shows progress and cancellation; **History** keeps runs
attached to the configuration that produced them.

At **Results**, choose the layer, quantity, region and time before interpreting a
map. Change colormaps and ranges to answer a display question, then use physical
averages, conservation and mesh studies to assess the calculation. Display changes
do not change your physical setup.

## What can you investigate?

| Capability | In the desktop | A useful first experiment |
| --- | --- | --- |
| **Layout and stack** | Browse hierarchy; define separate simulation, view and measurement regions; map masks to materials, thicknesses and interfaces. | [Trace heat through a multilayer stack](docs/examples.md#e10). |
| **Safe layout editing** | Drag polygon or hole vertices, enter coordinates, insert/delete points and undo changes in a project-owned copy. | [Edit a layout without overwriting its source](docs/layout-editor.md). |
| **DC and electrothermal analysis** | Run isothermal electrical, steady thermal or coupled calculations with finite contacts, feeds and thermal paths. | [Find current crowding at a bend](docs/examples.md#e2). |
| **Pulses and thermal memory** | Set pulse/PWL drives, initialization and saved-state continuation; inspect time-dependent maps and histories. | [Follow heating and cooldown](docs/examples.md#e17). |
| **Results you can interrogate** | Select layers/regions, probe fields, draw profiles, integrate branch current and synchronize comparison maps. Read E, K, metal J, heat and temperature over identical physical support. | [Follow a hotspot through its electrical and thermal causes](docs/engineering-results.md). |
| **Thermal limits and engineering evidence** | Set global/layer/region temperature limits; retain failed fields, stop a transient when requested, and separate convergence from conservation, mesh/time evidence and calibration. | [Set a maximum operating temperature](docs/temperature-limits.md). |
| **Physical local meshing** | Review corner-radius, contact-transfer, gate and via sizing, optional bounded flux guidance and graded refinement; inspect three-level per-metric confidence. | [Resolve a physical feature](docs/local-meshing.md). |
| **Thermal paths and source depth** | Add thermal-only passivation/dielectric films and choose interface/device-loss deposition without adding artificial power. | [Compare thermal-stack assumptions](docs/thermal-stacks.md). |
| **Power-interconnect analysis** | Inspect device groups, via/port loading, contact loss, source-potential spread and passive multiport exports. | [Explore GaN and SiC feeds](docs/power-examples.md). |
| **Studies and constrained routing** | Run parameter sweeps, mesh/time studies and bounded routing experiments; check equal service and mesh-verified finalists. | [Compare alternatives fairly](docs/POWER_ROUTING.md). |
| **Portable work and reporting** | Choose PNG/PDF/SVG figures or standalone reports, selected CSV/NPZ/VTU data, or a complete ETL experiment with embedded layout and retained runs. | [Save a result you can reopen](docs/handbook/first-calculation.md). |
| **On-demand run history** | Opt in to schema-3 metadata-first opening, inspect exact saved frames in background workers, and manage a bounded array cache while complete saves retain unloaded runs. | [Choose archive and history settings](docs/user-guide.md#on-demand-history-for-schema-3-projects). |

[The full feature catalog](docs/features.md) explains where to find each tool,
what it produces and what its result does—and does not—establish.

<table>
<tr>
<td width="50%"><a href="docs/images/showcase/current-crowding.png"><img src="docs/images/showcase/current-crowding.png" alt="Actual current-density result showing spatial current loading in a layout" /></a></td>
<td width="50%"><a href="docs/images/showcase/pulsed-operation.png"><img src="docs/images/showcase/pulsed-operation.png" alt="Actual E18 time history showing heating and cooling between current pulses" /></a></td>
</tr>
<tr>
<td><strong>Follow the electrical path.</strong> E2 shows corner crowding at 0.1 A. Inspect local loading and distinguish sheet current from volumetric current density.</td>
<td><strong>Watch the pulse response.</strong> E18 shows peak temperature during heating and cooldown. Inspect the accepted times and refine the time step before comparing peaks.</td>
</tr>
</table>

Open either image at full size to read its controls and scale. The [example
walkthroughs](docs/examples.md) explain how to reach and interpret these views.

## Your first GUI calculation

After installation, choose **File → Browse example library…**, search **E1**,
and open **Straight conductor** with **Original reference**. Its configuration is
already complete. The categorized **Ready-to-run examples** menu remains a shortcut
to the original references.

1. Select **DC electrical** in the header. Visit **Layers & Materials** and
   **Contacts & Connections** to see the copper strip and its two terminals.
2. At **Mesh & Checks**, press **F6** to validate, then **F7** to generate the mesh.
   Read the Issues drawer before continuing.
3. Open **Run** and press **F5**. At **Results**, choose layer **metal** and
   **Potential**; expect a linear rise along the strip. Switch to **Volumetric
   current density** to see its uniform loading.
4. Compare the reported resistance with **0.084 Ω**, current with **0.238095 A**,
   and Joule loss with **4.76190 mW** at the configured 20 mV and 300 K.
5. Use **File → Save project as…** to keep your own project and result. Then try
   [a bend, a via ladder or a pulsed heater](docs/examples.md).

The [first-calculation guide](docs/handbook/first-calculation.md) explains the
independent analytical check. All **40 reference projects** remain available;
opening or generating an example never starts a calculation. Large cases are
identified before execution.

## Find your next experiment

The [example library](docs/examples.md) brings early references and expanded
structures together by question. Numbers are stable IDs, not a difficulty order.
Follow one of the [six GUI learning paths](docs/example-paths.md):

| Engineering question | Start small | Continue toward a device or study |
| --- | --- | --- |
| Resistance and current sharing | E1 → E2/E3 | E25/E26 coupons; E28/E32 networks |
| Contacts and sensing | E6 → E27 | E27 finite-contact TLM study; E14/E31 sensing |
| Multilayer routing and packages | E4 → E5 | E12/E30 via routes; E20 RDL and E40 interposer |
| Heat paths and thermal interaction | E10 → E11 | E33 anchors, E34 sensor, E35 die attachment |
| Pulses and thermal storage | E17 → E18/E19 | E36 constriction, peak/final fields and time-step studies |
| Semiconductor interconnects | E37 voltage-controlled GaN; E39 lower-current SiC | E16 → E21 → E38/E22 GaN; E23/E24 SiC collection |

In **Browse example library…**, inspect **Size preset**, **Routing variant**,
**Thermal boundary** and **Operating scenario** where supported. A size preset
changes scale and its declared operating point; a routing variant changes
supported feed geometry; a scenario selects a named experiment. The browser
recommends **Voltage-controlled GaN coupon** for E37 and **Lower-current SiC
reference (2 A)** for E39. **Original reference** remains explicit: E39's original
10 A setup is a stress experiment. Existing reference-menu shortcuts keep their
original inputs. **File → Choose example operating scenario…** also changes the
scenario of an opened example.

E7/E8 remain deliberate ungated-bypass contrasts, and E9 remains a doped SiC
resistor. They are distinct lessons from the fully gated GaN and distributed SiC
on-state models. Follow the [device guide](docs/device-engineering-examples.md)
and [power-family reference](docs/power-examples.md) for scope and measured evidence.

**Studies → Run configured study…** runs an available stored experiment;
**Studies → Finite-contact TLM study (E27)…** fits actual contact coupons in a new
output folder. The [two companion routing experiments](examples/README.md#companion-routing-experiments)
form a separate collection for bounded optimization. Browse the
[project downloads](examples/README.md) or [documentation index](docs/README.md)
without searching through individual GDS/OASIS files.

## Read a result as an engineering decision

At **Results → Metrics & loss budget → Engineering**, read the independent
evidence states before accepting a design. A converged solution can still exceed
a temperature limit, lie outside checked material ranges or have an unresolved
corner peak. Use **Same support** to relate local field, sheet current, metal
current density, heat and temperature over one physical area.

For a pulse, compare **Peak frame** with **Last frame**. The displayed frame may
have cooled while the whole run remains failed. The retained peak time, first
violating accepted state and requested/actual duration remain explicit; sampling
does not establish the exact threshold-crossing time.

The [thermal scenario guide](docs/thermal-example-scenarios.md) shows deliberate
Joule-only, heating, cooldown and limit-failure experiments without replacing
the forty original references. The [scientific example audit](docs/example-science.md)
records the configured service, losses, temperatures and model limitations.
[Unreleased engineering notes](docs/releases/0.4.x.md) summarize compatibility
and measured verification.

## Install on Windows

**Development / unreleased 0.4.x:** The searchable example library, physical mesh
confidence, thermal-stack and heat-deposition controls, new operating scenarios,
finite-contact TLM studies, hard temperature limits and engineering results
require the matching source checkout. Published 0.4.0 Windows binaries do not
include these additions.

**0.4.0 · research prototype · MPL-2.0 original source.**
Windows 11 x64 is the validated desktop platform. Download a published Windows
ZIP from [Releases](https://github.com/mmensing/electrothermal-layout-studio/releases),
extract the whole folder, and start `ElectrothermalLayoutStudio.exe`. Keep
`_internal` beside the executables. Install the official
[Visual C++ x64 runtime](docs/windows-runtime.md) if it is absent; the ZIP does not
redistribute its installer or DLLs. Windows bundles use the documented
[GPL larger-work route](docs/licensing.md), with source, build information,
third-party notices and checksums alongside them.

<a id="start-from-source-on-windows"></a>

### Run from source

Source development uses Python 3.12. To run from source, install once in a clean environment using PowerShell:

```powershell
git clone https://github.com/mmensing/electrothermal-layout-studio.git
cd electrothermal-layout-studio
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes --only-binary=:all: -r requirements-windows-py312.lock
.\.venv\Scripts\python.exe -m pip install --no-build-isolation --no-deps -e .
.\.venv\Scripts\python.exe -m etlayout gui
```

Dependency installation needs Internet access; normal calculations run offline.
The lock includes runtime, development and build tools. See [Windows setup](docs/windows.md)
for troubleshooting and [licensing](docs/licensing.md) before redistribution.
The Python namespace and CLI remain `etlayout`; existing project formats are
preserved. [CLI/Python automation](docs/handbook/automation-and-troubleshooting.md)
is available for repeatable batch work after the GUI introduction.

## Know what the model can tell you

The solver uses thickness-averaged **2.5D finite elements** and explicitly
configured interlayer interfaces. It models resistive electrical conduction,
thermal conduction and thermal storage. Coupled transient operation uses
quasi-static electrical states as temperature evolves.

It does not establish full 3D spreading, drift diffusion, high-field velocity
saturation, breakdown, trapping, electrical capacitance/inductance or switching
overshoot. GaN examples use a local-gate-bias sheet law; SiC examples are
**fixed-on-state interconnect models** with area-specific resistance. Synthetic
materials and examples require calibration before use as process predictions.

Corner peaks can depend on the mesh. **Generated**, **converged** and
**mesh-verified** are different states; compare physical averages, conservation
and delivered service before ranking designs. See [physics](docs/handbook/model-and-physics.md)
and [scientific acceptance](docs/scientific-acceptance.md).

Calculations run in cancellable background processes. **Edit → Execution
settings…** defaults to at most four available logical CPUs, shared by eligible
assembly and study work. Some geometry operations and sparse direct factorization
remain serial; there is no GPU backend. Display units use engineering scales,
including **mA/µm²** for volumetric current density. Stored numerical inputs and raw numerical exports
remain SI.

## Keep exploring

- [Feature catalog](docs/features.md), [guided desktop workflow](docs/desktop-workflow.md)
  and [engineering result interpretation](docs/engineering-results.md).
- [Example catalog](docs/examples.md), [GUI learning paths](docs/example-paths.md),
  [project downloads](examples/README.md) and the [documentation index](docs/README.md).
- [Power devices](docs/power-examples.md), [pulsed operation](docs/handbook/transient.md)
  and the [physics handbook](docs/handbook/README.md).
- [Developer guide](docs/developer-guide.md), [validation](docs/validation.md),
  [architecture](docs/architecture.md) and [change history](CHANGELOG.md).
- [Contributing](CONTRIBUTING.md), [security reporting](SECURITY.md) and
  [maintainer procedure](docs/maintainer-guide.md).

Original project material uses the unmodified [MPL-2.0](LICENSE). Third-party
components retain their licenses and exceptions; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
and [licensing](docs/licensing.md). Windows releases require the official
[Visual C++ runtime](docs/windows-runtime.md) and include corresponding source.
A combined frozen application is not MPL-only;
public binary publication requires separate corresponding-source and
redistribution clearance.
