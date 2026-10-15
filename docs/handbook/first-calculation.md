# First calculation: a copper strip

[Handbook](README.md) · [Next: model and physics →](model-and-physics.md)

This example checks the entire import-to-result path against a resistance you can
calculate by hand. Start from the repository root with the dependencies installed
using the [README instructions](../../README.md#start-from-source-on-windows).

## Open the experiment

Launch the desktop after the one-time installation, then select **File →
Ready-to-run examples → E1 · Straight conductor**. The project already contains the
layout, copper material snapshot, physical layer and terminal conditions. No
scripting or JSON editing is needed.

You will check a known answer, learn the field controls, and save a result before
moving on to a less uniform layout.

| Input | E1 value |
| --- | --- |
| Physical layer ID | `metal` |
| Length × width | 100 µm × 10 µm |
| Thickness | 2 µm |
| Copper resistivity at 300 K | 1.68 × 10⁻⁸ Ω m |
| `source` terminal | Ground, at x = 0 |
| `drain` terminal | 0.02 V, at x = 100 µm |
| Calculation | Electrical, evaluated at 300 K |

The names `source` and `drain` are identifiers. Positive terminal current means
current entering the model; in this experiment it enters at the positive-voltage
`drain` and leaves through the grounded `source`.

## Validate, mesh and run

1. Choose **Simulation → Validate geometry & connectivity** or press **F6**. Read
   the Issues drawer; use **View → Hierarchy / layers / nets** for network
   inspection. E1 should be one electrically connected sheet with a voltage reference.
2. Generate the mesh with **F7**. Inspect the mesh on the strip. Boundary terminals
   should coincide with its two ends.
3. Choose **DC electrical** in the header's **Analysis type** selector and run with **F5**. Review the
   convergence status and current/power balance in the result output.
4. In **Results**, select layer **metal**, region **All solved regions**, and **Potential**.
   The potential should increase linearly from 0 to 0.02 V along x.
5. Select **Volumetric current density**. The magnitude should be uniform. A
   profile through the strip center (**Results → Draw line profile**) can confirm
   that visually. Enable **Show vectors** in **Appearance** to inspect the
   direction of the selected vector quantity.

![E1 actual geometry and computed potential](../example-gallery/E1_straight.png)

This gallery image is the electrical example at the stated operating point. Its
[manifest](../example-gallery/manifest.json) records the source and plotted range.

## Check the answer

The uniform-strip resistance follows directly from the sheet model:

$$
R = \rho_e\frac{L}{wt}
  = 1.68\times10^{-8}\frac{100\times10^{-6}}
    {(10\times10^{-6})(2\times10^{-6})}
  = 0.084\ \Omega.
$$

Therefore $I = 0.02/R = 0.238095$ A and $P = VI = 0.00476190$ W.
The [recomputed receipt](evidence/walkthroughs.json) agrees with these values:

| Quantity | Analytical value | Computed, rounded |
| --- | ---: | ---: |
| Resistance | 0.084 Ω | 0.084 Ω |
| Total positive terminal injection | 0.238095 A | 0.238095 A |
| Lateral Joule loss | 4.76190 mW | 4.76190 mW |

The result contains 360 planar nodes and 608 triangles in that receipt. A new
mesh/backend version may change these counts; the resistance comparison is the
useful check. This simple linear potential is represented exactly by the P1
elements up to numerical roundoff. It does not establish accuracy for a bend,
contact crowding region, or nonlinear device.

## Save and reproduce

Use **File → Save project as** to save the input configuration and result in a
portable project archive. Reopen it with **File → Open project…** and confirm that
the same layer and recorded result remain available. Use **Results → Export task**
for a figure, numerical fields or a self-contained report.

<details>
<summary>Optional: reproduce the same calculation from the command line</summary>

For a headless calculation and numerical exports:

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E1_straight.etl --mode electrical --output results/handbook-e1
```

Inspect `results.json`, `metrics.csv`, `terminals.csv`, `manifest.json`, the
per-layer fields and `simulation.etl` in the output directory. The manifest maps
layer IDs to actual filenames. A saved figure alone cannot reproduce a solve.

</details>

## One useful variation

Run the same voltage in **electrothermal** mode. E1 includes a distributed thermal
sink to 300 K. Heating changes copper resistivity and therefore current; the
fixed-300-K analytical comparison above no longer applies directly. This is the
distinction between an isothermal electrical calculation and a coupled
electrothermal calculation.

For the next experiment, follow the [multilayer thermal stack](worked-examples.md#e10-a-metaloxidesilicon-thermal-stack).

[← Handbook](README.md) · [Model and physics →](model-and-physics.md)
