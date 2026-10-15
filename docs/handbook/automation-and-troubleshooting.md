# Automation and troubleshooting

[Handbook](README.md) · [← Results and validation](results-and-validation.md)

Run commands from the repository root with the installed environment. The CLI
and Python entry points share the desktop solver; headless calculations import
no Qt. All numerical API inputs use SI units.

## Command-line workflow

```powershell
# Inspect source hierarchy and masks.
.\.venv\Scripts\python.exe -m etlayout inspect examples/E12_via_ladder.gds

# Inspect this computer's logical CPU count and native thread-control support.
.\.venv\Scripts\python.exe -m etlayout capabilities

# Validate the complete physical setup and connectivity.
.\.venv\Scripts\python.exe -m etlayout validate examples/E12_via_ladder.etl

# Prepare a saved mesh without solving fields.
.\.venv\Scripts\python.exe -m etlayout mesh examples/E12_via_ladder.etl --output results/e12-mesh

# Solve the original or a modified setup, leaving the source file unchanged.
.\.venv\Scripts\python.exe -m etlayout solve examples/E12_via_ladder.etl --mode electrothermal --set terminals.drain.value=0.2 --output results/e12-200mA

# Skip plot generation when only numerical data are needed.
.\.venv\Scripts\python.exe -m etlayout solve examples/E1_straight.etl --mode electrical --no-plots --output results/e1-data
```

`--set PATH=VALUE` accepts a safe configuration path and a JSON value, falling
back to text for string settings. Paths through ID-bearing lists use the ID:
`layers.metal.thickness` and `terminals.drain.value` are examples. Use JSON `true`
and `false` for booleans. A repeated `--set` applies another override. Solver
output and exports record the modified configuration; the input archive is not
rewritten by these commands.

`--operation steady|transient` explicitly overrides the saved time operation;
`--mode` independently selects the physics. Electrical mode supports Steady only.
For pulse calculations, the `transient` command enables transient operation and
accepts timing, initialization and periodic-detection flags. `time-refine --steps
1e-5,5e-6` compares decreasing maximum steps with fixed output cadence. See the
[transient command examples](transient.md#saved-starts-periodic-detection-and-time-refinement)
for warm starts, continuation and automatic replay of saved source states.

Use `python -m etlayout --help` or a subcommand's `--help` for its complete
arguments. Solve returns zero for convergence and 2 for a numerical/setup error
or nonconverged solve. Study commands retain failed rows and return nonzero if
any study point fails. Check exit status and row errors in automation; a written
output directory alone does not imply success.

## Python: solve, sample, export and reopen

Save this as `run_example.py` and run it from the repository root:

```python
from pathlib import Path

from etlayout.io.export import export_results
from etlayout.io.project import load_project, save_project
from etlayout.simulation.runner import run_project
from etlayout.visualization.sampling import FieldSampler

project, _ = load_project("examples/E1_straight.etl")
project.solver.mode = "electrical"
result = run_project(project)
if not result.converged:
    raise RuntimeError([d.message for d in result.diagnostics])

distance, voltage, coordinates = FieldSampler(result).profile(
    "metal", "potential", [0., 5e-6], [100e-6, 5e-6], count=101
)
print(result.metrics["resistance"], "ohm")
print(voltage[50], "V at strip midpoint")

output = Path("results/python-e1")
output.mkdir(parents=True, exist_ok=True)
save_project(project, output / "saved.etl", result)
export_results(result, output / "potential", layers=["metal"],
               field="potential", colormap="viridis", limits=(0., .02))

saved_project, saved_result = load_project(output / "saved.etl")
assert saved_result is not None
assert saved_result.configuration_hash == result.configuration_hash
```

For a region-filtered sample, pass a supported region key to `FieldSampler`, for
example `region="interface:via12"` on E12's `m1` or `m2`. Discover current keys
with `region_options(result, layer)` from
`etlayout.visualization.support`. Connected islands have `component:1`,
`component:2`, etc.; their numbering is local to the result's geometry. Preserve
the run identity when saving such selections.

## Repository organization

| Module | Responsibility |
| --- | --- |
| `models` | Typed configuration, mesh and result contracts |
| `geometry`, `connectivity` | Layout parsing, selection, geometry preparation and net analysis |
| `meshing` | Conforming planar triangles and exact selection tags |
| `materials`, `physics` | Property evaluation and transport laws |
| `solvers` | FEM assembly, constraints and electrical/thermal solution |
| `simulation` | Validated orchestration, caching, sweeps, refinement and analysis |
| `visualization` | Field support, interpolation, colormaps and difference support |
| `io` | Portable project archives and numerical/graphic exports |
| `gui` | Desktop controls, worker processes and inspection workflows |

See the [architecture reference](../architecture.md) for extension interfaces.
Prefer `run_project` for an ordinary configured experiment: it verifies the
source layout, prepares geometry and mesh, captures diagnostics and binds the
result to its setup. Low-level solver calls accept externally supplied arrays;
retain those arrays and their matching mesh if they are needed to reproduce the
experiment.

## CPU execution and Python studies

The default is a total budget of four logical CPUs, or the available count when
smaller. **Edit → Execution settings…** and the CLI can lower or raise it
within that count. The default study-worker limit equals the CPU budget; reduce
it when each study point needs substantial memory.
This is a cap, not a guarantee that every solver stage uses every CPU. A single
run uses worker threads for larger FEM assemblies and bounds native numerical
and meshing threads. Small batches and SuperLU factorization remain serial.
Results record actual assembly worker activity separately from the budget. Parameter sweeps, design
comparisons, mesh studies and time-step studies distribute independent points
among spawned processes. The total budget is divided among the actual workers;
each gets at least one thread, and workers cannot exceed the budget.

CLI execution flags follow the calculation or study subcommand:

```powershell
.\.venv\Scripts\python.exe -m etlayout time-refine examples/E17_pulsed_thermal.etl --cpu-threads 4 --study-workers 2 --steps 2.5e-5,1.25e-5 --output results/e17-study
```

Execution settings are machine preferences. Results record them as execution
provenance; changing them does not change the project's physical identity.
Parallel studies preserve input order and per-point errors, and cancellation
terminates their owned worker processes. Different generated triangulations can
occur at different native thread counts; compare physical profiles and integral
quantities, rather than array indices, across independently generated meshes.
GPU acceleration is not included. CPU preferences apply to future desktop jobs;
they do not interrupt or resize an already running worker pool.

The [measured CPU pair](../../benchmarks/e21-cpu-0.3.1.json) records four actual
assembly threads with identical fields on one historical mesh. Numerical time
was 63.851/59.339 s for one/four CPUs, while full calculation/reporting took
104.216/109.805 s. More CPUs did not improve that complete run. See
[current validation](../validation.md) for source and packaged checks.

Use a main guard in scripts that start studies because Windows uses process
spawning. This example also resolves an archive's original saved initial field:

```python
from etlayout.io.project import load_project
from etlayout.models import SweepSpec
from etlayout.simulation.execution import ExecutionSettings
from etlayout.simulation.sweep import resolve_study_initial_state, run_sweep

def main():
    project, archived = load_project("examples/E17_pulsed_thermal.etl")
    initial = resolve_study_initial_state(project, archived)
    rows = run_sweep(project, SweepSpec("transient.drives.0.waveform.high",
                     [2.5e5, 5e5, 1e6], "thermal"),
                     execution=ExecutionSettings(), **initial)
    for row in rows:
        print(row.value, row.error or row.metrics["transient_peak_temperature"])

if __name__ == "__main__":
    main()
```

For a serial study, pass `ExecutionSettings(study_workers=1)`. Low-level GUI or
notebook wrappers should call a guarded script when they cannot provide an
importable spawn entry point. `compare_projects` accepts aligned `initial_results`
and `resumes` lists; the other study APIs accept `initial_result` and `resume`.
Missing or incompatible saved-state dependencies are diagnosed before solving.
Changing the mesh requires a different initialization choice because arbitrary
field interpolation is not supported.

`estimate_project_archive(project, result, history=retained_runs)` from
`etlayout.io.project` reports expanded bytes, metadata size, entries, limits and
`fits_budget` before saving. Saved runs share the global archive budget. The save
path checks the temporary archive again before replacing an existing file.

## Diagnose by symptom

| Symptom | Check and next action |
| --- | --- |
| An overlapping pair of metals is disconnected | Declare an enabled electrical interface on a finite common area; verify its mask intersects both sheets |
| A network has no reference or is singular | Inspect isolated components, terminal selection and current balance; add a justified reference rather than an arbitrary conductive bridge |
| Thermal mode stays at ambient | Check prescribed heat sources and the one-way Joule option; plain thermal mode does not infer heating from terminal bias |
| A thermal component lacks a sink | For a steady solve, add the intended fixed temperature or heat-loss path; inspect thermal interface connectivity |
| A result field vanishes after changing layers | Check that the quantity exists on that layer, then region support and logarithmic positivity |
| A map clears after editing limits | Ensure finite Min < Max; log limits must also be positive; use Auto to reset |
| A line profile has gaps | Check physical holes, filtered regions and unsolved support; gaps can be the correct answer |
| A gate voltage barely changes HEMT current | Inspect the local gate mask and ungated bypass paths; compare E16's full-width gates with E7/E8 |
| An absolute current peak grows under refinement | Inspect sharp corners and contact edges; compare integral values, fixed-location profiles or a physically rounded geometry |
| Coupled solve fails or material warnings appear | Review material validity, load level and sink/stack assumptions; examine residual history before changing damping or iteration limits |
| A previous result disappears after an edit | Physical changes invalidate its compatibility; inspect the recorded run in history or solve the new configuration |
| An export contains unexpected filenames | Use `manifest.json`, which preserves original layer IDs and resolves filename collisions |
| A reopened figure cannot reproduce a calculation | Save the full `.etl` and numerical export; figures contain presentation, not every source array and input |
| A saved-state study cannot start | Open the result archive or bind its matching source field; geometry, mesh supports and thermal properties must match |
| A resumed history starts with nonzero accumulated energy | Read its JSON/CSV sidecar: energy is cumulative from the original run, while temperatures and peaks describe the recorded segment |
| A portable save exceeds its budget | Remove unneeded saved runs, or rerun with a longer saved-field interval and remove the superseded larger run before saving. Changing the interval affects future solves; it does not thin existing results. The limit covers expanded nested data, regardless of ZIP compression |
| Raising the CPU budget does not speed up a single solve | Inspect recorded assembly CPU/wall time and worker IDs; small batches, geometry work and SuperLU factorization remain serial. Compare the same mesh and physical inputs, and check memory before increasing study workers |
| A parallel Python study reports process startup errors | Put the script's study invocation under `if __name__ == "__main__":`; use one study worker for an interactive host without a spawn entry point |

Warnings carry object IDs and, where available, bounds. Use those to locate the
input that produced the warning. A warning about a full-domain interface being
clipped can be expected, as in the worked examples; an unintentionally missing
contact area is a modeling error. The diagnostic text is context to investigate,
not a universal pass/fail decision.

## Maintain the handbook

When changing a documented model or workflow:

1. Update the relevant page and its links to source/API definitions.
2. Run `docs/handbook/verify_walkthroughs.py` if example setup, mesh or solver
   behavior changed; compare and explain changed values before replacing the
   evidence receipt.
3. Verify executable snippets and links. Keep documentation of new operation
   modes with the implementation that makes them available.
4. Distinguish regression checks, analytic verification, mesh convergence and
   experimental calibration. Record what was actually tested.

The broader [developer guide](../developer-guide.md) and
[contribution guide](../../CONTRIBUTING.md) describe development checks.

[← Results, exports and validation](results-and-validation.md) · [Handbook home](README.md)
