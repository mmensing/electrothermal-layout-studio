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
