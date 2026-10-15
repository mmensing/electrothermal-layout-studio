# Results, exports and validation

[Handbook](README.md) · [← Worked examples](worked-examples.md) · [Next: automation and troubleshooting →](automation-and-troubleshooting.md)

A useful result states the solved configuration, field, physical layer, selected
region, units and convergence status. Start with those facts before comparing
colors or interpreting a hotspot.

For a transient run, identify the **saved time** as well. The map represents one
snapshot; whole-device time-history and peak-over-time metrics describe the sequence
of accepted integration steps. Local layer/region/probe histories use only retained
field snapshots and identify that sampling scope. Storage means instantaneous generated power need
not equal outward heat flow. Use the [transient energy balance](transient.md#read-energy-and-completion-correctly)
when assessing that run.

## Read one physical field at a time

Choose the result layer, then the quantity and region. Different layers have
different fields: current density is unavailable on a thermal-only substrate,
while temperature is available after a thermal calculation. An unavailable field
clears the map and explains the mismatch; it is not a zero field.

The region selector can limit the view to an edge-connected island or a named
terminal, interface, boundary or source footprint. Map, mesh overlay, vectors,
probes, profiles, selected-field statistics and displayed-field export use that
same support. Unsolved regions and holes remain empty. Selection does not change
the physical model or recompute a restricted-domain solution.

| Field | Location | Interpretation |
| --- | --- | --- |
| Potential | Nodes | Voltage, linearly interpolated in a triangle |
| Temperature | Nodes | Absolute sheet temperature in K |
| Temperature rise | Derived from temperature | Temperature minus the run's recorded thermal reference |
| Sheet current density | Elements | Lateral vector in A/m; scalar map shows magnitude |
| Volumetric current density | Elements | Sheet current divided by assigned thickness, A/m² |
| Joule power density | Elements | Lateral electrical heat generation, W/m² |
| Interlayer contact power density | Elements | Contact loss allocated to this sheet, W/m² |
| Total applied heat density | Elements | Electrical/contact heat plus prescribed sources, W/m² |

**Smooth** interpolates nodal potential and temperature within each triangle.
Element fields remain piecewise constant, as in the finite-element solution.
Vector arrows encode direction with normalized length; use their associated
scalar magnitude map for size. A visually smoother map is not a finer solve.

## Use color deliberately

Teal, Viridis, Magma, Cividis, Coolwarm and Grayscale are available. Choose a
sequential palette for an ordered magnitude; the diverging Coolwarm palette is
useful for a signed difference around zero. The legend and exports use the same
chosen palette.

Use **Manual range** for comparisons where the same color must mean the same
value. Values outside its limits receive the endpoint colors, while probes,
statistics and numerical exports retain their actual values. **Auto** derives
the range from the selected finite field. An invalid manual range clears the
map and reports the problem until corrected.

Logarithmic display requires positive support and limits. Nonpositive triangles
are omitted; they are not replaced by an arbitrary small positive value. This
can hide zero-current or negative-difference regions, so keep linear scaling when
those values are essential to the question. The log midpoint is the geometric
mean of the limits.

## Compare layers and runs

**Compare layers** opens two maps of the original run, each with its own layer
and region. Linked pan/zoom maintains common physical coordinates. A shared
range makes different layer temperatures directly comparable, as in E10.
Transient comparisons provide linked nearest-snapshot times or independent time
selection. Both actual times remain visible; no temporal field interpolation is
implied. **Scale across saved times** keeps a shared range across the retained
sequence. Direct time entry and **Peak** avoid dragging through every frame.

For a saved-run difference, the desktop computes **current minus comparison** on
complete current-mesh triangles covered by both finite domains. Unmatched areas
and holes stay empty. Vector fields compare scalar magnitudes, not vector
components. Temperature-rise differences use each run's own thermal reference.
Automatic Coolwarm difference limits are symmetric about zero; manual limits
override that symmetry.

Do not confuse that display operation with the low-level
`simulation.analysis.field_difference(a, b, ...)` function, which requires
identical meshes and returns raw arrays as **b − a**. Prefer the displayed
selection's exported provenance when reproducing a desktop comparison.

Difference probes, profiles and exports identify the two source runs. Original
run metrics remain explicitly labeled as such; a difference map is not a new
electrical or thermal solution. Check source convergence and experimental
compatibility before interpreting the difference physically.

## Separate local statistics from whole-run metrics

The selected-field summary reports supported element count, area, minimum,
area-weighted mean and maximum. It describes the displayed selection. Whole-run
peak temperature can be on another sheet; area-average temperature counts each
active sheet's area separately, including layers that overlap in x–y.
Whole-run engineering warnings remain visible while selecting a cooler frame or
another region; selected-frame statistics do not replace the run's audit record.
For resumed runs, peak metrics cover the current segment while cumulative energies
include the previous origin. The initial-state and energy-scope metadata distinguish
those meanings.

Temperature rise uses a reference determined by the thermal boundary model:
the mean fixed temperature when present, otherwise the conductance-weighted
ambient. With multiple reservoirs, a reported peak thermal resistance is a
descriptive ratio, not a unique two-terminal thermal network resistance.

Terminal currents are constraint reactions and the primary conservative current
measurement. A branch-current cut integrates the sheet-current vector with a
sign convention toward the left normal of start→end. Local P1 gradients are
discontinuous, so arbitrary internal cuts still need refinement checks.

At a point shared by disconnected components, the exported global nodal array
can only retain one display entry; it stores a mean. The internal solver keeps
separate unknowns, and element fields preserve each component. Avoid treating
that special shared-vertex display value as a physical electrical connection.

## Validate in three stages

1. **Setup validity:** confirm geometry, connectivity, units, material range,
   reference conditions and the intended electrical and thermal experiment.
2. **Numerical solution:** require convergence and inspect current, electrical
   power and thermal balance, residuals and iteration history. Reported balance
   errors are fractions: `1e-3` is 0.1%, not 0.001%.
3. **Suitability for use:** assess mesh sensitivity, compare against a known
   limit or measured data, and vary uncertain physical inputs. A small residual
   says nothing by itself about process calibration.

Steady electrical terminal/source work should match lateral plus contact loss.
Steady thermal generation should match outward boundary flow. These checks are
measured independently; the solver does not renormalize heat to force agreement.
The [numerics reference](../numerics.md#conservation-and-result-interpretation)
defines the normalization and multi-reservoir cases.

Refine the mesh and track the engineering quantities you actually need: total
current, loss, resistance, peak/mean temperature and fixed-location profiles.
An ideal sharp-corner current-density maximum can continue increasing under
refinement. Use physical corner geometry or an explicitly defined local average
when comparing to a process-specific criterion.

```powershell
.\.venv\Scripts\python.exe -m etlayout refine examples/E2_bent_taper.etl --mode electrical --sizes 1e-5,5e-6,2.5e-6 --output results/e2-refinement
```

The mesh study scales minimum, contact, local and per-layer sizes alongside the
requested maximum size. This is geometric refinement, not automatic
solution-error-based adaptation. Passing the E1 analytical check validates one
important limit; it does not establish E12 contact resolution or HEMT fidelity.

## Choose the export that matches the claim

| Export | Contents and intended use |
| --- | --- |
| Save project | Portable `.etl` with configuration, embedded layout, materials and optional numerical result/history |
| Original numerical results | All solved layers and quantities, CSV/JSON, remapped VTU, plots and source archive |
| Displayed field / region | The selected scalar field and valid support; `manifest.json` records layer IDs, filenames and scope |
| Current map | Graphic with color bar, units and display annotations, plus a JSON sidecar |
| Comparison map | Both panels and shared scale, plus source/selection/view metadata |
| Report | HTML describing the recorded/displayed result and its provenance |
| Profile | Sampled values with coordinates, units and source/selection identity |
| Transient time history | Every-step whole-device scalars or retained-field local traces, with source and time scope |

A normal filtered numerical export includes the complete original `simulation.etl`
for reproducibility and explicitly labels that source archive as full scope.
Difference exports instead write `difference-fields.npz` and paired-source
provenance; they omit original terminal/solver metrics and a misleading
simulation archive. Numerical color clipping never changes physical values.
Empty profile samples outside valid support remain gaps/blank cells.

Transient history exports include initial-state identity, segment bounds,
cumulative-energy scope, peak scope and stop reason. The CSV metadata sidecar
preserves that context alongside SI-unit columns. A resumed run's energy totals
include the earlier origin, while its peak metrics describe the current segment.
CPU execution settings are recorded separately from the physical configuration.

The Run history panel reports estimated retained memory and can remove selected
runs. A project save checks the combined expanded size of the current result,
history and nested arrays before atomically replacing an existing archive.
Compression does not bypass the limits. If a save is rejected, keep the previous
file and remove unneeded saved runs. Alternatively, rerun with a longer saved-field
interval, then remove the superseded larger run before saving. Changing the
interval affects future solves; it does not thin existing result snapshots.

[← Worked examples](worked-examples.md) · [Automation and troubleshooting →](automation-and-troubleshooting.md)
