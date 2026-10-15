# Example gallery

24 runnable reference examples, from analytical checks to power-device structures.
The first 19 previews pair actual prepared geometry with a computed field. The five
larger [power families](power-examples.md) have separate measured benchmark receipts
and actual-application views; opening or generating them does not launch a solve.
Open an ETL project to retain materials, boundary conditions and saved sweeps;
GDSII and OASIS contain geometry only.

All devices are synthetic teaching configurations. Results are deterministic for the recorded mesh; no experimental uncertainty or process calibration is claimed. Current peaks at ideal corners need a mesh-convergence study. Field scales and panel extents vary and are explicitly labeled. SVG previews retain vector geometry, axes and text; only the finite-element field is rasterized at twice the intended display resolution to keep downloads compact.

## Choose an example

| Example | Focus | Level | Preview mode |
|---|---|---|---|
| [E1 · Straight conductor](#e1) | Fundamentals | Starter | electrical |
| [E2 · Bent and tapered conductor](#e2) | Fundamentals | Starter | electrical |
| [E3 · Unequal parallel routes](#e3) | Fundamentals | Starter | electrical |
| [E4 · Two-metal transfer](#e4) | Interconnects | Intermediate | electrical |
| [E5 · Hierarchical via array](#e5) | Interconnects | Intermediate | electrical |
| [E6 · Kelvin sheet and contact test](#e6) | Measurements | Intermediate | electrical |
| [E7 · Interdigitated HEMT](#e7) | Devices | Intermediate | electrical |
| [E8 · HEMT with resistive routing](#e8) | Devices | Intermediate | electrical |
| [E9 · Conducting SiC sheet](#e9) | Devices | Intermediate | electrical |
| [E10 · Metal–oxide–silicon stack](#e10) | Thermal | Intermediate | electrothermal |
| [E11 · Serpentine thin-film heater](#e11) | Thermal | Advanced | electrothermal |
| [E12 · Three-level via ladder](#e12) | Interconnects | Advanced | electrothermal |
| [E13 · Perforated current spreader](#e13) | Interconnects | Advanced | electrothermal |
| [E14 · Four-arm resistive bridge](#e14) | Measurements | Advanced | electrothermal |
| [E15 · Dual-heater thermal crosstalk](#e15) | Thermal | Advanced | electrothermal |
| [E16 · Full-width gated HEMT array](#e16) | Devices | Advanced | electrothermal |
| [E17 · Pulsed heating and cooling](#e17) | Transient | Starter | transient thermal |
| [E18 · Pulsed electrothermal conductor](#e18) | Transient | Intermediate | transient electrothermal |
| [E19 · Multilayer pulse and thermal crosstalk](#e19) | Transient | Advanced | transient thermal |
| [E20 · High-current multilayer RDL](power-examples.md#reference-projects) | Power routing | Engineering | electrothermal |
| [E21 · Long-finger GaN HEMT](power-examples.md#reference-projects) | Power devices | Engineering | electrothermal |
| [E22 · Multi-bank GaN power die](power-examples.md#reference-projects) | Power devices | Engineering | electrothermal |
| [E23 · Stripe SiC on-state interconnect](power-examples.md#reference-projects) | Power devices | Engineering | electrothermal |
| [E24 · Tiled SiC source collection](power-examples.md#reference-projects) | Power devices | Engineering | electrothermal |

## Run locally

Run these commands from the repository root after the tested Windows installation. Other platforms do not have equivalent desktop/deployment validation.

```powershell
.\.venv\Scripts\python.exe -m etlayout gui examples/E11_serpentine_heater.etl
.\.venv\Scripts\python.exe -m etlayout solve examples/E11_serpentine_heater.etl --mode electrothermal --output results/E11
.\.venv\Scripts\python.exe -m etlayout sweep examples/E11_serpentine_heater.etl --output results/E11-sweep
```

The desktop **File → Ready-to-run examples** menu opens the same configurations. The per-example solve command below reproduces the preview mode; the saved sweep is a separate experiment.

<a id="e1"></a>
## E1 · Straight conductor

**Starter · Fundamentals.** Compare the solved resistance with the uniform-strip analytical limit.

[![Straight conductor: prepared geometry and potential on metal](example-gallery/E1_straight.png)](example-gallery/E1_straight.svg)

[Open project (.etl)](../examples/E1_straight.etl) · [GDSII](../examples/E1_straight.gds) · [OASIS](../examples/E1_straight.oas) · [SVG preview](example-gallery/E1_straight.svg)

**Operating point:** source 0 V (ground); drain 0.02 V (voltage). Preview mode: **electrical**.

**Physical layers:** `metal` (copper, 2 µm).

**Saved sweep:** `terminals.drain.value` = `[0.01, 0.02, 0.04]` (SI values, electrothermal)

**Physical scope:** Uniform thickness copper sheet; the analytical comparison applies at fixed 300 K. Analytical R = rho L/(w t) = 0.084 ohm at 300 K; L=100 µm, w=10 µm, t=2 µm.

**Computed preview:** 0.084 Ω effective resistance; 0.004762 W electrical power. 360 nodes / 608 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E1_straight.etl --mode electrical --output results/E1
```

<a id="e2"></a>
## E2 · Bent and tapered conductor

**Starter · Fundamentals.** See how a taper and a reentrant corner redistribute current.

[![Bent and tapered conductor: prepared geometry and current density on metal](example-gallery/E2_bent_taper.png)](example-gallery/E2_bent_taper.svg)

[Open project (.etl)](../examples/E2_bent_taper.etl) · [GDSII](../examples/E2_bent_taper.gds) · [OASIS](../examples/E2_bent_taper.oas) · [SVG preview](example-gallery/E2_bent_taper.svg)

**Operating point:** source 0 V (ground); drain 0.1 A (current). Preview mode: **electrical**.

**Physical layers:** `metal` (copper, 2 µm).

**Saved sweep:** `terminals.drain.value` = `[0.01, 0.02, 0.04]` (SI values, electrothermal)

**Physical scope:** Ideal sharp corners have mesh-sensitive peak current density; refine before comparing peaks. Bent, tapered conductor; current crowding near the reentrant corner is mesh-sensitive.

**Computed preview:** 0.06804 Ω effective resistance; 0.0006804 W electrical power. 845 nodes / 1,523 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

**Diagnostics:** Thermal boundary sink_metal extends outside its layer and is clipped.

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E2_bent_taper.etl --mode electrical --output results/E2
```

<a id="e3"></a>
## E3 · Unequal parallel routes

**Starter · Fundamentals.** Compare current sharing through unequal-width branches and common access buses.

[![Unequal parallel routes: prepared geometry and current density on metal](example-gallery/E3_parallel.png)](example-gallery/E3_parallel.svg)

[Open project (.etl)](../examples/E3_parallel.etl) · [GDSII](../examples/E3_parallel.gds) · [OASIS](../examples/E3_parallel.oas) · [SVG preview](example-gallery/E3_parallel.svg)

**Operating point:** source 0 V (ground); drain 0.3 A (current). Preview mode: **electrical**.

**Physical layers:** `metal` (copper, 2 µm).

**Saved sweep:** `terminals.drain.value` = `[0.01, 0.02, 0.04]` (SI values, electrothermal)

**Physical scope:** Distributed access resistance is included, so branch sharing is not a simple width-only ratio. Two parallel routes with 10 and 20 µm widths, including distributed access resistance.

**Computed preview:** 0.02689 Ω effective resistance; 0.00242 W electrical power. 1,152 nodes / 2,054 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

**Diagnostics:** Thermal boundary sink_metal extends outside its layer and is clipped.

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E3_parallel.etl --mode electrical --output results/E3
```

<a id="e4"></a>
## E4 · Two-metal transfer

**Intermediate · Interconnects.** Follow lateral transport through a nonrectangular second layer and a finite via.

[![Two-metal transfer: prepared geometry and potential on m2](example-gallery/E4_multilayer.png)](example-gallery/E4_multilayer.svg)

[Open project (.etl)](../examples/E4_multilayer.etl) · [GDSII](../examples/E4_multilayer.gds) · [OASIS](../examples/E4_multilayer.oas) · [SVG preview](example-gallery/E4_multilayer.svg)

**Operating point:** source 0 V (ground); drain 0.2 A (current). Preview mode: **electrical**.

**Physical layers:** `m1` (copper, 2 µm); `m2` (copper, 2 µm).

**Saved sweep:** `terminals.drain.value` = `[0.01, 0.02, 0.04]` (SI values, electrothermal)

**Physical scope:** Separate sheet potentials couple through a finite copper via; this is a 2.5D representation. Nonrectangular two-metal structure, finite copper via and separate layer potentials.

**Computed preview:** 0.06658 Ω effective resistance; 0.002663 W electrical power. 910 nodes / 1,643 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

**Diagnostics:** Thermal boundary sink_m1 extends outside its layer and is clipped. Thermal boundary sink_m2 extends outside its layer and is clipped.

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E4_multilayer.etl --mode electrical --output results/E4
```

<a id="e5"></a>
## E5 · Hierarchical via array

**Intermediate · Interconnects.** Inspect repeated layout instances and unequal transfer through a 4 × 5 via array.

[![Hierarchical via array: prepared geometry and current density on m2](example-gallery/E5_via_array.png)](example-gallery/E5_via_array.svg)

[Open project (.etl)](../examples/E5_via_array.etl) · [GDSII](../examples/E5_via_array.gds) · [OASIS](../examples/E5_via_array.oas) · [SVG preview](example-gallery/E5_via_array.svg)

**Operating point:** source 0 V (ground); drain 2 A (current). Preview mode: **electrical**.

**Physical layers:** `m1` (copper, 5 µm); `m2` (copper, 5 µm).

**Saved sweep:** `terminals.drain.value` = `[0.01, 0.02, 0.04]` (SI values, electrothermal)

**Physical scope:** Individual via footprints are resolved; through-thickness current is represented by interface links. Hierarchical 4 × 5 array of finite vias; nonuniform transfer current across a 200 µm pad.

**Computed preview:** 0.003132 Ω effective resistance; 0.01253 W electrical power. 12,160 nodes / 23,918 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E5_via_array.etl --mode electrical --output results/E5
```

<a id="e6"></a>
## E6 · Kelvin sheet and contact test

**Intermediate · Measurements.** Separate voltage sensing from current injection using zero-current floating electrodes.

[![Kelvin sheet and contact test: prepared geometry and potential on film](example-gallery/E6_kelvin.png)](example-gallery/E6_kelvin.svg)

[Open project (.etl)](../examples/E6_kelvin.etl) · [GDSII](../examples/E6_kelvin.gds) · [OASIS](../examples/E6_kelvin.oas) · [SVG preview](example-gallery/E6_kelvin.svg)

**Operating point:** source 0 V (ground); drain 0.001 A (current); sense_a 0 (floating); sense_b 0 (floating). Preview mode: **electrical**.

**Physical layers:** `film` (silicon, 1 µm); `pads` (copper, 2 µm).

**Saved sweep:** `terminals.drain.value` = `[0.01, 0.02, 0.04]` (SI values, electrothermal)

**Physical scope:** Floating sense electrodes are equipotential constraints and can perturb the sheet locally. Four-terminal sheet/contact test; floating sense terminals draw exactly zero net current.

**Computed preview:** 431.3 Ω effective resistance; 0.0004313 W electrical power. 411 nodes / 726 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

**Diagnostics:** Layer Pads contains 2 disconnected polygon islands; inspect connectivity. Thermal boundary sink_pads extends outside its layer and is clipped.

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E6_kelvin.etl --mode electrical --output results/E6
```

<a id="e7"></a>
## E7 · Interdigitated HEMT

**Intermediate · Devices.** Explore local gate control, finite source/drain contacts, and thermal coupling.

[![Interdigitated HEMT: prepared geometry and sheet current on channel](example-gallery/E7_hemt.png)](example-gallery/E7_hemt.svg)

[Open project (.etl)](../examples/E7_hemt.etl) · [GDSII](../examples/E7_hemt.gds) · [OASIS](../examples/E7_hemt.oas) · [SVG preview](example-gallery/E7_hemt.svg)

**Operating point:** source 0 V (ground); drain 0.2 V (voltage); gate_bias 0 V (voltage); field_plate_bias 0 V (voltage); gate_control 0 V (gate). Preview mode: **electrical**.

**Physical layers:** `channel` (2deg, 0.01 µm); `source_metal` (copper, 2 µm); `drain_metal` (copper, 2 µm); `gate_metal` (copper, 0.15 µm); `field_plate` (copper, 0.3 µm); `substrate` (sic, 100 µm).

**Saved sweep:** `terminals.gate_control.value` = `[-5.0, -3.0, -1.0, 1.0]` (SI values, electrothermal)

**Physical scope:** Illustrative resistive 2DEG law, with ungated edge bypass; the insulated field plate has no electrostatic effect. Illustrative depletion-mode interdigitated HEMT. Local gate control only under gate mask; access channel uses the configured ungated carrier-density cap. Field plate is insulated and has no electrostatic effect. Ungated channel edges permit bypass current. Metal/channel to substrate thermal links use an effective 2e-7 m²K/W for omitted buffer/dielectric paths; these parallel heat paths require process calibration. Not a calibrated transistor model.

**Computed preview:** 15.89 Ω effective resistance; 0.002517 W electrical power. 7,053 nodes / 13,807 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

**Diagnostics:** Physical volumes of Source Metal and Gate Metal overlap in z and x–y. Verify stack positions and material assignment; electrical potentials remain independent. Interface heat_channel extends outside its two layers and is clipped to their common area. Interface heat_source_metal extends outside its two layers and is clipped to their common area. Interface heat_drain_metal extends outside its two layers and is clipped to their common area. Interface heat_gate_metal extends outside its two layers and is clipped to their common area. Interface heat_field_plate extends outside its two layers and is clipped to their common area.

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E7_hemt.etl --mode electrical --output results/E7
```

<a id="e8"></a>
## E8 · HEMT with resistive routing

**Intermediate · Devices.** Compare thin access buses with the thicker routing in E7.

[![HEMT with resistive routing: prepared geometry and current density on source_metal](example-gallery/E8_hemt_routing.png)](example-gallery/E8_hemt_routing.svg)

[Open project (.etl)](../examples/E8_hemt_routing.etl) · [GDSII](../examples/E8_hemt_routing.gds) · [OASIS](../examples/E8_hemt_routing.oas) · [SVG preview](example-gallery/E8_hemt_routing.svg)

**Operating point:** source 0 V (ground); drain 0.2 V (voltage); gate_bias 0 V (voltage); field_plate_bias 0 V (voltage); gate_control 0 V (gate). Preview mode: **electrical**.

**Physical layers:** `channel` (2deg, 0.01 µm); `source_metal` (copper, 0.7 µm); `drain_metal` (copper, 0.7 µm); `gate_metal` (copper, 0.15 µm); `field_plate` (copper, 0.3 µm); `substrate` (sic, 100 µm).

**Saved sweep:** `terminals.gate_control.value` = `[-5.0, -3.0, -1.0, 1.0]` (SI values, electrothermal)

**Physical scope:** Same effective channel law as E7; metal thickness changes routing resistance, not transistor electrostatics. Illustrative depletion-mode interdigitated HEMT. Local gate control only under gate mask; access channel uses the configured ungated carrier-density cap. Field plate is insulated and has no electrostatic effect. Ungated channel edges permit bypass current. Metal/channel to substrate thermal links use an effective 2e-7 m²K/W for omitted buffer/dielectric paths; these parallel heat paths require process calibration. Not a calibrated transistor model.

**Computed preview:** 15.99 Ω effective resistance; 0.002501 W electrical power. 7,095 nodes / 13,891 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

**Diagnostics:** Physical volumes of Source Metal and Gate Metal overlap in z and x–y. Verify stack positions and material assignment; electrical potentials remain independent. Interface heat_channel extends outside its two layers and is clipped to their common area. Interface heat_source_metal extends outside its two layers and is clipped to their common area. Interface heat_drain_metal extends outside its two layers and is clipped to their common area. Interface heat_gate_metal extends outside its two layers and is clipped to their common area. Interface heat_field_plate extends outside its two layers and is clipped to their common area.

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E8_hemt_routing.etl --mode electrical --output results/E8
```

<a id="e9"></a>
## E9 · Conducting SiC sheet

**Intermediate · Devices.** Connect carrier density and mobility to sheet conduction and contact loss.

[![Conducting SiC sheet: prepared geometry and potential on sic](example-gallery/E9_sic.png)](example-gallery/E9_sic.svg)

[Open project (.etl)](../examples/E9_sic.etl) · [GDSII](../examples/E9_sic.gds) · [OASIS](../examples/E9_sic.oas) · [SVG preview](example-gallery/E9_sic.svg)

**Operating point:** source 0 V (ground); drain 1 V (voltage). Preview mode: **electrical**.

**Physical layers:** `sic` (sic, 2 µm); `pads` (copper, 2 µm).

**Saved sweep:** `terminals.drain.value` = `[0.01, 0.02, 0.04]` (SI values, electrothermal)

**Physical scope:** Effective doped semiconductor resistor; no junction, depletion, or breakdown physics. Effective n=10^18 cm^-3, mobility=400 cm²/Vs at 300 K; resistive sheet, no junction physics.

**Computed preview:** 226.6 Ω effective resistance; 0.004412 W electrical power. 960 nodes / 1,788 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

**Diagnostics:** Layer Pads contains 2 disconnected polygon islands; inspect connectivity. Thermal boundary sink_pads extends outside its layer and is clipped.

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E9_sic.etl --mode electrical --output results/E9
```

<a id="e10"></a>
## E10 · Metal–oxide–silicon stack

**Intermediate · Thermal.** Resolve lateral heat spreading and the resistance of a layered thermal path.

[![Metal–oxide–silicon stack: prepared geometry and temperature on substrate](example-gallery/E10_thermal_stack.png)](example-gallery/E10_thermal_stack.svg)

[Open project (.etl)](../examples/E10_thermal_stack.etl) · [GDSII](../examples/E10_thermal_stack.gds) · [OASIS](../examples/E10_thermal_stack.oas) · [SVG preview](example-gallery/E10_thermal_stack.svg)

**Operating point:** source 0 V (ground); drain 0.4 A (current). Preview mode: **electrothermal**.

**Physical layers:** `metal` (copper, 2 µm); `oxide` (sio2, 1 µm); `substrate` (silicon, 50 µm).

**Saved sweep:** `terminals.drain.value` = `[0.01, 0.02, 0.04]` (SI values, electrothermal)

**Physical scope:** Thickness-averaged layers; the bottom boundary includes half of the substrate thickness. Thickness-averaged metal/oxide/silicon model. Bottom boundary includes half substrate thickness.

**Computed preview:** 0.176 Ω effective resistance; 0.02816 W electrical power; 312.2 K overall maximum temperature. 7,899 nodes / 15,456 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

**Diagnostics:** Interface metal_oxide extends outside its two layers and is clipped to their common area. Thermal boundary top_air extends outside its layer and is clipped.

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E10_thermal_stack.etl --mode electrothermal --output results/E10
```

<a id="e11"></a>
## E11 · Serpentine thin-film heater

**Advanced · Thermal.** Inspect corner crowding, temperature feedback, and heat spreading beyond a five-pass trace.

[![Serpentine thin-film heater: prepared geometry and temperature on heater](example-gallery/E11_serpentine_heater.png)](example-gallery/E11_serpentine_heater.svg)

[Open project (.etl)](../examples/E11_serpentine_heater.etl) · [GDSII](../examples/E11_serpentine_heater.gds) · [OASIS](../examples/E11_serpentine_heater.oas) · [SVG preview](example-gallery/E11_serpentine_heater.svg)

**Operating point:** source 0 V (ground); drain 0.02 A (current); prescribed heat: background_heating 0.002 W. Preview mode: **electrothermal**.

**Physical layers:** `heater` (platinum, 0.25 µm); `substrate` (silicon, 50 µm).

**Saved sweep:** `terminals.drain.value` = `[0.01, 0.02, 0.03]` (SI values, electrothermal); `interfaces.heater_substrate.thermal_value` = `[1e-06, 2e-06, 4e-06]` (SI values, electrothermal)

**Physical scope:** Illustrative platinum film on silicon, with an effective thermal interface; sharp-corner peaks need mesh refinement. Five-pass 8 µm wide, 0.25 µm thick platinum serpentine, driven at 20 mA. Square turns demonstrate current crowding; corner peaks require a mesh study. The explicit 260 × 152 µm silicon substrate extends beyond the imported conductor. A 2e-6 m²K/W effective dielectric link and a 2e6 W/m²K backside sink are illustrative; the omitted dielectric has no resolved lateral heat flow. An independent 2 mW heater load also makes thermal-only mode meaningful. Steady-state thickness-averaged model; no transient heat capacity, radiation or calibrated thin-film transport.

**Computed preview:** 52.74 Ω effective resistance; 0.0211 W electrical power; 306.3 K overall maximum temperature. 5,567 nodes / 10,856 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

**Diagnostics:** Interface heater_substrate extends outside its two layers and is clipped to their common area. Heat source background_heating extends outside its layer and is clipped.

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E11_serpentine_heater.etl --mode electrothermal --output results/E11
```

<a id="e12"></a>
## E12 · Three-level via ladder

**Advanced · Interconnects.** Follow sequential vertical transfer through two hierarchical 3 × 3 via banks.

[![Three-level via ladder: prepared geometry and current density on m2](example-gallery/E12_via_ladder.png)](example-gallery/E12_via_ladder.svg)

[Open project (.etl)](../examples/E12_via_ladder.etl) · [GDSII](../examples/E12_via_ladder.gds) · [OASIS](../examples/E12_via_ladder.oas) · [SVG preview](example-gallery/E12_via_ladder.svg)

**Operating point:** source 0 V (ground); drain 0.3 A (current); prescribed heat: riser_heating 0.003 W. Preview mode: **electrothermal**.

**Physical layers:** `m1` (copper, 1 µm); `m2` (copper, 1 µm); `m3` (copper, 1 µm); `substrate` (silicon, 40 µm).

**Saved sweep:** `terminals.drain.value` = `[0.1, 0.3, 0.5]` (SI values, electrothermal); `interfaces.via12.height` = `[1e-06, 2e-06, 4e-06]` (SI values, electrical)

**Physical scope:** Three independent sheet potentials linked by finite copper vias; no full 3D via-sidewall field solution. Three 1 µm copper levels form a U-shaped series route with two hierarchical 3 × 3 banks of 6 × 6 µm finite copper vias, 2 µm high. A 0.3 A terminal current must cross both banks; individual vias can carry different currents. Each metal has a separate potential and temperature. Effective 1e-6 m²K/W links to a larger silicon substrate approximate omitted dielectric paths; they are parallel thermal paths requiring process calibration. An independent 3 mW load is applied to the middle riser. Vias are distributed interlayer conductances, not resolved 3D solids. The via-height sweep changes electrical resistance only; thermal via conductance is independently fixed.

**Computed preview:** 0.2367 Ω effective resistance; 0.0213 W electrical power; 303 K overall maximum temperature. 6,100 nodes / 11,914 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

**Diagnostics:** Interface heat_m1 extends outside its two layers and is clipped to their common area. Interface heat_m2 extends outside its two layers and is clipped to their common area. Interface heat_m3 extends outside its two layers and is clipped to their common area.

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E12_via_ladder.etl --mode electrothermal --output results/E12
```

<a id="e13"></a>
## E13 · Perforated current spreader

**Advanced · Interconnects.** See current diversion around staggered slots and diamond holes in a conducting sheet.

[![Perforated current spreader: prepared geometry and current density on spreader](example-gallery/E13_perforated_spreader.png)](example-gallery/E13_perforated_spreader.svg)

[Open project (.etl)](../examples/E13_perforated_spreader.etl) · [GDSII](../examples/E13_perforated_spreader.gds) · [OASIS](../examples/E13_perforated_spreader.oas) · [SVG preview](example-gallery/E13_perforated_spreader.svg)

**Operating point:** source 0 V (ground); drain 0.75 A (current); prescribed heat: local_load 0.006 W. Preview mode: **electrothermal**.

**Physical layers:** `spreader` (copper, 2 µm); `substrate` (sic, 40 µm).

**Saved sweep:** `terminals.drain.value` = `[0.25, 0.5, 0.75]` (SI values, electrothermal); `heat_sources.local_load.value` = `[0.002, 0.006, 0.012]` (SI values, thermal)

**Physical scope:** True void topology is preserved in the mesh; ideal hole corners produce mesh-sensitive current peaks. A 240 × 140 µm, 2 µm thick copper sheet contains three staggered 10 × 92 µm slots and two diamond cutouts. These are actual holes in the conductor, not decorative masks. A 0.75 A edge drive diverts current around slot ends; a separate 6 mW localized load exercises thermal-only mode. The continuous, larger SiC substrate remains beneath the copper holes. Its effective interface resistance is 5e-7 m²K/W and backside sink is 2e6 W/m²K. Sharp-corner current peaks are mesh-dependent; use spatial averages or a refinement study. This steady-state sheet approximation omits 3D sidewall spreading and mechanical stress.

**Computed preview:** 0.02849 Ω effective resistance; 0.01602 W electrical power; 301.9 K overall maximum temperature. 7,257 nodes / 14,204 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

**Diagnostics:** KLayout normalized a nonsimple polygon/cutline on mask 1/0: Ring Self-intersection[0.00021 8e-05]. Inspect the resulting geometry; cutlines encoding holes are expected in GDS. Interface sheet_substrate extends outside its two layers and is clipped to their common area.

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E13_perforated_spreader.etl --mode electrothermal --output results/E13
```

<a id="e14"></a>
## E14 · Four-arm resistive bridge

**Advanced · Measurements.** Read a differential bridge imbalance with separate current drive and floating midpoint probes.

[![Four-arm resistive bridge: prepared geometry and potential on arm_br](example-gallery/E14_bridge_sensor.png)](example-gallery/E14_bridge_sensor.svg)

[Open project (.etl)](../examples/E14_bridge_sensor.etl) · [GDSII](../examples/E14_bridge_sensor.gds) · [OASIS](../examples/E14_bridge_sensor.oas) · [SVG preview](example-gallery/E14_bridge_sensor.svg)

**Operating point:** source 0 V (ground); drain 0.1 V (voltage); sense_top 0 (floating); sense_bottom 0 (floating); prescribed heat: sensor_load 0.0002 W. Preview mode: **electrothermal**.

**Physical layers:** `arm_tl` (tan, 0.2 µm); `arm_tr` (tan, 0.2 µm); `arm_bl` (tan, 0.2 µm); `arm_br` (tan, 0.2 µm); `pads` (copper, 2 µm); `substrate` (silicon, 30 µm).

**Saved sweep:** `layers.arm_br.electrical.sheet_resistance.value` = `[80.0, 100.0, 120.0]` (SI values, electrical)

**Physical scope:** Four effective sheet resistors and metal pads; resistor mismatch represents a sensor response without a mechanical model. Four-arm Wheatstone bridge: three 100 Ω/□ TaN sheets and one 110 Ω/□ sensing arm. The 0.1 V drive uses finite-conductivity metal buses and ideal arm/pad interfaces. Floating midpoint electrodes draw zero net current. Read V(sense_top) − V(sense_bottom); effective resistance instead reports the source/drain drive resistance. Sweep the lower-right sheet resistance through 100 Ω/□ to reverse the differential output. A separate 0.2 mW prescribed sensor load also makes thermal-only operation meaningful; fixed sheet resistance does not model thermoresistive transduction. Synthetic 2.5D demonstration, not a calibrated sensor.

**Computed preview:** 640.3 Ω effective resistance; 1.562e-05 W electrical power; 300.4 K overall maximum temperature. 1,717 nodes / 3,280 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

**Diagnostics:** Layer Bridge electrodes contains 4 disconnected polygon islands; inspect connectivity. Interface contact_arm_tl extends outside its two layers and is clipped to their common area. Interface heat_arm_tl extends outside its two layers and is clipped to their common area. Interface contact_arm_tr extends outside its two layers and is clipped to their common area. Interface heat_arm_tr extends outside its two layers and is clipped to their common area. Interface contact_arm_bl extends outside its two layers and is clipped to their common area. Interface heat_arm_bl extends outside its two layers and is clipped to their common area. Interface contact_arm_br extends outside its two layers and is clipped to their common area. Interface heat_arm_br extends outside its two layers and is clipped to their common area. Interface heat_pads extends outside its two layers and is clipped to their common area. Heat source sensor_load extends outside its layer and is clipped.

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E14_bridge_sensor.etl --mode electrothermal --output results/E14
```

<a id="e15"></a>
## E15 · Dual-heater thermal crosstalk

**Advanced · Thermal.** Inspect overlapping temperature fields from independently driven meanders on a shared substrate.

[![Dual-heater thermal crosstalk: prepared geometry and temperature on substrate](example-gallery/E15_thermal_crosstalk.png)](example-gallery/E15_thermal_crosstalk.svg)

[Open project (.etl)](../examples/E15_thermal_crosstalk.etl) · [GDSII](../examples/E15_thermal_crosstalk.gds) · [OASIS](../examples/E15_thermal_crosstalk.oas) · [SVG preview](example-gallery/E15_thermal_crosstalk.svg)

**Operating point:** source_a 0 V (ground); drain_a 0.4 V (voltage); source_b 0 V (ground); drain_b 0.25 V (voltage); prescribed heat: load_a 0.0005 W, load_b 0.00015 W. Preview mode: **electrothermal**.

**Physical layers:** `heater_a` (tin, 0.5 µm); `heater_b` (tin, 0.5 µm); `substrate` (silicon, 30 µm).

**Saved sweep:** `heat_sources.load_a.value` = `[0.0, 0.0005, 0.002]` (SI values, thermal); `terminals.drain_a.value` = `[0.2, 0.4, 0.6]` (SI values, electrothermal)

**Physical scope:** Effective sheet-to-substrate heat transfer; prescribed sources also permit a meaningful independent thermal solve. Two electrically independent three-pass TiN heaters share a larger silicon thermal layer. A and B are biased at 0.4 V and 0.25 V; prescribed loads add 0.5 mW and 0.15 mW respectively. Electrical mode computes each powered net; reported resistance uses A only. Thermal-only mode uses prescribed loads without Joule heating, whereas electrothermal mode adds both Joule loads. Sweep load_a with B held fixed and inspect the rise of B temperature to observe thermal crosstalk. Effective 2e-6 m²K/W interfaces represent omitted isolation stacks; the 5e4 W/m²K backside sink and representative film properties need calibration. Synthetic steady-state 2.5D example.

**Computed preview:** 154.4 Ω effective resistance; 0.001441 W electrical power; 305.2 K overall maximum temperature. 4,688 nodes / 9,124 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

**Diagnostics:** Interface heat_a extends outside its two layers and is clipped to their common area. Interface heat_b extends outside its two layers and is clipped to their common area. Heat source load_a extends outside its layer and is clipped. Heat source load_b extends outside its layer and is clipped.

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E15_thermal_crosstalk.etl --mode electrothermal --output results/E15
```

<a id="e16"></a>
## E16 · Full-width gated HEMT array

**Advanced · Devices.** Contrast E7's edge bypass with gate control that spans every channel in a four-channel array.

[![Full-width gated HEMT array: prepared geometry and sheet current on channel](example-gallery/E16_full_gate_hemt_array.png)](example-gallery/E16_full_gate_hemt_array.svg)

[Open project (.etl)](../examples/E16_full_gate_hemt_array.etl) · [GDSII](../examples/E16_full_gate_hemt_array.gds) · [OASIS](../examples/E16_full_gate_hemt_array.oas) · [SVG preview](example-gallery/E16_full_gate_hemt_array.svg)

**Operating point:** source 0 V (ground); drain 0.1 V (voltage); gate_control 0 V (gate); prescribed heat: channel_load 0.0002 W. Preview mode: **electrothermal**.

**Physical layers:** `channel` (2deg, 0.01 µm); `source_metal` (copper, 1 µm); `drain_metal` (copper, 1 µm); `gate_metal` (gold, 0.2 µm); `substrate` (sic, 75 µm).

**Saved sweep:** `terminals.gate_control.value` = `[-5.0, -3.0, -1.0, 1.0]` (SI values, electrothermal)

**Physical scope:** Resistive 2DEG approximation with finite contacts; no velocity saturation, gate leakage, trapping, or electrostatic field-plate model. Four separated parallel GaN 2DEG stripes join shared source/drain metal buses through finite 1e-10 Ωm² contacts, with 0.1 V drain bias. One gate bar spans each complete channel width, so every source-to-drain channel path crosses a controlled region; there is no ungated edge bypass. The gold gate is electrically insulated and modeled as a thermal-only layer; its voltage enters the local analytical charge-control model through gate_control. No gate leakage, capacitance, electrostatic field solution, velocity saturation or calibrated transistor prediction is provided. A 1e-7 S sheet-conductance floor limits deep-off current and maintains numerical conditioning; the off-state current is not a leakage prediction. A 0.2 mW prescribed channel load supports thermal-only operation and adds to Joule heating in coupled mode. Effective thermal interfaces represent omitted buffer/dielectric paths.

**Computed preview:** 687.5 Ω effective resistance; 1.455e-05 W electrical power; 300 K overall maximum temperature. 5,027 nodes / 9,798 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

**Diagnostics:** Layer Four GaN 2DEG channels contains 4 disconnected polygon islands; inspect connectivity. Interface heat_channel extends outside its two layers and is clipped to their common area. Interface heat_source_metal extends outside its two layers and is clipped to their common area. Interface heat_drain_metal extends outside its two layers and is clipped to their common area. Interface heat_gate_metal extends outside its two layers and is clipped to their common area. Terminal gate_control extends outside its layer and is clipped. Heat source channel_load extends outside its layer and is clipped.

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E16_full_gate_hemt_array.etl --mode electrothermal --output results/E16
```

<a id="e17"></a>
## E17 · Pulsed heating and cooling

**Starter · Transient.** Compare pulse heating and cooling with the uniform lumped thermal RC limit, then refine the time step.

[![Pulsed heating and cooling: prepared geometry and temperature on plate](example-gallery/E17_pulsed_thermal.png)](example-gallery/E17_pulsed_thermal.svg)

[Open project (.etl)](../examples/E17_pulsed_thermal.etl) · [GDSII](../examples/E17_pulsed_thermal.gds) · [OASIS](../examples/E17_pulsed_thermal.oas) · [SVG preview](example-gallery/E17_pulsed_thermal.svg)

**Operating point:** source 0 V (ground); drain 0 V (voltage); prescribed heat: heater 1e+06 W/m². Preview mode: **thermal**.

**Physical layers:** `plate` (copper, 10 µm).

**Saved sweep:** `transient.drives.0.waveform.high` = `[250000.0, 500000.0, 1000000.0]` (SI values, thermal)

**Physical scope:** Prescribed heat drives uniform copper thermal storage; electrical Joule heating is excluded. Pulse width means high plateau. Uniform prescribed heat pulses in a 100 × 100 × 10 µm copper plate, with h=1e5 W/(m² K). Thermal-only operation excludes Joule heating. The spatially uniform limit has tau=rho*cp*t/h and high-level steady rise=10 K, so the transient can be checked against a lumped RC. Pulse width is the 200 µs plateau, period 700 µs, and delay 100 µs. Reduce dt to check time accuracy.

**Transient setup:** stop 0.0021 s; maximum step 2.5e-05 s; saved output interval 5e-05 s. `heat_sources.heater.value`: 0 → 1e+06, delay 0.0001 s, rise 0 s, plateau 0.0002 s, fall 0 s, period 0.0007 s (target SI units). The field preview shows the final accepted time; the saved project opens the full time workflow.

**Computed preview:** 301.6 K maximum temperature at final time; 305 K peak over all time steps; 6e-06 J integrated input heat. 302 nodes / 542 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E17_pulsed_thermal.etl --mode thermal --output results/E17
```

<a id="e18"></a>
## E18 · Pulsed electrothermal conductor

**Intermediate · Transient.** Follow current pulses, Joule energy, delayed temperature peaks and cooling with temperature feedback.

[![Pulsed electrothermal conductor: prepared geometry and temperature on metal](example-gallery/E18_pulsed_conductor.png)](example-gallery/E18_pulsed_conductor.svg)

[Open project (.etl)](../examples/E18_pulsed_conductor.etl) · [GDSII](../examples/E18_pulsed_conductor.gds) · [OASIS](../examples/E18_pulsed_conductor.oas) · [SVG preview](example-gallery/E18_pulsed_conductor.svg)

**Operating point:** source 0 V (ground); drain 0.25 A (current). Preview mode: **electrothermal**.

**Physical layers:** `metal` (copper, 2 µm).

**Saved sweep:** `transient.drives.0.waveform.high` = `[0.05, 0.15, 0.25]` (SI values, electrothermal)

**Physical scope:** Backward Euler thermal storage with quasi-static electrical feedback; no electrical capacitance, inductance or switching losses. Pulsed 100 × 10 × 2 µm copper conductor with quasi-static current and temperature-dependent resistivity. The 0.25 A pulse has 10 µs rise, 60 µs high plateau and 10 µs fall, repeated every 200 µs after a 25 µs delay. Finite edge times need time-step refinement. Heat capacity and cooling produce delayed thermal response; electrical capacitance and inductance are not included.

**Transient setup:** stop 0.0004 s; maximum step 5e-06 s; saved output interval 1e-05 s. `terminals.drain.value`: 0 → 0.25, delay 2.5e-05 s, rise 1e-05 s, plateau 6e-05 s, fall 1e-05 s, period 0.0002 s (target SI units). The field preview shows the final accepted time; the saved project opens the full time workflow.

**Computed preview:** 0 W electrical power; 301.4 K maximum temperature at final time; 323.6 K peak over all time steps; 7.529e-07 J integrated input heat. 360 nodes / 608 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E18_pulsed_conductor.etl --mode electrothermal --output results/E18
```

<a id="e19"></a>
## E19 · Multilayer pulse and thermal crosstalk

**Advanced · Transient.** Compare heater, substrate and unpowered receiver histories to locate the delayed crosstalk peak.

[![Multilayer pulse and thermal crosstalk: prepared geometry and temperature on substrate](example-gallery/E19_pulsed_multilayer.png)](example-gallery/E19_pulsed_multilayer.svg)

[Open project (.etl)](../examples/E19_pulsed_multilayer.etl) · [GDSII](../examples/E19_pulsed_multilayer.gds) · [OASIS](../examples/E19_pulsed_multilayer.oas) · [SVG preview](example-gallery/E19_pulsed_multilayer.svg)

**Operating point:** ; prescribed heat: pulse_heat 0.01 W. Preview mode: **thermal**.

**Physical layers:** `heater` (copper, 5 µm); `receiver` (copper, 10 µm); `substrate` (silicon, 20 µm).

**Saved sweep:** `transient.drives.0.waveform.high` = `[0.0025, 0.005, 0.01]` (SI values, thermal)

**Physical scope:** A prescribed single heat pulse transfers through finite interfaces and a shared silicon sheet; no electrical drive or through-thickness diffusion. A 10 mW, 100 µs single pulse heats a 30 × 30 × 5 µm copper island. An unpowered 30 × 30 × 10 µm copper receiver sits 20 µm away on the same 20 µm silicon substrate. Finite interface conductances create separate layer temperatures; receiver heating and its delayed peak arise entirely from thermal crosstalk. Inspect heater, substrate and receiver histories. Prescribed heat only; no electrical solve. Input energy is 1 µJ. Thickness-averaged sheets and representative materials require process calibration.

**Transient setup:** stop 0.001 s; maximum step 1e-05 s; saved output interval 1e-05 s. `heat_sources.pulse_heat.value`: 0 → 0.01, delay 5e-05 s, rise 0 s, plateau 0.0001 s, fall 0 s, period 0 s (target SI units). The field preview shows the final accepted time; the saved project opens the full time workflow.

**Computed preview:** 301.4 K maximum temperature at final time; 337.7 K peak over all time steps; 1e-06 J integrated input heat. 977 nodes / 1,838 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

**Diagnostics:** Interface heater_substrate extends outside its two layers and is clipped to their common area. Interface receiver_substrate extends outside its two layers and is clipped to their common area. Heat source pulse_heat extends outside its layer and is clipped.

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E19_pulsed_multilayer.etl --mode thermal --output results/E19
```

## Rebuild this gallery

```powershell
.\.venv\Scripts\python.exe scripts/build_example_gallery.py --use-cache
.\.venv\Scripts\python.exe scripts/build_example_gallery.py --examples E11 E16 --output .artifacts/gallery-preview --markdown .artifacts/gallery-preview.md
```

The generator reads existing projects from `examples/` and creates missing ones. Its cache is reused only when the physics hash, solver version and source-code fingerprint match. Use `--no-solve` for explicitly labeled geometry-only previews. The manifest records the plotted range, units, mesh, convergence, diagnostics, operating point and configuration hashes.
