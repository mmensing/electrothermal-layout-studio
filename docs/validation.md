# Validation and reproducible verification

Tests protect scientific behavior and workflow contracts. A higher test count or
coverage percentage does not establish physical accuracy or usable desktop behavior.
Maintain independent analytical references, conservation, failure cases and persistence.

## Evidence scope

Only receipts tied to an exact public source revision establish checks of this
edition. Sanitization, MPL metadata and packaging changes require fresh source,
CLI/save-reopen, resource and native GUI checks. A green PR fast check is not a
full numerical, packaged-build or physical calibration claim.

Pre-public 0.3.1 verification recorded 1,294 source tests, 86.26% statement and
75.94% branch coverage, native 100%/125% inspection of 24 references, and 25
frozen executable checks. These observations remain historical; their private
commit/CI mappings and large artifacts are not part of public history. Later
engineering work added report timing, numeric geometry operations and reviewed
local refinement; see the [changelog](../CHANGELOG.md). Small maintained scientific
receipts retain original geometry, numerical values and content identities.
They are not silently reidentified as public validation.

Native source/build evidence uses Windows CPython **3.12.14**. Hosted workflows
request the official Windows x64 **3.12.10** artifact, using the same CPython 3.12
wheel lock. Keep those patch versions explicit in receipts and inspect actual
GitHub checks for the public commit; local execution does not establish a hosted
CI pass. See [runtime availability](windows.md#python-runtime-and-verification-scope).

## Source checks

Install the locked Windows dependencies and editable package, then run:

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
$env:MPLBACKEND = 'Agg'
python -m coverage run -m pytest --basetemp=.pytest_validation --junitxml=.artifacts/pytest-results.xml --durations=25
python -m coverage combine
python -m coverage report
python -m coverage json -o .artifacts/coverage.json
python -m ruff check .
```

Tests group the same collection into `fast`, `numerical`, `desktop` and `packaging`
markers using `tests/conftest.py`. PR CI runs fast and packaging contracts plus
a stable path-aware `scientific-regression` gate for physical/source/data/test
changes. Documentation-only paths retain an explicit successful lightweight check.
Main scientific changes and owner-requested validation add the complete source and
Qt desktop journeys; documentation-only main pushes avoid those expensive jobs. Main coverage includes all groups
and spawned workers. The unfiltered
command above retains all tests and is used for comparable baseline/final measurements.
No difficult modules are excluded and no physical tolerances are relaxed.

Power engineering examples reuse exact compact nominal solves. E20–E24 do not
enter the E1–E19 three-mode teaching loop; full-size and large calculations run
through the explicit [benchmark protocol](power-examples.md#budgets-and-measured-acceptance)
and manual benchmark workflow. Resource-limited/model-invalid receipts are
retained; a job completing does not certify physical or mesh acceptance.

Use `pytest -m fast`, `pytest -m numerical`, `pytest -m desktop` or
`pytest -m packaging` for focused local checks. Packaging-marked tests check
resource/identity policy; they do not establish that a frozen executable runs.
Shared numerical fixtures are immutable or deep-copied for each mutating consumer.
Shared Qt fixtures create separate working projects, settings and worker lifecycles.

The independent references include linear strip resistance/field integrals,
thermal energy balances, HEMT gate response, individual contact currents and heat,
passive port circuits, mesh/time refinement and equal-service routing experiments.
Nested/direct supports are checked on partial elements, holes and multiple meshes.
Exceedance fractions, weak-link exports and via grouping receive negative tests;
see [scientific acceptance](scientific-acceptance.md).

Parallel execution tests compare actual serial/threaded fields and delivered
service on the same immutable mesh, test exact operator-cache invalidation, and
verify that worker failures join outstanding tasks before restoring native thread
limits. The [E21 CPU receipt](../benchmarks/e21-cpu-0.3.1.json) measures one
unprofiled one/four-CPU pair on historical 0.3.0 geometry: 63.851/59.339 s numerical
time and 104.216/109.805 s calculation plus reporting. It records four active
assembly worker threads, while SuperLU/reporting remain serial. This is evidence
of actual assembly concurrency, not a promise of complete-run speedup or
validation of revised example geometry.

## Real desktop and CLI journeys

Desktop scripts use actual workers, geometry and solver fields. Native Windows
runs use QPA `windows`; CI uses offscreen Qt. These are distinct evidence types.

```powershell
python scripts/gui_acceptance.py --offscreen --output .artifacts/gui-acceptance
python scripts/visualization_workbench_acceptance.py --offscreen --output .artifacts/visualization
python scripts/workflow_ui_acceptance.py --offscreen --output .artifacts/workflow
python scripts/transient_acceptance.py --offscreen --output .artifacts/transient
python scripts/transient_workflow_acceptance.py --offscreen --output .artifacts/transient-workflow
python scripts/power_workflow_acceptance.py --offscreen --output .artifacts/power
python scripts/guided_workflow_acceptance.py --output .artifacts/native-guided --scale 1
python -m etlayout inspect examples/E1_straight.oas
python -m etlayout solve examples/E1_straight.etl --no-plots --output .artifacts/cli-E1
python scripts/power_examples_desktop_acceptance.py --output .artifacts/native-power-examples
python scripts/power_examples_desktop_acceptance.py --catalog --workflow all --solve --scale 1 --output .artifacts/native-all-examples
python scripts/layout_editor_acceptance.py --output .artifacts/native-layout-editor --scale 1
python scripts/layout_editor_acceptance.py --output .artifacts/native-layout-editor-125 --scale 1.25
```

Inspect readable native captures at compact window sizes in both themes. Verify
selection purposes, preview/commit geometry, Apply/Discard/Stay before Save and
History, historical read-only state, cancellation and visible failure diagnostics.
Vertex-edit acceptance uses actual mouse dragging, preparation/export workers,
Apply/Discard/Stay, Undo/Redo, an edited-layout solve and embedded reopening, plus
visible failures and cancellation. Use `--offscreen` for that script in CI; native
captures remain a separate visual check.

Windows offscreen journeys register existing host fonts before creating the
workspace. The shared acceptance helper writes `font-verification.json` and
rejects invalid regular/bold fonts or missing required text and unit glyphs.
Host font files are read without copying them into source or artifacts. Native
runs retain their application font and font database. Inspect the resulting
captures as well: glyph availability alone does not establish usable layout.

Capture success with missing-glyph boxes is a failed visual check. Qt scale factors
verify widget rendering, not physical-monitor DPI, mixed-monitor transitions or
screen-reader behavior. Record available screen and actual window dimensions.

Earlier captures exposed terminal-label collisions and precision-sensitive colorbar
labels. The current implementation reflows callouts and uses distinct representable
ticks; validate these behaviors at compact sizes and both Qt scale factors. Inspect
absolute temperature and the stated reference for near-zero derived temperature rise.
These display policies do not change stored field values or numerical acceptance.

`--catalog` opens all seven stages without solving. `--workflow all` additionally
validates, meshes, previews and runs every exact default through the standard
worker, saves/reopens each project and checks read-only History/restoration.
An empty `--solve` omits additional compact solves. The complete journey includes
engineering-size references and may take substantial time/memory; it is explicit
acceptance work, not the routine three-mode test loop. Inspect current/voltage,
applicable conservation and model-validity diagnostics as well as the convergence
flag. A screenshot alone cannot prove a configured nominal calculation passed.

## Windows distribution checks

```powershell
python scripts/build_windows.py
```

The builder freezes GUI and CLI executables, uses an isolated DLL search path,
runs real electrical/transient/power/routing/optimization commands and Qt worker
smokes, verifies ZIP integrity and solves from the extracted application archive.
`build/verification/` contains receipts; `dist/` contains application/source ZIPs
and checksums. These are native build-host checks, not clean-machine certification.
Frozen checks include real E1, transient, power/routing and edited-layout workflows,
not just a successful launch. A source test result cannot substitute for executing
the actual new EXE. Frozen artifacts are not publicly distributed until their
[license and corresponding-source requirements](licensing.md) are satisfied.

`packaging/resources.py` is the shared resource policy for PyInstaller, adjacent
resources and the source archive. Repackaging removes stale docs/examples files
within their explicit bundle roots, without touching binaries or licenses. Verify
actual Git tracking and final archive members; ignore rules alone cannot remove
already tracked files. No prompts, agent handoffs, mockups, conversation exports,
scratch plans or repeated coverage/log/capture bundles belong in distributions.
Retain runtime materials, examples, scientific provenance, documentation, licenses
and contributor attribution. Release evidence is uploaded separately by CI.

## Reporting results

A PR/release must name its exact baseline revision, environment, commands,
collected/passed/failed counts, statement and branch coverage, exclusions and
runtimes. Distinguish source tests, desktop acceptance, packaged execution and
clean-machine validation. Timings are host observations, not portable assertions
or guaranteed speedups. Keep raw logs, coverage and repeated captures in artifacts;
maintained docs contain the contracts and reproducible commands.


## Collapsible workspace checks

`python scripts/workspace_panels_acceptance.py --offscreen --output artifacts/desktop-workspace`
solves the configured synthetic E19 multilayer transient, then checks all panel
controls, keyboard focus, compact/full-HD windows, both themes, long names and
unchanged scientific identity, frame, range and camera. Repeat with `--scale 1.25`,
`--scale 1.5` and `--scale 2` for Qt application scaling. Omit `--offscreen` for
native Windows checks. The shared font verification rejects unreadable glyphs;
inspect the actual PNGs before treating captures as visual evidence. These checks
do not establish physical monitor DPI, mixed-monitor restoration, assistive
technology support or clean-machine packaged behavior.


## Archive and on-demand history checks

`python scripts/archive_history_acceptance.py --offscreen --output artifacts/desktop-archive-history`
solves two configured E1 operating points, writes a schema-3 archive, enables
metadata-first opening through the settings dialog and checks recorded inspection,
return to the working setup, exact transient-frame navigation, metadata step
traces and complete Save As after partial browsing. It verifies the reopened
configuration, retained histories and every saved time, and checks that the
original file is unchanged. Omit `--offscreen` for native Windows source widgets;
use `--scale 1.25` for Qt application scaling. Preferences and recovery are
isolated. File-chooser input uses a process-scoped adapter because native Qt file
dialogs have their own persistent history; this script does not certify the
native chooser, a frozen package or physical mixed-monitor behavior. Inspect the
actual captures and font-verification receipt.

Focused backend regressions are `test_content_addressed_archives`,
`test_archive_directory`, `test_lazy_history_store` and `test_history_opening`.
Desktop behavior is covered by `test_lazy_history_workflows`,
`test_lazy_transient_ui`, `test_lazy_archive_export` and the existing eager
workflow/export/continuation suites. These tests supplement the independent
analytical and scientific acceptance regressions; storage changes do not relax
their tolerances.

The opt-in `scripts/benchmark_archive_history.py` matrix is separate from routine
PR CI. Its [archive](design/content-addressed-archives.md) and
[history](design/lazy-run-history.md) contracts define the solved workloads,
complete typed equality checks, resource guards and timing boundaries. Keep raw
archives, process logs and repeated receipts in CI/release artifacts, outside Git.


## Expanded reference examples

`python scripts/verify_extended_examples.py --output .artifacts/extended-examples`
checks E25–E40 at their exact configured nominal inputs. It uses a fresh bounded
process per reference, records provenance and phase timings, and checks service,
scientific acceptance, every retained transient frame and complete schema-2/3
result round trips. Geometry determinism, exported-layout reimport and embedded
source reopening are independent portability checks. No geometry-only generation
is reported as a successful simulation, and no converged case is relabeled as
mesh verified.

The complete matrix is an explicit owner/main CI step. The numerical unit suite
shares immutable nominal results and returns deep copies to each test, while
independent analytical/current-cut tests retain distinct physical checks. The
catalog's `reference_tier` explicitly selects introductory three-mode tests;
extended examples do not enter those tests merely because they have a builder.

For the GUI, run `python scripts/power_examples_desktop_acceptance.py --offscreen
--catalog --solve --workflow E25 E33 E36 E39 --output .artifacts/desktop-extended`.
The intentionally empty `--solve` avoids the separate compact power-family loop;
`--workflow` runs the saved nominal inputs through validation, meshing, background
solve, results and read-only History. `--catalog` opens all 40 references through
all seven stages. Omit `--offscreen` for native Windows and add `--scale 1.25` for
application scaling. Inspect readable captures and font receipts. This does not
certify clean-machine deployment or physical mixed-monitor DPI.

## Native lifecycle diagnostics and measured operator phases

Use the repeatable source diagnostic without disabling Python garbage collection:

```powershell
python scripts/scientific_lifecycle_acceptance.py --cycles 8 --output .artifacts/native-lifecycle
python scripts/scientific_lifecycle_acceptance.py --offscreen --cycles 4 --output .artifacts/offscreen-lifecycle
```

The journey performs actual solved operating points, durable asynchronous recovery
and schema-3 saves, repeated historical inspection/return, transient frame/project
switching, cancellation and closing during an actual subprocess, deferred native
Qt deletion, and explicit Python GC. Its receipt records versions, phases, elapsed
time and observed memory; faulthandler records Python stacks on native faults or
long stalls. Store receipts, captures and logs as CI/local artifacts. Passing this
journey does **not** identify or prove a fix for the intermittent Qt/Shiboken
access violation observed during earlier full-suite GC.

A separate regression demonstrates a concrete ownership defect: completed parented
compute jobs formerly accumulated as native Qt children. Completion now waits for
its owned process/receiver to exit and schedules native deletion through Qt's
event loop. Tests retain readable process diagnostics after completion and verify
no native children accumulate across repeated actual workers. This resource fix
is narrower than a claim about the cause of the intermittent crash.

Execution provenance now includes independently measured `electrical_assembly`
and `thermal_assembly` assemble-call wall times alongside `sparse_linear_solves`.
They are subsets of numerical time, include material checks and operator-cache
lookups, and exclude initial space/contact/boundary preparation. They are never
estimated by subtracting solve time. The Jobs timing tooltip exposes the measured
operator phases; machine timings do not participate in physical identity.

On-demand history already uses a bounded private disk snapshot per selected
archive and a separate bounded array cache. Closing the store disposes its copy
once independent save leases finish; those leases must not be deleted to reduce
apparent retention. Abnormal process termination can leave operating-system temp
remnants. Inspect those only after all application processes/readers stop; no
automatic deletion of arbitrary archive paths is introduced.

## Fixed spatial mesh for time-step studies

Time-step convergence prepares one conforming mesh and sends a verified reference
to every study point, including parallel processes. The reference contains only
mesh coordinates, connectivity and tagged supports. It is checked against the
verified source geometry/mesh settings and exact mesh fingerprint; it never acts
as an invented thermal initial result. Uniform, saved and resumed initialization
retain their independent physical-state contracts.

Rows record `time_study_mesh_sha256`, `time_study_fixed_mesh`, the study-wide common
preparation cost, and `time_study_status`. Temporal deltas are available only for
successful, complete same-duration results on that exact spatial mesh. An
incomplete/thermally stopped point or a mesh mismatch cannot certify time-step
convergence. This prevents native mesher variations between workers from being
misreported as temporal sensitivity. Field outputs still use the requested
cadence, and equal effective step counts remain explicitly diagnosed.
