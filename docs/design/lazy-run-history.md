# RFC: metadata-first run history

Status: **design only; no lazy reader or cache is enabled by this change**.
Depends on immutable atomic saving and the [content-addressed archive proposal](content-addressed-archives.md).
The existing eager readers and archive versions remain authoritative. This proposal
changes storage access, not physical models, convergence or acceptance criteria.

## Why a separate implementation review is needed

`load_run_history` currently decodes each nested run through `load_project`.
`RunHistoryPanel.set_results` expects real `SimulationResult` objects and walks mesh,
transient snapshots and retained arrays to describe history. Inspection, comparisons,
`history_field_limits`, local traces, exports and transient initialization likewise
expect materialized component-local fields. A proxy substituted into that dataclass
would conceal I/O, failures and partial validation inside numerical code.

The archive benchmark described in the prerequisite RFC measures the current writer
and eager reader on real solved synthetic fields at 1, 10 and 50 retained records.
Those measurements are the baseline, **not evidence of lazy-loading performance**.
Repeated references are storage stress, not independent simulations. See the measured
matrix and reproduction command there; raw archives/logs remain disposable artifacts.
An implementation must repeat the same matrix before claiming an improvement.

A single fresh-process sample of the shared-layout/mesh workload measured:

| Total retained records | Current-result open (s) | Additional eager history (s) | Sampled peak process-tree RSS (MiB) |
|---:|---:|---:|---:|
| 1 | 0.303 | 0.013 | 109.1 |
| 10 | 0.424 | 2.772 | 202.9 |
| 50 | 1.006 | 14.651 | 639.1 |

These are separate reader calls, not a measured GUI startup or a lazy prototype.
The one-record history call has no historical payload. RSS includes imports, the
input pool, integrity/equality checks, save and load; it is not attributable solely
to opening. OS storage caches were not flushed and there is one replicate, so no
uncertainty interval or speedup is established. The prerequisite RFC records the
Python/Qt/host configuration, varied-content/transient matrix and exact workload.

## Current metadata-inspection measurements

The bounded two-stage inspector was also measured on the unchanged original
archives, in separate fresh processes with `np.load` blocked. It still reads the
whole selected container and recursively inspects ZIP/NPZ headers; this is **not**
a lazy reader or a claim of zero payload I/O. The inspected source is the bounded
reader in commit `0d47bc1`; its resource module is unchanged in the subsequent
widget-fixture-only commit.

| Workload | Records | Stage-one inspection (s) | Sampled process-tree RSS (MiB) | Native runtime peak WS (MiB) |
|---|---:|---:|---:|---:|
| Shared E1 | 1 | 0.023 | 47.8 | 49.4 |
| Shared E1 | 10 | 0.293 | 73.7 | 81.9 |
| Shared E1 | 50 | 1.591 | 177.8 | 251.3 |
| E19, 101 frames | 1 | 0.102 | 53.4 | 49.6 |
| E19, 101 frames | 10 | 1.941 | 110.7 | 142.7 |
| E19, 101 frames | 50 | 11.389 | 557.8 | 555.4 |

One observation per row; OS filesystem caches were not flushed. The 10 ms sampled
process tree includes launcher/import costs and can miss brief allocations. Native
peak working set covers the runtime process and is a different measure, so neither
column is substituted for the other. Each run stayed within a 3 GiB/120 s guard.
The six metadata runs exclude geometry preparation and array materialization.
They show why the future manifest/lease reader must avoid retaining and repeatedly
decompressing complete nested history just to populate a list. They do not establish
startup gains for a future implementation or a numerical performance improvement.

A small-file regression test also bounds individual reads to 1 MiB while retaining
the aggregate archive limit. An input byte limit must not itself become a temporary
allocation of that size; measure allocations as well as on-disk archive bytes.

## Proposed explicit interfaces

Keep `load_project(path, ...) -> (Project, SimulationResult | None)` and
`load_run_history(path, ...) -> list[SimulationResult]` eager and compatible by default.
Add a separate `open_history_catalog(inspection, policy)` operation and a closable
`ResultStore`. No project JSON attribute can turn on filesystem or network access.
The store inherits resource approvals from the inspected archive, never from a run.
The current security inspector retains complete selected-container bytes and validates
nested NPZ headers. That API is not already a low-memory lazy reader. A subsequent
implementation must introduce an integrity-checked manifest/reader-lease path without
reintroducing metadata-driven external reads or authorization/version races; benchmark
that path separately from today's metadata inspection.

A lightweight immutable run record contains:

- Stable run identity, original configuration/physics identities and verified
  configuration content; distinguish these from the document/session/revision UUIDs.
- Mode, elapsed time, software/execution provenance, stored convergence/validation
  status, diagnostic summary and small, schema-validated scalar metrics.
- Available layer/field keys, support kind, units, component-local field version,
  mesh/object digests and estimated expanded sizes.
- Transient stored times, completion/stop reason and snapshot availability, without
  reconstructing every frame. Summary metadata must not contain arbitrary HTML.

Proposed `store.materialize(run_id)` returns a normal validated `SimulationResult`.
`store.frame(run_id, index)` requests the exact stored frame and the shared mesh.
Later field-specific reads require an explicit typed field view; they must never
pretend a partial field mapping is a complete result. Numerical/analysis consumers
continue receiving complete validated objects until individually migrated.
Both interfaces are explicit, cancellable operations with no property-triggered I/O.

List display must distinguish **stored convergence** from **validated loaded fields**.
A valid catalog is not proof of intact numerical payloads or mesh verification.
Hash/schema failures make the affected run unavailable with an actionable issue;
do not substitute zero arrays, a neighboring frame, or the latest working setup.

## Opening, file ownership and cancellation

Use metadata/object manifests from the new schema. Validate entry names, duplicate
members, schema, object kinds, dependency references and declared expansion budgets
before any array allocation. Each array is hash-checked and shape/dtype/support-checked
before becoming visible. A missing object invalidates its dependent run independently.
Legacy archives remain eager initially; displaying them does not silently rewrite them.

GUI acquisition runs in existing worker infrastructure. Each request carries document
epoch, selected run ID and a monotonic request generation. Only a matching completion
may replace the displayed historical context. Resolve pending Apply/Discard/Stay before
starting inspection; Stay creates no request and changes no selection/history context.
Returning to the working project restores its setup and camera, without applying a
historical configuration. Cancellation or failure preserves the previous usable view.

A store must pin an immutable archive generation while readers are active. Reopening
a path after Save As/replacement is unsafe even if its name or physical hash is unchanged.
On Windows, investigate a read handle allowing atomic replacement with identity checks;
on POSIX, pin the original inode/handle. Require native replacement/deletion tests before
adopting either strategy. A bounded private read-copy is an explicit fallback with disk
cost and cleanup disclosure, not an invisible full RAM copy. A separate ZipFile/stream
per worker avoids mutable shared seek state. Close waits for leases or cancels reads,
then releases handles, arrays and disposable read copies. No network fetch or extraction
of metadata-supplied paths is permitted.

## Bounded cache and observability

Start with an application-local byte budget, proposed 256 MiB, adjustable within a
validated range. This is a proposed default for evaluation, not a measured optimum.
Use LRU eviction by archive-generation/object digest and representation identity;
layer, field, frame and support semantics are part of a field-view key. Count owned
NumPy storage once by immutable object identity, including shared meshes. Reuse the
immutable bytes-backed array contract from asynchronous saving.

Active views, comparison pairs and save/continuation snapshots acquire leases. Pinned
objects cannot be evicted. If one required result exceeds the budget, disclose the
required size and offer explicit temporary over-budget use or cancellation; do not
silently load unlimited history. Report cache resident/pinned bytes, hit/miss counts,
active reads and estimated next allocation. This differs from process RSS and from the
existing retained-Python-memory estimate. No telemetry leaves the machine.

Invalidate by archive generation, not document name or mutable object address. Closing
a project releases unpinned entries; removing a history row affects the current document
manifest, not another session's recovery or an unrelated on-disk archive. Saved immutable
objects remain reachable by outstanding save/read leases until completion. Cache clearing
must not change the physical hash or accepted scientific values.

## Consumer migration

| Consumer | First safe behavior | Later selective behavior |
|---|---|---|
| History list/configuration comparison | Catalog only; no mesh allocation | None required |
| Historical inspection/maps | Full selected run in worker | Exact field view after support validation |
| Transient time navigation | Selected run initially eager | Exact requested frame, bounded prefetch of one adjacent frame |
| Two-run comparison | Materialize both, validate compatibility | Request corresponding supports; pin both during comparison |
| Whole-history color limits/local traces | Explicit cancellable scan | Validated per-frame reductions, exact existing weighting |
| Figure/selected-data export | Materialize selected run/frame | Use same export backend on validated selected support |
| Complete archive save/export | Immutable reader leases/object references | Copy verified referenced objects without changing identity |
| Initial-state continuation | Full required temperature/support/time validation | No approximate/interpolated substitute |
| CLI/Python | Existing eager functions | Explicit catalog/store context manager |

The existing weak-reference extrema cache remains a derived-value cache. Do not count it
as an array cache or use its scalars as proof that unloaded frames passed validation.
No history-wide option may silently use only the displayed frame to reduce memory.

## Migration and rollout gates

1. Merge/review recovery, async saving and archive contracts; keep default eager APIs.
2. Add catalog and reader leases behind an explicit local opt-in on the new archive
   schema, with a safe eager fallback. Unknown future formats fail closed.
3. Migrate history listing and one selected-run worker path. Keep comparison, reports,
   continuation and numerical analysis eager until their individual gates pass.
4. Introduce bounded frame/object caching, cancellation, byte accounting and cache UI.
5. Run the full equivalence/performance matrix, then evaluate enabling the GUI option
   by default. Retain eager CLI/Python behavior unless separately versioned.

No automatic project migration, schema bump, metadata rewrite, user-facing cache option
or runtime feature is implemented by this RFC. Existing .etl/.etlayout/JSON files retain
their current interpretation. Subsequent work must document opt-in writer compatibility
and provide a source-preserving migration path and rollback/export to legacy format.

## Required acceptance tests for the implementation

These are **planned tests**, not results asserted by this documentation PR:

- Instrument archive reads: catalog creation opens no field payload; failed resource
  authorization causes zero reads of declared external files, including UNC/junctions.
- Compare eager/lazy arrays bit-for-bit, dtypes, mesh/local indices, configuration and
  physics hashes, diagnostics and all stored transient times for shared/different meshes.
- Run analytical E1, multilayer local-field, conservation, measurement, saved-state
  continuation and existing transient regressions unchanged through both access paths.
- Inject missing/corrupt hashes, bad array headers, wrong component support, duplicate
  objects, unknown schemas and decompression bombs. Isolate affected runs and preserve
  the prior working context; no tolerance relaxation or fallback numerical data.
- Use actual Qt Apply/Discard/Stay, rapid A/B/history/frame changes, project close,
  cancellation and Save As during reads. Late completions cannot overwrite newer context.
- Assert bounded bytes/leases, shared-mesh accounting, eviction/reload equality, close
  cleanup and deterministic cache invalidation, including two application processes.
- Save/export after a partial browse must include every retained run intended for the
  complete archive, with exact original support; selected-data export stays filtered.
- Native Windows replacement/deletion, startup recovery and reopen must work with an
  active reader. Do not infer handle behavior from POSIX or offscreen-only checks.
- Repeat 1/10/50-record shared/mixed-layout/mesh and 101-frame transient workloads in
  fresh processes. Measure catalog opening, selected first frame, warm/cold navigation,
  full eager materialization, report/export time, peak process-tree RSS, cache bytes,
  archive bytes and GUI heartbeat gaps. Report cold/warm storage and hardware/versions.

Require lower startup memory and practical first-interaction improvement on the largest
shared-history cases without unacceptable single-run regression. Define numerical
performance gates from repeated baseline distributions before implementation review;
do not invent a speedup from metadata-only timing or compare different retained content.
Physical values, provenance, conservation and scientific acceptance must remain identical.
