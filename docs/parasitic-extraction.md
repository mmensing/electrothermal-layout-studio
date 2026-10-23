# Capacitance and closed-loop RL extraction

The [passive-network workflow](passive-network-workflow.md) reduces the existing
fixed-temperature conduction model to a DC resistor network. **Parasitics**
prepares a literal three-dimensional field model and can run an explicitly
selected external field solver. Generation alone is never reported as a solve.
These are experimental source capabilities; no field engine is bundled or
downloaded automatically. They do not replace electrothermal simulation or
produce a fitted broadband RLC SPICE model.

## Start with a reference in the GUI

1. Open **Studies → Capacitance & loop inductance**, then **Parasitics**.
2. Choose the field kind and **Create ready-to-open reference**. This writes
   GDSII, OASIS and a portable project without starting a calculation. Use
   **File → Open** to open that new `.etl` project before continuing.
3. Review the stack and **Configure field inputs**. Transport references do not
   establish electromagnetic properties: supply their own source or an explicit
   synthetic assumption and valid temperature range.
4. Use **Prepare inputs only** to inspect geometry, identities and refinement
   decks in a new/empty folder.
5. Select a trusted local console executable and **Calculate & check refinement**.
   Inspect raw-operator acceptance and mesh sensitivity separately; failed checks
   remain visible with diagnostics.

The same workflow needs no Python editing:

```console
python -m etlayout field-reference loop_rl loop-reference
python -m etlayout parasitic loop-reference/field_closed_return.etl --kind loop_rl --prepare-only -o loop-inputs
python -m etlayout parasitic loop-reference/field_closed_return.etl --kind loop_rl --executable /absolute/path/to/fasthenry --frequencies-hz 0 1 10 --refinements 1:1,2:3,4:5 --mesh-tolerance 0.02 --timeout 180 -o loop-calculation
```

On Windows, use the console executable's absolute `.exe` path. Imported decks,
shell commands and simulator startup files are never executed. Cancellation
owns the entire child-process group. Time/output/memory budgets apply to the
job: Windows uses a Job Object commit cap plus sampled RSS; other platforms use
sampled process-group RSS. Resource-limited results cannot be verified.

## Project compatibility

Electromagnetic material laws and versioned field settings are optional. Projects
without them retain their prior serialization and DC/thermal identities. Adding
or undoing only these inputs retires field evidence while preserving compatible
conduction results and meshes; retained runs keep their original setup hashes.
Configured field inputs require this newer source reader. Older applications can
reject unknown fields; keep the original project when exchanging with an older
installation. No migration silently discards configured electromagnetic inputs.

## Explicit three-dimensional inputs

The shared snapshot resolves normal project geometry, physical thickness,
holes and actual via replacement volumes. It preserves source/configuration and
geometry/model hashes, fixed Kelvin temperature, evaluated material values and
their references. Unsupported coefficients are rejected rather than averaged.

| Model | Electrical basis | Supported first scope | Boundary |
| --- | --- | --- | --- |
| Electrostatic C | Whole connected equipotential metal bodies; infinity at zero volts | Finite PEC conductors in an explicitly homogeneous positive real permittivity | No heterogeneous interfaces, semiconductor depletion/quantum capacitance or dielectric loss |
| Closed-loop RL | Oriented finite forward/return bars with a physical excitation cut | Separate closed nonmagnetic bulk-conductor loops, frequency-dependent impedance | No inferred ground return, shared conducting plane, arbitrary sheet-network reduction or radiation |

Effective sheet interfaces and HEMT laws are not literal bulk conductor volumes.
Preserve those models in the conduction workflow. An unsupported field model
fails explicitly rather than substituting ideal metal.

## Closed-loop RL calculations

The FastHenry adapter writes rectangular conductors in **meters** and bulk
conductivity in **S/m**. Declared bars must cover every assigned conductor
component and close through actual source volumes. Independent loops use
separate conductive components. Coincident but distinct excitation-cut nodes
retain all finite return bars; `.equiv` never erases a distant connector.

This is a rectangular-bar approximation with ideal end-node junctions, not
corner-current spreading in arbitrary polygonal buses. Bounds are eight loops,
128 physical segments, eight frequencies and three to five refinement levels.
Defaults are 1,024 maximum filaments and 1 GiB memory. The structure must be
electrically small: extent/background wavelength ≤ 0.01. Hierarchy does not
reduce the field cost.

The result preserves unmodified **Z(f)** in ohms. **R(f) = Re Z(f)** in Ω;
**L(f) = Im Z(f)/(2πf)** in H only when `f > 0`. At DC the engine omits magnetic
formation: **`L_H` is null**. Default frequencies are 0, 1 and 10 Hz. Agreement
between positive-frequency points is observed low-frequency sensitivity, not
proof of an exact static limit. Skin/proximity behavior at higher frequencies
requires its own filament study; skin-depth ratios are diagnostics.

Refinement increases longitudinal and uniform cross-section subdivisions. Every
R/L entry is compared separately, so weak mutual terms cannot disappear under a
global absolute tolerance. DC is checked against independent series-bar R.
R and L reciprocity/positive energy are checked separately after per-loop
scaling. Matrices are never clipped, sparsified or averaged into replacements.

Reviewed FastHenry 3.0wr mod031424 prints six significant digits in `Zc.mat`.
Reports record its **5×10⁻⁶ relative serialization floor**, raw matrices, actual
confirmed filament counts, deck/log and executable hashes, engine version,
timings and memory. The default spatial sensitivity limit is 2%; output precision,
mesh sensitivity and physical calibration are distinct. Existing DC SPICE and
static-matrix acceptance criteria are unchanged.

### Measured RL references

Native Windows source tests used unmodified FastHenry 3.0wr mod031424, one
external thread, direct LU and no multipole approximation. These are not frozen
package or clean-machine tests. The workstation has an AMD Ryzen AI 7 PRO 350
(8 cores / 16 logical CPUs); each external calculation uses one worker/thread.
Reported times are observations on this machine, not performance guarantees.

| Case | Actual result | Validation/resources |
| --- | --- | --- |
| Ready-to-open 1 × 0.5 mm centerline return; 50 × 10 µm synthetic Cu bars | DC **0.10344 Ω**; **1.89810 nH** at 1 and 10 Hz | GDS/embedded-project reopen; 4/72/400 actual filaments, nine calculations; all checks passed. Preparation 0.016 s, calculation 1.312 s, sampled peak RSS 16.3 MB |
| Independent 1 × 0.2 mm loop; 20 × 20 µm bars, σ = 5.8×10⁷ S/m | DC **0.103448 Ω**, refined low-frequency **1.391350 nH** | Series R and independent Neumann volume-current energy quadrature agree within native precision |
| Two separated unequal-width loops; 5/7/9 cross-section filaments | Self L **1.391350/1.285916 nH**, mutual **−13.6714 pH** | All entries passed at 0/1/10 Hz; 1,944 finest filaments, 12.032 s, peak RSS 104.5 MB. Reversing one contour reverses mutual sign |

The coarse two-loop study failed weak mutual-R sensitivity; finer filaments
resolved it without tolerance changes. A 100 MHz study increased R but its final
R change was 4.66%, so it stayed **not verified**. Process success or a plausible
value is insufficient acceptance.

## Electrostatic C calculations

The FastCap2 adapter computes the three-dimensional Maxwell operator **Q = C V**
from actual closed outward surfaces, in farads. Every finite connected conductor
is an independent voltage axis; infinity is explicitly fixed at zero volts. A
conductor named ground is retained. Multiple DC ports on one connected metal
body are one electrostatic conductor. Off-diagonal Maxwell entries are negative
mutual coefficients; a diagonal also includes its other couplings, so it is not
simply a two-terminal capacitor value. See the upstream
[Maxwell matrix explanation](https://www.fastfieldsolvers.com/Papers/The_Maxwell_Capacitance_Matrix_WP110301_R03.pdf).

Configure positive homogeneous relative permittivity and its physical material
assumptions explicitly. This first scope rejects mixed dielectric interfaces,
unknown dielectric coefficients, effective sheet/via laws and semiconductor
channel physics. Physical vias and holes use their actual finite boundaries.
Surface preparation removes internal faces, preserves the source volume and
checks closed, conforming orientation. Directional wall subdivision resolves tall
prisms before common refinement; hierarchy does not reduce field cost.

The ordinary FastCap2 table silently averages reciprocal entries. Select the
reviewed original-output build described in
[optional field-engine builds](field-engine-build.md), which prints unmodified
raw coefficients at 17 significant digits and declares its native permittivity
constant. Stock averaged output is rejected. No tool is bundled or downloaded.

The default study uses 100/50/25 um surface lengths, at most 20,000 panels per
level, one external thread and a 1 GiB memory limit. **Direct interactions** adds
a slower all-pairs calculation capped at 2,000 panels; it does not guarantee
strict acceptance. Every raw entry is compared on successive distinct meshes.
Raw calculation, panel sensitivity and strict static interchange remain separate:
reciprocity defects are measured and retained; coefficients are never averaged,
clipped or repaired into a pass. Failed time/memory/panel budgets stay visible.

GUI references can be reproduced without editing project JSON:

```console
python -m etlayout field-reference capacitance plate-reference
python -m etlayout parasitic plate-reference/field_plates.etl --kind capacitance --prepare-only -o plate-inputs
python -m etlayout parasitic plate-reference/field_plates.etl --kind capacitance --executable /absolute/path/to/fastcap-etlayout.exe --timeout 180 -o plate-fields
```

An unaccepted raw operator returns CLI status 2 with retained diagnostics even
when the field solve and panel sensitivity succeed. Review that result; it is
not a validated capacitor/SPICE export.

### Measured C references

Native Windows source calculations used reviewed FastCap2 2.0wr rev010524 with
its output-only wrapper, one external thread and unchanged `-o6 -t1e-10`.
These are not frozen-package or clean-machine tests.

| Actual geometry | Calculation and sensitivity | Acceptance/resources |
| --- | --- | --- |
| Ready-to-open two 200 x 200 x 5 um plates; 20 um face gap, vacuum | 224/1,920/15,872 panels; refined self **26.4877 fF**, mutual **21.6888 fF**; maximum successive entry changes **1.213% / 0.443%** | Raw field and panel checks passed. Reciprocity defect **1.85e-5** rejects strict interchange. Total **18.88 s**, sampled peak RSS **384.6 MB**; GDS/embedded project reopened before calculation |
| Isolated 1 mm cube | 48/176/2,496 panels; self **73.3658 fF**; changes **1.568% / 0.878%** | Strict interchange and panel checks passed. Doubling all lengths gives exactly **2x C**; homogeneous relative permittivity 4 gives exactly **4x C** |
| Two 1 mm square plates, 10 um thick; 50 um face gap, vacuum | Direct 96/224/1,920 panels; mutual **198.7877 fF**; changes **1.007% / 1.109%** | Panel checks passed; strict interchange remains rejected. Finite-plate fringing raises mutual C **12.26%** above the **177.0838 fF** parallel-plate limit |
| Finite 2 mm coax, regular 16-gon inner/outer radii 50/100 um; outer metal to 125 um | 1,136/1,904/3,440 panels; mutual **164.1630 fF**; changes **0.367% / 0.0734%** | Panel checks passed; strict interchange rejected. **2.27%** above the **160.5215 fF** infinite circular-coax limit, including end/fringing and polygon geometry differences. Total **8.37 s**, sampled peak RSS **79.7 MB** |

The plate and coax analytical limits are independently derived in
[OpenStax](https://openstax.org/books/university-physics-volume-2/pages/8-1-capacitors-and-capacitance).
Finite ends/fringing and polygonal boundaries are explicit model differences,
not relaxed mesh tolerances. Panel sensitivity is not an exact error bound.

Run `python scripts/benchmark_field_capacitance.py --executable <absolute-path>
--output <new-folder>` for actual cube/scaling/plate/coax calculations. The
maintained runner passed locally in **36.82 s**, retaining each source/deck,
implementation and executable hash, raw output, measured resources and separate
strict-interchange states. Detailed logs remain external artifacts. An earlier
coax discretization timed out; directional wall refinement resolved it without
changing solver tolerance. The selected FasterCap portable build did not finish
bounded numerical probes and is not advertised as a tested alternative.

## Separate static interchange contract

The Qt-free [`ParasiticMatrix`](../src/etlayout/power/parasitic_models.py) remains
a strict supplied-data contract. Positive-frequency FastHenry results never
enter this zero-frequency contract. It distinguishes two bases explicitly:

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

## External engines and next steps

Review the exact source distribution's license before installing or distributing
an engine. ETLayout does not bundle their executables or source. The reviewed
FastHenry source is the Whiteley **031424** archive at
[`wrcad/xictools`](https://github.com/wrcad/xictools/tree/c1244c23a99118843db48cee9860218befe98753/fasthenry).
It carries the corrected 2003 MIT permission and separate Sparse notices.
Older mirrors can contain restricted notices; a project name alone does not
establish redistribution permission. Future bundling needs an exact-source,
license and packaging review. The primary
[FastHenry manual](https://www.fastfieldsolvers.com/Download/FastHenry_User_Guide.pdf)
documents finite conductors, external ports, filaments and special DC behavior.

Next work is broader verified geometry/media and circuit reduction over a
declared bandwidth. RLC fitting, causality, holdout errors, transistor
small-signal models and electrothermal circuit feedback remain separate work.

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
