# SPDX-License-Identifier: MPL-2.0
"""Measure finite-contact TLM extraction on three actual spatial meshes."""

from __future__ import annotations

import argparse
import json
import platform
import time
from pathlib import Path

import psutil

from etlayout.contact_studies import transfer_length_study
from etlayout.simulation.execution import ExecutionSettings


def verify(output, cpu_threads=4, attempt_quarter=False):
    output.mkdir(parents=True, exist_ok=True)
    report = {
        "version": 1,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "processor": platform.processor(),
        "logical_cpus": psutil.cpu_count(),
        "cpu_threads_requested": cpu_threads,
        "study_workers": 1,
        "levels": [],
    }
    process = psutil.Process()
    for factor in (1.0, 0.75, 0.5):
        started = time.perf_counter()
        study = transfer_length_study(
            output / ("mesh-" + str(factor)), mesh_factor=factor, execution=ExecutionSettings(cpu_threads, 1)
        )
        row = {
            "factor": factor,
            "wall_seconds": time.perf_counter() - started,
            "process_high_water_bytes_cumulative": getattr(
                process.memory_info(), "peak_wset", process.memory_info().rss
            ),
            "report": study["report"],
        }
        report["levels"].append(row)
        print(f"factor {factor:g}: {row['wall_seconds']:.3g}s; fit {study['report']['fit']}", flush=True)
    keys = ("Rs_ohm_per_square", "Rc_ohm", "rho_c_ohm_m2", "LT_m")
    report["fit_mesh_relative_changes"] = {
        key: [
            abs(b["report"]["fit"][key] - a["report"]["fit"][key]) / abs(b["report"]["fit"][key])
            for a, b in zip(report["levels"], report["levels"][1:])
        ]
        for key in keys
    }
    # This verifies the fit only, not all local J/T peaks or process validity.
    report["fit_mesh_verified"] = all(
        value < 0.01 for changes in report["fit_mesh_relative_changes"].values() for value in changes
    )
    report["additional_resource_attempts"] = []
    if attempt_quarter:
        from etlayout.models import SimulationError

        try:
            attempted = transfer_length_study(
                output / "attempt-quarter", mesh_factor=0.25, execution=ExecutionSettings(cpu_threads, 1)
            )
            report["additional_resource_attempts"].append(
                {"factor": 0.25, "status": "completed", "report": attempted["report"]}
            )
        except SimulationError as error:
            report["additional_resource_attempts"].append(
                {
                    "factor": 0.25,
                    "status": "resource_limited" if "budget" in str(error) else "failed",
                    "reason": str(error),
                    "fit_verified": False,
                }
            )
    (output / "verification.json").write_bytes(
        (json.dumps(report, indent=2, allow_nan=False) + "\n").encode("utf-8")
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cpu-threads", type=int, default=4)
    parser.add_argument(
        "--attempt-quarter",
        action="store_true",
        help="Retain an additional opt-in quarter-size attempt, including resource failures",
    )
    args = parser.parse_args()
    verify(args.output, args.cpu_threads, args.attempt_quarter)
