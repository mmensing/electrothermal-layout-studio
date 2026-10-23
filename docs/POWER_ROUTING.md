# Power interconnects, bounded routing and equal-service experiments

This guide describes the power-interconnect and routing capability in 0.3.0. The [scientific acceptance contracts](scientific-acceptance.md) define
measurement, circuit-export and via-rule requirements. The historical v0.2.0 download
does not contain these changes; use the [0.3.0 release](releases/0.3.0.md) or current source.
The [measured demonstrations](power-routing-demonstrations.md) include a
verified metal-routing improvement and a HEMT finalist correctly withheld for
insufficient mesh resolution, with reproducible settings and numerical receipts.

## Start with a reproducible project

Open the ready-to-run [four-cell archive](../examples/power_routing/four_cell_feed.etl)
or [HEMT archive](../examples/power_routing/hemt_source_feed.etl) in this version's
desktop application. Both embed their synthetic layouts. To regenerate two
editable projects from the checkout:

```powershell
python examples/power_routing/generate.py --output .artifacts/power-examples
python -m etlayout power .artifacts/power-examples/four_cell_feed.etl --no-plots --output .artifacts/power-report
python -m etlayout routing .artifacts/power-examples/four_cell_feed.etl --action validate --output .artifacts/rule-report
python -m etlayout routing .artifacts/power-examples/four_cell_feed.etl --action generate --parameters '{"bus_width":6e-6,"strap_width":6e-6,"via_count":8}' --output .artifacts/wider-feed.gds
python -m etlayout optimize .artifacts/power-examples/four_cell_feed.etl --experiment four-cell-feed --output .artifacts/feed-search
python -m etlayout optimize .artifacts/power-examples/hemt_source_feed.etl --experiment hemt-source-feed --output .artifacts/hemt-search
```

`--parameters` also accepts a JSON file, which avoids shell quoting differences.
All coordinates, recipe dimensions and physical parameters in JSON are SI.
Use a fresh output filename for generation and export. Existing files and the
original input layout are never silently overwritten. Search output directories
are resumable only when their recorded experiment identity matches.

In the desktop, open **Studies → Routing & optimization**.
The workbench groups controls into **Setup**, **Analysis**, **Routing** and
**Experiments** tabs. Physical settings live in the project; machine CPU settings
remain in **Edit → Execution settings…**. Computational operations use the
same Qt-free backend as the CLI and run in the existing background worker.

In **Setup**, choose a collection to edit scoped measurements, physical ports and
landings, terminal/probe footprints, device groups, recipes or experiments. Use
**Technology rules** for geometric limits and the named/explicit thermal reference
controls for temperature-rise reporting. Nested records use typed editors;
recipe-specific parameter dictionaries have a focused JSON editor with SI values.
In **Analysis**, solve the current settings, select a contact row to highlight its
physical island, and inspect normal-current density using the colormap and manual
range controls. Port matrices and analysis tables have separate export actions.
In **Routing**, generate into a new filename, inspect violations and added/removed
geometry, then use a valid candidate as the project draft. **Apply settings & close**
retains the draft; expensive calculations remain in an owned worker process.

The four-cell example has fixed analytical areal-resistor cells, a frontside bus,
an upper strap, physical copper vias and a finite-resistance source landing.
Bus/strap widths and integer via count vary; active-cell and finger masks do not.
Its two current requirements are 0.05 A and 0.1 A at up to 0.5 V. The HEMT example
reuses the established E8 local-gate-bias model with a localized source landing.
Two 6–12 µm source-bus segments, single/dual-ended upper feeds, an upper strap,
and uniform/graded/staggered via distributions vary. Channel, original source
fingers, drain, gate, contacts and field plate remain fixed. Its baseline gate
metal is above source/drain metal to give the physical vias a non-overlapping
stack. Its scenarios use 1 mA at 0 V gate bias
and 2 mA at −1 V gate bias, with 2 V compliance. These are synthetic engineering
experiments, not measured or process-calibrated transistor predictions.

## Authoritative fields and measurements

The solver keeps independent DOFs for sheets and components that share only an
XY point. `SimulationResult.element_fields[layer][field]`, with shape `(M, 3)`,
stores authoritative P1 values at each triangle's three vertices. Nodal arrays
remain compatibility output and are not a valid substitute at an ambiguous
shared vertex. Rendering/export may duplicate coincident vertices. Interior
sampling uses the containing element; ambiguous shared boundaries return explicit
ambiguity/missing support instead of inventing an averaged physical state.

`PowerSettings.measurements` stores physical selectors, quantity, aggregation,
units, weighting, bounds and an optional fixed averaging length. Selectors union
IDs within one category and intersect populated categories. Categories include
layers, materials, declared nets, regions, device groups, contacts/vias and ports.
Explicit selector geometry clips integration support, rather than selecting a
triangle by its center alone. Empty/stale/nonfinite support is unavailable and
cannot pass optimization feasibility.

| Quantity | SI unit | Meaning |
| --- | --- | --- |
| `metal_current_density` | A/m² | Lateral current divided by real bulk-metal thickness; excludes sheet/2DEG reporting conversions |
| `sheet_current` | A/m | Thickness-integrated lateral current, appropriate for 2DEG conduction |
| `effective_current_density` | A/m² | Explicit reporting-only conversion using supplied effective thickness; excluded from optimization merit |
| `contact_normal_density` | A/m² | Current crossing a contact from declared layer A to B; select `signed` for direction or `magnitude` for absolute loading |
| `potential`, `contact_voltage_drop` | V | Scoped sheet potential or local A-minus-B interface drop |
| `temperature`, `temperature_rise` | K | Absolute temperature or rise relative to a named/explicit reference |
| `joule_density`, `contact_heat_density` | W/m² | Lateral or interfacial areal loss; an integral returns W |
| `port_current`, `terminal_current` | A | Positive current entering the modeled domain |

Means, RMS and integrals use geometric area and P1 quadrature. Percentiles use
area-weighted distributions, not triangle or vertex counts. Vector quantities
select magnitude, x or y explicitly; a signed component is different from a
mean magnitude. Exceedance reports the supported-area fraction above a limit.
Legacy global descriptive metrics remain available but are not implicit search
constraints.

For a hotspot, set `aggregation="maximum"` and a positive `averaging_length` in
meters. The window is a square of fixed side length. Its centers lie on an
origin-anchored grid at half that length; windows clip to the selected support
and divide by supported area. The window definition and selected center are
reported. This deterministic sampling is not a continuous search over all window
positions. The optimizer cannot change the averaging window. Raw sharp-corner
maxima remain mesh-sensitive; peak-current optimization requires a fixed physical
averaging length, while other raw maxima require explicit refinement assessment.

## Contacts, physical ports and losses

For a finite interface, `j_normal = g_area * (V_a - V_b)` in A/m². Its local
areal dissipation is `g_area * (V_a - V_b)^2` in W/m². Currents and power use the
same consistent FEM integration as the assembled operator. Each edge-connected
physical contact island receives a geometry-derived identity independent of mesh
numbering, polygon order and collinear subdivision. Loading tables retain signed
current, absolute transfer, area, normal density and power. Absolute loading
fractions use total absolute transfer; signed fractions are unavailable when net
transfer cancels. An array average does not replace a local-density map.

Resistance definitions are intentionally distinct:

| Input | Conductance per area |
| --- | --- |
| Specific contact resistivity, Ω·m² | `1 / rho_c` |
| Bulk via material resistivity, Ω·m, and height, m | `1 / (rho * height)` |
| Lumped resistance with `resistance_semantics="array"`, Ω | `1 / (R * total_mask_area)` |
| Lumped resistance with `resistance_semantics="instance"`, Ω | `1 / (R * individual_island_area)` |
| Direct areal conductance, S/m² | Supplied value |

Identifiable ideal-contact currents come from constraint reactions. Redundant
ideal paths can make individual currents nonunique: those records say so instead
of inventing a split. Ideal zero-drop connections generate no Joule hotspot.

A `PhysicalPort` owns one or more `PortLanding` references to terminal footprints.
Only explicit ideal landings are equipotential. A finite landing resistance is
the resistance of that footprint's distributed feed to its external reservoir;
local sheet potential and loading remain solved. Total-current ports solve an
unknown external potential and distribute current through their landings. Shared
`external_node` IDs declare a common reservoir. Invalid reference/disconnected
source configurations fail explicitly. Passive terminal `kind="probe"` reads
the solution without constraining it. A floating electrode has zero net current
but may redistribute current and is a different physical object.

External-feed loss is reported separately. `thermal_fraction` in [0, 1] selects
how much enters the modeled landing sheet; the remainder stays external. The
loss ledger distinguishes routing metal, other metal, access, channel, vertical
contacts/vias and external feeds. Shared-device views do not add their displayed
losses to that ledger a second time. Conservation compares the disjoint ledger
with terminal work.

The areal load interface uses `electrical_kind="load"`, `purpose="device_load"`
and `electrical_value=R_on_sp` in Ω·m²: `j = delta_V / R_on_sp(T)`. Its optional
linear temperature coefficient is referenced to an explicit positive temperature
and must retain positive resistance over evaluated states. Local solved voltage
determines loading and heat. It is a distributed resistor abstraction, not SiC
transistor, switching, breakdown or drift-diffusion physics. HEMT work retains the
existing local-gate-bias analytical conductivity model.

## Thermal reference and physical stack

Heat leaving the domain is positive; heat supplied by a boundary is negative.
Each reservoir is reported separately with its own reference temperature. Choose
`PowerSettings.thermal_reference_boundary`, an explicit reference temperature,
or a measurement-specific reference. Multiple unequal reservoirs do not have an
automatic mesh-node-weighted ambient. With zero or nonpositive internal power,
device thermal resistance is unavailable even if reservoirs exchange heat. A
multi-reservoir resistance requires an explicitly declared operational reference
and positive internal-power denominator; it is not a unique network resistance.

`Interface.model_kind` distinguishes `effective`, `physical_via` and
`physical_contact`. Effective links preserve expert assumptions, including legacy
effective geometries. A physical via spans facing stack surfaces and has positive
height. `height_mode="stack"` derives that height after thickness/z edits;
`"explicit"` checks the supplied height against the surfaces. A physical contact
requires adjoining surfaces and zero height. Neither is silently substituted for
the other.

Literal stacks check endpoint existence, legal transitions, landing/enclosure,
crossed conductors, unintended material volumes and duplicate connections.
Dielectric sheets crossed by a via must be explicitly listed in `replaces_layers`;
their via footprint is removed before meshing. A parallel effective thermal
coupling between the same endpoints excludes the physical-via footprint, so the
same dielectric area is not counted twice. Unsupported full-sheet replacement is
rejected; remove the redundant sheet explicitly. Intentional physical-contact
truncation needs `allow_partial`; ROI truncation also needs the project's clipped
boundary acknowledgement. Generated terminal/port footprints must retain complete
support rather than being silently clipped into a legal-looking shape.

## Recipes, rules and safe export

The rule deck stores a database unit, allowed physical layers, layer width/space
limits, declared-net separation, via size/pitch/enclosure, legal transitions,
notches, protected masks, fixed regions and keepouts. Coordinates round to the
nearest source DBU, ties to even, then pass integer KLayout width/space/notch
checks. Rules and source must use the same DBU. Vanished/sub-rule shapes fail.
Violations include rule ID, severity, layer/net/object, bounding marker and a
message. A positive circular bend-radius requirement is explicitly unsupported
and rejects a candidate rather than being ignored.

Recipes are declarative data, never arbitrary executable Python:

| Kind | Parameters |
| --- | --- |
| `bus` / `strap` | x/y axis, width or increasing stations and widths, start/end, center; tapered/segmented profiles |
| `finger_feed` | Array axis (`x`/`y`), integer count, pitch, finger width, bus width, single/dual feed ends |
| `via_array` | Integer columns/rows, size, pitch, center, uniform/graded/staggered pattern and bounded grade |

Each recipe has an immutable source hash, version, editable region, selected
parameters and optional bounds. `anchors` declares points that must remain in one
connected island. Projects with terminals also undergo connectivity preflight.
Protected masks, fixed regions and active-device fingerprints constrain changes.
Different net spacing is checked for declared net-bearing sheets at the same
vertical level. Rules are geometric/electrical-spacing constraints and do not
establish off-state breakdown or a safe operating area.

Generation edits a source copy by replacing approved mask support; it does not
overlay the old conductor with a second route. Unedited geometry outside an ROI
survives. A selected reused hierarchy path is cloned, including the affected array
placement. `all_instances=True` explicitly permits editing all placements of that
cell. Unrelated masks, labels, holes and transforms survive the file round trip.
Arrays requiring over 100,000 explicit placements fail with a budget error.

`generate_candidate(project, parameters, output_path)` returns a project,
structured violations and provenance; `valid` gates simulation/ranking. Parameters
may be experiment variable IDs, `recipe.parameter` keys or nested recipe maps.
`export_candidate(project, output_path)` copies the chosen candidate without
rerunning recipes. Both export through real KLayout GDS/OASIS writers and the
actual importer, compare canonical masks and run rule/physical checks. JSON
provenance records source/output hashes and geometry IDs. A verified finalist
requires fresh solves on the actual exported geometry, not only the earlier
in-memory project.

## Equal service, search status and mesh evidence

Experiments bind recipes, bounded variables, seed, operating scenarios, physical
measurements, hard limits, protected devices, evaluation/wall-time budgets,
objective directions and refinement policy. Every candidate receives the same
scenario settings, active geometry and intended width/area. Current-service
scenarios solve the requested total current and check compliance and returned
current. Fixed-voltage scenarios must satisfy a minimum delivered current. A
low-power candidate that simply starves the load fails service.

The unchanged baseline is included. Deterministic bounded sampling considers
continuous, integer and choice variables; duplicate canonical geometries are not
ranked twice. Worst-case or declared weighted-mean scenario aggregation produces
explicit Pareto objectives; no hidden cost or weights replace physical constraints.
Atomic `history.json` records every attempt, source/experiment/software identity,
parameters, geometry hashes, measurements, failures and artifacts. Resume verifies
identity and retained files. Cancellation and resource limits preserve completed
history; interrupted/failed rows never receive favorable zero objectives.
The experiment retains a hash-checked source copy under its own `source/`
directory, so reopening an embedded ETL in another process does not depend on
temporary extraction files. Keep the experiment output directory in place:
history currently records absolute artifact paths and does not automatically
relocate a copied study directory. Individual ETL results remain portable.

`feasible_provisional` means coarse checks passed. Rule violation, service failure,
stale selection, invalid material state, disconnected/singular setup,
nonconvergence, balance failure, cancellation and resource exhaustion remain
distinct explanatory failures. Coarse Pareto finalists and baseline are rerun at
the declared finer mesh factors, with contact/hotspot refinement. A candidate is
`verified_feasible` only when limits and service remain satisfied and improvement
exceeds the combined baseline/candidate mesh-change estimate and declared margin.
Otherwise it remains `mesh_uncertain`. Mesh-to-mesh differences are empirical
stability estimates, not rigorous error bounds or proof of a global optimum.

The **Experiments** tab starts or resumes a saved experiment and opens its
`history.json`. Its **Candidates** subtab keeps exact values and explanatory
status; selecting a row shows scenario service, limits and refinement evidence.
The **Pareto plot** subtab provides selectable horizontal/vertical objectives and
an explicit **Mesh basis** selector. Coarse and refined candidates are never mixed
in one comparison: the default uses the finest feasible evidence when available,
otherwise coarse evidence. Histories retain `coarse_pareto`, `refined_pareto` and
`pareto_resolution`; rows and their original coarse measurements remain available.

Squares mark verified finalists, circles provisional candidates or the baseline,
and triangles mesh-uncertain candidates. Outer rings indicate nondominance in the
displayed objective selection, which can differ from the full multi-objective
frontier. Arrows show preferred objective direction, and labels identify units
and scenario aggregation. Refined-view bars show recorded last-mesh changes,
not statistical confidence intervals; absent bars indicate unavailable estimates.
With one objective, the plot compares that quantity across candidate rows without
inventing a second trade-off. Click a point to inspect exact evidence; **Export
plot** writes SVG or PNG. The table's Pareto-only filter leaves the plot's comparison
context available. Completed experiments automatically export qualifying
finalists. **Export selected verified finalist** repeats that export for the
selected candidate in a chosen directory; both paths recheck eligibility.

For specific-contact-resistivity interfaces, refinement guidance estimates
`L_T = sqrt(rho_c / R_sheet)` on each contacting sheet. It uses the smallest
positive solved local directional sheet conductance on the interface across
the evaluated coarse scenarios, including temperature-dependent metal and biased
HEMT states. A supported constitutive-law fallback at the declared initial
temperature is explicitly labeled when local data are unavailable. The target
is at most `L_T / 4`; a user minimum mesh size that prevents this remains unresolved.
This local refinement guide is not a new transmission-line solver or a guarantee
that every contact geometry is mesh independent.

The final GDS and OASIS outputs are reimported and solved again, and their metric
changes are checked against the mesh/improvement policy. Results include solver
version, material snapshots, tolerances and mesh provenance. Selecting a visually
appealing coarse candidate does not bypass these checks. Optimization presently
uses steady DC/electrothermal scenarios; the existing transient workflow remains
available separately. Full autorouting, unconstrained topology optimization,
electromagnetics, RLC/switching and full 3D are outside this implementation.

## Passive port matrices

```powershell
python -m etlayout multiport .artifacts/power-examples/four_cell_feed.etl --ports supply,return_port --reference return_port --format json --output .artifacts/feed-multiport.json
```

Extraction holds the declared temperature fixed and forms a passive resistive
DC port matrix `I = G V`, with current into the domain. Interior unknowns are
eliminated using a reused sparse factorization. Reports include port order,
reference, fixed temperature, configuration/mesh identity, conservation,
reciprocity and passivity diagnostics. JSON and CSV preserve matrix metadata;
SPICE export emits a positive-resistor subcircuit only when off-diagonal signs
permit it. Extraction rejects HEMT voltage-dependent laws and undeclared driven
electrodes. It is not a transistor small-signal or electromagnetic extraction.

## Migration and reproducibility

Project schema 2 adds these contracts. Schema-1 projects remain supported and
migrate with legacy effective-link and total-array resistance semantics intact.
New files require the updated reader; do not expect v0.2.0 to decode unknown
schema-2 contracts. Already averaged legacy point-touch fields cannot always be
recovered; the reader identifies their limitation and recomputation is required
for authoritative scoped values. Do not treat a legacy nodal display as recovered
physics. Existing transient results keep their original solver provenance.

Experiment cache identity includes source implementation, numerical dependency
versions, Python, physical configuration and the complete experiment definition.
Frozen builds carry the implementation hash generated from the packaged Python
sources plus the required dependency metadata. Missing or malformed frozen
identity blocks experiments with a rebuild diagnostic; it never falls back to a
version-only identity. A changed identity requires a fresh output directory.
Source and frozen histories are compatible only when all recorded identity
inputs match, so resume does not silently reuse results from a different build.

CPU settings remain local and default to four available logical CPUs. Native
library caps and process isolation do not guarantee a speedup for one sparse
solve. The implementation has no mandatory CUDA/GPU dependency. Test results,
coverage methodology and native/frozen verification status are recorded in the
[validation guide](validation.md), not inferred from a prior release.

For current temperature choices, physical cross-probing, extraction-specific mesh
evidence and optional external circuit checks, use the [GUI-led passive-network
guide](passive-network-workflow.md). The [C/L expansion plan](parasitic-extraction.md)
distinguishes experimental result interchange from future field extraction.
