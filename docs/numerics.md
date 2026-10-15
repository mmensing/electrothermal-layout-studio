# Numerical method and verification

This reference describes the spatial discretization and steady solves. The
[transient method](handbook/transient.md#what-is-solved) extends the same thermal
system with consistent or row-sum lumped capacity, backward Euler, event-aligned inputs,
accepted-step history and energy storage. Steady sink and balance rules below
apply to steady operation; an adiabatic transient can instead accumulate energy.

## Geometry and degrees of freedom

A conforming planar triangular mesh represents all layer, interface and boundary
masks. Each active physical layer has its own linear P1 finite-element space.
Coordinates shared by two layers never identify electrical or thermal unknowns.
Within a layer, triangles are connected only through common edges. Components
that meet at a point receive separate degrees of freedom at that vertex. Ideal
electrical contacts and equipotential electrodes explicitly collapse degrees of
freedom; finite interfaces do not.

The authoritative P1 field contract is per-layer `(M, 3)` element-local nodal
values, with inactive support marked NaN. Compatibility N-node arrays cannot
represent distinct states at shared point-only vertices and never supply later
physics. Probes, profiles, restart, persistence and exports preserve local field
support; ambiguous boundary queries do not invent a mean. Rendering may duplicate
coincident vertices. Legacy results containing only already averaged nodal fields
are marked limited and require recomputation where the original values cannot
be recovered. Interior line electrodes use exact constrained mesh edges;
passive probes add no electrode constraint. Node-only fallback selections without
edge metadata remain for simple API meshes.

## Assembly and constraints

For triangle area A and constant shape-function gradients B, lateral stiffness
is `A * B * diag(Gx,Gy) * B.T` electrically, and the analogous matrix with `kx*t`
and `ky*t` thermally. Material properties use the element-mean temperature and,
for the HEMT law, element-mean channel potential. Interface and Robin terms use
the exact P1 mass matrix `A/12 * [[2,1,1],[1,2,1],[1,1,2]]`. Equal and opposite
off-diagonal contact blocks ensure conservative transfer between sheets.
The edge mass matrix is `L/6 * [[2,1],[1,2]]` times the relevant thickness and
conductance. Constant area or edge source loads integrate exactly.

An equipotential electrode is a single reduced unknown regardless of how many
vertices it covers. A total current adds one integrated load to that unknown.
A floating electrode receives zero total load. Known voltage/temperature values
are eliminated, not implemented as a large penalty. Terminal currents are
reactions in the reduced system, so an ideal interface attached to an electrode
includes currents from every identified sheet. Overlapping electrode conditions
or ambiguous shared ideal terminal groups raise a user-actionable error.

The matrix graph is checked for missing reference potentials and thermal sinks
before solving. A current-driven component with nonzero net source and no voltage
reference fails. `floating_policy="reference"` explicitly adds and reports a
zero-voltage gauge to each otherwise balanced unreferenced component. The default
is an error. Thermal components always require a temperature or heat-loss sink.

## Linear and nonlinear solution

SciPy sparse direct solving is the default. Symmetric Jacobi scaling reduces
unit and conductance disparities. Conjugate gradients are available for positive
linear conduction subproblems; failed iterations are reported. Newton HEMT
Jacobians are generally nonsymmetric and use a direct solve. Matrix rank failure,
nonfinite values, nonpositive conductivities or invalid absolute temperatures
produce explicit setup/numerical errors.

The electrical solve uses a per-component reference shift in its algebra and
field reconstruction. This removes roundoff currents in an unbiased conductor
held at a nonzero common voltage. Actual absolute channel potential still enters
the local gate-control model. Thermal algebra similarly solves temperature rise
about the initial reference, avoiding a 300 K offset in a tiny heating signal.

The HEMT Newton Jacobian includes `dGs/dVmean` in addition to ordinary stiffness.
A backtracking residual line search controls each step; a positive-conductance
Picard step with configured relaxation is the fallback. Nonlinear thermal
conductivity is solved by damped Picard iteration, with a reassembled residual
at every accepted temperature. Property-table/linear/polynomial evaluations are
provided by the material model subsystem, including validity-range warnings.

Coupling alternates electrical and thermal solves with temperature damping.
After each update, both PDEs are evaluated at the same temperature: the electrical
solution is refreshed and the thermal residual is assembled without a forced
thermal solve. This prevents a stale thermal source from masquerading as
self-consistency. Convergence requires temperature change below
`absolute_tolerance + relative_tolerance*max(abs(T))`, each normalized PDE
residual below `relative_tolerance`, and each configured conservation tolerance.
The coupled iteration history records these errors, peak temperature and power.
The iteration budget applies to each nonlinear subproblem and to the outer loop.
Native sparse factorization is synchronous; cancellation is checked between
assembly/iteration stages rather than inside a running factorization.

Execution uses a machine-local total CPU budget, defaulting to four logical CPUs
or the available count when smaller. Native numerical/meshing thread pools are
bounded. Independent study points use spawned processes with the budget divided
among actual workers; requested row order and individual failures are preserved.
Larger FEM topology, layer operators and sparse assembly run in bounded worker
threads. Each task owns its output buffers; matrices combine in deterministic
input order. Inner native pools are capped at one during an assembly batch to
prevent multiplying the CPU budget. Small batches and SuperLU factorization
remain serial. This changes execution, not tolerances or physical acceptance.
Execution provenance is stored outside configuration and physics hashes. Native
thread counts can affect generated triangulations, so compare common spatial
profiles and conserved quantities across independently meshed runs. Compatible
saved-state runs reuse the archived mesh rather than remeshing its temperatures.

The [historical 0.3.1 verification](releases/0.3.1.md#historical-verification) includes the guarded
1,294-test source suite, independent serial/parallel field comparisons and exact
cache-change regressions. The [historical-mesh CPU pair](../benchmarks/e21-cpu-0.3.1.json)
records four actual assembly threads; complete-run time was not improved.

## Conservation and result interpretation

Positive terminal current enters the sheet. Terminal work is `sum(Vterminal*I)`;
density-source work uses the exact nodal load dotted with nodal voltage.
Lateral loss is `sum(A*E dot K)`. Interface loss is `deltaV.T * (g*M) * deltaV`
on each contact element. Each half contact source is deposited on its adjacent
thermal sheet. Thermal generation includes those losses and independent sources.
Robin heat flow uses its assembled mass matrix; fixed-temperature heat removal
is the negative constrained-node reaction.

The current balance error is `abs(sum(I))/max(sum(abs(I))/2, tiny)`. Electrical
power error is the difference between source work and lateral-plus-contact loss,
normalized by the larger magnitude. Thermal balance compares generated heat and
total outward boundary flow, normalized by generated heat or gross boundary flow.
The gross boundary denominator also supports heat transfer between unequal
temperature reservoirs with zero internal heat generation. No source, current or
flux is rescaled to improve these metrics.

Effective electrical resistance is voltage span divided by total positive
terminal injection. It is a conventional two-terminal metric; in a multi-source
network use the complete terminal table and field results instead of treating it
as a unique circuit equivalent. Integrated power and terminal currents typically
converge faster than raw maxima at sharp corners.

`current_density_average` is the area-weighted mean magnitude over all conducting
sheets; `current_density_p95` is its area-weighted 95th percentile. Overlapping
sheets each contribute their physical area. `peak_to_average` divides the raw
maximum by that mean and is undefined for identically zero current. For branch
totals, `branch_current(result, layer_id, start, end)` integrates the sheet current
across an arbitrary straight cut in meters and returns amperes, positive toward
the left normal of start→end. Exact triangle intersections avoid sampling bias.
Along a shared element edge the two one-sided field estimates are averaged and
the edge counted once. P1 gradients are discontinuous, so arbitrary local cuts
still need refinement checks; terminal reactions remain the primary globally
conservative current measurement.

These compatibility statistics are descriptive. New optimization merit and
constraints use persisted physical selectors and measurements, including
area-weighted P1 integration, area percentiles and fixed-size hotspot windows.
Metal volume current, semiconductor sheet current and contact-normal current
have separate definitions. Thermal rises use an explicit named or numeric
reference; multiple unequal reservoirs no longer acquire a node-count-weighted
reference. See [power interconnect numerics](POWER_ROUTING.md) for equations,
support semantics, passive matrices and the empirical finalist refinement policy.

Transient conservation includes capacity storage and counts interlayer heat
exchange once with equal and opposite sheet contributions. Pointwise temperature
bounds are a separate check: consistent P1 capacity can create local undershoot
even when energy balances. The optional row-sum lumped capacity preserves total
capacity and can reduce this effect. It does not guarantee monotonicity on every
mesh or coupled-sheet operator. Review bound diagnostics, retain the selected
scheme in the setup, and check spatial and temporal refinement.

Mesh and time-step studies disable periodic early stopping to compare the same
requested physical duration. Time-step studies hold output cadence fixed.
Convergence deltas are emitted only between completed, converged consecutive
points; failed or partial results do not supply an accuracy baseline. Saved-state
studies require matching material/layer/interface definitions and exact mesh
supports. A changed mesh needs a uniform/steady start rather than implicit field
interpolation.

## Independent checks

The process-local runner reuses prepared geometry in a least-recently-used cache
limited to 32 entries and 64 MiB of estimated GEOS/container storage. The separate
mesh cache has a 256 MiB budget, including region, terminal and boundary arrays.
`cache_info()` reports retained counts and estimated bytes; `clear_caches()` drops
them. Returned geometry and mesh objects cannot mutate cached entries. Every run
validates the current settings and verifies the layout content hash. Bias and
material-property changes reuse geometry; thickness/z changes refresh geometry
diagnostics because physical-volume overlap can change, while preserving mesh
reuse. Geometry, ROI, selection, contact shape and relevant validation settings
invalidate the corresponding entries.

The configured `run_project`/`solve` workflows retain the reproducible setup and
recompute any configured Joule source. Explicit low-level `solve_electrical` and
`solve_thermal` calls are calculation primitives: when supplying external nodal
temperature or element heat arrays, retain those arrays and the matching mesh
alongside the project. The project configuration alone cannot reconstruct
arbitrary externally supplied fields. Set its mode to the calculation performed
when saving a low-level result for later interpretation.

The solver tests create structured meshes independently of Gmsh. Checks cover:

- Exact rectangular resistance, potential and field; anisotropic and CG variants.
- Independent overlapping sheets, ideal parallel sheets, specific/lumped/via
  contacts, distributed current, total-current and floating equipotentials.
- Missing references, incompatible injection, conflicting electrodes and
  point-only nonconnection.
- Uniform area heating with coefficient, area-resistance and total-resistance
  sinks; fixed-temperature analytical strip profiles and nonlinear conductivity
  against its Kirchhoff-transform solution.
- Explicit thermal stack, through-plane anisotropy and gap/interface resistance.
- Semiconductor mobility law and HEMT analytical conductance derivative,
  local-charge integral current, gate modulation, depletion floor and failure
  reporting.
- Voltage/current driven metal feedback against exact uniform electrothermal
  solutions, conserved contact heating, cancellation and nonconvergence.

`tests/test_solver_examples.py` additionally imports all 19 generated layouts,
meshes their true masks and solves the electrical, prescribed-source thermal and
coupled modes supported by their layers; thermal-only E19 also has dedicated
pulse and isolation checks. The workflow tests cover persistence, profiles, sweeps and
comparison. For application-specific use, run the automated mesh study with the
actual contacts and thermal stack; success of an analytic test does not establish
mesh convergence of a different layout.
