# Extending extraction to capacitance and inductance

## Current boundary

The [passive-network workflow](passive-network-workflow.md) extracts a
**fixed-temperature DC resistor network**. It does not extract capacitance,
inductance, skin/proximity effects or transistor small-signal parameters.

The experimental, Qt-free [matrix interchange API](../src/etlayout/power/parasitic_models.py)
is the first C/L development increment. It validates **supplied** real static
operators, their electrical basis and stored energy, and round-trips bounded
JSON. It does not solve fields, infer a model from GDS, run an external solver,
produce RLC SPICE or add a C/L GUI option. Its
[analytical tests](../tests/test_parasitic_models.py) validate the contract;
they are not field-solver verification of application examples.

ETLayout's existing thermal dielectric properties do not specify electrical
permittivity. A thickness-averaged sheet and projected overlap do not define a
3D magnetic return path. These inputs must become explicit before a field-solver
adapter can truthfully extract C or L.

## Implemented result contract

`ParasiticMatrix` distinguishes two bases and never infers one from matrix signs:

| Kind | Axes and reference | Coefficients and material inputs | Stored energy |
| --- | --- | --- | --- |
| `capacitance` | Named equipotential conductor boundaries, each with geometry support IDs; a distinct, explicit fixed zero-volt reference boundary | Reference-reduced Maxwell C in **F**; positive real linear permittivity in **F/m** | `0.5 Vᵀ C V` in **J**, with every V relative to that reference |
| `loop_inductance` | Named, oriented **closed 3D circuit contours**, including distinct forward/return support IDs and an explicit closing vertex | Loop L in **H**; positive real linear permeability in **H/m** | `0.5 Iᵀ L I` in **J**, with current signs following the declared contours |

Partial inductance, branch inductance, inverse capacitance, relative material
coefficients, per-length quantities, complex/lossy operators and nonzero-frequency
operators are unsupported conventions. Adapters must convert deliberately or
reject them. A reference conductor is not an inferred magnetic return.

The version-1 JSON requires SI units, an explicit schema version, external
geometry/mesh/result SHA-256 identities, solver/version, fixed positive
Kelvin temperature and an approximation statement. No paths, credentials or
executable content are needed. Duplicate/unknown keys, nonfinite coefficients,
ambiguous units, invalid dimensions, open/degenerate contours and conflicting
support IDs fail explicitly. The operator is bounded to 128 axes and interchange
to 1 MiB.

Reciprocity and passivity are checked after per-axis energy scaling, using a
machine-roundoff bound, so a strong axis cannot mask a weak negative-energy
subnetwork. Grounded Maxwell C additionally requires nonpositive off-diagonal
terms and nonnegative reference coupling. Signed mutual L is allowed. Values
are never clipped, sparsified, averaged into a replacement matrix or repaired.
Energy evaluation rejects a negative/nonfinite result.

These checks cannot authenticate a supplied hash, establish field convergence,
resolve IDs against external geometry, prove complete material coverage or
validate 3D conductor intersections. A contour establishes orientation; it does
not replace conductor cross-sections or the external current-distribution model.
The approximation statement must distinguish external-only inductance from
models including conductor-internal energy. None of these records automatically
receives ETLayout's mesh-verification or engineering-acceptance status.

The independent fixtures use the uniform parallel-plate limit
`C = epsilon A / d` and long concentric-shell coaxial limit
`L = mu length log(b/a) / (2 pi)`. Energy is checked against uniform electric-field
volume and a separate magnetic-energy quadrature. Plate fringing, coax end fields
and conductor-internal inductance are excluded explicitly. These are analytical
limits, not formulas applied to arbitrary example layouts. The underlying field
and energy relations are described in OpenStax's
[capacitance](https://openstax.org/books/university-physics-volume-2/pages/8-1-capacitors-and-capacitance)
and [magnetic-energy/coax example](https://openstax.org/books/university-physics-volume-2/pages/14-3-energy-in-a-magnetic-field).

## Implementation sequence and acceptance gates

1. **Audited 3D input snapshot.** Add explicit electrical permittivity/permeability
   and temperature/validity ranges without guessing them from thermal materials.
   Preserve actual solid thickness, dielectric interfaces, holes, via volumes,
   localized terminals, reference conductors and return paths. Require unambiguous
   material precedence and geometry-to-result identity. Technology/PDK data stays
   subject to its redistribution permission.
2. **Electrostatic C adapter.** Start with an independently installed solver such
   as [FasterCap](https://www.fastfieldsolvers.com/fastercap.htm), or an explicit
   [KLayout-PEX/PEX25D](https://iic-jku.github.io/klayout-pex-website/doc/doc.html)
   exchange adapter. Export deterministic inputs and parse a declared matrix
   convention. Test parallel plates with a fringing-controlled limit, analytic
   coax capacitance, shielding, multiple conductors and holes. Require boundary
   enlargement and mesh refinement, charge/energy checks, reference correctness
   and documented SI conversion. A generated deck alone is not a successful solve.
3. **Closed-loop RL adapter.** Evaluate [FastHenry2](https://www.fastfieldsolvers.com/fasthenry2.htm)
   for magnetoquasistatic R/L. Require complete return conductors and a clear
   partial-to-loop basis transformation; do not import partial self-L as loop-L.
   Verify coax/parallel-conductor limits, mutual orientation, DC resistance,
   skin/proximity resolution and frequency validity before broadening the
   current real-static interchange to frequency-dependent impedance.
4. **Circuit-model integration.** Only after field verification, add C/L
   workflow choices, provenance and mesh-confidence reporting, and circuit export.
   Check passivity, reciprocity, causality and independently executed port
   responses across the declared bandwidth. A fitted equivalent circuit needs
   holdout/error bounds and a rejected-fit state. Keep electrothermal feedback
   and transistor models separate from passive parasitics.

Each adapter must preserve working physics/results, use cancellable background
jobs, impose memory/time/output budgets and execute only an explicitly selected
local tool. Test imported output as data. Do not bundle a field solver or
change the Windows distribution until its exact dependency/source/license and
packaging obligations have been reviewed. No additional dependency is introduced
by the current contract.

## Comparables informing the plan

- [KLayout-PEX](https://github.com/iic-jku/klayout-pex) connects layout/technology
  data to extraction engines and provides inspectable matrix/netlist workflows.
  Its current documentation distinguishes usable engines from planned RL and
  RC/RCC work; adopting an interchange format does not adopt those capabilities.
- [Palace](https://awslabs.github.io/palace/dev/) provides 3D electrostatic C and
  magnetostatic L alongside full-wave FEM and adaptive refinement. It is a
  possible independent benchmark, with a much larger execution footprint.
- [openEMS](https://docs.openems.de/en/latest/intro.html) solves full-wave time-domain
  electromagnetics; its port/S-parameter and reference-plane concepts are useful
  later. Its electrical transient is different from ETLayout's thermal transient.
- [Q3D](https://ansyshelp.ansys.com/public/Views/Secured/Electronics/v261/en/Subsystems/Q3DExtractor/Content/GettingStarted/WelcometoQ3DExtractorHelp.htm)
  is a commercial reference for separated RLGC extraction and SPICE workflows.
  Comparable labels do not establish comparable physical scope or accuracy.
