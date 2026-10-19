# Transient and pulsed operation

[Handbook](README.md) · [Calculation workflows](workflows.md) · [Results and validation](results-and-validation.md)

Transient operation adds heat storage to each physical sheet so that a pulse can
warm the structure and an off interval can let it cool. Electrical states remain
quasi-static: at a requested drive and temperature, the existing resistive model
is solved without electrical capacitance, inductance or wave propagation.

This approximation answers thermal questions such as pulse heating, cooldown
and cycle-to-cycle accumulation when electrical settling can be treated as
instantaneous on the time scale of interest. It does not predict switching
overshoot, gate charging, RF loss, trapping dynamics or breakdown. The distinction
between stationary electrical states and a time-dependent thermal solution is
also described in [COMSOL's electromagnetic-heating formulation guide](https://www.comsol.com/support/learning-center/article/setting-up-and-solving-electromagnetic-heating-problems-46881).

## What is solved

For sheet thickness $d_i$, mass density $\rho_{m,i}$ and specific heat $c_{p,i}$,
the area heat capacity is $C_{A,i}=\rho_{m,i}c_{p,i}d_i$ in J/(m² K). The equation
adds storage to the steady thermal balance:

$$
C_{A,i}\frac{\partial T_i}{\partial t}
-\nabla_{xy}\cdot(d_i\mathbf k_i(T_i)\nabla_{xy}T_i)
+\sum_j g^{\mathrm{th}}_{ij}(T_i-T_j)=q''_i(t).
$$

The density is mass density in kg/m³, not electrical resistivity; specific heat
is in J/(kg K). Both are positive constant material inputs in this implementation.
Thermal conductivity and electrical transport may still depend on temperature.
Thermal storage is assigned to physical sheets. An interface is a transfer
resistance/conductance and contributes no separate heat capacity. A via's
electrical height does not create a resolved thermal volume.

This thickness integration is the sheet approximation; through-thickness
transient diffusion inside a layer is not resolved. For background on the
approximation, see [COMSOL's thin-layer heat-transfer theory](https://doc.comsol.com/6.3/doc/com.comsol.help.heat/heat_ug_theory.07.031.html).

The default solver uses a consistent P1 capacity matrix and backward Euler:

$$
\mathbf M\frac{\mathbf T^{n+1}-\mathbf T^n}{\Delta t}
+\mathbf K(\mathbf T^{n+1})\mathbf T^{n+1}=\mathbf f^{n+1}.
$$

For a triangle of area $A$, the local capacity matrix is
$C_A A/12$ times the matrix with diagonal entries 2 and off-diagonal entries 1.
`transient.capacity_scheme="lumped"` instead uses the row sums on the diagonal,
preserving total heat capacity. This can reduce nonphysical local undershoots
from consistent capacity on a coarse mesh, but is not an unconditional
monotonicity guarantee: conductivity, mesh geometry and interlayer coupling
also matter. Bound diagnostics report eligible passive heating/cooling cases;
conservation alone does not establish pointwise physical accuracy. Compare both
spatial and temporal refinement when a temperature bound warning appears.
This is a first-order time method: a converged step can still have time-discretization
error. Halve the maximum time step and compare the quantities you need. The
[FEniCS heat-equation tutorial](https://hplgit.github.io/fenics-tutorial/pub/html/._ftut1008.html)
derives the backward-Euler weak form. The application implementation is in
[`solvers/transient.py`](../../src/etlayout/solvers/transient.py) and
[`solvers/thermal.py`](../../src/etlayout/solvers/thermal.py).

## Choose how electrical power is evaluated

| Transient calculation | Source behavior |
| --- | --- |
| Thermal, Joule option off | Time-dependent prescribed heat sources; no electrical solve |
| Thermal, Joule option on | A fresh electrical state at the initial temperature for each step's drive; loss follows the waveform but has no temperature feedback |
| Electrothermal | Electrical/contact loss and temperature are iterated self-consistently at each step |

Pure electrical mode does not support transient operation because it has no
storage equation. A terminal waveform requires electrothermal mode or thermal
mode with Joule heating enabled. Independently prescribed heat remains additive
in every mode that includes it.

## Initial condition and boundaries

The default initial condition is the uniform `solver.initial_temperature`, which
must agree with every fixed-temperature boundary. Select `transient.initialization="steady"`
to first solve the steady thermal/electrothermal state at the initial waveform
drive. This bias solution needs the heat-removal paths required by a steady solve.
A coefficient boundary may have a different ambient temperature, enabling an
initially warm sheet to cool.

Alternatively, supply a compatible saved result as `initial_result` to `run_project`.
With `resume=False` its temperature field starts a new history at t=0. With
`resume=True` the saved transient continues at its accepted absolute time and
retains cumulative energy accounting. The requested stop time is the absolute
end, not an additional duration. Geometry, mesh and thermal properties must be
compatible; arbitrary remapping of external fields is not provided. The new
history describes the continuation segment and records its source provenance.
Older saved snapshots without cumulative-energy baseline metadata can be used
for a new warm start, but cannot be resumed with trustworthy energy accounting.
Saved warm-start and continuation projects retain their initial-state identity.
Running such an archive again replays its original first snapshot; it does not
silently revert to a uniform start or extend the completed run. Use an explicit
`--resume RESULT.etl` and a later absolute stop time to continue its final state.
Peak metrics cover the current segment, while resumed energy totals include the
earlier origin; both scopes are recorded in the result metadata.
Compatible saved-state runs reuse their archived mesh, preserving the exact
field support when CPU settings change. Parameter sweeps and design comparisons
accept the same saved-state inputs. Mesh refinement requires uniform or steady
initialization when it changes the mesh; saved-field interpolation is not supplied.

Unlike a steady solve, a transient problem can be fully adiabatic: generated
heat then accumulates as stored energy. A finite stop time with converged steps
does not imply a steady or periodic equilibrium. Optional periodic detection
compares all thermal degrees of freedom at a common cycle phase. It supports
equal-period pulse drives and constants, with a nonnegative tolerance in K,
minimum cycle count and required consecutive matching cycles. A zero tolerance
disables it. Piecewise-linear drives and mixed pulse periods are rejected when
detection is requested. Early convergence records the actual end time and stop
reason; it does not replace a time-step convergence study or prove agreement
throughout the entire cycle.

## Desktop workflow

1. Open a ready-to-run pulse example, or configure a steady project with the
   required thermal sheets and boundaries first.
2. In the header's **Analysis** selector, choose **Thermal transient** or
   **Electrothermal transient**, as appropriate for the physical question.
3. Open **Simulation → Time and load settings…**, also reachable from Operating
   Conditions. **Timing** groups stop time, maximum step, output
   interval and initialization; **Waveforms** groups readable targets, unit-aware
   pulse/constant inputs and a piecewise-linear time/value table. **Advanced**
   holds storage budgets, peak retention, **Thermal capacity** (consistent or
   lumped) and periodic convergence.
4. Validate, mesh and Run. Review completion, diagnostics and the actual accepted
   end time before interpreting fields.
5. Use the saved-time slider or direct time entry below the map to select a snapshot.
   **Peak** jumps to the retained whole-run temperature peak. The layer,
   field, region, colormap and manual range work at that time. Keep a common
   manual temperature range when comparing early and late frames.
6. Open the canvas's **Time history** tab for whole-device temperature, power
   and energy histories at every accepted step. Select the layer/region or map-probe
   scope for local temperature traces derived from retained fields. Sparse output
   can miss a local peak. Click a trace to select its nearest retained map frame.
7. Use **Studies → Time-step convergence…** to compare integration steps. Use
   **Simulation → Start transient from saved state…** or **Resume saved transient…**
   for compatible saved fields. Save the project with its result to retain the
   snapshot sequence.

Timing and waveform controls are kept in their own dialog and time histories in
the **Time history** canvas tab, so ordinary layout and steady workflows keep their
map space.

## Waveforms and units

A `TransientDrive` targets `terminals.<id>.value`, `heat_sources.<id>.value`
or `power.ports.<id>.value`. Eligible terminal kinds are voltage, total current,
distributed density and gate; driven physical ports use voltage or current.
Drive a port rather than one of its owned landing terminals, so its finite-feed
constraints remain consistent. The waveform replaces the target value during
integration; undriven values remain at their configured values. Each target can
have only one drive.

| Kind | Parameters | Behavior |
| --- | --- | --- |
| `constant` | `value` | A fixed value for the run |
| `pulse` | `low`, `high`, `delay`, `width`, `period`, `rise_time`, `fall_time` | Baseline, ramp up, high plateau, ramp down; repeats if period is positive |
| `piecewise_linear` | Increasing `times` and matching `values` | Linear interpolation between knots; first/last value held beyond the supplied times |

The model stores times in seconds; desktop inputs also accept units such as µs.
Amplitudes use the target's existing SI unit: V, A, A/m², W, or W/m² as appropriate.
Distributed terminal current density uses A/m² for both area and edge supports;
an edge load integrates density over edge length × sheet thickness. A pulse's
**width is the high plateau duration**;
rise + width + fall must not exceed the period. Set `period=0` for a single pulse.
For ideal rectangular pulses, the on fraction is width/period. With finite ramps,
the time above baseline is (rise + width + fall)/period; those two ratios are not
interchangeable measures of injected energy.

For a current pulse, heat is recomputed from the electrical solution. Even a
fixed resistor has $P=I^2R$, so applying the average current is generally not the
same as applying the pulse train. Do not multiply a resolved pulse's amplitude
or energy by duty cycle a second time.

## Time steps, outputs and pulse edges

`time_step` is a maximum integration step. The grid also lands on waveform edges,
piecewise-linear knots, requested output times and the exact stop time. This
prevents an ideal pulse from disappearing simply because its width is shorter
than the nominal step. It does not replace temporal refinement for the thermal
response or a ramped electrical loss.

`output_interval` controls saved field snapshots. Scalar history is retained for
every accepted integration step. Output times are also integration times, so
changing output interval can change the numerical grid; hold it fixed when
isolating the effect of `time_step`.

At an ideal discontinuity, a snapshot at the end of an interval records the
drive from the **left-hand side** that heated that interval. The initial snapshot
at t = 0 uses the right-hand value. Thus a snapshot exactly at a falling edge
can still contain the high electrical drive and the corresponding temperature;
the following interval uses the low drive. Temperature remains the integrated
state. Metadata records `drive_time_convention` and `applied_drives` so this is
inspectable. Ramp values use the incoming endpoint value, not an exact integral
of nonlinear electrical power over the ramp.

## E17: a pulse response with an analytical reference

Open [E17_pulsed_thermal.etl](../../examples/E17_pulsed_thermal.etl). The 100 × 100 ×
10 µm copper plate has uniform prescribed heating and a uniform sink to 300 K.
Because the ideal solution is spatially uniform, this case isolates time
integration from lateral mesh resolution.

The area capacity is 34.496 J/(m² K) and the sink coefficient is `1e5` W/(m² K),
giving a thermal time constant $\tau=C_A/h=344.96$ µs. A source of `1e6` W/m²
would produce a steady rise of 10 K if held continuously. The example instead
applies a 200 µs plateau every 700 µs after a 100 µs delay, ending at 2.1 ms.
Thermal-only mode excludes electrical heating.

During a constant source interval, the exact uniform response is

$$
T_{n+1}=T_a+(T_n-T_a)e^{-\Delta t/\tau}
+\frac{q''}{h}(1-e^{-\Delta t/\tau}).
$$

Applying this relation over the pulse intervals supplies an independent
continuous-time reference. The handbook verifier compares every accepted FEM
temperature with it, and independently checks each interval's pulse amplitude.

| Maximum step | Computed peak | Maximum error against RC at accepted times |
| --- | ---: | ---: |
| 25 µs | 304.97592 K | 0.113395 K |
| 12.5 µs | 305.01420 K | 0.057739 K |

Both runs inject 6 µJ over the three pulses. Regular field snapshots are scheduled
at 50 µs intervals; enabled peak retention can add an extra field. Halving the step approximately halves the error, as
expected for this first-order time discretization. The small cumulative energy
imbalance does not eliminate that temperature error.

```powershell
.\.venv\Scripts\python.exe -m etlayout transient examples/E17_pulsed_thermal.etl --time-step 1.25e-5 --output results/e17-refined
```

## E18: current pulses with thermal feedback

Open [E18_pulsed_conductor.etl](../../examples/E18_pulsed_conductor.etl). The
100 × 10 × 2 µm copper conductor is driven from 0 to 0.25 A with a 10 µs rise,
60 µs high plateau and 10 µs fall. Pulses repeat every 200 µs after a 25 µs delay.
The run ends at 400 µs. A coefficient of `2e5` W/(m² K) connects the sheet to
300 K, and electrothermal operation updates resistance as it heats.

| Maximum step | Peak temperature | Integrated generated heat |
| --- | ---: | ---: |
| 5 µs | 323.59226 K | 0.752905 µJ |
| 2.5 µs | 323.63119 K | 0.745256 µJ |

The peak changes by about 0.03893 K under this refinement, while the injected
energy also changes because the ramped current and temperature-dependent loss
are evaluated at step endpoints. This is a refinement comparison, not a claim
that either result is a fully converged time limit.

Both recorded peaks occur at **295 µs**, between the regular 10 µs saved-field
times. Peak retention now adds that field, allowing **Peak** to select it exactly.
Disabling retention restores output-grid-only storage, which can miss this peak.
Local peaks on another layer or at another probe are not automatically retained;
use a smaller output interval for their traces and recheck time-step sensitivity.

```powershell
.\.venv\Scripts\python.exe -m etlayout transient examples/E18_pulsed_conductor.etl --stop-time 4e-4 --time-step 2.5e-6 --output-interval 1e-5 --output results/e18-refined
```

Reproduce both tables, analytical checks and archive round trips with:

```powershell
.\.venv\Scripts\python.exe docs/handbook/verify_transient_walkthroughs.py --output .artifacts/handbook/transient/walkthroughs.json
```

The checked-in [transient receipt](evidence/transient-walkthroughs.json) contains
unrounded metrics, hashes and saved times. The script generates fresh inputs in
its output folder and recomputes all six runs. The [example gallery](../examples.md)
provides geometry and field previews for E17–E19; read their displayed time,
since a final cooled field is different from the maximum during a pulse.

## E19: multilayer delay and thermal crosstalk

Open [E19_pulsed_multilayer.etl](../../examples/E19_pulsed_multilayer.etl). A single
10 mW pulse heats a 30 × 30 × 5 µm copper island from 50 to 150 µs. An unpowered
30 × 30 × 10 µm copper receiver sits 20 µm away, coupled through the same 20 µm
silicon substrate. Finite interface conductances create separate sheet temperatures;
the substrate backside removes heat to 300 K. There are no electrical sources.

| Layer | Peak, 10 µs maximum step | Peak, 5 µs maximum step | Retained peak time, coarse / refined |
| --- | ---: | ---: | ---: |
| Heater | 337.69212 K | 338.21184 K | 150 / 150 µs |
| Substrate | 302.57806 K | 302.59671 K | 270 / 270 µs |
| Unpowered receiver | 301.55541 K | 301.56435 K | 690 / 680 µs |

Both runs inject exactly 1 µJ and keep a 10 µs output interval. Layer peak times
above are measured on retained fields; the refined integration grid is finer
than that output grid. These comparisons demonstrate discretization sensitivity,
not a converged continuum limit. The receiver peaks hundreds of microseconds
after the heater switches off. Removing its only thermal interface leaves the
unpowered receiver at 300 K, a regression test that distinguishes transferred
heat from an accidental local source.

In the canvas's **Time history** tab, choose **Selected layer / region** and compare
heater, substrate and receiver. Use **Compare layers** with linked times to
inspect their different temperature fields at the same instant. The receipt
records every unrounded layer peak alongside conservation and archive checks.

```powershell
.\.venv\Scripts\python.exe -m etlayout time-refine examples/E19_pulsed_multilayer.etl --steps 1e-5,5e-6 --output results/e19-time-study
```

## Python example: pulse the E1 conductor

```python
from pathlib import Path

from etlayout.io.project import load_project, save_project
from etlayout.models import TransientDrive, TransientSettings, Waveform
from etlayout.simulation.runner import run_project

project, _ = load_project("examples/E1_straight.etl")
project.solver.mode = "electrothermal"
project.transient = TransientSettings(
    enabled=True, stop_time=100e-6, time_step=1e-6, output_interval=5e-6,
    drives=[TransientDrive("terminals.drain.value", Waveform(
        kind="pulse", low=0., high=.02, delay=5e-6,
        width=10e-6, period=30e-6,
    ))],
)
result = run_project(project)
assert result.converged and result.transient.completed

for time_s, snapshot in zip(result.transient.times, result.transient.snapshots):
    print(time_s, snapshot.metrics["temperature_max"], "K")
print("Peak over accepted steps:", result.metrics["transient_peak_temperature"], "K")

output = Path("results/pulsed-e1.etl")
output.parent.mkdir(parents=True, exist_ok=True)
save_project(project, output, result)
```

The 20 mV pulse uses the E1 voltage terminal, its temperature-dependent copper
law and its existing thermal sink. All source values and waveform settings are
saved with the result.

For an existing saved transient setup, the ordinary `solve` command honors
`project.transient.enabled`. The `transient` command explicitly enables it and
accepts timing overrides. Use `--operation steady` with `solve` when
requesting a steady result from a pulse project's static input values; waveform
drives are inactive in that steady calculation. `--operation transient` explicitly
enables a saved setup. `--mode` independently selects electrical, thermal or
electrothermal physics; electrical plus transient is rejected with an explanation.

## Saved starts, periodic detection and time refinement

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E18_pulsed_conductor.etl --operation steady --mode electrical --output results/e18-dc
.\.venv\Scripts\python.exe -m etlayout transient examples/E18_pulsed_conductor.etl --initialization steady --output results/e18-biased
.\.venv\Scripts\python.exe -m etlayout transient examples/E18_pulsed_conductor.etl --initial-state results/e18-biased/simulation.etl --output results/e18-warm
.\.venv\Scripts\python.exe -m etlayout transient examples/E18_pulsed_conductor.etl --resume results/e18-biased/simulation.etl --stop-time 8e-4 --output results/e18-resumed
.\.venv\Scripts\python.exe -m etlayout transient examples/E17_pulsed_thermal.etl --stop-time 0.02 --periodic-tolerance 0.01 --periodic-min-cycles 3 --periodic-required-cycles 2 --output results/e17-periodic
.\.venv\Scripts\python.exe -m etlayout time-refine examples/E18_pulsed_conductor.etl --steps 5e-6,2.5e-6,1.25e-6 --output results/e18-time-study
```

Time and mesh studies disable early periodic stopping so all runs cover the same
requested duration. The time study also keeps saved output interval fixed. It exports absolute peak
temperature, peak time, input/output/stored energies and energy imbalance plus
changes from the preceding step. Agreement of those values supports the chosen
time resolution; a small conservation residual alone does not establish accuracy.
`validate` reports time-grid/snapshot estimates without meshing; byte estimates
require a mesh and are available in the desktop preflight. Estimates are guidance;
runtime budgets remain authoritative.
Independent study points use the configured CPU budget, shared among a bounded
number of spawned processes. Results retain their requested order and individual
failure messages. See [CPU execution and Python studies](automation-and-troubleshooting.md#cpu-execution-and-python-studies)
for the default four-CPU budget and the required Python entry-point guard.

## Read energy and completion correctly

Per-step thermal balance includes storage:

$$P_{\mathrm{in}}-P_{\mathrm{out}}=\frac{\Delta U}{\Delta t}.$$

The cumulative check is
$E_{\mathrm{in}}-E_{\mathrm{out}}=U(t)-U(0)$.
Stored energy is relative to the initial condition and can be negative during
cooling. A positive outward boundary flow removes heat.

| Metric | Meaning/unit |
| --- | --- |
| `thermal_storage_rate` | Accepted-step storage rate, W |
| `transient_input_energy` | Integrated prescribed plus electrical/contact generation, J |
| `transient_output_energy` | Integrated outward boundary heat flow, J |
| `transient_stored_energy` | Stored-energy change from initial temperature, J |
| `transient_energy_balance_error` | Absolute cumulative energy mismatch, J |
| `transient_peak_temperature` / `transient_peak_time` | Highest accepted-step temperature, K / its time, s |
| `transient_time` / `transient_requested_stop_time` | Actual accepted end time / requested end time, s |

The cumulative energy error is an absolute value in joules; the per-step
`thermal_balance_error` remains a normalized fraction. At t = 0 no integration
step has been solved, so transient storage-rate/residual/balance fields are
unavailable rather than fabricated zeros.

The outer result holds the last accepted state plus complete scalar history and
requested field snapshots. Peak-over-time metrics use all accepted steps; enabled
peak retention reserves one extra snapshot if the peak falls between regular
outputs. On a failed nonlinear step, integration
stops and retains only accepted states; `converged` and `history.completed` are
false. Cancellation raises a cancellation error. A converged transient result
means the requested integration finished, not that the structure reached steady
state.

Step count, snapshot count and retained-history bytes are bounded. Defaults are
100,000 steps, 1,000 saved fields and 256 MiB of retained history. Saved fields
have a hard ceiling of 10,000; history has a hard ceiling of 1 GiB so archives
remain compatible with the portable loader. If a budget is exceeded, increase
the output interval or make a deliberate budget change within those limits.
Reducing snapshots saves field storage; a very fine integration grid still has
a scalar-history cost.
Saving also checks the combined current result and immutable run history against
a 2 GB expanded archive budget, a 20 MB limit per JSON metadata document and
100,000 archive entries, including nested NPZ members. Compression does not
increase those limits. Rejected saves leave the previous archive intact. Remove
unneeded saved runs, or rerun with a longer output interval and remove the
superseded larger run before saving. Changing the interval affects future solves;
it does not thin snapshots already retained in a result.

The Results time controls show **displayed-frame Tmax** and **run peak/time**
separately. **Peak frame** selects its retained spatial field; **Last frame**
returns to the last accepted saved state. If the peak field is absent, **Nearest
to peak** explains the approximation. Segment peaks and early stopping remain
explicit. See [engineering results](../engineering-results.md).

## Save and export time-dependent results

The portable `.etl` archive stores one shared mesh, scalar history and the saved
field snapshots. Reopening it restores that history. The ordinary numerical
export maps the final accepted fields and includes `transient_history.csv`,
`transient_history.json`, a `transient_history.metadata.json` CSV sidecar,
time-history plots and the complete source archive.
Its manifest states the mapped time and available snapshot times. A displayed
frame export records the selected snapshot; distinguish it from a full-history
archive when sharing a result.

CSV columns carry SI units, including watts for instantaneous powers and joules
for integrated energy. JSON retains applied drives, terminal data and the
left/right timing convention. Whole-device JSON and its CSV sidecar identify the
initial source, actual segment bounds, requested stop, stop reason, initial energy
totals and energy/peak scopes. A resumed segment's energy curves remain cumulative
from the original run; its peak describes the segment. Execution metadata records
the machine's CPU settings separately from physical configuration identity.
**Export time history…** in the desktop writes
the selected whole-device, layer/region or probe sequence. For downstream processing, use
`result.transient.rows` for every accepted step and
`zip(result.transient.times, result.transient.snapshots)` for retained fields.
Local layer, region and probe traces use retained field times and preserve their
selection provenance. Comparisons can link nearest retained times or select each
time independently; exported metadata reports both actual times.

## Verification and limits

The automated transient tests cover an analytical thermal RC response,
time-step refinement, event-aligned pulse energy, adiabatic heating, two-layer
exchange, initial cooldown, fixed-temperature reactions, coupled resistivity,
waveform timing and budget/failure handling. These are code-verification tests;
the layout, material and thermal calibration checks from the steady workflow
still apply.

The current implementation has fixed geometry and heat capacity, no adaptive
error-controlled time stepping, no phase change, no transient boundary-temperature
drive and no arbitrary-field remapping. Periodic detection compares cycle-boundary
temperature states rather than solving a periodic boundary-value problem. Ideal electrical pulse edges do not model their physical
switching transient. Verify that the sheet and quasi-static approximations fit
the pulse durations and device dimensions being studied.

[Calculation workflows](workflows.md) · [Results and validation](results-and-validation.md) · [Handbook home](README.md)
