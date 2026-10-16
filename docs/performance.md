# Measure calculation performance

Execution settings bound CPU use; they do not guarantee a speedup. Large FEM
assemblies can use several workers, while the current sparse direct factorization
remains serial. Independent studies can use processes, subject to memory budgets.

The existing **Jobs** drawer shows preparation, mesh, numerical, reporting and
total measured wall times for the selected run. Its tooltip reports sparse linear
solve time, which is included in numerical time. Saved projects and numerical
exports retain the same timings in `execution.timings_seconds`, outside physical
configuration identity. Old results without timing evidence say so explicitly.
Cached result retrieval is identified separately and never reported as a new solve.

Power reporting preserves exact nested supports, partial-element integration and
physical averaging windows. Report-owned coordinate/area data, conservative
bounding candidates and prepared geometries avoid repeated work. Sorted ID tags
use exact search-based intersection with a fallback for unsorted/duplicate API
tags. Hotspot integrations batch exact intersections and reuse affine P1 gradients;
quadratic contact heat still uses exact degree-two moments. Field arrays, window
grid, solver tolerances and conservation criteria are unchanged. When a uniform
field has tied maxima, floating-point summation may select a different equivalent
supported window center; a reported location is not a unique hotspot in that case.

Reporting progress names the measurement/device being evaluated. Cancellation is
checked between records and bounded hotspot batches. A sparse factorization or
individual GEOS call remains synchronous within its worker process.

## Reproduce a comparison

Use the same portable project containing a converged result on both revisions:

```powershell
python scripts/benchmark_reporting.py saved-result.etl --cpu-threads 1 --repeats 3 --output artifacts/reporting.json
python scripts/reporting_performance_acceptance.py --output artifacts/reporting-desktop
```

The benchmark only re-evaluates configured reporting; it does not generate a
layout or launch a new solve. It records field/source hashes, mesh size, hardware,
wall/CPU times and all scoped outputs. Compare output values as well as timings.
Full-run comparisons also need identical physics, mesh and solver settings;
report-only improvements are not a guarantee of equal whole-run speedup. Detailed
profiles and solved arrays belong in CI/release artifacts, not the repository.

## Measured E21 result

On this native Windows 8-core/16-thread host, two unprofiled reporting repetitions
on the same 143,324-triangle, nominal 5 A E21 fields measured **58.997 s before**
and **21.321 s after** (median, one CPU). All 12 configured measurements and 33
device groups agreed at relative `1e-10`/absolute `1e-13` comparison tolerances;
the archived field checksum was identical. This is a **63.9% reporting-time
reduction**, not a whole-run or multi-CPU speedup guarantee. The small maintained
[receipt](../benchmarks/e21-reporting.json) records source/field identity and
hardware; detailed profiles and report outputs remain external artifacts.


## Project save responsiveness

`scripts/benchmark_saving.py` builds deterministic finite arrays for storage testing;
it does not calculate a physical device. Use separate fresh output directories:

```powershell
python scripts/benchmark_saving.py --size 24 --output artifacts/save-small
python scripts/benchmark_saving.py --size 512 --history 3 --output artifacts/save-large
python scripts/benchmark_saving.py --size 512 --history 3 --synchronous --output artifacts/save-blocking
```

Each process records wall time, 10 ms event-loop heartbeats, sampled peak RSS, archive
bytes, hardware, one-time buffer freeze and snapshot handoff. It verifies reopening
and exact stored temperature values. Compare revisions in the same environment; one
sample is evidence of responsiveness, not a stable throughput or hardware guarantee.
Keep generated archives and detailed receipts outside source control.

A native Windows 11 / Python 3.12.14 / 8-core, 16-thread / 32 GiB comparison used the
previous blocking writer and the worker implementation on the same deterministic case:

| Storage case | Blocking / worker time | Worker heartbeats | Archive bytes | Blocking / worker peak RSS |
|---|---:|---:|---:|---:|
| 576 nodes, 1,058 elements | 0.075 / 0.068 s | 6 | 33,020 | 53.6 / 56.3 MB |
| 262,144 nodes, 522,242 elements | 2.691 / 2.761 s | 272 | 12,745,807 | 146.1 / 165.6 MB |
| Same large case plus three retained runs | 12.637 / 12.909 s | 1,275 | 50,372,785 | 169.3 / 180.3 MB |

The blocking writer delivered zero heartbeats during each save. Large worker maximum
heartbeat gaps were 60–61 ms. The initial immutable-array freeze cost 4.8–5.2 ms and
subsequent snapshot capture 0.46–0.51 ms. Archive sizes vary slightly with metadata UUIDs.
Retaining three runs added about 10.1 s to worker time: archives still serialize each
run independently. This change improves responsiveness and integrity, not compression
or history deduplication; immutable ownership has a measured memory cost.


## Archive storage and on-demand history

[Content-addressed archives](design/content-addressed-archives.md) are implemented
as an explicit schema-3 option. [Metadata-first history](design/lazy-run-history.md)
is separately enabled through **Edit → Archive and history settings…** for
schema-3 opening. Schema 2 remains the default writer and recovery format; its
historical worker measurements above are not schema-3 performance results.

The opt-in `scripts/benchmark_archive_history.py` harness measures schema-2/3
storage and explicit eager/lazy reader phases on the same solved input archives.
Report complete eager opening separately from catalog plus first selection,
complete lazy materialization, warm selection and post-eviction selection. Cache
payload bytes, private disk-copy bytes and process-tree RSS are distinct measures.
The linked format/history documents define the measurement and validation method;
no universal size or speed improvement is implied. Use new artifact directories
and keep generated archives/logs outside source control.
