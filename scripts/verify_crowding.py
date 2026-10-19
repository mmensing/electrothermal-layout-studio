# SPDX-License-Identifier: MPL-2.0
"""Independent current-crowding/heat verification; generated evidence only.

python scripts/verify_crowding.py --output evidence.json
No plots, hidden calibration or solver-tolerance changes are involved.
"""

from __future__ import annotations

import argparse
import json
import platform
import time
from pathlib import Path

import psutil
from shapely.geometry import box

from etlayout.geometry.prepare import prepare_geometry
from etlayout.power.crowding import local_crowding, verify_mesh_sequence
from etlayout.simulation.runner import clear_caches, run_project
from etlayout.validation.crowding import CASES, benchmark_project, exact_resistance, finite_volume_reference


def verify(cases=CASES, sizes=(4e-6, 2e-6, 1e-6)):
    report = {
        "version": 1,
        "hardware": {
            "platform": platform.platform(),
            "processor": platform.processor(),
            "logical_cpus": psutil.cpu_count(),
        },
        "cases": [],
    }
    process = psutil.Process()
    for case in cases:
        print(f"Measuring {case}", flush=True)
        records, results = [], []
        project = benchmark_project(case)
        references = [finite_volume_reference(project, spacing) for spacing in (1e-6, 0.5e-6)]
        support = box(16e-6, 16e-6, 26e-6, 26e-6) if case.endswith("L") else box(30e-6, 10e-6, 50e-6, 30e-6)
        for size in sizes:
            project = benchmark_project(case, size)
            clear_caches()
            started = time.perf_counter()
            result = run_project(project)
            geometry = prepare_geometry(project)
            local = local_crowding(
                project,
                result,
                "metal",
                support=support,
                geometry=geometry,
                reference_sheet_current=abs(result.terminals["return"]["current"]) / 40e-6,
            )
            row = {
                "mesh_size_m": size,
                "elements": len(result.mesh.triangles),
                "elapsed_s": time.perf_counter() - started,
                "process_peak_working_set_bytes": process.memory_info().peak_wset,
                "execution": result.execution,
                "metrics": result.metrics,
                "local": local,
                "converged": result.converged,
            }
            records.append(row)
            results.append(result)
        report["cases"].append(
            {
                "case": case,
                "exact_resistance_ohm": exact_resistance(case),
                "references": references,
                "meshes": records,
                "verification": verify_mesh_sequence(project, results, "metal", support),
            }
        )
    return report


def radius_study():
    rows = []
    for radius in (0.2e-6, 0.5e-6, 1e-6, 2e-6, 5e-6):
        print(f"Measuring radius {radius * 1e6:g} um", flush=True)
        results = []
        for size in (4e-6, 2e-6, 1e-6):
            project = benchmark_project("rounded_L", size, radius=radius)
            project.mesh.min_size = min(size / 4, radius / 6)
            project.terminals[0].kind = "current"
            project.terminals[0].value = 0.25
            results.append(run_project(project))
        support = box(16e-6, 16e-6, 30e-6, 30e-6)
        record = verify_mesh_sequence(
            project, results, "metal", support, reference_sheet_current=0.25 / 20e-6
        )
        rows.append(
            {
                "radius_m": radius,
                "current_A": 0.25,
                "verification": record,
                "finest": local_crowding(
                    project, results[-1], "metal", support=support, reference_sheet_current=0.25 / 20e-6
                ),
            }
        )
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", nargs="+", choices=CASES, default=list(CASES))
    args = parser.parse_args()
    report = verify(args.cases)
    report["radius_study"] = radius_study()
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    main()
