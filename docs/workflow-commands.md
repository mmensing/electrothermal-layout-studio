# Command destinations and shared state

`gui/guided.py:GuidedWorkspace` registers the top-level commands using the
Qt-independent `workflow.CommandRegistry`. `gui/document.py:WorkspaceDocument`
owns transactional access to the window's working project; guided inspectors and
the embedded `PowerWorkbench` use that document. Existing numerical actions
continue to call the established `MainWindow`/worker services. Component-local
actions (table sorting, plot controls and form widgets) retain their existing
component handlers; they do not create a second project.

| Purpose/access | Current destination | Shared handler / command |
|---|---|---|
| New/open/import/save/save as/duplicate/relink, examples | File; header project access | `file.new/open/import/save/save_as/duplicate/relink`, `example.<id>` → existing `MainWindow` methods |
| Undo/redo; simulation execution preferences | Edit; Run inspector CPU access | `edit.undo/redo/preferences` → existing undo stack and `edit_execution_settings` |
| Original layout vertex editing | Canvas → Edit layout; Edit → Edit layout polygon vertices; Ctrl+Shift+E | `edit.layout` → `LayoutEditor`, cancellable `layout:load/edit` workers, immutable source copies and shared document transaction |
| Selected-object editing/deletion | Edit; contextual inspector | `edit.selected/remove`; document transaction and explicit removal confirmation |
| Cell/instance hierarchy, mask/layer/net browser | View → Hierarchy / layers / nets | `view.browser`; browsing is separate from explicit Layout scope application |
| Simulation ROI, numeric bounds, domain use/clear | Layout & Region inspector | `GuidedWorkspace.begin_selection`, pending selection commit, explicit scope action |
| Display ROI | View → Draw/Clear view crop; Layout domain-type choice | `view.crop/clear_crop`; existing display-only state |
| Connected selection/path tracing | Model → Select connected geometry / Trace path between terminals | `model.connected/trace` → existing selection and trace services |
| Physical stack/materials and layer mapping | Layers & Materials; Model → Materials and library | `model.layers/materials`, typed inspector; `model.expert` retains complete tables |
| Saved technology mapping proposal | Layers & Materials → Technology templates… | `technology_templates` portable save/load, mask remap and layer inclusion, explicit Preview/confirmation, shared document transaction |
| Terminals/interfaces/gates, physical ports/probes | Contacts & Connections purpose chooser | `model.connections`; shared object editor and graphical landing session |
| Electrical, thermal and heat-source conditions | Operating Conditions groups | `model.conditions`; shared project object editors |
| Mesh sizes/refinement/solver settings/presets | Mesh & Checks; Simulation → Numerical settings | typed mesh/solver inspector, explicit Mesh / solver preset button |
| Transient settings/waveforms/initialization | Operating Conditions; Simulation → Time and load settings | `simulation.pulse` → `edit_transient` |
| Saved-state start/resume | Simulation → Start transient from saved state / Resume saved transient | `simulation.restart/resume` → `run_saved_state` |
| Validate / mesh / run / cancel | Simulation; stage primary actions; F6/F7/F5/Shift+F5 | `simulation.validate/mesh/run/cancel` and current shared preparation/worker path |
| Diagnostics/log/progress/history | Issues / Jobs / Logs / History drawer | issue Locate/Fix routes use backend object/stage evidence; `view.drawer` (Ctrl+J) |
| Field/layer/support/time/range/colormap | Results inspector; Quantitative results | authoritative `MainWindow` result selection and existing result-panel controls |
| Historical inspection/return | Results → Inspect saved runs / Return to working setup | `results.history/return`, immutable inspection context |
| Probe/profile/cross-section/branch current/hotspot | canvas tools; Results; Cross-section / Profiles pages | existing viewer tools; `results.profile/branch/hotspot` |
| Layer/run maps, difference/configuration comparison | Results → Compare field maps / Compare layers / Load field difference / Compare run configuration | `results.compare/layers/difference/config` |
| Scoped measurements/device groups/contact loading/loss budget | Results → Contacts, devices and loss budget; embedded Analysis | `results.analysis`, same `PowerWorkbench` document and worker |
| Measurement regions/routing keepouts | Model → Define measurement support / Define routing keepout | `model.measurement/keepout`, shared pending selection |
| Reanalyze retained fields | embedded power workspace → Analysis | `power:reanalyze` worker; compatibility check, copied result, reporting-context hashes |
| Sweep, mesh/time convergence, compare projects | Studies | `studies.sweep/mesh/time/compare` → preserved study dialogs and background jobs |
| Power setup/routing/optimization | Studies → Routing & optimization; study workspace | `studies.routing`, embedded workbench, typed recipe/scenario editors |
| Passive multiport extraction and circuit exports | embedded Analysis | preserved `power:multiport` worker/export service |
| Study results | Studies → Saved study results | `studies.saved` and existing study panel |
| Numerical/displayed-field/map/report exports | Results → Export task; explicit export menu entries | `results.export`, `file.export_full/export_field/export_map/export_report` → preserved exporters |
| Selected-run project archive | Results → Export task → Selected run project archive | `export_run_archive` exports the selected result's original configuration; File → Save retains the working setup/history |
| Candidate layout export | Routing / Experiments | preserved guarded export/reimport verification; not a screenshot export |
| Fit/pan/zoom, layers, visibility, overlays | canvas toolbar, layer controls; View → Fit layout / Fit selection | `view.fit/selection`; viewer operations preserve physics |
| Theme/workspace persistence | View; compact Workflow / Inspector buttons and status strip | `view.left_panel/right_panel/drawer/focus_canvas/maximize_canvas`; `view.workspace_save/restore/reset`; application-local `WorkspacePanels` |
| Help/about/workflow limits | Help; contextual stage assistance | preserved offline help and limitations |

Keyboard stage navigation is **Ctrl+1–7**, fit is **Home**, drawer visibility is
**Ctrl+J**, independent sides are **Ctrl+Shift+L/R**, focus canvas is **Ctrl+Shift+F**,
and maximize/restore is **Ctrl+Shift+M**. **Shift+Home** explicitly fits selection/ROI;
the established File/Undo/Redo shortcuts remain. Local text/form
editing retains focus; global mutation/run shortcuts must not consume ordinary
typing. Escape cancels a pending graphical selection. The selection and workflow
tests exercise these concerns separately from numerical equivalence.

The old modal power workbench constructor remains as a compatibility entry point
for callers/tests. The desktop's main study route embeds it and shares the document.
Small file, confirmation, saved-state and specialized study dialogs remain where
their existing task warrants a dialog. There is no second hidden editable project
behind the embedded main study workspace.
