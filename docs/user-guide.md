# Desktop quick start — 0.2.0

This historical guide describes **version 0.2.0** and its four-part ribbon.
For the current 0.4.0 desktop, use the [guided workflow](desktop-workflow.md) and
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

## First calculation with the current 0.4.0 controls

This walkthrough uses the current seven-stage workspace. The later ribbon
references describe the historical interface; use the linked guided workflow
for current equivalents.

1. Choose **File → Ready-to-run examples → Fundamentals → E1 · Straight conductor**. This opens a real copper
   conductor with a 2 µm thickness, source reference and 0.02 V drain condition.
2. In **Mesh & checks**, select **Run validation** (F6). Inspect the Issues drawer
   and use **Locate** to inspect reported geometry.
3. Select **Generate / update mesh** (F7). After solving, **Results → Appearance → Show mesh**
   overlays the selected layer's mesh.
4. Select **DC electrical** in the header, open **Run**, and select **Run analysis** (F5).
   The Jobs drawer reports progress; **Cancel computation** or Shift+F5 stops a calculation.
5. In the **Results & analysis** inspector, choose **Layer** `metal` and **Quantity**
   **Potential (V)** or **Volumetric current density (mA/µm²)**. **Appearance → Show vectors**
   adds directions for vector quantities. The legend states units and range.
6. Open **Metrics & loss budget**: resistance is approximately 0.084 Ω, current 0.238095 A,
   and loss 0.00476190 W. Review current/power balance, not only the convergence label.
7. Select **Place result probe** and click a location. **Results → Draw line profile**
   draws a line for the **Profiles** tab. The canvas tool **Cross-section** draws a cut
   through actual geometry and z positions.
8. Use **File → Save project as…** to retain the complete setup and calculated result.

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
choose the thermal link. Coincident x/y coordinates alone never connect different layers. For explicit dielectric/passivation sheets, half-thickness resistance, source-depth calibration and cross-section interpretation, see [thermal stacks](thermal-stacks.md).
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
recorded with the solve. Current display units are W/mm² for sheet power density,
mA/µm for sheet current and mA/µm² for volumetric current density; these quantities are
not interchangeable. Stored inputs and raw numerical exports retain SI units W/m²,
A/m and A/m², respectively.

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
remain visible while inspecting cooler frames. With [on-demand history](#on-demand-history-for-schema-3-projects),
whole-device scalar charts use recorded metadata. **Load complete time history…**
explicitly loads the retained fields needed for local traces; selecting one map
time loads only that exact stored frame and its shared support.

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

In the current workspace, open **Edit → Execution settings…** to set **Total CPU thread budget** and
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
Eligible assembly can use worker threads; geometry work and sparse direct factorization
can remain serial. A larger budget does
not guarantee a faster single solve. GPU acceleration is not included.

## Engineering evidence and linked hotspots

**Results → Metrics & loss budget → Engineering** groups independent evidence,
device/contact loss records and **Same support** readouts. Inspect one physical
layer/region or a fixed square around a map probe; E, K, metal J, heat integrals,
T and ΔT share exactly that support. Read raw-peak qualifications before accepting
a hotspot. **Peak frame** and **Last frame** distinguish the transient maximum
from a cooled final state; missing peak fields are identified explicitly. See the
[engineering results guide](engineering-results.md) for the complete GUI workflow.

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

Reports from the GUI and command line use the same offline rendering contract.
Names, notes, diagnostics, captions and recorded configuration are text, including
when they contain HTML-like tags. Generated field images are embedded PNGs, so the
HTML file can be moved without its image directory. Its Content Security Policy
blocks scripts, remote resources, forms and embedded objects. This is protection
against active report content, **not** a confidentiality filter: reports can still
contain layout paths, material settings and notes, and the comprehensive numerical
export can contain the full source archive. Review the recorded export scope
before sharing.

All application CSV exports use a spreadsheet-safe text policy. Text identifiers
beginning with formula prefixes (`=`, `+`, `-`, `@`, including after whitespace),
leading control characters or equivalent full-width prefixes receive an initial
apostrophe. CSV readers will see that apostrophe; it deliberately identifies text.
Actual numeric values retain their SI values, including negative voltage, signed
current and scientific notation. JSON, NPZ and VTU preserve the original identifiers
and numeric data for programmatic exchange. Do not remove the CSV text protection
and then open untrusted identifiers in a spreadsheet.


## Recorded runs and result compatibility

**Results → Inspect saved runs** opens the **Run history** tab. Each entry shows its
mode, convergence state, mesh size, duration and configuration hash. Select **Inspect run**
or double-click an entry to view its recorded design and fields. The **READ-ONLY RUN**
banner identifies this context: editing and new calculations are disabled, and exports
use the inspected run's recorded configuration. **Results → Compare run configuration…**
shows configuration differences from the current project, with numeric values in SI.
Use **Return to current project** to restore the editable design and its undo history,
as well as layer, region, field, palette, range, difference context and viewport. Each
inspected run retains its own view choices during the session. These presentation choices
do not change the physics configuration or require another solve.
Select multiple history entries with Ctrl-click to compare them. Saved project archives
retain the history, each run's configuration and its embedded layout source.
The history panel shows retained-data estimates and can remove selected runs.
Saving checks the expanded archive size, including all retained runs and nested
arrays, before replacing the destination. A rejected save preserves the previous
file. Remove unneeded saved runs, or rerun with a longer saved-field interval and
remove the superseded larger run before saving. Changing the interval affects
future solves; it does not thin fields already retained in a result.

### On-demand history for schema-3 projects

First create **File → Save content-addressed copy…**. Then open **Edit → Archive
and history settings…**, enable **Open schema-3 history on demand**, and reopen
that copy. The choice applies to the next opening. Schema-1/2 archives and legacy
JSON retain their eager opening behavior; opening never converts the source.

Schema-3 opening copies the selected compressed archive to an immutable private
**disk** snapshot, checks its metadata, and lists runs before their numerical
fields are read. The current result or a selected historical run loads in a
cancellable worker. A stored convergence badge describes the original calculation;
it does not certify unloaded fields or mesh verification. Selecting a transient
time loads the exact stored frame, with no interpolation or neighbouring-frame
substitution. Failed or cancelled reads preserve the previous usable view, and
the Issues drawer explains failures.

The array-cache default is **256 MiB**, adjustable from **16 to 8,192 MiB** in the
same settings dialog. The History drawer reports unique resident and pinned array
payload bytes, cache budget and active reads. These figures are not process RSS:
Python metadata, temporary buffers, other application allocations and the private
disk snapshot are separate. Active views and comparisons pin their arrays. The
cache evicts only unpinned arrays; a selection needing more than its budget asks
for explicit temporary over-budget approval or cancellation.

Whole-device scalar charts can use recorded metadata before all fields load.
Local traces, whole-history color limits, comparisons and complete numerical
exports explicitly load the complete supports they need. **Load complete time
history…** requests the retained transient fields. A selected figure or numerical
export keeps its stated layer, support and time scope.

**Save** and **Archive complete experiment** retain every remaining run, including
runs and frames never browsed. Their workers hold source-generation leases and
verify the required archive objects; closing a view cannot remove a save's source.
Progress and cancellation remain available. Cancelling before atomic publication
preserves the previous durable archive. The private copy is released after its
readers and save/export leases close. See the [history contract](design/lazy-run-history.md)
for ownership and validation details.

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
Recovery uses an application-local `recovery/` directory beside the editable material
library. Each application window has a random session UUID; portable projects persist a
separate document UUID in metadata, outside scientific configuration and result hashes.
Snapshots have unique UUID filenames and increasing document revisions. The atomic,
process-locked index can be rebuilt from independent receipts after a crash. On startup,
choose individual interrupted sessions to recover, ignore, discard (with confirmation),
or open their original project. Active sessions are excluded. A damaged entry cannot
hide another intact snapshot. Older shared recovery files are imported once without
deleting their source. JSON saves use the already-supported `project` metadata envelope to retain this UUID;
legacy bare-project JSON remains readable. Legacy projects acquire an identity on migration; Save As
preserves it, while Duplicate creates a new document.

A durable save removes only this window's snapshots through its saved revision; other
projects, sessions and newer revisions remain. Failed cleanup is reported and may leave
a redundant local copy. Recovery archives contain the same potentially confidential
layout, notes, materials and results as full project archives: they are local files, not
encrypted backups, and inherit the user's directory permissions. Temporary project writes
are sibling files removed after success/failure; embedded layouts use the local
content-hash cache. Deliberate exports remain at their chosen destination. Recovery is
crash protection, not a replacement for a durable project save.

Project saving now runs in a dedicated background worker. The status bar shows the
current stage and a **Cancel save** button; cancellation is checked between serialization,
history, verification and atomic commit. Compression already in progress may finish
before cancellation is acknowledged. A commit already completed is a successful save.
The previous durable file remains intact if serialization, validation, cancellation or
replacement fails. Finished archives pass budget, CRC and embedded-source hash checks
and are flushed before a same-filesystem atomic replacement. A small sibling `.save.lock`
file coordinates competing local writers; it contains no project content.

Editing can continue while a snapshot is saved. Completion clears the dirty indicator
only for the same document generation; the saved revision is shown when newer edits
remain. One latest manual request and one latest recovery request may wait behind the
active worker. Repeated Save, Save As and autosave coalesce queued revisions without
retargeting the active worker. Save As changes the working filename after success.
An older worker cannot publish over a newer submitted revision, and a destination changed
by another process after submission (including while queued) is rejected. Resolve that conflict by reopening or
choosing Save As. Navigation and shutdown wait for save completion or cancellation;
Save-before-navigation continues only if the saved revision remains current. While
inspecting History, Save writes the working setup without replacing the inspected
canvas. Cancel, a cancelled Save As chooser, and a failed save preserve that context.

Completed GUI result arrays use immutable bytes-backed buffers. The initial buffer freeze
costs one copy; subsequent snapshots share those buffers while copying metadata. Mutable
arrays supplied through Python are copied when captured, including read-only views with
writable aliases. The synchronous Python/CLI `save_project` API remains available;
GUI `MainWindow.save()` returns whether a request was accepted, not proof of durability.
Wait for the worker to finish before reopening a GUI-created archive in automation.

Opening JSON project metadata preserves that source file: **Save** offers a portable
`.etlayout` destination instead of overwriting it. **Save As** suggests `.etlayout`
and adds that suffix when none is entered. JSON destinations are rejected for archive
saving; Python callers can continue using `save_json` for metadata-only JSON files.


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


## Portable archive format choice

Ordinary **Save project as…** writes the existing schema-2 portable archive.
**File → Save content-addressed copy…** explicitly chooses schema 3: identical
layout, mesh and field payloads are stored once within that file, while each
recorded run keeps its own ordered identity, exact setup and scientific metadata.
The file remains self-contained; it does not depend on a shared object directory.
Normal Save retains the chosen container format. Automatic recovery remains schema 2.

Schema 3 changes storage, not the solver, numerical precision, mesh verification or
model validity. Older ETLayout versions may not understand it. Keep the original
file when trying the new format, or use **Save legacy-compatible copy…** to create
a schema-2 copy. Merely opening a project never converts or rewrites it. Figure and
selected-data sharing still exclude the source archive and unselected fields;
complete project copies remain confidential engineering data.

For scripted, verified conversion to a **new, nonexistent destination**:

```console
etlayout archive-convert experiment.etl experiment-v3.etlayout --archive-format 3
etlayout archive-convert experiment-v3.etlayout experiment-legacy.etlayout --archive-format 2
```

Conversion preserves the current result and every retained run, then checks a full
reopen. It refuses to overwrite an existing destination. External resource grants
are still explicit; a format choice never authorizes a declared file or network path.

### Hard temperature acceptance

Use **Operating conditions → Maximum operating temperature…** for an optional
global °C/K limit and layer/region limits. An accepted temperature at or above
the limit fails engineering acceptance while retaining numerical fields.
Transient operation can stop at the first violating accepted state.
See [maximum operating temperature](temperature-limits.md) for status, history,
CLI exit-code and compatibility details. Existing warning thresholds remain warnings.
