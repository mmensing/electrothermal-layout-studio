# Six learning paths through the examples

[All forty examples](examples.md) · [Portable projects](../examples/README.md) · [Documentation](README.md)

Example numbers are stable identifiers, not a difficulty sequence. The same
question can connect an early analytical coupon, a newer teaching example and a
larger engineering family. Follow a path below without running every case.

## Open, inspect, run and compare

1. Choose **File → Browse example library…**, search by ID or question, and review
   the subject, level, model scope and available choices. Direct reference
   shortcuts remain under **File → Ready-to-run examples**.
2. Select the scenario named below deliberately. **Original reference** preserves
   historical inputs; a recommended tutorial scenario does not replace it. Use
   **Save project as…** for your own copy. Opening never starts a calculation.
3. Review **Analysis type**, layers, contacts and **Operating conditions**. Earlier
   simple cases use direct terminals; later cases use physical ports with finite
   landing/feed resistance. Inspect the drive rather than applying it twice.
4. Use **Mesh & checks** to validate and generate the mesh, then **Run**. Read the
   Issues drawer. A known disconnected-island notice still requires checking the
   declared contacts/vias that connect those islands.
5. At **Results**, choose the stated layer/quantity, then inspect numerical metrics
   and loss accounting. Use **Studies → Run configured study…** for an available
   named study; review its actual mode and values before starting it.

Keep three choices separate: **size preset** changes the family scale and declared
operating point; **routing variant** changes supported feed/collection geometry;
**operating scenario** selects a named experiment. Only available choices are
shown. E20–E24 provide size/routing families and a **Thermal boundary** selector;
not every example supports them. Named scenarios keep their configured choices
separate from family generation. The browser recommends the E37 voltage-controlled
and E39 lower-current scenarios; direct reference shortcuts keep original inputs.
Generated alternatives need their own mesh and calculation. Large/stress cases
are opt-in, and successful generation does not establish simulation validity.

<a id="conductors"></a>

## Conductors and current sharing

**Path:** [E1](foundation-examples.md#e1) → [E2/E3](foundation-examples.md#e2) →
[E25/E26](extended-examples.md#e25) → [E28/E29/E32](extended-examples.md#e28).

Start with **E1 · Original reference**, **DC electrical**. Inspect `metal →
Potential`, then current density and resistance. The analytical strip gives an
independent reference before bends, branch access and finite landings complicate
current distribution. E25 adds width steps; E26 supplies a fixed physical neck
measurement; E28 exposes signed multi-terminal currents.

**Compare one change:** use E25's fixed-temperature current study to distinguish
constant resistance from quadratic Joule power. Do not transfer that scaling rule
to self-heated or nonlinear devices. At corners, compare integrated current and a
fixed physical average before interpreting a raw point peak.

<a id="contacts"></a>

## Contacts, sensing and extraction

**Path:** [E6](foundation-examples.md#e6) → [E27](extended-examples.md#e27) →
[E27 TLM study](contact-extraction.md); [E14](foundation-examples.md#e14) and
[E31](extended-examples.md#e31) extend differential/tapped sensing.

Open **E27 · Original reference**, **DC electrical**. Inspect `film → Potential`,
the passive sense voltages and the Contacts records. Separate injection-contact
drop from the sensed sheet drop. E6's floating equipotential sense electrodes and
later passive observations have different physical meanings; a sense electrode
can perturb its local sheet.

**Compare one change:** run **Studies → Finite-contact TLM study (E27)…** in a new
output folder. Read the fitted sheet/contact quantities and finite-length
reference alongside the individual solved coupons. Fit scatter is not physical
calibration or a complete discretization-error estimate.

<a id="routing"></a>

## Multilayer routing and packages

**Path:** [E4](foundation-examples.md#e4) → [E5](foundation-examples.md#e5) →
[E12](foundation-examples.md#e12)/[E30](extended-examples.md#e30) →
[E20](power-examples.md#e20)/[E40](extended-examples.md#e40).

Trace the route in **Contacts & connections**, then inspect each metal's
potential/current field and the individual via transfers. Projected overlap alone
does not create an electrical connection. E12 includes a separate prescribed heat
source; E30/E40 offer Joule-heated routes with finite external feeds.

**Compare one change:** use E40's configured copper-thickness study at fixed
service. Separate internal package loss from external feed loss in **Results →
Device and fixture observations…**. For a routing search, continue with the
[companion experiments](../examples/README.md#companion-routing-experiments);
retain failed candidates and mesh/export-reimport evidence before ranking them.

<a id="heat"></a>

## Heat flow and thermal interaction

**Path:** [E10](foundation-examples.md#e10) → [E11/E15](foundation-examples.md#e11) →
[E33/E34/E35](extended-examples.md#e33).

Begin with E10 and compare metal, oxide and substrate temperatures on a common
range. Then choose **E11 · Joule thermal demonstration** for self-heating without
its original independent prescribed load. **Separate Joule heating** removes that
load while keeping the original electrical excitation; it is a different scenario.
E33 studies finite anchors, E34 an electrically isolated sensor and E35 a partial
backside attachment.

**Compare one change:** run a stored electrical-drive or thermal-path study and
inspect temperature rise relative to the named reservoir, input heat and removed
heat. Do not add prescribed heat to make a Joule-heating case look hotter. Follow
[thermal scenarios](thermal-example-scenarios.md) for exact inputs and measured
sensitivity; the [stack guide](thermal-stacks.md) explains effective heat paths.

<a id="pulses"></a>

## Pulsed operation

**Path:** [E17](foundation-examples.md#e17) → [E18](foundation-examples.md#e18)/
[E19](foundation-examples.md#e19) → [E36](extended-examples.md#e36).

Start with **E17 · Original reference**, **Thermal transient**, and compare the
uniform thermal RC response. E18 adds current pulses and electrical temperature
feedback; E19 transfers a prescribed heat pulse to an unpowered receiver. E36
concentrates Joule heating in a finite constriction.

**Compare one change:** run a time-step study while retaining the same mesh and
output interval. Inspect **Peak frame**, **Last frame**, temperature histories and
the input/removed/stored-energy ledger. A cooled final field does not erase an
earlier failed limit. The **Failed engineering limit** and **Stop at accepted hot
state** scenarios deliberately demonstrate that distinction where available.
These are thermal-storage/quasi-static electrical models, not switching-loss or
inductive-overshoot calculations. See the [transient guide](handbook/transient.md).

## Semiconductor interconnects

<a id="gan"></a>

**GaN path:** [E37](device-engineering-examples.md#e37-separate-the-gate-contact-and-thermal-effects)
→ [E16](foundation-examples.md#e16) → [E21](power-examples.md#e21) →
[E38](device-engineering-examples.md#e38-let-parallel-bank-currents-emerge) →
[E22](power-examples.md#e22).

Choose **E37 · Voltage-controlled GaN coupon**. Inspect `channel_a → Sheet current
density`, finite contact loss and local gate/source bias. Use its stored output,
transfer or source-contact study. Every intended path crosses its gate. E16 offers
four fully gated stripes; **E38 · Parallel GaN banks** lets branch currents emerge
from a shared voltage reservoir instead of prescribing an equal bank current.
The original independently driven bank experiment remains available.

<a id="legacy-hemt"></a>

**E7/E8 are deliberate bypass contrasts**, not the starting point for a fully
controlled channel. Their ungated edges permit conduction around the gate;
insulated field plates have no electrostatic effect in this model. The local-bias
sheet law does not establish saturation, breakdown, trapping or off-state leakage.

<a id="sic"></a>

**SiC path:** optionally begin with [E9's material-conductivity lesson](foundation-examples.md#e9),
then [E39](device-engineering-examples.md#e39-separate-a-lower-current-reference-from-the-stress-case)
→ [E23](power-examples.md#e23) → [E24](power-examples.md#e24). Choose **Lower-current SiC reference (2 A)**
before running E39. Its original **10 A stress** reference is preserved. Compare
active-island sharing, source collection and passive Kelvin potential; then use
**SiC: heat in semiconductor (2 A)** to isolate heat-placement assumptions.
E23/E24 extend stripe and tiled macrocell source collection.

These are **SiC MOSFET on-state interconnect models** with area-normalized
resistance and a fixed-on-state assumption. An insulated gate drawing does not
control that load automatically. [E9](foundation-examples.md#e9) remains a doped SiC sheet
resistor; it is a separate conductivity lesson, not a MOSFET model.

**Compare one change:** preserve active geometry, device parameters, thermal
conditions and delivered service for a routing ranking. Voltage-biased GaN
alternatives can deliver different currents; a lower loss at lower current is
not an improvement at equal service. The [device guide](device-engineering-examples.md)
and [power-family reference](power-examples.md) separate observations, limitations
and resource-limited cases.

## Read every example the same way

Each example's catalog card identifies its question, topic/level, model limits,
recommended scenario and workload. Keep the original-reference identity alongside
any generated size, routing or operating choice. In the detailed walkthrough,
read the opening route, specific layer/quantity, expected behavior and one bounded
follow-up study before the downloads and maintainer reproduction commands.

Use the [scientific evidence index](example-science.md) for the exact configured
reference and scenario receipts. Generated, converged, conservation-checked,
mesh/time-tested and engineering-accepted describe different evidence. Historical
figures keep their original source identity; no example is experimentally
calibrated. ETL downloads contain the physical project; GDS/OASIS contain geometry
only. The [complete catalog](examples.md) retains all stable IDs.
