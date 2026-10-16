# SPDX-License-Identifier: MPL-2.0
"""Opt-in measurements of the existing archive writer, not a new archive format.

Each retained entry references a small pool of genuinely solved synthetic cases.
Repeating a result stresses storage; it does not represent additional experiments.
Raw archives and receipts belong in disposable artifacts, never the source tree.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import io
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import numpy as np
import psutil

FAMILIES = ("shared", "varied-mesh", "varied-layout", "transient")


def array_digest(array):
    data = np.asarray(array)
    prefix = json.dumps([data.dtype.str, data.shape], separators=(",", ":")).encode()
    return hashlib.sha256(prefix + b"\0" + data.tobytes(order="C")).hexdigest()


def result_digest(result):
    from etlayout.io.project import _json_value
    groups = {"mesh": {"points": result.mesh.points, "triangles": result.mesh.triangles,
        **{f"{kind}/{key}": value for kind in ("regions", "terminal_nodes", "boundary_nodes", "boundary_edges")
           for key, value in getattr(result.mesh, kind).items()}},
        **{kind: {f"{layer}/{field}": value for layer, fields in getattr(result, kind).items()
                  for field, value in fields.items()} for kind in ("fields", "element_fields", "interface_fields")}}
    data = {kind: {key: array_digest(value) for key, value in sorted(arrays.items())}
            for kind, arrays in groups.items()}
    data.update(configuration_hash=result.configuration_hash, physics_hash=result.physics_hash,
                metrics=_json_value(result.metrics), terminals=_json_value(result.terminals),
                converged=result.converged, field_version=result.field_version)
    if result.transient:
        data["transient"] = {"times": array_digest(result.transient.times),
            "completed": result.transient.completed, "rows": _json_value(result.transient.rows),
            "snapshots": [result_digest(frame) for frame in result.transient.snapshots]}
    return hashlib.sha256(json.dumps(data, sort_keys=True, allow_nan=False).encode()).hexdigest()


def payload_inventory(path):
    """Hash bounded existing archive leaves; estimate duplication, not v3 size."""
    from etlayout.io.project import (
        MAX_ARCHIVE_BYTES,
        MAX_ARCHIVE_DEPTH,
        MAX_ARCHIVE_ENTRIES,
        inspect_archive_size,
    )
    inspect_archive_size(path)
    inventory = {kind: {"occurrences": 0, "expanded_bytes": 0, "unique": {}}
                 for kind in ("layout", "array", "metadata")}
    entries = 0
    used = 0

    def scan(source, depth=0):
        nonlocal entries, used
        if depth > MAX_ARCHIVE_DEPTH:
            raise RuntimeError("Benchmark archive depth exceeds the existing safety budget")
        with zipfile.ZipFile(source) as archive:
            for member in archive.infolist():
                if member.is_dir():
                    continue
                entries += 1
                if entries > MAX_ARCHIVE_ENTRIES:
                    raise RuntimeError("Benchmark entry budget exceeded")
                content = archive.read(member)
                if member.filename.endswith((".etl", ".npz")):
                    scan(io.BytesIO(content), depth + 1)
                    continue
                used += len(content)
                if used > MAX_ARCHIVE_BYTES:
                    raise RuntimeError("Benchmark expanded byte budget exceeded")
                kind = "array" if member.filename.endswith(".npy") else "layout" if member.filename.startswith("layout/") else "metadata"
                record = inventory[kind]
                record["occurrences"] += 1
                record["expanded_bytes"] += len(content)
                record["unique"][hashlib.sha256(content).hexdigest()] = len(content)
    scan(path)
    return {kind: {"occurrences": record["occurrences"], "expanded_bytes": record["expanded_bytes"],
                   "unique_objects": len(record["unique"]), "unique_bytes": sum(record["unique"].values())}
            for kind, record in inventory.items()}


def prepare_inputs(output, families=FAMILIES):
    from copy import deepcopy

    from etlayout.examples import make_example
    from etlayout.io.project import configuration_hash, physics_hash, project_from_dict, save_project
    from etlayout.simulation.runner import run_project

    manifests = {}
    for family in families:
        cases = [("E19", None, None)] if family == "transient" else (
            [("E1", .6e-6, voltage) for voltage in (.01, .02, .03)] if family == "shared" else
            [("E1", size, .02) for size in (.4e-6, .6e-6, .9e-6)] if family == "varied-mesh" else
            [(name, .6e-6, None) for name in ("E1", "E2", "E3")])
        rows = []
        # The first two families deliberately reuse one exact source layout.
        shared = make_example("E1", output / "inputs" / family) if family in {"shared", "varied-mesh"} else None
        for index, (name, size, voltage) in enumerate(cases):
            project = deepcopy(shared) if shared else make_example(name, output / "inputs" / family / str(index))
            if size is not None:
                project.solver.mode = "electrical"
                project.mesh.max_size, project.mesh.min_size, project.mesh.contact_size = size, .2e-6, .2e-6
            if voltage is not None:
                next(terminal for terminal in project.terminals if terminal.id == "drain").value = voltage
            result = run_project(project)
            assert result.converged and (not result.transient or result.transient.completed)
            project = project_from_dict(result.configuration)
            if family in {"shared", "varied-mesh"}:
                assert np.isclose(result.metrics["resistance"], .084, rtol=1e-9)
            target = output / "inputs" / f"{family}-{index}.etl"
            save_project(project, target, result)
            mesh_hash = hashlib.sha256((array_digest(result.mesh.points) + array_digest(result.mesh.triangles)).encode()).hexdigest()
            rows.append({"file": target.relative_to(output).as_posix(), "example": name,
                "nodes": len(result.mesh.points), "elements": len(result.mesh.triangles),
                "mesh_hash": mesh_hash, "layout_sha256": project.layout.sha256,
                "configuration_hash": configuration_hash(project), "physics_hash": physics_hash(project),
                "snapshots": len(result.transient.snapshots) if result.transient else 0,
                "solve_seconds": result.elapsed_seconds, "mode": project.solver.mode})
        manifests[family] = rows
    if "shared" in manifests:
        assert len({row["mesh_hash"] for row in manifests["shared"]}) == 1
    if "varied-mesh" in manifests:
        assert len({row["layout_sha256"] for row in manifests["varied-mesh"]}) == 1
        assert len({row["mesh_hash"] for row in manifests["varied-mesh"]}) == 3
    if "varied-layout" in manifests:
        assert len({row["layout_sha256"] for row in manifests["varied-layout"]}) == 3
    (output / "inputs.json").write_text(json.dumps(manifests, indent=2), encoding="utf-8")
    return manifests


def run_case(output, family, count):
    from etlayout.io.project import (
        configuration_hash,
        estimate_project_archive,
        load_project,
        load_run_history,
        save_project,
    )
    from etlayout.io.saving import SaveSnapshot, freeze_result_arrays

    manifests = json.loads((output / "inputs.json").read_text(encoding="utf-8"))
    pool = [load_project(output / record["file"]) for record in manifests[family]]
    project, result = pool[0]
    history = [pool[(index + 1) % len(pool)][1] for index in range(count - 1)]
    expected = [result_digest(value) for value in [result, *history]]
    before = configuration_hash(project)
    start = time.perf_counter()
    for _, value in pool:
        freeze_result_arrays(value)
    freeze_seconds = time.perf_counter() - start
    started = time.perf_counter()
    snapshot = SaveSnapshot.capture(project, result, history, revision=count)
    snapshot_seconds = time.perf_counter() - started
    assert configuration_hash(project) == before
    estimate = estimate_project_archive(snapshot.project, snapshot.result, list(snapshot.history))
    target = output / f"{family}-{count}.etl"
    started = time.perf_counter()
    save_project(snapshot.project, target, snapshot.result, history=list(snapshot.history))
    save_seconds = time.perf_counter() - started
    del snapshot, pool, history, result, project
    gc.collect()
    started = time.perf_counter()
    reopened, current = load_project(target)
    current_load_seconds = time.perf_counter() - started
    started = time.perf_counter()
    loaded = load_run_history(target)
    history_load_seconds = time.perf_counter() - started
    assert len(loaded) == count - 1
    assert [result_digest(value) for value in [current, *loaded]] == expected
    assert configuration_hash(reopened) == before
    return {"family": family, "total_retained_records": count, "historical_entries": count - 1,
        "initial_freeze_seconds": freeze_seconds, "snapshot_seconds": snapshot_seconds,
        "save_seconds": save_seconds, "current_load_seconds": current_load_seconds,
        "eager_history_load_seconds": history_load_seconds, "archive_bytes": target.stat().st_size,
        "estimate": estimate, "payload_inventory": payload_inventory(target), "round_trip_exact": True}


def process_tree_rss(process):
    total = 0
    for member in [process, *process.children(recursive=True)]:
        try:
            total += member.memory_info().rss
        except psutil.NoSuchProcess:
            pass
    return total


def stop_process_tree(process):
    owned = [*reversed(process.children(recursive=True)), process]
    for member in owned:
        try:
            member.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(owned, timeout=5)
    for member in alive:
        try:
            member.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(alive, timeout=5)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New output directory; existing directories are refused")
    parser.add_argument("--counts", type=int, nargs="+", default=[1, 10, 50], choices=range(1, 51))
    parser.add_argument("--families", nargs="+", default=list(FAMILIES), choices=FAMILIES)
    parser.add_argument("--max-rss-gib", type=float, default=3.)
    parser.add_argument("--timeout", type=float, default=600., help="Per case; a resource limit is reported, never called a pass")
    parser.add_argument("--case", nargs=2, metavar=("FAMILY", "COUNT"), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    if args.case:
        record = run_case(output, args.case[0], int(args.case[1]))
        print(json.dumps(record, allow_nan=False))
        return 0
    if not math.isfinite(args.max_rss_gib) or not math.isfinite(args.timeout) or args.max_rss_gib <= 0 or args.timeout <= 0:
        parser.error("Resource budgets must be positive")
    output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    git = shutil.which("git")
    if git is None:
        parser.error("Run this source benchmark with Git available for exact revision provenance")
    revision = subprocess.check_output([git, "rev-parse", "HEAD"], text=True).strip()
    inputs = prepare_inputs(output, args.families)
    records = []
    for family in args.families:
        for count in args.counts:
            command = [sys.executable, str(Path(__file__).resolve()), "--output", str(output), "--case", family, str(count)]
            stderr = output / f"{family}-{count}.stderr.log"
            stdout = output / f"{family}-{count}.json"
            with stderr.open("w", encoding="utf-8") as error_file, stdout.open("w", encoding="utf-8") as result_file:
                child = subprocess.Popen(command, stdout=result_file, stderr=error_file, env=os.environ.copy())
                process = psutil.Process(child.pid)
                peak = 0
                start = time.perf_counter()
                limited = ""
                while child.poll() is None:
                    try:
                        peak = max(peak, process_tree_rss(process))
                    except psutil.NoSuchProcess:
                        break
                    if peak > args.max_rss_gib * 1024 ** 3 or time.perf_counter() - start > args.timeout:
                        limited = "peak RSS" if peak > args.max_rss_gib * 1024 ** 3 else "wall time"
                        stop_process_tree(process)
                        break
                    time.sleep(.01)
                code = child.wait(timeout=30)
            record = json.loads(stdout.read_text(encoding="utf-8")) if code == 0 else {
                "family": family, "total_retained_records": count, "status": "resource-limited" if limited else "failed",
                "limitation": limited or "Inspect retained stderr; no successful load is claimed", "exit_code": code}
            record.update(process_wall_seconds=time.perf_counter() - start, peak_rss_bytes=peak)
            records.append(record)
            print(json.dumps({key: record.get(key) for key in ("family", "total_retained_records", "save_seconds", "eager_history_load_seconds", "archive_bytes", "peak_rss_bytes", "status")} ), flush=True)
            (output / "measurements.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    receipt = {"writer_revision": revision,
        "peak_rss_scope": "Sampled 10ms aggregate of owned Windows launcher and descendant Python processes; imports, validation and inventory included",
        "storage_only": True, "repeated_pool_entries_are_not_independent_analyses": True,
        "python": platform.python_version(), "platform": platform.platform(), "processor": platform.processor(),
        "logical_cpus": psutil.cpu_count(), "physical_cpus": psutil.cpu_count(logical=False),
        "ram_bytes": psutil.virtual_memory().total, "max_rss_gib": args.max_rss_gib,
        "inputs": inputs, "measurements": records, "elapsed_seconds": time.perf_counter() - started}
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return int(any(not row.get("round_trip_exact") for row in records))


if __name__ == "__main__":
    raise SystemExit(main())
