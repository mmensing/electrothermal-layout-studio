# SPDX-License-Identifier: MPL-2.0
"""Opt-in same-mesh CPU comparison; generated receipts/fields belong in artifacts.

Generate the exact nominal E21 once, prepare its conforming mesh once, and pass
its guarded immutable mesh authority to fresh worker processes. No bias, load,
geometry, material, mesh size or numerical tolerance is changed. Shared initial
preparation/meshing are reported separately from each run's verified reuse.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import platform
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def write(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def solve_worker(project, reference, budget, directory):
    import numpy as np

    from etlayout.io.project import physics_hash
    from etlayout.simulation.runner import mesh_fingerprint, run_project

    try:
        started = time.perf_counter()
        cpu_started = time.process_time()
        result = run_project(project, execution={"cpu_threads": budget, "study_workers": 1}, _mesh_reference=reference)
        calculation_wall = time.perf_counter() - started
        calculation_cpu = time.process_time() - cpu_started
        arrays = {}
        for group in ("fields", "element_fields", "interface_fields"):
            for owner, fields in sorted(getattr(result, group).items()):
                for key, value in sorted(fields.items()):
                    arrays[group + "/" + owner + "/" + key] = value
        np.savez_compressed(directory / "comparison-fields.npz", **arrays)
        write(directory / "run.json", {"status": "converged" if result.converged else "not_converged",
              "wall_seconds": calculation_wall, "process_cpu_seconds": calculation_cpu,
              "physics_hash": physics_hash(project),
              "mesh_sha256": mesh_fingerprint(result.mesh), "mesh": result.mesh.statistics,
              "execution": result.execution, "metrics": result.metrics,
              "ports": result.analysis.get("ports", {}), "terminals": result.terminals,
              "measurements": result.analysis.get("measurements", {}), "iterations": result.iterations})
    except Exception as error:
        write(directory / "run.json", {"status": "failed", "error": str(error), "error_type": type(error).__name__})
        raise


def compare_fields(first, second):
    """Compare every supported field, including component-local/interface data."""
    import numpy as np

    rows = {}
    with np.load(first, allow_pickle=False) as a, np.load(second, allow_pickle=False) as b:
        if set(a.files) != set(b.files):
            raise ValueError("Runs have different field/support sets")
        for key in a.files:
            left, right = a[key], b[key]
            same = left.shape == right.shape and np.allclose(left, right, rtol=1e-10, atol=1e-12, equal_nan=True)
            finite = np.isfinite(left) & np.isfinite(right) if left.shape == right.shape else np.array([], dtype=bool)
            rows[key] = {"equivalent": bool(same), "maximum_absolute_difference":
                         float(np.max(np.abs(left[finite]-right[finite]))) if finite.any() else None}
    return rows


def compare_observables(rows, project, requested_current=None):
    """Check delivered service and the configured conservation criteria independently."""
    import math

    names = ("effective_resistance", "voltage_drop", "terminal_power", "electrical_power",
             "joule_power", "contact_power", "external_feed_power", "temperature_max", "heat_out")
    compared = {}
    for name in names:
        values = [row.get("metrics", {}).get(name) for row in rows]
        if all(isinstance(value, (int, float)) for value in values):
            compared[name] = {"values": values, "equivalent": all(math.isclose(values[0], value, rel_tol=1e-10, abs_tol=1e-12) for value in values[1:])}
    acceptance = []
    for row in rows:
        metrics = row.get("metrics", {})
        required = []
        if project.solver.mode != "thermal" or project.solver.thermal_include_joule:
            required += [("current_balance_error", "current_balance_tolerance"),
                         ("power_balance_error", "power_balance_tolerance")]
        if project.solver.mode != "electrical":
            required.append(("thermal_balance_error", "thermal_balance_tolerance"))
        checks = {name: metrics.get(name, float("inf")) <= getattr(project.solver, tolerance)
                  for name, tolerance in required}
        if requested_current is not None:
            delivered = row.get("ports", {}).get("drain", {}).get("current")
            checks["delivered_service"] = (isinstance(delivered, (int, float)) and
                abs(delivered/requested_current-1) <= project.solver.current_balance_tolerance)
        acceptance.append(checks)
    return {"quantities": compared, "acceptance": acceptance,
            "accepted": bool(compared) and all(value["equivalent"] for value in compared.values())
                        and all(all(checks.values()) for checks in acceptance)}


def run_plan(repetitions):
    """Alternate budget order to expose first-run/thermal drift in repeated pairs."""
    return [(repeat, budget) for repeat in range(1, repetitions + 1)
            for budget in ((1, 4) if repeat % 2 else (4, 1))]


def main(argv=None):
    import psutil

    from etlayout.examples import make_example
    from etlayout.io.project import physics_hash
    from etlayout.power_examples import metadata
    from etlayout.provenance import build_info
    from etlayout.simulation.execution import (
        ExecutionSettings,
        detect_capabilities,
        execution_context,
        execution_provenance,
    )
    from etlayout.simulation.runner import _capture_mesh_reference, mesh_fingerprint, prepare_mesh

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--example", default="E21", choices=["E1", "E21"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=1200., help="Wall budget per numerical worker, seconds")
    parser.add_argument("--repetitions", type=int, default=2, help="Alternating 1/4 CPU pairs (default: 2, range: 1-10)")
    parser.add_argument("--keep-fields", action="store_true", help="Retain comparison-only arrays in the private artifact directory")
    args = parser.parse_args(argv)
    if not 0 < args.timeout <= 7200:
        parser.error("Use a positive timeout no larger than 7200 s")
    if not 1 <= args.repetitions <= 10:
        parser.error("Use 1-10 repetitions")
    if detect_capabilities()["cpu_count"] < 4:
        parser.error("This explicit 1/4 CPU comparison requires at least four available logical CPUs")
    if args.output.exists():
        parser.error("Use a fresh output directory to preserve earlier evidence")
    args.output.mkdir(parents=True)
    project = make_example(args.example, args.output / "inputs")
    build = build_info()
    with execution_context(ExecutionSettings(1, 1), reset_counts=True):
        _, mesh = prepare_mesh(project)
        preparation = execution_provenance()["timings_seconds"]
    expected = physics_hash(project)  # verified database units are now known
    reference = _capture_mesh_reference(project, mesh)
    write(args.output / "preparation.json", {"build": build, "physics_hash": expected,
          "mesh_sha256": mesh_fingerprint(mesh), "timings_seconds": preparation, "mesh": mesh.statistics})
    rows, fields = [], {}
    reference_fields = None
    context = multiprocessing.get_context("spawn")
    for repeat, budget in run_plan(args.repetitions):
        directory = args.output / f"repeat-{repeat:02d}-cpu-{budget}"
        directory.mkdir()
        process = context.Process(target=solve_worker, args=(project, reference, budget, directory))
        started = time.perf_counter()
        process.start()
        monitored = psutil.Process(process.pid)
        peak = 0
        stopped = None
        memory_limit = min(metadata(project).get("memory_budget_bytes", 2*1024**3), int(psutil.virtual_memory().available*.8)) if args.example == "E21" else 2*1024**3
        while process.is_alive():
            try:
                peak = max(peak, monitored.memory_info().rss)
                if peak > memory_limit or time.perf_counter()-started > args.timeout:
                    stopped = "memory budget" if peak > memory_limit else "wall-time budget"
                    process.terminate()
                    break
            except psutil.NoSuchProcess:
                pass
            time.sleep(.05)
        process.join()
        path = directory / "run.json"
        row = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"status": "resource_limited" if stopped else "failed"}
        row.update(cpu_threads=budget, repetition=repeat, peak_process_RSS_bytes=peak,
                   process_wall_seconds=time.perf_counter()-started, exit_code=process.exitcode, stop_reason=stopped)
        write(path, row)
        rows.append(row)
        print(f"Repetition {repeat}, {budget} CPU budget: {row['status']}, {row['process_wall_seconds']:.3f} s, {peak/1024**2:.1f} MiB", flush=True)
        current_fields = directory / "comparison-fields.npz"
        if row["status"] == "converged":
            if reference_fields is None:
                reference_fields = current_fields
            fields[directory.name] = compare_fields(reference_fields, current_fields)
            if not args.keep_fields and current_fields != reference_fields:
                current_fields.unlink()
    observables = compare_observables(rows, project, 5. if args.example == "E21" else None)
    equal = (len(fields) == len(rows) and all(row["status"] == "converged" for row in rows)
             and all(value["equivalent"] for compared in fields.values() for value in compared.values())
             and observables["accepted"])
    unchanged = build_info() == build
    identity = all(row.get("physics_hash") == expected and row.get("mesh_sha256") == reference.digest for row in rows)
    write(args.output / "comparison.json", {"schema_version": 2, "scope": "same-mesh fresh-process repeated CPU budget comparison; not mesh verification",
          "build": build, "source_unchanged": unchanged, "identical_physics_and_mesh": identity,
          "status": "equivalent" if equal and identity and unchanged else "unverified", "repetitions": args.repetitions,
          "configuration": metadata(project) if args.example == "E21" else {"example": "E1"},
          "hardware": {"system": platform.system(), "release": platform.release(), "processor": platform.processor(),
                       "physical_cpus": psutil.cpu_count(logical=False), "logical_cpus": psutil.cpu_count(),
                       "physical_memory_bytes": psutil.virtual_memory().total},
          "shared_preparation_timings_seconds": preparation, "runs": rows, "field_comparison": fields, "observable_comparison": observables,
          "comparison_tolerances": {"relative": 1e-10, "absolute": 1e-12},
          "limitations": "Alternating fresh-process budget pairs with uncontrolled background system load; process RSS excludes the preparation parent. Native kernels use one thread per assembly worker; SuperLU factorization remains serial. No universal speedup or mesh-convergence claim."})
    if not args.keep_fields and reference_fields is not None:
        reference_fields.unlink(missing_ok=True)
    return int(not(equal and identity and unchanged))


if __name__ == "__main__":
    raise SystemExit(main())
