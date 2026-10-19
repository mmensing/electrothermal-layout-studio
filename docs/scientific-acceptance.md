# Scientific measurement and export acceptance

All stored coordinates and thresholds use SI units. A result retains its original
configuration, component-local fields and solver/conservation diagnostics.
Reporting changes create a new report; they do not rewrite the original fields.

## Exact geometric support

Support selectors union entries within a category and intersect populated
categories. Layer/material/net filters, named mesh regions, contacts, ports and
device groups all restrict support. A device group may reference another group;
cyclic and stale references are errors. Exact geometric clips, holes and
disconnected polygons survive every nested reference. Partially intersected
triangles are geometrically cut and integrated using their P1 fields, rather than
included or excluded by their centroids.

For a linear potential V=x/L on a 10 µm by 2 µm strip, clipping the first 2.5 µm
has area 5e-12 m² and mean 0.125 V. Direct and nested group selections must agree
on both quantities under mesh subdivision. Independent regression tests also
check unions, intersections, holes, RMS, quantiles and contact heat integrals.

## Exceedance contracts and migration

`aggregation="exceedance"` reports a dimensionless fraction of supported area
whose sample value is strictly greater than `threshold`. `threshold_units`
must match the quantity's canonical units; empty selects canonical SI units.
Result `units` must be empty or `fraction`. `max_exceedance_fraction` is an
independent allowed fraction in [0, 1]. Empty means report only and cannot serve
as an optimization acceptance constraint.

For example, potential threshold 0.1 V over the linear strip produces fraction
0.9. An allowed fraction 0.8 fails. A threshold below every sample produces
fraction 1 and fails every smaller allowed fraction.

Older exceedance specifications overloaded `limit` as a dimensional sample
threshold and had no fraction bound. They remain loadable for inspection but
validation/reporting rejects their ambiguous acceptance contract. To migrate:

1. Move the old sample value to `threshold` and declare its `threshold_units`.
2. Clear `limit` and `lower_limit` for this exceedance measurement.
3. Explicitly set `max_exceedance_fraction` if used as a constraint.
4. Reanalyze compatible retained fields and review optimization settings.

No fraction is inferred from an old value. Non-exceedance measurements retain
their existing upper `limit` and `lower_limit` meanings. Fields, solver equations,
conservation tolerances and equal-service checks are unaffected.

## Passive multiport resistor export

Extraction describes fixed-temperature passive resistive DC ports, excluding
nonlinear HEMT/device laws. JSON and CSV preserve the full ungrounded operator.
SPICE retains every finite positive pairwise conductance, including weak links;
the global matrix-validation tolerance is never a sparsification threshold.

Before writing, the exporter reconstructs the actual serialized resistor graph
and checks each port's basis-voltage response and a stable grounded resistance
operator. Positive off-diagonal terms, missing connectivity, nonfinite values or
unestablished fidelity reject export explicitly. Refinement or the full operator
export is preferable to a circuit that silently isolates a port. Independent
tests reconstruct the network and compare the analytical star response at large
conductance ratios.

## Fabricated via rules

Via minimum width uses integer-DBU polygon edge checks, including rotated and
notched polygons. A bounding box does not establish a minimum physical width.
Unsupported or vanished shapes reject the candidate.

Pitch is the distance between polygon area centroids. Optional `via_spacing`
is the minimum edge-to-edge gap. Both apply across all enabled physical-via
objects on the same unordered physical-layer transition. Splitting an array
across interface objects or reversing its layer order does not evade checks.
Different fabrication transitions are independent. Enclosure is checked on both
landing sheets. Circular bend-radius checking remains explicitly unsupported.

## Result and optimization status

Setup readiness, solver convergence, engineering constraints and mesh
verification are separate. A feasible coarse candidate is provisional. Existing
equal-service checks require delivered current, compliance and applicable mesh
resolution before an improvement can be certified. Never relax physical criteria
or relabel unverified results to make a visual status pass.

See [power analysis and routing](POWER_ROUTING.md), [numerics](numerics.md) and
[validation](validation.md) for the models and reproducible test commands.

## Hard maximum temperature

[Maximum operating temperature](temperature-limits.md) defines the explicit
project hard-limit contract, accepted transient states, CLI code 3 and the
separation of numerical convergence from engineering acceptance. Warning
thresholds and named measurement constraints retain their existing meaning.
