# Implementation status

This source checkout is **0.4.1**, retaining the established desktop, CLI and
file compatibility. Check [Releases](https://github.com/mmensing/electrothermal-layout-studio/releases)
for actual Windows downloads; older binaries do not acquire new source tools.
See the [versioned capability catalog](features.md), [0.4.1 notes](releases/0.4.1.md),
[engineering notes](releases/0.4.x.md) and [validation](validation.md) for scope,
evidence and reproduction. About and `etlayout build-info` identify this
installation; recorded results retain their original implementation receipt.
Historical receipts describe their original geometry and environment; they are
not automatically measurements of the sanitized public edition.

## Current capabilities

| Area | Delivered behavior | Reference and regression coverage |
| --- | --- | --- |
| Layout and hierarchy | GDS/OASIS import; cell and instance transforms; arrays, holes, Boolean regions, live excluded masks and ROI clipping; paged hierarchy and background preparation | [Geometry](geometry.md); `test_geometry`, `test_geometry_audit_fixes` |
| Layout polygon editing | Drag/numerically edit exterior/hole vertices, insert/delete points and translate whole polygons; configurable DBU-multiple snapping, invalid-draft rejection and affected contact/measurement/device/routing support preview; verified immutable copies, sibling-placement isolation, shared Apply/Discard/Stay and Undo/Redo, portable embedded reopening | [Layout editor](layout-editor.md); `test_polygon_edit`, `test_layout_editor`, `test_edit_operations`, `test_layout_editor_operations` |
| Physical stack and materials | Independent thickness-averaged electrical/thermal sheets, explicit contacts/vias and thermal links, editable constant/linear/polynomial/tabulated properties, anisotropy, SQLite library and project material snapshots | [Physics](physics.md); `test_materials`, `test_electrical`, `test_thermal` |
| Connectivity and mesh | Edge-connected networks; voltage/current/density/floating/gate terminals; Kelvin references; conforming Gmsh mesh with layer/contact/corner refinement, quality and resource budgets | [Numerics](numerics.md); `test_connectivity`, `test_meshing`, `test_planar_references` |
| Steady calculations | Selectable electrical, prescribed-source or Joule-heated thermal, and self-consistent electrothermal operation; semiconductor and local-bias HEMT sheet laws; reaction-current, power and heat-balance diagnostics | [Workflows](handbook/workflows.md); `test_electrical`, `test_thermal`, `test_coupled`, `test_hemt` |
| Transient and pulses | Separate steady/transient selection; backward-Euler thermal storage with quasi-static electrical states; pulse/PWL drives, retained peak fields, uniform/steady/saved initialization, continuation, periodic detection and time-step studies | [Transient handbook](handbook/transient.md); `test_transient*` |
| Results workbench | Explicit layer/region selection, synchronized comparison panels, colormaps/ranges, histories and profiles; separate frame/run peaks, independent engineering evidence and exact same-support field/heat/temperature inspection | [Visualization](visualization-workbench.md); result visualization and transient exploration tests |
| Studies | Parameter sweeps, design comparisons and mesh/time refinement; requested/applied values, retained failed rows, separate failed markers and gaps in accepted trends; fixed physical horizon during transient refinement | [Guided workflow](desktop-workflow.md); `test_workflows`, `test_study_execution`, `test_transient_studies` |
| Power interconnects | Scoped physical metrics, individual contact loading, finite-feed ports, passive probes, device groups, reservoir heat flows, disjoint loss ledger and fixed-state passive multiports | [Power guide](POWER_ROUTING.md); `test_power_solver*`, `test_power_measurements` |
| Routing experiments | Protected GDS/OASIS source edits, bounded bus/feed/strap/via recipes, equal-service scenarios, resumable failures, separate coarse/refined Pareto plots and verified exported finalists | [Power guide](POWER_ROUTING.md); `test_power_routing*`, `test_power_optimization*`, `test_power_ui`, `test_power_cli` |
| Responsive execution | GUI/main-thread widget updates separated from spawned calculations and result deserialization; bounded larger FEM assembly threads and parallel study processes; actual worker provenance and measured Jobs stage timings; exact batched power reporting with cancellation; serialized preparation before queued solves | [Performance](performance.md); `test_reporting_performance`, `test_gui`, `test_execution*`, `test_parallel_fem`, `test_study_execution`, `test_gui_state_regressions` |
| Reproducibility and exports | Portable ETL archives with embedded layouts, immutable run configurations, typed JSON/non-pickled arrays and hashes; aggregate archive limits with atomic replacement; CSV/JSON/VTU/PNG/SVG/HTML exports with layer/region/time context | `test_persistence`, `test_archive_budgets`, `test_transient_io`, `test_transient_export_context` |
| Desktop and help | Seven numbered workflow stages, contextual inspectors, purpose-based panels, unit-aware forms, inline validation, undo/redo, recovery, light/dark themes, saved workspaces, packaged offline Help and repository handbook | [Guided workflow](desktop-workflow.md), [handbook](handbook/README.md); desktop acceptance scripts |
| Examples and distribution | 40 portable reference projects: E1–E19 teaching references, E20–E24 parameterized power-device families and E25–E40 analytical/device/package experiments; coplanar GaN combs, insulated gate networks and protected SiC margins; compact/reference/large presets and versioned benchmark provenance; Python API/CLI and Windows GUI/console package | [Gallery](examples.md), [Power examples](power-examples.md), [Windows packaging](windows.md) |

Hard [temperature limits](temperature-limits.md), [thermal-only films and heat
placement](thermal-stacks.md), [physical local meshing](local-meshing.md) and
[engineering evidence](engineering-results.md) are integrated with the normal
GUI/CLI and archives. [Named scenarios](thermal-example-scenarios.md),
[device studies](device-engineering-examples.md) and [finite-contact TLM](contact-extraction.md)
retain original references and state their model limits. Scientific source changes
require both source-quality and scientific-regression; complete main validation
and frozen/clean-machine evidence remain separate scopes.

The [desktop workflow](desktop-workflow.md) describes the current shell.
[Scientific acceptance](scientific-acceptance.md) defines measurement, export and
via-rule contracts. [Validation](validation.md) describes reproducible checks;
CI/release artifacts record measured outcomes for their exact commits.

Historical pre-public verification is condensed in the
[0.3.1 engineering notes](releases/0.3.1.md). Current public CI artifacts must
identify their exact public revision. No private CI/PR links are required to
install or inspect this source edition.

No GPU backend is supported. Execution settings default to at most four available
logical CPUs and control larger independent FEM assemblies, native library
threads and independent study processes. Small batches and SuperLU factorization
remain serial. Device laws remain the documented 2.5D
research models; see [physics](physics.md) for excluded effects and calibration.


[Content-addressed archives](design/content-addressed-archives.md) are implemented
as an explicit schema-3 option. Schema 2 remains the default writer and recovery
format; older archives and the public compatibility loading APIs remain eager.

[Metadata-first history](design/lazy-run-history.md) is an opt-in schema-3 desktop
mode with worker-based opening and exact-frame loading, bounded immutable array
caching, explicit cache leases and complete saves/exports of unloaded records.
Enable it through **Edit → Archive and history settings…** for the next opening.
The [user guide](user-guide.md#on-demand-history-for-schema-3-projects) explains
cache limits, disk snapshots and explicit complete-history consumers. Storage
changes do not establish mesh verification or alter numerical acceptance.
