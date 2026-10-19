# Model and physics

[Handbook](README.md) · [← First calculation](first-calculation.md) · [Next: setup →](setup-and-boundaries.md)

Electrothermal Layout Studio converts a mask layout into a set of coupled
conducting and thermal sheets. It resolves lateral variation within each sheet
and exchange through explicitly declared interfaces. This page describes the
steady model; all equations use SI units. The
[transient chapter](transient.md#what-is-solved) adds the sheet heat-capacity term
and explains pulse timing and quasi-static electrical assumptions.

## Why layers remain separate

The same planar triangle can support several physical layers. Each layer has
its own unknown potential $V_i$ and/or temperature $T_i$, even when its vertices
share x–y coordinates with another layer. Thickness and z describe the stack;
an enabled interface supplies the physical connection.

Two metal masks that overlap in the drawing are therefore not automatically
shorted. Similarly, a hot metal sheet does not heat an underlying substrate
unless a thermal connection has been declared. Within a layer, edge-connected
geometry conducts; a point-only touch remains disconnected.

This construction supports multilayer routing without collapsing its fields
into a single image. It cannot resolve a through-thickness gradient inside a
sheet or the detailed three-dimensional field inside a via.

## Electrical conduction

For an electrically active sheet $i$, the thickness-integrated equation is

$$
-\nabla_{xy}\cdot(\mathbf G_i\nabla_{xy} V_i)
+\sum_j g_{ij}(V_i-V_j)=j_{\mathrm{ext},i}.
$$

$\mathbf G_i$ is the sheet-conductance tensor in S, and $g_{ij}$ is a distributed
interface conductance in S/m² on the contact area. A bulk conductor has
$G_s=t/\rho_e(T)$; an explicit sheet-resistance model has $G_s=1/R_s(T)$.
Configured x/y anisotropy multiplies the two layout-axis conductances. Rotated
off-diagonal anisotropy is not represented.

The reconstructed fields are

$$
\mathbf E=-\nabla V,\qquad
\mathbf K=\mathbf G\mathbf E,\qquad
\mathbf J=\frac{\mathbf K}{t},\qquad
q''_{\mathrm{Joule}}=\mathbf E\cdot\mathbf K.
$$

| Quantity | Meaning | Unit |
| --- | --- | --- |
| $\mathbf E$ | Lateral electric field | V/m |
| $\mathbf K$ | Current per transverse width | A/m |
| $\mathbf J$ | Effective volume current density | A/m² |
| $q''$ | Heat generation per sheet area | W/m² |

Integrating $q''$ over the physical sheet gives power in W. Do not multiply
`joule_density` by thickness again: thickness is already included in the sheet
current. The volume-current density of a 2DEG depends on its assigned effective
thickness; sheet current is often the more direct comparison.

Finite electrical interfaces dissipate

$$P_{ij}=\int_{A_{ij}} g_{ij}(V_i-V_j)^2\,dA.$$

The solver integrates that expression with the consistent P1 mass matrix and
defaults to assigning half the contact heat to each adjacent sheet. The interface
editor supports a calibrated endpoint partition or complete deposition into a
named physical thermal sheet covering the interface. Neither choice changes the
electrical loss or resolves a 3D internal heat profile. Ideal interfaces identify
electrical unknowns and generate no interface loss. See the measured
[passivation and thermal-stack comparisons](../thermal-stacks.md).

## Thermal conduction

Each thermal sheet solves

$$
-\nabla_{xy}\cdot(t_i\mathbf k_i(T_i)\nabla_{xy}T_i)
+\sum_j g^{\mathrm{th}}_{ij}(T_i-T_j)=q''_i.
$$

$\mathbf k_i$ is lateral thermal conductivity in W/(m K); an interlayer thermal
conductance has units W/(m² K). The source combines prescribed heating and, when
requested by the calculation mode, lateral Joule and allocated contact loss.

A thermal `stack` interface connects sheet midplanes using the area resistance

$$
R_A=\frac{t_a}{2k_{z,a}}+R_{A,\mathrm{interface}}
+\frac{h_{\mathrm{gap}}}{k_{z,\mathrm{gap}}}
+\frac{t_b}{2k_{z,b}},\qquad g^{\mathrm{th}}=\frac{1}{R_A}.
$$

The gap term exists only when a material is assigned to the interface. Specifying
a height without a gap material does not supply that term. Check whether an
external backside boundary already accounts for the remaining half thickness;
otherwise a stack model can double-count or omit resistance.

Fixed-temperature boundaries impose $T$. A coefficient boundary supplies
$q''_{\mathrm{out}}=h(T-T_{\mathrm{ambient}})$; unspecified exterior boundaries
are insulated. Positive reported boundary heat flow leaves the model. A thermal
component in a steady calculation needs a heat-removal or temperature reference
to define its steady state.

## Temperature-dependent and semiconductor models

Material properties can be constant, linear in temperature, polynomial in
$T-T_{\mathrm{ref}}$, or tabulated. Evaluation must remain finite and positive.
Validity warnings indicate evaluation outside the configured temperature range;
tabulated models use endpoint values outside their tabulated range and warn.

The effective semiconductor sheet uses
$G_s=q n\mu(T)t$, with
$\mu(T)=\mu_{\mathrm{ref}}(T/T_{\mathrm{ref}})^{-m}$.
Carrier density and mobility are supplied effective parameters. The calculation
does not derive them from doping, solve a junction, or compute recombination.

The HEMT sheet adds a local gate-controlled charge law. Its smoothed overdrive
depends on **gate voltage minus local element-mean channel potential**, with a
temperature-dependent threshold, density cap and positive minimum conductance.
The [full equation and gate-mask rules](../physics.md#gate-controlled-gan-channel)
are in the physics reference. The electrical solver includes the derivative of
conductance with respect to channel potential in its nonlinear Jacobian.

Local gate masks only control the channel under those masks. An ungated route
can remain conductive around a gated region. Compare E7/E8 with E16 in the
[gallery](../examples.md) before interpreting a weak gate response as a solver
failure. The minimum sheet conductance sets a depleted-channel floor; calculated
deep-off current is not a leakage prediction.

## Coupling and numerical representation

An electrical solve evaluates transport at the initial temperature. A one-way
thermal calculation first solves electrical loss at that temperature, then
holds the loss fixed while solving temperature. A coupled solve alternates the
electrical and thermal equations with damping, refreshing both residuals at the
same temperature before accepting convergence.

Potential and temperature use linear P1 shape functions within each triangle.
Their gradients, and the corresponding element fields, are piecewise constant.
An equipotential electrode reduces all its selected degrees of freedom to one
unknown. Total-current and floating electrodes solve that unknown voltage from
an integrated current constraint. They do not impose uniform current density.

The [numerics reference](../numerics.md) explains assembly, constraints, nonlinear
iterations, conservation and the separate degrees of freedom at point contacts.
The [architecture reference](../architecture.md) maps those stages to modules.
Relevant implementation entry points are
[`constitutive.py`](../../src/etlayout/physics/constitutive.py),
[`electrical.py`](../../src/etlayout/solvers/electrical.py),
[`thermal.py`](../../src/etlayout/solvers/thermal.py) and
[`solve.py`](../../src/etlayout/solvers/solve.py).

## Assumptions that determine whether a calculation is useful

The sheet model omits full 3D spreading, through-layer variation, skin effects,
radiation and detailed via sidewalls. The HEMT approximation also omits drift
diffusion, velocity saturation, trapping, gate leakage, breakdown and detailed
field-plate electrostatics. A drawn field plate can conduct and transfer heat;
its electrostatic influence is absent unless a gate-control mask explicitly
models an effective influence.

Process-calibrated dimensions, materials, contacts and thermal boundaries matter
as much as the mesh. Numerical convergence confirms the configured equations
were solved; it does not validate those equations or parameters for a device.

[← First calculation](first-calculation.md) · [Geometry, materials and boundaries →](setup-and-boundaries.md)
