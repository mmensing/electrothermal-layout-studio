# RFC: content-addressed experiment archives

**Status: implemented as an explicit schema-3 option.** Schema 2 remains the default,
including automatic recovery. Use `save_project(..., archive_format=3)`, **File →
Save content-addressed copy…**, or `etlayout archive-convert` to choose the new
container. Existing eager reader APIs remain compatible. This document is the
maintained format contract and measurement method; on-demand history is a
[separate consumer layer](lazy-run-history.md).

The implementation preserves typed metadata and exact numeric array bytes,
stores only reachable archive-local objects, verifies completed temporary files,
and rejects ambiguous legacy conversions. Complete saves can copy pinned stored
run references without retaining the entire decoded history, but physical
validation still materializes one complete run at a time. Deduplication improves
storage reuse; it does not accelerate the physical solver.

## Problem and measured baseline

The schema-2 writer stores one complete `results/arrays.npz` plus independent
`runs/run_XXXX.etl` archives. Every retained entry embeds its own source layout,
mesh, material/configuration snapshot and fields. Transient frames already share
one mesh within a run, but another retained run repeats that run's complete
history. A single run can also contain identical arrays in different field roles.
ZIP compression does not share a dictionary between separate run entries.

The current aggregate expanded-data budget is 2,000,000,000 bytes, with at most
100,000 recursive entries, depth 3 and 20,000,000 bytes per metadata document.
These are safety limits, not compression targets. The current writer verifies the
finished temporary archive before atomic replacement. None of those safeguards
should be removed to obtain smaller files or faster measurements.

### Reproduction and measurement scope

Run the opt-in harness from a source checkout; ordinary CI does not run it:

```powershell
python scripts/benchmark_archive_history.py --output artifacts/archive-history-baseline
```

The output directory must be new. It holds actual generated layouts, solved
input projects, archives, bounded-process diagnostics and machine-readable
receipts. Keep those artifacts outside Git. The harness samples RSS every 10 ms
across the owned Windows launcher **and its descendant Python process**; observing
only the launcher would substantially undercount memory. Each case gets a fresh
process, a 3 GiB sampled-RSS ceiling and a 600 s deadline. Resource-limited or
failed cases are retained and return a nonzero status.

A total of 1, 10 or 50 records means the current result plus 0, 9 or 49 historical
entries. Entries cycle through a small pool of actually solved synthetic
configurations. This isolates repeated-content storage cost; it is **not** a
claim that 50 independent experiments were run. Shared-buffer inputs are frozen
once and handed to `SaveSnapshot.capture`; its metadata isolation and source/
configuration identity checks remain in force. Preparation and numerical solves
are outside the save/open timings.

- **Shared layout and mesh:** E1 at 10, 20 and 30 mV, fixed-temperature electrical
  calculations on the same source and mesh. Each independently agrees with the
  0.084 Ω analytical resistance; fields differ with delivered service.
- **Same layout, varied mesh:** E1 at 20 mV with 0.4, 0.6 and 0.9 µm global mesh
  limits and 0.2 µm minimum/contact sizing. Source identity stays fixed; three
  distinct mesh payloads are verified. Contact/boundary refinement dominates: the
  resulting meshes have 29,562–29,564 nodes and 58,022–58,026 triangles. This is
  a distinct-content storage case, not a mesh-convergence study.
- **Varied layouts and meshes:** E1, E2 and E3, using fixed-temperature electrical
  calculations and 0.6 µm global / 0.2 µm minimum/contact sizing. These are storage
  workloads, not new mesh-verified scientific rankings of those examples.
- **Transient:** the configured E19 multilayer thermal example, retaining all
  101 actual snapshots. Fields, time order and thermal-history rows are preserved.

The harness verifies reopened mesh/field array dtype, shape and logical C-order
bytes, configuration and physics hashes, metrics, terminals, transient times and
rows. It separately counts equal SHA-256 hashes of existing uncompressed NPY,
layout and metadata members. The digest does not independently assert every
execution or diagnostic metadata member; the proposed format must add that
complete typed-metadata coverage before migration is enabled. Counting repeated
payload is **not** a
measurement of schema-3 compression, speedup or future memory savings.

Measured on 16 October 2026: Windows 11 build 26200, CPython 3.12.14,
NumPy 2.5.4, an 8-core/16-thread AMD64 host and 31.2 GiB reported system RAM.
The runtime source was the frozen immutable-save candidate `60d3324`; measurement
harness revision `2b84427` changes no runtime code. This is the schema-2
`SaveSnapshot.capture` → `save_project` contract, with eager `load_project` and
`load_run_history`. Subsequent resource-review/metadata-inspection changes are
not included in these timings. Raw receipts retain the exact revisions. Sizes
and memory use binary MiB (2²⁰ bytes).

| Storage workload | Records | Archive (MiB) | Save (s) | Current open (s) | History open (s) | Peak RSS (MiB) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Shared layout / mesh | 1 | 2.1 | 0.773 | 0.303 | 0.0128 | 109.1 |
| Shared layout / mesh | 10 | 21.1 | 8.49 | 0.424 | 2.77 | 202.9 |
| Shared layout / mesh | 50 | 105.7 | 42.6 | 1.01 | 14.7 | 639.1 |
| Same layout, varied mesh | 1 | 2.1 | 0.721 | 0.275 | 0.0119 | 111.2 |
| Same layout, varied mesh | 10 | 21.2 | 8.19 | 0.377 | 2.58 | 201.8 |
| Same layout, varied mesh | 50 | 106.0 | 41.8 | 1.03 | 14.8 | 638.3 |
| Varied layouts / meshes | 1 | 2.1 | 0.741 | 0.271 | 0.0124 | 168.6 |
| Varied layouts / meshes | 10 | 93.3 | 23.4 | 0.563 | 6.88 | 382.6 |
| Varied layouts / meshes | 50 | 500.5 | 130 | 1.91 | 35.8 | 1369.8 |
| 101-frame E19 transient | 1 | 5.2 | 1.03 | 0.414 | 0.0961 | 134.4 |
| 101-frame E19 transient | 10 | 51.5 | 13.1 | 1.29 | 5.03 | 435.2 |
| 101-frame E19 transient | 50 | 257.5 | 73.9 | 5.37 | 35.5 | 1671.3 |

One fresh-process observation per row; uncertainty and cold-cache distributions
were not measured. Peak RSS includes imports, input loading, validation and the
payload inventory, rather than an isolated save-only peak. “Current open” uses
`load_project`; “history open” uses eager `load_run_history` and excludes the
subsequent byte-equality digest. Both retain the current reader's recursive
archive checks. These host timings are not portable guarantees.

At fifty records, identical uncompressed NPY payload accounts for 96.3% (shared
layout / mesh), 94.3% (same layout, varied mesh), 94.2% (varied layouts / meshes)
and 98.4% (101-frame E19 transient). These fractions reflect the deliberately
repeated input pools and include intra-run duplication. They indicate available
repeated content, not achieved archive-size reductions for fifty distinct analyses.
Fifty different operating points can have far less field reuse. Shared synthetic
layouts are tiny (178-byte E1 GDS in this measurement); these data do not establish
a general layout-heavy customer workload benefit.

### Integration smoke after resource-review changes

A later functional check used the integrated saving/resource-review candidate
`4c51eb0`, including bounded chunked container reads. It copied the original
solved input archives byte-for-byte and repeated shared E1 and full 101-frame
E19 save/reopen checks with one and three total records. All four cases retained
the checked array bytes, hashes, metrics, terminals and transient history.
Actual RSS exhaustion, wall-time exhaustion and nonfinite budget controls failed
as intended. This small compatibility check is separate from the original
1/10/50-record performance table; that table was not regenerated on the later
reader. A separate exact-file reader-allocation diagnostic is also not a new
writer benchmark. Header inspection remains eager, not lazy history startup.

## Proposed representation

Keep a self-contained ZIP and the `.etl` / `.etlayout` extensions. A versioned
root declares `format: etlayout`, `schema_version: 3`, the stable document ID,
current configuration, current run reference and ordered history records. An
index describes each object's kind, byte length, compression and SHA-256 address:

```text
project.json
objects/sha256/<64 lowercase hex>.npy
objects/sha256/<64 lowercase hex>.gds   # or .oas
objects/sha256/<64 lowercase hex>.json
runs.json
```

Object members are addressed only by validated content IDs inside this archive;
no URI, directory traversal, absolute path, filesystem symlink or automatic
cross-project cache lookup is permitted. Keep external-source declarations under
the existing explicit resource-trust policy. An object reference never grants
outside-root or network access.

Use standard non-pickled NPY arrays and canonical UTF-8 JSON. Specify the NPY
header/order encoding as part of the object-codec version; preserve numeric dtype,
byte order, shape, NaN payloads and values. Do not cast, quantize or round arrays.
Hash the complete uncompressed canonical object bytes, independently of ZIP
compression. The manifest preserves the existing **raw layout SHA-256** used by
source identity; an archive-object address must not silently replace that hash.

Store common payload only once:

- A mesh descriptor references coordinates, triangle connectivity and every
  region/terminal/boundary mapping. Keep per-run mesh statistics/provenance in
  its run metadata. Equal point coordinates alone do not establish equal meshes.
- A run references its original configuration/material snapshot, layout, mesh,
  nodal fields, component-local element fields and interface fields. Field
  bindings retain layer, quantity, units and support even if equal arrays share
  one object.
- A transient references its shared mesh, exact ordered times, accepted-step
  rows, completion state and each frame's complete fields and metadata. Its final
  fields must still match its last retained snapshot and restart-state digest.
- Configuration/material snapshots may share canonical JSON objects, while
  original identifiers, units, model parameters and annotations remain intact.

Deduplicate **payload, not run records**. Current `SimulationResult` has no
persisted run UUID; configuration hash plus elapsed time is not a unique run ID.
Introduce a versioned storage-record ID without replacing scientific configuration
or physics hashes. Legacy migration must keep repeated entries and order, retain
the original metadata, and deterministically distinguish their archive positions.
A storage ID must never imply that two equal fields came from the same experiment.

The object graph is archive-local and acyclic. The writer collects only objects
reachable from the chosen current/history records; removing a historical record
can drop unreachable payload on the next complete atomic save. No in-place ZIP
editing, external object store, filesystem hard-link scheme or background global
garbage collection is proposed.

## Compatibility and migration

1. Keep schema-1/2 and legacy JSON readers. Opening an older project must not
   rewrite it or modify its document, source, configuration or physics identity.
2. Introduce the new writer through an explicit keyword-only archive-format
   option or Save As choice. Keep schema 2 the default through compatibility and
   performance review; do not silently switch autosave/recovery formats.
3. The schema-3 reader restores the existing public `load_project` two-value API
   and eager `load_run_history` list semantics. Do not make writable Python
   results share mutable buffers between runs. Disk deduplication alone does not
   establish lower eager memory use; lazy/cache behavior requires another review.
4. Validate an explicit migration into a **new** destination by reopening it and
   comparing arrays, typed metadata, document identity and every run's provenance.
   Preserve legacy field versions and diagnostics; repacking is not numerical or
   mesh verification. Unknown future versions/codecs fail with upgrade guidance.
5. Offer an explicit legacy-schema export where its existing budgets permit it.
   A failed migration, downgrade, cancellation or replacement must preserve the
   previous durable archive and the relevant session's recovery snapshot.

Use the immutable snapshot, revision ordering, progress/cancellation checkpoints
and atomic verified replacement from asynchronous saving. Stream hashing and
writing on the save worker. Reuse already-hashed buffers only when ownership and
immutability are established; a writable alias invalidates such a cache. Do not
identify data by Python object address alone or hash large fields on the GUI
thread. Changing a source layout while saving still aborts an inconsistent save.

## Integrity, trust and resource budgets

Validate the manifest and central directory before array allocation or external
resource resolution. Reject duplicate ZIP names, duplicate/conflicting object
records, unsupported kinds, dangling references, cycles, invalid addresses,
length mismatches, object-dtype arrays and malicious NPY dimensions. Bound member
count, manifest length, nesting, decompressed bytes and hashing/decompression work.
Verify each object hash against its exact bytes; a manifest hash is integrity
metadata, not an authenticity signature or evidence that a project is trusted.

Maintain **both** stored unique-byte limits and logical materialization limits.
One object referenced a million times must not bypass the existing expanded-data
budget or cause the eager compatibility API to allocate unbounded copies. Writer
preflight and reader budgets must agree. A later lazy API may expose a separately
bounded access strategy; this RFC does not weaken current limits to accept larger
histories. Corrupt or unavailable data is an explicit failure, never a zero-filled
field, a silently skipped run or a verified badge.

Archive-complete and safe-sharing exports keep their existing separate policies.
A selected numerical/figure export must not include the whole source object graph,
confidential notes, layouts or unrelated process/material snapshots merely because
objects are reachable from an original run. No network storage, telemetry,
third-party dependency or binary-publication-policy change is needed.

## Verification requirements

- Legacy schema-1/2/JSON fixtures retain exact fields, component-local support,
  thresholds, run order, source/document identities and compatibility diagnoses.
- One/ten/fifty-record and many-frame cases reopen with exact array and typed
  metadata equality, including execution, convergence, diagnostics, units,
  configuration/physics hashes and restart state. Repeated runs remain distinct.
- Equal layout/mesh/array payload is stored exactly once. Different supports,
  dtypes, byte order, shapes or source bytes never alias accidentally. Deliberate
  single-object corruption invalidates every affected binding and cannot return
  stale cached fields.
- Missing/dangling objects, malformed NPY headers, duplicate names, manifest
  bombs, cycles and excessive logical references fail before unsafe allocation.
  Reuse the current malicious-archive and external-resource trust regressions.
- Fault injection during hash/write/verify/replace/cancel retains the last durable
  archive, respects save ordering and removes only owned recovery generations.
- Save/reopen, historical inspection, transient continuation, numerical exports,
  CLI and GUI preserve their existing public contracts. Safe export sentinel
  tests verify that deduplication cannot expand a selected disclosure scope.
- Compare real schema-2/3 archive size, save/open time and sampled process-tree
  RSS on these same workloads, with repeated fresh-process observations. Publish
  actual values and uncertainty; do not infer a speedup from duplicate hashes.
  Preserve the single-run case and cases with little repeated content.

## Rollout and alternatives

The reviewed object collector, guarded reader, explicit migration and legacy-copy
checks are implemented behind the format option. A default-format change remains
a separate decision based on repeated measurements and compatibility evidence.
Opening an old project never rewrites it, and ordinary schema-2 saving remains
available. Unsupported typed metadata in an exact downgrade is an actionable
failure rather than a silent conversion.

A smaller alternative is caching immutable compressed payload during a save;
that can reduce serialization work without changing storage semantics, but still
repeats payload in the archive. Storing already-compressed NPZ entries without a
second deflate pass also deserves measurement. External object stores and a
custom database/container would add portability, trust and migration complexity
that is not justified by these measurements.

Lazy history should build on the reviewed object/reference contract. It requires
its own metadata catalog, bounded cache, corruption policy, mutable-API adapter
and consumer migration. Archive-only measurements do not establish lazy-startup performance.
