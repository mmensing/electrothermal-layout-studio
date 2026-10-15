# Visualization workbench

Display settings do not alter the physical model or require another solve.

## Display and export contracts

| Previous finding or requested addition | Implemented behavior | Regression coverage |
| --- | --- | --- |
| P1: colliding layer filenames | Allocate all export filenames together, handling sanitized IDs, truncation, Windows reserved names, case differences and collisions between artifact types. `manifest.json` maps original IDs to their artifacts. | `test_visualization_exports.py` |
| P2: inconsistent export support and unused coordinates | One support resolver governs valid triangles, regions, bounds and data ranges. Per-layer VTU nodes are subset and connectivity is remapped. Inactive coordinates and nodal values cannot enlarge plotted bounds or scales. | `test_visualization_support.py`, `test_visualization_exports.py` |
| P2: missing temperature-rise export | Viewer, sampler and standalone plots share derived-field resolution and thermal-reference semantics. | `test_visualization_exports.py`, `test_visualization_workbench.py` |
| P2: implicit difference mode and mixed report scope | Explicit Original / Current − comparison mode survives presentation changes. Source identities, convergence and operation accompany derived outputs. Original metrics are labeled in the UI and omitted from derived reports. Separate original and displayed-selection numerical exports. | `test_visualization_workbench.py`, `test_visualization_exports.py` |
| P3: missing quantitative color bars in exports | PNG/SVG maps and comparisons include ticks, quantity, units, palette, scale transform and manual-clipping disclosure. JSON sidecars record selection and provenance. | `test_visualization_exports.py`, `test_comparison_workbench.py` |
| P3: lost run-inspection presentation state | Return restores layer, region, field, range, palette, log/smooth/mesh settings, difference context and viewport; inspected runs retain their own choices within the session. | `test_visualization_workbench.py` |
| Synchronized layer panels | Compare two layers from one run, or two saved runs, with shared quantity/range and linked pan/zoom. Each panel retains its own layer and region. Physical extents and linking are explicit controls. | `test_comparison_workbench.py`, native acceptance |
| Region filtering | Named mesh footprints and edge-connected islands share one mask across maps, mesh, vectors, probes, profiles, statistics and displayed-field exports. Unknown regions fail visibly; no silent full-layer fallback. | `test_visualization_support.py`, `test_visualization_workbench.py` |
| Active mesh and smooth nodal fields | Mesh follows selected support. Optional P1 scalar interpolation for nodal fields maps the interpolated value through the chosen palette; element fields stay flat. Profiles leave gaps outside valid regions. | `test_result_layer_renderer.py`, `test_visualization_exports.py` |
| Responsiveness and accessibility | Bounded field-picture cache, renderer timing harness, explicit normalized-vector labeling, accessible names, label shortcuts and native Qt scale checks. | `test_result_layer_renderer.py`, acceptance and performance receipts below |
| Colormaps and manual limits | Teal, Viridis, Magma, Cividis, Coolwarm and Grayscale. Editable Min/Max accept field units and engineering prefixes. Auto resets the scale; invalid limits clear the map and disable export until corrected. | `test_field_scale_workbench.py`, `test_comparison_workbench.py` |

## Scientific display rules

- Regions are intersections with the selected physical layer. Components join through
  shared triangle edges; touching at one node does not join two islands. Component IDs
  remain tied to the original run when a difference removes unmatched islands.
- Nodal triangles require three finite vertices. Log maps additionally require every
  vertex to be positive; all consumers use the same eligibility rule.
- Displayed statistics use supported values and area-weighted element means. Manual
  color clipping does not clip numerical exports or change these statistics.
- Differences are current minus comparison. Vector/tensor quantities compare scalar
  magnitudes. Each source uses its own temperature-rise reference. Only complete
  current-mesh triangles covered by both valid domains remain; this conservative
  operation can leave an empty boundary band on independently meshed domains.
- Automatic Coolwarm difference scales are symmetric about zero. An exactly zero
  difference uses a machine-epsilon display span around zero, while actual statistics
  remain zero. Ordinary constant fields use the palette midpoint.
- GUI smooth fields use scalar gradients with 64 color stops; standalone plots use
  256 scalar contour intervals. Both interpolate the scalar before applying colors
  and log transforms. Their rasterization is not claimed to be pixel-identical.
- Normal filtered exports identify `simulation.etl` as the full source archive.
  Derived exports instead provide `difference-fields.npz`, selected CSV/VTU data and
  paired-source metadata; they do not pretend to be another solved simulation.

## Acceptance and reproducibility

The [acceptance harness](../scripts/visualization_workbench_acceptance.py) loads or
solves real E12 via-ladder and E10 thermal-stack examples, solves a second E12 at
0.2 A, then operates live application widgets. It captures nine states: palette
selection, invalid and valid manual ranges, logarithmic mapping, interface-region
profiling, linked layer panels, differences, smooth temperature rise and dark themes.
Mouse clicks, wheel events and label shortcuts use QtTest; combo selections emit
the application's normal signals. No screenshot or solver field is synthesized.

```powershell
python scripts/visualization_workbench_acceptance.py --offscreen
python scripts/visualization_workbench_acceptance.py --scale 1
python scripts/visualization_workbench_acceptance.py --scale 1.5
python scripts/benchmark_field_renderer.py --cells 100
```

PR CI runs fast source/package contracts and lint; main and owner-requested
Windows validation add the complete numerical/desktop suite, real CLI workflows
and offscreen workbench acceptance. Native receipts record
actual widget sizes, device pixel ratios, keyboard focus and platform name. Scaling
uses `QT_SCALE_FACTOR`, not a physical monitor reconfiguration. The available display
can constrain requested wide windows at 150%; the receipt records actual dimensions.
There is no claim of screen-reader, multiple-monitor, native window-chrome or rebuilt
installed-executable validation.

The performance harness uses a declared synthetic analytical fixture solely to measure
rendering, not as simulation or acceptance evidence. A 100×100 cell grid has 20,000
triangles. The cache retains at most eight pictures and 64 MiB; smooth fields cost more
to record on a cold selection. This is a local timing sample, not a hardware-independent
latency guarantee. See the [renderer benchmark](../scripts/benchmark_field_renderer.py).

The recorded 20,000-triangle sample took 193 ms for a cold flat field and 1.03 s for
a cold smooth field; cached requests took 5–6 ms. Vector overlays took 195 ms cold
and 15 ms on cached requests. The three cached pictures occupied 30.5 MiB.


See [desktop workflow](desktop-workflow.md) and [validation](validation.md). Runtime measurements and repeated captures are CI/release artifacts.
