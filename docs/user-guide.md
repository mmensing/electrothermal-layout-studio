# Desktop quick start — 0.2.0

This historical guide describes **version 0.2.0** and its four-part ribbon.
For the current 0.3.1 desktop, use the [guided workflow](desktop-workflow.md) and
[layout editor guide](layout-editor.md). This older ribbon is retained only as a compatibility reference. Install the current
source edition using the [README](../README.md#start-from-source-on-windows);
no historical binary is advertised as a public MPL release.

Start `ElectrothermalLayoutStudio.exe` from the complete extracted Windows folder,
or run `python -m etlayout gui` in the installed source environment. Normal use is offline.
The welcome screen provides **New project**, **Import layout**, **Open project**,
**Try straight conductor** and **Try GaN HEMT**. Its workflow hints identify missing setup.

The ribbon groups commands into **Layout → Setup → Solve → Inspect**; **Ctrl+1–4**
switches stages. Result layer, field and region remain above the map. Display
settings are grouped under **Inspect → Display settings**, and the Analysis drawer
opens for requested results, traces, profiles and diagnostics. Resize or close the
drawer to recover map space. [Bundled offline Help](../src/etlayout/data/user-guide.html)
is the concise packaged control reference; the [handbook](handbook/README.md)
explains physics and reproducible calculations.

## First calculation

1. Choose **File → Ready-to-run examples → E1 · Straight conductor**. This opens a real copper
   conductor with a 2 µm thickness, source reference and 0.02 V drain condition.
2. Choose **Simulation → Validate geometry & connectivity** (F6). Inspect the Validation
   and Nets tabs. Double-click a reported object or net to locate its geometry.
3. Choose **Generate mesh** (F7), then turn on **Mesh** to inspect element boundaries.
4. In **Solve**, select **electrical** and **Steady**, then press **Run** (F5). The worker reports progress;
   **Cancel** or Shift+F5 stops an active calculation.
5. Select **Result layer** and **Result field** directly above the canvas. Choose
   **Potential (V)** or **Volumetric current density (A/m²)**. **Vectors** adds current
   directions for vector quantities. The legend states units and range.
6. Check Results: resistance is approximately 0.084 Ω, current 0.238095 A, and loss
   0.00476190 W. Review residuals and current/power balance, not only the convergence label.
7. Select **Probe** and click a location. Select **Profile**, then draw a line; inspect the
   Line profiles tab. **Cross-section** draws a cut through actual geometry and z positions.
8. Use **File → Save project as** to retain the complete setup and calculated result.

## Import and configure your layout

Create a new project (Ctrl+N), import `.gds`, `.gdsii`, `.oas` or `.oasis` (Ctrl+I), and
choose a cell in the wizard. Map only physical sheets: a via mask normally defines an
interface rather than a separate laterally conducting sheet. Paths are polygonized and
instance transformations are preserved; text labels are not conductors.

Layout loading and geometry preparation run in the background. The progress indicator
shows active work, and **Cancel** stops it. Wait for preparation before inspecting
fields. If you start a calculation while preparation is active, it waits for the
current preparation to finish; a superseded preview cannot replace the latest selection.

The Design browser lists cells/instances and mask/physical layers. Expand an instance to
load its immediate children. Large branches have a **Load next 100…** row showing the
number loaded and the total; double-click it to reveal the next page. Double-click a cell or
instance to select it. The geometry tool selector supports rectangle/polygon simulation
ROI, coordinate bounds, connected selection and a separate display-only ROI. A simulation
ROI that cuts a conducting path requires explicit boundary review and acknowledgement in
the Geometry menu. Place justified terminal conditions at exposed cuts before running.
Selecting a complete isolated island does not itself create a cut. The physical domain
includes explicitly drawn physical layers, so a substrate may extend beyond the imported
conductor. An explicit simulation ROI can enlarge that domain; intentional clipping of
physical layers is reported during preparation.

In the **Technology & conditions** dock, use the grouped selector to choose
**Geometry / Stack** or **Geometry / Refinement**. Add/edit physical layers in Stack.
Forms expose geometry,
material, role, thickness, z position, conductivity model, visibility and opacity. Region
definitions can combine masks with union/intersection/difference or use explicit geometry.
Full-domain thermal/insulator layers cover the simulation domain without needing a mask.
Reordering the list does not silently change physical z coordinates: edit z explicitly.

Choose **Electrical / thermal / Interfaces** to declare which two layers connect and where. Select ideal, lumped,
specific-contact, via or distributed electrical conductance as appropriate; independently
choose the thermal link. Coincident x/y coordinates alone never connect different layers.
The cross-section uses solid shapes for physical layers/conductive vias and dashed links
for effective thermal-only coupling. Thermal coefficients do not imply a solid material.

Select **Draw terminal area**, **Draw polygon terminal**, or **Draw terminal boundary edge**
in the tool selector and draw on the intended layer. The resulting form sets voltage,
ground, total current, distributed density, floating, insulated or gate conditions. A total
current terminal is equipotential with an unknown voltage. Gate regions affect only the
declared local HEMT channel; they do not create an ohmic connection.
Existing terminals are edited through **Electrical / Terminals** in the grouped selector.

Reference materials are nominal starting values. In **Technology / Materials**, **Copy** creates an
editable material. The **Library** persists custom records; project snapshots retain their
own overrides. Property forms support temperature laws and tabulated CSV import. Review
temperature ranges, source notes and units before using a material for a process.

Numeric forms accept values with units and engineering prefixes, such as `20 mV`,
`200 nm` or `2.5 µm`; a bare number uses the unit shown beside the field. Required blanks,
nonfinite values and invalid ranges display an inline error and keep the dialog open.
Correct the highlighted field before saving. Expand **Advanced options** when needed.
Choose **Simulation / Settings** for **Mesh settings…**, **Solver settings…**,
**Engineering warning thresholds…**, simulation presets, project notes and advanced JSON.

## Thermal and coupled analysis

Add appropriate thermal sheets, interlayer links and, under **Thermal / Thermal BC**, a fixed
temperature or heat-transfer coefficient with ambient temperature. Use **Thermal / Heat sources**
for independently prescribed heating.

Select **thermal** and enable **Include electrical heating** to solve DC once at the configured
initial temperature and map its Joule/contact losses into the thermal model. Disable that
option for a prescribed-source thermal problem. Select **electrothermal** to update losses
and temperature-dependent transport self-consistently. **Solver settings…** exposes damping, tolerances,
iteration limits and solver choice. Unconverged results remain visibly marked.

Choose **Temperature**, **Temperature rise** or **Joule power density** after solving.
**Hotspot** locates the maximum. Temperature rise uses the boundary/reference temperature
recorded with the solve. Sheet power density is W/m², sheet current A/m, and volumetric
current density A/m²; these quantities are not interchangeable.

## Transient and pulsed operation

In **Solve**, select **Transient** separately from the physical mode. Supported
transient scopes are **thermal** and **electrothermal**. Electrical mode computes
DC conduction and requires Steady operation. In transient thermal mode, Include
electrical heating recalculates loss for each drive at the reference temperature;
electrothermal mode also includes temperature feedback.

Open **Pulse settings…**. **Timing** contains stop time, maximum step, saved-field
interval and uniform/steady initialization. **Waveforms** contains readable source
names, engineering-unit inputs, a time/value table for piecewise-linear drives and
a preview. **Advanced** contains budgets, peak retention, **Thermal capacity** and
optional periodic convergence. A pulse's width is its high plateau; a zero period requests one pulse.
Waveforms replace the corresponding static source values.

Consistent finite-element capacity is the default. Lumped nodal capacity can
reduce local temperature undershoot under short, localized pulses, but neither
choice guarantees physical bounds for every mesh and interface. Check the
temperature-bound diagnostics and compare mesh/time refinement; energy balance
alone does not establish local accuracy.

Transient validation checks applied waveform currents and voltages across all
pulse edges and piecewise-linear segments. Fully adiabatic thermal transients are
valid: heat then accumulates as stored energy. Insulation is not a steady heat sink.

After solving, the time controls select a retained snapshot, accept direct time
entry and locate the retained whole-run temperature peak. **Inspect → Time history**
opens the right Display settings tab, preserving map height. It offers every-step
whole-device traces or retained-field traces for the selected
layer/region and map probe. Click the trace to select the nearest map frame. Local
traces can miss peaks between retained fields; whole-run temperature warnings
remain visible while inspecting cooler frames.

**Solve → Studies / start…** groups **Time-step convergence…**, **Start from saved
state…** and **Resume saved transient…**. A warm start begins at zero time with a
compatible field. Resume preserves the absolute time and cumulative energy, and
the stop time is the desired absolute end. Time-step studies keep output cadence
fixed and disable periodic early stopping so each run covers the same duration.
Review peak temperature/time and energy changes, not just solver convergence.
Re-running a saved warm-start or continuation archive reuses its original starting
snapshot. Choose Resume saved transient explicitly to continue its final state.

Start with E17 for an analytical thermal RC comparison, E18 for current pulses,
and E19 for a passive receiver's delayed thermal response through a shared substrate.
See the [transient handbook](handbook/transient.md) for equations, initialization,
energy accounting, examples and CLI equivalents.

## CPU execution and responsiveness

Open **Settings → Simulation execution…** to set **Total CPU thread budget** and
**Parallel study worker limit**. Both default to four, capped at the number of
available logical CPUs. The worker limit cannot exceed the total budget. One
run can use the native thread budget; independent sweep, design-comparison,
mesh-study and time-step-study points share it across separate processes.
For example, a budget of four and two active workers permits two native threads
per worker. Parallel points each require their own solver memory.

The desktop runs preparation and simulation in the background so its controls
remain available. **Cancel** stops an active calculation and its study workers.
CPU preferences are stored on
this computer, apply to future jobs and do not change project physics or invalidate
results. Supported native numerical libraries and Gmsh honor the thread limits;
Python assembly and some sparse solves can remain serial. A larger budget does
not guarantee a faster single solve. GPU acceleration is not included.

## Inspect multiple layers and regions

The persistent **Result layer** selector shows each physical layer's name and ID.
You can also click a physical layer in **Design browser → Layers** to select it.
The map initially displays that layer's field across all its solved regions. **Result region**
can select a connected island or a named interface, terminal, heat-source or boundary
footprint. The same selection controls the map, vectors, mesh, point probes, profiles,
selected-field statistics and displayed-field exports. Holes and regions without valid
values stay unpainted. Fields from different physical heights are not summed.

Changing layers keeps the chosen quantity. If that layer has no such field—for example,
current density on a thermal-only substrate—the map clears and explains the mismatch.
Choose an available field, such as **Temperature**, or return to a conducting layer.
Unavailable quantities are disabled in the field list. The hint reports how many layers
support the selected quantity.

**Isolate layer** is enabled by default while viewing fields: other layer geometry and
terminal markers are hidden. Turn it off to see geometry context; those background colors
are not field values. The layer-tree checkboxes separately control saved background
visibility. **Display source masks** temporarily bypasses isolation to keep mask geometry
visible. None of these selections changes the simulated domain or requires another solve.

**Fit layer** frames the selected field's active elements. The toolbar's **Fit** frames the
layout. Selecting **Layout geometry** restores geometry inspection and keeps that choice
through layer changes. **Overall peak temperature** in the Results summary is the maximum
across the solved stack; the color legend describes only the selected layer.
The selected-field statistics report supported element count, area, minimum, area-weighted
mean and maximum. **Hotspot** locates the maximum of the displayed quantity within that
selection; without a displayed field it searches the original temperature results.
**Smooth** interpolates nodal potential and temperature within each triangle. Element
fields, including current density, remain piecewise constant. Vector arrows show direction
with normalized lengths; their size does not encode magnitude. The **Mesh** overlay follows
the selected layer and region while viewing a field.

## Resistance measurements and field scales

The Results summary shows electrical resistance, its terminal references, current, loss
and peak temperature when available. Terminal current is positive into the device.
Automatic resistance requires an unambiguous two-terminal conducting network; an isolated
biased gate does not set the voltage difference. For a Kelvin or multiport measurement,
open **Simulation / Settings → Solver settings…** and enter two terminal IDs, separated
by a comma, in **Resistance voltage terminals**. Set **Resistance current terminal** to
the driving terminal whose current defines the measurement. Ambiguous automatic
measurements are shown as unavailable; configure the references before interpreting them.

Open **Inspect → Display settings → Appearance** and choose **Color map**: Teal, Viridis, Magma, Cividis, Coolwarm (diverging)
or Grayscale. Map, color bar and exports use the same palette. The legend shows minimum,
midpoint and maximum values in the field's units. Enable **Manual range**, then enter
**Min** and **Max**, to retain fixed limits while comparing layers or regions of the same
quantity. Entries accept units and engineering prefixes, for example `200 mV`. **Auto**
restores limits from the selected finite field. Values outside manual limits use endpoint
colors; numerical values, probes and statistics remain unclipped.

Changing the field, or selecting a layer without that field, resets the range. **Log scale**
requires positive limits and omits triangles with nonpositive field support. Invalid,
nonfinite or reversed manual limits clear the map and show an error until corrected;
they do not silently select Auto. A logarithmic midpoint is the geometric mean. Difference
maps initially use Coolwarm with automatic limits symmetric about zero; manual limits
override that symmetry. Other palettes are available for differences too.

## Studies and exports

**Simulation → Parameter sweep** offers named parameters with their units and linear/log
or explicit values. Advanced parameter paths are also supported.
For example, `terminals.drain.value`, `layers.metal.thickness`, or
`layers.channel.electrical.gate_voltage` selects a configured scalar. Inspect the actual IDs
in your project. Values are checked against the parameter's declared type: integer settings
reject fractional values. Studies retain requested and applied values. The Studies tab
stores each successful run; failed points retain their errors. The study table
keeps status and requested value visible. Failed points are marked on plots and
leave gaps between successful curve segments. The metric selector defaults to
relevant quantities; **All metrics** exposes the full list.
**Compare project designs** compares saved projects, while **Compare field difference**
loads a saved result and enables an explicit **View mode** selector: **Original result**
or **Current − comparison**. Palette, range, smoothing, isolation and region changes
preserve this mode. Differences retain only complete current-mesh triangles within both
finite domains, so unmatched areas and holes remain empty. Vector/tensor fields compare
their scalar magnitudes, not component vectors. Each source's own thermal reference is
used when comparing temperature rise. Probes and profiles identify difference quantities;
an unconverged source is disclosed in the difference view and exported reports.
The Results table remains explicitly labeled as original-run metrics. The headless
array-difference API requires identical meshes. **Mesh convergence study** compares integral quantities
and terminal currents at several mesh sizes. Select existing drawn profiles in its dialog to
track fixed spatial samples and relative L2 changes. Profile definitions and samples remain
with each saved run. Ideal sharp-corner peaks can remain resolution-dependent.

Use **Simulation → Side-by-side field maps…** for two simultaneous maps with a common color scale.
Each view identifies its result and physical layer; compatible difference maps are a separate
operation. A shared color scale makes different designs directly comparable without silently
stretching each map's colors to a different numerical range.
**Compare layers…** above the canvas opens two panels for the current original run without
requiring another file. Each panel has its own layer and region selection. **Link pan / zoom**
keeps physical coordinates and magnification synchronized; turn it off for independent
inspection. **Shared physical extents** and **Fit maps** control initial framing. The shared
field, palette, manual/automatic range, log scale and smoothing apply to both panels.
Invalid or unsupported comparisons clear both maps and disable their export.

Use **Simulation / Settings → Engineering warning thresholds…** for project-specific limits. These are
user-selected criteria, not universal safe operating limits.

**File → Export original numerical results** writes all original layers and quantities as
CSV/JSON, remapped VTU fields, plots and a full source archive. **Export displayed field /
region** exports only the chosen field and supported region, including derived temperature
rise or a difference. `manifest.json` maps original layer IDs to collision-free filenames,
including on Windows. Unused global nodes cannot expand per-layer plot bounds or ranges.
The full source archive in a normal filtered export is explicitly labeled as such.

Difference exports preserve both source identities and omit original solver metrics and
terminals. They include `difference-fields.npz` rather than a misleading simulation archive.
**Export map** saves the current graphic with a ticked color bar, units, log/manual-range
labels and a JSON metadata sidecar. **Export report** documents the displayed selection;
difference reports include the paired-run provenance. Comparison exports also include a
sidecar recording both panels and their views. Profiles retain selection and source
provenance in CSV. Save/reopen a project to resume with both setup and results.

## Recorded runs and result compatibility

**Simulation → Inspect run history** opens the **Run history** tab. Each entry shows its
mode, convergence state, mesh size, duration and configuration hash. Select **Inspect run**
or double-click an entry to view its recorded design and fields. The **READ-ONLY RUN**
banner identifies this context: editing and new calculations are disabled, and exports
use the inspected run's recorded configuration. **Simulation → Compare run configuration…**
shows configuration differences from the current project, with numeric values in SI.
Use **Return to current project** to restore the editable design and its undo history,
as well as layer, region, field, palette, range, difference context and viewport. Each
inspected run retains its own view choices during the session. These presentation choices
do not change the physics configuration or require another solve.
Select multiple history entries with Ctrl-click to compare them. Saved project archives
retain the history, each run's configuration and its embedded layout source.
The history panel shows estimated retained memory and can remove selected runs.
Saving checks the expanded archive size, including all retained runs and nested
arrays, before replacing the destination. A rejected save preserves the previous
file. Remove unneeded saved runs, or rerun with a longer saved-field interval and
remove the superseded larger run before saving. Changing the interval affects
future solves; it does not thin fields already retained in a result.

Physical edits, such as geometry, material properties or boundary conditions, invalidate
the current calculation and prepare fresh geometry. Presentation and annotation changes,
such as notes, visibility, opacity, display-only ROI and engineering warning thresholds,
preserve compatible fields. Threshold changes refresh diagnostics without rewriting
recorded runs. The exact configuration recorded with a run remains available independently
of this physics compatibility check. Active calculations use an immutable project snapshot;
editing or importing another design is blocked until the calculation finishes or is cancelled.

Undo/redo handles configuration edits. Recovery is offered after an interrupted editing
session; a normal project save is the durable record. Missing external layouts can be relinked
through File. Portable archives normally include the layout already.
Autosave failures appear in the status area; save explicitly after resolving the cause.

## Power interconnects and routing — added in 0.3.0

Version 0.2.0 does not include this extension. In 0.3.0, open **Studies → Routing &
optimization** for the shared Setup, Analysis, Routing and Experiments workspace.
Use the [current power-routing guide](POWER_ROUTING.md) for physical ports, scoped
measurements, protected source copies, equal-service acceptance and mesh/export
verification, and the [guided workflow](desktop-workflow.md) for current controls.

## Workspace and offline help

Use **Workflow** and **Inspector** above the canvas to hide or restore either side;
the status strip below the canvas restores Issues/Jobs/History. **View** provides
checkable toggles, **Focus layout canvas**, **Maximize canvas / restore panels**,
and explicit **Fit selection / ROI**. See the [workspace shortcuts](desktop-workflow.md)
for keyboard access. Visibility and splitter sizes are remembered locally between
launches and preserve drafts, field choices, transient time, zoom and pan.

Resize visible panels, then use **View → Save workspace layout…** to keep a named
arrangement. **Restore workspace layout…** recalls it; **Reset workspace layout** restores
the default arrangement. Workspace layouts are stored locally. **View → Dark theme**
switches the application palette. **Help → Open user guide** opens this bundled guide
inside the application without a network connection; **Workflow & model limits** provides
a shorter reminder of the workflow and the model's scope.

## Interpreting results

The 2.5D model resolves lateral fields and explicit vertical links, not full 3D spreading or
through-thickness gradients. HEMT output is an analytical resistive charge-control model;
it excludes drift diffusion, velocity saturation, trapping, breakdown and field-plate
electrostatics. Check [physical models](physics.md), [numerics](numerics.md), and
[validation](validation.md), then calibrate inputs and assess mesh sensitivity for your use.
