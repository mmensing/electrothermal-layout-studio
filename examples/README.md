# Example projects

Open **File → Browse example library…** in the matching source application to
search the 40 reference examples by subject, level or question. Select an example,
review its configuration and available choices, then open it. Opening prepares an
editable project; it never starts a calculation. **File → Ready-to-run examples**
retains direct shortcuts to the references.

New here? Start with **E1 · Straight conductor**, keep **Original reference**, and
follow the [first GUI calculation](../docs/handbook/first-calculation.md). Then
choose a [learning path](../docs/example-paths.md) or use the
[complete example catalog](../docs/examples.md).

## Choose by engineering question

| Subject | A first case | Continue with | GUI walkthrough |
| --- | --- | --- | --- |
| Conductors and current sharing | [E1 straight strip](E1_straight.etl) | E2/E3 → E25/E26/E28/E29/E32 | [Resistance and sharing](../docs/example-paths.md#conductors-and-current-sharing) |
| Contacts, sensing and extraction | [E6 Kelvin sheet](E6_kelvin.etl) | E27 → E27 finite-contact TLM study; E14/E31 | [Sensing and contact transfer](../docs/example-paths.md#contacts-sensing-and-extraction) |
| Multilayer routing and packages | [E4 two-metal transfer](E4_multilayer.etl) | E5 → E12/E30 → E20/E40 | [Follow a route between sheets](../docs/example-paths.md#multilayer-routing-and-packages) |
| Heat flow and thermal interaction | [E10 layered stack](E10_thermal_stack.etl) | E11/E15 → E33/E34/E35 | [Trace the thermal path](../docs/example-paths.md#heat-flow-and-thermal-interaction) |
| Pulsed operation | [E17 thermal RC reference](E17_pulsed_thermal.etl) | E18/E19 → E36 | [Heating, storage and cooling](../docs/example-paths.md#pulsed-operation) |
| Semiconductor interconnects | [E37 gated GaN coupon](E37_gated_hemt_coupon.etl), [E39 SiC islands](E39_sic_island_die.etl) | E16 → E21 → E38 → E22; E23/E24 | [Gate control and on-state collection](../docs/example-paths.md#semiconductor-interconnects) |

For **E37**, choose **Voltage-controlled GaN coupon** for the gate-control
walkthrough. For **E39**, choose **Lower-current SiC reference (2 A)** before
running: the downloaded reference preserves its original **10 A stress** setup.
Neither scenario is a calibrated device rating. E7/E8 are deliberate ungated-bypass
contrasts; E9 is a doped SiC resistor, separate from the MOSFET on-state models.

## Projects, layouts and choices

- **`.etl`** is the complete portable project, with its embedded layout, layer
  mappings, connections, conditions and stored study definitions. Save your own
  copy after opening it.
- **`.gds` / `.oas`** contain geometry only. Importing one does not reconstruct the
  physical setup. All E1–E40 filenames and stable IDs remain unchanged.
- A **size preset** changes dimensions and its declared operating point. A
  **routing variant** changes supported feed/collection geometry. These choices
  belong to E20–E24; larger presets are explicit, potentially expensive choices.
- An **operating scenario** is a named experiment on a reference, such as Joule
  heating, voltage-biased GaN or a stress limit. Availability depends on the
  selected example. **Original reference** retains the baseline; a recommended
  tutorial scenario is a separate choice.

The [power-family guide](../docs/power-examples.md) lists sizes, variants, thermal
assumptions and resource budgets. The [scientific evidence index](../docs/example-science.md)
separates generated configurations, converged observations, mesh/time sensitivity
and model limitations. No example is a physically calibrated device reference.

## Companion routing experiments

These two projects are a separate collection, outside the forty numbered
references. In **File → Browse example library…**, choose **Companion routing
studies**; downloaded ETLs also open with **File → Open project…**. Their names
are retained for existing scripts and links.

| Project | Question | Guide |
| --- | --- | --- |
| [four_cell_feed.etl](power_routing/four_cell_feed.etl) | Can bounded bus, strap and via changes reduce feed loss while preserving four fixed loads and delivered service? | [Routing setup](power_routing/README.md), [measured comparisons](../docs/power-routing-demonstrations.md) |
| [hemt_source_feed.etl](power_routing/hemt_source_feed.etl) | How do finite source feeds affect a fixed small HEMT layout? Its E8-derived channel retains the documented bypass limitations. | [Routing setup](power_routing/README.md), [acceptance and model scope](../docs/POWER_ROUTING.md) |

Use **Studies → Routing & optimization** after reviewing the project. Candidate
failures and unresolved mesh evidence remain visible; opening a project is not a
claim of an optimization improvement.

## Maintaining the assets

[`index.json`](index.json) records the forty generated reference assets. Builders
and the shared catalog live in [`src/etlayout`](../src/etlayout); generated inputs
do not establish a completed simulation. Retain the small analytical references
and use compact presets for routine tests. Larger benchmarks and solved-field
archives belong in explicit validation/artifact runs, not this directory.

After deliberately updating canonical archives or navigation metadata, run from
the repository root:

```powershell
.\.venv\Scripts\python.exe scripts/generate_examples.py --sync-runtime-resources
.\.venv\Scripts\python.exe scripts/generate_examples.py --check-runtime-resources
```

The [maintainer command](../scripts/generate_examples.py) synchronizes `index.json`
navigation metadata and the exact portable copies in `src/etlayout/data/examples`
used by installed/frozen discovery. It reads existing E1–E40 references and the
two companions; it does not generate geometry, run calculations or rewrite
scientific evidence. Check mode fails on stale metadata/copies. Unexpected runtime
archives require manual review and are never deleted by synchronization.
