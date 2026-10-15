# Feature catalog

[Project introduction](../README.md) · [Guided examples](examples.md) · [Desktop workflow](desktop-workflow.md)

Use the desktop to move from a layout question to a result you can inspect,
compare and reopen. This catalog covers the current source edition; it describes
available tools, not a claim that every physical model is calibrated for your process.

## Build the experiment

| Tool | Where to find it | What you can do |
| --- | --- | --- |
| GDSII/OASIS and hierarchy | **File → Import layout…**, **Layout & Region**, **View → Hierarchy / layers / nets** | Inspect cells and instances, frame geometry and choose an explicit simulation scope. New masks start display-only until mapped. |
| Purposeful regions | **Layout & Region**, **Model → Define measurement support**, **Model → Define routing keepout**, **View → Draw view crop** | Keep physical domain, reporting support, routing protection and camera framing separate. Preview and commit the stated purpose. |
| Project-owned vertex editor | **Edit → Edit layout polygon vertices**, **Ctrl+Shift+E** | Move polygons or individual exterior/hole vertices, enter coordinates, insert/delete points and apply an undoable copy. The imported source file is preserved. |
| Physical stack and materials | **Layers & Materials**, **Model → Materials and library** | Assign sheet roles, thickness, bottom elevation and electrical/thermal material models. Review a stack cross-section. |
| Reusable technology mapping | **Layers & Materials → Technology templates…** | Preview and apply physical mappings/material snapshots to chosen masks; project drives and thermal boundaries remain a separate setup task. |
| Connections | **Contacts & Connections** | Define boundary terminals, finite-feed physical ports, interlayer contacts, passive probes, floating electrodes and supported gate controls. Projected overlap alone is not a physical interface. |
| Operating conditions | **Operating Conditions** | Configure electrical drive, prescribed heat, thermal reservoirs and numerical/initial conditions; dormant settings survive analysis-mode changes. |

[Desktop setup](desktop-workflow.md) explains the complete path. The [layout editor](layout-editor.md)
and [setup handbook](handbook/setup-and-boundaries.md) cover geometry and physical assumptions.

## Choose the calculation

In the working setup, use the header's **Analysis type** selector. Its selection
updates the prerequisites; it does not launch a run or erase inputs used by
another mode. Historical inspection and active jobs retain their editing locks.

| Analysis | Question it answers | Start with |
| --- | --- | --- |
| **DC electrical** | Where does current flow at a fixed configured temperature, and what resistance/loss does that produce? | E1 straight strip, E2 bend/taper, E12 via ladder. |
| **Steady-state thermal** | Where does prescribed or supported electrical heating go through the configured stack and reservoirs? | E15 thermal crosstalk (prescribed heating). |
| **Coupled DC electrothermal** | How do temperature-dependent conduction and Joule heating settle together? | E11 serpentine heater or a nominal power reference. |
| **Thermal transient** | How do thermal mass, pulses and cooling affect temperature over time? | E17 thermal RC reference, E19 passive multilayer thermal delay. |
| **Electrothermal transient** | How does changing temperature feed back into quasi-static electrical loss during a pulse? | E18 pulsed conductor. |

**Simulation → Time and load settings…** provides supported pulse/PWL waveforms,
initialization and time controls. Saved-state start/resume and time-step studies
support repeatable continuation. There is no electrical charge-storage or
switching-parasitic model. See [transient operation](handbook/transient.md).

## Inspect evidence, not just colors

![Read the run identity, physical support, display context and acceptance evidence](images/result-reading.svg)

| Task | GUI route | Read the result with this context |
| --- | --- | --- |
| Validate and mesh | **Mesh & Checks**, **F6**, **F7** | Issues carry severity and object/location where available. **Locate** frames evidence; **Fix** opens its editing task. |
| Review local refinement | **Mesh & Checks → Suggest local refinement** | Inspect bounded contact/channel/hotspot suggestions and resource estimates before applying them. A suggestion is not proof of convergence. |
| Explore maps | **Results** | Select result, layer, quantity, physical region and transient time. Colormaps/manual ranges change display, not the saved fields. |
| Sample and integrate | Result probes; **Results → Draw line profile**, **Integrate branch current**, **Contacts, devices and loss budget** | Use component-local fields and physical support. Keep sheet current, metal volumetric current density and contact-normal loading distinct. |
| Compare results | **Results → Compare layers…**, **Compare field maps…**, **Load field difference…** | Compare the intended layer, support, scale and time; inspect a numerical difference alongside the maps. |
| Revisit a run | **History**, **Results → Inspect saved runs** | Historical context is read-only. **Return to working setup** restores editing; retained runs keep their original configuration identity. |
| Check a change | **Studies → Mesh convergence…**, **Time-step convergence…**, **Compare project designs…** | Compare integral quantities and regularized hotspots as well as solver convergence. |

Maps and human-readable tables use engineering units. Volumetric current density
is **mA/µm²**, sheet current is **mA/µm**, and electric field is **V/µm**. Absolute
temperature and rise are reported in K; scalar voltage/current/power/time use
appropriate prefixes. Manual ranges use their printed unit. Raw numerical
exports and model inputs remain SI. See [result units](desktop-workflow.md#result-display-units)
and [results and validation](handbook/results-and-validation.md).

## Compare power routes at equal service

**Studies → Routing & optimization** opens the shared power workspace:
**Baseline → Allowed changes → Operating scenarios → Goals / limits → Preview /
check → Evaluate → Compare / verify**.

- Analyze device groups, supported measurements, physical ports, individual via
  loading, contact loss, external-feed loss and current-sharing metrics.
- Generate bounded supported routing changes while respecting protected geometry,
  connectivity and fabrication rules.
- Evaluate multiple operating scenarios, retain failed candidates and check equal
  delivered service before comparing objectives.
- Review mesh verification and exported-layout reimport before accepting a
  finalist. Lower delivered current is not a routing improvement.
- Export supported passive multiports with electrical-response fidelity checks.

The [power guide](POWER_ROUTING.md) describes the controls and acceptance checks.
The [power-device examples](power-examples.md) keep active geometry, device law,
thermal environment and service fixed when those are not the studied variables.
They include RDL feeds, long-finger/multi-bank GaN and stripe/cellular SiC
on-state collection, with reference projects, compact generation presets and opt-in larger
benchmarks. The reference projects open in the GUI; generating other preset/variant
combinations uses the optional CLI.

## Keep and share the experiment

Use **File → Save project as…** for your working configuration, embedded layout,
material snapshots and retained history. Resolve pending edits with **Apply**,
**Discard** or **Stay** before changing context. Use **Results → Export task** to
choose a map, displayed field/support, original numerical results, report or
selected-run project archive. A selected-run archive belongs to that run's setup;
it is different from saving a subsequently edited working project.

The CLI/Python interface shares the backend for batch use and reproduction. It is
optional for these [GUI walkthroughs](examples.md). See [automation](handbook/automation-and-troubleshooting.md)
for scripts and command-line details.

## Scope and execution

This is a thickness-averaged **2.5D** conduction application. Explicit interfaces
connect sheets; the stack preview does not add full 3D field resolution. GaN's
local-gate-bias sheet model and SiC's fixed-on-state area-specific load are
effective models, not calibrated transistor ratings. Read [model limits](handbook/model-and-physics.md)
before interpreting an example as a device prediction.

**Edit → Execution settings…** sets the shared CPU budget (default: at most four
available logical CPUs). Background jobs remain cancellable with **Shift+F5**;
assembly and independent studies can use that budget, while some phases remain
serial. There is no GPU backend. Windows 11 x64/Python 3.12 is the validated
desktop platform; see [Windows setup](windows.md) and [verification scope](validation.md).
