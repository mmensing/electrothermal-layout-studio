<!-- BEGIN GENERATED EXAMPLE CONTENT -->
# Choose an example

The library contains 40 runnable reference examples, from a straight strip to long-finger GaN arrays, SiC on-state collection and package interconnects. IDs are permanent references, not a difficulty order. Choose a physical question, then follow its GUI walkthrough.

Start with the [learning paths](example-paths.md) or **File → Browse example library…**. The browser separates **Size preset**, **Routing variant**, **Operating scenario** and **Thermal boundary** where supported; opening never starts a calculation.

## Open → inspect → run → compare

1. Open an example in the browser, or use **File → Ready-to-run examples** for the original reference. Use **Save project as…** for your copy.
2. Inspect **Layout & region → Layers & materials → Contacts & connections → Operating conditions**. Check the active scenario, drive, named reservoir and independent heat sources.
3. Validate (F6), mesh (F7), then run (F5). Read the Issues drawer and keep resource limits enabled.
4. Read Results by layer and quantity; use **Fit layer**, **Metrics & loss budget** and **Compare saved runs…**. Change one declared input, keeping delivered service equal when comparing routes.

[Maintained nominal measurements](example-science/nominal-reference.md) and [scientific scope](example-science.md) describe tested configurations. These single-grid observations are not blanket mesh verification or physical calibration. [Historical field previews](foundation-examples.md) keep their original result hashes; older diagnostics are not current setup instructions.

## Conductors and current sharing

| Example and walkthrough | Question | Recommended start | Analysis | Workload |
| --- | --- | --- | --- | --- |
| <a id="e1"></a>[E1 · Straight conductor](foundation-examples.md#e1) | Compare the solved resistance with the uniform-strip analytical limit. | Original reference | DC electrical | compact steady calculation |
| <a id="e2"></a>[E2 · Bent and tapered conductor](foundation-examples.md#e2) | See how a taper and a reentrant corner redistribute current. | Original reference | DC electrical | compact steady calculation |
| <a id="e3"></a>[E3 · Unequal parallel routes](foundation-examples.md#e3) | Compare current sharing through unequal-width branches and common access buses. | Original reference | DC electrical | compact steady calculation |
| <a id="e13"></a>[E13 · Perforated current spreader](foundation-examples.md#e13) | See current diversion around staggered slots and diamond holes in a conducting sheet. | Original reference | Coupled DC electrothermal | compact steady calculation |
| <a id="e25"></a>[E25 · Stepped-width series reference](extended-examples.md#e25) | Compare series resistance with access spreading at width changes. | Original reference | DC electrical | compact steady calculation |
| <a id="e26"></a>[E26 · Dog-bone current-crowding coupon](extended-examples.md#e26) | Separate a physically averaged neck loading from a mesh-sensitive corner maximum. | Original reference | Coupled DC electrothermal | compact steady calculation |
| <a id="e28"></a>[E28 · Three-terminal T-junction splitter](extended-examples.md#e28) | Track signed terminal currents through a branched route with two return landings. | Original reference | DC electrical | compact steady calculation |
| <a id="e29"></a>[E29 · Annular current-spreading bus](extended-examples.md#e29) | Follow current around a protected central opening and compare the two annular paths. | Original reference | Coupled DC electrothermal | compact steady calculation |
| <a id="e32"></a>[E32 · Parallel resistor array with resistive buses](extended-examples.md#e32) | Measure how finite access buses change sharing among repeated resistive strips. | Original reference | Coupled DC electrothermal | multilayer steady calculation |

## Contacts, sensing and extraction

| Example and walkthrough | Question | Recommended start | Analysis | Workload |
| --- | --- | --- | --- | --- |
| <a id="e6"></a>[E6 · Kelvin sheet and contact test](foundation-examples.md#e6) | Separate voltage sensing from current injection using zero-current floating electrodes. | Original reference | DC electrical | compact steady calculation |
| <a id="e14"></a>[E14 · Four-arm resistive bridge](foundation-examples.md#e14) | Read a differential bridge imbalance with separate current drive and floating midpoint probes. | Original reference | Coupled DC electrothermal | multilayer steady calculation |
| <a id="e27"></a>[E27 · Finite-contact transfer-length coupon](extended-examples.md#e27) | Compare contact drop and passive Kelvin voltage with the contact transfer length. | Original reference | DC electrical | compact steady calculation |
| <a id="e31"></a>[E31 · Tapped resistive ladder](extended-examples.md#e31) | Read passive midpoint voltages and compare the ladder with its analytical resistance network. | Original reference | DC electrical | compact steady calculation |

## Multilayer routing and packages

| Example and walkthrough | Question | Recommended start | Analysis | Workload |
| --- | --- | --- | --- | --- |
| <a id="e4"></a>[E4 · Two-metal transfer](foundation-examples.md#e4) | Follow lateral transport through a nonrectangular second layer and a finite via. | Original reference | DC electrical | multilayer steady calculation |
| <a id="e5"></a>[E5 · Hierarchical via array](foundation-examples.md#e5) | Inspect repeated layout instances and unequal transfer through a 4 × 5 via array. | Original reference | DC electrical | multilayer steady calculation |
| <a id="e12"></a>[E12 · Three-level via ladder](foundation-examples.md#e12) | Follow sequential vertical transfer through two hierarchical 3 × 3 via banks. | Original reference | Coupled DC electrothermal | multilayer steady calculation |
| <a id="e20"></a>[E20 · High-current multilayer RDL](power-examples.md#e20) | Compare branching buses, individual via loading and localized single/distributed feeds. | Original reference | Coupled DC electrothermal | engineering benchmark; review resource limits |
| <a id="e30"></a>[E30 · Multilevel via daisy chain](extended-examples.md#e30) | Follow repeated serial transfers through physical via banks and inspect their loss contributions. | Original reference | Coupled DC electrothermal | multilayer steady calculation |
| <a id="e40"></a>[E40 · Three-level package interposer](extended-examples.md#e40) | Trace localized package landings through millimeter buses and hierarchical via banks. | Original reference | Coupled DC electrothermal | engineering benchmark; review resource limits |

## Heat flow and thermal interaction

| Example and walkthrough | Question | Recommended start | Analysis | Workload |
| --- | --- | --- | --- | --- |
| <a id="e10"></a>[E10 · Metal–oxide–silicon stack](foundation-examples.md#e10) | Resolve lateral heat spreading and the resistance of a layered thermal path. | Original reference | Coupled DC electrothermal | multilayer steady calculation |
| <a id="e11"></a>[E11 · Serpentine thin-film heater](foundation-examples.md#e11) | Inspect corner crowding, temperature feedback, and heat spreading beyond a five-pass trace. | Original reference | Coupled DC electrothermal | multilayer steady calculation |
| <a id="e15"></a>[E15 · Dual-heater thermal crosstalk](foundation-examples.md#e15) | Inspect overlapping temperature fields from independently driven meanders on a shared substrate. | Original reference | Coupled DC electrothermal | multilayer steady calculation |
| <a id="e33"></a>[E33 · Thermal bridge heater](extended-examples.md#e33) | Compare lateral heat escape through anchors with weak distributed thermal attachment. | Original reference | Coupled DC electrothermal | multilayer steady calculation |
| <a id="e34"></a>[E34 · Interleaved heater and sensor](extended-examples.md#e34) | Separate the driven heater, weakly biased resistive sensor and common substrate temperatures. | Original reference | Coupled DC electrothermal | multilayer steady calculation |
| <a id="e35"></a>[E35 · Localized backside die attachment](extended-examples.md#e35) | Observe lateral spreading when the thermal reservoir touches only a selected backside patch. | Original reference | Coupled DC electrothermal | multilayer steady calculation |

## Pulsed operation

| Example and walkthrough | Question | Recommended start | Analysis | Workload |
| --- | --- | --- | --- | --- |
| <a id="e17"></a>[E17 · Pulsed heating and cooling](foundation-examples.md#e17) | Compare pulse heating and cooling with the uniform lumped thermal RC limit, then refine the time step. | Original reference | Thermal transient | transient calculation |
| <a id="e18"></a>[E18 · Pulsed electrothermal conductor](foundation-examples.md#e18) | Follow current pulses, Joule energy, delayed temperature peaks and cooling with temperature feedback. | Original reference | Electrothermal transient | transient calculation |
| <a id="e19"></a>[E19 · Multilayer pulse and thermal crosstalk](foundation-examples.md#e19) | Compare heater, substrate and unpowered receiver histories to locate the delayed crosstalk peak. | Original reference | Thermal transient | multilayer transient calculation |
| <a id="e36"></a>[E36 · Pulsed constriction and cooling](extended-examples.md#e36) | Follow transient neck heating and temperature-dependent resistance over a current pulse train. | Original reference | Electrothermal transient | transient calculation |

## Semiconductor interconnects

| Example and walkthrough | Question | Recommended start | Analysis | Workload |
| --- | --- | --- | --- | --- |
| <a id="e7"></a>[E7 · Interdigitated HEMT](foundation-examples.md#e7) | Explore local gate control, finite source/drain contacts, and thermal coupling. | Original reference | DC electrical | multilayer steady calculation |
| <a id="e8"></a>[E8 · HEMT with resistive routing](foundation-examples.md#e8) | Compare thin access buses with the thicker routing in E7. | Original reference | DC electrical | multilayer steady calculation |
| <a id="e9"></a>[E9 · Conducting SiC sheet](foundation-examples.md#e9) | Connect carrier density and mobility to sheet conduction and contact loss. | Original reference | DC electrical | compact steady calculation |
| <a id="e16"></a>[E16 · Full-width gated HEMT array](foundation-examples.md#e16) | Contrast E7's edge bypass with gate control that spans every channel in a four-channel array. | Original reference | Coupled DC electrothermal | multilayer steady calculation |
| <a id="e21"></a>[E21 · Long-finger GaN HEMT array](power-examples.md#e21) | Resolve coplanar opposing source/drain combs and compare equal-current finger feeds through upper redistribution. | Original reference | Coupled DC electrothermal | engineering benchmark; review resource limits |
| <a id="e22"></a>[E22 · Multi-bank GaN power die](power-examples.md#e22) | Compare bank sharing, source-potential spread and substrate thermal interaction through shared redistribution. | Original reference | Coupled DC electrothermal | engineering benchmark; review resource limits |
| <a id="e23"></a>[E23 · SiC MOSFET on-state interconnect · stripes](power-examples.md#e23) | Separate distributed on-state device loss from source metal, vias and external feed losses. | Original reference | Coupled DC electrothermal | engineering benchmark; review resource limits |
| <a id="e24"></a>[E24 · SiC MOSFET on-state interconnect · macrocells](power-examples.md#e24) | Compare bond, clip and bump footprints at fixed active area and delivered current with passive Kelvin sensing. | Original reference | Coupled DC electrothermal | engineering benchmark; review resource limits |
| <a id="e37"></a>[E37 · Fully gated HEMT contact coupon](extended-examples.md#e37) | Inspect a single fully controlled channel with finite source and drain contacts. | Voltage-controlled GaN coupon | Coupled DC electrothermal | multilayer steady calculation |
| <a id="e38"></a>[E38 · Independently driven GaN banks](extended-examples.md#e38) | Compare separately driven gated banks through their shared source and thermal environment. | Original reference | Coupled DC electrothermal | multilayer steady calculation |
| <a id="e39"></a>[E39 · SiC on-state die with active islands](extended-examples.md#e39) | Inspect area-normalized vertical current sharing between protected active islands and finite collection straps. | Lower-current SiC reference (2 A) · original is stress | Coupled DC electrothermal | engineering benchmark; review resource limits |

## From field pictures to useful comparisons

The [foundational walkthroughs](foundation-examples.md), [power-device guide](power-examples.md) and [additional walkthroughs](extended-examples.md) share the same open–inspect–run–read–change-one flow. Each links to scientific evidence and states the model's limits.

E7/E8 intentionally include ungated edge bypass; use E37/E16 to learn a fully gated channel. E9 is a doped SiC resistor; E23/E24/E39 are SiC MOSFET **on-state interconnect models**. The browser recommends E39's lower-current scenario while its original ETL remains a 10 A stress case.

Two separate [bounded power-routing demonstrations](POWER_ROUTING.md) add feed-generation and equal-service optimization workflows. Their source projects remain under `examples/power_routing/`.

**Cost labels describe setup complexity, not measured runtimes.** The browser shows configured element/DOF/memory budgets. Size presets and high-current requests do not certify a feasible solve; review the [measured power-example limits](power-examples.md) before running larger cases.

ETL downloads embed the configured project and layout. GDSII/OASIS contain geometry only. All existing paths and example IDs are retained.
<!-- END GENERATED EXAMPLE CONTENT -->

See the [scientific evidence audit](example-science.md) for applicability levels, exact nominal measurements, model limits and reproduction commands.

## Compare electrical operating scenarios

[Meaningful Joule thermal demonstrations](thermal-example-scenarios.md) add
optional editable scenarios through **File → Choose example operating scenario…**.
They keep the E1–E40 reference inputs available and distinguish failed engineering
limits from numerical convergence. The guide includes actual maps, losses,
pulse peaks and mesh/time sensitivity observations.
