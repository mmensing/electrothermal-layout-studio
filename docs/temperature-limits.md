# Maximum operating temperature

Use **Operating conditions → Maximum operating temperature…** (also available
under Simulation / Settings). Enable a global limit, enter an absolute maximum
in °C or K, and choose whether to finish the calculation or stop at the first
violating accepted transient state. The dialog converts °C to kelvin on save.
150 °C is an editable example, not a certified rating for any material or device.

**Layer / selected-region limits…** adds named, enabled limits with layer IDs and
optional exact area geometry. Scoped entries display SI kelvin. Empty layer IDs
cover all active thermal sheets; no region means their complete physical support.
Use the region editor for mask Boolean operations or explicit polygon areas.
Zero-area lines and empty regions are rejected. A region outside active thermal
support reports unavailable compliance and fails engineering acceptance.

A temperature **at or above** its configured maximum fails. The maximum comes
from authoritative component-local P1 temperature fields. For partial elements,
the test evaluates vertices of the exact clipped support, including interpolated
vertices on its boundary. It does not use centroids, a smoothed display field,
color limits, percentiles or an arbitrary averaging length.

## Reading a failed run

**Failed — Maximum temperature exceeded** reports the maximum, limit, excess,
layer, physical position and, for transients, accepted sample time. Temperature
rise uses the solver's selected reservoir/reference; it is unavailable when no
unambiguous reference exists. Converged fields remain inspectable and exportable.
The issue drawer provides the violated layer and location.

The following are independent conclusions:

- Numerical convergence and existing electrical/thermal conservation checks.
- Hard temperature compliance (`thermal_limit_compliance`).
- Mesh and time verification, which the limit does not establish.
- Material validity and calibration, which remain separately diagnosed.

A raw maximum can be mesh sensitive. A passing limit is not proof of a physical
rating or of a mesh-independent hotspot; retain the existing refinement and model
validity checks. Nonconverged fields are diagnostic, even if their current
finite temperature is below the hard limit.

## Transient behavior

Limits are checked on the initial physical state and **every accepted integration
step**, independently of the field-output interval. Nonlinear trial iterates and
rejected steps cannot trigger a temperature stop. A completed pulse that later
cools remains failed if any accepted intermediate state violated a limit.

Stop mode retains the violating accepted state, all recorded accepted scalar
rows, scheduled prior snapshots and the retained global peak within the existing
storage budget. `transient_stop_reason` is `maximum_temperature`; requested and
actual stop times remain separate. The first violation is the **first observed
accepted sample**, not an exactly localized threshold crossing. Time-step
refinement remains necessary where crossing time matters.

On an intentional early thermal stop, `converged` remains true when all retained
PDE states passed numerical checks, while `transient.completed` is false because
the requested integration interval was not completed. `engineering_accepted` is
false. Both portable archive formats preserve and validate this distinction.
Cancellation and numerical-step failure retain their existing behavior.

## CLI and studies

Save the limit in the project through the GUI, then use the normal CLI:

```console
etlayout solve limited-project.etl --output results --no-plots
```

Exit codes are **0** for numerically converged accepted runs, **2** for setup or
numerical failure, and **3** for a converged run with failed/unavailable hard
thermal compliance. Code 3 still writes the report, fields and `simulation.etl`.
A fixed-temperature electrical-only calculation has `not_applicable` thermal
compliance; its assigned material temperature is not a computed thermal solution.

Sweep rows keep rejected results and an explicit engineering error. Routing
optimization records `thermal_limit_failure`, saves each scenario archive, and
excludes such candidates from feasible rankings and mesh-verified finalists.
Other service, conservation, physical-model and mesh criteria remain unchanged.

## Persistence and existing constraints

The typed `Project.temperature_limits` list is the hard-temperature mechanism:

```json
{
  "id": "maximum_temperature",
  "enabled": true,
  "maximum_temperature": 423.15,
  "layer_ids": [],
  "geometry": null,
  "evaluation": "stop_transient"
}
```

Kelvin is always stored. Existing projects default to an empty list and retain
both numerical behavior and legacy physical identity. Disabled-only lists do not
change physical identity. Active limits do participate in identity because an
early stop can change the accepted history. New simulation presets include the
limits; loading an older numerical preset preserves the project's existing limits.
Older application versions that do not recognize this field reject it rather
than silently ignoring its safety meaning.

Existing `Project.thresholds` remain **warning-only annotations**, including
`temperature_max` and `transient_peak_temperature`. They are not silently
promoted to hard limits. Existing `MeasurementSpec.limit` constraints retain their
explicit quantity, support and aggregation; a named averaged-temperature study
constraint is distinct from a hard unsmoothed maximum. No legacy Celsius/Kelvin
reinterpretation is performed. A selected historical frame retains the run-wide
thermal failure, including when that frame has cooled below the limit.

## Verification

Regression tests include independent uniform heat-transfer and backward-Euler RC
solutions, exact partial-element P1 support, layer scopes, equality failures,
intermediate pulse heating/cooling, intentional early stops, both archive formats,
CLI code 3, equal-service optimization rejection and native Qt controls. They do
not establish process calibration, full 3D thermal accuracy or a universal rating.
