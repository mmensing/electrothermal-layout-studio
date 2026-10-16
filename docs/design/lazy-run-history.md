# Metadata-first run history

**Status: implemented, explicitly opt-in for schema-3 projects.** The archive
contract is documented in [content-addressed archives](content-addressed-archives.md).
Schema-1/2 archives and legacy JSON keep their existing eager behavior. Opening
an older project does not convert it, and the default writer/recovery format
remains schema 2. Storage access does not change physical models or acceptance
criteria.

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

The default stays opt-in pending broader workloads. Synthetic repeated pools do
not establish a universal speedup or a benefit for fifty unrelated analyses.
Small projects may pay container/validation overhead; a single selected result
can exceed the local cache budget. Native source verification does not certify
a frozen package, clean machine, live network share or mixed-monitor setup.
