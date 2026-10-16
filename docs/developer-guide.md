# Developer guide

Install Python 3.12, create a virtual environment, install the tested Windows lock, then
`pip install --no-build-isolation --no-deps -e .`. Use hash-verified binary wheels
from the tested lock. Windows 11 x64 is the validated desktop platform; other
platforms do not have equivalent validation.

## Checks

```powershell
python -m pytest
python -m coverage run -m pytest --basetemp=.pytest_coverage --junitxml=artifacts/pytest-results.xml
python -m coverage combine
python -m coverage report
python -m coverage json -o artifacts/coverage.json
python -m coverage html -d artifacts/coverage-html
python -m ruff check src tests scripts benchmarks
python scripts/verify_examples.py
python scripts/gui_smoke.py
python scripts/gui_acceptance.py
python docs/handbook/verify_walkthroughs.py
python docs/handbook/verify_transient_walkthroughs.py
python scripts/transient_workflow_acceptance.py --offscreen
python benchmarks/run.py
python scripts/build_windows.py
```

Pytest uses Python stream capture instead of replacing native file descriptors. This avoids a
Windows native-DLL handle failure observed once during a sandboxed Gmsh load. Native imports,
CLI runs and the repeat tests succeeded. Use a fresh `--basetemp` beneath `.cache` if a restricted
host gives its system temporary directories incompatible ownership. Create the parent directory
first, or use a root-level `--basetemp=.pytest_run` as CI does. Pytest does not create missing parents.

Public CI uses only isolated GitHub-hosted Windows runners. The stable
`source-quality` check runs lint, publication/workflow contracts and fast/package
policy tests for PRs and main. Full numerical/desktop regression and acceptance
journeys run on main or a deliberate owner request on an owner-controlled branch. Superseded PR jobs cancel;
main runs do not duplicate feature-branch pushes. No workflow publishes packages,
releases or tags, writes repository contents or holds publishing credentials.

See [`ci.yml`](../.github/workflows/ci.yml) and
[the maintainer guide](maintainer-guide.md) for trust boundaries and repository
settings. Manual frozen-build evidence is distinct from source tests. Native
UI inspection and extracted executable checks remain build-host evidence, not
clean-machine, screen-reader or physical mixed-monitor DPI certification.

Run the ordinary pytest command for a quick local check or the coverage sequence
for an audited full run. Coverage configuration in `pyproject.toml` includes
branches, multiprocessing/thread workers and subprocesses. The tested Windows
lock includes coverage.py. Read statement and branch percentages separately:
coverage.py's combined percentage is not statement coverage. Do not exclude hard
paths or lower a gate to make a numerical change appear better covered. The
[power-routing audit](scientific-acceptance.md) records the isolated starting source,
environment, current baseline counts and final-run status.

## Execution and desktop isolation

`simulation.execution.ExecutionSettings` controls this computer's execution policy separately
from `Project` physics. `cpu_threads` is a total logical CPU budget, defaulting to four or the
available CPU count when fewer are available. Availability respects process affinity where
supported. `study_workers` defaults to the same count and cannot exceed that budget. The desktop
persists these preferences in **Edit → Execution settings…**; CLI commands accept
`--cpu-threads` and `--study-workers`, and `python -m etlayout capabilities` reports availability.

`threadpoolctl` caps supported native BLAS/OpenMP pools, and meshing applies the selected Gmsh
thread limit. Independent sweep, design, mesh and time-study points use bounded `spawn` processes.
The worker count is `min(study_workers, point_count)`; each receives
`floor(total_budget / worker_count)` native threads. Results retain input
order and row-level failures. Native pool limits are process-wide, so execution contexts in
different Python threads are serialized. Use processes for concurrent independent runs.
Larger FEM batches dispatch independent array/sparse work through a run-owned thread executor.
Workers share read-only input and own output buffers; results combine in input order. Native
BLAS pools use one thread during a batch, and the selected cap is restored afterwards. Running
tasks join before limits or execution context are released, including failures. Mesh topology
and interface addressing are reused only within their owning immutable space. Small batches,
geometry work and SuperLU factorization remain serial. More study workers also need more memory.
Execution provenance records dispatched batches, worker thread IDs and measured CPU/wall time;
the configured CPU cap alone does not demonstrate parallel calculation or a speedup.
The [performance guide](performance.md) describes measured run stages, exact-reporting
optimizations and comparisons using an unchanged archived field set.
The supported backend is CPU; no GPU runtime or CUDA dependency is required.

The [measured CPU comparison](../benchmarks/e21-cpu-0.3.1.json) records four actual
assembly threads and identical fields for one/four-CPU runs on the same historical
mesh. Numerical time was 63.851/59.339 s; complete calculation/reporting was
104.216/109.805 s. It demonstrates assembly concurrency, not overall speedup.
[Validation](validation.md) separates historical observations from current public checks.

Desktop jobs run in a separate, non-daemon coordinator process so studies can spawn their own
workers. A receiver thread deserializes IPC results; widget updates remain on the Qt main thread.
Cancellation uses `psutil` to stop the owned process tree, including study descendants. Keep
geometry preparation and hierarchy jobs serialized with numerical jobs: preparation binds source
identity before a queued solve starts, and concurrent coordinators would exceed the chosen budget.
Execution settings and observed native pools are recorded as result/export provenance without
changing the physics configuration hash. A saved-state restart must reuse its compatible source
mesh even if the local CPU budget changes.

## Adding physics

Add a typed configuration model and explicit schema decoding. Keep constitutive evaluation in
`physics`, local matrices in `solvers`, and orchestration in `simulation`. A backend never imports
Qt. Support cancellation at assembly/iteration checkpoints and report nonconvergence. Add an
analytical reference, discrete power identity and negative/singular-setup tests before adding UI.

First-order fields are nodal scalars or per-triangle constant gradients/currents. Distinguish
volumetric current density from sheet current. Preserve exact contact and boundary integration.
Changing a solver field shape requires coordinated archive, plotting and probe updates.

## Adding geometry

KLayout owns database units and hierarchy. Geometry outside it is SI Shapely. Add a representative
GDS/OASIS case with arrays, transforms, holes and nonrectangular boundaries to `tests`. Preserve
global region tags and exact line-edge support. Shared x/y nodes never imply interlayer contacts.

## Parameter studies and constrained optimization

`set_parameter(project, 'layers.metal.thickness', value)` resolves dataclass fields and list IDs
without executing expressions. Sweep inputs are cloned; failed rows remain visible with their
errors. `optimization.run_experiment` uses declarative routing recipes, immutable
scenarios, physical measurements and equal-service checks. It always includes the
baseline and records explicit failures. Coarse Pareto candidates are provisional;
only mesh-refined improvements that pass actual GDS/OASIS reimport solves can be
exported as verified finalists. The backend imports no Qt. See
[power routing and optimization](POWER_ROUTING.md) for public commands, data
contracts, persistence, empirical mesh uncertainty and supported scope.

Mesh studies accept `profiles=[{"layer_id": "metal", "field": "potential",
"start": [1e-6, 5e-6], "end": [99e-6, 5e-6], "count": 201}]` with SI coordinates.
The CLI accepts the same list in a JSON file via `refine --profiles cuts.json`.
Each run records per-terminal currents, scalar integral changes and sampled profiles;
export writes per-cut CSV/PNG/SVG. Relative profile changes use only spatial samples valid
in both consecutive runs and report their count. An absent or zero reference norm does not
produce an invented relative error.

`meshing.refinement.suggest_refinements` creates configuration-bound sizing plans
shared by the Mesh inspector and `mesh-plan`. `mesh_refinement_study(...,
local_plan=plan, selected=ids)` uses 2–4 local factors instead of global sizes,
preserves physical averaging lengths, screens resources and executes levels
serially. [Local refinement](local-refinement.md) documents the geometric
heuristics, exact support, current-sharing definition and memory-estimate limits.

`time_step_convergence_study(project, [1e-5, 5e-6])` holds saved output cadence
fixed and disables periodic early stopping so comparisons share a physical end
time. It tracks peak temperature/time and cumulative input/output/stored energy
and conservation error. CLI: `time-refine PROJECT --steps 1e-5,5e-6`.
Transient profile requests can include `time_s` and `region`; reports retain
requested and actual retained time, and suppress profile deltas for unmatched times.

`run_project(project, initial_result=source, resume=False)` starts a new transient
from a compatible temperature field. `resume=True` continues absolute time and
cumulative energy from a saved transient. The result configuration records a
saved/resume initialization marker and field digest. To replay that experiment,
supply its first retained frame; omitting the required source raises an error.
Use the result's configuration when saving, since it includes bound source
identity. CLI and desktop perform that binding and replay automatically.

Connectivity preflight evaluates driven current and voltage constraints at both
sides of each event-grid point. Pulse edges and PWL knots make each interval
linear for these constraints, so interior violations are not missed by a coarse
integration or output interval.

The concise offline guide is `src/etlayout/data/user-guide.html`, loaded through
`importlib.resources` and included in wheels and frozen distributions. Update it
with the desktop guide and handbook when control names or workflows change.

## Release process

Follow the [owner-only maintainer procedure](maintainer-guide.md) and
[licensing guide](licensing.md). Source, binary, dependency corresponding-source,
notices and replacement/debugging requirements must be checked for the exact
public commit and artifacts. Historical pre-public tags and binaries are not
public MPL releases. Use a new unambiguous version for any public binary.

The manual [Windows candidate workflow](../.github/workflows/windows-package.yml)
can build and execute frozen candidates, but uploads validation evidence only.
Its read-only token cannot publish a release. Public binary redistribution stays
blocked until the separate compliance gate is satisfied. Owner publication is
local, manual and staged through an inspected draft; no automatic mirror, tag
trigger, package publisher or long-lived publishing token is used.

## Report and CSV output contract

`etlayout.io.reporting` owns `prepare_report_context` and `render_report` for both
GUI and headless HTML reports. Presentation-specific map generation stays in the
existing exporters. The context resolves foreign physical setups to the recorded
configuration, copies annotation diagnostics, retains selected-frame and whole-run
provenance, and leaves source fields and identities unchanged. Metadata mappings
and optional snapshot times are validated before rendering. Nonfinite diagnostic
values are shown as unavailable; numerical exports are not rounded to report text.

Use `html_text` for every metadata value, `html_table` for textual tables and
`html_image` for generated PNG bytes and textual captions. `html_document` accepts
renderer-owned markup only. Do not interpolate a project string into its body,
styles, attributes, URLs or filenames. The shared CSP permits embedded data images
and the renderer's inline stylesheet, with other resource classes disabled.
`safe_filename` and the exporter's collision allocator retain portable artifact
names; image URLs never come from metadata. The historical `_safe` import remains
an alias for Python compatibility.

Use `csv_writer` / `csv_dict_writer` for every application CSV writer, including
headers, GUI tables, power reports and studies. They protect *string* fields and
never coerce numeric objects to text identifiers. The low-level
`spreadsheet_safe=False` switch is for deliberate machine-only consumers and is
not exposed as the application's spreadsheet default. JSON/NPZ/VTU are the normal
lossless machine exchange formats. JSON material export is unchanged; its material
property CSV path is an importer, not an output writer.

Reports remain local files with recorded design metadata; CSP is not redaction,
filesystem isolation, or a guarantee that every spreadsheet/import tool will
retain text typing after a user edits or resaves the file. Browser and spreadsheet
software should remain current. No HTML or CSV content is executed during tests.

## Project resource API

`inspect_project(path)` parses the selected container and returns `ProjectInspection`
without probing referenced layouts. `resolve_project(inspection, external_policy=...)`
resolves that exact metadata/container. `load_project(path)` preserves its two-value
return contract and composes those stages with a local-project-root default.
`ExternalResourcePolicy` supplies distinct `allow_outside_root`, `allow_network`, and
per-resource approval keys. A trusted caller may supply `allowed_root` for an explicitly
selected artifact workspace; optimization uses this bounded scope for its retained
source and candidate JSON. It does not grant network access or arbitrary outside files.
Never infer approval from saved project metadata.
`load_run_history` applies the same policy to every historical resource. Geometry and
simulation entry points reject projects deliberately left unresolved by the GUI.
`inspection.for_project(project)` copies current persisted metadata while retaining the
inspected archive bytes and historical declarations. The GUI uses this same-document
review path to preserve unresolved edits. Physically incompatible current fields are
detached; historical runs continue to load against their original recorded setups.

The CLI `etlayout project-info PROJECT` prints metadata/resource declarations without
opening referenced files. Calculation, validation and power commands fail closed;
use `--allow-outside-root` deliberately for files outside the project directory and
`--allow-network` separately for network locations. These flags also apply to the GUI
launcher and comparison/initial-state inputs. `project-info --allow-network PROJECT`
permits reading a network project container, without resolving its referenced files.
A Python caller that previously loaded JSON with absolute external references must
now supply the corresponding explicit policy. Portable embedded archives need no grant.

Archive schemas 1 and 2 remain readable. Unsafe member names, symlink entries, unknown
versions, aggregate expansion budgets and invalid declared layout hashes are rejected.
Configuration/result identity checks hash stored metadata without probing its declared
paths; authorized current layout content remains independently verified.

Direct `export_finalist` calls using candidate JSON outside its source directory must
supply `resource_policy=ExternalResourcePolicy(allowed_root=experiment_directory)`.
The shared GUI/CLI workflow derives this bounded grant from the user-selected
experiment directory, never from a saved history declaration alone.
