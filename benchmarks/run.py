# SPDX-License-Identifier: MPL-2.0
"""Measured native Windows benchmarks in isolated spawned processes.

Records observed peak RSS at 10 ms sample interval including Python/native runtimes.
This is a desktop observation, not a controlled performance certification.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import platform
import sys
import time
from pathlib import Path

import psutil


def hardware():
    cpu = platform.processor()
    if sys.platform == "win32":
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
            cpu = winreg.QueryValueEx(key,"ProcessorNameString")[0]
    return {"cpu": cpu, "logical_cpus": os.cpu_count(), "physical_cpus": psutil.cpu_count(logical=False),
            "system_ram_gib": psutil.virtual_memory().total / 2**30,
            "platform": platform.platform(), "python": sys.version, "machine": platform.machine()}


def task(name, directory, connection):
    try:
        import importlib.metadata as metadata

        from etlayout.examples import large_example, make_example
        from etlayout.io.project import _json_value
        from etlayout.models import SweepSpec
        from etlayout.simulation.runner import clear_caches, run_project
        from etlayout.simulation.sweep import run_sweep
        source = Path(directory) / name
        mode = "electrical"
        if name == "small_metal":
            p = make_example("E1", source)
        elif name == "multilayer":
            p = make_example("E5", source)
        elif name == "millimeter":
            p = large_example(source)
        elif name == "hemt":
            p = make_example("E7", source)
        elif name == "coupled_stack":
            p = make_example("E10", source)
            mode = "electrothermal"
        elif name == "parameter_sweep":
            p = make_example("E1", source)
        else:
            raise ValueError(name)
        clear_caches()
        p.solver.mode = mode
        started = time.perf_counter()
        if name == "parameter_sweep":
            rows = run_sweep(p, SweepSpec("terminals.drain.value", [.01,.02,.03,.04,.05], "electrothermal"))
            results = [row.result for row in rows if row.result]
            errors = [row.error for row in rows if row.error]
        else:
            results = [run_project(p)]
            errors = []
        elapsed = time.perf_counter() - started
        record = {"name": name, "mode": mode if name != "parameter_sweep" else "5-point electrothermal sweep",
                  "elapsed_s": elapsed, "converged": all(r.converged for r in results) and not errors,
                  "errors": errors, "nodes": max(len(r.mesh.points) for r in results),
                  "triangles": max(len(r.mesh.triangles) for r in results),
                  "electrical_dofs": max(r.metrics.get("electrical_dofs",0) for r in results),
                  "thermal_dofs": max(r.metrics.get("thermal_dofs",0) for r in results),
                  "mesh_statistics": results[0].mesh.statistics,
                  "results": [dict(metrics=r.metrics, configuration_hash=r.configuration_hash) for r in results],
                  "versions": {d: metadata.version(d) for d in ["numpy","scipy","gmsh","klayout","PySide6","shapely"]}}
        connection.send(_json_value(record))
    except BaseException as exc:
        connection.send({"name": name, "converged":False, "error":str(exc)})
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="benchmarks/results-windows.json")
    parser.add_argument("--work", default="artifacts/benchmark-inputs")
    args = parser.parse_args()
    context = mp.get_context("spawn")
    rows = []
    for name in ("small_metal", "multilayer", "millimeter", "hemt", "coupled_stack", "parameter_sweep"):
        receive, send = context.Pipe(duplex=False)
        process = context.Process(target=task, args=(name,args.work,send))
        process.start()
        send.close()
        native = psutil.Process(process.pid)
        peak = 0
        while process.is_alive() and not receive.poll(.01):
            try:
                peak = max(peak, native.memory_info().rss)
            except psutil.NoSuchProcess:
                break
        row = receive.recv() if receive.poll(2) else {"name":name,"converged":False,"error":"Worker exited without result"}
        process.join(timeout=10)
        row["peak_rss_mib"] = peak / 2**20
        row["worker_exit_code"] = process.exitcode
        rows.append(row)
        print(json.dumps({k:row.get(k) for k in ("name","converged","elapsed_s","triangles","peak_rss_mib","error")}), flush=True)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    document = {"hardware":hardware(), "measured_local_date":"2026-10-10",
                "protocol":"One fresh native worker per workload; single observed run; RSS sampled every 10 ms; "
                           "runner including lazy backend imports, mesh and physics timed; input layout generation excluded; other workstation activity possible.",
                "benchmarks":rows}
    output.write_text(json.dumps(document,indent=2), encoding="utf-8")
    if not all(row.get("converged") for row in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    mp.freeze_support()
    main()
