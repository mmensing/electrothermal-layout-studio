<!-- BEGIN GENERATED EXAMPLE CONTENT -->
# Foundational example walkthroughs

This gallery covers 19 of the library's 40 runnable reference examples. Use the [complete chooser](examples.md) to select by physical question rather than E-number.

## Read the current evidence before interpreting an old picture

The [maintained nominal matrix](example-science/nominal-reference.md) records all forty configured calculations. [Scientific applicability](example-science.md) and the linked scenario guides separate numerical convergence, engineering acceptance, mesh/time sensitivity and physical calibration.

The scientific previews below are historical result illustrations, retained with their original operating points, solver versions and hashes. They have not been recalculated for this documentation edit. Their old clipping/overlap diagnostics are superseded as current setup guidance; newly run validation and maintained evidence describe the current configuration. A converged picture does not verify a different scenario or certify mesh independence.

Every walkthrough follows the same sequence: question, open and inspect, run and read, change one thing, evidence, limits and next step. Use the [learning paths](example-paths.md) for progression across the whole library.

ETL projects embed configuration and geometry; GDSII/OASIS contain geometry only. SVG previews retain vector geometry and labels; the finite-element field is rasterized. Physical geometry is equal aspect and may appear very narrow for a long finger. Use **Fit layer**, meaningful zoom and probes in the GUI; do not infer missing geometry from white space. Sheet current uses mA/µm; metal volumetric and via normal current densities are separate quantities using mA/µm². New figures use engineering-prefixed scalar units; retained images keep their stated scales.

## Examples in this walkthrough

| Example | Subject | Recorded preview analysis |
| --- | --- | --- |
| [E1 · Straight conductor](#e1) | Conductors and current sharing | DC electrical |
| [E2 · Bent and tapered conductor](#e2) | Conductors and current sharing | DC electrical |
| [E3 · Unequal parallel routes](#e3) | Conductors and current sharing | DC electrical |
| [E4 · Two-metal transfer](#e4) | Multilayer routing and packages | DC electrical |
| [E5 · Hierarchical via array](#e5) | Multilayer routing and packages | DC electrical |
| [E6 · Kelvin sheet and contact test](#e6) | Contacts, sensing and extraction | DC electrical |
| [E7 · Interdigitated HEMT](#e7) | Semiconductor interconnects | DC electrical |
| [E8 · HEMT with resistive routing](#e8) | Semiconductor interconnects | DC electrical |
| [E9 · Conducting SiC sheet](#e9) | Semiconductor interconnects | DC electrical |
| [E10 · Metal–oxide–silicon stack](#e10) | Heat flow and thermal interaction | Coupled DC electrothermal |
| [E11 · Serpentine thin-film heater](#e11) | Heat flow and thermal interaction | Coupled DC electrothermal |
| [E12 · Three-level via ladder](#e12) | Multilayer routing and packages | Coupled DC electrothermal |
| [E13 · Perforated current spreader](#e13) | Conductors and current sharing | Coupled DC electrothermal |
| [E14 · Four-arm resistive bridge](#e14) | Contacts, sensing and extraction | Coupled DC electrothermal |
| [E15 · Dual-heater thermal crosstalk](#e15) | Heat flow and thermal interaction | Coupled DC electrothermal |
| [E16 · Full-width gated HEMT array](#e16) | Semiconductor interconnects | Coupled DC electrothermal |
| [E17 · Pulsed heating and cooling](#e17) | Pulsed operation | Thermal transient |
| [E18 · Pulsed electrothermal conductor](#e18) | Pulsed operation | Electrothermal transient |
| [E19 · Multilayer pulse and thermal crosstalk](#e19) | Pulsed operation | Thermal transient |

<a id="e1"></a>
## E1 · Straight conductor

**Question:** Compare the solved resistance with the uniform-strip analytical limit.

**Starter · Conductors and current sharing · benchmark.** compact steady calculation.

[![Straight conductor: prepared geometry and potential on metal](example-gallery/E1_straight.png)](example-gallery/E1_straight.svg)

**Open and inspect:** choose **File → Ready-to-run examples → Conductors and current sharing → E1** for the original project. Review layers, contacts and operating conditions before editing.

**Starting setup:** **Original reference** · **DC electrical**. Original reference inputs are retained.

**Run and read:** validate (F6), generate the mesh (F7), then run (F5). In Results select `metal` and **Potential (V)**, then **Fit layer**. Open Metrics & loss budget and compare the resistance with the uniform-strip value in the reference details.

**Look for:** A smooth voltage drop along the straight strip; the fixed-temperature resistance follows its length, width and thickness.

**Change one thing:** Use Parameter sweep… to vary the drain voltage in DC electrical mode. Does the resistance stay constant while Joule power rises?

**Evidence:** [Maintained nominal measurements](example-science/nominal-reference.md) · [Applicability and verification](example-science.md). The retained image records **DC electrical**; its original inputs and hashes remain in the expandable preview record. It does not certify the currently selected scenario.

**Limits:** Uniform thickness copper sheet; the analytical comparison applies at fixed 300 K.

**Next:** [E25 · Stepped-width series reference](extended-examples.md#e25) in [Conductors and current sharing](example-paths.md#conductors).

[Project (.etl)](../examples/E1_straight.etl) · [GDSII](../examples/E1_straight.gds) · [OASIS](../examples/E1_straight.oas) · [Vector preview](example-gallery/E1_straight.svg)

<details>
<summary>Recorded inputs, model limits and numerical details</summary>

**Recorded operating point:** source 0 V (ground); drain 0.02 V (voltage). Preview mode: **electrical**.

**Physical layers:** `metal` (copper, 2 µm).

**Saved sweep:** `terminals.drain.value` = `[0.01, 0.02, 0.04]` (SI values, electrothermal)

The saved sweep is a separate experiment; review the selected parameter, values and analysis in **Parameter sweep…** before starting it.

**Physical scope:** Uniform thickness copper sheet; the analytical comparison applies at fixed 300 K. Analytical R = rho L/(w t) = 0.084 ohm at 300 K; L=100 µm, w=10 µm, t=2 µm.

**Recorded computed preview:** 84 mΩ effective resistance; 4.762 mW electrical power. 360 nodes / 608 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

Optional command-line run of the current project:

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E1_straight.etl --mode electrical --output results/E1
```

</details>

<a id="e2"></a>
## E2 · Bent and tapered conductor

**Question:** See how a taper and a reentrant corner redistribute current.

**Starter · Conductors and current sharing · tutorial.** compact steady calculation.

[![Bent and tapered conductor: prepared geometry and current density on metal](example-gallery/E2_bent_taper.png)](example-gallery/E2_bent_taper.svg)

**Open and inspect:** choose **File → Ready-to-run examples → Conductors and current sharing → E2** for the original project. Review layers, contacts and operating conditions before editing.

**Starting setup:** **Original reference** · **DC electrical**. Original reference inputs are retained.

**Run and read:** validate (F6), generate the mesh (F7), then run (F5). In Results select `metal` and **Volumetric current density (mA/µm²)**, then **Fit layer**. Zoom into the inside bend, then enable Show vectors in Appearance to follow current around it.

**Look for:** Current bunches at the reentrant corner and redistributes through the taper.

**Change one thing:** Save a copy and use Edit layout to widen the neck. Does an averaged loading measure improve when the mesh is refined?

**Evidence:** [Maintained nominal measurements](example-science/nominal-reference.md) · [Applicability and verification](example-science.md). The retained image records **DC electrical**; its original inputs and hashes remain in the expandable preview record. It does not certify the currently selected scenario.

**Limits:** Ideal sharp corners have mesh-sensitive peak current density; refine before comparing peaks.

**Next:** [E26 · Dog-bone current-crowding coupon](extended-examples.md#e26) in [Conductors and current sharing](example-paths.md#conductors).

[Project (.etl)](../examples/E2_bent_taper.etl) · [GDSII](../examples/E2_bent_taper.gds) · [OASIS](../examples/E2_bent_taper.oas) · [Vector preview](example-gallery/E2_bent_taper.svg)

<details>
<summary>Recorded inputs, model limits and numerical details</summary>

**Recorded operating point:** source 0 V (ground); drain 0.1 A (current). Preview mode: **electrical**.

**Physical layers:** `metal` (copper, 2 µm).

**Saved sweep:** `terminals.drain.value` = `[0.01, 0.02, 0.04]` (SI values, electrothermal)

The saved sweep is a separate experiment; review the selected parameter, values and analysis in **Parameter sweep…** before starting it.

**Physical scope:** Ideal sharp corners have mesh-sensitive peak current density; refine before comparing peaks. Bent, tapered conductor; current crowding near the reentrant corner is mesh-sensitive.

**Recorded computed preview:** 68.04 mΩ effective resistance; 680.4 µW electrical power. 845 nodes / 1,523 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

**Historical preview diagnostics (superseded as current setup guidance):** Thermal boundary sink_metal extends outside its layer and is clipped.

These belong to the stored preview and may describe corrected clipping/overlap behavior. Use maintained evidence and new validation for the current project; do not reproduce old warnings or ignore a current blocker.

Optional command-line run of the current project:

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E2_bent_taper.etl --mode electrical --output results/E2
```

</details>

<a id="e3"></a>
## E3 · Unequal parallel routes

**Question:** Compare current sharing through unequal-width branches and common access buses.

**Starter · Conductors and current sharing · comparison.** compact steady calculation.

[![Unequal parallel routes: prepared geometry and current density on metal](example-gallery/E3_parallel.png)](example-gallery/E3_parallel.svg)

**Open and inspect:** choose **File → Ready-to-run examples → Conductors and current sharing → E3** for the original project. Review layers, contacts and operating conditions before editing.

**Starting setup:** **Original reference** · **DC electrical**. Original reference inputs are retained.

**Run and read:** validate (F6), generate the mesh (F7), then run (F5). In Results select `metal` and **Volumetric current density (mA/µm²)**, then **Fit layer**. Compare both branch widths and their common access buses before inspecting the current map.

**Look for:** The wider route carries more current, but shared access resistance also affects the division.

**Change one thing:** Change one branch in a project-owned layout copy. How much does current sharing change without changing total delivered current?

**Evidence:** [Maintained nominal measurements](example-science/nominal-reference.md) · [Applicability and verification](example-science.md). The retained image records **DC electrical**; its original inputs and hashes remain in the expandable preview record. It does not certify the currently selected scenario.

**Limits:** Distributed access resistance is included, so branch sharing is not a simple width-only ratio.

**Next:** [E28 · Three-terminal T-junction splitter](extended-examples.md#e28) in [Conductors and current sharing](example-paths.md#conductors).

[Project (.etl)](../examples/E3_parallel.etl) · [GDSII](../examples/E3_parallel.gds) · [OASIS](../examples/E3_parallel.oas) · [Vector preview](example-gallery/E3_parallel.svg)

<details>
<summary>Recorded inputs, model limits and numerical details</summary>

**Recorded operating point:** source 0 V (ground); drain 0.3 A (current). Preview mode: **electrical**.

**Physical layers:** `metal` (copper, 2 µm).

**Saved sweep:** `terminals.drain.value` = `[0.01, 0.02, 0.04]` (SI values, electrothermal)

The saved sweep is a separate experiment; review the selected parameter, values and analysis in **Parameter sweep…** before starting it.

**Physical scope:** Distributed access resistance is included, so branch sharing is not a simple width-only ratio. Two parallel routes with 10 and 20 µm widths, including distributed access resistance.

**Recorded computed preview:** 26.89 mΩ effective resistance; 2.42 mW electrical power. 1,152 nodes / 2,054 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

**Historical preview diagnostics (superseded as current setup guidance):** Thermal boundary sink_metal extends outside its layer and is clipped.

These belong to the stored preview and may describe corrected clipping/overlap behavior. Use maintained evidence and new validation for the current project; do not reproduce old warnings or ignore a current blocker.

Optional command-line run of the current project:

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E3_parallel.etl --mode electrical --output results/E3
```

</details>

<a id="e4"></a>
## E4 · Two-metal transfer

**Question:** Follow lateral transport through a nonrectangular second layer and a finite via.

**Intermediate · Multilayer routing and packages · tutorial.** multilayer steady calculation.

[![Two-metal transfer: prepared geometry and potential on m2](example-gallery/E4_multilayer.png)](example-gallery/E4_multilayer.svg)

**Open and inspect:** choose **File → Ready-to-run examples → Multilayer routing and packages → E4** for the original project. Review layers, contacts and operating conditions before editing.

**Starting setup:** **Original reference** · **DC electrical**. Original reference inputs are retained.

**Run and read:** validate (F6), generate the mesh (F7), then run (F5). In Results select `m2` and **Potential (V)**, then **Fit layer**. Switch Layer between m1 and m2; use Fit layer after each switch to follow the finite interlayer transfer.

**Look for:** Each sheet has its own potential distribution, joined through the declared via footprint.

**Change one thing:** Change contact refinement in Mesh & checks. Which voltage drops remain stable as that region is resolved?

**Evidence:** [Maintained nominal measurements](example-science/nominal-reference.md) · [Applicability and verification](example-science.md). The retained image records **DC electrical**; its original inputs and hashes remain in the expandable preview record. It does not certify the currently selected scenario.

**Limits:** Separate sheet potentials couple through a finite copper via; this is a 2.5D representation.

**Next:** [E5 · Hierarchical via array](foundation-examples.md#e5) in [Multilayer routing and packages](example-paths.md#routing).

[Project (.etl)](../examples/E4_multilayer.etl) · [GDSII](../examples/E4_multilayer.gds) · [OASIS](../examples/E4_multilayer.oas) · [Vector preview](example-gallery/E4_multilayer.svg)

<details>
<summary>Recorded inputs, model limits and numerical details</summary>

**Recorded operating point:** source 0 V (ground); drain 0.2 A (current). Preview mode: **electrical**.

**Physical layers:** `m1` (copper, 2 µm); `m2` (copper, 2 µm).

**Saved sweep:** `terminals.drain.value` = `[0.01, 0.02, 0.04]` (SI values, electrothermal)

The saved sweep is a separate experiment; review the selected parameter, values and analysis in **Parameter sweep…** before starting it.

**Physical scope:** Separate sheet potentials couple through a finite copper via; this is a 2.5D representation. Nonrectangular two-metal structure, finite copper via and separate layer potentials.

**Recorded computed preview:** 66.58 mΩ effective resistance; 2.663 mW electrical power. 910 nodes / 1,643 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

**Historical preview diagnostics (superseded as current setup guidance):** Thermal boundary sink_m1 extends outside its layer and is clipped. Thermal boundary sink_m2 extends outside its layer and is clipped.

These belong to the stored preview and may describe corrected clipping/overlap behavior. Use maintained evidence and new validation for the current project; do not reproduce old warnings or ignore a current blocker.

Optional command-line run of the current project:

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E4_multilayer.etl --mode electrical --output results/E4
```

</details>

<a id="e5"></a>
## E5 · Hierarchical via array

**Question:** Inspect repeated layout instances and unequal transfer through a 4 × 5 via array.

**Intermediate · Multilayer routing and packages · comparison.** multilayer steady calculation.

[![Hierarchical via array: prepared geometry and current density on m2](example-gallery/E5_via_array.png)](example-gallery/E5_via_array.svg)

**Open and inspect:** choose **File → Ready-to-run examples → Multilayer routing and packages → E5** for the original project. Review layers, contacts and operating conditions before editing.

**Starting setup:** **Original reference** · **DC electrical**. Original reference inputs are retained.

**Run and read:** validate (F6), generate the mesh (F7), then run (F5). In Results select `m2` and **Volumetric current density (mA/µm²)**, then **Fit layer**. Zoom into the repeated via footprints and compare the two sheet layers at the same operating point.

**Look for:** Transfer is distributed across the array; distance from the feed can make individual vias load unequally.

**Change one thing:** Would moving the physical feed change loading more than adding another row of vias? Keep planar metal density distinct from normal via current density.

**Evidence:** [Maintained nominal measurements](example-science/nominal-reference.md) · [Applicability and verification](example-science.md). The retained image records **DC electrical**; its original inputs and hashes remain in the expandable preview record. It does not certify the currently selected scenario.

**Limits:** Individual via footprints are resolved; through-thickness current is represented by interface links.

**Next:** [E12 · Three-level via ladder](foundation-examples.md#e12) in [Multilayer routing and packages](example-paths.md#routing).

[Project (.etl)](../examples/E5_via_array.etl) · [GDSII](../examples/E5_via_array.gds) · [OASIS](../examples/E5_via_array.oas) · [Vector preview](example-gallery/E5_via_array.svg)

<details>
<summary>Recorded inputs, model limits and numerical details</summary>

**Recorded operating point:** source 0 V (ground); drain 2 A (current). Preview mode: **electrical**.

**Physical layers:** `m1` (copper, 5 µm); `m2` (copper, 5 µm).

**Saved sweep:** `terminals.drain.value` = `[0.01, 0.02, 0.04]` (SI values, electrothermal)

The saved sweep is a separate experiment; review the selected parameter, values and analysis in **Parameter sweep…** before starting it.

**Physical scope:** Individual via footprints are resolved; through-thickness current is represented by interface links. Hierarchical 4 × 5 array of finite vias; nonuniform transfer current across a 200 µm pad.

**Recorded computed preview:** 3.132 mΩ effective resistance; 12.53 mW electrical power. 12,160 nodes / 23,918 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

Optional command-line run of the current project:

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E5_via_array.etl --mode electrical --output results/E5
```

</details>

<a id="e6"></a>
## E6 · Kelvin sheet and contact test

**Question:** Separate voltage sensing from current injection using zero-current floating electrodes.

**Intermediate · Contacts, sensing and extraction · tutorial.** compact steady calculation.

[![Kelvin sheet and contact test: prepared geometry and potential on film](example-gallery/E6_kelvin.png)](example-gallery/E6_kelvin.svg)

**Open and inspect:** choose **File → Ready-to-run examples → Contacts, sensing and extraction → E6** for the original project. Review layers, contacts and operating conditions before editing.

**Starting setup:** **Original reference** · **DC electrical**. Original reference inputs are retained.

**Run and read:** validate (F6), generate the mesh (F7), then run (F5). In Results select `film` and **Potential (V)**, then **Fit layer**. Inspect the sense_a and sense_b terminal values in Quantitative results as well as the sheet potential.

**Look for:** The floating sense electrodes have zero net current, while their equipotential footprints can locally perturb the film.

**Change one thing:** How does the sensed voltage differ from a two-terminal voltage that also includes the current contacts?

**Evidence:** [Maintained nominal measurements](example-science/nominal-reference.md) · [Applicability and verification](example-science.md). The retained image records **DC electrical**; its original inputs and hashes remain in the expandable preview record. It does not certify the currently selected scenario.

**Limits:** Floating sense electrodes are equipotential constraints and can perturb the sheet locally.

**Next:** [E27 · Finite-contact transfer-length coupon](extended-examples.md#e27) in [Contacts, sensing and extraction](example-paths.md#contacts).

[Project (.etl)](../examples/E6_kelvin.etl) · [GDSII](../examples/E6_kelvin.gds) · [OASIS](../examples/E6_kelvin.oas) · [Vector preview](example-gallery/E6_kelvin.svg)

<details>
<summary>Recorded inputs, model limits and numerical details</summary>

**Recorded operating point:** source 0 V (ground); drain 0.001 A (current); sense_a 0 (floating); sense_b 0 (floating). Preview mode: **electrical**.

**Physical layers:** `film` (silicon, 1 µm); `pads` (copper, 2 µm).

**Saved sweep:** `terminals.drain.value` = `[0.01, 0.02, 0.04]` (SI values, electrothermal)

The saved sweep is a separate experiment; review the selected parameter, values and analysis in **Parameter sweep…** before starting it.

**Physical scope:** Floating sense electrodes are equipotential constraints and can perturb the sheet locally. Four-terminal sheet/contact test; floating sense terminals draw exactly zero net current.

**Recorded computed preview:** 431.3 Ω effective resistance; 431.3 µW electrical power. 411 nodes / 726 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

**Historical preview diagnostics (superseded as current setup guidance):** Layer Pads contains 2 disconnected polygon islands; inspect connectivity. Thermal boundary sink_pads extends outside its layer and is clipped.

These belong to the stored preview and may describe corrected clipping/overlap behavior. Use maintained evidence and new validation for the current project; do not reproduce old warnings or ignore a current blocker.

Optional command-line run of the current project:

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E6_kelvin.etl --mode electrical --output results/E6
```

</details>

<a id="e7"></a>
## E7 · Interdigitated HEMT

**Question:** Explore local gate control, finite source/drain contacts, and thermal coupling.

**Intermediate · Semiconductor interconnects · legacy comparison.** multilayer steady calculation.

[![Interdigitated HEMT: prepared geometry and sheet current on channel](example-gallery/E7_hemt.png)](example-gallery/E7_hemt.svg)

**Open and inspect:** choose **File → Ready-to-run examples → Semiconductor interconnects → E7** for the original project. Review layers, contacts and operating conditions before editing.

**Starting setup:** **Original reference** · **DC electrical**. Original reference inputs are retained. Legacy ungated edge bypass is intentional; this is not the fully gated teaching reference.

**Run and read:** validate (F6), generate the mesh (F7), then run (F5). In Results select `channel` and **Sheet current density (mA/µm)**, then **Fit layer**. In Operating conditions, choose Electrical inputs / gate controls and inspect gate_control. Run a second gate bias and use History to compare.

**Look for:** The gated region changes conductance, while the documented ungated edges still provide a bypass path.

**Change one thing:** Compare the response with E16. Does every source-to-drain path cross a gate-controlled region?

**Evidence:** [Maintained nominal measurements](example-science/nominal-reference.md) · [Applicability and verification](example-science.md). The retained image records **DC electrical**; its original inputs and hashes remain in the expandable preview record. It does not certify the currently selected scenario.

**Limits:** Illustrative resistive 2DEG law, with ungated edge bypass; the insulated field plate has no electrostatic effect.

**Next:** [E8 · HEMT with resistive routing](foundation-examples.md#e8) in [Legacy HEMT routing comparison](example-paths.md#legacy-hemt).

[Project (.etl)](../examples/E7_hemt.etl) · [GDSII](../examples/E7_hemt.gds) · [OASIS](../examples/E7_hemt.oas) · [Vector preview](example-gallery/E7_hemt.svg)

<details>
<summary>Recorded inputs, model limits and numerical details</summary>

**Recorded operating point:** source 0 V (ground); drain 0.2 V (voltage); gate_bias 0 V (voltage); field_plate_bias 0 V (voltage); gate_control 0 V (gate). Preview mode: **electrical**.

**Physical layers:** `channel` (2deg, 0.01 µm); `source_metal` (copper, 2 µm); `drain_metal` (copper, 2 µm); `gate_metal` (copper, 0.15 µm); `field_plate` (copper, 0.3 µm); `substrate` (sic, 100 µm).

**Saved sweep:** `terminals.gate_control.value` = `[-5.0, -3.0, -1.0, 1.0]` (SI values, electrothermal)

The saved sweep is a separate experiment; review the selected parameter, values and analysis in **Parameter sweep…** before starting it.

**Physical scope:** Illustrative resistive 2DEG law, with ungated edge bypass; the insulated field plate has no electrostatic effect. Illustrative depletion-mode interdigitated HEMT. Local gate control only under gate mask; access channel uses the configured ungated carrier-density cap. Field plate is insulated and has no electrostatic effect. Ungated channel edges permit bypass current. Metal/channel to substrate thermal links use an effective 2e-7 m²K/W for omitted buffer/dielectric paths; these parallel heat paths require process calibration. Not a calibrated transistor model.

**Recorded computed preview:** 15.89 Ω effective resistance; 2.517 mW electrical power. 7,053 nodes / 13,807 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

**Historical preview diagnostics (superseded as current setup guidance):** Physical volumes of Source Metal and Gate Metal overlap in z and x–y. Verify stack positions and material assignment; electrical potentials remain independent. Interface heat_channel extends outside its two layers and is clipped to their common area. Interface heat_source_metal extends outside its two layers and is clipped to their common area. Interface heat_drain_metal extends outside its two layers and is clipped to their common area. Interface heat_gate_metal extends outside its two layers and is clipped to their common area. Interface heat_field_plate extends outside its two layers and is clipped to their common area.

These belong to the stored preview and may describe corrected clipping/overlap behavior. Use maintained evidence and new validation for the current project; do not reproduce old warnings or ignore a current blocker.

Optional command-line run of the current project:

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E7_hemt.etl --mode electrical --output results/E7
```

</details>

<a id="e8"></a>
## E8 · HEMT with resistive routing

**Question:** Compare thin access buses with the thicker routing in E7.

**Intermediate · Semiconductor interconnects · legacy comparison.** multilayer steady calculation.

[![HEMT with resistive routing: prepared geometry and current density on source_metal](example-gallery/E8_hemt_routing.png)](example-gallery/E8_hemt_routing.svg)

**Open and inspect:** choose **File → Ready-to-run examples → Semiconductor interconnects → E8** for the original project. Review layers, contacts and operating conditions before editing.

**Starting setup:** **Original reference** · **DC electrical**. Original reference inputs are retained. Legacy ungated edge bypass is intentional; this is not the fully gated teaching reference.

**Run and read:** validate (F6), generate the mesh (F7), then run (F5). In Results select `source_metal` and **Volumetric current density (mA/µm²)**, then **Fit layer**. Inspect the source_metal current map, then compare the layer thicknesses with E7 in Layers & materials.

**Look for:** The thinner access metal changes routing resistance and current crowding while retaining the same illustrative channel law.

**Change one thing:** Which part of the voltage drop comes from routing, and which from the channel? Compare at equal delivered current before calling a layout better.

**Evidence:** [Maintained nominal measurements](example-science/nominal-reference.md) · [Applicability and verification](example-science.md). The retained image records **DC electrical**; its original inputs and hashes remain in the expandable preview record. It does not certify the currently selected scenario.

**Limits:** Same effective channel law as E7; metal thickness changes routing resistance, not transistor electrostatics.

**Next:** compare saved runs and review the [Legacy HEMT routing comparison learning path](example-paths.md#legacy-hemt).

[Project (.etl)](../examples/E8_hemt_routing.etl) · [GDSII](../examples/E8_hemt_routing.gds) · [OASIS](../examples/E8_hemt_routing.oas) · [Vector preview](example-gallery/E8_hemt_routing.svg)

<details>
<summary>Recorded inputs, model limits and numerical details</summary>

**Recorded operating point:** source 0 V (ground); drain 0.2 V (voltage); gate_bias 0 V (voltage); field_plate_bias 0 V (voltage); gate_control 0 V (gate). Preview mode: **electrical**.

**Physical layers:** `channel` (2deg, 0.01 µm); `source_metal` (copper, 0.7 µm); `drain_metal` (copper, 0.7 µm); `gate_metal` (copper, 0.15 µm); `field_plate` (copper, 0.3 µm); `substrate` (sic, 100 µm).

**Saved sweep:** `terminals.gate_control.value` = `[-5.0, -3.0, -1.0, 1.0]` (SI values, electrothermal)

The saved sweep is a separate experiment; review the selected parameter, values and analysis in **Parameter sweep…** before starting it.

**Physical scope:** Same effective channel law as E7; metal thickness changes routing resistance, not transistor electrostatics. Illustrative depletion-mode interdigitated HEMT. Local gate control only under gate mask; access channel uses the configured ungated carrier-density cap. Field plate is insulated and has no electrostatic effect. Ungated channel edges permit bypass current. Metal/channel to substrate thermal links use an effective 2e-7 m²K/W for omitted buffer/dielectric paths; these parallel heat paths require process calibration. Not a calibrated transistor model.

**Recorded computed preview:** 15.99 Ω effective resistance; 2.501 mW electrical power. 7,095 nodes / 13,891 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

**Historical preview diagnostics (superseded as current setup guidance):** Physical volumes of Source Metal and Gate Metal overlap in z and x–y. Verify stack positions and material assignment; electrical potentials remain independent. Interface heat_channel extends outside its two layers and is clipped to their common area. Interface heat_source_metal extends outside its two layers and is clipped to their common area. Interface heat_drain_metal extends outside its two layers and is clipped to their common area. Interface heat_gate_metal extends outside its two layers and is clipped to their common area. Interface heat_field_plate extends outside its two layers and is clipped to their common area.

These belong to the stored preview and may describe corrected clipping/overlap behavior. Use maintained evidence and new validation for the current project; do not reproduce old warnings or ignore a current blocker.

Optional command-line run of the current project:

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E8_hemt_routing.etl --mode electrical --output results/E8
```

</details>

<a id="e9"></a>
## E9 · Conducting SiC sheet

**Question:** Connect carrier density and mobility to sheet conduction and contact loss.

**Intermediate · Semiconductor interconnects · tutorial.** compact steady calculation.

[![Conducting SiC sheet: prepared geometry and potential on sic](example-gallery/E9_sic.png)](example-gallery/E9_sic.svg)

**Open and inspect:** choose **File → Ready-to-run examples → Semiconductor interconnects → E9** for the original project. Review layers, contacts and operating conditions before editing.

**Starting setup:** **Original reference** · **DC electrical**. Original reference inputs are retained.

**Run and read:** validate (F6), generate the mesh (F7), then run (F5). In Results select `sic` and **Potential (V)**, then **Fit layer**. Inspect the sic layer's electrical model in Layers & materials, then run its fixed-temperature electrical calculation.

**Look for:** An effective doped-SiC resistive sheet with finite contacts; this example has no transistor gate or junction model.

**Change one thing:** How does changing mobility affect sheet resistance? For a distributed MOSFET on-state load, continue with E23 or E24.

**Evidence:** [Maintained nominal measurements](example-science/nominal-reference.md) · [Applicability and verification](example-science.md). The retained image records **DC electrical**; its original inputs and hashes remain in the expandable preview record. It does not certify the currently selected scenario.

**Limits:** Effective doped semiconductor resistor; no junction, depletion, or breakdown physics.

**Next:** [E39 · SiC on-state die with active islands](extended-examples.md#e39) in [SiC: resistor to on-state collection](example-paths.md#sic).

[Project (.etl)](../examples/E9_sic.etl) · [GDSII](../examples/E9_sic.gds) · [OASIS](../examples/E9_sic.oas) · [Vector preview](example-gallery/E9_sic.svg)

<details>
<summary>Recorded inputs, model limits and numerical details</summary>

**Recorded operating point:** source 0 V (ground); drain 1 V (voltage). Preview mode: **electrical**.

**Physical layers:** `sic` (sic, 2 µm); `pads` (copper, 2 µm).

**Saved sweep:** `terminals.drain.value` = `[0.01, 0.02, 0.04]` (SI values, electrothermal)

The saved sweep is a separate experiment; review the selected parameter, values and analysis in **Parameter sweep…** before starting it.

**Physical scope:** Effective doped semiconductor resistor; no junction, depletion, or breakdown physics. Effective n=10^18 cm^-3, mobility=400 cm²/Vs at 300 K; resistive sheet, no junction physics.

**Recorded computed preview:** 226.6 Ω effective resistance; 4.412 mW electrical power. 960 nodes / 1,788 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

**Historical preview diagnostics (superseded as current setup guidance):** Layer Pads contains 2 disconnected polygon islands; inspect connectivity. Thermal boundary sink_pads extends outside its layer and is clipped.

These belong to the stored preview and may describe corrected clipping/overlap behavior. Use maintained evidence and new validation for the current project; do not reproduce old warnings or ignore a current blocker.

Optional command-line run of the current project:

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E9_sic.etl --mode electrical --output results/E9
```

</details>

<a id="e10"></a>
## E10 · Metal–oxide–silicon stack

**Question:** Resolve lateral heat spreading and the resistance of a layered thermal path.

**Intermediate · Heat flow and thermal interaction · tutorial.** multilayer steady calculation.

[![Metal–oxide–silicon stack: prepared geometry and temperature on substrate](example-gallery/E10_thermal_stack.png)](example-gallery/E10_thermal_stack.svg)

**Open and inspect:** choose **File → Ready-to-run examples → Heat flow and thermal interaction → E10** for the original project. Review layers, contacts and operating conditions before editing.

**Starting setup:** **Original reference** · **Coupled DC electrothermal**. Original reference inputs are retained.

**Run and read:** validate (F6), generate the mesh (F7), then run (F5). In Results select `substrate` and **Temperature (K)**, then **Fit layer**. Switch Layer between the metal and substrate, then compare Temperature with Temperature rise when a reservoir reference is available.

**Look for:** Heat spreads laterally in the substrate while the configured stack and backside boundary set the thermal path.

**Change one thing:** Change the backside condition in Operating conditions. Which layer changes most, and why is it misleading to inspect only the metal?

**Evidence:** [Maintained nominal measurements](example-science/nominal-reference.md) · [Applicability and verification](example-science.md). The retained image records **Coupled DC electrothermal**; its original inputs and hashes remain in the expandable preview record. It does not certify the currently selected scenario.

**Limits:** Thickness-averaged layers; the bottom boundary includes half of the substrate thickness.

**Next:** [E11 · Serpentine thin-film heater](foundation-examples.md#e11) in [Heat flow and thermal interaction](example-paths.md#heat).

[Project (.etl)](../examples/E10_thermal_stack.etl) · [GDSII](../examples/E10_thermal_stack.gds) · [OASIS](../examples/E10_thermal_stack.oas) · [Vector preview](example-gallery/E10_thermal_stack.svg)

<details>
<summary>Recorded inputs, model limits and numerical details</summary>

**Recorded operating point:** source 0 V (ground); drain 0.4 A (current). Preview mode: **electrothermal**.

**Physical layers:** `metal` (copper, 2 µm); `oxide` (sio2, 1 µm); `substrate` (silicon, 50 µm).

**Saved sweep:** `terminals.drain.value` = `[0.01, 0.02, 0.04]` (SI values, electrothermal)

The saved sweep is a separate experiment; review the selected parameter, values and analysis in **Parameter sweep…** before starting it.

**Physical scope:** Thickness-averaged layers; the bottom boundary includes half of the substrate thickness. Thickness-averaged metal/oxide/silicon model. Bottom boundary includes half substrate thickness.

**Recorded computed preview:** 176 mΩ effective resistance; 28.16 mW electrical power; 312.2 K overall maximum temperature. 7,899 nodes / 15,456 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

**Historical preview diagnostics (superseded as current setup guidance):** Interface metal_oxide extends outside its two layers and is clipped to their common area. Thermal boundary top_air extends outside its layer and is clipped.

These belong to the stored preview and may describe corrected clipping/overlap behavior. Use maintained evidence and new validation for the current project; do not reproduce old warnings or ignore a current blocker.

Optional command-line run of the current project:

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E10_thermal_stack.etl --mode electrothermal --output results/E10
```

</details>

<a id="e11"></a>
## E11 · Serpentine thin-film heater

**Question:** Inspect corner crowding, temperature feedback, and heat spreading beyond a five-pass trace.

**Advanced · Heat flow and thermal interaction · comparison.** multilayer steady calculation.

[![Serpentine thin-film heater: prepared geometry and temperature on heater](example-gallery/E11_serpentine_heater.png)](example-gallery/E11_serpentine_heater.svg)

**Open and inspect:** choose **File → Ready-to-run examples → Heat flow and thermal interaction → E11** for the original project. Review layers, contacts and operating conditions before editing.

**Starting setup:** **Original reference** · **Coupled DC electrothermal**. Original reference inputs are retained.

**Run and read:** validate (F6), generate the mesh (F7), then run (F5). In Results select `heater` and **Temperature (K)**, then **Fit layer**. Inspect the heater temperature, then switch to the substrate and look beyond the meander footprint.

**Look for:** Bends concentrate electrical loading, while the effective interface and substrate spread the deposited heat.

**Change one thing:** Vary current with Parameter sweep… and compare temperature and resistance. Keep the configured prescribed heat input when reproducing the nominal project.

**Evidence:** [Maintained nominal measurements](example-science/nominal-reference.md) · [Applicability and verification](example-science.md) · [Thermal/scenario observations](thermal-example-scenarios.md). The retained image records **Coupled DC electrothermal**; its original inputs and hashes remain in the expandable preview record. It does not certify the currently selected scenario.

**Limits:** Illustrative platinum film on silicon, with an effective thermal interface; sharp-corner peaks need mesh refinement.

**Next:** [E33 · Thermal bridge heater](extended-examples.md#e33) in [Heat flow and thermal interaction](example-paths.md#heat).

[Project (.etl)](../examples/E11_serpentine_heater.etl) · [GDSII](../examples/E11_serpentine_heater.gds) · [OASIS](../examples/E11_serpentine_heater.oas) · [Vector preview](example-gallery/E11_serpentine_heater.svg)

<details>
<summary>Recorded inputs, model limits and numerical details</summary>

**Recorded operating point:** source 0 V (ground); drain 0.02 A (current); prescribed heat: background_heating 0.002 W. Preview mode: **electrothermal**.

**Physical layers:** `heater` (platinum, 0.25 µm); `substrate` (silicon, 50 µm).

**Saved sweep:** `terminals.drain.value` = `[0.01, 0.02, 0.03]` (SI values, electrothermal); `interfaces.heater_substrate.thermal_value` = `[1e-06, 2e-06, 4e-06]` (SI values, electrothermal)

The saved sweep is a separate experiment; review the selected parameter, values and analysis in **Parameter sweep…** before starting it.

**Physical scope:** Illustrative platinum film on silicon, with an effective thermal interface; sharp-corner peaks need mesh refinement. Five-pass 8 µm wide, 0.25 µm thick platinum serpentine, driven at 20 mA. Square turns demonstrate current crowding; corner peaks require a mesh study. The explicit 260 × 152 µm silicon substrate extends beyond the imported conductor. A 2e-6 m²K/W effective dielectric link and a 2e6 W/m²K backside sink are illustrative; the omitted dielectric has no resolved lateral heat flow. An independent 2 mW heater load also makes thermal-only mode meaningful. Steady-state thickness-averaged model; no transient heat capacity, radiation or calibrated thin-film transport.

**Recorded computed preview:** 52.74 Ω effective resistance; 21.1 mW electrical power; 306.3 K overall maximum temperature. 5,567 nodes / 10,856 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

**Historical preview diagnostics (superseded as current setup guidance):** Interface heater_substrate extends outside its two layers and is clipped to their common area. Heat source background_heating extends outside its layer and is clipped.

These belong to the stored preview and may describe corrected clipping/overlap behavior. Use maintained evidence and new validation for the current project; do not reproduce old warnings or ignore a current blocker.

Optional command-line run of the current project:

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E11_serpentine_heater.etl --mode electrothermal --output results/E11
```

</details>

<a id="e12"></a>
## E12 · Three-level via ladder

**Question:** Follow sequential vertical transfer through two hierarchical 3 × 3 via banks.

**Advanced · Multilayer routing and packages · comparison.** multilayer steady calculation.

[![Three-level via ladder: prepared geometry and current density on m2](example-gallery/E12_via_ladder.png)](example-gallery/E12_via_ladder.svg)

**Open and inspect:** choose **File → Ready-to-run examples → Multilayer routing and packages → E12** for the original project. Review layers, contacts and operating conditions before editing.

**Starting setup:** **Original reference** · **Coupled DC electrothermal**. Original reference inputs are retained.

**Run and read:** validate (F6), generate the mesh (F7), then run (F5). In Results select `m2` and **Volumetric current density (mA/µm²)**, then **Fit layer**. Switch Layer among m1, m2 and m3, using Fit layer to follow the two via banks and the middle riser.

**Look for:** Current transfers vertically twice; a map of one metal level does not describe loading in the others.

**Change one thing:** Draw a profile through a transfer region and compare it with a finer mesh. Can you separate a persistent bottleneck from a corner peak?

**Evidence:** [Maintained nominal measurements](example-science/nominal-reference.md) · [Applicability and verification](example-science.md). The retained image records **Coupled DC electrothermal**; its original inputs and hashes remain in the expandable preview record. It does not certify the currently selected scenario.

**Limits:** Three independent sheet potentials linked by finite copper vias; no full 3D via-sidewall field solution.

**Next:** [E30 · Multilevel via daisy chain](extended-examples.md#e30) in [Multilayer routing and packages](example-paths.md#routing).

[Project (.etl)](../examples/E12_via_ladder.etl) · [GDSII](../examples/E12_via_ladder.gds) · [OASIS](../examples/E12_via_ladder.oas) · [Vector preview](example-gallery/E12_via_ladder.svg)

<details>
<summary>Recorded inputs, model limits and numerical details</summary>

**Recorded operating point:** source 0 V (ground); drain 0.3 A (current); prescribed heat: riser_heating 0.003 W. Preview mode: **electrothermal**.

**Physical layers:** `m1` (copper, 1 µm); `m2` (copper, 1 µm); `m3` (copper, 1 µm); `substrate` (silicon, 40 µm).

**Saved sweep:** `terminals.drain.value` = `[0.1, 0.3, 0.5]` (SI values, electrothermal); `interfaces.via12.height` = `[1e-06, 2e-06, 4e-06]` (SI values, electrical)

The saved sweep is a separate experiment; review the selected parameter, values and analysis in **Parameter sweep…** before starting it.

**Physical scope:** Three independent sheet potentials linked by finite copper vias; no full 3D via-sidewall field solution. Three 1 µm copper levels form a U-shaped series route with two hierarchical 3 × 3 banks of 6 × 6 µm finite copper vias, 2 µm high. A 0.3 A terminal current must cross both banks; individual vias can carry different currents. Each metal has a separate potential and temperature. Effective 1e-6 m²K/W links to a larger silicon substrate approximate omitted dielectric paths; they are parallel thermal paths requiring process calibration. An independent 3 mW load is applied to the middle riser. Vias are distributed interlayer conductances, not resolved 3D solids. The via-height sweep changes electrical resistance only; thermal via conductance is independently fixed.

**Recorded computed preview:** 236.7 mΩ effective resistance; 21.3 mW electrical power; 303 K overall maximum temperature. 6,100 nodes / 11,914 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

**Historical preview diagnostics (superseded as current setup guidance):** Interface heat_m1 extends outside its two layers and is clipped to their common area. Interface heat_m2 extends outside its two layers and is clipped to their common area. Interface heat_m3 extends outside its two layers and is clipped to their common area.

These belong to the stored preview and may describe corrected clipping/overlap behavior. Use maintained evidence and new validation for the current project; do not reproduce old warnings or ignore a current blocker.

Optional command-line run of the current project:

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E12_via_ladder.etl --mode electrothermal --output results/E12
```

</details>

<a id="e13"></a>
## E13 · Perforated current spreader

**Question:** See current diversion around staggered slots and diamond holes in a conducting sheet.

**Advanced · Conductors and current sharing · comparison.** compact steady calculation.

[![Perforated current spreader: prepared geometry and current density on spreader](example-gallery/E13_perforated_spreader.png)](example-gallery/E13_perforated_spreader.svg)

**Open and inspect:** choose **File → Ready-to-run examples → Conductors and current sharing → E13** for the original project. Review layers, contacts and operating conditions before editing.

**Starting setup:** **Original reference** · **Coupled DC electrothermal**. Original reference inputs are retained.

**Run and read:** validate (F6), generate the mesh (F7), then run (F5). In Results select `spreader` and **Volumetric current density (mA/µm²)**, then **Fit layer**. Inspect the narrow ligaments around slots and diamond holes; preview the mesh to check that voids stay empty.

**Look for:** Current detours around genuine holes, concentrating in the remaining conducting paths.

**Change one thing:** Compare a physically averaged hotspot with the largest element value while refining near a hole. Which conclusion is stable?

**Evidence:** [Maintained nominal measurements](example-science/nominal-reference.md) · [Applicability and verification](example-science.md). The retained image records **Coupled DC electrothermal**; its original inputs and hashes remain in the expandable preview record. It does not certify the currently selected scenario.

**Limits:** True void topology is preserved in the mesh; ideal hole corners produce mesh-sensitive current peaks.

**Next:** [E32 · Parallel resistor array with resistive buses](extended-examples.md#e32) in [Conductors and current sharing](example-paths.md#conductors).

[Project (.etl)](../examples/E13_perforated_spreader.etl) · [GDSII](../examples/E13_perforated_spreader.gds) · [OASIS](../examples/E13_perforated_spreader.oas) · [Vector preview](example-gallery/E13_perforated_spreader.svg)

<details>
<summary>Recorded inputs, model limits and numerical details</summary>

**Recorded operating point:** source 0 V (ground); drain 0.75 A (current); prescribed heat: local_load 0.006 W. Preview mode: **electrothermal**.

**Physical layers:** `spreader` (copper, 2 µm); `substrate` (sic, 40 µm).

**Saved sweep:** `terminals.drain.value` = `[0.25, 0.5, 0.75]` (SI values, electrothermal); `heat_sources.local_load.value` = `[0.002, 0.006, 0.012]` (SI values, thermal)

The saved sweep is a separate experiment; review the selected parameter, values and analysis in **Parameter sweep…** before starting it.

**Physical scope:** True void topology is preserved in the mesh; ideal hole corners produce mesh-sensitive current peaks. A 240 × 140 µm, 2 µm thick copper sheet contains three staggered 10 × 92 µm slots and two diamond cutouts. These are actual holes in the conductor, not decorative masks. A 0.75 A edge drive diverts current around slot ends; a separate 6 mW localized load exercises thermal-only mode. The continuous, larger SiC substrate remains beneath the copper holes. Its effective interface resistance is 5e-7 m²K/W and backside sink is 2e6 W/m²K. Sharp-corner current peaks are mesh-dependent; use spatial averages or a refinement study. This steady-state sheet approximation omits 3D sidewall spreading and mechanical stress.

**Recorded computed preview:** 28.49 mΩ effective resistance; 16.02 mW electrical power; 301.9 K overall maximum temperature. 7,257 nodes / 14,204 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

**Historical preview diagnostics (superseded as current setup guidance):** KLayout normalized a nonsimple polygon/cutline on mask 1/0: Ring Self-intersection[0.00021 8e-05]. Inspect the resulting geometry; cutlines encoding holes are expected in GDS. Interface sheet_substrate extends outside its two layers and is clipped to their common area.

These belong to the stored preview and may describe corrected clipping/overlap behavior. Use maintained evidence and new validation for the current project; do not reproduce old warnings or ignore a current blocker.

Optional command-line run of the current project:

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E13_perforated_spreader.etl --mode electrothermal --output results/E13
```

</details>

<a id="e14"></a>
## E14 · Four-arm resistive bridge

**Question:** Read a differential bridge imbalance with separate current drive and floating midpoint probes.

**Advanced · Contacts, sensing and extraction · comparison.** multilayer steady calculation.

[![Four-arm resistive bridge: prepared geometry and potential on arm_br](example-gallery/E14_bridge_sensor.png)](example-gallery/E14_bridge_sensor.svg)

**Open and inspect:** choose **File → Ready-to-run examples → Contacts, sensing and extraction → E14** for the original project. Review layers, contacts and operating conditions before editing.

**Starting setup:** **Original reference** · **Coupled DC electrothermal**. Original reference inputs are retained.

**Run and read:** validate (F6), generate the mesh (F7), then run (F5). In Results select `arm_br` and **Potential (V)**, then **Fit layer**. Inspect arm_br and the floating midpoint terminal values in Quantitative results.

**Look for:** A differential bridge signal caused by unequal effective sheet resistances, with no mechanical strain calculation.

**Change one thing:** Change one arm's thickness in a copied setup. How do bridge imbalance and total power respond?

**Evidence:** [Maintained nominal measurements](example-science/nominal-reference.md) · [Applicability and verification](example-science.md). The retained image records **Coupled DC electrothermal**; its original inputs and hashes remain in the expandable preview record. It does not certify the currently selected scenario.

**Limits:** Four effective sheet resistors and metal pads; resistor mismatch represents a sensor response without a mechanical model.

**Next:** compare saved runs and review the [Contacts, sensing and extraction learning path](example-paths.md#contacts).

[Project (.etl)](../examples/E14_bridge_sensor.etl) · [GDSII](../examples/E14_bridge_sensor.gds) · [OASIS](../examples/E14_bridge_sensor.oas) · [Vector preview](example-gallery/E14_bridge_sensor.svg)

<details>
<summary>Recorded inputs, model limits and numerical details</summary>

**Recorded operating point:** source 0 V (ground); drain 0.1 V (voltage); sense_top 0 (floating); sense_bottom 0 (floating); prescribed heat: sensor_load 0.0002 W. Preview mode: **electrothermal**.

**Physical layers:** `arm_tl` (tan, 0.2 µm); `arm_tr` (tan, 0.2 µm); `arm_bl` (tan, 0.2 µm); `arm_br` (tan, 0.2 µm); `pads` (copper, 2 µm); `substrate` (silicon, 30 µm).

**Saved sweep:** `layers.arm_br.electrical.sheet_resistance.value` = `[80.0, 100.0, 120.0]` (SI values, electrical)

The saved sweep is a separate experiment; review the selected parameter, values and analysis in **Parameter sweep…** before starting it.

**Physical scope:** Four effective sheet resistors and metal pads; resistor mismatch represents a sensor response without a mechanical model. Four-arm Wheatstone bridge: three 100 Ω/□ TaN sheets and one 110 Ω/□ sensing arm. The 0.1 V drive uses finite-conductivity metal buses and ideal arm/pad interfaces. Floating midpoint electrodes draw zero net current. Read V(sense_top) − V(sense_bottom); effective resistance instead reports the source/drain drive resistance. Sweep the lower-right sheet resistance through 100 Ω/□ to reverse the differential output. A separate 0.2 mW prescribed sensor load also makes thermal-only operation meaningful; fixed sheet resistance does not model thermoresistive transduction. Synthetic 2.5D demonstration, not a calibrated sensor.

**Recorded computed preview:** 640.3 Ω effective resistance; 15.62 µW electrical power; 300.4 K overall maximum temperature. 1,717 nodes / 3,280 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

**Historical preview diagnostics (superseded as current setup guidance):** Layer Bridge electrodes contains 4 disconnected polygon islands; inspect connectivity. Interface contact_arm_tl extends outside its two layers and is clipped to their common area. Interface heat_arm_tl extends outside its two layers and is clipped to their common area. Interface contact_arm_tr extends outside its two layers and is clipped to their common area. Interface heat_arm_tr extends outside its two layers and is clipped to their common area. Interface contact_arm_bl extends outside its two layers and is clipped to their common area. Interface heat_arm_bl extends outside its two layers and is clipped to their common area. Interface contact_arm_br extends outside its two layers and is clipped to their common area. Interface heat_arm_br extends outside its two layers and is clipped to their common area. Interface heat_pads extends outside its two layers and is clipped to their common area. Heat source sensor_load extends outside its layer and is clipped.

These belong to the stored preview and may describe corrected clipping/overlap behavior. Use maintained evidence and new validation for the current project; do not reproduce old warnings or ignore a current blocker.

Optional command-line run of the current project:

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E14_bridge_sensor.etl --mode electrothermal --output results/E14
```

</details>

<a id="e15"></a>
## E15 · Dual-heater thermal crosstalk

**Question:** Inspect overlapping temperature fields from independently driven meanders on a shared substrate.

**Advanced · Heat flow and thermal interaction · comparison.** multilayer steady calculation.

[![Dual-heater thermal crosstalk: prepared geometry and temperature on substrate](example-gallery/E15_thermal_crosstalk.png)](example-gallery/E15_thermal_crosstalk.svg)

**Open and inspect:** choose **File → Ready-to-run examples → Heat flow and thermal interaction → E15** for the original project. Review layers, contacts and operating conditions before editing.

**Starting setup:** **Original reference** · **Coupled DC electrothermal**. Original reference inputs are retained.

**Run and read:** validate (F6), generate the mesh (F7), then run (F5). In Results select `substrate` and **Temperature (K)**, then **Fit layer**. Choose the shared substrate layer and inspect both heat-source neighborhoods on one temperature scale.

**Look for:** Overlapping heat-spreading fields and warming outside the driven meanders; narrow temperature ranges need distinct tick labels.

**Change one thing:** Change one configured heat input and compare saved runs at a common manual range. Does the unpowered neighborhood still warm?

**Evidence:** [Maintained nominal measurements](example-science/nominal-reference.md) · [Applicability and verification](example-science.md) · [Thermal/scenario observations](thermal-example-scenarios.md). The retained image records **Coupled DC electrothermal**; its original inputs and hashes remain in the expandable preview record. It does not certify the currently selected scenario.

**Limits:** Effective sheet-to-substrate heat transfer; prescribed sources also permit a meaningful independent thermal solve.

**Next:** [E34 · Interleaved heater and sensor](extended-examples.md#e34) in [Heat flow and thermal interaction](example-paths.md#heat).

[Project (.etl)](../examples/E15_thermal_crosstalk.etl) · [GDSII](../examples/E15_thermal_crosstalk.gds) · [OASIS](../examples/E15_thermal_crosstalk.oas) · [Vector preview](example-gallery/E15_thermal_crosstalk.svg)

<details>
<summary>Recorded inputs, model limits and numerical details</summary>

**Recorded operating point:** source_a 0 V (ground); drain_a 0.4 V (voltage); source_b 0 V (ground); drain_b 0.25 V (voltage); prescribed heat: load_a 0.0005 W, load_b 0.00015 W. Preview mode: **electrothermal**.

**Physical layers:** `heater_a` (tin, 0.5 µm); `heater_b` (tin, 0.5 µm); `substrate` (silicon, 30 µm).

**Saved sweep:** `heat_sources.load_a.value` = `[0.0, 0.0005, 0.002]` (SI values, thermal); `terminals.drain_a.value` = `[0.2, 0.4, 0.6]` (SI values, electrothermal)

The saved sweep is a separate experiment; review the selected parameter, values and analysis in **Parameter sweep…** before starting it.

**Physical scope:** Effective sheet-to-substrate heat transfer; prescribed sources also permit a meaningful independent thermal solve. Two electrically independent three-pass TiN heaters share a larger silicon thermal layer. A and B are biased at 0.4 V and 0.25 V; prescribed loads add 0.5 mW and 0.15 mW respectively. Electrical mode computes each powered net; reported resistance uses A only. Thermal-only mode uses prescribed loads without Joule heating, whereas electrothermal mode adds both Joule loads. Sweep load_a with B held fixed and inspect the rise of B temperature to observe thermal crosstalk. Effective 2e-6 m²K/W interfaces represent omitted isolation stacks; the 5e4 W/m²K backside sink and representative film properties need calibration. Synthetic steady-state 2.5D example.

**Recorded computed preview:** 154.4 Ω effective resistance; 1.441 mW electrical power; 305.2 K overall maximum temperature. 4,688 nodes / 9,124 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

**Historical preview diagnostics (superseded as current setup guidance):** Interface heat_a extends outside its two layers and is clipped to their common area. Interface heat_b extends outside its two layers and is clipped to their common area. Heat source load_a extends outside its layer and is clipped. Heat source load_b extends outside its layer and is clipped.

These belong to the stored preview and may describe corrected clipping/overlap behavior. Use maintained evidence and new validation for the current project; do not reproduce old warnings or ignore a current blocker.

Optional command-line run of the current project:

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E15_thermal_crosstalk.etl --mode electrothermal --output results/E15
```

</details>

<a id="e16"></a>
## E16 · Full-width gated HEMT array

**Question:** Contrast E7's edge bypass with gate control that spans every channel in a four-channel array.

**Advanced · Semiconductor interconnects · comparison.** multilayer steady calculation.

[![Full-width gated HEMT array: prepared geometry and sheet current on channel](example-gallery/E16_full_gate_hemt_array.png)](example-gallery/E16_full_gate_hemt_array.svg)

**Open and inspect:** choose **File → Ready-to-run examples → Semiconductor interconnects → E16** for the original project. Review layers, contacts and operating conditions before editing.

**Starting setup:** **Original reference** · **Coupled DC electrothermal**. Original reference inputs are retained.

**Run and read:** validate (F6), generate the mesh (F7), then run (F5). In Results select `channel` and **Sheet current density (mA/µm)**, then **Fit layer**. Inspect the four channel stripes, then vary gate_control through Operating conditions and compare the channel maps.

**Look for:** Every intended conducting path crosses a gate-controlled region, avoiding E7/E8's edge bypass.

**Change one thing:** Compare gate-bias response with E7, then move to E21 for long fingers. The numerical conductance floor is not an off-state leakage prediction.

**Evidence:** [Maintained nominal measurements](example-science/nominal-reference.md) · [Applicability and verification](example-science.md). The retained image records **Coupled DC electrothermal**; its original inputs and hashes remain in the expandable preview record. It does not certify the currently selected scenario.

**Limits:** Resistive 2DEG approximation with finite contacts; no velocity saturation, gate leakage, trapping, or electrostatic field-plate model.

**Next:** [E21 · Long-finger GaN HEMT array](power-examples.md#e21) in [Fully gated GaN: coupon to power die](example-paths.md#gan).

[Project (.etl)](../examples/E16_full_gate_hemt_array.etl) · [GDSII](../examples/E16_full_gate_hemt_array.gds) · [OASIS](../examples/E16_full_gate_hemt_array.oas) · [Vector preview](example-gallery/E16_full_gate_hemt_array.svg)

<details>
<summary>Recorded inputs, model limits and numerical details</summary>

**Recorded operating point:** source 0 V (ground); drain 0.1 V (voltage); gate_control 0 V (gate); prescribed heat: channel_load 0.0002 W. Preview mode: **electrothermal**.

**Physical layers:** `channel` (2deg, 0.01 µm); `source_metal` (copper, 1 µm); `drain_metal` (copper, 1 µm); `gate_metal` (gold, 0.2 µm); `substrate` (sic, 75 µm).

**Saved sweep:** `terminals.gate_control.value` = `[-5.0, -3.0, -1.0, 1.0]` (SI values, electrothermal)

The saved sweep is a separate experiment; review the selected parameter, values and analysis in **Parameter sweep…** before starting it.

**Physical scope:** Resistive 2DEG approximation with finite contacts; no velocity saturation, gate leakage, trapping, or electrostatic field-plate model. Four separated parallel GaN 2DEG stripes join shared source/drain metal buses through finite 1e-10 Ωm² contacts, with 0.1 V drain bias. One gate bar spans each complete channel width, so every source-to-drain channel path crosses a controlled region; there is no ungated edge bypass. The gold gate is electrically insulated and modeled as a thermal-only layer; its voltage enters the local analytical charge-control model through gate_control. No gate leakage, capacitance, electrostatic field solution, velocity saturation or calibrated transistor prediction is provided. A 1e-7 S sheet-conductance floor limits deep-off current and maintains numerical conditioning; the off-state current is not a leakage prediction. A 0.2 mW prescribed channel load supports thermal-only operation and adds to Joule heating in coupled mode. Effective thermal interfaces represent omitted buffer/dielectric paths.

**Recorded computed preview:** 687.5 Ω effective resistance; 14.55 µW electrical power; 300 K overall maximum temperature. 5,027 nodes / 9,798 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

**Historical preview diagnostics (superseded as current setup guidance):** Layer Four GaN 2DEG channels contains 4 disconnected polygon islands; inspect connectivity. Interface heat_channel extends outside its two layers and is clipped to their common area. Interface heat_source_metal extends outside its two layers and is clipped to their common area. Interface heat_drain_metal extends outside its two layers and is clipped to their common area. Interface heat_gate_metal extends outside its two layers and is clipped to their common area. Terminal gate_control extends outside its layer and is clipped. Heat source channel_load extends outside its layer and is clipped.

These belong to the stored preview and may describe corrected clipping/overlap behavior. Use maintained evidence and new validation for the current project; do not reproduce old warnings or ignore a current blocker.

Optional command-line run of the current project:

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E16_full_gate_hemt_array.etl --mode electrothermal --output results/E16
```

</details>

<a id="e17"></a>
## E17 · Pulsed heating and cooling

**Question:** Compare pulse heating and cooling with the uniform lumped thermal RC limit, then refine the time step.

**Starter · Pulsed operation · benchmark.** transient calculation.

[![Pulsed heating and cooling: prepared geometry and temperature on plate](example-gallery/E17_pulsed_thermal.png)](example-gallery/E17_pulsed_thermal.svg)

**Open and inspect:** choose **File → Ready-to-run examples → Pulsed operation → E17** for the original project. Review layers, contacts and operating conditions before editing.

**Starting setup:** **Original reference** · **Thermal transient**. Original reference inputs are retained.

**Run and read:** validate (F6), generate the mesh (F7), then run (F5). In Results select `plate` and **Temperature (K)**, then **Fit layer**. Open Time, waveform & initial-state settings… in Operating conditions, inspect Timing and Waveforms, then run and open Time history.

**Look for:** Uniform prescribed heating and cooling with thermal storage; the field can be nearly uniform even while the time history changes strongly.

**Change one thing:** Use Time-step convergence… to compare peak temperature and pulse energy. Does a shorter integration step resolve the pulse edges?

**Evidence:** [Maintained nominal measurements](example-science/nominal-reference.md) · [Applicability and verification](example-science.md). The retained image records **Thermal transient**; its original inputs and hashes remain in the expandable preview record. It does not certify the currently selected scenario.

**Limits:** Prescribed heat drives uniform copper thermal storage; electrical Joule heating is excluded. Pulse width means high plateau.

**Next:** [E18 · Pulsed electrothermal conductor](foundation-examples.md#e18) in [Pulsed operation](example-paths.md#pulses).

[Project (.etl)](../examples/E17_pulsed_thermal.etl) · [GDSII](../examples/E17_pulsed_thermal.gds) · [OASIS](../examples/E17_pulsed_thermal.oas) · [Vector preview](example-gallery/E17_pulsed_thermal.svg)

<details>
<summary>Recorded inputs, model limits and numerical details</summary>

**Configured baseline drives (waveforms applied during the run):** source 0 V (ground); drain 0 V (voltage); prescribed heat: heater 1e+06 W/m². Preview mode: **transient thermal**.

**Physical layers:** `plate` (copper, 10 µm).

**Saved sweep:** `transient.drives.0.waveform.high` = `[250000.0, 500000.0, 1000000.0]` (SI values, thermal)

The saved sweep is a separate experiment; review the selected parameter, values and analysis in **Parameter sweep…** before starting it.

**Physical scope:** Prescribed heat drives uniform copper thermal storage; electrical Joule heating is excluded. Pulse width means high plateau. Uniform prescribed heat pulses in a 100 × 100 × 10 µm copper plate, with h=1e5 W/(m² K). Thermal-only operation excludes Joule heating. The spatially uniform limit has tau=rho*cp*t/h and high-level steady rise=10 K, so the transient can be checked against a lumped RC. Pulse width is the 200 µs plateau, period 700 µs, and delay 100 µs. Reduce dt to check time accuracy.

**Transient setup:** stop 2.1 ms; maximum step 25 µs; saved output interval 50 µs. `heat_sources.heater.value`: 0 → 1e+06, delay 0.0001 s, rise 0 s, plateau 0.0002 s, fall 0 s, period 0.0007 s (target SI units). The field preview shows the final accepted time; use Time history to inspect the full run.

**Recorded computed preview:** 301.6 K maximum temperature at final time; 305 K peak over all time steps; 6 µJ integrated input heat. 302 nodes / 542 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

Optional command-line run of the current project:

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E17_pulsed_thermal.etl --mode thermal --output results/E17
```

</details>

<a id="e18"></a>
## E18 · Pulsed electrothermal conductor

**Question:** Follow current pulses, Joule energy, delayed temperature peaks and cooling with temperature feedback.

**Intermediate · Pulsed operation · tutorial.** transient calculation.

[![Pulsed electrothermal conductor: prepared geometry and temperature on metal](example-gallery/E18_pulsed_conductor.png)](example-gallery/E18_pulsed_conductor.svg)

**Open and inspect:** choose **File → Ready-to-run examples → Pulsed operation → E18** for the original project. Review layers, contacts and operating conditions before editing.

**Starting setup:** **Original reference** · **Electrothermal transient**. Original reference inputs are retained.

**Run and read:** validate (F6), generate the mesh (F7), then run (F5). In Results select `metal` and **Temperature (K)**, then **Fit layer**. Inspect the current waveform, run, then select retained times and use Time history to view temperature and Stored energy.

**Look for:** Joule heating during current pulses, thermal delay and cooling between pulses, with temperature-dependent electrical feedback.

**Change one thing:** Change the pulse plateau or cooling interval in Waveforms, then refine time. This models thermal dynamics, not electromagnetic switching losses.

**Evidence:** [Maintained nominal measurements](example-science/nominal-reference.md) · [Applicability and verification](example-science.md) · [Thermal/scenario observations](thermal-example-scenarios.md). The retained image records **Electrothermal transient**; its original inputs and hashes remain in the expandable preview record. It does not certify the currently selected scenario.

**Limits:** Backward Euler thermal storage with quasi-static electrical feedback; no electrical capacitance, inductance or switching losses.

**Next:** [E19 · Multilayer pulse and thermal crosstalk](foundation-examples.md#e19) in [Pulsed operation](example-paths.md#pulses).

[Project (.etl)](../examples/E18_pulsed_conductor.etl) · [GDSII](../examples/E18_pulsed_conductor.gds) · [OASIS](../examples/E18_pulsed_conductor.oas) · [Vector preview](example-gallery/E18_pulsed_conductor.svg)

<details>
<summary>Recorded inputs, model limits and numerical details</summary>

**Configured baseline drives (waveforms applied during the run):** source 0 V (ground); drain 0.25 A (current). Preview mode: **transient electrothermal**.

**Physical layers:** `metal` (copper, 2 µm).

**Saved sweep:** `transient.drives.0.waveform.high` = `[0.05, 0.15, 0.25]` (SI values, electrothermal)

The saved sweep is a separate experiment; review the selected parameter, values and analysis in **Parameter sweep…** before starting it.

**Physical scope:** Backward Euler thermal storage with quasi-static electrical feedback; no electrical capacitance, inductance or switching losses. Pulsed 100 × 10 × 2 µm copper conductor with quasi-static current and temperature-dependent resistivity. The 0.25 A pulse has 10 µs rise, 60 µs high plateau and 10 µs fall, repeated every 200 µs after a 25 µs delay. Finite edge times need time-step refinement. Heat capacity and cooling produce delayed thermal response; electrical capacitance and inductance are not included.

**Transient setup:** stop 400 µs; maximum step 5 µs; saved output interval 10 µs. `terminals.drain.value`: 0 → 0.25, delay 2.5e-05 s, rise 1e-05 s, plateau 6e-05 s, fall 1e-05 s, period 0.0002 s (target SI units). The field preview shows the final accepted time; use Time history to inspect the full run.

**Recorded computed preview:** 0 W electrical power; 301.4 K maximum temperature at final time; 323.6 K peak over all time steps; 752.9 nJ integrated input heat. 360 nodes / 608 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

Optional command-line run of the current project:

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E18_pulsed_conductor.etl --mode electrothermal --output results/E18
```

</details>

<a id="e19"></a>
## E19 · Multilayer pulse and thermal crosstalk

**Question:** Compare heater, substrate and unpowered receiver histories to locate the delayed crosstalk peak.

**Advanced · Pulsed operation · comparison.** multilayer transient calculation.

[![Multilayer pulse and thermal crosstalk: prepared geometry and temperature on substrate](example-gallery/E19_pulsed_multilayer.png)](example-gallery/E19_pulsed_multilayer.svg)

**Open and inspect:** choose **File → Ready-to-run examples → Pulsed operation → E19** for the original project. Review layers, contacts and operating conditions before editing.

**Starting setup:** **Original reference** · **Thermal transient**. Original reference inputs are retained.

**Run and read:** validate (F6), generate the mesh (F7), then run (F5). In Results select `substrate` and **Temperature (K)**, then **Fit layer**. Compare heater, substrate and unpowered receiver layers, then inspect their Time history scopes.

**Look for:** A delayed receiver temperature peak after a prescribed heat pulse transfers through the shared structure.

**Change one thing:** Change an interface thermal parameter and compare the receiver's peak time as well as its maximum temperature.

**Evidence:** [Maintained nominal measurements](example-science/nominal-reference.md) · [Applicability and verification](example-science.md). The retained image records **Thermal transient**; its original inputs and hashes remain in the expandable preview record. It does not certify the currently selected scenario.

**Limits:** A prescribed single heat pulse transfers through finite interfaces and a shared silicon sheet; no electrical drive or through-thickness diffusion.

**Next:** [E36 · Pulsed constriction and cooling](extended-examples.md#e36) in [Pulsed operation](example-paths.md#pulses).

[Project (.etl)](../examples/E19_pulsed_multilayer.etl) · [GDSII](../examples/E19_pulsed_multilayer.gds) · [OASIS](../examples/E19_pulsed_multilayer.oas) · [Vector preview](example-gallery/E19_pulsed_multilayer.svg)

<details>
<summary>Recorded inputs, model limits and numerical details</summary>

**Configured baseline drives (waveforms applied during the run):** ; prescribed heat: pulse_heat 0.01 W. Preview mode: **transient thermal**.

**Physical layers:** `heater` (copper, 5 µm); `receiver` (copper, 10 µm); `substrate` (silicon, 20 µm).

**Saved sweep:** `transient.drives.0.waveform.high` = `[0.0025, 0.005, 0.01]` (SI values, thermal)

The saved sweep is a separate experiment; review the selected parameter, values and analysis in **Parameter sweep…** before starting it.

**Physical scope:** A prescribed single heat pulse transfers through finite interfaces and a shared silicon sheet; no electrical drive or through-thickness diffusion. A 10 mW, 100 µs single pulse heats a 30 × 30 × 5 µm copper island. An unpowered 30 × 30 × 10 µm copper receiver sits 20 µm away on the same 20 µm silicon substrate. Finite interface conductances create separate layer temperatures; receiver heating and its delayed peak arise entirely from thermal crosstalk. Inspect heater, substrate and receiver histories. Prescribed heat only; no electrical solve. Input energy is 1 µJ. Thickness-averaged sheets and representative materials require process calibration.

**Transient setup:** stop 1 ms; maximum step 10 µs; saved output interval 10 µs. `heat_sources.pulse_heat.value`: 0 → 0.01, delay 5e-05 s, rise 0 s, plateau 0.0001 s, fall 0 s, period 0 s (target SI units). The field preview shows the final accepted time; use Time history to inspect the full run.

**Recorded computed preview:** 301.4 K maximum temperature at final time; 337.7 K peak over all time steps; 1 µJ integrated input heat. 977 nodes / 1,838 triangles; converged. [Full metrics and hashes](example-gallery/manifest.json).

**Historical preview diagnostics (superseded as current setup guidance):** Interface heater_substrate extends outside its two layers and is clipped to their common area. Interface receiver_substrate extends outside its two layers and is clipped to their common area. Heat source pulse_heat extends outside its layer and is clipped.

These belong to the stored preview and may describe corrected clipping/overlap behavior. Use maintained evidence and new validation for the current project; do not reproduce old warnings or ignore a current blocker.

Optional command-line run of the current project:

```powershell
.\.venv\Scripts\python.exe -m etlayout solve examples/E19_pulsed_multilayer.etl --mode thermal --output results/E19
```

</details>

## Maintaining this guide

The marked generated region is maintained by `scripts/build_example_gallery.py`; contributor guidance outside its markers is preserved. Rebuild Markdown from the existing manifest without solving or altering images:

```powershell
.\.venv\Scripts\python.exe scripts/build_example_gallery.py --markdown-only --output docs/example-gallery --markdown docs/foundation-examples.md
```

A deliberate numerical/image refresh is a separate operation. The result cache requires matching physics, solver version and source fingerprint; `--no-solve` produces explicitly labeled geometry-only previews. Keep original manifests with their images, and update evidence only from actual calculations.
<!-- END GENERATED EXAMPLE CONTENT -->
