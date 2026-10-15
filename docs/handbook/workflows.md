# Calculation workflows

[Handbook](README.md) · [← Setup](setup-and-boundaries.md) · [Next: worked examples →](worked-examples.md)

Choose the mode from the physical question, then validate the corresponding
inputs. A saved electrical result does not automatically become the heat source
for a later thermal calculation.

The table below describes **steady** operation. For startup, cooldown or a drive
waveform, select Transient operation and follow the [pulsed workflow](transient.md).
Transient thermal mode with Joule heating recomputes the isothermal electrical
source for each changing drive; the steady one-way calculation computes it once.

| Question | Mode/settings | Heating used | Feedback |
| --- | --- | --- | --- |
| What is the resistance/current distribution at a specified temperature? | `electrical` | Electrical loss is calculated as an output | Temperature held at `initial_temperature` |
| What temperature follows a prescribed power map? | `thermal`, Joule option off | Declared heat sources | Thermal conductivity may depend on temperature |
| What temperature follows the cold electrical loss? | `thermal`, `thermal_include_joule=True` | Fresh electrical loss at initial temperature, plus declared heat sources | Electrical source held fixed |
| What operating point includes temperature-dependent electrical loss? | `electrothermal` | Recomputed electrical/contact loss plus declared heat sources | Electrical and thermal equations coupled |

The desktop follows seven stages: **Layout & Region → Layers & Materials →
Contacts & Connections → Operating Conditions → Mesh & Checks → Run → Results**.
The header's Analysis selector offers the supported combinations of physics and
time operation; changing it preserves inactive settings and updates prerequisites.
Electrical analysis is DC only. Thermal and electrothermal analyses offer steady
and transient choices. The [guided desktop workflow](../desktop-workflow.md) is
the current control reference. **Edit → Execution settings…** controls a
machine-local CPU budget (four by default, bounded by availability) and parallel
study workers. These preferences change execution, not the physical experiment.

## Electrical: establish the current path first

1. Confirm conducting sheets, contacts and terminal selections. Inspect the
   source-to-reference connectivity path.
2. Set the temperature at which transport is evaluated. Use 300 K for the
   [E1 analytical comparison](first-calculation.md).
3. Validate, mesh and solve in electrical mode.
4. Inspect terminal currents and electrical power balance before reading a
   local maximum. Potential should be defined on the intended network.
5. Use current-density maps, line profiles and a mesh study to examine bends,
   narrow necks and contacts.

A floating sense terminal draws zero net current but imposes equipotentiality
over its footprint. For Kelvin resistance, explicitly choose the voltage-sense
pair and the current-reference terminal in solver settings. A multi-source
network does not have a unique resistance without a measurement definition.

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E1_straight.etl --mode electrical --output results/e1-dc
```

## Thermal: distinguish prescribed and electrical power

For an independent thermal experiment, add heat sources and thermal boundaries,
then select thermal mode. A fully unloaded structure with every reservoir at
300 K should remain at 300 K. This is the expected result for E10 in plain
thermal mode, because E10 has no independent heat source.

To heat E10 using a fresh isothermal electrical calculation:

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E10_thermal_stack.etl --mode thermal --set solver.thermal_include_joule=true --output results/e10-one-way
```

In the desktop, enable **Include electrical heating** for this workflow. Both lateral and
contact losses are included, together with any independently prescribed heat.
The source is evaluated at the configured initial temperature; it does not
update when the sheet warms. Compare with coupled operation to assess the
importance of that approximation for your inputs.

For low-level Python work, `electrical_heat(dc_result)` creates the heat-density
dictionary for `solve_thermal`. Use **the same mesh** for both. Reusing element
indices after remeshing can assign heat to the wrong place; a different mesh
requires an explicit conservative transfer.

## Coupled: include feedback

In electrothermal mode, the solver updates resistivity, semiconductor mobility
and charge control, via-material resistivity and thermal conductivity as
temperature changes. Scalar specific/lumped electrical contact parameters stay
temperature-independent. Prescribed sources remain additive.

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E10_thermal_stack.etl --mode electrothermal --output results/e10-coupled
```

Review the iteration history, residuals and balances. A result is converged only
when the configured change, residual and conservation checks pass. Finite fields
from a failed iteration are retained for diagnosis; they are not a solved
operating point. If coupling fails, first check material validity and boundary
assumptions, then consider smaller load increments, increased iteration budget
or adjusted damping. Raising tolerances is not a substitute for understanding a
strongly nonlinear or physically inappropriate setup.

## Compare alternatives with controlled changes

A parameter sweep clones the project for each point and records both requested
and applied values. Dotted paths select IDs, not their positions in a list. For
example, `terminals.drain.value` refers to the terminal whose ID is `drain`.

```powershell
.\.venv\Scripts\python.exe -m etlayout sweep examples/E12_via_ladder.etl --parameter terminals.drain.value --values '[0.1,0.3,0.5]' --mode electrothermal --output results/e12-current
```

Each example may contain several saved sweep definitions. The CLI uses the first
saved sweep when no explicit parameter is supplied; the desktop offers a study
workflow for choosing parameters. Read the units and role of the selected
parameter before reusing values across projects.

Use design comparison when geometry or stack topology changes, and a mesh study
when only discretization changes. Compare the same quantity with a shared color
range. If operating points, source definitions or thermal references differ,
state those differences alongside the field comparison.
Independent study points can run concurrently within the shared CPU budget.
Results retain input order; failed rows keep their status and error and leave
gaps in plotted successful curves. Mesh and time-step studies disable periodic
early stopping so transient rows cover the same requested duration. A saved-state
mesh study cannot change its source mesh; choose uniform or steady initialization
to refine it. See [CPU execution](automation-and-troubleshooting.md#cpu-execution-and-python-studies)
for settings and guarded Python examples.

## Preserve a reproducible result

Save the `.etl` project with its result. It retains the layout, material
snapshots, configuration and numerical arrays. Export numerical data for further
analysis and maps for communication. Read the configuration recorded with the
run, especially when inspecting history after editing the current project.

Presentation changes preserve compatible fields. Physical edits invalidate the
current result and require a new solve. The [results chapter](results-and-validation.md)
explains filtered exports, difference provenance and validation checks.

[← Geometry, materials and boundaries](setup-and-boundaries.md) · [Worked examples →](worked-examples.md)
