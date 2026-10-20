# Metadata-first run history

**Status: implemented, explicitly opt-in for schema-3 projects.** The archive
contract is documented in [content-addressed archives](content-addressed-archives.md).
Schema-1/2 archives and legacy JSON keep their existing eager behavior. Opening
an older project does not convert it, and the default writer/recovery format
remains schema 2. Storage access does not change physical models or acceptance
criteria.

All desktop File Open paths, including eager legacy and JSON decoding, use the
owned `ProjectOpeningController` / `HistoryOpener` pipeline. Resource review is a
Qt-thread interaction; the reviewed reader retry remains off-thread. Eager
loading semantics and public Python API return types are unchanged. Cancellation
is checked between bounded eager phases; native array decoding already in
progress completes in the worker before its reply is discarded. Epoch/generation
checks and the final shared document transition protect newer edits and views.

## Using the application

1. Create a portable schema-3 copy with **File → Save content-addressed copy…**.
   Keep the original project when evaluating the format.
2. In **Edit → Archive and history settings…**, enable **Open schema-3 history
   on demand**. This applies to the next project opening. The array-cache default
   is 256 MiB; the desktop setting accepts 16–8,192 MiB.
3. Reopen that copy. History lists immutable records and stored scalar summaries
   before numerical fields are loaded. Stored convergence is labelled separately
   from validated loaded fields; a catalog is not a mesh-verification certificate.
4. Inspect a run or choose an exact stored transient time. Loading happens in a
   worker and preserves the previous usable view until the requested fields pass
   validation. The issue drawer explains corruption, missing support, resource
   approval or cache-budget failures.
5. Return to the working setup to restore its project and view. Historical
   inspection never applies another run's configuration to the working document.

The History drawer reports resident/pinned payload bytes, cache budget, active
reads and hit/miss counts. Its tooltip includes the next allocation estimate,
private disk-copy size and any explicitly approved over-budget use. These
numbers describe the array cache, not process RSS. Reducing the budget evicts
unpinned objects; an active view remains valid. A selection that cannot fit asks
for explicit temporary over-budget approval or cancellation.

## Explicit Python interfaces

The public compatibility functions remain eager:

```python
project, current = load_project(path)
history = load_run_history(path)
```

They return ordinary mutable `SimulationResult` objects with independent buffers
between retained runs. They do not substitute proxies or perform hidden I/O on
attribute access.

The separate `etlayout.io.history_store` API exposes a closable `ResultStore`:

```python
from etlayout.io.history_store import open_history_catalog

with open_history_catalog(path, cache_bytes=256 * 1024**2) as store:
    for record in store.history:
        print(record.run_id, record.mode, record.available)
    with store.acquire(store.current_run_id) as result:
        # A complete, validated SimulationResult; arrays are immutable.
        use_result(result)
```

`store.records` contains the current record first, when present, followed by the
original ordered historical records. `store.history` excludes the current
record. Repeated identical runs remain distinct records. `RunRecord.run_id`
(the `id` alias is also available) is a storage identity; configuration and
physics hashes keep their existing scientific meanings.

`store.acquire_frame(run_id, index)` returns the exact stored frame and mesh,
with no nested transient history attached. It does not claim that unloaded
frames were validated. `store.acquire(run_id)` materializes the complete run,
including its original transient times, every retained snapshot and accepted
step rows. Both return explicit leases; closing a lease releases its pins.
The convenience `materialize`/`frame` methods return real results with automatic
pins; use `release_result(result)` or release the last reference to end them.

Records are immutable metadata: original configuration/physics identities,
mode, elapsed time, diagnostics, execution summary, field/support keys, exact
stored times, completion status and expanded-array estimates. Their mappings
cannot be edited in place. `configuration_copy()` returns an isolated ordinary
model dictionary when an explicit comparison needs one. Record summaries never
replace the authoritative numerical arrays.

## Ownership, cancellation and trust

Catalog opening copies and hashes the explicitly selected archive into a bounded
private **disk** snapshot using 1 MiB chunks. The maximum compressed copy is
2,000,000,000 bytes. It then validates the central directory, manifests, JSON
objects, dependency graph and declared logical materialization budgets. It does
not read/decompress NPY field payloads. Copying the complete compressed file is
real I/O and disk consumption; metadata-first does not mean zero I/O or zero
memory. Each numerical object is hash/header/support checked before exposure.

The private generation prevents pathname races: later Save As, atomic
replacement, rename or deletion of the original cannot redirect an active
reader. The store identifies its generation with the SHA-256 of those exact
copied bytes, not a document name or physical hash. Source-generation leases
for saves keep the copy alive until the last associated save finishes. Closing
cancels view reads, waits for the owned active read, closes file handles and
cleans its disposable copy when generation leases are released.

The existing external-resource policy remains authoritative. Catalog creation
reads no declared external layout. Resolving a layout requires the same explicit
local-root, outside-root, network/junction and hash/relink review as an eager
opening. An object reference grants no filesystem/network authority. A previously
reviewed inspection must match the copied generation. Unknown future schemas
fail closed; damaged objects make dependent runs unavailable with diagnostics,
without fabricated fields or a neighbouring-frame fallback.

Each GUI request carries document epoch, selected record identity and a monotonic
request generation. Only a matching completion can replace the display. Draft
Apply/Discard/Stay resolution precedes historical inspection and transitions;
Stay submits no new request. Rapid selection, cancellation, project changes and
closing invalidate late completions. Failure leaves the previous usable fields
and working setup intact.

## Cache and complete consumers

The application-local LRU counts each unique immutable NumPy payload buffer once,
including meshes shared by multiple results. The NPY header is not retained as
uncounted backing storage. The budget excludes Python metadata, temporary read
buffers, the private disk copy and other application/process allocations; it is
not an operating-system memory limit. Pinned views/comparisons cannot be evicted.
`store.clear()` removes unpinned payload and does not change physics identity.
No global cross-project cache, network store or telemetry is introduced.

Complete archive Save/Export captures source-generation references to every
retained run, including those never browsed. The save worker copies only reachable
verified objects and validates complete physical support one run at a time.
Selected numerical/figure exports keep their existing disclosure/filtering
policy. They cannot pull in the complete source graph merely because it is
reachable. An explicit legacy copy is rejected before publication if schema 2
cannot preserve its typed values.

Comparison, continuation, whole-history limits, local traces and reports require
complete validated supports. The GUI requests those full results explicitly
instead of passing partial field mappings into numerical code. Local transient
traces offer a **Load complete time history…** action when only a frame is
loaded. Whole-history display choices must scan the requested history; displaying
one frame never silently stands in for all stored times. Existing weak-reference
extrema caches remain derived-value caches, distinct from this payload cache.

## Validation and measurement method

Focused tests cover metadata-only reads, external-resource denial before reads,
exact eager/lazy numerical and typed-metadata equality, malformed/corrupt objects,
distinct ordered records, immutable sharing, leases, eviction/reload, budgets,
cancellation and native Windows replacement/deletion. GUI regressions cover
Apply/Discard/Stay, rapid selections/frames, stale completions, return to working
context, partial-browse complete saves/exports, and visible errors. Existing
analytical, conservation, transient continuation and scientific acceptance tests
remain required.

Run the opt-in performance matrix from a source checkout:

```powershell
python scripts/benchmark_archive_history.py --output artifacts/archive-history --formats 2 3 --repeats 3 --reader-phases eager lazy-startup lazy-full
```

Use `--inputs-from` to reuse the exact solved input archive bytes. `--runtime-root`
selects a separately frozen checkout, which must also be selected through
`PYTHONPATH`; receipts record runtime revision and harness revision/hash. Each
observation has its own process, sampled process-tree RSS and unchanged 3 GiB /
600 s guard. Resource-limited or failed readers remain failed receipts, and other
independent requested reader modes still run. The command returns nonzero if
any observation fails.

The matrix uses 1/10/50 total records with shared layout/mesh, varied meshes,
varied layouts and all 101 retained E19 transient frames. Repeated entries cycle
through a genuinely solved pool; they are not independent experiments. Report
catalog-plus-exact-first-selection time separately from full eager opening and
sequential complete lazy materialization. Warm selection and post-eviction
selection measure the application cache; the operating-system filesystem cache
is not flushed. Process RSS and cache payload bytes remain separate measures.
Three observations support medians and observed ranges, not a confidence interval.
Python API timing is not evidence of GUI heartbeat latency or packaged-build
performance. Keep detailed archives/logs in disposable artifacts outside Git.

## Measured source comparisons

Measured on 16 October 2026 using Windows 11 build 26200, CPython 3.12.14,
NumPy 2.5.4, 8 physical / 16 logical AMD64 CPUs and 31.2 GiB RAM. The pinned
schema-2 source baseline was `4a464593`; the measured schema-3 runtime was
`7ce0f09d`, with harness SHA-256
`a9a5276f5c138f4c4f552670646d441f16bbeb3f0a41a9356d53e06c4841113d`.
Inputs were copied byte-for-byte from the same actually solved pool; no solver
was substituted or rerun to obtain a storage improvement.

The baseline completed 36 save/reopen and 36 separate eager-reader observations;
the schema-3 matrix completed 36 save/reopen and 108 separate reader observations.
Every reopened record/frame passed independent complete typed equality. All
observations stayed within the unchanged sampled 3 GiB RSS / 600 s limits.
Each row below has three fresh-process observations. Time and RSS values are
**median [observed minimum–maximum]**; archive sizes are medians. MiB means
2²⁰ bytes. These are observed ranges, not confidence intervals.

The save/reopen baseline was an ordinary-workstation run with brief disclosed
Qt/functionality overlaps. Reader-only baseline and candidate windows had no
known concurrent local numerical/desktop suites. All observations were retained;
OS filesystem caches were not flushed. Expected pool fingerprints were cached
before timing in the newer harness, while every actual loaded value was still
checked independently. Save/open timers exclude preparation and fingerprinting;
whole-process wall times therefore are not directly comparable across harnesses.

A later portable-frame follow-up (`8ad3d8b`) resolves matching-SHA frame source
paths and checks frame/run provenance. That correctness change is outside this
measured revision; these values must not be described as a complete timing matrix
of the final follow-up commit.

| Storage workload | Records | Schema-2 eager ready (s) | Schema-3 eager ready (s) | Schema-3 first selection (s) |
| --- | ---: | ---: | ---: | ---: |
| Shared layout / mesh | 1 | 0.904 [0.731–1.088] | 0.796 [0.784–0.804] | 0.775 [0.757–0.780] |
| Shared layout / mesh | 10 | 5.532 [4.983–5.706] | 3.152 [3.142–3.345] | 0.894 [0.837–0.914] |
| Shared layout / mesh | 50 | 28.2 [28.1–29.0] | 13.9 [13.7–13.9] | 0.975 [0.969–1.089] |
| Same layout, varied mesh | 1 | 0.856 [0.792–0.892] | 0.827 [0.765–0.827] | 0.781 [0.776–0.822] |
| Same layout, varied mesh | 10 | 5.267 [5.132–5.380] | 3.181 [3.158–3.227] | 0.841 [0.841–0.914] |
| Same layout, varied mesh | 50 | 28.1 [28.0–30.4] | 13.8 [13.7–13.9] | 0.994 [0.988–0.996] |
| Varied layouts / meshes | 1 | 0.847 [0.770–0.967] | 0.767 [0.755–0.806] | 0.767 [0.752–0.900] |
| Varied layouts / meshes | 10 | 13.0 [12.9–13.8] | 6.952 [6.928–7.253] | 0.951 [0.862–4.366] |
| Varied layouts / meshes | 50 | 69.4 [66.6–74.1] | 34.5 [33.6–35.9] | 1.058 [1.006–1.090] |
| 101-frame E19 | 1 | 1.336 [1.232–1.379] | 1.463 [1.454–1.601] | 0.207 [0.195–0.225] |
| 101-frame E19 | 10 | 18.1 [16.9–20.2] | 8.097 [7.978–8.279] | 0.326 [0.309–0.340] |
| 101-frame E19 | 50 | 113.9 [111.4–120.3] | 39.0 [38.4–39.4] | 0.937 [0.851–0.951] |

Eager readiness is `load_project` plus `load_run_history`, excluding independent
comparison. First-selection readiness includes the full compressed private disk
copy/catalog and one complete exact stored frame for E19, or one complete run
otherwise. It is not complete transient materialization and does not certify
unloaded frames. This is Python storage timing, not a GUI heartbeat measurement.

Sampled **reader-process-tree** RSS at 50 retained records is separate from cache
payload accounting. Imports and comparison are included; the save/input-pool
process is not reused for this table.
E19 eager schema-3 RSS was higher than schema 2; lower selected-view RSS
depends on on-demand access. Disk deduplication alone does not lower eager RAM.

| Storage workload | Schema-2 eager RSS (MiB) | Schema-3 eager RSS (MiB) | Schema-3 first-selection RSS (MiB) |
| --- | ---: | ---: | ---: |
| Shared layout / mesh | 734.7 [733.2–735.0] | 636.3 [635.9–637.2] | 99.0 [98.6–99.0] |
| Same layout, varied mesh | 731.9 [731.2–732.6] | 637.4 [636.1–638.3] | 99.0 [98.1–99.1] |
| Varied layouts / meshes | 1806.8 [1805.3–1808.1] | 1330.5 [1329.8–1333.4] | 98.2 [97.7–99.3] |
| 101-frame E19 | 1884.4 [1882.0–1895.4] | 1973.9 [1972.3–1974.5] | 64.5 [64.5–64.7] |

At 50 records the same-store warm selection and explicit release/clear/reload
checks retained exact values. All 36 first-selection observations increased cache
hits on warm reacquisition, cleared resident/pinned payload after release, and
reloaded exact fields within the configured budget. Cold cache here means
application LRU eviction, not a flushed operating-system filesystem cache.

| Storage workload | Warm selection (s) | After eviction (s) | Full lazy scan + verification (s) | Full-scan RSS (MiB) |
| --- | ---: | ---: | ---: | ---: |
| Shared layout / mesh | 0.230 [0.215–0.234] | 0.269 [0.266–0.270] | 12.5 [12.5–12.9] | 111.2 [110.5–112.6] |
| Same layout, varied mesh | 0.229 [0.218–0.238] | 0.263 [0.262–0.270] | 12.3 [12.2–12.4] | 122.9 [121.6–123.6] |
| Varied layouts / meshes | 0.224 [0.214–0.241] | 0.272 [0.251–0.273] | 30.7 [30.1–30.8] | 184.0 [179.8–184.4] |
| 101-frame E19 | 0.022 [0.022–0.023] | 0.025 [0.023–0.025] | 84.1 [82.3–84.3] | 181.6 [180.3–182.9] |

The complete sequential lazy scan includes independent typed comparison of every
actual run and all 101 E19 frames per retained run. Its duration is deliberately
not compared as an I/O-only speedup with eager readiness. It remains a useful
end-to-end completeness/resource observation; cache leases are released between
runs.

The preregistered adoption targets at 50 records were: archive bytes at most
20% of the baseline median; first-selection time at most 50% of the fastest
baseline eager reader; and first-selection RSS at most 60% of the smallest
baseline eager RSS. At one and 50 records, save/eager time targets were the
baseline minimum plus `max(0.15 s, 25% of that minimum)`. Correctness, cache
accounting and the resource ceilings remained hard requirements.

All size, startup, RSS, eager-reader and cache conditions passed. Three
single-record save targets failed; the bounds were not changed:

| Target | Observed median (s) | Upper bound (s) |
| --- | ---: | ---: |
| shared/1 save overhead bound | 0.917 | 0.864 |
| varied-mesh/1 save overhead bound | 0.949 | 0.886 |
| transient/1 save overhead bound | 2.072 | 1.389 |

On-demand history remains opt-in pending broader workloads. Synthetic repeated pools do
not establish a universal speedup or a benefit for fifty unrelated analyses.
Small projects may pay container/validation overhead; a single selected result
can exceed the local cache budget. Native source verification does not certify
a frozen package, clean machine, live network share or mixed-monitor setup.
