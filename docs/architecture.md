# Architecture and extension interfaces

`models.py` contains versioned typed dataclasses. Distances are meters, temperatures kelvin,
current density A/m², sheet current A/m and distributed sheet heat W/m². Only GUI/presentation
code converts engineering units. `Project` snapshots the complete physical experiment.

1. `geometry.layout.LayoutDocument` reads hierarchical KLayout geometry and metadata.
2. `geometry.prepare.prepare_geometry` resolves Boolean mask expressions, explicit polygons,
   full-domain sheets, ROI, terminal regions, interfaces, and validation diagnostics.
3. `connectivity.analyze` constructs inspectable components/nets and terminal traces.
4. `meshing.generate.generate_mesh` creates the common conforming P1 triangle mesh and region tags.
5. `physics` evaluates transport; `solvers` assembles and solves separate layer spaces.
6. `simulation.runner` validates, caches prepared geometry and mesh structure, orchestrates progress/cancellation,
   records material validity warnings and binds results to the setup hash.
7. `simulation.sweep` clones configurations through safe scalar parameter paths;
   `simulation.analysis` probes actual FE fields and compares compatible maps.
8. `io.project` saves JSON+NPZ+layout archives with no arbitrary pickle loading.
   `io.export` retains comprehensive numerical bundles. `io.reporting` owns text,
   CSP, CSV safety and provenance contracts; `io.sharing` owns explicit figure/data
   boundaries, exact selected-support clipping and complete-experiment archives.
   GUI and CLI both use its preview/execute contract. Sharing never serializes a
   source project and never uses viewport cropping as a data filter.
9. `simulation.execution` controls machine-local CPU budgets and records execution provenance;
   independent study points use bounded spawn workers.
10. `gui` owns widgets and worker lifecycle. The solver runs in an independent process;
    a receiver thread deserializes results and Qt's main thread updates widgets.
    Backend modules do not import Qt.

## Geometry and degrees of freedom

The common mesh describes x/y topology. The solver builds its own per-layer spaces;
sharing a global planar vertex does not join different conductors. Point-touching same-layer
components also remain separate. Only declared finite-area interfaces (or explicit ideal
contacts) connect layers. This separation lets future backends use independent meshes or 3D
without changing the configuration model or report provenance.

`PlanarMesh.regions` uses `layer:`, `interface:`, `terminal:`, `thermal:` and `heat:` prefixes.
Authoritative scalar fields use `SimulationResult.element_fields` with three
P1 values per element and layer. Compatibility global-node arrays cannot
represent point-touch ambiguity and never replace those fields in later physics.
Inactive entries are NaN. Boundary edge pairs preserve the exact support of
edge sources and Dirichlet boundaries. `interface_fields` retains local normal
current/drop/loss data; versioned `analysis` records scoped measurements and
physical contact/port/group identities.

## Material precedence

Reference seeds are immutable; SQLite stores custom copies. Every project includes full material
snapshots, so changing a user library record cannot silently alter saved experiments. Editing the
project material snapshot is the highest-priority override. Reapplying a library value is explicit.

## Persistence and reproducibility

`.etl` is ZIP with `project.json`, optional `layout/source.*`, `results/metadata.json`, and
`results/arrays.npz`. Embedded layout hashes are verified. Result configuration hashes exclude
machine-specific layout paths when the file hash is available. Unknown schema versions fail
explicitly. Archives have decompressed-size limits and do not extract user-controlled paths.
Limits cover the aggregate expanded project, nested run archives and numerical arrays.
`estimate_project_archive` checks a save before serialization; `inspect_archive_size` checks the
completed temporary archive before atomic replacement. Rejected saves preserve the prior file.

Result identity covers the physical setup and bound saved/resume source field. Execution
preferences are excluded from the physics hash and are stored as separate provenance. Saved-state
initialization validates materials, geometry and field support, and reuses the compatible source
mesh rather than regenerating it under a different CPU setting. Resume retains absolute time and
cumulative energy; exports label segment times, initialization, peak scope and energy scope.

Process-local LRU caches retain at most 32 entries each: an estimated 64 MiB of prepared
geometry and 256 MiB of mesh arrays. Source file content is reverified before reuse. Geometry
keys include ROI, region shapes and validation-sensitive stack settings; bias/material-value
sweeps reuse geometry. Defensive copies prevent callers corrupting cached arrays or diagnostics.
`clear_caches()` and `cache_info()` expose controls for embedding applications. Optional full
result caching retains one bounded result and uses the complete configuration hash.

## CPU execution and worker lifecycle

`ExecutionSettings.cpu_threads` is a total logical CPU budget, defaulting to
`min(4, available_cpus)`; `study_workers` is bounded by it. `threadpoolctl` limits supported native
BLAS/OpenMP pools and Gmsh receives the native thread limit. Sweep/design/mesh/time studies divide
the budget among `min(study_workers, point_count)` spawned processes, preserving input order and
individual failures.
Because native limits apply to an entire process, execution contexts in separate Python threads
are serialized. Larger FEM batches use a run-owned executor with independent output buffers
and deterministic ordered reduction. Their inner native pools use one thread to prevent
oversubscription; executor tasks join before the batch or context releases its limits.
Small batches, geometry work and SuperLU factorization remain serial. Actual assembly activity
is recorded separately from the configured budget. The implemented backend is CPU only.

The [common-mesh CPU receipt](../benchmarks/e21-cpu-0.3.1.json) records four assembly
threads and 49 identical compared fields; its complete four-CPU run was slower.
Use [validation](validation.md) for measured source, native and frozen checks,
rather than inferring verification or speedup from a requested CPU count.

The desktop coordinator is non-daemon to permit nested study workers. Its queue receiver performs
deserialization outside the GUI thread, then signals the main thread to update widgets. `psutil`
supports cancellation of the owned coordinator and descendants. Preparation and hierarchy jobs
are serialized with numerical runs; queued solves wait for preparation to bind source identity.
The settings menu stores execution preferences locally through Qt settings. They do not travel
with the project's physical experiment, while each result records how it was executed.

## Transient thermal operation

Transient heat flow is implemented by `solvers.transient`, using backward Euler with the existing
thermal assembly, a selectable consistent or row-sum lumped heat-capacity matrix and quasi-static
electrical states. Event-aligned steps include pulse edges and PWL knots. Bound diagnostics report
discretization excursions without clipping fields; lumping alone does not guarantee monotonicity
on every mesh/interface operator. Periodic stopping checks canonical cycle events, while mesh and
time-step convergence studies disable early stopping to compare the same physical horizon.
`Project.transient` stores timing and waveform drives. `SimulationResult.transient`
holds accepted-step scalar history and saved field snapshots on one shared mesh;
`io.transient` validates and persists that history. See the
[transient handbook](handbook/transient.md) for its public workflow and model limits.

## Power routing and optimization

`power.analysis` owns physical selectors, quadrature and reporting;
`power.multiport` reduces passive fixed-temperature networks. `solvers.power`
assembles external port feeds, local contacts and disjoint losses. The shared
`power.workflows` dispatcher is used by desktop workers and the CLI.

`routing` generates declarative bounded recipes through KLayout source copies,
checks integer-DBU rules, clones selected hierarchy paths and verifies exported
GDS/OASIS geometry. `technology.stack` derives/checks physical via dimensions and
removes displaced dielectric support. `optimization` binds immutable scenarios,
service requirements and measurement policy, records resumable candidate history,
and refines the baseline/finalists before accepting an improvement. See
[power routing](POWER_ROUTING.md) for contracts and limitations.

## Future backends

`MeshBackend` and `SimulationBackend` protocols expose stable generation and solve contracts.
The layer z/thickness information, versioned geometry definitions, scalar parameter paths, and
configuration-linked fields support later 3D and imported transport backends.
These future features have no decorative controls in the current application.
