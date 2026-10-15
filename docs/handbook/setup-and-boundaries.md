# Geometry, materials and boundary conditions

[Handbook](README.md) · [← Model and physics](model-and-physics.md) · [Next: workflows →](workflows.md)

A useful model records the experiment as well as the drawing: what is connected,
what supplies current or heat, and where energy leaves. Build these choices
explicitly before interpreting a field map.

## Start with the intended physical domain

Import a GDSII/OASIS file and choose its top cell or hierarchical instance. Mask
keys use `layer/datatype`, for example `1/0`. Paths become polygons; text labels
do not become conductors. Instance transformations, holes and array placement
are retained.

Map each conducting or thermal sheet to a physical layer. A via mask normally
defines an **interface area**, rather than a separate laterally conducting
layer. An insulator still conducts heat according to its assigned thermal
properties, but has no electrical sheet solve.

Layer geometry can combine masks by union, intersection, difference or exclusive
or, and can include explicit polygons. A full-domain sheet covers the simulation
domain without needing a source mask. Explicit substrate polygons can extend
beyond the imported metal.

Use a **simulation ROI** when reducing the physical problem, and a **display ROI**
when only changing the view. A simulation cut through a conductor introduces a
new boundary. The default is no current flow across that cut unless a terminal
is assigned. Review and acknowledge cut boundaries rather than using a cropped
layout as an unexplained replacement for its original circuit.

## Assign stack and materials

For each physical layer, set its ID, role, material, thickness and bottom
elevation z. Reordering a list does not move a layer physically. Use a cross-section
to check the intended stack and overlaps.

| Input | API/storage unit | Example conversion |
| --- | --- | --- |
| x, y, z, thickness, mesh size | m | 2 µm = `2e-6` |
| Absolute temperature | K | 27 °C = 300.15 K |
| Electrical resistivity | Ω m | 1 µΩ cm = `1e-8` Ω m |
| Sheet resistance | Ω per square | Enter the numerical value in Ω |
| Carrier density | m⁻³ | 10¹⁸ cm⁻³ = `1e24` m⁻³ |
| Mobility | m²/(V s) | 400 cm²/(V s) = `0.04` m²/(V s) |
| Thermal conductivity | W/(m K) | No area or thickness factor included |
| Specific electrical contact resistivity | Ω m² | 1 Ω µm² = `1e-12` Ω m² |
| Thermal area resistance | m² K/W | Distinct from a total resistance in K/W |

Engineering-unit inputs in the desktop perform conversion. Python and CLI
parameter values use SI. Read the declared unit, especially for area, edge and
total quantities.

Projects contain complete material snapshots. Editing a library record does not
silently change an existing project. Copy a nominal reference into an editable
material, document its process basis, and check the temperature range of its
resistivity and thermal-conductivity laws. A useful calibration sequence is
geometry and thickness, isothermal sheet resistance, contact resistance, then
thermal links; otherwise several uncertain inputs can compensate for one another.

## Electrical boundaries and contacts

| Terminal kind | What is constrained | Typical use |
| --- | --- | --- |
| Ground / voltage | Equipotential at 0 V / a specified voltage | Reference and voltage drive |
| Total current | Equipotential with a specified total current in A; voltage is solved | Current-driven conductor |
| Floating | Equipotential with zero integrated current | Ideal voltage sense electrode |
| Distributed density | Prescribed distributed source | Area or sidewall injection |
| Insulated | No applied terminal injection | Explicit no-flux annotation |
| Gate | Local charge control on a HEMT sheet; ordinary metal gates also constrain that metal potential | Effective gated-channel experiment |

Positive terminal current enters the model. For a density terminal on an area,
the input in A/m² integrates over that area. For a density terminal along a sheet
edge, the same volume-current-density input integrates over thickness × edge
length. A total-current terminal instead imposes one integrated load and lets
the solver determine the spatial current distribution.

Every independently driven conducting component needs an electrical reference.
The explicit `floating_policy="reference"` option can add a reported zero-voltage
gauge to an otherwise balanced floating component. It cannot repair a component
with incompatible nonzero net injected current.

Declare interlayer connections on their actual overlap:

| Electrical interface | Input | Distributed conductance |
| --- | --- | --- |
| Ideal | No finite resistance | Corresponding unknowns identified |
| Specific contact | $\rho_c$ in Ω m² | $1/\rho_c$ |
| Lumped mask resistance | $R$ in Ω | $1/(R A_{\mathrm{mask}})$ |
| Conductive via | Material resistivity and height $h$ | $1/(\rho_{\mathrm{via}}h)$ |
| Conductance | Value in S/m² | Supplied value |

A lumped mask resistance is distributed over its mask. Lateral access resistance
and current crowding remain part of the solved layout; it is not a point circuit
element. Electrical and thermal interface settings are independent. Assigning
electrical via resistance does not automatically choose a thermal conductance.

## Thermal boundaries and sources

| Thermal input | Unit | Interpretation |
| --- | --- | --- |
| Fixed temperature | K | Selected nodes held at that absolute temperature |
| Coefficient | W/(m² K) | Heat flow per area per temperature difference |
| Area resistance | m² K/W | Reciprocal of coefficient |
| Total boundary resistance | K/W | Converted using the actual selected area |
| Prescribed total heat | W | Distributed over the selected source area |
| Prescribed heat density | W/m² | Integrated over the selected source area |

For an edge heat-loss boundary, the side area is layer thickness × selected edge
length. Keep area and total boundary resistances distinct. Specifying a heat-transfer
coefficient models an effective thermal connection; it does not add a solid
substrate or resolve the fluid or package attached to it.

Thermal links can use conductance, area resistance, or a stack of adjacent half
thicknesses and an optional material gap. Explicit links can also approximate
omitted dielectric paths, as in E12. Record that approximation and calibrate it
for the intended process. A source specified separately from electrical losses
is **additional heating**, including in coupled mode.

## Validate before increasing mesh density

Run geometry and connectivity validation. Resolve errors such as empty masks,
missing materials, disconnected driven conductors, conflicting terminal values,
missing thermal sinks or zero-area contacts. Warnings can be intentional: an
interface defined over the full domain is clipped to the two sheets' common
area. Confirm that this is the expected footprint.

Then mesh and inspect contacts, narrow routes, holes, corners and boundary
endpoints. Local refinements concentrate elements where geometry and gradients
demand them. A finer mesh cannot create a missing interface, correct a unit
error, or choose the missing thermal boundary for you.

Detailed geometry APIs and diagnostic rules are in [geometry](../geometry.md).
The available types and defaults are defined in
[`models.py`](../../src/etlayout/models.py).

[← Model and physics](model-and-physics.md) · [Calculation workflows →](workflows.md)
