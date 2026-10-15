# Desktop design and state ownership

Maintain the native PySide6 shell: a persistent project/analysis/save header,
seven numbered workflow steps, a large central canvas, a contextual inspector
and an actionable Issues/Jobs/Logs/History drawer. Blue marks interaction; warning
and error colors describe actual evidence. Keep model details in task groups and
advanced fields accessible without crowding the canvas.

Use native widgets, readable engineering units and the existing light/dark theme.
Do not render concept images as backgrounds or fabricate field values. Compact
1280×800 layouts use scrolling and splitters. Verify readable screenshots,
keyboard focus and glyph availability when changing labels/icons; screenshot
creation alone is not visual acceptance.

## Shared state and transitions

`WorkspaceDocument` owns transactions on the working project. The embedded power
workbench must not create a second editable project. `PendingSelection` owns the
explicit purpose and canonical requested/effective support. Domain, crop,
measurement, keepout and contact intent survive drawing, numeric entry, existing
geometry and resize routes. Domain construction is not clipped to the old ROI;
physical support clipping must show both requested and effective geometry.

Draft transitions use the shared Apply/Discard/Stay policy. Apply validates the
complete draft; Discard restores applied values and cancels pending selection;
Stay leaves editor, canvas, working setup and historical context unchanged.
Resolve drafts before Save, Run, navigation, context replacement and close.
Cancellation and immutable worker snapshots remain part of the transition contract.

History inspection is read-only and preserves the working project, undo stack,
selected result and view state. Return restores that context. View crops,
visibility, palettes and ranges must not alter physical hashes or invalidate a
mesh. Geometry/worker failures belong in the visible issue drawer with a meaningful
message and an Open/Locate action when evidence permits it.

## Scientific presentation

Keep analysis type prominent and editable while preserving dormant settings.
Mode changes update prerequisites without implying a solve. Distinguish current
setup, selected-run identity, solver convergence, mesh verification and constraints.
Use explicit layer/support/time scope in maps, probes and exports. Unsupported
controls should explain their limits rather than produce an optimistic status.

Color-scale ticks must distinguish distinct values, including narrow temperature
ranges. Increase significant-digit precision from the actual ticks; use endpoint
rows when compact labels would collide. Never round or rescale the scientific
arrays to make labels fit. Automatic temperature-rise scales account for the
floating-point resolution of the absolute temperature and reference subtraction.
If that variation is unresolved, pad the display range and explain why; retain
raw statistics, exports and explicit manual ranges.

Study charts use a contextual vertical viewport for constant values and changes
within 4096 floating-point steps (also requiring a tiny relative span). This
prevents accumulated summation noise from looking like a large mesh trend. It is
a display choice, not an uncertainty bound: retain the original curve points,
failed-point markers, scalar metrics and CSV values. Ordinary, signed and tiny
resolved signals keep normal automatic scaling; users can zoom further.

Physical terminal/callout anchors retain their coordinates. Reflow screen-sized
labels on zoom, pan, visibility and window changes; suppress labels that cannot
fit while keeping markers and tooltips, then restore them when space is available.
Scale cosmetic outlines down for narrow features so they remain visible beneath
contact and gate outlines.

The [workflow guide](desktop-workflow.md), [command ownership map](workflow-commands.md)
and [scientific acceptance contracts](scientific-acceptance.md) are maintained
requirements for ordinary contributors. Logs, repeated screenshots, coverage
outputs and build receipts belong in CI/release artifacts, not in tracked docs.
