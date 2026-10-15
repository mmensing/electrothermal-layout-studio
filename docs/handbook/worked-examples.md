# Worked examples: strip, thermal stack and via ladder

[Handbook](README.md) · [← Workflows](workflows.md) · [Next: results and validation →](results-and-validation.md)

These examples build from a known analytical answer to separate temperatures
and currents in a multilayer structure. Values below come from fresh solves of
the checked-in projects, rounded for readability. They are illustrative
calculation results, not measured device data or a mesh-convergence claim.

Run every numerical check in this chapter with:

```powershell
.\.venv\Scripts\python.exe docs/handbook/verify_walkthroughs.py --output .artifacts/handbook/walkthroughs.json
```

The script asserts the E1 analytical resistance, unloaded E10 equilibrium,
imposed E12 terminal current and E12 heat accounting. Compare its output with
the checked-in [receipt](evidence/walkthroughs.json), which retains unrounded
values, hashes, mesh counts and diagnostics.

## E1: establish the electrical reference

Open [E1_straight.etl](../../examples/E1_straight.etl) and follow
[the first calculation](first-calculation.md). The 100 × 10 × 2 µm copper strip
has 0.084 Ω resistance at 300 K. Driving 20 mV produces 0.238095 A and
4.76190 mW.

Use this case to recognize an expected field: linear potential, constant
current-density magnitude, opposite signed terminal currents and equal terminal
work and lateral Joule loss. Then open E2 to see why a bend invalidates the
uniform-strip current-density assumption even while integrated terminal current
remains meaningful.

## E10: a metal/oxide/silicon thermal stack

**Question:** how much can three layers at the same x–y location differ in
temperature?

Open [E10_thermal_stack.etl](../../examples/E10_thermal_stack.etl). A 200 µm long,
10 µm wide, 2 µm thick copper strip sits above a 1 µm oxide and a 50 µm silicon
substrate. Oxide and substrate span a wider 220 × 120 µm region. The strip is
current-driven at 0.4 A.

![E10 geometry and computed substrate temperature](../example-gallery/E10_thermal_stack.png)

The gallery preview shows the **substrate**. The much hotter metal sheet has its
own field and range, inspected in the steps below.

The two thermal `stack` interfaces include adjacent half-layer resistances and
an additional `1e-8` m² K/W each. The substrate backside coefficient is
$148/(25\times10^{-6})$ W/(m² K), accounting for the remaining half of the
substrate thickness at its nominal conductivity. A much smaller top-air
coefficient of 10 W/(m² K) is applied to the metal. Those choices are model inputs;
they are not fitted measurements.

1. Inspect the layer z positions and thermal links in Setup and the cross-section.
   The metal has electrical fields; the oxide and substrate have thermal fields.
2. Solve in **thermal** mode with **Include electrical heating** off. E10 has no prescribed
   heat source, so the maximum temperature is exactly 300 K. The current terminal
   does not by itself request an electrical solve in that mode.
3. Solve in **electrothermal** mode. At the checked-in mesh, electrical loss is
   approximately **28.1596 mW** and peak temperature **312.1645 K**. The receipt
   records a thermal balance error of about **6.45 × 10⁻⁶** as a fraction.
4. Choose **Temperature**, then switch the result layer between `metal`, `oxide`
   and `substrate`. Use a shared manual range or **Compare layers** to keep the
   numerical color meaning fixed.
5. Probe x = 100 µm, y = 50 µm on each layer. This position lies on the strip center.

| Layer at (100 µm, 50 µm) | Computed temperature |
| --- | ---: |
| Metal | 312.1643 K |
| Oxide | 307.3332 K |
| Substrate | 300.3289 K |

These are distinct sheet temperatures separated by thermal resistance. A common
planar mesh does not impose the same temperature on the layers. The display
selection is essential: a metal hotspot map and a substrate map answer different
questions.

The receipt contains geometry warnings that the full-domain `metal_oxide`
interface and `top_air` boundary are clipped to their physical overlap. Here that
is intentional. Check the actual footprint before treating such a warning as
harmless in another project.

For a mesh study, track peak and average temperature and a fixed centerline
profile while reducing mesh sizes. For a stack sensitivity study, vary an
interface area resistance while holding the electrical load fixed. Do not
interpret the small algebraic balance error as a bound on mesh or material error.

## E12: a three-level route with two via banks

**Question:** how does the same series current cross several sheets, and where
does its heat enter the thermal model?

Open [E12_via_ladder.etl](../../examples/E12_via_ladder.etl). Three 1 µm copper
levels form a U-shaped route. Each of the two interlayer banks has a 3 × 3 array
of 6 × 6 µm copper via footprints, with a 2 µm electrical via height.

![E12 actual geometry and computed middle-layer current density](../example-gallery/E12_via_ladder.png)

| Layer ID | Function | Bottom elevation |
| --- | --- | ---: |
| `m1` | Lower horizontal route, grounded at its left edge | 0 µm |
| `m2` | Right-hand riser between the via banks | 3 µm |
| `m3` | Upper return, driven by a 0.3 A terminal at its left edge | 6 µm |
| `substrate` | Wider 40 µm silicon thermal sheet | −40 µm |

Conventional current enters the `drain` on `m3`, crosses `via23` into `m2`, then
`via12` into `m1`, and exits the grounded `source`. Each metal retains separate
potential and temperature fields. Individual vias need not share current equally:
the lateral access geometry determines their distribution.

1. Run validation and trace the terminal connection through all three metals.
   An x–y overlap without the two electrical interfaces would leave the route open.
2. Solve in **electrical** mode. The imposed current remains 0.3 A; the terminal
   voltage adjusts to satisfy it.
3. Inspect `m1`, `m2` and `m3` separately. Compare two layers using linked pan/zoom
   and a common current-density range. Select an interface footprint in the
   result-region selector to focus on its participating triangles.
4. Switch to **electrothermal** and rerun. There is also a separate **3 mW** heat
   source on the middle riser. It remains additive to electrical losses.
5. Review the thermal links: each metal has an effective `1e-6` m² K/W link to
   silicon, alongside finite via thermal conductances. These approximate omitted
   dielectric paths and require process calibration.

| Metric | Electrical at 300 K | Coupled |
| --- | ---: | ---: |
| Total positive terminal current | 0.300000 A | 0.300000 A |
| Effective resistance | 0.235071 Ω | 0.236688 Ω |
| Electrical loss | 21.1564 mW | 21.3019 mW |
| Contact loss, included in electrical loss | 0.088744 mW | 0.089156 mW |
| Total thermal generation | Not solved | 24.3019 mW |
| Peak temperature | Not solved | 302.9760 K |

The power accounting is deliberate: **24.3019 mW = 21.3019 mW + 3 mW**. Do not
add contact loss to `electrical_power` a second time; it is already included.
The thermal solver distributes contact heat half to each neighboring sheet.

The saved current sweep uses 0.1, 0.3 and 0.5 A in coupled mode. The second saved
sweep changes `interfaces.via12.height` electrically. Its independently fixed
thermal conductance does **not** change with that height; use a coordinated
thermal model change if the physical experiment requires one.

## Reproduce a cross-layer probe in Python

Save this as a Python file and run it from the repository root:

```python
import numpy as np

from etlayout.io.project import load_project
from etlayout.simulation.runner import run_project
from etlayout.visualization.sampling import FieldSampler

project, _ = load_project("examples/E10_thermal_stack.etl")
project.solver.mode = "electrothermal"
result = run_project(project)
assert result.converged

sampler = FieldSampler(result)
point = np.array([[100e-6, 50e-6]])  # meters
for layer in ("metal", "oxide", "substrate"):
    value = sampler.sample(layer, "temperature", point)[0]
    print(layer, value, "K")
```

Samples outside finite support return NaN. Do not fill a physical hole or an
inactive layer with interpolated values from another sheet.

Continue with the [power-device families](../power-examples.md) for
long-finger/bank current sharing and vertical SiC on-state source collection.
E7/E8 retain their documented edge bypass; E9 remains a doped SiC resistor.
Those teaching models are not the new power-device foundations.

[← Calculation workflows](workflows.md) · [Results, exports and validation →](results-and-validation.md)
