# SPDX-License-Identifier: MPL-2.0
"""Opt-in reporting benchmark on one immutable solved field set.

Use the same portable result archive on each revision for a comparable report-only
measurement. No solver fields or acceptance criteria are changed for benchmarking.
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import platform
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    import numpy as np
    import psutil

    from etlayout.io.project import load_project
    from etlayout.power.analysis import analyze_power
    from etlayout.simulation.execution import ExecutionSettings, execution_context

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path, help="Portable ETL archive containing a converged result")
    parser.add_argument("--cpu-threads", type=int, default=1)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 10:
        parser.error("Use 1–10 reporting repetitions")
    project, result = load_project(args.project)
    if result is None or not result.converged:
        parser.error("A converged stored result is required; solve and save the project first")
    if not (project.power.measurements or project.power.devices):
        parser.error("The project needs configured measurements or device groups")
    checksum = hashlib.sha256()
    for label, group in (("fields", result.fields), ("element_fields", result.element_fields),
                         ("interface_fields", result.interface_fields)):
        for owner, fields in sorted(group.items()):
            for name, values in sorted(fields.items()):
                checksum.update(f"{label}/{owner}/{name}".encode())
                checksum.update(np.ascontiguousarray(values).tobytes())
    timings, cpu_times = [], []
    process = psutil.Process()
    with execution_context(ExecutionSettings(args.cpu_threads, 1)):
        for _ in range(args.repeats):
            cpu_started = time.process_time()
            started = time.perf_counter()
            analyze_power(project, result)
            timings.append(time.perf_counter()-started)
            cpu_times.append(time.process_time()-cpu_started)
    source = hashlib.sha256(Path(inspect.getsourcefile(analyze_power)).read_bytes()).hexdigest()
    receipt = {"schema_version": 1, "scope": "reporting only; same archived mesh and solved fields",
        "source_sha256": source, "field_sha256": checksum.hexdigest(), "physics_hash": result.physics_hash,
        "triangles": len(result.mesh.triangles), "cpu_threads": args.cpu_threads,
        "wall_seconds": timings, "process_cpu_seconds": cpu_times, "median_wall_seconds": float(np.median(timings)),
        "hardware": {"platform": platform.platform(), "python": platform.python_version(),
                     "logical_cpus": psutil.cpu_count(), "physical_cpus": psutil.cpu_count(logical=False)},
        "process_peak_rss_bytes": process.memory_info().peak_wset if hasattr(process.memory_info(), "peak_wset") else None,
        "measurements": result.analysis["measurements"], "devices": result.analysis["devices"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    print(f"Report median: {receipt['median_wall_seconds']:.3f} s; {len(result.mesh.triangles):,} triangles")


if __name__ == "__main__":
    main()
