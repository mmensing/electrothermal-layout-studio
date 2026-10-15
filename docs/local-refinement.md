# Guided local mesh refinement

**Mesh & Checks → Suggest local refinement** prepares suggestions in the
cancellable background worker. Review their supports, target sizes and resource
screens before choosing **Apply selected sizing** or **Run bounded study**.
Preview and Cancel leave physical settings and result identity unchanged. Apply
is one undoable document transaction. Existing manually defined regions remain.
The preview is a single temporary outline: repeat previews replace it, and
closing the review removes it while preserving ordinary annotations and camera.
Repeated reviews update the corresponding `guided-…` regions rather than
accumulating duplicate sizing controls.

Suggestions cover:

- Finite specific contacts: four elements per estimated transfer length
  `L_T = sqrt(rho_c G_s)`. `rho_c` is in Ω·m²; directional sheet conductance
  `G_s` is in S. The shortest available direction is used. Compatible solved
  local conductance is preferred; an initial-temperature constitutive estimate
  is available for bulk, sheet and semiconductor layers. A biased HEMT requires
  a compatible solved state for this estimate.
- Finite external-feed landings: the footprint area times its feed resistance
  supplies an equivalent specific resistance. Ideal landings are omitted.
- Explicit HEMT gate supports: four elements across the short rotated extent
  of the gate-controlled geometry, independently of the conducting finger width.
- Elongated conductor islands: a short-extent sizing heuristic. It is not a
  minimum-width or notch-rule certificate, and complex necks still need review.
- Compatible solved, physically averaged hotspots: four elements across the
  fixed averaging window, clipped to the exact selected support, including
  nested device groups, exclusions, holes and partially intersected elements.

Suggestions below the current minimum size explain that Apply will lower that
minimum. Geometry, contact resistance, constitutive properties and solver
tolerances are never changed to make a mesh cheaper. Via arrays retain the
existing conforming contact-size controls; this feature does not prescribe a
new physical via model.

## Bounded studies and interpretation

A local study contains two to four strictly decreasing factors beginning at
`1`, for example `1, 0.75, 0.5`. Only selected suggestion sizes change. Global
size, existing manual refinements, active geometry, loading and averaging lengths
stay fixed. Levels run serially to limit simultaneous memory demand; each solve
retains the selected CPU thread budget. Ordinary global refinement and parallel
parameter studies remain available.

The study records resistance, power, temperatures and every terminal current.
It also tracks configured physical hotspot averages and current sharing among
the largest set of like-sized device groups. Sharing is the range of signed
current per declared active width/area divided by its mean absolute loading;
parent banks and their differently sized constituent fingers are not mixed.
Source-potential spread is available only when every participating group has an
available source-potential record. Missing measurements remain failed study rows
with diagnostics, rather than fabricated zeros.

If no physical hotspot average exists, the review supplies one on the selected
physical layer: metal/sheet current for electrical analysis, temperature for
thermal/coupled analysis. Its default square side is four times the *original*
global size, shown in the review and fixed across all levels. This is an explicit
reporting choice, not a calibrated material length. Configure a maintained
measurement with an appropriate physical averaging length to replace this
default; the CLI also accepts `--averaging-length` and `--hotspot-layer`.

Element and DOF budgets are enforced by the actual mesher. The additional memory
screen estimates **additional numerical memory**, approximately 2 KiB per DOF
plus 512 B per element, excluding the Python/Qt/import baseline; overlapping
supports are counted independently. It can overestimate overlapping refinements
and underestimate sparse fill or reporting allocations. It is a planning screen,
**not a peak-memory guarantee**. Increase budgets explicitly, deselect unnecessary
supports, or refine more locally when screened out. Saved/resume thermal states
require identical meshes and cannot start a changed-mesh local study.
Dormant saved/resume preferences remain preserved and do not block DC studies
when transient operation is disabled.

Successive metric changes are empirical mesh sensitivity, not a rigorous error
bound or automatic verified badge. Inspect sharing, conservation and fixed-window
hotspots alongside resistance. A converged solve alone does not establish mesh
independence.

## CLI and portable results

Generate a review file without changing the source project or running a solve:

```powershell
python -m etlayout mesh-plan examples/E1_straight.etl --output results/plan --memory-mib 4096
```

Review `local-mesh-plan.json` and run a bounded study. `--selected` accepts the
comma-separated IDs printed in the JSON. All suggestions are selected when it is
omitted:

```powershell
python -m etlayout refine examples/E1_straight.etl --local-plan results/plan/local-mesh-plan.json --factors 1,0.75,0.5 --output results/local-study
```

`mesh-plan --apply reviewed.etl` explicitly saves a new portable project with the
suggested sizing after screening resources. Imported GDS/OASIS files remain
unchanged. Plans bind to the source configuration; a changed setup requires a
new plan. Study exports retain per-level ETL archives, numeric SI measurements,
configuration identity, planning estimates and the reviewed plan. Detailed logs
and solved fields belong in user output directories or CI artifacts.

Focused regression coverage includes analytic transfer lengths, exact nested
hotspot support, stale/invalid plan rejection, all three resource screens,
monotonic bounded levels, read-only preview/Cancel, Apply/Undo, and a real two-mesh
uniform-metal calculation with invariant resistance, current density and power
conservation. A separate real coupled calculation reproduces a uniform
`R_on,sp/A = 50 Ω` load, two equal `1 mA` cell groups, `0.2 mW` terminal power,
and a `300.105 K` supported-area thermal hotspot on both local mesh levels.
Its intentionally equipotential electrodes define an analytical limit; it is
not a realistic power-device routing example.
