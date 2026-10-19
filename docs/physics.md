# Physical model and units

Electrothermal Layout Studio solves thickness-integrated conduction on separate
sheets in steady or transient thermal operation. All geometry, properties, boundary values, stored arrays and API
arguments use SI units. Display conversion does not change the physical model.
Each physical layer retains its bottom elevation `z` and thickness. Neither an
x–y overlap nor proximity in z creates an electrical or thermal connection: an
enabled interface with an explicitly selected overlap is required.

## Electrical sheets

For each conducting sheet, the weak equation represents

`−div(Gs grad V) + sum(gij (Vi−Vj)) = j_ext`.

Bulk sheet conductance is `Gs = t/rho(T)` in S. An electrical anisotropy pair
multiplies the x and y conductances; the initial implementation supports diagonal
anisotropy in the layout axes. Sheet-resistance models use `Gs = 1/Rs(T)`.
Effective semiconductor conduction uses `Gs = q n mu(T) t`, with carrier density
in m⁻³, mobility in m²/(V s), and
`mu(T) = mu_ref (T/T_ref)^(-mobility_exponent)`.

The potential field is in V; `E = −grad V` is in V/m; sheet current
`K = Gs E` is in A/m; the effective volume current `J = K/t` is in A/m².
The volume current reported for a 2DEG uses the user-assigned effective channel
thickness. It is not an independently resolved microscopic depth profile.
Sheet Joule density is `q'' = E dot K` in W/m². Its integral is in W.

This resistive semiconductor model has no pn junction, depletion-region
electrostatics, carrier continuity equation, recombination, breakdown or
incomplete-ionization physics. Carrier concentration and mobility are effective,
process-calibrated inputs, not deductions from nominal doping.

## Gate-controlled GaN channel

Within a gated region define

```
Vth(T) = Vth_ref + threshold_temperature_coefficient * (T−T_ref)
x = (Vgate−Vchannel−Vth(T)) / smoothing_voltage
n_raw = gate_coupling * smoothing_voltage * log(1+exp(x))
n_s = background_sheet_density + n_max * tanh(n_raw/n_max)
Gs = minimum_sheet_conductance + q * mu(T) * n_s
```

The stable `logaddexp` implementation avoids overflow. The tanh cap is smooth;
a nonpositive configured `maximum_sheet_density` disables that cap. The positive
minimum conductance is an explicit depleted-channel floor and prevents singular
disconnected channels. Very deep off-state current consequently depends on this
numerical/model floor and should not be interpreted as leakage prediction.

The element-mean **local channel potential** enters charge control. The nonlinear
Newton Jacobian includes the analytical derivative of conductance with respect
to that potential. Positive and negative threshold voltages allow enhancement
and depletion parameter sets.

With no gate-control masks, `gate_voltage` controls the whole sheet. A gate
terminal on a HEMT sheet controls charge without imposing channel potential or
injecting current. Explicit `gate_regions` may reference gate terminals on another
layer; their mask intersection selects channel elements. With spatial gates,
ungated access regions use the maximum density plus background; if the density
cap is disabled, they use the charge law at channel potential zero and the
configured baseline gate voltage. Temperature still affects access mobility.
A gate terminal on an ordinary metal also fixes that metal's potential.

The model excludes full drift diffusion, velocity saturation, short-channel
effects, field-dependent mobility, trapping, gate leakage, avalanche, hot
electrons and detailed field-plate electrostatics. A field plate can conduct and
transport heat as a separate metal sheet, but its electrostatic effect is absent
unless the user explicitly assigns a gate-control mask. Example parameter sets
are demonstrations, not predictive or calibrated GaN transistor models.

## Electrodes, contacts and vias

Voltage, ground, total-current and floating terminals are equipotential regions.
Total-current terminals solve an unknown voltage with exactly the requested
integrated injection. Floating terminals solve an unknown voltage with zero
integrated injection. They do not impose a uniform current density. Density
terminals instead apply an explicit distributed source: A/m² over an area mask,
or A/m² times thickness times selected edge length for an edge selection.
Positive terminal current flows **into** the model.

Ideal interfaces identify corresponding layer degrees of freedom over the contact
mask. Finite contacts retain separate potentials and add distributed exchange:

| Interface input | Distributed conductance in S/m² |
| --- | --- |
| Specific contact resistivity `rho_c` in Ω m² | `1/rho_c` |
| Lumped mask resistance `R` in Ω | `1/(R*A_mask)` |
| Via bulk material and height `h` | `1/(rho_via(T)*h)` |
| Direct interface conductance | supplied S/m² |

The legacy/default `resistance_semantics="array"` lumped-resistance option
distributes its specified resistance over the complete mask. Explicit
`"instance"` semantics applies that resistance independently to each physical
contact island. These meanings are persisted and never silently interchanged.
Array semantics gives exactly R for two uniform contacting sheets; lateral access resistance
and current crowding remain part of a general layout solution. This is not a
zero-size circuit element attached to two arbitrarily selected points.

Contact loss is the integral of `g (Va−Vb)^2` over the true mask, evaluated with
the consistent triangular mass matrix. The default assigns half of each contact
element's heat to each adjacent sheet, preserving existing projects. A calibrated
`heat_to_layer_a_fraction` may change that partition; alternatively `heat_layer_id`
deposits the complete interface/device-load loss into a named physical thermal
sheet on identical x–y support. Missing support is rejected, not clipped or
renormalized. Electrical loss remains unchanged. These explicit assumptions
conserve energy without resolving an internal vertical temperature profile.
Ideal interfaces dissipate no interface power. `joule_density` contains lateral
loss, `contact_density` contains allocated contact loss, and thermal
`heat_density` includes both plus any prescribed source.

## Thermal sheets and boundaries

The thermal equation is

`−div(k_parallel(T) t grad T) + sum(gth_ij (Ti−Tj)) = q''`.

Conductivity is in W/(m K), temperature in K, and interface conductance in
W/(m² K). Thermal anisotropy multiplies the layout-axis lateral conductivities.
An optional `thermal_conductivity_z` sets the vertical conductivity separately.
An interface of kind `stack` uses the sum

`t_a/(2*kz_a) + R_interface_area + h_gap/kz_gap + t_b/(2*kz_b)`.

The gap term is present only when an interface material is assigned. Height
alone does not invent a gap material. These terms connect sheet midplanes and
must be chosen consistently with any external half-thickness sink boundary.
An explicit dielectric film replaces the corresponding material-filled effective
gap; use adjacent stack links so its two half-thickness contributions add to one
full film resistance. Preparation rejects a gap occupying an explicit intervening
sheet and diagnoses legacy direct bridges that can create parallel bypass paths.
See [thermal stacks](thermal-stacks.md) for GUI setup, deposition assumptions,
measured sensitivities and the unverified external 3D reference protocol.

Fixed-temperature boundaries constrain selected nodes to an absolute temperature.
An area coefficient h gives `q_out'' = h*(T−Tambient)`. Area resistance is its
reciprocal. A total boundary resistance R in K/W becomes `h = 1/(R*A_mask)`.
For edge heat loss, the selected side area is thickness times edge length.
Insulated boundaries supply no flux; unspecified exterior edges are also
insulated. Prescribed heat may be a density in W/m² or an integrated total in W.

Thermal boundary heat flows are positive outward and reported per reservoir.
Peak thermal resistance is `(Tmax−Treference)/Q_internal` only for positive
internal power and an unambiguous or explicitly selected reference. A named
boundary or numeric reference is independent of mesh-node counts. Multiple
unequal reservoirs require an explicit operational reference; zero-generation
reservoir-to-reservoir heat flow has no device thermal resistance. The area-average
temperature includes each active sheet's area, so overlapping layers count
separately. Hotspot coordinates include the sheet elevation.

## Coupling, accuracy and use

The electrothermal solve updates material resistivity, channel charge/mobility,
via-material resistivity and thermal conductivity at the current temperature.
The scalar specific/lumped contact parameters remain temperature independent.
Both PDE residuals and electrical/current/thermal conservation must pass before
`converged` is true. Heat is never renormalized to force agreement with boundary
power. Nonconverged finite fields are retained for diagnosis and clearly marked.

For one-way heating, use `electrical_heat(dc_result)` and pass that dictionary
with **the same mesh** to `solve_thermal`. Ordinary thermal mode uses prescribed
heat sources. The explicit `thermal_include_joule` setting requests a fresh
electrical solve at the initial temperature before thermal mode; it does not
reuse a potentially stale earlier result. Coupled mode recomputes heating
self-consistently.

This 2.5D approximation resolves lateral gradients and explicit vertical links.
It does not resolve temperature or current variation through a layer, full 3D
spreading, detailed via sidewalls, skin effects or radiation. Transient operation
adds sheet heat storage using material mass density and specific heat, with
quasi-static electrical states at each step. It does not model electrical
capacitance, inductance or switching dynamics. See
[transient and pulsed operation](handbook/transient.md) for the equation,
initial-condition assumptions, waveforms, time integration and energy checks.
Material validity warnings must be considered when interpreting hot results. Sharp-corner
peak current density is mesh-sensitive; use refinement studies and local profile
averages, and specify process-specific engineering limits.
