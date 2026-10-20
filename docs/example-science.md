# Scientific evidence for the example library

The forty stable examples answer different questions. An analytical strip is not
supposed to run hot, and a converged synthetic power die is not a calibrated
transistor rating. The example menu now identifies the **applicability class**;
calculation convergence, conservation, mesh/time verification and engineering
limits remain separate observations.

| Applicability | What the evidence supports | What it does not establish |
| --- | --- | --- |
| `analytical reference` (E1, E17) | Comparison with a uniform-strip or lumped thermal-RC limit under stated assumptions | Process calibration, nonuniform device performance |
| `synthetic functional demonstration` | Real geometry, finite contacts and the stated numerical model can reproduce the workflow | Measured device accuracy or a rated operating point |
| `synthetic engineering study` (E20–E24, E39, E40) | Investigation of routing, loading and heat paths with declared synthetic parameters | Qualification, lifetime, breakdown or safe continuous operation |

Applicability, learning level and workload are separate from numerical evidence.
**Original-reference status** is another independent field: E39's original 10 A
configuration is a **stress** experiment, while the browser recommends **Lower-current
SiC reference (2 A)**. Both belong to the same synthetic on-state model. The
original remains available; neither current defines a continuous device rating.
Large/stress family presets likewise require explicit review rather than inheriting
the original reference's acceptance.

No example is classified as a physically calibrated reference. The material
library states its sources and reference-temperature ranges; rounded bulk seeds
and thin-film/interface assumptions require measured process data. Numerical
verification is a separate evidence axis, not a promotion to physical calibration.

## Reproduce an exact configured nominal

Open **File → Ready-to-run examples**, choose the question, review its analysis
mode and inputs, then validate, mesh and run through the normal workflow. Inspect
**Metrics & loss budget** and the Issues drawer before comparing colors. For
transients, compare the complete-run peak and peak time with the cooled final
frame. Electrical-only examples have no calculated thermal maximum.

Maintainers can reproduce a bounded full-catalog observation with:

```powershell
python scripts/audit_examples.py --output C:/temporary/example-audit --save-fields
```

The command never substitutes a drive, adds a heater, changes a tolerance or
starts during example generation. It uses fresh processes with explicit CPU,
memory and wall-time limits. `--examples E1 E17 E39` selects a smaller set.
Detailed fields, native diagnostics and performance receipts belong in the chosen
artifact directory. They should not be committed. `scientific_record` rejects a
result whose configuration or physics identity differs from its project.

Each receipt records SI quantities, exact layout/project identities, dimensions,
connected regions, stack/material sources, contacts and thermal boundaries,
terminal/port responses, disjoint power categories, thermal input/removal,
transient energies/peak, raw field ranges, model/property diagnostics, mesh sizes,
elements/DOFs, measured phases and archive save/reopen cost. Missing quantities
remain null. A single-mesh observation is explicitly **not mesh verified**.
The contact-transfer screen reuses the existing local conductance estimate;
maximum triangle edge versus transfer length is conservative, particularly for
anisotropic finger meshes, and is not an error estimator.

## Geometry and interpretation findings

- **E7/E8:** the source finger and gate-bus masks cross in plan, but their physical
  volumes do not overlap. The gate starts at z = 2.4 µm; source metal ends at
  2.1 µm (E7) or 0.8 µm (E8). The insulated crossing keeps the gate fingers
  connected. Omitted dielectric thermal paths are effective assumptions. The
  ungated channel-edge bypass is an intentional teaching contrast with E16 and
  the fully gated power examples, not predicted off-state leakage.
- **E16:** all four channel paths cross its gate. The independent 0.2 mW source
  is prescribed thermal excitation and adds to the much smaller channel Joule
  power in coupled mode. It is not electrical loss. E11/E15 likewise retain
  explicitly prescribed loads for thermal-only teaching.
- **E20–E24:** generated physical-via height now uses the same surface-to-surface
  expression as geometry preparation. This removes floating-point identity drift
  without changing the designed layout or material values. Older project
  archives remain readable; existing normalized physical hashes are unchanged.
  The correction prevents a few floating-point units of serialization drift
  during explicit geometry inspection.
- **E23/E24/E39:** the vertical `device_load` ledger category is intrinsic
  area-normalized on-state loss, not conventional metal/contact resistance.
  E39's four 320 × 320 µm islands have 0.4096 mm² active area: 10 A means
  2441.4 A/cm², and 1 mΩ cm² implies a 0.24414 Ω uniform device-only limit at
  300 K. Treat that configured nominal as a stress experiment.
- **E18/E19/E36:** a cooled final frame can be far below the accepted-time peak.
  Preserve both and the input/output/stored-energy ledger. Thermal balance of a
  transient includes storage; instantaneous heat-out need not equal input.
- **E26/E29/E30/E40:** point maxima at ideal sharp corners or contact/via edges
  remain mesh-sensitive. Fixed physical averaging supports and integral losses
  are better comparison quantities. GDSII/OASIS identity is checked independently
  of a plotted screenshot.

All disconnected parts, clipping and nonsimple-polygon diagnostics remain visible
in the machine-readable observations; the audit does not suppress warnings to
make an example appear clean. No 3D or experimental comparison has been inferred
from a converged 2.5D solve.

See [the complete nominal matrix](example-science/nominal-reference.md),
[guided examples](examples.md), [power examples](power-examples.md) and
[extended device walkthroughs](extended-examples.md). The matrix retains original
observations independently of later optional operating scenarios.

For stronger Joule heating with unchanged reference geometry/material/cooling,
see [editable thermal operating scenarios](thermal-example-scenarios.md). The
original reference is always available; failed engineering limits retain fields.

## Follow device-specific operating studies

[Device engineering examples](device-engineering-examples.md) link the original
GaN/SiC models to voltage-driven coupon/bank studies, lower-current SiC operation,
semiconductor heat placement and package fixture deembedding. The
[combined all-forty index](example-science/device-observations.json) joins original,
thermal-scenario and engineering observations without repeating field archives.
