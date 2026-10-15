# Layout, connectivity and mesh API

All public geometry coordinates are in **meters**. Layer z and thickness are
also SI. GDS database units are preserved separately in `LayoutSource.dbu_um`.
The viewer is responsible for displaying micrometers.

## Import and hierarchy

```python
from etlayout.geometry import LayoutDocument

layout = LayoutDocument("chip.oas")
print(layout.cells, layout.top_cells, layout.dbu_um)
print(layout.layers)  # key, layer, datatype, name, index
page = layout.instances_page("TOP", offset=0, limit=200)
print(page["total"], page["has_more"])
placement = page["items"][0]
children = layout.instances_page("TOP", instance_path=placement["path"])
masks = layout.extract("TOP", instance_path=placement["path"])
```

`cells` and `top_cells` are lists of cell names. `layers` contains dictionaries
whose stable mask key is `"layer/datatype"`, for example `"1/0"`. `bounds` and
`cell_bounds(cell, instance_path)` return `[xmin, ymin, xmax, ymax]` in meters.
An empty cell name selects the first alphabetical top cell. The UI should make
the selection explicit when multiple top cells exist.

Each instance path is a list of placement indices, one per hierarchy depth.
Every placement in an array is separately selectable. Enumeration follows
KLayout instance order, then array transformation order; paths remain stable
while the source layout is unchanged. `instances_page(cell, instance_path,
offset=0, limit=200)` returns only the selected parent's immediate children,
with `items`, exact placement `total`, `offset`, `limit` and `has_more`. Returned
paths remain relative to the original root cell. Array offsets are addressed
directly; selecting the last of a million array placements does not enumerate
the preceding placements. Instance extraction retains the original
root-cell coordinate frame and composes rotations, reflections, magnification,
and translations. The legacy `instances(..., max_instances=10000)` method is
bounded and emits a warning if truncated; use pages for complete browsing.

Layout metadata stays hierarchical. Geometry is expanded only for the selected
cell/instance. Passing a Shapely `roi` to `extract` prunes recursive traversal
using its bounding box and then applies the exact polygon intersection.
`clip=False` retains full candidate polygons crossing that box, allowing cut-edge
diagnostics without loading distant placements. `mask_keys` optionally selects
only required masks. An eight-query LRU reuses immutable Shapely geometries;
returned dictionaries are independent. `geometry_bounds(cell, instance_path)`
uses KLayout's hierarchical geometry representation and excludes text without
expanding every placement. Extraction and preparation accept progress/cancel
callbacks. Native reading/merging calls are interrupted by terminating the GUI
worker; Python conversion also checks cancellation between batches.

KLayout converts paths with their widths and cap extensions into polygons.
Polygons are merged within a mask, including overlapping and edge-touching
shapes. Holes survive import and triangulation. Text does not contribute area.

## Region expressions and validation

`resolve_region(spec, masks, domain)` combines source masks left-to-right using
`union`, `intersection`, `difference` or `xor`, then unions explicit polygons.
The polygon form is `{"exterior": [[x,y], ...], "holes": [[[x,y], ...], ...]}`.
`shell` and `points` are accepted aliases for `exterior`; a `bounds` four-tuple
is also supported. `full_domain=True` selects the simulation domain, including
regions without a layout mask. An empty region selects nothing.

`prepare_geometry(project, progress=None, cancel=None, *, document=None,
masks_out=None)` returns a
`PreparedGeometry` with resolved layers, interfaces, terminals, thermal
boundaries, heat sources, local refinements and actionable diagnostics.
Pass a parsed `LayoutDocument` to reuse its metadata and extraction cache.
An optional `masks_out` dictionary receives domain-clipped raw masks for display,
avoiding a second viewport extraction. The default physical domain includes the
imported polygon/path extent **and explicit physical-layer polygons**. An
explicit ROI may enlarge that domain, so a thermal substrate can surround a
smaller imported metal footprint. Full-domain layers use this physical domain.
An intentionally smaller ROI clips layers and reports discarded area when
present among extracted candidates. Simulation ROI and display ROI are separate;
only `project.roi` changes physics.
Clipping a conductor requires `acknowledge_clipped_boundaries=True`: cut edges
are natural no-flux boundaries unless explicitly assigned a terminal. This
acknowledgment records the user's acceptance of that physical assumption.
Selecting a complete island while excluding a disconnected island does not
count as cutting a current path; only boundaries through conductor interiors do.

Invalid polygons, zero-area layers, unknown masks, invalid stack dimensions,
invalid contacts and terminals on nonconducting layers fail before meshing.
Warnings identify disconnected islands, very narrow feature clearances,
overlapping effective stack volumes, region clipping and implausible unit scales.
Literal `physical_via`/`physical_contact` interfaces enforce stack height,
endpoint/enclosure, volume and replacement checks as described in
[physical stack validation](POWER_ROUTING.md#thermal-reference-and-physical-stack).
They fail illegal geometry rather than silently clipping it. Legacy effective
links retain their labeled assumptions and warnings.
Every diagnostic can include a geometry bounding box for viewer navigation.
Drawn distributed-current-density and thermal boundary lines must lie along a
physical sheet boundary, including a hole edge. Equipotential electrical lines
may also be embedded inside a conductor, for example Kelvin sense probes; these
explicitly impose a zero-width equipotential constraint. Area polygons represent
physical contacts with finite area.

## Connectivity

`analyze_connectivity(project, prepared)` returns a `ConnectivityReport`:

- `components`: component id to `Component` with layer, Shapely geometry and net.
- `nets`: net id to component id list.
- `terminal_nets` and `terminal_components`: selections attached to each terminal.
- `interfaces`: actual component pairs joined by each electrical contact.
- `diagnostics`: opens, point contacts, absent references, current imbalance,
  conflicting terminals, and projected overlaps with no declared contact.
- `trace(source_id, drain_id)`: shortest component path, or an empty list for an open.
- `to_dict()`: JSON-compatible report with geometry bounds and areas.

Edge contact and overlap connect within one physical layer. Point-only contacts
remain disconnected and are reported. Different physical layers stay electrically
independent until an enabled finite-area electrical interface joins them.
Spatial indexes restrict point-contact, terminal and interlayer predicates to
intersecting candidates, avoiding all-pairs scans of distant islands. Conflicting
terminal potentials are checked on shared electrical components, not merely
shared x–y corners. Distributed sources are integrated separately on each net.
An equipotential terminal intentionally ties all finite-area/edge components it
spans. Add explicit interface rules to connect layers; disable a rule to override
that connection. Splitting same-layer geometry requires a Boolean difference.

## Conforming mesh

`generate_mesh(project, prepared, progress=None, cancel=None)` creates a common
first-order triangular mesh. Every resolved physical, contact, boundary, heat
source and refinement outline participates in a noded planar arrangement. The
Gmsh planar kernel receives shared line segments, preserving holes, partial edge
terminal endpoints and material boundaries. Centroid classification is exact
for this conforming partition; no triangle is deliberately allowed to straddle
an assigned region boundary.

Gmsh coordinates are translated to a local origin and scaled to micrometers to
avoid geometric-tolerance problems at semiconductor dimensions. Returned points
are in meters. Gmsh access is serialized because its API is process-global;
workers initialize it with signal handling disabled. Cancellation is checked
between geometry and mesh stages; the synchronous Gmsh mesh call itself cannot
be interrupted midway.

`PlanarMesh.regions` maps `layer:`, `interface:`, `terminal:`, `thermal:`, `heat:`
and `refinement:` keys to global triangle indices. Line boundary regions have
empty area-element arrays. Their selected vertices are in `terminal_nodes[id]`
or `boundary_nodes[id]`; `boundary_edges["terminal:" + id]` and
`boundary_edges["thermal:" + id]` store exact global vertex pairs so disjoint
segments and point-touching components cannot create a spurious boundary edge.
Supporting triangles are additionally tagged as
`terminal_support:id` and `thermal_support:id`. Layer perimeter nodes are in
`boundary_nodes["layer:" + id]`. Shared coordinates carry no physical connection:
the solver creates independent unknowns for layers and edge-connected components.

Global minimum/maximum sizes, per-layer sizes, exact polygon refinement regions,
contact-distance fields and corner point refinement control mesh density. Small
geometric features are retained even below a target minimum size; quality
statistics and warnings expose slender elements. Refinement is geometric;
automatic solution-error estimation is not implemented. A mesh refinement study
can repeatedly reduce size controls and compare engineering metrics.

Element/DOF budgets reject obviously infeasible requests before meshing and
reject oversized generated meshes before solve. Statistics include node/element
counts, partitions, estimated combined electrical/thermal DOFs, area, minimum
angle, normalized triangle quality, generation time and backend version.

Validation covers GDS and OASIS arrays and transforms, path polygonization,
holes/text exclusion, nested selection, ROI acknowledgement, mask Booleans,
finite-area via connectivity, point-only isolation and exact mesh region areas.

Regression tests also cover expanded thermal domains, late hierarchy pages,
whole-island ROI selection, per-net density integration, prescribed-source thermal
validation, cancellation and spatial-query operation counts. Structural tests
prevent full-array conversion and all-pairs predicates. Use the [validation guide](validation.md)
for reproducible checks; wall-clock receipts belong in CI/release artifacts.
