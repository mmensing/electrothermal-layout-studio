# Measured power and routing demonstrations

These results were measured on 12 October 2026 using a historical pre-public
0.3.0 source snapshot, with
implementation SHA-256 `168388106f5f2c578c5e819f5290c5b976ae26c8c8db168a89ab4145cd3194da`.
The [machine-readable receipt](example-provenance/power-routing.json)
contains the full experiment settings, source/software identities, individual
contact and device measurements, scenario results, mesh evidence, export hashes
and CLI resume outcomes. Detailed result archives are not distributed with source. These historical
measurements are not a fresh validation of the public edition. Reproduce the
maintained generator and CLI commands below on a named public revision.

The host ran Windows 11 build 26200 and Python 3.12.14 on an AMD processor with
eight physical cores, sixteen logical CPUs and 33,476,079,616 bytes of reported
RAM. Numerical runs used four native CPU threads. Coverage tests and a frozen
application build ran concurrently, so these are observed timings rather than
isolated performance benchmarks. The search is bounded sampling, not a claim
of global optimality.

## A: multilayer metal, contacts and a verified routing improvement

The four-cell project combines a nonrectangular feed, an upper strap, two
physical copper vias, a finite-resistance source landing, four fixed areal
loads and a named 300 K backside reservoir. Electrical and coupled solves were
saved with their actual solved configurations, closed, reopened and compared:
authoritative element fields and power-analysis records were unchanged. JSON
and category CSV reports were exported from the reopened results.

At 50 mA, the coupled baseline's two vias carried 30.7366 and 19.2634 mA in the
strap-to-feed direction. The four active-cell currents were 11.6541, 13.6912,
13.3181 and 11.3366 mA; their local drain-side potentials ranged from 1.8896 to
2.2822 mV above the return. The solved sharing is not a prescribed equal split.
The disjoint loss ledger totalled 454.819 µW: 302.198 µW routing metal,
22.393 µW vias, 104.884 µW active loads and 25.345 µW external feed. Its peak
temperature was 302.246 K, outside the active cells themselves.

The 300 K passive port reduction used one sparse factorization. Its KCL error
was 1.86e-12 S, reciprocity error 5.33e-15 S and minimum symmetric-part
eigenvalue 1.57e-12 S, against a 5.51e-8 S validation tolerance. Matrix-predicted
terminal currents agreed with the parent FEM's ±50 mA excitation. JSON, CSV
with metadata, and a SPICE resistor subcircuit were written.

Eight configurations were evaluated at both 50 and 100 mA with 0.5 V
compliance. The selected configuration used a 6 µm bus, 6 µm strap and eight
vias. Comparable refinement gave:

- Worst routing loss: **1.228267 → 0.598108 mW**, a **51.30%** reduction.
- Scoped RMS metal current density: **11.0710 → 6.80233 GA/m²**, a **38.56%** reduction.
- High-current peak temperature: **309.182 → 301.297 K**. Delivered current
  remained 100 mA; the required source voltage changed from 18.3967 to 11.5706 mV.

The largest tracked mesh change was 0.280% for the baseline and 0.527% for the
finalist. The routing-loss gain was 630.160 µW, exceeding the combined
mesh-change estimate of 5.055 µW. These differences are empirical uncertainty
estimates, not rigorous error bounds. Independent GDS and OASIS reimports
produced 0.598107613 and 0.598107582 mW routing loss and retained feasibility.
The candidate therefore reached `verified_export`. Reopening the original
project and resuming through the CLI returned exit code zero with unchanged
history.

The initial DC mesh had 4,839 nodes and 9,434 elements; the solve took 1.01 s.
The initial coupled solve took 5.27 s. Refined meshes had 17,696/34,905
nodes/elements for the baseline and 18,317/36,148 for the finalist; individual
coupled refined scenarios took 18.6–23.3 s. The full demonstration, including
search, reimported solves and resume, took 298.67 s. Process peak working set
was 351.86 MiB.

## B: fixed HEMT fingers and an honestly unresolved finalist

The HEMT project retains E8's analytical channel and fixed channel, gate,
contact, field-plate and original finger masks. Its baseline places gate metal
above source/drain metal before introducing physical source vias. Six bounded
variables control two source-bus segment widths, single/dual-ended upper
feeds, upper-strap width, via columns, and uniform/graded/staggered placement.
The six sampled configurations include the original single-ended baseline and
dual-ended alternatives; they do not exhaust the design space.

At the 1 mA, 0 V gate-bias condition, the four solved finger currents were
0.252900, 0.246435, 0.246309 and 0.254357 mA. Local source-side potentials ranged
from 0.151852 to 0.232419 mV, while local drain-side potentials were
15.7088–15.7121 mV. The nine upper-feed vias carried both current directions,
from −0.190356 to +0.100484 mA, and their signed total was approximately zero:
the upper feed redistributes current within the source network. Averaging
that signed total would conceal real individual-via loading.

The nominal coupled solution reached 300.001832 K and dissipated 15.8861 µW.
Its separate loss categories were 0.2030 µW routing metal, 9.5948 µW access
channel, 5.5268 µW gated channel and 0.5615 µW contacts. Electrical and coupled
archives reopened with exact authoritative fields and quantitative reports.

All six coarse candidates met the 1 mA/0 V-gate and 2 mA/−1 V-gate scenarios
with 2 V compliance. The best coarse source-routing loss changed from
0.480909 to 0.305533 µW, and scoped RMS current density from 96.1217 to
70.0407 MA/m². **These apparent improvements are not verified.**

Refinement estimated a shortest source-contact transfer length of 0.243 µm,
requiring about 0.061 µm resolution under the declared quarter-length policy.
The configured refined minimum was 0.125 µm. Baseline refinement generated
479,337–479,355 elements and the finalist 476,387–476,401, exceeding the
250,000-element limit. Both retained `mesh_uncertain` evidence; no HEMT
candidate was verified or published, and no verified HEMT export is claimed.
The history's coarse Pareto view remains available. Its saved project resumed
through the CLI with unchanged history.

The coarse baseline had 7,441 nodes and 14,579 elements. DC took 3.23 s,
the initial coupled solve 15.67 s, and search scenarios 15.5–20.8 s. Including
negative cases and failed refinement attempts, this demonstration took
301.80 s. Cumulative process peak working set reached 432.98 MiB. A further
verification run needs explicit mesh/DOF budgets and minimum sizes adequate
for the measured transfer length; increasing a budget alone does not establish
mesh convergence. The analytical HEMT and effective substrate links still need
process calibration and do not model switching, breakdown or field-plate
electrostatics.

## C: rejection and compatibility evidence

The demonstration preserved these failed cases rather than ranking them:

- A 0.1 µm source-bus variable outside its 6–12 µm bounds was rejected before
  layout generation or a numerical solve.
- Opening the fixed source/channel contact in a separate negative model
  produced an unreferenced component with 1 mA unbalanced injection and
  `invalid_physical_model`. Legal routing variables cannot make that edit.
- A converged 5 mV bias delivered only 0.314930 mA against the required 1 mA,
  producing `service_failure` and an empty accepted Pareto set.
- A retained earlier 1 nV experiment produced about 6.3e-20 W. Its thermal
  response rounded to zero at the 300 K reference, so relative thermal balance
  rejected the coupled solution as `nonconverged`. The receipt identifies the
  earlier implementation hash; this thermal limitation was not hidden or
  treated as a favorable zero-loss result.

Two limitations remain visible in these results. The HEMT mesh-budget
exception is currently classified under the generic `invalid_physical_model`
status, although its retained diagnostic explicitly identifies the element
budget. Sub-resolution heat can require a separate electrical calculation
instead of a coupled result. Neither case enters verified ranking.

The [scientific acceptance guide](scientific-acceptance.md) records
separate automated evidence for legacy field ambiguity, point-touch fields,
empty measurement support, multiple thermal reservoirs, stack inconsistency,
and interrupted histories. A discovered embedded-archive resume defect was
fixed before this final run: experiment histories now retain their immutable
source layout rather than depending on a temporary extraction directory.

## Reproduce the experiments

Generate fresh inputs and run the same declared experiments from the checkout:

```powershell
python examples/power_routing/generate.py --output .artifacts/reproduce-power
python -m etlayout optimize .artifacts/reproduce-power/four_cell_feed.etl --experiment four-cell-feed --output .artifacts/reproduce-feed-search
python -m etlayout optimize .artifacts/reproduce-power/hemt_source_feed.etl --experiment hemt-source-feed --output .artifacts/reproduce-hemt-search
```

Repeat an `optimize` command to resume its unchanged history. In the GUI,
open the project and use the Power workbench's Experiments tab to inspect
coarse/refined Pareto evidence and failed rows. The receipt records all
selected parameters and mesh policies; do not substitute a changed project
into an existing history directory. See the [workflow guide](POWER_ROUTING.md)
for physical port, measurement, export and background-execution settings.
